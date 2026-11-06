"""
theorycraft.specials -- abilities whose damage shape the generic reader cannot
infer from property names. Each one cites the wiki text it implements.

Damage is returned split by type, per cast:
    {"weapon": x, "spirit": y, "pure": z}
weapon takes bullet resist and bullet shred, spirit takes spirit resist and
spirit shred, pure ignores resists (wiki Damage_Resistance / Pure_Damage).
"""

FIGHT_S = 10.0     # sustained-fight length used to average stack ramps (a judgement call)


def fixation(props, wd, target, weapon_pct, spirit):
    """
    Haze, Fixation (wiki Haze): "Gain one stack per bullet hit, three if the hit
    is a headshot". +DamageBonusFixedPerStack weapon damage per stack, up to
    MaxStacks, on the stacked target. T1: ProcDamage spirit damage every
    ProcDamageStackCount stacks. Flat weapon damage is added after the weapon
    damage % multiplier and is affected by falloff, resist and crit (wiki
    Weapon_Damage). 20% damage to objectives (BossDamageScale).

    Returns (flat bullet damage per bullet, spirit proc DPS, average stacks).
    """
    cap = props["MaxStacks"].base if "MaxStacks" in props else 40
    per_stack = props["DamageBonusFixedPerStack"].at(spirit, 0, weapon_pct) \
        if "DamageBonusFixedPerStack" in props else 0.0
    hs = target.headshot_rate
    stack_rate = wd["hit_rate"] * (1 + 2 * hs)          # stacks per second while shooting
    if stack_rate <= 0:
        return 0.0, 0.0, 0.0
    t_cap = cap / stack_rate
    # average stacks over a FIGHT_S fight: linear ramp to the cap, then held
    if t_cap >= FIGHT_S:
        avg = stack_rate * FIGHT_S / 2
    else:
        avg = cap * (1 - t_cap / (2 * FIGHT_S))
    proc = 0.0
    if "ProcDamage" in props and props["ProcDamage"].base > 0:
        every = props["ProcDamageStackCount"].base if "ProcDamageStackCount" in props else 20
        if every > 0:
            proc = props["ProcDamage"].at(spirit) * stack_rate / every
    return per_stack * avg, proc, avg


def kinetic_carbine(props, wd, target, hero):
    """
    Paradox, Kinetic Carbine (wiki Paradox + Weapon_Damage): spirit damage that
    scales with weapon damage "of a full burst shot", from BASE weapon damage
    (so boons count). The wiki shows x2.25 / x1.1 for coefficients 125 / 10,
    i.e. (coeff/100)+1, and 76.5 = 34 x 2.25 at base (6.8 x 5 rounds). The
    prop's base value (5.0) is the burst. Bullet shred does not apply; spirit
    shred does. Full charge assumed. +HeadshotBonus% on a headshot.
    """
    p = props.get("MaxBonusBulletDamage")
    if p is None:
        return {}
    burst = p.base                                    # 5 rounds
    dmg = wd["bullet"] * burst * (1 + p.scale / 100)
    hs_bonus = props["HeadshotBonus"].base / 100 if "HeadshotBonus" in props else 0.0
    dmg *= 1 + target.headshot_rate * hs_bonus
    return {"spirit": dmg}


def ira_domini(props, tier, spirit, boons, weapon_pct):
    """
    Venator, Ira Domini (wiki Venator): "Load your crossbow with 3 stakes,
    dealing massively increased weapon damage. The final stake is blessed,
    dealing bonus pure damage". Damage per stake scales with bonus weapon
    damage (x1.5); BonusDamage scales x3.6 per boon (wiki update log).
    T3: all stakes blessed. All stakes assumed to hit.
    """
    stakes = props["StakeCount"].base if "StakeCount" in props else 3
    per = props["Damage"].at(spirit, boons, weapon_pct)
    bonus = props["BonusDamage"].at(spirit, boons, weapon_pct) if "BonusDamage" in props else 0.0
    blessed = stakes if tier >= 3 else 1
    return {"weapon": per * stakes, "pure": bonus * blessed}


# ability class_name -> handler kind
SPECIAL = {
    "ability_stacking_damage": "fixation",
    "citadel_ability_chrono_kinetic_carbine": "kinetic_carbine",
    "ability_priest_weaponswap": "ira_domini",
}
