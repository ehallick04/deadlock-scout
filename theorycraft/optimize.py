"""
theorycraft.optimize -- recommend a buy order.

A beam search over purchase orders. Each candidate order is played out on the
souls curve (purchases land as soon as affordable) and scored at checkpoints
across the game:

  damage     sustained DPS vs the enemy hero (after their resists and your
             shred), with extra weight in a window around each Rift
  objective  DPS vs Lane Guardians while their resist is falling (6-14 min)
             and vs Walkers (12-24 min), on their real resist clocks
  sustain    lane durability: effective HP against the lane's damage mix
             plus what you heal in a skirmish window, averaged over the lane

Three ways to trade them off (mode):
  "floor"     maximise damage + objective, with lane sustain held >= a floor
  "weighted"  one score from user weights (each part normalised to the
              no-items baseline so the weights are comparable)
  "frontier"  run the search at several sustain weights and return the builds
              that are not beaten on both damage and sustain

Three ways to handle the lane (lane):
  "locked"    the given lane items are bought first, then the search
  "matchup"   enemy laners named: their bullet/spirit damage mix sets the
              sustain metric, and the search picks lane items too
  "auto"      the search picks everything; lane mix assumed 50/50

The 4.8k investment spikes are not scored separately -- they show up in the
damage curve when they land; Result reports when each category reaches 4.8k.
Everything the simulator does not count is invisible to the optimizer too:
read MECHANICS.md before trusting a recommendation.
"""

from dataclasses import dataclass, field

from .combat import rift_times
from .simulate import (SoulsCurve, State, Target, apply_change, evaluate, schedule,
                       souls_curve_for)
from .data import catalog as _live_catalog

DEFAULT_DISTANCES = ((10, 0.35), (20, 0.45), (30, 0.20))   # fight ranges (m), a judgement call
LANE_END_S = 540                 # laning phase for the sustain metric (~9 min)
SKIRMISH_S = 30                  # heal counted over this window in lane durability
RIFT_HALF_WINDOW_S = 90
GUARDIAN_WINDOW = (360, 840)     # guardian resist <= 0 from 6:00
WALKER_WINDOW = (720, 1440)


@dataclass
class Weights:
    damage: float = 1.0
    objective: float = 0.5
    sustain: float = 0.5
    rift: float = 1.0            # extra damage weight inside Rift windows (1 = double)


@dataclass
class Plan:
    items: list
    score: float
    parts: dict                  # damage, objective, sustain (raw, unnormalised)
    lane_sustain: float
    milestones: list = field(default_factory=list)
    notes: list = field(default_factory=list)


def lane_mix(cat, enemy_names, boons=6):
    """Bullet share of the named laners' early burst (3 s combo, no items)."""
    if not enemy_names:
        return 0.5
    shares = []
    for name in enemy_names:
        h = cat.hero(name)
        st = State(level=boons + 1, tiers={1: 0, 2: 0, 3: 0})
        r = evaluate(h, st, Target(), objectives=(), window_s=3.0)
        weapon = r["weapon_burst_dps"] * 3.0
        spirit = sum(a["per_cast"] for a in r["abilities"].values())
        shares.append(weapon / (weapon + spirit) if weapon + spirit else 0.5)
    return sum(shares) / len(shares)


def shop(cat):
    """Normal-mode shop, with interchangeable utility items collapsed."""
    items = [i for i in cat.items.values() if not i.disabled and 1 <= i.tier <= 4]
    seen, out = {}, []
    for i in sorted(items, key=lambda i: (i.tier, i.slot, i.name)):
        sig = _signature(i)
        if sig is None:                       # no modelled value beyond investment
            key = ("filler", i.slot, i.tier)
            if key in seen:
                continue
            seen[key] = i
        out.append(i)
    return out


def _signature(item):
    relevant = [(p.provided, p.base) for p in item.props.values()
                if p.provided and not p.conditional and p.base]
    relevant = [r for r in relevant if any(k in r[0] for k in (
        "WEAPON_DAMAGE", "TECH_POWER", "FIRE_RATE", "AMMO", "RELOAD", "COOLDOWN", "DURATION",
        "CHARGES", "HEALTH", "LIFESTEAL", "RESIST", "REGEN"))]
    if not relevant and not item.effects and not item.damage_props():
        return None
    return tuple(sorted(relevant))


class Scorer:
    def __init__(self, cat, hero, ability_order, curve, target, end_s, mix, step_s=60, rift_weight=1.0):
        self.cat, self.h, self.order = cat, hero, list(ability_order)
        self.curve, self.target, self.end_s, self.mix = curve, target, end_s, mix
        self.checkpoints = list(range(step_s, end_s + 1, step_s))
        self.rifts = rift_times(end_s)
        self.memo = {}
        self.rift_weight = rift_weight

    def _eval(self, state, t):
        key = (state.level, tuple(sorted(i.class_name for i in state.items)),
               tuple(sorted(state.tiers.items())), t)
        r = self.memo.get(key)
        if r is None:
            r = evaluate(self.h, state, self.target, t=t, objectives=("Guardian", "Walker"))
            r = {k: r[k] for k in ("total_dps", "sustain_hps", "health", "ehp_bullet",
                                   "ehp_spirit", "obj_Guardian", "obj_Walker")}
            self.memo[key] = r
        return r

    def profile(self, items):
        warnings = []
        events = schedule(self.cat, self.h, items, self.order, self.curve, self.end_s, warnings)
        state, rows, ei = State(), [], 0
        for t in self.checkpoints:
            while ei < len(events) and events[ei][0] <= t:
                apply_change(state, events[ei][4])
                ei += 1
            rows.append((t, self._eval(state, t)))
        return rows

    def parts(self, items):
        rows = self.profile(items)
        dmg = obj = lane = 0.0
        dmg_w = obj_n = lane_n = 0
        for t, r in rows:
            w = 1.0 + (self.rift_weight if any(abs(t - x) <= RIFT_HALF_WINDOW_S for x in self.rifts) else 0)
            dmg += w * r["total_dps"]
            dmg_w += w
            if GUARDIAN_WINDOW[0] <= t <= GUARDIAN_WINDOW[1]:
                obj += r["obj_Guardian"]
                obj_n += 1
            if WALKER_WINDOW[0] <= t <= WALKER_WINDOW[1]:
                obj += r["obj_Walker"]
                obj_n += 1
            if t <= LANE_END_S:
                taken = self.mix * (r["health"] / r["ehp_bullet"]) + \
                    (1 - self.mix) * (r["health"] / r["ehp_spirit"])
                lane += r["health"] / taken + SKIRMISH_S * r["sustain_hps"]
                lane_n += 1
        return {"damage": dmg / max(dmg_w, 1), "objective": obj / max(obj_n, 1),
                "sustain": lane / max(lane_n, 1)}


def recommend(hero, ability_order, mode="floor", lane="auto", lane_items=(), enemy_laners=(),
              weights=None, sustain_floor=None, n_items=10, beam=6, candidates=30,
              end_s=1800, rank_band="Phantom", target=None, cat=None, frontier_weights=(0, 0.5, 1, 2, 4),
              souls_curve=None):
    """
    Returns a list of Plan (one for floor/weighted, several for frontier), best first.

    sustain_floor  lane durability (HP) the floor mode must keep; default = the
                   lane items' (locked) or no-items value x 1.15
    n_items        purchases to plan after the lane items
    beam, candidates  search width; candidates per step are pre-ranked by a
                   one-step look-ahead, so the search is not exhaustive
    """
    cat = cat or _live_catalog()
    h = cat.hero(hero)
    target = target or Target(distance_mix=DEFAULT_DISTANCES)
    curve = SoulsCurve(souls_curve or souls_curve_for(h, rank_band)[0])
    mix = lane_mix(cat, enemy_laners) if lane == "matchup" else 0.5
    sc = Scorer(cat, h, ability_order, curve, target, end_s, mix,
                rift_weight=(weights or Weights()).rift)
    prefix = [cat.item(n).name for n in lane_items] if lane == "locked" else []
    base = sc.parts(prefix)
    norm = {k: max(v, 1e-6) for k, v in sc.parts([]).items()}
    floor = sustain_floor if sustain_floor is not None else base["sustain"] * 1.15
    pool = shop(cat)

    def search(w, use_floor):
        def score(p):
            s = (w.damage * p["damage"] / norm["damage"] + w.objective * p["objective"] / norm["objective"])
            if use_floor:
                short = max(0.0, floor - p["sustain"]) / max(floor, 1e-6)
                # lexicographic: any build under the floor ranks below every build
                # that meets it, and among those under it, closer is better
                return s - 1000 * short - (100 if short > 0 else 0)
            return s + w.sustain * p["sustain"] / norm["sustain"]

        # Search by SPEND, not by item count: two orders are only compared once
        # they have spent the same souls (bucketed to 800). Comparing equal-length
        # prefixes instead rewards rushing one big item, because a lone 6,400
        # item beats a lone 800 one over the game regardless of what follows.
        start_cost = _spend(prefix, cat)
        buckets = {start_cost // BUCKET: [(score(base), prefix, base)]}
        budget = sc.curve.souls(sc.end_s) - cat.starting_souls
        best_final = []
        for k in range(start_cost // BUCKET, int(budget // BUCKET) + 1):
            states = sorted(buckets.pop(k, []), key=lambda x: -x[0])[:beam]
            for s_, order, p in states:
                if len(order) - len(prefix) >= n_items:
                    best_final.append((s_, order, p))
                    continue
                owned = set(order)
                cands = [i for i in pool if i.name not in owned and not _consumed(i, order, cat)]
                quick = sorted(((_quick(sc, order, i, w), i) for i in cands), key=lambda x: -x[0])
                grew = False
                for _, i in quick[:candidates]:
                    o = order + [i.name]
                    cost = _spend(o, cat)
                    if cost > budget:
                        continue
                    pp = sc.parts(o)
                    buckets.setdefault(cost // BUCKET, []).append((score(pp), o, pp))
                    grew = True
                if not grew:
                    best_final.append((s_, order, p))
        for rest in buckets.values():
            best_final.extend(rest)
        best_final.sort(key=lambda x: -x[0])
        out, seen = [], set()
        for b_ in best_final:
            key = tuple(b_[1])
            if key not in seen:
                seen.add(key)
                out.append(b_)
        return out[:beam]

    def plan(b):
        s, order, p = b
        return Plan(items=order, score=s, parts=p, lane_sustain=p["sustain"],
                    notes=[f"lane damage mix: {mix:.0%} bullet", f"sustain floor: {floor:.0f}"
                           if mode == "floor" else ""])

    if mode == "floor":
        return [plan(b) for b in search(weights or Weights(sustain=0), True)[:3]]
    if mode == "weighted":
        return [plan(b) for b in search(weights or Weights(), False)[:3]]
    if mode == "frontier":
        cands = []
        for sw in frontier_weights:
            w = Weights(damage=(weights or Weights()).damage, objective=(weights or Weights()).objective,
                        sustain=sw)
            cands.extend(plan(b) for b in search(w, False)[:2])
        return pareto(cands)
    raise ValueError(f"mode {mode!r}")


BUCKET = 800


def _spend(order, cat):
    """Souls spent on an order, components credited."""
    owned, total = [], 0
    for n in order:
        it = cat.item(n)
        comp = next((o for o in owned if o.class_name in it.components), None)
        total += it.cost - (comp.cost if comp else 0)
        if comp:
            owned.remove(comp)
        owned.append(it)
    return total


def _quick(sc, order, item, w):
    """Cheap pre-rank: damage/objective gain per soul at the moment of purchase."""
    t = min(sc.end_s, sc.curve.time_for(_spend(order, sc.cat) + item.cost))
    events = schedule(sc.cat, sc.h, order, sc.order, sc.curve, t, [])
    st = State()
    for e in events:
        apply_change(st, e[4])
    before = sc._eval(st, round(t))
    st2 = State(level=st.level, items=list(st.items), tiers=dict(st.tiers))
    comp = next((o for o in st2.items if o.class_name in item.components), None)
    if comp:
        st2.items.remove(comp)
    st2.items.append(item)
    after = sc._eval(st2, round(t))
    gain = (w.damage * (after["total_dps"] - before["total_dps"])
            + w.objective * (after["obj_Guardian"] - before["obj_Guardian"])
            + (w.sustain or 0.5) * 0.1 * (after["sustain_hps"] - before["sustain_hps"]) * SKIRMISH_S)
    return gain / max(item.cost - (comp.cost if comp else 0), 1)


def _consumed(item, order, cat):
    """An item already used as a component of something in the order."""
    for n in order:
        if item.class_name in cat.item(n).components:
            return True
    return False


def pareto(plans):
    """Plans not beaten on both damage(+objective) and sustain; damage-first."""
    dmg = lambda p: p.parts["damage"] + p.parts["objective"]
    out, seen = [], set()
    for p in plans:
        k = tuple(p.items)
        if k in seen:
            continue
        seen.add(k)
        if not any(dmg(q) >= dmg(p) and q.parts["sustain"] >= p.parts["sustain"]
                   and (dmg(q) > dmg(p) or q.parts["sustain"] > p.parts["sustain"]) for q in plans):
            out.append(p)
    return sorted(out, key=lambda p: -dmg(p))
