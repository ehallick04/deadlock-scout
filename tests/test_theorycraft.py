"""
Tests for theorycraft.

Two kinds:
  * planted -- a synthetic hero and items with round numbers, every expected
    value worked out by hand in the comment beside it;
  * real    -- the local heroes.json / items.json snapshot against numbers
    published on deadlock.wiki and against the API's own derived weapon fields.
    Skipped if the snapshot is missing and cannot be fetched.
"""
import math

import pytest

from theorycraft import SoulsCurve, Target, build_catalog, evaluate, simulate
from theorycraft.data import investment_bonus
from theorycraft.simulate import State, falloff, upgraded_props

# ------------------------------------------------------------------ planted data

U = 39.37   # game units per metre


def _levels():
    # required_gold excludes the 600 starting souls, as in the live data.
    req = [0, 200, 500, 900, 1400, 2000, 2600, 3200]
    cur = ["EAbilityUnlocks", "EAbilityPoints", "EAbilityUnlocks", "EAbilityPoints",
           "EAbilityUnlocks", "EAbilityPoints", "EAbilityPoints", "EAbilityUnlocks"]
    out = {str(i + 1): {"required_gold": r, "bonus_currencies": [c]} for i, (r, c) in enumerate(zip(req, cur))}
    for i in range(9, 20):                       # plenty of AP afterwards
        out[str(i)] = {"required_gold": 3200 + 500 * (i - 8), "bonus_currencies": ["EAbilityPoints"]}
    return out


HERO = {
    "id": 1, "name": "Testo", "player_selectable": True, "disabled": False,
    "items": {"weapon_primary": "w_test", "signature1": "a_nuke", "signature2": "a_dot",
              "signature3": "a_util", "signature4": "a_ult"},
    "level_info": _levels(),
    "standard_level_up_upgrades": {"MODIFIER_VALUE_BASE_BULLET_DAMAGE_FROM_LEVEL": 1.0,
                                   "MODIFIER_VALUE_TECH_POWER": 2.0,
                                   "MODIFIER_VALUE_BASE_HEALTH_FROM_LEVEL": 10.0},
    "starting_stats": {"max_health": {"value": 500}},
    "scaling_stats": {},
    "cost_bonuses": {"weapon": [{"gold_threshold": 800, "bonus": 10}, {"gold_threshold": 1600, "bonus": 20}],
                     "spirit": [{"gold_threshold": 800, "bonus": 5}],
                     "vitality": [{"gold_threshold": 800, "bonus": 10}]},
}


def P(v, **kw):
    d = {"value": v}
    d.update(kw)
    return d


SPIRIT = {"class_name": "scale_function_tech_damage", "specific_stat_scale_type": "ETechPower"}

ITEMS = [
    {"type": "weapon", "class_name": "w_test", "weapon_info": {
        "bullet_damage": 10, "bullets": 1, "cycle_time": 0.1, "burst_shot_count": 1,
        "intra_burst_cycle_time": 0, "clip_size": 10, "reload_duration": 1.0,
        "damage_falloff_start_range": 20 * U, "damage_falloff_end_range": 40 * U,
        "damage_falloff_end_scale": 0.1, "crit_bonus_start": 1.65}},
    {"type": "ability", "class_name": "a_nuke", "name": "Nuke", "ability_type": "signature",
     "properties": {"Damage": P(100, css_class="tech_damage", scale_function={**SPIRIT, "stat_scale": 1.0}),
                    "AbilityCooldown": P(10)},
     "upgrades": [{"property_upgrades": [{"name": "Damage", "bonus": "50"}]},
                  {"property_upgrades": [{"name": "Damage", "bonus": 0.5, "upgrade_type": "EAddToScale",
                                          "scale_stat_filter": "ETechPower"}]},
                  {"property_upgrades": [{"name": "AbilityCooldown", "bonus": "-5"}]}]},
    {"type": "ability", "class_name": "a_dot", "name": "Dot", "ability_type": "signature",
     "properties": {"DPS": P(20, css_class="tech_damage", scale_function={**SPIRIT, "stat_scale": 0.5}),
                    "AbilityDuration": P(4), "AbilityCooldown": P(20)},
     "upgrades": [{"property_upgrades": []}] * 3},
    {"type": "ability", "class_name": "a_util", "name": "Util", "ability_type": "signature",
     "properties": {"AbilityCooldown": P(15)}, "upgrades": [{"property_upgrades": []}] * 3},
    {"type": "ability", "class_name": "a_ult", "name": "Ult", "ability_type": "ultimate",
     "properties": {"Damage": P(300, css_class="tech_damage", scale_function={**SPIRIT, "stat_scale": 2.0}),
                    "AbilityCooldown": P(100)},
     "upgrades": [{"property_upgrades": []}] * 3},
    {"type": "upgrade", "shopable": True, "class_name": "upgrade_gun", "name": "Gun", "cost": 800,
     "item_tier": 1, "item_slot_type": "weapon",
     "properties": {"BaseAttackDamagePercent": P("20", provided_property_type="MODIFIER_VALUE_WEAPON_DAMAGE_INCREASE")}},
    {"type": "upgrade", "shopable": True, "class_name": "upgrade_big_gun", "name": "Big Gun", "cost": 1600,
     "item_tier": 2, "item_slot_type": "weapon", "component_items": ["upgrade_gun"],
     "properties": {"BaseAttackDamagePercent": P("40", provided_property_type="MODIFIER_VALUE_WEAPON_DAMAGE_INCREASE")}},
    {"type": "upgrade", "shopable": True, "class_name": "upgrade_spirit", "name": "Spirit", "cost": 800,
     "item_tier": 1, "item_slot_type": "spirit",
     "properties": {"TechPower": P("10", provided_property_type="MODIFIER_VALUE_TECH_POWER"),
                    "CooldownReduction": P("20", provided_property_type="MODIFIER_VALUE_COOLDOWN_REDUCTION_PERCENTAGE"),
                    "TechPowerReduction": P("-30", provided_property_type="MODIFIER_VALUE_TECH_POWER")}},
    {"type": "upgrade", "shopable": True, "class_name": "upgrade_cdr", "name": "Cdr", "cost": 800,
     "item_tier": 1, "item_slot_type": "spirit",
     "properties": {"CooldownReduction": P("25", provided_property_type="MODIFIER_VALUE_COOLDOWN_REDUCTION_PERCENTAGE"),
                    "SlowPercent": P("40", provided_property_type="MODIFIER_VALUE_FIRE_RATE",
                                     usage_flags=["ConditionallyApplied"])}},
    {"type": "upgrade", "shopable": True, "class_name": "upgrade_burst", "name": "Burst", "cost": 800,
     "item_tier": 1, "item_slot_type": "spirit",
     "properties": {"Damage": P("50", css_class="tech_damage"), "AbilityCooldown": P("10")}},
]


@pytest.fixture(scope="module")
def cat():
    return build_catalog([HERO], ITEMS, starting_souls=600)


# 10 souls per second from 600: net worth N is reached at t = (N - 600) / 10.
LINEAR = [(0, 600), (1000, 10_600)]
T0 = Target(distance_m=10)


def _ev(cat, level=1, items=(), tiers=None, target=T0, **kw):
    h = cat.hero("Testo")
    st = State(level=level, items=[cat.item(i) for i in items], tiers=dict(tiers or {}))
    return evaluate(h, st, target, **kw)


# ------------------------------------------------------------------ planted tests

def test_level_thresholds_add_starting_souls(cat):
    h = cat.hero("testo")
    assert h.level_souls[:4] == [600, 800, 1100, 1500]


def test_weapon_dps_hand_computed(cat):
    r = _ev(cat)
    # 10 dmg x 10 shots/s = 100 burst; sustained 10 x 10 / (10/10 + 1.0 reload) = 50
    assert r["weapon_burst_dps"] == pytest.approx(100)
    assert r["weapon_dps"] == pytest.approx(50)


def test_falloff_linear_between_ranges(cat):
    w = cat.hero("Testo").weapon
    assert falloff(w, 10) == 1.0
    assert falloff(w, 30) == pytest.approx(0.55)      # halfway from 1.0 to 0.1
    assert falloff(w, 99) == pytest.approx(0.1)


def test_crit_rate(cat):
    r = _ev(cat, target=Target(distance_m=10, headshot_rate=0.5))
    assert r["weapon_burst_dps"] == pytest.approx(100 * (1 + 0.5 * 0.65))


def test_boons_raise_bullet_and_spirit(cat):
    r = _ev(cat, level=5)                               # 4 boons
    assert r["bullet"] == pytest.approx(14)            # 10 + 4 x 1
    assert r["spirit"] == pytest.approx(8)             # 4 x 2
    assert r["health"] == pytest.approx(540)


def test_item_plus_investment(cat):
    r = _ev(cat, items=["Gun"])
    assert r["weapon_pct"] == pytest.approx(30)        # 20 item + 10 investment at 800
    assert r["bullet"] == pytest.approx(13)


def test_component_is_consumed_and_discounted(cat):
    res = simulate("Testo", items=["Gun", "Big Gun"], ability_order=[], souls_curve=LINEAR,
                   end_s=400, cat=cat, target=T0)
    buys = [e for e in res.events if e["kind"] == "item"]
    # Gun at 800 net worth (t=20); Big Gun costs 1600-800 more: 1600 net worth (t=100)
    assert [round(e["t"]) for e in buys] == [20, 100]
    last = res.at(400)
    assert last["weapon_pct"] == pytest.approx(60)     # 40 + 20 investment (1600 owned)
    assert "(from Gun)" in buys[1]["label"]


def test_scoped_and_conditional_props_do_not_leak(cat):
    r = _ev(cat, items=["Spirit", "Cdr"])
    # TechPowerReduction (-30) is an enemy debuff; the conditional "fire rate" is not global
    assert r["spirit"] == pytest.approx(10 + 5)       # item + spirit investment at 800
    assert r["shots_per_s"] == pytest.approx(10)


def test_cooldown_reduction_is_multiplicative(cat):
    r = _ev(cat, items=["Spirit", "Cdr"], tiers={1: 0})
    assert r["cooldown_mult"] == pytest.approx(0.8 * 0.75)
    a = r["abilities"][1]
    sp = 15                                           # 10 item + 5 investment
    assert a["cooldown"] == pytest.approx(6.0)
    assert a["dps"] == pytest.approx((100 + sp) / 6.0)


def test_ability_upgrade_tiers(cat):
    sp = 0
    h = cat.hero("Testo")
    p = upgraded_props(h.abilities[1], 3)
    assert p["Damage"].base == 150                    # T1 +50
    assert p["Damage"].scale == pytest.approx(1.5)    # T2 adds 0.5 to the spirit scale
    assert p["AbilityCooldown"].base == 5             # T3 -5s
    r = _ev(cat, level=5, tiers={1: 3})
    sp = 8
    assert r["abilities"][1]["per_cast"] == pytest.approx(150 + 1.5 * sp)
    assert r["abilities"][1]["dps"] == pytest.approx((150 + 1.5 * sp) / 5)


def test_per_second_damage_uses_duration(cat):
    r = _ev(cat, level=5, tiers={2: 0})
    assert r["abilities"][2]["per_cast"] == pytest.approx((20 + 0.5 * 8) * 4)
    assert r["abilities"][2]["dps"] == pytest.approx((20 + 0.5 * 8) * 4 / 20)


def test_spirit_resist_and_overrides(cat):
    r = _ev(cat, tiers={1: 0}, target=Target(distance_m=10, spirit_resist=0.25),
            overrides={1: {"hits": 2}})
    # per_cast is raw; the enemy's spirit resist applies to the DPS it adds up to
    assert r["abilities"][1]["per_cast"] == pytest.approx(100 * 2)
    assert r["ability_dps"] == pytest.approx(100 * 2 * 0.75 / 10)


def test_item_proc(cat):
    r = _ev(cat, items=["Burst"])
    assert r["item_dps"] == pytest.approx(50 / 10)


def test_ability_queue_waits_for_currency(cat):
    # Unlocks at levels 1,3,5,8 (t=0,50,140,320); AP at levels 2,4,6,7 (t=20,90,200,260)
    # then 9.. ; net worth = 600 + required_gold at 10 souls/s.
    res = simulate("Testo", ability_order=[1, 4, 1, 1, 2], souls_curve=LINEAR, end_s=2000,
                   cat=cat, target=T0)
    ab = [(round(e["t"]), e["label"]) for e in res.events if e["kind"] == "ability"]
    # Ult needs the 4th unlock (level 8, t=320) and the queue blocks behind it.
    # Nuke T1 (1 AP) and T2 (2 AP; 3 earned by t=200) are then already affordable,
    # and Dot takes the 3rd unlock, which was banked at t=140.
    assert ab == [(0, "unlock Nuke"), (320, "unlock Ult"), (320, "Nuke T1"),
                  (320, "Nuke T2"), (320, "unlock Dot")]
    assert not res.warnings


def test_ability_queue_drops_what_is_never_affordable(cat):
    # 15 AP exist in the planted table; Nuke T1-3 + Dot T1-3 costs 16.
    res = simulate("Testo", ability_order=[1, 2] + [1] * 3 + [2] * 3, souls_curve=LINEAR,
                   end_s=5000, cat=cat, target=T0)
    labels = [e["label"] for e in res.events if e["kind"] == "ability"]
    assert labels[-1] == "Dot T2"
    assert any("Dot" in w and "never affordable" in w for w in res.warnings)


def test_purchase_respects_requested_time_and_warns(cat):
    res = simulate("Testo", items=[("Gun", 100), ("Spirit", 10)], ability_order=[],
                   souls_curve=LINEAR, end_s=400, cat=cat, target=T0)
    buys = [round(e["t"]) for e in res.events if e["kind"] == "item"]
    # Gun asked at 100 (affordable at 20) -> 100. Spirit asked at 10, needs 1600 net
    # worth (t=100), and the order is sequential -> 100, with a warning.
    assert buys == [100, 100]
    assert any("Spirit" in w for w in res.warnings)


def test_spikes_marked(cat):
    res = simulate("Testo", items=["Gun"], ability_order=[], souls_curve=LINEAR,
                   end_s=200, cat=cat, target=T0, spike_pct=5)
    labels = [s["label"] for s in res.spikes]
    assert any(l.startswith("Gun") and "weapon investment 0->10" in l for l in labels)
    gun = next(s for s in res.spikes if s["label"].startswith("Gun"))
    # before: boons 0 -> sustained 50 ; after: x1.3
    assert gun["dps_after"] / gun["dps_before"] == pytest.approx(1.3)


def test_unmodelled_reported(cat):
    res = simulate("Testo", ability_order=[3], souls_curve=LINEAR, end_s=60, cat=cat, target=T0)
    assert any("Util" in n for n in res.unmodelled)


def test_souls_curve_inverse():
    c = SoulsCurve([(0, 600), (100, 1600), (200, 1600), (300, 3600)])
    assert c.time_for(1100) == pytest.approx(50)
    assert c.time_for(2600) == pytest.approx(250)
    assert c.souls(250) == pytest.approx(2600)
    assert c.time_for(5600) == pytest.approx(400)     # extrapolates last slope


def test_investment_bonus_is_cumulative_table():
    table = [(800, 9), (1600, 12), (4800, 46)]
    assert investment_bonus(table, 799) == 0
    assert investment_bonus(table, 3200) == 12
    assert investment_bonus(table, 4800) == 46


# ------------------------------------------------------------------ live data vs wiki

@pytest.fixture(scope="module")
def live():
    try:
        from theorycraft.data import catalog, fetch_raw
        heroes, items, _ = fetch_raw()
        return catalog(), heroes, items
    except Exception as e:  # offline
        pytest.skip(f"live catalog unavailable: {e}")


def test_live_soul_table_matches_wiki(live):
    cat, _, _ = live
    wiki = [600, 800, 1100, 1500, 2000, 2600, 3200, 3800, 4500, 5200, 5900, 6600, 7400, 8200,
            9000, 9800, 10700, 11600, 12500, 13400, 14400, 15500, 16700, 18000, 19500, 21200,
            23100, 25200, 27500, 30000, 32700, 35600, 38700, 42000, 45500, 49200]
    for h in cat.heroes.values():
        assert h.level_souls == wiki, h.name
        assert [i + 1 for i, r in enumerate(h.level_rewards) if r == "unlock"] == [1, 3, 5, 8]
        assert sum(r == "ap" for r in h.level_rewards) == 32


@pytest.mark.parametrize("hero,boons,bullet_per_shot", [
    ("Haze", 10, 6.69), ("Haze", 20, 8.12), ("Haze", 35, 10.27),
    ("Abrams", 10, 43.02), ("Abrams", 35, 65.52),         # 9 pellets
    ("Grey Talon", 20, 40.51), ("Vindicta", 35, 29.66),
])
def test_live_boon_bullet_damage_matches_wiki(live, hero, boons, bullet_per_shot):
    cat, _, _ = live
    h = cat.hero(hero)
    r = evaluate(h, State(level=boons + 1), Target(distance_m=1))
    # scaling_stats (e.g. Vindicta EBulletDamage x SP) would add on top; the wiki
    # table is base bullet damage without spirit, so compare the boon part only.
    base = (h.weapon.bullet_damage + boons * h.per_boon["bullet"]) * h.weapon.pellets
    assert base == pytest.approx(bullet_per_shot, abs=0.011)
    if hero in ("Haze", "Abrams"):
        assert r["bullet"] * h.weapon.pellets == pytest.approx(bullet_per_shot, abs=0.011)


@pytest.mark.parametrize("hero,boons,spirit,health", [
    ("Haze", 20, 10.0, 1390), ("Kelvin", 35, 45.5, None), ("Haze", 35, 17.5, 1885),
])
def test_live_boon_spirit_and_health_match_wiki(live, hero, boons, spirit, health):
    cat, _, _ = live
    r = evaluate(cat.hero(hero), State(level=boons + 1), Target())
    assert r["spirit"] == pytest.approx(spirit)
    if health:
        assert r["health"] == pytest.approx(health)


WIKI_BASE_DPS = {  # deadlock.wiki Hero_Comparison_Table, fetched 6 Nov 2026: (DPS, Sustained DPS)
    'Abrams': (54.0, 32.07),
    'Apollo': (48.69, 33.84),
    'Baba': (28.39, 18.46),
    'Bebop': (59.29, 41.64),
    'Billy': (74.12, 34.68),
    'Calico': (78.86, 38.81),
    'Celeste': (31.03, 21.69),
    'Deadman Danny': (60.0, 31.34),
    'Drifter': (45.58, 31.18),
    'Dynamo': (48.0, 33.16),
    'Graves': (35.29, 20.93),
    'Grey Talon': (39.18, 31.85),
    'Haze': (50.1, 26.43),
    'Holliday': (41.69, 26.35),
    'Infernus': (52.38, 29.2),
    'Ivy': (60.54, 30.16),
    'Kelvin': (70.86, 41.6),
    'Lady Geist': (43.81, 27.25),
    'Lash': (49.33, 33.5),
    'McGinnis': (72.38, 45.42),
    'Mina': (29.01, 18.57),
    'Mirage': (40.27, 27.93),
    'Mo & Krill': (59.68, 34.18),
    'Nurse Harrow': (38.01, 26.13),
    'Paige': (64.0, 47.16),
    'Paradox': (51.4, 34.53),
    'Pocket': (57.07, 38.34),
    'Rat King': (58.0, 37.7),
    'Rem': (61.54, 38.66),
    'Seven': (62.97, 42.77),
    'Shiv': (52.25, 34.65),
    'Silver': (42.0, 29.23),
    'Sinclair': (44.9, 31.5),
    'Solomon': (50.0, 28.57),
    'The Doorman': (38.1, 25.81),
    'Venator': (63.49, 37.94),
    'Victor': (70.18, 44.28),
    'Vindicta': (53.38, 32.08),
    'Violet': (46.16, 36.15),
    'Viscous': (49.24, 30.87),
    'Vyper': (94.0, 48.15),
    'Warden': (66.06, 39.96),
    'Wraith': (59.68, 37.92),
    'Yamato': (63.22, 42.57),
}


def test_live_weapon_dps_matches_wiki_for_every_hero(live):
    """Burst and sustained DPS at 0 boons, no items, point blank -- every hero."""
    cat, _, _ = live
    assert len(WIKI_BASE_DPS) >= 44
    for name, (dps, sustained) in WIKI_BASE_DPS.items():
        r = evaluate(cat.hero(name), State(), Target(distance_m=0), objectives=())
        assert r["weapon_burst_dps"] == pytest.approx(dps, rel=0.02, abs=0.05), name
        assert r["weapon_dps"] == pytest.approx(sustained, rel=0.02, abs=0.05), name


# Where the API's derived shots/s and the wiki disagree we follow the wiki:
# spin-up guns taken fully spun, and no intra-burst gap on 1-bullet "bursts".
API_DIVERGES = {"McGinnis", "Victor", "Paige", "Rat King", "Solomon", "The Doorman"}


def test_live_shots_per_second_matches_api_elsewhere(live):
    cat, heroes, items = live
    by = {i["class_name"]: i for i in items}
    checked = 0
    for raw in heroes:
        h = cat.heroes.get(raw["name"].lower())
        w = (by.get(raw["items"].get("weapon_primary")) or {}).get("weapon_info") or {}
        if not h or "shots_per_second" not in w or h.name in API_DIVERGES:
            continue
        assert h.weapon.shots_per_second == pytest.approx(w["shots_per_second"], rel=1e-6), h.name
        checked += 1
    assert checked >= 35


def test_live_falloff_ranges_match_wiki(live):
    cat, _, _ = live
    for hero, lo, hi in [("Abrams", 17, 40), ("Haze", 20, 46), ("Graves", 7.6, 17)]:
        w = cat.hero(hero).weapon
        assert (round(w.falloff_start_m, 1), round(w.falloff_end_m, 1)) == (lo, hi)


def test_live_weapon_investment_matches_wiki(live):
    cat, _, _ = live
    wiki = [9, 12, 15, 18, 46, 54, 62, 74, 86, 100, 115]
    assert [b for _, b in cat.hero("Haze").investment["weapon"]] == wiki


def test_live_sleep_dagger_matches_wiki(live):
    cat, _, _ = live
    p = cat.hero("Haze").abilities[1].props["Damage"]
    assert (p.base, p.scale) == (65, 2.2)             # wiki: 65, x2.2 spirit


def test_live_end_to_end_haze(live):
    cat, _, _ = live
    res = simulate("Haze", items=["Extended Magazine", "Headshot Booster", "Rapid Rounds",
                                  "Swift Striker"],
                   ability_order=[1, 3, 2, 4], end_s=1200, cat=cat)
    assert res.timeline[0]["t"] == 0 and res.timeline[-1]["t"] == 1200
    dps = [r["total_dps"] for r in res.timeline]
    assert all(b >= a - 1e-9 for a, b in zip(dps, dps[1:]))   # nothing here should lower DPS
    assert any("weapon investment" in s["label"] for s in res.spikes)


def test_multiply_upgrades():
    # EMultiplyBase carries a percent ('33.33'), EMultiplyScale a factor (1.3333):
    # Baba's Brew T3 ships both on the same property, which is where this reading
    # comes from. It is an inference, not a documented contract.
    from theorycraft.data import parse_ability
    raw = {"class_name": "a", "name": "A", "properties": {
        "DPS": {"value": 12, "scale_function": {**SPIRIT, "stat_scale": 0.3}}},
        "upgrades": [{"property_upgrades": [
            {"name": "DPS", "bonus": "50", "upgrade_type": "EMultiplyBase"},
            {"name": "DPS", "bonus": 1.2, "upgrade_type": "EMultiplyScale",
             "scale_stat_filter": "ETechPower"}]}]}
    p = upgraded_props(parse_ability(raw, 1), 1)["DPS"]
    assert p.base == pytest.approx(18)
    assert p.scale == pytest.approx(0.36)


# ------------------------------------------------------------------ optimizer (planted)

from theorycraft.optimize import Weights, _spend, lane_mix, pareto, recommend, Plan  # noqa: E402

LEECH = {"type": "upgrade", "shopable": True, "class_name": "upgrade_leechy", "name": "Leechy",
         "cost": 800, "item_tier": 1, "item_slot_type": "vitality",
         "properties": {"BulletLifestealPercent": P("40", provided_property_type="MODIFIER_VALUE_BULLET_LIFESTEAL")}}
ARMOR = {"type": "upgrade", "shopable": True, "class_name": "upgrade_armor", "name": "Armor",
         "cost": 800, "item_tier": 1, "item_slot_type": "vitality",
         "properties": {"BulletResist": P("30", provided_property_type="MODIFIER_VALUE_BULLET_ARMOR_DAMAGE_RESIST")}}


@pytest.fixture(scope="module")
def ocat():
    return build_catalog([HERO], ITEMS + [LEECH, ARMOR], starting_souls=600)


SLOW = [(0, 600), (1800, 6_600)]    # 5,600 souls over 30 min: a handful of purchases


def test_spend_credits_components(ocat):
    assert _spend(["Gun", "Big Gun"], ocat) == 1600
    assert _spend(["Spirit", "Gun"], ocat) == 1600


def test_floor_forces_sustain_only_when_needed(ocat):
    kw = dict(ability_order=[1], souls_curve=SLOW, cat=ocat, n_items=4, beam=4, candidates=8,
              target=Target(distance_m=10))
    free = recommend("Testo", mode="floor", sustain_floor=0, **kw)[0]
    assert "Leechy" not in free.items and "Armor" not in free.items
    needy = recommend("Testo", mode="floor", sustain_floor=free.parts["sustain"] * 1.3, **kw)[0]
    assert needy.parts["sustain"] >= free.parts["sustain"] * 1.3
    assert {"Leechy", "Armor"} & set(needy.items)


def test_frontier_is_nondominated_and_spans(ocat):
    plans = recommend("Testo", [1], mode="frontier", souls_curve=SLOW, cat=ocat, n_items=4,
                      beam=4, candidates=8, target=Target(distance_m=10),
                      weights=Weights(objective=0))
    dmg = [p.parts["damage"] + p.parts["objective"] for p in plans]
    sus = [p.parts["sustain"] for p in plans]
    assert dmg == sorted(dmg, reverse=True)
    assert sus == sorted(sus)            # more damage, less sustain, along the frontier
    assert len(plans) >= 2


def test_pareto_drops_dominated():
    mk = lambda d, s: Plan(items=[str(d), str(s)], score=0, parts={"damage": d, "objective": 0, "sustain": s},
                           lane_sustain=s)
    out = pareto([mk(10, 1), mk(5, 5), mk(4, 4), mk(10, 1)])
    assert [(p.parts["damage"], p.parts["sustain"]) for p in out] == [(10, 1), (5, 5)]


def test_locked_lane_items_come_first(ocat):
    p = recommend("Testo", [1], mode="weighted", lane="locked", lane_items=["Armor"],
                  souls_curve=SLOW, cat=ocat, n_items=3, beam=3, candidates=6,
                  target=Target(distance_m=10))[0]
    assert p.items[0] == "Armor"


def test_lane_mix_reads_the_enemy(ocat):
    # Testo at 6 boons: bullet 10+6 = 16 x 10 shots/s x 3 s = 480 of gunfire;
    # spirit 6 x 2 = 12: Nuke 100+12 = 112, Dot (20 + 0.5 x 12) x 4 s = 104.
    assert lane_mix(ocat, ["Testo"]) == pytest.approx(480 / (480 + 112 + 104))
    assert lane_mix(ocat, []) == 0.5


# ------------------------------------------------------------------ combat (hand-computed)

from theorycraft.combat import OBJECTIVES, final_resist, rift_times, stack  # noqa: E402


def test_resist_stacking_and_shred_subtract():
    # wiki example: 40% and 20% -> 52%; reductions 25% and 20% -> 40%; 52 - 40 = 12%
    assert stack([0.4, 0.2]) == pytest.approx(0.52)
    assert final_resist([0.4, 0.2], [0.25, 0.2]) == pytest.approx(0.12)
    assert final_resist([0.1], [0.3]) == pytest.approx(-0.2)     # can amplify


def test_guardian_clock():
    g = OBJECTIVES["Guardian"]
    # t=0: (1-0.5) x 1.25 x 0.9 ; t>=12:00: (1+0.5) x 1.25 x 0.9 ; 6:00: 1 x 1.25 x 0.9
    assert g.multipliers(0)[0] == pytest.approx(0.5 * 1.25 * 0.9)
    assert g.multipliers(360)[0] == pytest.approx(1.25 * 0.9)
    assert g.multipliers(2000)[0] == pytest.approx(1.5 * 1.25 * 0.9)
    # wiki formula for spirit: SD x [(1 - 0.5) + t/720] -- the -25% player vuln is bullet-only
    assert g.multipliers(360)[1] == pytest.approx(1.0)
    assert g.multipliers(720)[1] == pytest.approx(1.5)


def test_walker_clock_and_crowd():
    w = OBJECTIVES["Walker"]
    # wiki example: minute 18, 3 enemies -> 1.65 x 0.65 x 0.8 = 0.858 ("86%")
    assert w.multipliers(1080, attackers=3)[0] == pytest.approx(1.65 * 0.65 * 0.8)
    assert w.multipliers(540)[0] == pytest.approx(1.0 * 0.65)       # resist 0 at 9:00


def test_midboss_takes_half_shred_and_only_when_enabled():
    m = OBJECTIVES["Mid-Boss"]
    assert m.multipliers(0, bullet_shred=0.2)[0] == pytest.approx(0.85)
    assert m.multipliers(0, bullet_shred=0.2, shred_applies=True)[0] == pytest.approx(0.95)


def test_rift_schedule():
    assert rift_times(1800) == [660, 1080, 1500]


# ------------------------------------------------------------------ item effects (planted)

FX_ITEMS = [
    {"type": "upgrade", "shopable": True, "class_name": "upgrade_fx_shred", "name": "Shredder",
     "cost": 800, "item_tier": 1, "item_slot_type": "spirit",
     "properties": {"BulletArmorReduction": P("-10"), "MagicResistReduction": P("-20")}},
    {"type": "upgrade", "shopable": True, "class_name": "upgrade_fx_proc", "name": "Zapper",
     "cost": 800, "item_tier": 1, "item_slot_type": "spirit",
     "properties": {"DamagePerChain": P("30"), "ProcChance": P("50"), "ProcCooldown": P("1"),
                    "BonusFireRate": P("50", provided_property_type="MODIFIER_VALUE_FIRE_RATE")}},
    {"type": "upgrade", "shopable": True, "class_name": "upgrade_fx_stack", "name": "Stacker",
     "cost": 800, "item_tier": 1, "item_slot_type": "weapon",
     "properties": {"PerStack": P("5"), "MaxStacks": P("10")}},
    {"type": "upgrade", "shopable": True, "class_name": "upgrade_fx_bleed", "name": "Bleeder",
     "cost": 800, "item_tier": 1, "item_slot_type": "weapon",
     "properties": {"Pct": P("2"), "Hs": {"value": 10, "scale_function": {
         "specific_stat_scale_type": "ELevelUpBoons", "stat_scale": 2.0}}}},
    {"type": "upgrade", "shopable": True, "class_name": "upgrade_fx_ls", "name": "Leech2",
     "cost": 800, "item_tier": 1, "item_slot_type": "vitality",
     "properties": {"BulletLifestealPercent": P("50", provided_property_type="MODIFIER_VALUE_BULLET_LIFESTEAL")}},
    {"type": "upgrade", "shopable": True, "class_name": "upgrade_fx_ls2", "name": "Leech3",
     "cost": 800, "item_tier": 1, "item_slot_type": "vitality",
     "properties": {"BulletLifestealPercent": P("50", provided_property_type="MODIFIER_VALUE_BULLET_LIFESTEAL")}},
    {"type": "upgrade", "shopable": True, "class_name": "upgrade_fx_lucky", "name": "Lucky",
     "cost": 800, "item_tier": 1, "item_slot_type": "weapon",
     "properties": {"ProcChance": P("25"), "CritDamagePercent": P("100")}},
]
FX = {
    "upgrade_fx_shred": [{"type": "bullet_shred", "value_prop": "BulletArmorReduction", "uptime": 1},
                         {"type": "spirit_shred", "value_prop": "MagicResistReduction", "uptime": 0.5}],
    "upgrade_fx_proc": [{"type": "on_hit_spirit", "damage_prop": "DamagePerChain", "per": "shot",
                         "proc_chance_prop": "ProcChance", "cooldown_prop": "ProcCooldown", "uptime": 1}],
    "upgrade_fx_stack": [{"type": "conditional_stat", "stat": "MODIFIER_VALUE_WEAPON_DAMAGE_INCREASE",
                          "value_prop": "PerStack", "stacks_prop": "MaxStacks", "uptime": 0.5}],
    "upgrade_fx_bleed": [{"type": "max_hp_dot", "pct_prop": "Pct", "unit": "per_second", "uptime": 1,
                          "ignores_resist": True, "heals_self": True},
                         {"type": "headshot_bonus", "damage_prop": "Hs", "damage_type": "spirit", "uptime": 1}],
    "upgrade_fx_lucky": [{"type": "weapon_multiplier", "chance_prop": "ProcChance",
                          "bonus_prop": "CritDamagePercent", "uptime": 1}],
}


@pytest.fixture(scope="module")
def fcat():
    return build_catalog([HERO], ITEMS + FX_ITEMS, starting_souls=600, effects=FX,
                         excluded={"upgrade_fx_proc": ["BonusFireRate"]})


def _fev(fcat, items, level=1, target=None):
    h = fcat.hero("Testo")
    return evaluate(h, State(level=level, items=[fcat.item(i) for i in items]),
                    target or Target(distance_m=10), objectives=())


def test_shred_against_enemy_resist(fcat):
    r = _fev(fcat, ["Shredder"], target=Target(distance_m=10, bullet_resist=0.3, spirit_resist=0.3))
    # bullet: 30% - 10% = 20% -> x0.8 ; gun raw 50 sustained -> 40
    assert r["weapon_dps"] == pytest.approx(50 * 0.8)
    assert r["bullet_shred"] == pytest.approx(0.10) and r["spirit_shred"] == pytest.approx(0.10)


def test_on_hit_proc_capped_and_excluded_prop(fcat):
    r = _fev(fcat, ["Zapper"])
    # BonusFireRate is excluded (buff-only), so shots/s stays 10.
    assert r["shots_per_s"] == pytest.approx(10)
    # sustained shot rate 10 clip / 2 s = 5/s, x50% = 2.5/s, capped at 1/s by the
    # cooldown -> 30 dps (DamagePerChain has no spirit scaling in this planted item)
    assert r["item_dps"] == pytest.approx(30)


def test_stacks_on_conditional_stat(fcat):
    r = _fev(fcat, ["Stacker"])
    # 5% x 10 stacks x 0.5 uptime = +25%, plus weapon investment +10 at 800
    assert r["weapon_pct"] == pytest.approx(35)


def test_true_damage_heal_and_boon_scaling(fcat):
    tgt = Target(distance_m=10, headshot_rate=0.5, spirit_resist=0.5, max_health=1000)
    r = _fev(fcat, ["Bleeder"], level=5, target=tgt)
    # 2%/s of 1000 = 20 true dps, unresisted, and healed back
    # headshot bonus: 10 + 2 x 4 boons = 18 spirit, 5 shots/s x 0.5 = 2.5/s -> 45 raw, x0.5 resist
    assert r["item_dps"] == pytest.approx(20 + 45 * 0.5)
    assert r["sustain_hps"] == pytest.approx(20)        # no regen, no lifesteal on the planted hero


def test_lifesteal_stacks_multiplicatively(fcat):
    r = _fev(fcat, ["Leech2", "Leech3"])
    # 1 - 0.5 x 0.5 = 75%, not 100%; weapon 50 dps
    assert r["sustain_hps"] == pytest.approx(0.75 * 50)


def test_weapon_multiplier(fcat):
    r = _fev(fcat, ["Lucky"])
    # x(1 + 0.25 x 1.0) on weapon, plus weapon investment +10% at 800 -> 11 x 10 / 2 x 1.25
    assert r["weapon_dps"] == pytest.approx(11 * 10 / 2 * 1.25)


def test_real_effect_spec_is_well_formed():
    from theorycraft.effects import TYPES, load_effects
    import json, os
    eff = load_effects()
    raw = {i["class_name"]: i for i in json.load(open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "items.json")))}
    assert len(eff) >= 50
    for cn, effects in eff.items():
        assert cn in raw, cn
        for e in effects:
            assert e["type"] in TYPES, (cn, e["type"])
            for k, v in e.items():
                if k.endswith("_prop"):
                    assert v in raw[cn]["properties"], (cn, k, v)


def test_pct_base_bullet_and_clip_cap():
    item = {"type": "upgrade", "shopable": True, "class_name": "upgrade_fx_mm", "name": "Magnum",
            "cost": 800, "item_tier": 1, "item_slot_type": "spirit",
            "properties": {"BonusPct": P("50"), "Charge": P("10")}}
    fx = {"upgrade_fx_mm": [{"type": "on_hit_spirit", "damage_prop": "BonusPct", "per": "bullet",
                             "damage_unit": "pct_base_bullet", "cap": "clip_per_window",
                             "window_prop": "Charge", "uptime": 1}]}
    c = build_catalog([HERO], ITEMS + [item], starting_souls=600, effects=fx)
    r = evaluate(c.hero("Testo"), State(items=[c.item("Magnum")]), Target(distance_m=10), objectives=())
    # 50% of base bullet 10 = 5 per bullet; hits 5/s capped at one 10-round clip per 10 s = 1/s
    # (Magnum is a spirit item, so no weapon investment changes the base bullet)
    assert r["item_dps"] == pytest.approx(5 * 1.0)


def test_shrine_base_guardian_patron():
    sh, bg, pa = OBJECTIVES["Shrine"], OBJECTIVES["Base Guardian"], OBJECTIVES["Patron"]
    # Shrine: (1 - 0.2) x (1 - (0.6 - 0.2 x n)), 0 floor at 3+ attackers
    assert sh.multipliers(0, attackers=1)[0] == pytest.approx(0.8 * 0.6)
    assert sh.multipliers(0, attackers=3)[0] == pytest.approx(0.8)
    # Base Guardian: x1.25 vs players, 20% bullet, 40% - 20%/hero crowd
    assert bg.multipliers(0, attackers=1)[0] == pytest.approx(1.25 * 0.8 * 0.8)
    assert pa.multipliers(0) == (1.0, 1.0)


def test_accuracy_scales_gun_and_on_hit(fcat):
    full = _fev(fcat, ["Zapper"])
    half = _fev(fcat, ["Zapper"], target=Target(distance_m=10, accuracy=0.4))
    assert half["weapon_dps"] == pytest.approx(full["weapon_dps"] * 0.4)
    # Zapper: 5 shots/s x 0.4 accuracy x 50% = 1/s, exactly the 1 s cooldown cap -> 30
    assert half["item_dps"] == pytest.approx(30)
    quarter = _fev(fcat, ["Zapper"], target=Target(distance_m=10, accuracy=0.2))
    assert quarter["item_dps"] == pytest.approx(15)       # now under the cap


def test_uptime_override(fcat):
    base = _fev(fcat, ["Stacker"])
    over = _fev(fcat, ["Stacker"], target=Target(distance_m=10, uptimes=(("upgrade_fx_stack#0", 1.0),)))
    assert base["weapon_pct"] == pytest.approx(35)        # 5 x 10 x 0.5 + 10
    assert over["weapon_pct"] == pytest.approx(60)        # 5 x 10 x 1.0 + 10


def test_guardian_takes_headshots_walker_does_not(cat):
    h = cat.hero("Testo")
    tg = Target(distance_m=10, headshot_rate=1.0)
    r = evaluate(h, State(), tg, t=720, objectives=("Guardian", "Walker"))
    # raw gun 50 sustained; all headshots x1.65 on the Guardian only
    assert r["obj_Guardian"] == pytest.approx(50 * 1.65 * 1.5 * 1.25 * 0.9)
    assert r["obj_Walker"] == pytest.approx(50 * (1 - (0.65 - 1.3 * 720 / 1080)) * 0.65)


def test_ability_objective_reduction(cat):
    from theorycraft.combat import OBJECTIVE_ABILITY_MULT
    h = cat.hero("Testo")
    tg = Target(distance_m=10, abilities_on_objectives=True)
    r = evaluate(h, State(tiers={1: 0}), tg, t=0, objectives=("Patron",))
    assert "Nuke" not in OBJECTIVE_ABILITY_MULT
    assert r["obj_Patron"] == pytest.approx(50 + 100 / 10)   # gun + full-damage Nuke


def test_objective_shred_comes_off_the_stacked_bullet_pool():
    g = OBJECTIVES["Guardian"]
    # pool: 10% bullet and -25% vs players stack to 1 - 0.9 x 1.25 = -12.5%.
    # 20% shred -> final -32.5%; time resist at 12:00 is -50% (separate stat).
    assert g.multipliers(720, bullet_shred=0.2, shred_applies=True)[0] == pytest.approx(1.5 * 1.325)
    # no shred: identical to the wiki's own formula BD x 1.5 x 1.25 x 0.9
    assert g.multipliers(720)[0] == pytest.approx(1.5 * 1.25 * 0.9)


def test_weapon_damage_abilities_use_bullet_resist():
    from theorycraft.data import parse_ability
    hero = dict(HERO, items={**HERO["items"], "signature1": "a_crack"})
    crack = {"type": "ability", "class_name": "a_crack", "name": "Crackshot", "ability_type": "signature",
             "properties": {"Damage": P(100, css_class="tech_damage",
                                        scale_function={**SPIRIT, "stat_scale": 1.0}),
                            "AbilityCooldown": P(10)}, "upgrades": []}
    c = build_catalog([hero], ITEMS + [crack], starting_souls=600)
    r = evaluate(c.hero("Testo"), State(tiers={1: 0}),
                 Target(distance_m=10, bullet_resist=0.5, spirit_resist=0.0), objectives=())
    assert r["ability_dps"] == pytest.approx(100 / 10 * 0.5)   # bullet resist, not spirit


# ------------------------------------------------------------------ scaling and weapon fixes

def test_tech_damage_class_without_type_scales_with_spirit():
    from theorycraft.data import parse_prop
    p = parse_prop("Damage", {"value": 85, "scale_function": {
        "class_name": "scale_function_tech_damage", "stat_scale": 1.3}})
    assert p.at(10) == pytest.approx(85 + 13)
    q = parse_prop("Damage", {"value": 60, "scale_function": {
        "class_name": "scale_function_tech_damage", "specific_stat_scale_type": "EStatsCount",
        "stat_scale": 1.0}})
    assert q.at(10) == pytest.approx(70)


def test_weapon_scaled_ability_uses_bonus_weapon_damage():
    from theorycraft.data import parse_prop
    p = parse_prop("Damage", {"value": 120, "scale_function": {
        "class_name": "scale_function_ability_weapon_damage",
        "specific_stat_scale_type": "EBaseWeaponDamageIncrease", "stat_scale": 1.5}})
    # ASSUMED unit: per percentage point of bonus weapon damage
    assert p.at(spirit=50, boons=10, weapon_pct=40) == pytest.approx(120 + 1.5 * 40)


def test_live_weapon_fixes(live):
    cat, _, _ = live
    assert cat.hero("Baba").weapon.damage_pellets == 1            # pellets share damage
    assert cat.hero("Abrams").weapon.damage_pellets == 9
    ab = cat.hero("Abrams").weapon
    assert ab.reload_time(9) == pytest.approx(0.705 + 9 * 0.3525)  # shell by shell
    assert cat.hero("McGinnis").weapon.shots_per_second == pytest.approx(1 / 0.084)


def test_live_ability_scaling_types(live):
    cat, _, _ = live
    sigil = cat.hero("Apollo").abilities[1].props["Damage"]
    assert (sigil.scale_stat, sigil.scale) == ("ETechPower", 1.3)    # wiki x1.3 spirit
    ira = cat.hero("Venator").abilities[4].props["Damage"]
    assert (ira.scale_stat, ira.scale) == ("EBaseWeaponDamageIncrease", 1.5)
    blessed = cat.hero("Venator").abilities[4].props["BonusDamage"]
    assert (blessed.scale_stat, blessed.scale) == ("ELevelUpBoons", 3.6)  # wiki: boon scaling 3.6


def test_live_kinetic_carbine_matches_wiki(live):
    cat, _, _ = live
    h = cat.hero("Paradox")
    r = evaluate(h, State(tiers={3: 0}), Target(distance_m=0), objectives=())
    carbine = next(a for a in r["abilities"].values() if a["name"] == "Kinetic Carbine")
    assert carbine["parts"]["spirit"] == pytest.approx(76.5)       # wiki: 6.8 x 5 x 2.25
    r3 = evaluate(h, State(tiers={3: 3}), Target(distance_m=0), objectives=())
    c3 = next(a for a in r3["abilities"].values() if a["name"] == "Kinetic Carbine")
    assert c3["parts"]["spirit"] == pytest.approx(6.8 * 5 * (1 + 180 / 100))   # T3 +55 scaling


def test_live_ira_domini_stakes(live):
    cat, _, _ = live
    h = cat.hero("Venator")
    r = evaluate(h, State(level=11, tiers={4: 0}), Target(distance_m=10), objectives=())
    ira = next(a for a in r["abilities"].values() if a["name"] == "Ira Domini")
    # no items: weapon% 0 -> 3 stakes x 120 weapon; blessed 95 + 3.6 x 10 boons pure, once
    assert ira["parts"]["weapon"] == pytest.approx(360)
    assert ira["parts"]["pure"] == pytest.approx(95 + 36)
    r3 = evaluate(h, State(level=11, tiers={4: 3}), Target(distance_m=10), objectives=())
    ira3 = next(a for a in r3["abilities"].values() if a["name"] == "Ira Domini")
    assert ira3["parts"]["pure"] == pytest.approx(3 * (95 + 65 + 36))   # T2 +65, T3 all blessed


def test_fixation_ramp_hand_computed():
    from theorycraft.data import Prop
    from theorycraft.specials import fixation
    props = {"MaxStacks": Prop("MaxStacks", 40), "DamageBonusFixedPerStack":
             Prop("DamageBonusFixedPerStack", 0.2, 0.0, "EBaseWeaponDamageIncrease")}
    # 8 hits/s, no headshots: cap in 5 s; 10 s fight -> avg 40 x (1 - 5/20) = 30 stacks
    flat, proc, avg = fixation(props, {"hit_rate": 8.0}, Target(), 0.0, 0.0)
    assert avg == pytest.approx(30) and flat == pytest.approx(6.0) and proc == 0
    # headshots triple the stack rate: 8 x (1 + 2 x 0.5) = 16/s -> cap in 2.5 s
    flat2, _, avg2 = fixation(props, {"hit_rate": 8.0}, Target(headshot_rate=0.5), 0.0, 0.0)
    assert avg2 == pytest.approx(40 * (1 - 2.5 / 20))


def test_live_fixation_raises_haze_gun(live):
    cat, _, _ = live
    h = cat.hero("Haze")
    base = evaluate(h, State(level=11), Target(distance_m=10), objectives=())
    fix = evaluate(h, State(level=11, tiers={3: 0}), Target(distance_m=10), objectives=())
    assert fix["weapon_dps"] > base["weapon_dps"] * 1.3      # +0.2 per stack on a ~6.7 bullet
    assert fix["fixation_stacks"] > 0
