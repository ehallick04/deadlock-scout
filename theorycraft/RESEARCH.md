# Theorycraft research notes

The source research behind the simulator: where each number comes from, and
what the data and the wiki disagree on. `MECHANICS.md` is the current model;
this file is the evidence. Both parts date from 6 Nov 2026 and were written
while building it, so the code is newer where they differ (status notes are
inline).

## Part 1: field map (what each source provides)

> **Status:** later findings revised several lines below. See MECHANICS.md, "Scaling audit".
> - **Shots/s:** the API's formula is followed except for spin-up guns and 1-bullet "bursts", where the wiki is followed.
> - **Pellets:** Baba's and Drifter's share one shot's damage.
> - **Reload:** Abrams and Silver reload shell by shell.
> - **Spirit scaling:** 78 `scale_function_tech_damage` props without a stat type are spirit-scaled.
> - **Weapon scaling:** `EBaseWeaponDamageIncrease` scales per percentage point of bonus weapon damage.
> - **Souls curve:** measured per hero and rank band (`souls_curves.json`). `net_worth_series` samples every 180 s up to 900 s, then every 300 s, plus one at the match end.

Live probes: `/v1/assets/heroes`, `/v1/assets/items`, `/v1/assets/ranked-seasons`,
`/v1/assets/convars/*`, `/v1/assets/generic-data`. Wiki: Boon, Items, Weapon
Damage, Spirit Power, Fire Rate, Falloff Range, Cooldown Reduction, Ammo, Damage
Resistance (rendered with `action=render`).

### The stale-cache finding
deadlock-scout's `heroes.json` (31 Aug) and `items.json` (1 Sep) are out of date.
All 56 hero boon profiles changed since then, 705 items/abilities changed
properties, 75 are new and 19 are gone. `deadlock.heroes()` never expires its file.
Both files were refreshed on 6 Nov 2026. The simulator now reads them locally
(no network per run); `refresh_catalog()` refetches after a patch.

### Heroes (`/v1/assets/heroes`)
| Need | Field | Notes |
|---|---|---|
| Soul thresholds per level | `level_info[n].required_gold` | 36 levels. **Excludes the 600 starting souls.** Threshold = 600 + required_gold (convar `citadel_player_starting_gold`=600). Matches the wiki soul table exactly. |
| AP / unlock per level | `level_info[n].bonus_currencies` | `EAbilityUnlocks` at levels 1,3,5,8 (wiki "0,2,4,7"); `EAbilityPoints` otherwise. 32 AP total. |
| Boon per-level gains | `standard_level_up_upgrades` | `MODIFIER_VALUE_BASE_BULLET_DAMAGE_FROM_LEVEL` (per bullet, *not* per shot: Abrams 0.1 x 9 pellets = wiki's 0.90), `..._TECH_POWER`, `..._BASE_HEALTH_FROM_LEVEL`, `..._BASE_MELEE_DAMAGE_FROM_LEVEL`. |
| Base health / melee | `starting_stats.max_health`, `light/heavy_melee_damage` | |
| Spirit-scaled hero stats | `scaling_stats` | e.g. Haze `EClipSize` x0.5 SP, Vindicta `EBulletDamage` x0.022. Semantics only partly known. |
| Investment bonuses | `cost_bonuses.{weapon,spirit,vitality}[].gold_threshold/bonus` | weapon = Weapon Damage %, spirit = flat Spirit Power, vitality = Health %. Matches the wiki table. |
| Weapon / abilities | `items.weapon_primary`, `items.signature1..4` | class names into `/v1/assets/items`. |
| Crit bonus scale (Drifter -45% etc.) | **missing** from hero; weapon has `crit_bonus_start` (1.65, Rat King 1.35). |

### Weapon (`items[type=weapon].weapon_info`)
`bullet_damage`, `bullets` (pellets), `cycle_time`, `burst_shot_count`,
`intra_burst_cycle_time`, `clip_size`, `reload_duration`,
`damage_falloff_start_range/end_range/end_scale` (game units; /39.37 = metres,
matches wiki for Abrams 17-40 m and Haze 20-46 m), `crit_bonus_start`.
- shots/s = burst / (cycle + burst*intra_burst). This reproduces the API's own `shots_per_second` exactly for all 45 heroes, bursts included.
- The API's `*_with_reload` = clip / (clip*cycle + reload + **0.25 s**). The 0.25 s has no source I can find. The simulator leaves it out by default (`reload_overhead=0`).
- `damage_falloff_bias` (0.5) exists. The wiki says falloff is linear, and the bias is unexplained.

### Abilities (`items[type=ability]`)
- `properties[Name].value` is the base. A `scale_function` of `scale_function_tech_damage` with `stat_scale` gives the spirit coefficient (Sleep Dagger 65 + 2.2/SP, which matches the wiki).
- `AbilityCooldown`, `AbilityCharges`, `AbilityCooldownBetweenCharge`, `AbilityDuration`.
- `upgrades[0..2].property_upgrades[] {name, bonus, upgrade_type?, scale_stat_filter?}`. `upgrade_type` is None (add to base), `EAddToBase`, `EMultiplyBase`, `EAddToScale` or `EMultiplyScale`.
- **Missing:** whether a damage number is per hit, per tick or per second, the hit count, and the cast pattern. These are inferred from the property name (`DPS`/`DamagePerSecond` = per second x duration), so they are approximate.
- AP tier costs 1/2/5 come from the wiki only.

### Shop items (`items[type=upgrade, shopable]`)
- `cost`, `item_tier`, `item_slot_type`, `component_items`, `activation`, `is_active_item`, `disabled`.
- Stats: `properties[*].provided_property_type` (MODIFIER_VALUE_*), skipping `usage_flags: ConditionallyApplied`. The main ones are `WEAPON_DAMAGE_INCREASE` (BaseAttackDamagePercent), `TECH_POWER`, `TECH_POWER_PERCENT`, `FIRE_RATE`, `AMMO_CLIP_SIZE_PERCENT`, `AMMO_CLIP_SIZE`, `COOLDOWN_REDUCTION_PERCENTAGE`, `RELOAD_SPEED`, `HEALTH_MAX` and `HEALTH_MAX_PERCENT`.
- `upgrades` / `corrupted_info` on shop items are the **Street Brawl "Enhanced" / corrupted** variants and do not apply in normal mode. The 17 tier-5 items at cost 9999 are Street Brawl legendaries.
- Procs and actives (Mystic Burst, Tesla, Toxic Bullets...) are free-form. Only "tech_damage prop + cooldown" is modelled generically. The rest is listed as unmodelled.

### Not in any source
Conditional damage (headshot procs, build-up), stacking ability mechanics (Haze
Fixation), ult channels (Bullet Flurry), and the souls-over-time curve. The
souls curve is an input; the default is a labelled approximation.
`ranked-seasons` carries nothing combat-related.

### Wiki self-inconsistency
The Boon page's per-boon "Full Details" tables put boon 1 at 0.6K, which is
200 souls below its own soul table (0.8K). The live convar (600 start) agrees
with the soul table, so the detail tables are stale.

## Part 2: item effects research (6 Nov 2026)

Output: `theorycraft/item_effects.json`. It has 61 items and 89 effects, and the validator reports 0 errors.

### Method
- **Numbers.** All numbers come from `items.json` (the live API dump) and are referenced by property name. I used only two literals:
  - Stalker wound: 85 = DPS 17 × DebuffDuration 5.
  - None of the other effects needed one.
- **Semantics.** Per hit vs per second, triggers, stacking and what counts as spirit damage all come from the rendered deadlock.wiki pages, (fetched with `action=render`; not kept).
- **Street Brawl.** The wiki's "Enhanced" and "Corrupted" tabs are the Street Brawl variants, so I ignored them along with the API's `upgrades` and `corrupted_info`.
- **Reading the simulator.** I read `theorycraft/effects.py`, `simulate.py` and `data.py` without editing them. That way the spec avoids double counting:
  - Unconditional lifesteal and regen props are already read by `stats()`.
  - The close and long range weapon bonuses are already applied by distance.
  - So Bullet Lifesteal, Spirit Lifesteal, Leech, Extra Regen, Juggernaut, Point Blank, Sharpshooter and Long Range have `effects: []` on purpose.
- **Missing or disabled items.** `upgrade_point_blank` does not exist; Point Blank is `upgrade_close_quarter_combat`. Every other class exists, is shopable, and is not disabled.

### Hazards found in the data

> **Status:** all of the hazards and gaps listed here were fixed in the simulator afterwards, except the items still marked `unmodelled`. The fixes are: mislabelled props excluded, stacks honored, lifesteal stacked multiplicatively, Siphon Bullets and Tankbuster as true damage, Headhunter scaling per boon, and Alchemical Fire and Lucky Shot coded. Decay, Express Shot, Escalating Exposure's amp, Lightning Scroll and Mystic Reverb remain unmodelled. Mercurial Magnum was later found to be misread here too: its per-bullet bonus is a % of base bullet damage, capped at one clip per cast. See MECHANICS.md.
These props have **no** `ConditionallyApplied` flag but are conditional, so `Item.stat()` counts them as permanent. They are listed in `_about.mislabelled_props`.

| Item | Prop | What it really is |
|---|---|---|
| Vampiric Burst | `ActiveBonusLifesteal` = 75 (bullet lifesteal) | 5s of every 30s. This is the worst one: today it is a permanent 75% lifesteal. |
| Fury Trance | `TechResist` = 40 | Active only |
| Spiritual Overflow | `BonusFireRate` 25, `BonusSpirit` 30, `BonusSpiritLifesteal` 10 | Flagged `IntrinsicallyProvidedInAbility`, but only active during the build-up buff |
| Mercurial Magnum | `BonusFireRate` 22 | Only for 12s after an imbued cast |
| Quicksilver Reload | `BonusFireRate` 10 | Only for 12s after an imbued cast |
| Mystic Reverb | `ImbueAbilityLifesteal` 22 | Imbued ability only |
| Focus Lens | `MagicResistReduction` | An enemy debuff |

`IntrinsicallyProvidedInAbility` is ambiguous. It marks plain stats on Bullet Resist Shredder, Mystic Vulnerability, Siphon Bullets and others, but buff-only stats on Spiritual Overflow.

Other simulator gaps the spec cannot fix:
- `conditional_stats()` ignores `stacks_prop`. Berserker and Glass Cannon use it, so today they count one stack.
- The simulator sums lifesteal, but the wiki says lifesteal stacks multiplicatively.
- Siphon Bullets damage ignores resists, but the simulator applies spirit resist to `pct_hp`.
- Tankbuster ignores spirit resist.
- Headhunter's bonus damage scales per boon (`ELevelUpBoons`), not with spirit power.
- Items marked only `unmodelled` now get 0 damage, because `item_damage` skips any item that has a spec entry. The big ones are Alchemical Fire, Decay, Lucky Shot, Express Shot, Escalating Exposure's amp, Lightning Scroll and Mystic Reverb. Each `reason` gives a closed-form approximation.

### Low-confidence effects
- **Headhunter heal.** Neither source says what `HealPercentPerHeadshot` (4%) is a percent of. I assumed own max HP.
- **Mercurial Magnum per-bullet bonus.** Its uptime depends on how many bullets land before the next reload.
- **Spiritual Overflow buff.** The build-up and rebuild rule is not documented.
- **Glass Cannon.** Average kill stacks are a guess. The data also has a 30% build-up slow that no wiki text or tooltip mentions.
- **Spirit Strike and Spirit Snatch.** Uptime depends entirely on how often you melee (I assumed 0.3).

### Data vs wiki disagreements
- **Toxic Bullets.** A wiki note says 1.2% per tick × 6 ticks = 7.5%. The API and the wiki's own update log say 1.9%/s for 4s = 7.6%. The note is stale.
- **Spirit Lifesteal vs creeps.** The wiki says 40% effective. The data has `NonHeroAbilityLifestealTooltipOnly` = 3 against 15, which is 20%.
- **Mercurial Magnum.** `AbilityCooldown` is 15 and `AbilityChargeUpTime` is 14; the wiki shows 14s.
- **Shadow Weave.** A wiki note mentions a 25% fire-rate ramp, but the current tooltip and the API say +50% weapon damage and +25 SP.
- **Unexplained props.** These have no explanation anywhere:
  - Mystic Reverb `MaxHealthDamage` = 10
  - Weakening Headshot and Crippling Headshot `DiminishingMultiplier` = 0.5
  - Tesla Bullets and Capacitor `BonusPerChain`
- **Shred values agree.** Every bullet and spirit shred value on the Damage_Resistance page matches the API.

### The four questions
1. **Does shred apply to objectives?**
   - **Mid-Boss:** yes, but only 50% as effective. The Mid-Boss update log for 30 Oct 2026 says: "Midboss resistance reductions are now only 50% as effective against the Midboss."
   - **Lane Guardian and Walker:** unknown. Their pages give damage-resist, player-vulnerability and bullet-resist formulas (Guardian 10%, Walker 35% bullet resist) but say nothing about shred.
   - Toxic Bullets bleed does not build on Guardians or Walkers, and the Mid-Boss is immune to it. Sources: Toxic_Bullets, Lane_Guardian, Walker, Mid-Boss.
2. **How do bullet resist reductions stack?** Sources: Damage_Resistance, Armor_Piercer, Spirit_Rend.
   - Reductions combine like resists, multiplicatively: R_red = 1 − ∏(1 − r_i).
   - That total is **subtracted** from the target's own multiplicatively stacked resist: final = R_total − R_red.
   - Final resist **can go negative**, which amplifies damage. For example, −30% resist means the target takes 130%.
   - Armor Piercer procs ignore bullet resist, and on those bullets shred adds instead of multiplying.
   - Spirit Rend's headshot stacks add to −28% among themselves, then multiply with the base −8%, for −33.76% at most.
3. **Lifesteal: before or after resists, and how strong vs non-heroes?**
   - **Before or after resists:** not stated explicitly. The Lifesteal page says it "returns a percentage of all outgoing bullet damage", and the wiki never gives a pre-mitigation basis. Assuming post-resist damage dealt (which `simulate.py` does) is reasonable, but it is unverified.
   - **Vs non-heroes:** bullet lifesteal is 60% effective and spirit lifesteal 40% (Lifesteal page), though the data hints 20% for spirit.
   - Melee Lifesteal heals at 30% on non-heroes and Lifestrike at 40%. Restorative Shot heals 20 from NPCs instead of 50.
   - Several lifesteal sources stack multiplicatively.
4. **Does item spirit damage count for spirit lifesteal and spirit shred?** Yes for both.
   - It is spirit damage, reduced by spirit resist (Tesla_Bullets and Toxic_Bullets notes), so spirit shred amplifies it.
   - The Lifesteal page says spirit lifesteal returns a share of "all outgoing spirit damage", and the Mystic_Burst page confirms that spirit lifesteal heals from its proc.
   - Item spirit damage also triggers on-spirit-damage items: Stalker and Torment Pulse trigger Bullet Resist Shredder, Escalating Exposure and Mystic Vulnerability.
   - **Exceptions:**
     - Tankbuster "no longer procs off of items" (July 2026).
     - Decay does not apply item procs, except Spirit Burn.
     - Siphon Bullets damage ignores resists and amps and does not proc items.
     - Tankbuster's own damage ignores spirit resist.
   - Whether every item's damage heals with spirit lifesteal is confirmed explicitly only for Mystic Burst. For Mystic Shot and Tesla it is inferred.
