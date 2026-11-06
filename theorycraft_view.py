"""
theorycraft_view.py -- the Theorycraft mode.

Simulate: a hero's damage over a game for a given buy order and ability order,
with power spikes, objective damage on the objectives' resist clocks, and
sustain.
Recommend: a searched buy order under one of three scoring modes and three
laning modes (see theorycraft/optimize.py).

The model and its caveats live in theorycraft/ (see theorycraft/MECHANICS.md).
This only collects the inputs and draws the result.
"""

import altair as alt
import pandas as pd
import streamlit as st

from theorycraft import Target, catalog, simulate
from theorycraft.data import snapshot_age_days
from theorycraft.effects import tunable
from theorycraft.optimize import DEFAULT_DISTANCES, Weights, recommend
from theorycraft.simulate import RANK_BANDS

EXAMPLE_BUILD = ["Extended Magazine", "Headshot Booster", "Rapid Rounds", "Swift Striker"]
PARTS = {"total_dps": "Total", "weapon_dps": "Weapon", "ability_dps": "Abilities",
         "item_dps": "Item procs"}
OBJ = {"obj_Guardian": "Lane Guardian", "obj_Walker": "Walker", "obj_Mid-Boss": "Mid-Boss",
       "obj_Shrine": "Shrine", "obj_Patron": "Patron"}
ALL_OBJECTIVES = ("Guardian", "Walker", "Mid-Boss", "Shrine", "Patron")
EFFECT_LABEL = {
    "on_hit_spirit": "on-hit spirit damage", "headshot_bonus": "headshot bonus",
    "weapon_multiplier": "bonus-damage bullets", "bullet_shred": "bullet shred",
    "spirit_shred": "spirit shred", "bullet_lifesteal": "bullet lifesteal",
    "spirit_lifesteal": "spirit lifesteal", "max_hp_dot": "%-max-HP damage",
    "periodic_spirit": "proc on cooldown", "conditional_stat": "conditional stat",
    "regen": "regen", "heal_on_event": "heal on event",
}


@st.cache_resource(show_spinner="Reading hero and item data…")
def _catalog():
    # Parsed once per server process from the local heroes.json / items.json.
    return catalog()


def _item_label(item):
    return f"{item.name}  ·  {item.slot} T{item.tier}  ·  {item.cost:,}"


def _shop(cat):
    """Normal-mode shop items: enabled, tiers 1-4 (tier 5 is Street Brawl only)."""
    items = [i for i in cat.items.values() if not i.disabled and 1 <= i.tier <= 4]
    return sorted(items, key=lambda i: (i.tier, i.slot, i.name))


def _parse_order(text, hero):
    """'1 3 2 3' or '1,3,2,3' -> [1, 3, 2, 3]; anything else is an error."""
    tokens = text.replace(",", " ").split()
    bad = [t for t in tokens if not t.isdigit() or int(t) not in hero.abilities]
    if bad:
        raise ValueError(f"not an ability slot: {', '.join(bad)} "
                         f"(use 1–{max(hero.abilities)})")
    return [int(t) for t in tokens]


def _timeline(res):
    """One row per moment: several events can share a timestamp."""
    tl = pd.DataFrame(res.timeline).drop_duplicates("t", keep="last")
    tl["minute"] = tl["t"] / 60
    return tl


def _rules(res, data_key="spikes"):
    """Dashed spike lines and solid Rift lines to layer on a time chart."""
    layers = []
    if res.spikes:
        sp = pd.DataFrame(res.spikes)
        sp["minute"] = sp["t"] / 60
        layers.append(alt.Chart(sp).mark_rule(strokeDash=[4, 3], opacity=0.5)
                      .encode(x="minute:Q",
                              tooltip=[alt.Tooltip("label:N", title="Spike"),
                                       alt.Tooltip("minute:Q", format=".1f"),
                                       alt.Tooltip("pct:Q", title="DPS change %", format="+.0f")]))
    if res.rifts:
        rf = pd.DataFrame({"minute": [t / 60 for t in res.rifts], "what": "Rift (nominal)"})
        layers.append(alt.Chart(rf).mark_rule(color="#9b59b6", strokeWidth=2, opacity=0.7)
                      .encode(x="minute:Q", tooltip=["what:N", alt.Tooltip("minute:Q", format=".0f")]))
    return layers


def _lines(res, columns, y_title):
    tl = _timeline(res)
    long = tl.melt(id_vars=["minute", "souls", "boons"], value_vars=list(columns),
                   var_name="part", value_name="value")
    long["part"] = long["part"].map(columns)
    # Lines, not a stacked area: Vega draws stacked step-areas with spurious spikes.
    chart = (alt.Chart(long).mark_line(interpolate="step-after")
             .encode(x=alt.X("minute:Q", title="Minute"),
                     y=alt.Y("value:Q", title=y_title),
                     color=alt.Color("part:N", title=None, sort=list(columns.values())),
                     tooltip=[alt.Tooltip("minute:Q", format=".1f"), alt.Tooltip("part:N"),
                              alt.Tooltip("value:Q", format=".1f"),
                              alt.Tooltip("souls:Q", format=",.0f"), "boons:Q"]))
    return alt.layer(chart, *_rules(res))


# ------------------------------------------------------------------ shared inputs

def _target_inputs(prefix, distance_default=15, mix=False):
    with st.expander("Target, objectives and length"):
        c1, c2 = st.columns(2)
        if mix:
            distances = c1.pills("Fight distances (m)", [5, 10, 15, 20, 25, 30, 40],
                                 selection_mode="multi", default=[10, 20, 30], key=f"{prefix}_dist",
                                 help="Weapon damage is averaged over these, equally weighted. "
                                      "Range items switch on at exactly 15 m, so a single "
                                      "distance lets the search collect both close- and "
                                      "long-range bonuses at once.")
            distance = None
        else:
            distance = c1.slider("Distance (m)", 0, 60, distance_default, key=f"{prefix}_d")
            distances = None
        accuracy = c2.slider("Bullets that hit (%)", 5, 100, 100, key=f"{prefix}_acc",
                             help="Share of bullets that land. Scales gun damage and every "
                                  "on-hit proc. 100% is a target dummy; set it to your own.")
        headshots = c2.slider("Headshots (% of hits)", 0, 100, 0, key=f"{prefix}_hs",
                              help="Share of LANDED bullets that are headshots: drives crit "
                                   "damage (Holliday, Vindicta…) and headshot procs "
                                   "(Headhunter, Headshot Booster, Crippling Headshot). "
                                   "Lane Guardians take headshots on the brazier; Walkers "
                                   "do not.")
        bullet_res = c1.slider("Enemy bullet resist (%)", 0, 80, 0, key=f"{prefix}_br")
        spirit_res = c2.slider("Enemy spirit resist (%)", 0, 80, 0, key=f"{prefix}_sr")
        end_min = c1.slider("Game length (min)", 10, 50, 30, key=f"{prefix}_len")
        band = c2.selectbox("Souls curve: rank band", RANK_BANDS, index=3, key=f"{prefix}_band",
                            help="Median net worth over time for this hero at this rank, "
                                 "measured from the server's sweep matches.")
        shred_obj = c1.toggle("Shred applies to objectives", False, key=f"{prefix}_so",
                              help="Not documented. The Lane Guardian, Walker, Shrine and "
                                   "Patron pages give damage formulas with no shred term; "
                                   "only the Mid-Boss is documented to take resist "
                                   "reduction (at 50%).")
        abil_obj = c2.toggle("Abilities hit objectives", False, key=f"{prefix}_ao",
                             help="Many abilities do not damage structures; off counts the "
                                  "gun and item procs only.")
        crowd = c1.slider("Heroes attacking the objective", 1, 6, 1, key=f"{prefix}_crowd",
                          help="Your team's heroes near it. Walker: 3+ give it 20–50% "
                               "weapon resist. Shrine: 60% bullet resist, −20% per hero "
                               "(0 at 3+). Base Guardian: 40%, −20% per hero.")
    target = Target(distance_m=distance or 15, headshot_rate=headshots / 100,
                    accuracy=accuracy / 100,
                    bullet_resist=bullet_res / 100, spirit_resist=spirit_res / 100,
                    shred_on_objectives=shred_obj, abilities_on_objectives=abil_obj,
                    objective_enemies=crowd,
                    distance_mix=tuple((d, 1.0) for d in sorted(distances)) if distances else None)
    return target, end_min, band


def _uptime_editor(rows, key, title):
    """Editable uptimes for item effects -> Target.uptimes overrides."""
    if not rows:
        return ()
    df = pd.DataFrame([{
        "key": k, "item": it.name, "effect": EFFECT_LABEL.get(e["type"], e["type"]),
        "gun": gun, "model": float(e.get("uptime", 1.0)) * 100,
        "yours": float(e.get("uptime", 1.0)) * 100,
        "confidence": e.get("confidence", ""), "why": e.get("why", "")} for k, it, e, gun in rows])
    df = df.sort_values(["gun", "item"], ascending=[False, True])
    st.markdown(f"**{title}**")
    st.caption("Uptime = share of a fight the effect is working. The model's number is "
               "a reasoned guess (hover 'why'); put in your own, e.g. how often you "
               "actually land the headshot that procs Headhunter.")
    ed = st.data_editor(
        df, hide_index=True, key=key, disabled=["item", "effect", "gun", "model", "confidence", "why"],
        column_order=["item", "effect", "gun", "model", "yours", "confidence", "why"],
        column_config={
            "gun": st.column_config.CheckboxColumn("Gun", help="Depends on landing bullets"),
            "model": st.column_config.NumberColumn("Model %", format="%.0f"),
            "yours": st.column_config.NumberColumn("Your %", min_value=0, max_value=100, step=5,
                                                   format="%.0f"),
            "why": st.column_config.TextColumn("Why", width="large"),
        })
    return tuple((r.key, r.yours / 100) for r in ed.itertuples() if abs(r.yours - r.model) > 1e-9)


# ------------------------------------------------------------------ simulate

def _render_simulate(cat, hero, by_label):
    loaded = st.session_state.get("tc_loaded")
    with st.form("theorycraft"):
        names = loaded["items"] if loaded and loaded["hero"] == hero.name else EXAMPLE_BUILD
        labels = []
        for n in names:
            try:
                labels.append(_item_label(cat.item(n)))
            except KeyError:          # renamed or removed in a patch
                pass
        defaults = pd.DataFrame({"item": [x for x in labels if x in by_label]})
        defaults["minute"] = float("nan")
        st.markdown("**Buy order**")
        build = st.data_editor(
            defaults, num_rows="dynamic", hide_index=True,
            key=f"tc_build_{hero.name}_{hash(tuple(names))}",
            column_config={
                "item": st.column_config.SelectboxColumn(
                    "Item", options=list(by_label), required=True, width="large"),
                "minute": st.column_config.NumberColumn(
                    "Bought at (min)", min_value=0.0, step=0.5,
                    help="Optional. Blank buys it as soon as it is affordable."),
            })
        st.session_state.setdefault("tc_order", "1 3 2 3 3 4 3")   # the Recommend callback may set it
        order_text = st.text_input(
            "Ability order", key="tc_order",
            help="Slots in the order they are spent. The first mention of a slot "
                 "unlocks it; each later mention buys the next tier (1, 2, then 5 "
                 "points). The ultimate needs the fourth unlock, at 3,800 souls.")
        target, end_min, band = _target_inputs("sim")
        window = st.slider("Combo window (s)", 1.0, 10.0, 3.0, 0.5,
                           help="Gunfire counted in combo damage, on top of one "
                                "cast of every ability and item proc.")
        st.form_submit_button("Simulate", icon=":material/play_arrow:", type="primary")

    try:
        order = _parse_order(order_text, hero)
    except ValueError as e:
        st.error(str(e))
        return
    items = []
    for row in build.dropna(subset=["item"]).itertuples():
        name = by_label[row.item].name
        items.append((name, row.minute * 60) if pd.notna(row.minute) else name)

    bought = [cat.item(n if isinstance(n, str) else n[0]) for n in items]
    with st.expander("Item uptimes for this build", icon=":material/tune:"):
        over = _uptime_editor(tunable(bought), f"tc_up_{hash(tuple(i.name for i in bought))}",
                              "Effects in this build")
    if over:
        target = Target(**{**target.__dict__, "uptimes": over})
    res = simulate(hero.name, items=items, ability_order=order, end_s=end_min * 60,
                   target=target, combo_window_s=window, cat=cat, rank_band=band,
                   objectives=ALL_OBJECTIVES)
    _show_result(res, end_min, window)


def _show_result(res, end_min, window):
    for w in res.warnings:
        st.warning(w, icon=":material/schedule:")

    final = res.timeline[-1]
    with st.container(horizontal=True):
        st.metric(f"DPS at {end_min} min", f"{final['total_dps']:.0f}", border=True)
        st.metric("Weapon", f"{final['weapon_dps']:.0f}", border=True)
        st.metric("Abilities + items", f"{final['ability_dps'] + final['item_dps']:.0f}", border=True)
        st.metric("Combo damage", f"{final['combo_damage']:,.0f}", border=True)
        st.metric("Sustain (HP/s)", f"{final['sustain_hps']:.1f}", border=True)
    hit = {m["category"]: m["t"] / 60 for m in res.milestones}
    st.caption("4.8k investment reached — " + " · ".join(
        f"{k}: {hit[k]:.1f} min" if k in hit else f"{k}: not reached"
        for k in ("weapon", "vitality", "spirit"))
        + f".  Souls curve: {res.curve_source}.")

    st.subheader("Damage per second vs the enemy hero")
    st.caption("After their resists and your shred. Weapon includes reloads; abilities "
               "and procs on cooldown. Dashed: power spikes. Purple: Rift (nominal "
               "11:00, then every 7 min; ±1 min in game).")
    st.altair_chart(_lines(res, PARTS, "Sustained DPS"))

    st.subheader("Damage per second vs objectives")
    st.caption("On each objective's resist clock (wiki formulas): Lane Guardian 50% → −50% "
               "by 12:00, bullets ×1.25 vs players and 10% bullet resist, headshots land; "
               "Walker 65% → −65% by 18:00, 35% bullet resist, no headshots; Mid-Boss 15% "
               "bullet resist; Shrine 20% bullet resist plus 60% −20% per attacking hero; "
               "Patron 0%. Shrine and Patron only matter once they're exposed.")
    obj = {k: v for k, v in OBJ.items() if k in res.timeline[0]}
    st.altair_chart(_lines(res, obj, "DPS vs objective"))

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Combo damage")
        st.caption(f"One cast of every ability and item proc, plus {window:g}s of gunfire.")
        st.altair_chart(_lines(res, {"combo_damage": "Combo"}, "Damage"))
    with c2:
        st.subheader("Sustain")
        st.caption("HP/s healed while dealing the sustained damage: lifesteal, regen, heals.")
        st.altair_chart(_lines(res, {"sustain_hps": "Sustain"}, "HP per second"))

    st.subheader("Power spikes")
    if res.spikes:
        sp = pd.DataFrame(res.spikes)
        sp["minute"] = sp["t"] / 60
        sp["combo_pct"] = 100 * (sp["combo_after"] / sp["combo_before"] - 1)
        st.dataframe(
            sp[["minute", "kind", "label", "souls", "dps_before", "dps_after", "pct", "combo_pct"]],
            hide_index=True,
            column_config={
                "minute": st.column_config.NumberColumn("Min", format="%.1f"),
                "kind": "Kind", "label": st.column_config.TextColumn("What", width="large"),
                "souls": st.column_config.NumberColumn("Souls", format="%,.0f"),
                "dps_before": st.column_config.NumberColumn("DPS before", format="%.0f"),
                "dps_after": st.column_config.NumberColumn("DPS after", format="%.0f"),
                "pct": st.column_config.NumberColumn("DPS change", format="%+.0f%%"),
                "combo_pct": st.column_config.NumberColumn("Combo change", format="%+.0f%%"),
            })
    else:
        st.caption("No single event moved damage by 5% or more.")

    with st.expander("What this does not count", icon=":material/info:"):
        st.markdown(f"- Souls curve: {res.curve_source}. Medians from sampled games; "
                    "a fed or starved game shifts every spike.")
        for note in res.unmodelled:
            st.markdown(f"- {note}")
        age = snapshot_age_days()
        if age is not None:
            st.caption(f"Hero and item data is {age:.0f} days old "
                       "(heroes.json / items.json). Full list of modelled and "
                       "approximated mechanics: theorycraft/MECHANICS.md.")


# ------------------------------------------------------------------ recommend

@st.cache_data(ttl=3600, max_entries=50, show_spinner="Searching buy orders…")
def _recommend(hero, order, mode, lane, lane_items, enemies, weights, floor_mult, n_items,
               width, end_s, band, target):
    cat = _catalog()
    w = Weights(*weights)
    floor = None
    if mode == "floor":
        from theorycraft.optimize import Scorer, SoulsCurve, lane_mix, souls_curve_for
        h = cat.hero(hero)
        mix = lane_mix(cat, list(enemies)) if lane == "matchup" else 0.5
        sc = Scorer(cat, h, list(order), SoulsCurve(souls_curve_for(h, band)[0]), target,
                    end_s, mix)
        floor = sc.parts(list(lane_items) if lane == "locked" else [])["sustain"] * floor_mult
    beam, cands = {"Fast": (3, 8), "Thorough": (6, 14)}[width]
    return recommend(hero, list(order), mode=mode, lane=lane, lane_items=list(lane_items),
                     enemy_laners=list(enemies), weights=w, sustain_floor=floor,
                     n_items=n_items, beam=beam, candidates=cands, end_s=end_s,
                     rank_band=band, target=target, cat=cat)


def _render_recommend(cat, hero, by_label, heroes):
    st.caption("Searches buy orders and scores each across the whole game: damage vs "
               "the enemy hero (extra weight around each Rift), damage vs Lane "
               "Guardians (6–14 min) and Walkers (12–24 min) on their resist clocks, "
               "and lane durability (effective HP vs the lane's damage mix plus 30 s "
               "of healing). Items the simulator does not value are invisible to it.")
    with st.form("tc_recommend"):
        order_text = st.text_input("Ability order", "1 3 2 3 3 4 3", key="rc_order")
        c1, c2 = st.columns(2)
        mode_label = c1.segmented_control(
            "Scoring", ["Sustain floor", "Weighted", "Frontier"], default="Sustain floor",
            key="rc_mode",
            help="Floor: most damage that keeps lane durability above a floor. "
                 "Weighted: one score from your weights. Frontier: the builds that "
                 "trade damage for sustain without being beaten on both.")
        lane_label = c2.segmented_control(
            "Laning", ["I pick lane items", "From enemy laners", "Optimizer picks"],
            default="Optimizer picks", key="rc_lane")
        lane_items = st.multiselect("Lane items (bought first, in this order)", list(by_label),
                                    key="rc_lane_items",
                                    help="Used when Laning is 'I pick lane items'.")
        enemies = st.multiselect("Enemy laners", [h.name for h in heroes], max_selections=2,
                                 key="rc_enemies",
                                 help="Used when Laning is 'From enemy laners': their early "
                                      "bullet vs spirit burst sets the durability metric.")
        c1, c2, c3, c4 = st.columns(4)
        wd = c1.slider("Damage weight", 0.0, 2.0, 1.0, 0.1, key="rc_wd")
        wo = c2.slider("Objective weight", 0.0, 2.0, 0.5, 0.1, key="rc_wo")
        ws = c3.slider("Sustain weight", 0.0, 2.0, 0.5, 0.1, key="rc_ws",
                       help="Weighted mode only.")
        wr = c4.slider("Rift emphasis", 0.0, 3.0, 1.0, 0.25, key="rc_wr",
                       help="Extra weight on damage within 90 s of each Rift (1 = double).")
        c1, c2, c3 = st.columns(3)
        floor_mult = c1.slider("Sustain floor (× no-items)", 1.0, 2.0, 1.15, 0.05, key="rc_floor",
                               help="Floor mode: lane durability must reach this multiple "
                                    "of the hero's durability with no items (or with the "
                                    "locked lane items).")
        n_items = c2.slider("Items to plan", 4, 14, 10, key="rc_n")
        width = c3.segmented_control("Search", ["Fast", "Thorough"], default="Fast", key="rc_w",
                                     help="Fast: ~5–10 s (Frontier ~30 s, it runs five "
                                          "searches). Thorough: roughly 3× longer.")
        target, end_min, band = _target_inputs("rec", mix=True)
        with st.expander("Gun item uptimes", icon=":material/tune:"):
            from theorycraft.optimize import shop as _opt_shop
            over = _uptime_editor(tunable(_opt_shop(cat), gun_only=True), "rc_uptimes",
                                  "Every gun-dependent item effect the search can value")
        go = st.form_submit_button("Recommend", icon=":material/auto_awesome:", type="primary")
    if over:
        target = Target(**{**target.__dict__, "uptimes": over})

    if not go and "rc_last" not in st.session_state:
        return
    try:
        order = _parse_order(order_text, hero)
    except ValueError as e:
        st.error(str(e))
        return
    mode = {"Sustain floor": "floor", "Weighted": "weighted", "Frontier": "frontier"}[mode_label or "Sustain floor"]
    lane = {"I pick lane items": "locked", "From enemy laners": "matchup",
            "Optimizer picks": "auto"}[lane_label or "Optimizer picks"]
    if lane == "locked" and not lane_items:
        st.warning("Pick lane items, or switch Laning to another option.")
        return
    if lane == "matchup" and not enemies:
        st.warning("Name the enemy laner(s), or switch Laning to another option.")
        return
    if target.distance_mix is None:
        target = Target(**{**target.__dict__, "distance_mix": DEFAULT_DISTANCES})
    plans = _recommend(hero.name, tuple(order), mode, lane,
                       tuple(by_label[x].name for x in lane_items), tuple(enemies),
                       (wd, wo, ws, wr), floor_mult, n_items, width or "Fast",
                       end_min * 60, band, target)
    st.session_state["rc_last"] = True
    if not plans:
        st.info("No build found.")
        return

    if mode == "frontier" and len(plans) > 1:
        fr = pd.DataFrame([{"build": f"#{n + 1}", "damage": p.parts["damage"] + p.parts["objective"],
                            "sustain": p.parts["sustain"], "items": ", ".join(p.items)}
                           for n, p in enumerate(plans)])
        st.subheader("Damage vs sustain frontier")
        st.altair_chart(alt.Chart(fr).mark_line(point=True)
                        .encode(x=alt.X("sustain:Q", title="Lane durability (HP)",
                                        scale=alt.Scale(zero=False)),
                                y=alt.Y("damage:Q", title="Damage score (hero + objective DPS)",
                                        scale=alt.Scale(zero=False)),
                                tooltip=["build:N", alt.Tooltip("damage:Q", format=".0f"),
                                         alt.Tooltip("sustain:Q", format=".0f"), "items:N"]))
    for n, p in enumerate(plans):
        res = simulate(hero.name, items=p.items, ability_order=order, end_s=end_min * 60,
                       target=target, cat=cat, rank_band=band)
        buys = [e for e in res.events if e["kind"] == "item"]
        with st.container(border=True):
            st.markdown(f"**Build #{n + 1}**")
            with st.container(horizontal=True):
                st.metric("Avg DPS vs hero", f"{p.parts['damage']:.0f}")
                st.metric("Avg DPS vs objectives", f"{p.parts['objective']:.0f}")
                st.metric("Lane durability", f"{p.parts['sustain']:,.0f}")
            hit = {m["category"]: m["t"] / 60 for m in res.milestones}
            st.caption("4.8k reached — " + " · ".join(
                f"{k}: {hit[k]:.1f} min" if k in hit else f"{k}: —"
                for k in ("weapon", "vitality", "spirit")) + "  ·  "
                + " · ".join(x for x in p.notes if x))
            st.dataframe(pd.DataFrame([{"min": e["t"] / 60, "item": e["label"],
                                        "dps after": e["dps_after"], "change": e["pct"]}
                                       for e in buys]),
                         hide_index=True,
                         column_config={"min": st.column_config.NumberColumn("Min", format="%.1f"),
                                        "item": st.column_config.TextColumn("Buy", width="large"),
                                        "dps after": st.column_config.NumberColumn("DPS after", format="%.0f"),
                                        "change": st.column_config.NumberColumn("Change", format="%+.0f%%")})
            st.button("Open in Simulate", key=f"rc_open_{n}", icon=":material/open_in_new:",
                      on_click=_open_in_simulate, args=(hero.name, list(p.items), order))
    st.caption("Not exhaustive: a beam search with a one-step pre-rank. Items "
               "with no modelled effect only count for their investment bonus. "
               "Read 'What this does not count' in Simulate before trusting a build.")


def _open_in_simulate(hero, items, order):
    # A callback, so the view switch lands before the segmented control is drawn.
    st.session_state["tc_loaded"] = {"hero": hero, "items": items,
                                     "order": " ".join(map(str, order))}
    st.session_state["tc_order"] = " ".join(map(str, order))
    st.session_state["tc_view"] = "Simulate"


# ------------------------------------------------------------------ entry

def render() -> None:
    st.title("Theorycraft")
    cat = _catalog()
    heroes = sorted(cat.heroes.values(), key=lambda h: h.name)
    by_label = {_item_label(i): i for i in _shop(cat)}

    c1, c2 = st.columns([2, 1])
    # Outside the forms so the slot names follow the hero as soon as it changes.
    hero = c1.selectbox("Hero", heroes, format_func=lambda h: h.name, key="tc_hero",
                        index=next((n for n, h in enumerate(heroes) if h.name == "Haze"), 0))
    st.session_state.setdefault("tc_view", "Simulate")   # set here, not as a default:
    view = c2.segmented_control("View", ["Simulate", "Recommend"],  # a callback switches it
                                key="tc_view")
    st.caption("Ability slots: " + " · ".join(
        f"**{s}** {a.name}" for s, a in sorted(hero.abilities.items())))
    if view == "Recommend":
        _render_recommend(cat, hero, by_label, heroes)
    else:
        _render_simulate(cat, hero, by_label)
