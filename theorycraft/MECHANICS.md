# theorycraft: what is modelled

A damage-over-time simulator for build theorycrafting. It is a library only;
there is no UI yet. The field map (which source gives what) is in
`data.py`'s docstring and in the shared session note
`dot-simulator-field-map.md`.

```python
from theorycraft import simulate, Target
r = simulate("Haze",
             items=["Extended Magazine", "Headshot Booster", ("Swift Striker", 420)],
             ability_order=[1, 3, 2, 3, 4, 3],            # slots or ability names
             souls_curve=[(0, 600), (600, 9000), (1800, 31000)],
             target=Target(distance_m=15, headshot_rate=0.2, bullet_resist=0.3),
             end_s=1800)
r.frame()        # timeline as a DataFrame (t, souls, boons, spirit, weapon_dps, ...)
r.spikes         # events that moved total DPS or combo damage by >= spike_pct
r.unmodelled     # what the build contains that was NOT counted -- read this
r.warnings       # purchases asked for before they were affordable, dropped AP entries
```

## Modelled, and checked against the wiki or the API

| Mechanic | Source | Check |
|---|---|---|
| Soul threshold per level = 600 starting souls + `required_gold` | API `level_info`, convar | All heroes match the wiki soul table (800 … 49,200) |
| Unlocks at levels 1/3/5/8, 32 AP, tier costs 1/2/5, ult only from the 4th unlock | API + wiki | Planted queue tests |
| Boons: base bullet (per pellet), spirit power, health | API `standard_level_up_upgrades` | Wiki boon tables for Haze, Abrams (9 pellets), Grey Talon, Vindicta, Kelvin |
| Investment bonuses: weapon %, spirit flat, health % | API `cost_bonuses` | Wiki investment table |
| Component items: consumed, discounted, counted once in investment | API `component_items` | Planted test |
| Weapon: shots/s = burst/(cycle + burst × intra-burst); pellets | API `weapon_info` | Equals the API's own `shots_per_second` and `damage_per_second` for every hero |
| Falloff: linear from start to end range, down to end scale (×0.1) | API, wiki | Ranges in metres match the wiki |
| Weapon damage = (base + boons) × (1 + Σ weapon %) | Wiki formula | Planted test |
| Close-range / long-range weapon bonus, gated on target distance | API item props | — |
| Crit: 1 + headshot rate × (`crit_bonus_start` − 1) | API, wiki | Planted test |
| Clip = ceil((base + flat) × (1 + Σ %)); sustained DPS includes reload | Wiki Ammo | Planted test |
| Spirit damage = base + spirit power × coefficient | API `scale_function.stat_scale` | Sleep Dagger 65 + 2.2 × SP, as on the wiki |
| Ability upgrades: add base, add scale, multiply | API `upgrades[].property_upgrades` | Planted test |
| Cooldown reduction stacks multiplicatively | Wiki | Planted test |
| Charges restore one at a time, so the sustained rate is 1/cooldown; charges count toward burst | Wiki Charges | — |
| Bullet and spirit resist on the target | Parameters | Planted test |

## Scaling audit (6 Nov 2026)

- **Weapon: all 44 heroes** match the wiki's Hero_Comparison_Table base DPS and sustained DPS within 2%. This is a test (`WIKI_BASE_DPS`). Getting there fixed:
  - **Baba and Drifter**: pellets share one shot's damage (their wiki pages say so). They were 3× too high. No data flag marks this; `SHARED_PELLET_HEROES` lists them.
  - **Abrams and Silver** reload shell by shell (`reload_single_bullets`). The full reload is the initial delay plus the per-shell time × clip, not 0.35 s.
  - **McGinnis and Victor** spin up; they are taken fully spun (`max_spin_cycle_time`), as the wiki does.
  - **Paige, Rat King, Solomon and Doorman** carry an intra-burst time on a 1-bullet "burst". The wiki ignores it and the API's derived field adds it; we follow the wiki. Real bursts (Seven, Lash, Paradox, Sinclair) agree in both sources.
- **Boons: all 44 heroes'** base and per-boon health and spirit power, and base and per-boon bullet damage, match the wiki Boon tables (128 checks).
- **Spirit scaling:** 181 of the 201 coefficients on the wiki's Spirit_Power list are found in the data.
  - **Fixed:** 78 ability damage properties carry `scale_function_tech_damage` with a coefficient but no stat type (Apollo's Disengaging Sigil ×1.3, for example). They had been getting **no spirit scaling**. Mina's Rake carried `EStatsCount`; the wiki shows ×1.0 spirit.
  - Of the 20 misses, most are healing (a separate class the damage model does not use). Aura of Suffering, Setup Bishop and Lycan Curse differ on display properties with base 0, or look stale on the wiki; they are left as they are.
- **Weapon-damage scaling** (`scale_function_ability_weapon_damage` / `EBaseWeaponDamageIncrease`) is now read:
  - Venator: Consecrating Grenade ×1.0, Gutshot ×0.7 (+×0.8 wall bonus), Ira Domini ×1.5.
  - Deadman Danny: Chimera Mk1 ×1.4, Jettison Fuel ×0.8.
  - Rat King: Scrap Grenade ×0.6, Rule Ratannia! ×1.2.
  - Haze: Fixation's T3 per-stack bonus.
  - It scales with **bonus** weapon damage (items + investment, not boons; wiki Weapon_Damage). The unit is **damage per percentage point**. No wiki page states it, but the data only works that way. Fixation T3 adds 0.00035 per stack per unit: per fraction that would be nothing, per point it is +0.035 per stack at +100%. The wiki's ability-card module shows this type's coefficient raw (×1.5) and only transforms `weapon_power`.
- **Kinetic Carbine** (Paradox) uses the other weapon type, `weapon_power`. The wiki's module displays it as (coeff/100)+1, applied to a full burst from **base** weapon damage, which grows with boons and weapon items. At base: 6.8 × 5 × 2.25 = 76.5, exactly the wiki's listed max damage. The 5.0 base value is the burst count. It is spirit damage; spirit shred applies and bullet shred does not (wiki Paradox). It is taken at full charge, with +14% on a headshot. Tested.
- **Ira Domini** (Venator) fires 3 stakes of weapon damage (×1.5 bonus weapon damage each). The blessed stake adds **pure** damage, 95 + 3.6 per boon, which ignores resists and objective resist. At T3 all three stakes are blessed. All stakes are assumed to hit. Tested.
- **Fixation** (Haze) is flat bullet damage per stack: 0.2, +0.11 and weapon scaling at T3, up to 40 stacks (80 at T2). It gains 1 stack per hit and 3 per headshot, averaged over a 10 s fight ramp. It is added after the weapon-damage % and is affected by falloff, resist and crit (wiki Weapon_Damage), and deals 20% to objectives. The T1 proc is spirit damage every 20 stacks. Tested.
- **Boon-scaled ability properties** (`ELevelUpBoons`) use the current boon count. Venator's multi-stat values also list `EWeaponDamageScale` and `EDamageScale`; those are treated as 1 (unknown semantics; the wiki shows only the boon coefficient).
- **Not modelled:** Consecrating Grenade's burn. The wiki says "burning targets deal pure damage to enemies in the area", i.e. splash around the burning target, not extra damage to it. Also not modelled: Bullet Dance's gun buff during the ultimate.

## Approximated (the number comes out, but the shape is a guess)

- **Souls over time.** Measured median net worth over time per hero and rank band (`souls_curves.json`, built by `measure_souls.py` from the server's sweep matches, Oct 14 – Nov 1 2026). Each band reads one sweep frame: Phantom and up from the badge-91 sweep, lower bands from the badge-0 sweep's ranked games. Points with fewer than 30 players are dropped. These are **medians of a typical game**: a fed or starved game moves every spike. Late points come only from games that lasted that long. Rank barely moves the curve (Initiate–Acolyte 11.9k vs Eternus 13.2k at 15:00); the hero moves it about three times as much (Phantom: Nurse Harrow 11.3k, Wraith 14.7k). The sample times of the server's `net_worth_series` were established for this: every 180 s to 900 s, every 300 s after, plus one at the match end. All 480k stored rows fit that schedule. `DEFAULT_SOULS_CURVE` remains only as the fallback.
- **Ability damage shape.** The API does not say whether a number is per hit, per tick or per second. Properties named `Damage`, `ImpactDamage`, `ExplosionDamage`, … count once per cast. `DPS` / `DamagePerSecond` / `FireDPS` count × `AbilityDuration`. Hit counts, ticks and channels are not in the data. Use `ability_overrides={slot: {"hits": n, "duration": s}}`.
- **Casts on cooldown.** Every damaging ability is cast the moment it is ready, and every hit lands.
- **Damage items** with a cooldown (Mystic Burst and similar) proc every cooldown, with their condition assumed met. Item cooldown reduction is not applied.
- **Spirit power %** (Boundless Spirit) multiplies all spirit power, boons included.
- **Spirit-scaled hero stats** (`scaling_stats`): `EBulletDamage` adds SP × scale to base bullet damage, `EClipSize` adds to base ammo, `EFireRate` adds to fire-rate %. Haze's ammo-from-spirit is in the data but not on the wiki.
- **Reload speed** items scale reload time by (1 + value/100). The sign convention is a guess.
- **`EMultiplyBase` is read as a percent** ('33.33' → ×1.3333). This is inferred from Baba's Brew, which ships it paired with `EMultiplyScale` 1.3333.
- **Combo damage**: one cast of every ability, with all charges, plus every item proc and `combo_window_s` of gunfire.
- The API's `shots_per_second_with_reload` adds 0.25 s per reload, from an unknown source. It is exposed as `reload_overhead` (default 0) and moves sustained DPS by about 3–5%.

## Targets, shred, objectives, sustain (`combat.py`)

**Resist math, audited against wiki Damage_Resistance (6 Nov 2026):**
- Resist sources stack as 1 − ∏(1 − r). Shred sources stack the same way in their own pool, and that total is subtracted from the stacked resist. The final value can go negative, which amplifies damage. Tests reproduce the page's worked example (52% − 40% = 12%).
- Hero targets: `Target.bullet_resist` / `spirit_resist` are the enemy's total resists; your shred comes off them.
- Objectives: the bullet pool is flat bullet resist, the vs-player modifier and the crowd resist, stacked; shred comes off that total. The time-scaling Damage Resist is a separate all-damage stat and multiplies on top.
  - **Fixed in the audit:** shred used to come off the flat bullet resist only, which overstated it by 1.25× on Guardians.
  - With no shred, this is exactly the Lane_Guardian page's formula.
- **Fixed in the audit: weapon-damage abilities.** Crackshot, Fixation, Gutshot, Ira Domini, Chimera Mk1, Jettison Fuel, Scrap Grenade, Royal Pestments, Rule Ratannia! and Consecrating Grenade take bullet resist and bullet shred (not spirit), and heal through bullet lifesteal.
- Shred uptime is applied as an expected value per source before stacking. That is close for one or two sources and slightly off for many.

| Objective | Source | Model |
|---|---|---|
| Lane Guardian | wiki + npc_boss_tier1 | Damage resist 50% → −50% by 12:00; bullet pool 10% and −25% vs players (bullet only; spirit gets the time resist alone); takes headshots on the brazier |
| Walker | wiki + npc_boss_tier2 | 65% → −65% by 18:00; 35% bullet; crowd resist 20–50% with 3–6 attacking heroes (4 and 5 interpolated); no headshots. Backdoor protection (65%) and `ranged_armor_modifier` are not applied |
| Mid-Boss | wiki + npc_super_neutral | 15% bullet; shred 50% effective |
| Shrine | wiki | 20% bullet and crowd 60% −20% per attacking hero (0 at 3+); backdoor not applied |
| Base Guardian | npc_barrack_boss | 20% bullet, −25% vs players, crowd 40% −20% per hero |
| Patron | wiki | 0% / 0% (phase 1); the Weakened Patron's 50%/100% defender reduction is not applied |
| Rift | wiki Unstable_Rift | Nominal 11:00, then every 7:00 (±1 min) |

- **Shred on structures:** none of the Lane_Guardian, Walker, Shrine or Patron pages says whether item shred applies, and their damage formulas have no shred term. Only the Mid-Boss is documented to take it (at 50%). It stays a switch, off by default.
- **Abilities vs structures:** off by default. When on, the wiki's per-ability reductions apply (Fixation 20%; Hyper Beam, Flame Dash, Heavy Barrage, Nox Nostra, Pulse Grenade, Affliction, Storm Cloud, Vexing Bolt, Setup Bishop, Aura of Suffering and Last Stand 50%).
- %-max-HP damage is not applied to structures.
- **Accuracy and headshots:** `Target.accuracy` is the share of bullets that land; it scales the gun and every on-hit proc. `headshot_rate` is the share of landed bullets that are headshots; it drives crit damage and headshot procs.
- **Uptime overrides:** `Target.uptimes` overrides any item effect's uptime (`"<class_name>#<index>"`). The UI exposes this as editable tables (the build's items in Simulate, every gun-dependent item in Recommend).
- **Sustain:** lifesteal (sources stacked multiplicatively) × post-resist damage to the hero, plus regen and item heals. Durability = HP against the lane's bullet/spirit mix. Lifesteal on post-resist damage is assumed; the wiki doesn't say.
- **Target max HP for %-HP effects** defaults to a mirror: the attacker's own HP at that time.

## Item effects (`effects.py` + `item_effects.json`)

- **Coverage:** 61 items, researched against the API properties and their wiki pages (6 Nov 2026), in a fixed vocabulary. The effect types are: shred, conditional stats (with stacks), on-hit and periodic spirit procs, %-max-HP DoTs, headshot bonuses, lifesteal, regen, heals on events, weapon multipliers, and `unmodelled`. Each effect carries an **uptime** (expected-value share of a fight), a **confidence** (40 high, 37 medium, 12 low) and its reasoning. The research notes are in the shared session notes (`item-effects-research.md`).
- **Uptime is applied linearly** (+20% up half the time counts as +10%). Range-, melee- and headshot-dependent uptimes are guesses to tune per hero.
- **Fixed after the research:**
  - Seven props the API does not flag as conditional but that are are now excluded from the always-on stats (`_about.mislabelled_props`). Examples: Vampiric Burst's 75% lifesteal is active-only; Spiritual Overflow's buff stats.
  - Stacks are honored on conditional stats (Berserker: up to +70%, not +7%).
  - Siphon Bullets and Tankbuster's proc deal true damage.
  - Headhunter's bonus scales per boon.
  - Lifesteal sources stack multiplicatively (wiki Lifesteal).
- **Mercurial Magnum was badly overvalued and is fixed.** Its per-bullet bonus is a percent of base bullet damage (the property is labelled "Base Bullet Damage" with a "%" postfix), not a flat number. It also applies only to the one magazine after each cast. It was valued at ~140 DPS on Haze; it is now ~17 DPS. I checked every other spec damage property's unit against its postfix and label; none had the same problem.
- **Coded from the research's closed forms:** Alchemical Fire ≈ 3.9 × DPSMax per 30 s cast; Lucky Shot as a separate ×(1 + 25% × 100%) weapon multiplier.
- **Still unmodelled, so the recommender under-buys them:** Decay (% of current HP), Express Shot, Escalating Exposure's stacking amp, Lightning Scroll, Mystic Reverb, Tankbuster's 7.5%-current-HP part, Toxic Bullets' anti-heal, and every CC or utility effect.

## Recommender (`optimize.py`)

- A beam search that compares buy orders only at **equal spend** (bucketed to 800 souls). Comparing equal item counts made it rush a 6,400-soul item, because one big item beats one small one over the game whatever follows.
- Each order is played out on the measured souls curve. It is scored every 60 s on: DPS vs the enemy hero (×2 within 90 s of a Rift, adjustable), DPS vs Guardians from 6–14 min and Walkers from 12–24 min, and lane durability up to 9:00.
- **Scoring modes:**
  - *floor*: maximize damage with durability ≥ a floor; any build under the floor ranks below every build over it;
  - *weighted*: each part normalized to the no-items baseline;
  - *frontier*: five sustain weights, keeping the builds not beaten on both damage and sustain.
- **Laning modes:** *locked* (your lane items first), *matchup* (the enemy laners' 3 s burst split sets the durability mix), *auto*.
- **Distance:** weapon damage is averaged over a fight-distance mix (default 10/20/30 m). Range items switch on at exactly 15 m, and a single distance let the search collect both close- and long-range bonuses.
- **Not exhaustive.** Candidates are pre-ranked by damage gain per soul at purchase time. Items with no modelled effect still count for their investment bonus. **Anything the simulator does not count, the recommender cannot value.** Items whose effect is unmodelled are systematically under-bought.
- A generic "damage property + cooldown = proc" fallback used to count trigger conditions (Witchmail's `DamageThreshold`) as damage. It now accepts only properties named as damage, and a researched entry in `item_effects.json` overrides it.

## Not modelled (listed in `Result.unmodelled` when they appear in a build)

- Conditional and proc damage: headshot procs, build-up/bleed (Toxic Bullets), stacking passives (Haze Fixation), Tesla-type chains.
- Ultimates and abilities that buff the gun instead of dealing damage (Bullet Flurry, fire-rate steroids). Ability-granted fire rate is not applied.
- Fire-rate slows, enemy debuffs on you, shred items (resist reduction), imbued spirit power (Surge of Power), melee, healing and lifesteal.
- Falloff curve shape: `damage_falloff_bias` (0.5) exists in the data, and the wiki says linear.
- Hero crit-bonus scale (Drifter −45%, etc.) is not in the hero data, and per-hero falloff exceptions (Apollo, Graves, Drifter "no damage past max range") are not handled.
- Item slot limits, selling, Street Brawl "enhanced"/corrupted items (`upgrades`/`corrupted_info` on shop items) and legendaries.
- `/v1/assets/ranked-seasons` has nothing combat-related. It is not used.

## Data notes

- Data comes from the repo's local snapshots, `heroes.json` and `items.json`, which are the same files `deadlock.heroes()` / `deadlock.items()` keep. A simulation makes no network calls. They were refreshed on 6 Nov 2026; the 31 Aug copies predated a patch that changed every hero's boon profile. After a patch, run `refresh_catalog()`. It refetches both files and checks the starting-souls convar (pinned at 600 in `STARTING_SOULS`). `snapshot_age_days()` says how old they are.
- The wiki Boon page contradicts itself. Its per-boon "Full Details" tables put boon 1 at 0.6K, while its soul table and the live convar say 0.8K. The detail tables are stale.
