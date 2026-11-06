"""
theorycraft.effects -- the hand-modelled part of items: procs, conditional
stats, shred and sustain, read from item_effects.json.

The JSON is a researched spec (per item: effect type, which property holds the
number, an uptime and a confidence). This module only interprets it. The effect
vocabulary is fixed; anything that does not fit is `unmodelled` and reported.

Uptime is applied as an expected value: a +20% stat up 50% of the time counts as
+10%. That is linear where the game is not (stacking, burst windows), and is
listed as an approximation in MECHANICS.md.
"""

import json
import os
from functools import lru_cache

EFFECTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "item_effects.json")

TYPES = {"bullet_shred", "spirit_shred", "conditional_stat", "on_hit_spirit",
         "periodic_spirit", "max_hp_dot", "headshot_bonus", "bullet_lifesteal",
         "spirit_lifesteal", "regen", "heal_on_event", "weapon_multiplier", "unmodelled"}

# Optional keys on any damage effect:
#   ignores_resist: true   -- true damage (Siphon Bullets, Tankbuster's proc)
#   heals_self: true       -- you heal by the damage dealt (Siphon Bullets)
#   damage_mult: x         -- closed-form multiplier on damage_prop (a ramp, e.g.
#                             Alchemical Fire ~3.9 x DPSMax per cast)
#   stacks_prop / max_stacks on conditional_stat -- value x stacks x uptime

# conditional_stat channels the simulator can fold into its stat sheet
STAT_KEYS = {
    "MODIFIER_VALUE_WEAPON_DAMAGE_INCREASE": "weapon_pct",
    "MODIFIER_VALUE_FIRE_RATE": "fire_rate_pct",
    "MODIFIER_VALUE_TECH_POWER": "spirit_flat",
    "MODIFIER_VALUE_BULLET_LIFESTEAL": "bullet_lifesteal_pct",
    "MODIFIER_VALUE_TECH_LIFESTEAL": "spirit_lifesteal_pct",
    "MODIFIER_VALUE_AMMO_CLIP_SIZE_PERCENT": "clip_pct",
    "MODIFIER_VALUE_BULLET_ARMOR_DAMAGE_RESIST": "bullet_resist_pct",
    "MODIFIER_VALUE_TECH_RESIST": "spirit_resist_pct",
}


@lru_cache(maxsize=1)
def load_effects(path=EFFECTS_FILE):
    """class_name -> list of effect dicts. Empty when the spec file is absent."""
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {k: list(v.get("effects") or []) for k, v in (data.get("items") or {}).items()}


@lru_cache(maxsize=1)
def load_excluded(path=EFFECTS_FILE):
    """class_name -> props the data does not flag as conditional but that are."""
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    mis = (data.get("_about") or {}).get("mislabelled_props") or {}
    return {k: list(v) for k, v in mis.items() if not k.startswith("_") and isinstance(v, list)}


def _val(item, eff, key, spirit, boons=0):
    """A number from `<key>_prop` (spirit or boon scaling) or `<key>_value`."""
    prop = eff.get(f"{key}_prop")
    if prop and prop in item.props:
        return item.props[prop].at(spirit, boons)
    v = eff.get(f"{key}_value")
    return float(v) if v is not None else 0.0


def effect_key(item, index):
    """Stable id of one effect, for uptime overrides: '<class_name>#<index>'."""
    return f"{item.class_name}#{index}"


def _uptime(eff, item=None, index=None, overrides=None):
    if overrides and item is not None and effect_key(item, index) in overrides:
        return min(max(float(overrides[effect_key(item, index)]), 0.0), 1.0)
    u = eff.get("uptime", 1.0)
    try:
        return min(max(float(u), 0.0), 1.0)
    except (TypeError, ValueError):
        return 1.0


def conditional_stats(items, spirit_guess=0.0, boons=0, overrides=None):
    """Phase A: stat deltas (percent points) from conditional_stat effects."""
    out = {}
    for it in items:
        for idx, eff in enumerate(it.effects):
            if eff.get("type") != "conditional_stat":
                continue
            key = STAT_KEYS.get(eff.get("stat"))
            if not key:
                continue
            stacks = 1.0
            if eff.get("stacks_prop") or eff.get("max_stacks"):
                stacks = float(eff.get("max_stacks") or _val(it, eff, "stacks", spirit_guess, boons) or 1)
            out[key] = out.get(key, 0.0) + (_val(it, eff, "value", spirit_guess, boons) * stacks
                                            * _uptime(eff, it, idx, overrides))
    return out


def shred(items, spirit, overrides=None):
    """(bullet reductions, spirit reductions) as fractions, uptime-weighted."""
    bullet, spirit_r = [], []
    for it in items:
        for idx, eff in enumerate(it.effects):
            t = eff.get("type")
            if t not in ("bullet_shred", "spirit_shred"):
                continue
            v = abs(_val(it, eff, "value", spirit)) / 100
            stacks = eff.get("max_stacks") or (
                _val(it, eff, "stacks", spirit) if eff.get("stacks_prop") else 1)
            v *= max(1.0, float(stacks)) * _uptime(eff, it, idx, overrides)
            (bullet if t == "bullet_shred" else spirit_r).append(min(v, 1.0))
    return bullet, spirit_r


def damage_and_sustain(items, spirit, weapon, target_hp, headshot_rate, own_hp=0.0, boons=0,
                       overrides=None):
    """
    Phase B, raw (pre-resist) numbers per second:
      weapon_extra  -- weapon-type bonus damage (headshot bonuses)
      spirit_extra  -- spirit damage from procs / on-hit / periodic
      pct_hp        -- %-max-health damage (treated as spirit damage)
      bullet_ls, spirit_ls (fractions), regen (hp/s), heals (hp/s)
      per_item      -- name -> dps, for display
    weapon: dict with sustained bullets/s ('hit_rate') and shots/s.
    """
    o = {"weapon_extra": 0.0, "spirit_extra": 0.0, "pct_hp": 0.0, "bullet_ls": 0.0,
         "spirit_ls": 0.0, "regen": 0.0, "heals": 0.0, "per_item": {}, "unmodelled": [],
         "true_dps": 0.0, "true_heal": 0.0, "weapon_mult": 1.0}
    hits, shots = weapon["hit_rate"], weapon["shot_rate"]
    for it in items:
        for idx, eff in enumerate(it.effects):
            t, up = eff.get("type"), _uptime(eff, it, idx, overrides)
            dps = 0.0
            if t == "on_hit_spirit":
                rate = hits if eff.get("per", "bullet") == "bullet" else shots
                if eff.get("proc_chance_prop"):
                    rate *= _val(it, eff, "proc_chance", spirit, boons) / 100
                cd = _val(it, eff, "cooldown", spirit, boons) if eff.get("cooldown_prop") else 0
                if cd > 0:
                    rate = min(rate, 1 / cd)
                if eff.get("cap") == "clip_per_window":
                    # e.g. Mercurial Magnum: only the magazine after each cast is buffed
                    window = _val(it, eff, "window", spirit, boons) or 1.0
                    per_clip = weapon.get("clip", 0) * (hits / shots if shots else 1)
                    rate = min(rate, per_clip / window)
                    up = 1.0 if eff.get("cap_includes_uptime") else up
                dmg = _val(it, eff, "damage", spirit, boons)
                if eff.get("damage_unit") == "pct_base_bullet":
                    dmg = dmg / 100 * weapon.get("base_bullet", 0.0)    # e.g. "20% of Base Bullet Damage"
                dps = dmg * rate * up
                o["spirit_extra"] += dps
            elif t == "periodic_spirit":
                cd = _val(it, eff, "cooldown", spirit, boons)
                if cd > 0:
                    dps = (_val(it, eff, "damage", spirit, boons) * float(eff.get("damage_mult", 1.0))
                           / cd * up)
                    o["spirit_extra"] += dps
            elif t == "max_hp_dot":
                pct = _val(it, eff, "pct", spirit, boons) / 100
                unit = eff.get("unit", "per_second")
                if unit == "per_tick":
                    tick = _val(it, eff, "tick", spirit, boons) or 1.0
                    pct /= tick
                elif unit == "total_per_application":
                    dur = _val(it, eff, "duration", spirit, boons) or 1.0
                    pct /= dur
                dps = pct * target_hp * up
                o["pct_hp"] += dps
            elif t == "headshot_bonus":
                rate = shots * headshot_rate
                cd = _val(it, eff, "cooldown", spirit, boons) if eff.get("cooldown_prop") else 0
                if cd > 0:
                    rate = min(rate, 1 / cd)
                dps = _val(it, eff, "damage", spirit, boons) * rate * up
                if eff.get("damage_type") == "spirit":
                    o["spirit_extra"] += dps
                else:
                    o["weapon_extra"] += dps
            elif t == "bullet_lifesteal":
                o["bullet_ls"] += _val(it, eff, "value", spirit, boons) / 100 * up
            elif t == "spirit_lifesteal":
                o["spirit_ls"] += _val(it, eff, "value", spirit, boons) / 100 * up
            elif t == "regen":
                v = _val(it, eff, "value", spirit, boons)
                if eff.get("unit") == "total_over_duration":
                    cd = _val(it, eff, "cooldown", spirit, boons) or _val(it, eff, "duration", spirit, boons) or 1.0
                    v /= cd
                if eff.get("in_combat", True):
                    o["regen"] += v * up
            elif t == "heal_on_event":
                v = _val(it, eff, "value", spirit, boons)
                if eff.get("unit") == "pct_max_hp":
                    v = v / 100 * own_hp
                rate = {"headshot": shots * headshot_rate, "hit": hits}.get(eff.get("event"), 0.0)
                cd = _val(it, eff, "cooldown", spirit, boons) if eff.get("cooldown_prop") else 0
                if cd > 0:
                    rate = min(rate, 1 / cd) if rate else 1 / cd
                o["heals"] += v * rate * up
            elif t == "weapon_multiplier":
                # e.g. Lucky Shot: chance% of bullets deal bonus% extra weapon damage
                chance = _val(it, eff, "chance", spirit, boons) / 100
                bonus = _val(it, eff, "bonus", spirit, boons) / 100
                o["weapon_mult"] *= 1 + chance * bonus * up
            elif t == "unmodelled":
                o["unmodelled"].append(f"{it.name}: {eff.get('reason', 'not modelled')}")
            if dps and eff.get("ignores_resist"):
                # move it out of the resisted buckets into true damage
                for bucket in ("spirit_extra", "pct_hp", "weapon_extra"):
                    if o[bucket] >= dps - 1e-9 and _bucket(t, eff) == bucket:
                        o[bucket] -= dps
                o["true_dps"] += dps
            if dps and eff.get("heals_self"):
                o["true_heal"] += dps
            if dps:
                o["per_item"][it.name] = o["per_item"].get(it.name, 0.0) + dps
    return o


def _bucket(t, eff):
    if t == "max_hp_dot":
        return "pct_hp"
    if t == "headshot_bonus" and eff.get("damage_type") != "spirit":
        return "weapon_extra"
    return "spirit_extra"


GUN_TYPES = {"on_hit_spirit", "headshot_bonus", "weapon_multiplier", "bullet_shred",
             "bullet_lifesteal", "max_hp_dot"}
GUN_STATS = {"MODIFIER_VALUE_WEAPON_DAMAGE_INCREASE", "MODIFIER_VALUE_FIRE_RATE",
             "MODIFIER_VALUE_AMMO_CLIP_SIZE_PERCENT", "MODIFIER_VALUE_BULLET_LIFESTEAL"}


def tunable(items, gun_only=False):
    """[(key, item, effect)] for effects with an uptime a player could set."""
    out = []
    for it in items:
        for idx, eff in enumerate(it.effects):
            if eff.get("type") == "unmodelled":
                continue
            gun = (eff.get("type") in GUN_TYPES or eff.get("stat") in GUN_STATS
                   or it.slot == "weapon")
            if gun_only and not gun:
                continue
            out.append((effect_key(it, idx), it, eff, gun))
    return out
