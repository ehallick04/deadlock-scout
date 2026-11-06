"""
theorycraft.data -- hero, weapon, ability and item data for the simulator.

Everything here comes from the public asset endpoints and is parsed into small
dataclasses. Field names were established against live responses (6 Nov 2026);
see MECHANICS.md for the field map and what is missing.

Data is read from the project's local snapshots, heroes.json and items.json in
the repo root (the same files deadlock.heroes() / deadlock.items() keep), so a
simulation never touches the network. The files are gitignored and re-fetchable.
After a patch, call refresh_catalog() to refetch both files.
The parsed catalog is also cached in process (catalog()'s lru_cache).

Snapshot taken 6 Nov 2026. The files it replaced (31 Aug / 1 Sep) predated a
patch that changed every hero's boon profile, so check snapshot_age_days()
when numbers look off.
"""

from dataclasses import dataclass, field
from functools import lru_cache
import json
import math
import os
import re
import time
import warnings

UNITS_PER_METRE = 39.37      # weapon ranges are in game units; this reproduces the wiki's metres

# MODIFIER_VALUE_* channels the simulator reads from items. Anything else an
# item provides is carried in Item.stats but has no effect on damage.
DAMAGE_STATS = {
    "MODIFIER_VALUE_WEAPON_DAMAGE_INCREASE",     # +% weapon damage
    "MODIFIER_VALUE_TECH_POWER",                 # flat spirit power
    "MODIFIER_VALUE_TECH_POWER_PERCENT",         # +% spirit power
    "MODIFIER_VALUE_FIRE_RATE",                  # +% fire rate
    "MODIFIER_VALUE_AMMO_CLIP_SIZE_PERCENT",     # +% ammo
    "MODIFIER_VALUE_AMMO_CLIP_SIZE",             # flat ammo
    "MODIFIER_VALUE_RELOAD_SPEED",               # % reload time change
    "MODIFIER_VALUE_COOLDOWN_REDUCTION_PERCENTAGE",
    "MODIFIER_VALUE_BONUS_ABILITY_DURATION_PERCENTAGE",
    "MODIFIER_VALUE_BONUS_ABILITY_CHARGES",
    "MODIFIER_VALUE_CLOSE_RANGE_WEAPON_DAMAGE_INCREASE",
    "MODIFIER_VALUE_BONUS_WEAPON_DAMAGE_CLOSE_RANGE_MAX_RANGE",
    "MODIFIER_VALUE_LONG_RANGE_BULLET_DAMAGE_INCREASE",
    "MODIFIER_VALUE_BONUS_BULLET_DAMAGE_LONG_RANGE_MIN_RANGE",
    "MODIFIER_VALUE_HEALTH_MAX",
    "MODIFIER_VALUE_HEALTH_MAX_PERCENT",
}

# Properties that share a stat channel with the global stat but do not apply
# globally. Kept apart so they neither inflate the stat nor vanish silently.
SCOPED_PROPS = {
    "BonusSpiritForChargedAbilities": "spirit power for charged abilities only",
    "CooldownReductionOnChargedAbilities": "cooldown reduction for charged abilities only",
    "ImbuedTechPower": "spirit power for the one imbued ability (not modelled)",
    "TechPowerReduction": "enemy spirit power debuff (not modelled)",
}

# Ability damage properties, by how the number is meant. Inferred from the
# property name -- the API does not say whether a number is per hit or per
# second. Everything not listed is reported, not guessed at.
PER_CAST_DAMAGE = {
    "Damage", "ImpactDamage", "ExplosionDamage", "ExplodeDamage", "StompDamage",
    "BonusDamage", "LandingDamage", "ExplosiveDamage", "PulseDamage", "SwapDamage",
    "LightningStrikeDamage", "BoloHitDamage", "CenterDamage", "BaseDamage", "ExplosionBulletDamage",
}
PER_SECOND_DAMAGE = {"DPS", "DamagePerSecond", "FireDPS"}

AP_TIER_COST = (1, 2, 5)     # wiki: ability tiers cost 1, 2 then 5 points


def num(value, default=0.0):
    """'1.5m' / '40' / 30.0 / '-1.0' / None -> float."""
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    m = re.match(r"\s*(-?\d+(?:\.\d+)?)", str(value))
    return float(m.group(1)) if m else default


# ------------------------------------------------------------------ dataclasses

@dataclass
class Prop:
    """One numeric property: value = base + scale * <scale_stat>."""
    name: str
    base: float
    scale: float = 0.0
    scale_stat: str = ""         # "ETechPower" for spirit scaling
    css_class: str = ""
    provided: str = ""           # MODIFIER_VALUE_* this property feeds, if any
    conditional: bool = False    # usage_flags ConditionallyApplied

    def at(self, spirit, boons=0, weapon_pct=0.0):
        if self.scale_stat == "ETechPower":
            return self.base + self.scale * spirit
        if self.scale_stat == "ELevelUpBoons":       # e.g. Headhunter: +4 per boon
            return self.base + self.scale * boons
        if self.scale_stat == "EBaseWeaponDamageIncrease":
            # Bonus Weapon Damage scaling (wiki Weapon_Damage: items + investment,
            # not boons), per PERCENTAGE POINT of bonus weapon damage. No page states
            # the unit, but the data only makes sense this way: Haze's Fixation T3
            # adds 0.00035 per stack per unit -- per fraction that is nothing, per
            # point it is +0.035/stack at +100%. Ira Domini x1.5 -> +150 at +100%.
            return self.base + self.scale * weapon_pct
        return self.base


# Weapons whose pellets SHARE one shot's damage (the target takes bullet_damage
# per shot however many pellets hit): wiki Baba and Drifter pages, and the
# Weapon_Damage page's "Spreadshots" exceptions. No data flag marks this.
SHARED_PELLET_HEROES = {"Baba", "Drifter"}


@dataclass
class Weapon:
    bullet_damage: float
    pellets: int
    cycle_time: float
    burst: int
    intra_burst: float
    clip: int
    reload: float
    falloff_start_m: float
    falloff_end_m: float
    falloff_end_scale: float
    crit_multiplier: float
    damage_pellets: int = 0          # pellets that each deal bullet_damage (0 = pellets)
    reload_single: bool = False      # shells loaded one at a time (Abrams, Silver)
    reload_initial: float = 0.0
    max_spin_cycle: float = 0.0      # spin-up guns: cycle time when fully spun

    def reload_time(self, clip, reload_mult=1.0):
        """Full reload. Single-shell weapons: initial delay + per-shell time x clip."""
        if self.reload_single:
            return (self.reload_initial + self.reload * clip) * reload_mult
        return self.reload * reload_mult

    @property
    def shots_per_second(self):
        """
        Bursts: burst / (cycle + burst x intra_burst) -- the API's own field and
        the wiki's Hero_Comparison_Table agree (Seven, Lash, Paradox, Sinclair).
        Single shots: 1 / cycle. Paige, Rat King, Solomon and Doorman carry an
        intra_burst time on a 1-shot "burst"; the API adds it (Paige 1.67/s), the
        wiki does not (2.0/s). We follow the wiki: no gap inside a 1-bullet burst.
        Spin-up guns (McGinnis, Victor) are taken fully spun, as the wiki does.
        """
        cycle = self.max_spin_cycle or self.cycle_time
        if self.burst > 1:
            return self.burst / (cycle + self.burst * self.intra_burst)
        return 1.0 / cycle


@dataclass
class Ability:
    class_name: str
    name: str
    slot: int                    # 1..4, 4 = ultimate
    kind: str                    # signature / ultimate
    props: dict                  # name -> Prop
    upgrades: list               # 3 lists of property_upgrade dicts


@dataclass
class Item:
    class_name: str
    name: str
    cost: int
    tier: int
    slot: str                    # weapon / vitality / spirit
    components: list
    activation: str
    props: dict                  # name -> Prop
    disabled: bool = False
    effects: list = field(default_factory=list)   # hand-modelled, from item_effects.json
    # Props the data does NOT flag as conditional but that are (active-only,
    # buff-only, imbue-only, enemy debuffs); listed in item_effects.json.
    excluded: frozenset = frozenset()

    def applies(self, p):
        """Does this prop hold all the time, for everyone it says it does?"""
        return not p.conditional and p.name not in SCOPED_PROPS and p.name not in self.excluded

    def stat(self, provided):
        """Sum of unconditional, globally applying props feeding one stat channel."""
        return sum(p.base for p in self.props.values()
                   if p.provided == provided and self.applies(p))

    @property
    def cooldown(self):
        p = self.props.get("AbilityCooldown")
        return p.base if p else 0.0

    def damage_props(self):
        return [p for p in self.props.values()
                if p.css_class == "tech_damage" and not p.conditional]


@dataclass
class Hero:
    id: int
    name: str
    weapon: Weapon
    abilities: dict              # slot -> Ability
    level_souls: list            # souls needed for level 1..N (index 0 = level 1)
    level_rewards: list          # "unlock" / "ap" / None per level
    per_boon: dict               # bullet, spirit, health, melee
    base_health: float
    base_regen: float
    scaling_stats: dict          # EStat -> (scaling_stat, scale)
    investment: dict             # weapon/spirit/vitality -> [(threshold, bonus)]

    @property
    def max_boons(self):
        return len(self.level_souls) - 1


@dataclass
class Catalog:
    heroes: dict = field(default_factory=dict)      # lowercase name -> Hero
    items: dict = field(default_factory=dict)       # class_name -> Item
    starting_souls: int = 600

    def hero(self, name):
        key = str(name).strip().lower()
        if key in self.heroes:
            return self.heroes[key]
        for h in self.heroes.values():
            if str(h.id) == key:
                return h
        raise KeyError(f"unknown hero {name!r}")

    def item(self, name):
        key = str(name).strip().lower()
        if key in self.items:
            return self.items[key]
        hits = [i for i in self.items.values() if i.name.lower() == key]
        if not hits:
            hits = [i for i in self.items.values() if i.class_name == "upgrade_" + key]
        live = [i for i in hits if not i.disabled] or hits
        if len(live) != 1:
            raise KeyError(f"item {name!r}: {len(live)} matches")
        return live[0]


# ------------------------------------------------------------------ parsing

def parse_prop(name, raw):
    sf = raw.get("scale_function") or {}
    scale_stat = sf.get("specific_stat_scale_type") or ""
    # 78 ability damage props carry scale_function_tech_damage with a stat_scale
    # but NO specific_stat_scale_type. The class itself means spirit power
    # (e.g. Apollo's Disengaging Sigil x1.3, which the wiki shows as spirit).
    if sf.get("class_name") == "scale_function_tech_damage" and scale_stat not in (
            "ETechPower", "ELevelUpBoons", "EBaseWeaponDamageIncrease"):
        # missing, or odd (Mina's Rake carries "EStatsCount"; the wiki shows x1.0 spirit)
        scale_stat = "ETechPower"
    if sf.get("class_name") == "scale_function_ability_weapon_damage":
        scale_stat = "EBaseWeaponDamageIncrease"
    return Prop(
        name=name,
        base=num(raw.get("value")),
        scale=num(sf.get("stat_scale")) if sf.get("stat_scale") is not None else 0.0,
        scale_stat=scale_stat if sf.get("stat_scale") is not None else "",
        css_class=raw.get("css_class") or "",
        provided=raw.get("provided_property_type") or "",
        conditional="ConditionallyApplied" in (raw.get("usage_flags") or []),
    )


def parse_props(raw_props):
    return {k: parse_prop(k, v) for k, v in (raw_props or {}).items() if isinstance(v, dict)}


def parse_weapon(raw):
    w = raw.get("weapon_info") or {}
    return Weapon(
        bullet_damage=num(w.get("bullet_damage")),
        pellets=int(w.get("bullets") or 1),
        cycle_time=num(w.get("cycle_time"), 1.0),
        burst=int(w.get("burst_shot_count") or 1),
        intra_burst=num(w.get("intra_burst_cycle_time")),
        clip=int(w.get("clip_size") or 1),
        reload=num(w.get("reload_duration")),
        falloff_start_m=num(w.get("damage_falloff_start_range")) / UNITS_PER_METRE,
        falloff_end_m=num(w.get("damage_falloff_end_range")) / UNITS_PER_METRE,
        falloff_end_scale=num(w.get("damage_falloff_end_scale"), 1.0),
        crit_multiplier=num(w.get("crit_bonus_start"), 1.65),
        reload_single=bool(w.get("reload_single_bullets")),
        reload_initial=num(w.get("reload_single_bullets_initial_delay")),
        max_spin_cycle=num(w.get("max_spin_cycle_time")) if w.get("spins_up") else 0.0,
    )


def parse_ability(raw, slot):
    return Ability(
        class_name=raw["class_name"], name=raw.get("name") or raw["class_name"],
        slot=slot, kind=raw.get("ability_type") or "",
        props=parse_props(raw.get("properties")),
        upgrades=[list(u.get("property_upgrades") or []) for u in (raw.get("upgrades") or [])],
    )


def parse_item(raw):
    return Item(
        class_name=raw["class_name"], name=raw.get("name") or raw["class_name"],
        cost=int(raw.get("cost") or 0), tier=int(raw.get("item_tier") or 0),
        slot=raw.get("item_slot_type") or "",
        components=list(raw.get("component_items") or []),
        activation=raw.get("activation") or "",
        props=parse_props(raw.get("properties")),
        disabled=bool(raw.get("disabled")),
    )


REWARD = {"EAbilityUnlocks": "unlock", "EAbilityPoints": "ap"}


def parse_hero(raw, items_by_class, starting_souls):
    slots = raw.get("items") or {}
    weapon_raw = items_by_class.get(slots.get("weapon_primary"), {})
    abilities = {}
    for n in (1, 2, 3, 4):
        a = items_by_class.get(slots.get(f"signature{n}"))
        if a:
            abilities[n] = parse_ability(a, n)

    levels = sorted((raw.get("level_info") or {}).items(), key=lambda kv: int(kv[0]))
    # required_gold excludes the starting souls: 600 + 200 = 800 for boon 1,
    # which is what the wiki's soul table and the live convar both say.
    level_souls = [starting_souls + int(v.get("required_gold") or 0) for _, v in levels]
    level_rewards = [next((REWARD[c] for c in v.get("bonus_currencies") or [] if c in REWARD), None)
                     for _, v in levels]

    up = raw.get("standard_level_up_upgrades") or {}
    ss = raw.get("starting_stats") or {}
    weapon = parse_weapon(weapon_raw)
    weapon.damage_pellets = 1 if raw["name"] in SHARED_PELLET_HEROES else weapon.pellets
    return Hero(
        id=raw["id"], name=raw["name"],
        weapon=weapon,
        abilities=abilities,
        level_souls=level_souls, level_rewards=level_rewards,
        per_boon={
            "bullet": num(up.get("MODIFIER_VALUE_BASE_BULLET_DAMAGE_FROM_LEVEL")),
            "spirit": num(up.get("MODIFIER_VALUE_TECH_POWER")),
            "health": num(up.get("MODIFIER_VALUE_BASE_HEALTH_FROM_LEVEL")),
            "melee": num(up.get("MODIFIER_VALUE_BASE_MELEE_DAMAGE_FROM_LEVEL")),
        },
        base_health=num((ss.get("max_health") or {}).get("value")),
        base_regen=num((ss.get("base_health_regen") or {}).get("value")),
        scaling_stats={k: (v.get("scaling_stat"), num(v.get("scale")))
                       for k, v in (raw.get("scaling_stats") or {}).items()},
        investment={slot: [(int(b["gold_threshold"]), num(b["bonus"])) for b in rows]
                    for slot, rows in (raw.get("cost_bonuses") or {}).items()},
    )


def build_catalog(heroes_raw, items_raw, starting_souls=600, playable_only=True, effects=None,
                  excluded=None):
    """Pure: raw JSON in, Catalog out. Tests feed this planted data.
    effects: class_name -> [effect dicts] (see effects.py); none by default."""
    by_class = {i["class_name"]: i for i in items_raw if "class_name" in i}
    cat = Catalog(starting_souls=starting_souls)
    for i in items_raw:
        if i.get("type") == "upgrade" and i.get("shopable"):
            it = parse_item(i)
            it.effects = list((effects or {}).get(it.class_name, []))
            it.excluded = frozenset((excluded or {}).get(it.class_name, []))
            cat.items[it.class_name] = it
    for h in heroes_raw:
        if playable_only and (not h.get("player_selectable") or h.get("disabled")):
            continue
        hero = parse_hero(h, by_class, starting_souls)
        cat.heroes[hero.name.lower()] = hero
    return cat


# ------------------------------------------------------------------ loaders

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEROES_FILE = os.path.join(REPO, "heroes.json")
ITEMS_FILE = os.path.join(REPO, "items.json")

# Live convar citadel_player_starting_gold, checked 6 Nov 2026 and re-checked by
# refresh_catalog(). Level thresholds are this plus level_info.required_gold.
STARTING_SOULS = 600


def _read_or_fetch(path, endpoint, refresh):
    if os.path.exists(path) and not refresh:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    import api   # deadlock-scout's HTTP layer; only touched when a snapshot is missing or refreshed
    data = api.get_json(endpoint, refresh=True)
    api.save_json(data, path)
    return data


def fetch_raw(refresh=False):
    """heroes, items, starting souls -- from the local snapshots unless refresh."""
    heroes = _read_or_fetch(HEROES_FILE, "/v1/assets/heroes", refresh)
    items = _read_or_fetch(ITEMS_FILE, "/v1/assets/items", refresh)
    start = STARTING_SOULS
    if refresh:
        import api
        live = int(api.get_json("/v1/assets/convars/citadel_player_starting_gold",
                                refresh=True)["default"])
        if live != STARTING_SOULS:
            warnings.warn(f"starting souls is now {live}, not {STARTING_SOULS}; "
                          "update STARTING_SOULS in theorycraft/data.py")
            start = live
    return heroes, items, start


def snapshot_age_days():
    """Age of the older of the two snapshot files, in days (None if missing)."""
    if not (os.path.exists(HEROES_FILE) and os.path.exists(ITEMS_FILE)):
        return None
    oldest = min(os.path.getmtime(HEROES_FILE), os.path.getmtime(ITEMS_FILE))
    return (time.time() - oldest) / 86400


@lru_cache(maxsize=1)
def catalog():
    """The parsed catalog from the local snapshots, cached in process."""
    from .effects import load_effects, load_excluded
    heroes, items, start = fetch_raw()
    return build_catalog(heroes, items, start, effects=load_effects(), excluded=load_excluded())


def refresh_catalog():
    """Refetch heroes.json / items.json from the API (after a patch) and reparse."""
    fetch_raw(refresh=True)
    catalog.cache_clear()
    return catalog()


def investment_bonus(table, spent):
    """Accumulated bonus for `spent` souls in one category (thresholds are cumulative)."""
    bonus = 0.0
    for threshold, value in table:
        if spent >= threshold:
            bonus = value
    return bonus


def ceil_int(x):
    return int(math.ceil(x - 1e-9))
