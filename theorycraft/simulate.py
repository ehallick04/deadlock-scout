"""
theorycraft.simulate -- a hero's damage over the course of a game.

    from theorycraft import simulate
    r = simulate("Haze",
                 items=["Headshot Booster", "Rapid Rounds", ("Swift Striker", 600)],
                 ability_order=[1, 3, 2, 3, 3, 4, 3],
                 end_s=1800)
    r.timeline      # one dict per sample: t, souls, boons, spirit, dps parts ...
    r.spikes        # events that moved total DPS by >= spike_pct
    r.unmodelled    # effects present in the build that were NOT counted

Time drives souls (a souls curve), souls drive boons, ability points and
purchases. Purchases happen in the given order, each as soon as it is
affordable or at its given time, whichever is later. The ability order is a
queue that is spent the same way.

See MECHANICS.md for what is modelled, approximated and unknown.
"""

from bisect import bisect_right
from copy import copy
from dataclasses import dataclass, field

from .combat import (OBJECTIVE_ABILITY_MULT, OBJECTIVES, WEAPON_DAMAGE_ABILITIES, final_resist,
                     rift_times, stack)
from .effects import conditional_stats, damage_and_sustain, shred
from .specials import SPECIAL, fixation, ira_domini, kinetic_carbine
from .data import (AP_TIER_COST, PER_CAST_DAMAGE, PER_SECOND_DAMAGE, SCOPED_PROPS,
                   catalog as _live_catalog, ceil_int, investment_bonus, num)

# An APPROXIMATION, not a measurement: net worth over time for a mid-pace
# game, the same for every hero. Pass your own curve for anything that matters.
# TODO(refine): souls over time is hero-dependent (farm speed, lane presence).
# Replace this with measured curves: sample games from each rank, average
# net worth over time per hero (the server's match store has per-player
# net_worth by timestamp), and pick the curve by hero + rank.
DEFAULT_SOULS_CURVE = [
    (0, 600), (300, 4_000), (600, 9_000), (900, 14_500), (1200, 20_000),
    (1800, 31_000), (2400, 42_000), (3000, 52_000),
]


@dataclass
class Target:
    """The enemy hero being shot. Resists are fractions (0.3 = 30%)."""
    distance_m: float = 15.0
    headshot_rate: float = 0.0       # fraction of bullets that crit
    bullet_resist: float = 0.0       # the enemy's own resist, before your shred
    spirit_resist: float = 0.0
    max_health: float = None         # None = mirror: same HP as the attacker at that time
    shred_on_objectives: bool = False   # unknown whether shred applies; see combat.py
    objective_enemies: int = 1       # enemies near a Walker (3+ adds weapon resist)
    abilities_on_objectives: bool = False   # many abilities do not hit structures
    # Optional ((distance_m, weight), ...): weapon numbers are averaged over it.
    # Range items switch on at exactly 15 m, so one fixed distance lets an
    # optimizer collect both close- and long-range bonuses at once.
    distance_mix: tuple = None
    # Fraction of bullets that land at all. headshot_rate is the share of LANDED
    # bullets that are headshots, so headshot procs fire at shots x accuracy x hs.
    accuracy: float = 1.0
    # ((effect_key, uptime), ...) overriding item_effects.json uptimes, where
    # effect_key is "<class_name>#<index of the effect in that item's list>".
    uptimes: tuple = ()


@dataclass
class Result:
    hero: str
    timeline: list
    events: list
    spikes: list
    unmodelled: list
    warnings: list
    target: Target = field(default_factory=Target)
    milestones: list = field(default_factory=list)   # first 4.8k per category
    rifts: list = field(default_factory=list)        # nominal Rift times (s)
    curve_source: str = ""

    def frame(self):
        import pandas as pd
        return pd.DataFrame(self.timeline)

    def at(self, t):
        """The last timeline row at or before t."""
        rows = [r for r in self.timeline if r["t"] <= t]
        return rows[-1] if rows else self.timeline[0]


# ------------------------------------------------------------------ souls curve

class SoulsCurve:
    """Piecewise-linear net worth over time; extrapolates the last slope."""

    def __init__(self, points):
        pts = sorted((float(t), float(s)) for t, s in points)
        if len(pts) < 2 or any(b[1] < a[1] for a, b in zip(pts, pts[1:])):
            raise ValueError("souls curve needs >= 2 points and must not decrease")
        self.t = [p[0] for p in pts]
        self.s = [p[1] for p in pts]

    def souls(self, t):
        i = max(1, min(bisect_right(self.t, t), len(self.t) - 1))
        t0, t1, s0, s1 = self.t[i - 1], self.t[i], self.s[i - 1], self.s[i]
        return s0 + (s1 - s0) * (t - t0) / (t1 - t0)

    def time_for(self, souls):
        """Earliest time net worth reaches `souls`."""
        if souls <= self.s[0]:
            return self.t[0]
        for i in range(1, len(self.s)):
            if self.s[i] >= souls:
                t0, t1, s0, s1 = self.t[i - 1], self.t[i], self.s[i - 1], self.s[i]
                return t0 + (t1 - t0) * (souls - s0) / (s1 - s0) if s1 > s0 else t1
        t0, t1, s0, s1 = self.t[-2], self.t[-1], self.s[-2], self.s[-1]
        if s1 <= s0:
            return float("inf")
        return t1 + (souls - s1) * (t1 - t0) / (s1 - s0)


# ------------------------------------------------------------------ build state

@dataclass
class State:
    level: int = 1                   # 1-based; boons = level - 1
    items: list = field(default_factory=list)
    tiers: dict = field(default_factory=dict)    # slot -> 0..3 (0 = unlocked)

    @property
    def boons(self):
        return self.level - 1


def upgraded_props(ability, tier, notes=None):
    """Ability properties with upgrade tiers 1..tier applied."""
    props = {k: copy(p) for k, p in ability.props.items()}
    for t in range(min(tier, len(ability.upgrades))):
        for up in ability.upgrades[t]:
            p = props.get(up.get("name"))
            if p is None:
                continue
            b = num(up.get("bonus"))
            kind = up.get("upgrade_type")
            if kind in (None, "EAddToBase"):
                p.base += b
            elif kind == "EMultiplyBase":
                p.base *= 1 + b / 100           # '33.33' pairs with EMultiplyScale 1.3333
            elif kind == "EAddToScale":
                p.scale += b
                p.scale_stat = p.scale_stat or up.get("scale_stat_filter") or "ETechPower"
            elif kind == "EMultiplyScale":
                if b:
                    p.scale *= b
                elif notes is not None:
                    notes.add(f"{ability.name} T{t + 1}: {p.name} scale multiplier 0 in data; ignored")
    return props


def falloff(weapon, d):
    a, b, end = weapon.falloff_start_m, weapon.falloff_end_m, weapon.falloff_end_scale
    if d <= a:
        return 1.0
    if d >= b or b <= a:
        return end
    return 1.0 + (end - 1.0) * (d - a) / (b - a)


def _sources(items, provided):
    """Each item's contribution to one stat, as fractions, for multiplicative stacking."""
    return [v / 100 for v in (i.stat(provided) for i in items) if v > 0]


def stats(hero, state, target):
    """Hero-wide stats after boons, items and investment."""
    items = state.items
    tot = lambda key: sum(i.stat(key) for i in items)
    spent = {}
    for i in items:
        spent[i.slot] = spent.get(i.slot, 0) + i.cost
    inv = {s: investment_bonus(hero.investment.get(s, []), spent.get(s, 0))
           for s in ("weapon", "spirit", "vitality")}

    cond = conditional_stats(items, boons=state.boons, overrides=dict(target.uptimes))
    spirit_flat = (state.boons * hero.per_boon["spirit"] + tot("MODIFIER_VALUE_TECH_POWER")
                   + inv["spirit"] + cond.get("spirit_flat", 0.0))
    spirit = spirit_flat * (1 + tot("MODIFIER_VALUE_TECH_POWER_PERCENT") / 100)

    d = target.distance_m
    close = sum(i.stat("MODIFIER_VALUE_CLOSE_RANGE_WEAPON_DAMAGE_INCREASE") for i in items
                if d <= i.stat("MODIFIER_VALUE_BONUS_WEAPON_DAMAGE_CLOSE_RANGE_MAX_RANGE"))
    long_ = sum(i.stat("MODIFIER_VALUE_LONG_RANGE_BULLET_DAMAGE_INCREASE") for i in items
                if d >= i.stat("MODIFIER_VALUE_BONUS_BULLET_DAMAGE_LONG_RANGE_MIN_RANGE"))
    weapon_pct = (tot("MODIFIER_VALUE_WEAPON_DAMAGE_INCREASE") + inv["weapon"] + close + long_
                  + cond.get("weapon_pct", 0.0))

    cdr_keep = 1.0
    for i in items:
        for p in i.props.values():
            if p.provided == "MODIFIER_VALUE_COOLDOWN_REDUCTION_PERCENTAGE" and i.applies(p):
                cdr_keep *= 1 - p.base / 100            # multiplicative (wiki)
    health = ((hero.base_health + state.boons * hero.per_boon["health"] + tot("MODIFIER_VALUE_HEALTH_MAX"))
              * (1 + (tot("MODIFIER_VALUE_HEALTH_MAX_PERCENT") + inv["vitality"]) / 100))
    return {
        "spirit": spirit,
        "weapon_pct": weapon_pct,
        "fire_rate_pct": tot("MODIFIER_VALUE_FIRE_RATE") + cond.get("fire_rate_pct", 0.0),
        "clip_pct": tot("MODIFIER_VALUE_AMMO_CLIP_SIZE_PERCENT") + cond.get("clip_pct", 0.0),
        "clip_flat": tot("MODIFIER_VALUE_AMMO_CLIP_SIZE"),
        "reload_pct": tot("MODIFIER_VALUE_RELOAD_SPEED"),
        "cooldown_mult": cdr_keep,
        "ult_cooldown_mult": 1 - tot("MODIFIER_VALUE_ULTIMATE_COOLDOWN_REDUCTION_PERCENTAGE") / 100,
        "duration_mult": 1 + tot("MODIFIER_VALUE_BONUS_ABILITY_DURATION_PERCENTAGE") / 100,
        "bonus_charges": tot("MODIFIER_VALUE_BONUS_ABILITY_CHARGES"),
        "charged_spirit": sum(i.props["BonusSpiritForChargedAbilities"].base for i in items
                              if "BonusSpiritForChargedAbilities" in i.props),
        "investment": inv,
        "health": health,
        "regen": hero.base_regen + tot("MODIFIER_VALUE_HEALTH_REGEN_PER_SECOND"),
        # lifesteal sources stack multiplicatively (wiki Lifesteal), like resists
        "bullet_lifesteal": stack(_sources(items, "MODIFIER_VALUE_BULLET_LIFESTEAL")
                                  + [cond.get("bullet_lifesteal_pct", 0.0) / 100]),
        "spirit_lifesteal": stack(_sources(items, "MODIFIER_VALUE_TECH_LIFESTEAL")
                                  + [cond.get("spirit_lifesteal_pct", 0.0) / 100]),
        # own resists, multiplicative stacking (wiki Damage_Resistance)
        "bullet_resist": stack([p.base / 100 for i in items for p in i.props.values()
                                if p.provided == "MODIFIER_VALUE_BULLET_ARMOR_DAMAGE_RESIST"
                                and i.applies(p) and p.base > 0]
                               + [cond.get("bullet_resist_pct", 0.0) / 100]),
        "spirit_resist": stack([p.base / 100 for i in items for p in i.props.values()
                                if p.provided == "MODIFIER_VALUE_TECH_RESIST"
                                and i.applies(p) and p.base > 0]
                               + [cond.get("spirit_resist_pct", 0.0) / 100]),
    }


def weapon_damage(hero, state, st, target, reload_overhead=0.0, window_s=3.0, headshot_rate=None):
    """Raw weapon output: falloff and crit applied, target resists NOT applied."""
    w = hero.weapon
    sp = st["spirit"]
    scale = lambda stat: hero.scaling_stats.get(stat, ("", 0.0))[1] * sp \
        if hero.scaling_stats.get(stat, ("",))[0] == "ETechPower" else 0.0
    hs = target.headshot_rate if headshot_rate is None else headshot_rate
    base = w.bullet_damage + state.boons * hero.per_boon["bullet"] + scale("EBulletDamage")
    bullet = base * (1 + st["weapon_pct"] / 100)
    crit = 1 + hs * (w.crit_multiplier - 1)
    shot = bullet * (w.damage_pellets or w.pellets) * falloff(w, target.distance_m) * crit
    sps = w.shots_per_second * (1 + (st["fire_rate_pct"] + scale("EFireRate")) / 100)
    clip = max(1, ceil_int((w.clip + st["clip_flat"] + scale("EClipSize")) * (1 + st["clip_pct"] / 100)))
    reload = max(0.0, w.reload_time(clip, 1 + st["reload_pct"] / 100))
    cycle = clip / sps + reload + reload_overhead
    acc = target.accuracy
    burst = shot * sps * acc
    sustained = shot * clip / cycle * acc
    empty = clip / sps
    window = (shot * sps * window_s * acc if window_s <= empty
              else shot * clip * acc + sustained * (window_s - empty))
    return {"base_bullet": base, "bullet": bullet, "shot": shot, "shots_per_s": sps, "clip": clip, "reload_s": reload,
            "weapon_burst_dps": burst, "weapon_dps": sustained, "weapon_window": window,
            "shot_rate": clip / cycle * acc, "hit_rate": clip / cycle * w.pellets * acc}


def ability_damage(hero, state, st, target, overrides, notes, wd=None):
    """Per ability: damage per cast split by type, effective cooldown, sustained DPS."""
    out = {}
    for slot, tier in state.tiers.items():
        ab = hero.abilities.get(slot)
        if ab is None:
            continue
        ov = overrides.get(slot, {})
        if ov.get("exclude"):
            continue
        props = upgraded_props(ab, tier, notes)
        charges = props["AbilityCharges"].base if "AbilityCharges" in props else 0
        sp = st["spirit"] + (st["charged_spirit"] if charges > 0 else 0)
        boons, wpct = state.boons, st["weapon_pct"]
        dur = (ov.get("duration") if "duration" in ov
               else (props["AbilityDuration"].base if "AbilityDuration" in props else 0.0)) * st["duration_mult"]
        kind = SPECIAL.get(ab.class_name)
        if kind == "fixation":
            continue                                  # a gun modifier, applied in evaluate()
        parts = None
        if kind == "kinetic_carbine" and wd is not None:
            parts = kinetic_carbine(props, wd, target, hero)
        elif kind == "ira_domini":
            parts = ira_domini(props, tier, sp, boons, wpct)
        per_cast = 0.0
        for name, p in ([] if parts is not None else props.items()):
            v = p.at(sp, boons, wpct)
            if name in PER_CAST_DAMAGE and v > 0:
                per_cast += v
            elif name in PER_SECOND_DAMAGE and v > 0:
                if dur > 0:
                    per_cast += v * dur
                else:
                    notes.add(f"{ab.name}: {name} is per second but no duration is known (pass duration=)")
            elif p.css_class == "tech_damage" and p.scale_stat and v > 0:
                notes.add(f"{ab.name}: {name} not counted (meaning unknown)")
        if parts is None:
            dtype = "weapon" if ab.name in WEAPON_DAMAGE_ABILITIES else "spirit"
            parts = {dtype: per_cast}
        parts = {k: v * ov.get("hits", 1) for k, v in parts.items()}
        per_cast = sum(parts.values())
        cd = props["AbilityCooldown"].base if "AbilityCooldown" in props else 0.0
        cd *= st["ult_cooldown_mult"] if slot == 4 else 1.0
        cd *= st["cooldown_mult"]
        if per_cast <= 0:
            notes.add(f"{ab.name}: no modelled damage (utility, weapon buff or unknown shape)")
            continue
        if cd <= 0:
            notes.add(f"{ab.name}: damage with no cooldown (passive or conditional); not counted")
            continue
        n_charges = charges + st["bonus_charges"] if charges > 0 else 1
        out[slot] = {"name": ab.name, "tier": tier, "per_cast": per_cast, "parts": parts,
                     "cooldown": cd, "charges": n_charges, "dps": per_cast / cd}
    return out


def item_damage(state, st, target, notes):
    """Damage items as 'fires every cooldown'. Everything else is reported."""
    out = {}
    for it in state.items:
        if it.effects:                 # researched spec in item_effects.json wins
            continue
        dprops = it.damage_props()
        if not dprops:
            continue
        # Only properties that ARE damage by name; tech_damage-styled triggers
        # (DamageThreshold, DamageWindow, ...) are conditions, not damage.
        useful = [p for p in dprops if p.name in PER_CAST_DAMAGE]
        if it.cooldown > 0 and useful:
            dmg = sum(p.at(st["spirit"]) for p in useful)
            out[it.name] = {"per_proc": dmg, "cooldown": it.cooldown, "dps": dmg / it.cooldown}
            notes.add(f"{it.name}: modelled as procing every {it.cooldown:g}s (condition assumed met)")
        else:
            notes.add(f"{it.name}: damage effect not modelled ({', '.join(p.name for p in dprops)})")
    return out


def _weapon_mixed(hero, state, target, reload_overhead, window_s, headshot_rate=None):
    """weapon_damage at target.distance_m, or averaged over target.distance_mix."""
    if not target.distance_mix:
        st = stats(hero, state, target)
        return weapon_damage(hero, state, st, target, reload_overhead, window_s, headshot_rate)
    from dataclasses import replace
    total_w = sum(w for _, w in target.distance_mix)
    acc = {}
    for d, w in target.distance_mix:
        td = replace(target, distance_m=d, distance_mix=None)
        r = weapon_damage(hero, state, stats(hero, state, td), td, reload_overhead, window_s,
                          headshot_rate)
        for k, v in r.items():
            acc[k] = acc.get(k, 0.0) + v * w / total_w
    return acc


def evaluate(hero, state, target, overrides=None, notes=None, reload_overhead=0.0, window_s=3.0,
             t=0.0, objectives=("Guardian", "Walker", "Mid-Boss")):
    """
    Everything at one moment. Damage numbers are AFTER the target's resists and
    your shred (vs the enemy hero), except the obj_* columns, which are vs that
    objective at time t. Sustain is what you heal per second while dealing the
    sustained damage to a hero.
    """
    notes = set() if notes is None else notes
    st = stats(hero, state, target)
    wd = _weapon_mixed(hero, state, target, reload_overhead, window_s)
    # Haze's Fixation: flat bullet damage from stacks, plus its T1 spirit proc.
    fix_ratio, fix_proc, fix_stacks = 1.0, 0.0, 0.0
    for slot, tier in state.tiers.items():
        a_ = hero.abilities.get(slot)
        if a_ is not None and SPECIAL.get(a_.class_name) == "fixation":
            fprops = upgraded_props(a_, tier, notes)
            flat, fix_proc, fix_stacks = fixation(fprops, wd, target, st["weapon_pct"], st["spirit"])
            if wd["bullet"] > 0:
                fix_ratio = (wd["bullet"] + flat) / wd["bullet"]
            for k in ("weapon_dps", "weapon_burst_dps", "weapon_window"):
                wd = {**wd, k: wd[k] * fix_ratio}
    ab = ability_damage(hero, state, st, target, overrides or {}, notes, wd=wd)
    it = item_damage(state, st, target, notes)
    target_hp = target.max_health if target.max_health else st["health"]
    fx = damage_and_sustain(state.items, st["spirit"], wd, target_hp, target.headshot_rate,
                            own_hp=st["health"], boons=state.boons,
                            overrides=dict(target.uptimes))
    for n in fx["unmodelled"]:
        notes.add(n)

    b_shred, s_shred = shred(state.items, st["spirit"], overrides=dict(target.uptimes))
    bullet_mult = 1 - final_resist([target.bullet_resist], b_shred)
    spirit_mult = 1 - final_resist([target.spirit_resist], s_shred)

    raw_weapon = wd["weapon_dps"] * fx["weapon_mult"] + fx["weapon_extra"]
    part = lambda kind: sum(a["parts"].get(kind, 0.0) / a["cooldown"] for a in ab.values())
    raw_spirit_ab = part("spirit") + fix_proc
    raw_weapon_ab = part("weapon")
    raw_pure_ab = part("pure")
    raw_spirit_items = sum(i["dps"] for i in it.values()) + fx["spirit_extra"] + fx["pct_hp"]
    weapon_dps = raw_weapon * bullet_mult
    ability_dps = raw_spirit_ab * spirit_mult + raw_weapon_ab * bullet_mult + raw_pure_ab
    item_dps = raw_spirit_items * spirit_mult + fx["true_dps"]      # true damage ignores resist
    total = weapon_dps + ability_dps + item_dps

    combo = (wd["weapon_window"] * bullet_mult
             + sum(a["charges"] * (a["parts"].get("weapon", 0.0) * bullet_mult
                                   + a["parts"].get("spirit", 0.0) * spirit_mult
                                   + a["parts"].get("pure", 0.0))
                   for a in ab.values())
             + sum(i["per_proc"] for i in it.values()) * spirit_mult
             + (fx["spirit_extra"] + fx["pct_hp"]) * window_s * spirit_mult)

    weapon_ab = raw_weapon_ab * bullet_mult
    heal = (st["bullet_lifesteal"] + fx["bullet_ls"]) * (weapon_dps + weapon_ab) \
        + (st["spirit_lifesteal"] + fx["spirit_ls"]) * (raw_spirit_ab * spirit_mult + item_dps) \
        + st["regen"] + fx["regen"] + fx["heals"] + fx["true_heal"]

    row = {
        "boons": state.boons, "spirit": st["spirit"], "weapon_pct": st["weapon_pct"],
        "health": st["health"], "cooldown_mult": st["cooldown_mult"],
        **{k: wd[k] for k in ("bullet", "shots_per_s", "clip", "weapon_burst_dps")},
        "weapon_dps": weapon_dps, "ability_dps": ability_dps, "item_dps": item_dps,
        "total_dps": total, "combo_damage": combo,
        "bullet_shred": stack(b_shred), "spirit_shred": stack(s_shred),
        "fixation_stacks": fix_stacks,
        "sustain_hps": heal,
        "ehp_bullet": st["health"] / max(1e-6, 1 - st["bullet_resist"]),
        "ehp_spirit": st["health"] / max(1e-6, 1 - st["spirit_resist"]),
        "abilities": ab, "items": {**it, **{k: {"dps": v} for k, v in fx["per_item"].items()}},
    }
    # Objectives: the gun and item procs; abilities only if asked, scaled by the
    # wiki's per-ability objective reductions. Headshots only where they land.
    if objectives:
        wd_flat = _weapon_mixed(hero, state, target, reload_overhead, window_s, headshot_rate=0.0)
        # Fixation's bonus deals 20% to objectives (wiki Haze, BossDamageScale)
        obj_fix = 1 + (fix_ratio - 1) * 0.2
        gun = {False: wd_flat["weapon_dps"] * fx["weapon_mult"] * obj_fix,
               True: wd["weapon_dps"] / fix_ratio * fx["weapon_mult"] * obj_fix}
        raw_spirit_obj = raw_spirit_items - fx["pct_hp"]          # %-HP effects: not on structures
        weapon_ab_obj = pure_obj = 0.0
        if target.abilities_on_objectives:
            for a in ab.values():
                m = OBJECTIVE_ABILITY_MULT.get(a["name"], 1.0) / a["cooldown"]
                weapon_ab_obj += a["parts"].get("weapon", 0.0) * m
                raw_spirit_obj += a["parts"].get("spirit", 0.0) * m
                pure_obj += a["parts"].get("pure", 0.0) * m
        for name in objectives:
            ob = OBJECTIVES[name]
            bm, sm = ob.multipliers(t, stack(b_shred), stack(s_shred), target.shred_on_objectives,
                                    target.objective_enemies)
            row[f"obj_{name}"] = (gun[ob.headshots] + weapon_ab_obj) * bm + raw_spirit_obj * sm + pure_obj
    return row


# ------------------------------------------------------------------ schedule

def _purchases(cat, curve, order, warnings):
    """[(time, item, consumed_component_or_None)] in order."""
    out, owned, spent, t_prev = [], [], 0.0, 0.0
    for entry in order or []:
        name, at = (entry, None) if isinstance(entry, str) else (
            (entry["item"], entry.get("at")) if isinstance(entry, dict) else (entry[0], entry[1]))
        item = cat.item(name)
        comp = next((o for o in owned if o.class_name in item.components), None)
        cost = item.cost - (comp.cost if comp else 0)
        # Unspent souls = net worth - spent, so net worth must cover all purchases so far.
        afford = curve.time_for(spent + cost)
        t = max(t_prev, afford, at or 0.0)
        if at is not None and afford > at + 1e-6:
            warnings.append(f"{item.name}: asked for at {at:.0f}s but not affordable until {afford:.0f}s")
        spent += cost
        if comp:
            owned.remove(comp)
        owned.append(item)
        out.append((t, item, comp))
        t_prev = t
    return out


def _ability_schedule(hero, level_times, order, warnings):
    """[(time, slot, new_tier)]; tier 0 = unlock. The order is a queue."""
    rewards = hero.level_rewards
    unlocks_by = [sum(r == "unlock" for r in rewards[:k + 1]) for k in range(len(rewards))]
    ap_by = [sum(r == "ap" for r in rewards[:k + 1]) for k in range(len(rewards))]
    names = {a.name.lower(): s for s, a in hero.abilities.items()}
    tiers, used_u, used_ap, t_prev, out = {}, 0, 0, 0.0, []
    for entry in order or []:
        slot = names.get(str(entry).lower()) if not isinstance(entry, int) else entry
        if slot not in hero.abilities:
            raise KeyError(f"{hero.name} has no ability {entry!r}")
        if slot not in tiers:
            ok = lambda k: unlocks_by[k] - used_u >= 1 and (slot != 4 or unlocks_by[k] >= 4)
            cost, kind = 0, "unlock"
        else:
            if tiers[slot] >= 3:
                warnings.append(f"{hero.abilities[slot].name}: already at T3; extra entry ignored")
                continue
            cost, kind = AP_TIER_COST[tiers[slot]], "ap"
            ok = lambda k: ap_by[k] - used_ap >= cost
        k = next((k for k in range(len(rewards)) if ok(k)), None)
        if k is None:
            warnings.append(f"{hero.abilities[slot].name}: never affordable; it and the rest of the order are dropped")
            break
        t = max(t_prev, level_times[k])
        if kind == "unlock":
            used_u += 1
            tiers[slot] = 0
        else:
            used_ap += cost
            tiers[slot] += 1
        out.append((t, slot, tiers[slot]))
        t_prev = t
    return out


# ------------------------------------------------------------------ simulate

def souls_curve_for(hero, rank_band="Phantom"):
    """Measured median curve for this hero and band (souls_curves.json), falling
    back to the band's all-hero curve, then to DEFAULT_SOULS_CURVE."""
    data = _measured_curves()
    band = data.get(rank_band) or {}
    pts = band.get(str(hero.id)) or band.get("all")
    if pts:
        return [(p[0], p[1]) for p in pts], f"measured: {rank_band}, " + (
            hero.name if str(hero.id) in band else "all heroes")
    return DEFAULT_SOULS_CURVE, "default guess (no measured curve)"


def _measured_curves():
    import json
    import os
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "souls_curves.json")
    if not os.path.exists(path):
        return {}
    if not hasattr(_measured_curves, "cache"):
        with open(path, encoding="utf-8") as f:
            _measured_curves.cache = json.load(f).get("curves", {})
    return _measured_curves.cache


RANK_BANDS = ("Initiate-Acolyte", "Sentinel-Ritualist", "Emissary-Oracle", "Phantom",
              "Ascendant", "Eternus")


def schedule(cat, h, items, ability_order, curve, end_s, warnings):
    """Every state change in time order: (t, order, kind, label, change)."""
    level_times = [curve.time_for(s) for s in h.level_souls]
    events = []
    for lvl in range(2, len(h.level_souls) + 1):
        t = level_times[lvl - 1]
        if t <= end_s:
            events.append((t, 0, "boon", f"boon {lvl - 1}", ("level", lvl)))
    for t, item, comp in _purchases(cat, curve, items, warnings):
        if t <= end_s:
            label = item.name + (f" (from {comp.name})" if comp else "")
            events.append((t, 1, "item", label, ("item", item, comp)))
    for t, slot, tier in _ability_schedule(h, level_times, ability_order, warnings):
        if t <= end_s:
            name = h.abilities[slot].name
            label = f"unlock {name}" if tier == 0 else f"{name} T{tier}"
            events.append((t, 2, "ability", label, ("tier", slot, tier)))
    events.sort(key=lambda e: (e[0], e[1]))
    return events


def apply_change(state, change):
    if change[0] == "level":
        state.level = change[1]
    elif change[0] == "item":
        if change[2] is not None:
            state.items.remove(change[2])
        state.items.append(change[1])
    else:
        state.tiers[change[1]] = change[2]


def simulate(hero, items=(), ability_order=(1, 2, 3, 4), souls_curve=None, end_s=1800,
             step_s=30, target=None, ability_overrides=None, spike_pct=5.0,
             reload_overhead=0.0, combo_window_s=3.0, cat=None, rank_band="Phantom",
             objectives=("Guardian", "Walker", "Mid-Boss")):
    """
    hero            name or id
    items           buy order: names, (name, at_seconds) or {"item": .., "at": ..}
    ability_order   queue of slots (1-4) or ability names; first mention unlocks
    souls_curve     [(t_s, net_worth)]; default: measured median for hero + rank_band
    target          Target(...) -- the enemy hero, plus objective switches
    ability_overrides  {slot: {"hits": n, "duration": s, "exclude": True}}
    """
    cat = cat or _live_catalog()
    h = cat.hero(hero)
    target = target or Target()
    if souls_curve is None:
        souls_curve, curve_source = souls_curve_for(h, rank_band)
    else:
        curve_source = "given"
    curve = SoulsCurve(souls_curve)
    warnings, notes = [], set()
    events = schedule(cat, h, items, ability_order, curve, end_s, warnings)

    state = State()
    ev = lambda t: evaluate(h, state, target, ability_overrides, notes, reload_overhead,
                            combo_window_s, t=t, objectives=objectives)

    def row(t, cur, label=""):
        r = {k: v for k, v in cur.items() if k not in ("abilities", "items")}
        r.update({"t": t, "souls": curve.souls(t), "event": label})
        for slot, a in cur["abilities"].items():
            r[f"dps_{a['name']}"] = a["dps"]
        return r

    timeline, log, milestones = [], [], []
    grid = [i * step_s for i in range(int(end_s // step_s) + 1)]
    gi = 0
    for t, _, kind, label, change in events:
        while gi < len(grid) and grid[gi] < t:
            timeline.append(row(grid[gi], ev(grid[gi])))
            gi += 1
        before = ev(t)
        inv_before = stats(h, state, target)["investment"] if change[0] == "item" else None
        apply_change(state, change)
        cur = ev(t)
        if inv_before is not None:
            inv_after = stats(h, state, target)["investment"]
            steps = [f"{k} investment {inv_before[k]:g}->{inv_after[k]:g}"
                     for k in inv_after if inv_after[k] != inv_before[k]]
            if steps:
                label += " + " + ", ".join(steps)
            for k in inv_after:
                spent = sum(i.cost for i in state.items if i.slot == k)
                if spent >= 4800 and not any(m["category"] == k for m in milestones):
                    milestones.append({"category": k, "t": t, "souls": curve.souls(t)})
        delta = cur["total_dps"] - before["total_dps"]
        pct = 100 * delta / before["total_dps"] if before["total_dps"] else float("inf")
        log.append({"t": t, "kind": kind, "label": label, "souls": curve.souls(t),
                    "dps_before": before["total_dps"], "dps_after": cur["total_dps"],
                    "delta": delta, "pct": pct,
                    "combo_before": before["combo_damage"], "combo_after": cur["combo_damage"]})
        timeline.append(row(t, cur, label))
    while gi < len(grid):
        timeline.append(row(grid[gi], ev(grid[gi])))
        gi += 1

    spikes = [e for e in log if e["pct"] >= spike_pct
              or (e["combo_before"] and 100 * (e["combo_after"] / e["combo_before"] - 1) >= spike_pct)]
    return Result(hero=h.name, timeline=timeline, events=log, spikes=spikes,
                  unmodelled=sorted(notes), warnings=warnings, target=target,
                  milestones=milestones, rifts=rift_times(end_s), curve_source=curve_source)
