"""
theorycraft.combat -- what the damage is dealt TO: resist stacking, shred,
objectives on their clocks, and the attacker's own sustain.

Sources (6 Nov 2026), checked against each other where both exist:
  * wiki Damage_Resistance: resist sources stack multiplicatively,
        total = 1 - prod(1 - r_i); reductions ("shred") stack the same way and
        are then SUBTRACTED: final = total_resist - total_reduction.
  * wiki Lane_Guardian (+ /v1/assets/npc-units npc_boss_tier1):
        bullet = BD x [(1 - 0.5) + t/720] x (1 - PlayerResist) x (1 - BulletResist)
        spirit = SD x [(1 - 0.5) + t/720]
        PlayerResist -25% is BULLET-ONLY; bullet resist 10%; headshots land on
        the brazier.
  * wiki Walker (+ npc_boss_tier2): 65% -> -65% at 18:00, bullet resist 35%,
        weapon resist 20%..50% with 3..6 attacking heroes within 51 m, no
        headshots.
  * wiki Shrine: bullet resist 20%, plus 60% bullet resist losing 20% per
        attacking hero (0% at 3+), multiplicative.
  * Base Guardian (npc_barrack_boss): bullet resist 20%, player resist -25%,
        crowd bullet resist 40% losing 20% per hero (API backdoor_bullet_resist_modifier).
  * wiki Patron: 0% bullet and spirit resist.
  * Mid-Boss (wiki + npc_super_neutral): bullet resist 15%, and resist
        reductions are only 50% effective against it.

Shred on structures: none of the Lane_Guardian, Walker, Shrine or Patron pages
says whether item shred applies, and the damage formulas they give have no
shred term. The Mid-Boss is the only unit documented to take it (at 50%). So it
stays a switch (Target.shred_on_objectives), off by default.
"""

from dataclasses import dataclass, field


def stack(values):
    """Multiplicative stacking of fractions (0.3 = 30%): 1 - prod(1 - v)."""
    keep = 1.0
    for v in values:
        keep *= 1.0 - v
    return 1.0 - keep


def final_resist(resists, reductions, reduction_effectiveness=1.0):
    """Wiki rule: stack each group, then subtract. May go negative (amplifies)."""
    return stack(resists) - reduction_effectiveness * stack(reductions)


def walker_crowd(n):
    return {3: 0.20, 4: 0.30, 5: 0.40}.get(n, 0.50 if n >= 6 else 0.0)  # 4, 5 interpolated


@dataclass
class Objective:
    name: str
    bullet_resist: float              # flat bullet resist
    player_bullet_resist: float       # bullet resist vs players (negative = takes more)
    decay_start: float = 0.0          # time-scaling damage resist at t=0 (all damage)
    decay_end: float = 0.0            # ... at decay_s and after
    decay_s: float = 0.0
    shred_effectiveness: float = 1.0  # < 1 when the unit resists resist reduction
    headshots: bool = False
    health: float = 0.0
    crowd: object = None              # attacking heroes -> extra bullet resist (fraction)

    def damage_resist(self, t):
        if self.decay_s <= 0:
            return self.decay_start
        f = min(max(t / self.decay_s, 0.0), 1.0)
        return self.decay_start + (self.decay_end - self.decay_start) * f

    def multipliers(self, t, bullet_shred=0.0, spirit_shred=0.0, shred_applies=False,
                    attackers=1):
        """
        (bullet damage multiplier, spirit damage multiplier) at time t.

        Bullet resist pool = flat bullet resist, the vs-player modifier and the
        crowd resist, stacked multiplicatively; shred comes off that stacked
        total (wiki Damage_Resistance). The time-scaling Damage Resist is its own
        stat applied to all damage, so it multiplies separately -- with no shred
        this is exactly the Lane_Guardian page's formula.
        """
        common = 1 - self.damage_resist(t)
        eff = self.shred_effectiveness if shred_applies else 0.0
        pool = [self.bullet_resist, self.player_bullet_resist]
        if self.crowd:
            pool.append(self.crowd(attackers))
        bullet = common * (1 - final_resist(pool, [bullet_shred], eff))
        spirit = common * (1 - final_resist([], [spirit_shred], eff))
        return bullet, spirit


OBJECTIVES = {
    "Guardian": Objective("Guardian", 0.10, -0.25, 0.50, -0.50, 720, headshots=True, health=5500),
    "Walker": Objective("Walker", 0.35, 0.0, 0.65, -0.65, 1080, health=6000, crowd=walker_crowd),
    "Mid-Boss": Objective("Mid-Boss", 0.15, 0.0, shred_effectiveness=0.5, health=15600),
    "Base Guardian": Objective("Base Guardian", 0.20, -0.25, health=4000,
                               crowd=lambda n: max(0.0, 0.40 - 0.20 * n)),
    "Shrine": Objective("Shrine", 0.20, 0.0, health=5000,
                        crowd=lambda n: max(0.0, 0.60 - 0.20 * n)),
    "Patron": Objective("Patron", 0.0, 0.0, health=12000),
}

# Abilities the wiki lists as dealing reduced damage to objectives (Lane_Guardian,
# Walker, Patron pages agree). Everything else is full damage when it hits.
OBJECTIVE_ABILITY_MULT = {
    "Hyper Beam": 0.5, "Fixation": 0.2, "Flame Dash": 0.5, "Heavy Barrage": 0.5,
    "Nox Nostra": 0.5, "Pulse Grenade": 0.5, "Affliction": 0.5, "Storm Cloud": 0.5,
    "Vexing Bolt": 0.5, "Setup Bishop": 0.5, "Aura of Suffering": 0.5, "Last Stand": 0.5,
}

# Abilities that deal WEAPON damage (wiki Damage_Resistance, "Bullet Resist"):
# bullet resist and bullet shred apply to them, not spirit resist.
WEAPON_DAMAGE_ABILITIES = {
    "Chimera Mk1", "Jettison Fuel", "Fixation", "Crackshot", "Scrap Grenade",
    "Royal Pestments", "Rule, Ratannia!", "Consecrating Grenade", "Gutshot", "Ira Domini",
}

# Wiki "Unstable_Rift": first spawns 10-12 min (11 nominal), then every 6-8 min (7).
RIFT_FIRST_S, RIFT_EVERY_S = 660, 420


def rift_times(end_s):
    out, t = [], RIFT_FIRST_S
    while t <= end_s:
        out.append(t)
        t += RIFT_EVERY_S
    return out
