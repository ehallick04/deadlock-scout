"""
app.py — the Deadlock scouting report as a web app.

Another runner on top of deadlock.py, exactly like main.py. No HTTP and no
game logic lives here; this file only collects input and displays results.

Run locally:
    uv pip install streamlit pandas
    streamlit run app.py

Deploy free: push this folder to GitHub, then connect it at share.streamlit.io
"""

import time

import pandas as pd
import streamlit as st

from api import cache_info, clear_cache, export_cache, import_cache
from deadlock import (
    ABILITY_STYLES, CUSTOMS_ONLY, DEFAULT_DAYS, DEFAULT_GAME_MODE,
    DEFAULT_MATCH_MODE, WITH_CUSTOMS, ability_order, ability_rows,
    ability_slots, build_report, build_summary, build_team_report, buy_order,
    buy_order_by_player, composition_counts, custom_match_ids, flatten,
    flow_edges, flow_rows, hero_names, hero_totals, item_flow,
    match_build_order, match_builds, match_builds_bulk, match_compositions,
    bulk_lineups, hero_combos, hero_matchups, lineup_teams,
    winner_offset,
    badge_label, drop_report, hero_swaps, hit_limit,
    metadata_report, strength_from_baselines,
    mirror_matches,
    parse_ids, phase_label, rank_choices,
    playable_hero_names,
    top_heroes_for,
    typical_builds,
)
from roster_import import BOOKMARKLET, HARVESTER, find_teams, parse_any
import store
import synergy
from teams import LEAGUE, PINNED, TEAMS, divisions, roster_many

st.set_page_config(page_title="Deadlock Scout", page_icon="🔒", layout="wide")

@st.cache_data(ttl=900, show_spinner=False)
def load(ids_tuple, days, top, match_mode, game_mode, labels_tuple):
    """Cached so re-sorting a table doesn't re-hit the API."""
    # labels_tuple is ((account_id, (("ign", ...), ("team", ...))), ...)
    # dict(v) turns the inner pairs back into a dict -- without it,
    # build_report gets tuples and .get() fails.
    labels = {k: dict(v) for k, v in labels_tuple}
    return build_report(list(ids_tuple), days=days, top=top,
                        match_mode=match_mode, game_mode=game_mode,
                        labels=labels), None


@st.cache_data(ttl=900, show_spinner=False)
def load_team(ids_tuple, days, top, min_players, labels_tuple, include_subs):
    """Only matches where several of the roster were in the same game."""
    labels = {k: dict(v) for k, v in labels_tuple}
    return build_team_report(list(ids_tuple), days=days, top=top,
                             min_players=min_players, labels=labels,
                             include_subs=include_subs)


def whole_number(text, fallback, label, minimum=1, maximum=3650):
    """Read a typed number, falling back with a warning instead of crashing."""
    text = (text or "").strip()
    if not text:
        return fallback
    if not text.isdigit() or not minimum <= int(text) <= maximum:
        st.sidebar.warning(f"{label}: enter a number {minimum}–{maximum}. "
                           f"Using {fallback}.")
        return fallback
    return int(text)


WINRATE_COL = st.column_config.ProgressColumn(
    "Win rate", format="%.1f%%", min_value=0, max_value=100)

@st.cache_data(ttl=900, show_spinner=False)
def load_comps(match_ids, roster_ids, labels_tuple, limit):
    labels = {k: dict(v) for k, v in labels_tuple}
    return match_compositions(list(match_ids), list(roster_ids), labels,
                              limit=limit)


@st.cache_data(ttl=3600, show_spinner=False)
def load_buy_order(ids_tuple, hero_id, days, match_mode, min_matches):
    """Pooled buy order for everyone selected. One request."""
    return buy_order(list(ids_tuple), hero_id=hero_id, days=days,
                     match_mode=match_mode, min_matches=min_matches)


@st.cache_data(ttl=3600, show_spinner=False)
def load_buy_order_bucketed(ids_tuple, hero_id, days, match_mode, min_matches,
                            bucket):
    """One request, rows split by bucket (hero) instead of pooled."""
    return buy_order(list(ids_tuple), hero_id=hero_id, days=days,
                     match_mode=match_mode, min_matches=min_matches,
                     bucket=bucket)


@st.cache_data(ttl=3600, show_spinner=False)
def load_typical_builds(ids_tuple, labels_tuple, hero_id, days, match_mode,
                        min_matches, core_share):
    """One request per player."""
    labels = {k: dict(v) for k, v in labels_tuple}
    return typical_builds(list(ids_tuple), hero_id, labels=labels, days=days,
                          match_mode=match_mode, min_matches=min_matches,
                          core_share=core_share)


@st.cache_data(ttl=6 * 3600, show_spinner="Running the synergy analysis...")
def load_synergy(days, badge, min_matches, method):
    """Ladder-wide pair synergy. Two requests, cached for six hours."""
    return synergy.report(days=days, badge=badge, min_matches=min_matches,
                          method=method)


@st.cache_data(ttl=3600, show_spinner=False)
def load_lineups(ids_tuple, labels_tuple, days, match_mode, limit):
    """Both lineups of every match in the window. One request."""
    labels = {k: dict(v) for k, v in labels_tuple}
    return bulk_lineups(list(ids_tuple), days=days, match_mode=match_mode,
                        limit=limit, labels=labels)


@st.cache_data(ttl=3600, show_spinner=False)
def load_top_heroes(account_id, days, match_mode):
    return top_heroes_for(account_id, days=days, match_mode=match_mode)


@st.cache_data(ttl=3600, show_spinner=False)
def load_buy_raw(ids_tuple, hero_id, days, match_mode, min_matches):
    from api import get_json
    import time as _t
    params = {"min_unix_timestamp": int(_t.time()) - days * 86400,
              "min_matches": min_matches, "match_mode": match_mode or None}
    if ids_tuple:
        params["account_ids"] = ",".join(str(a) for a in ids_tuple)
    if hero_id is not None:
        params["hero_id"] = hero_id
    return get_json("/v1/analytics/item-stats", **params)


@st.cache_data(ttl=3600, show_spinner=False)
def load_flow_raw(ids_tuple, hero_id, days, match_mode, min_matches):
    return item_flow(list(ids_tuple), hero_id=hero_id, days=days,
                     match_mode=match_mode, min_matches=min_matches)


@st.cache_data(ttl=3600, show_spinner=False)
def load_ability_raw(ids_tuple, hero_id, days, match_mode, min_matches):
    return ability_order(hero_id, list(ids_tuple), days=days,
                         match_mode=match_mode, min_matches=min_matches)


@st.cache_data(ttl=3600, show_spinner=False)
def load_buy_order_players(ids_tuple, labels_tuple, hero_id, days,
                           match_mode, min_matches):
    """One request per player, so it is behind a button."""
    labels = {k: dict(v) for k, v in labels_tuple}
    return buy_order_by_player(list(ids_tuple), labels, hero_id=hero_id,
                               days=days, match_mode=match_mode,
                               min_matches=min_matches)


@st.cache_data(ttl=3600, show_spinner=False)
def load_match_ids(ids_tuple, days, match_mode):
    """{account_id: set(match_id)} -- one batched call."""
    by_player = custom_match_ids(list(ids_tuple), days=days,
                                 match_mode=match_mode)
    return {k: sorted(v) for k, v in by_player.items()}


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def load_bulk_builds(ids_tuple, labels_tuple, days, match_mode, limit):
    """One request for many matches, rather than one request per match."""
    labels = {k: dict(v) for k, v in labels_tuple}
    return match_builds_bulk(list(ids_tuple), labels=labels, days=days,
                             match_mode=match_mode, limit=limit)


@st.cache_data(ttl=300, show_spinner=False)
def load_store_status():
    try:
        return store.status()
    except Exception as e:
        return {"error": str(e)}


def load_store_builds(ids_tuple, labels_tuple, days):
    """Straight from SQLite -- no request, no cache needed."""
    labels = {k: dict(v) for k, v in labels_tuple}
    return store.purchases(list(ids_tuple), days=days, labels=labels)


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def load_match_builds(match_ids_tuple, ids_tuple, labels_tuple, limit):
    """Purchases read out of match metadata. Finished matches never change."""
    labels = {k: dict(v) for k, v in labels_tuple}
    return match_builds(list(match_ids_tuple), account_ids=list(ids_tuple),
                        labels=labels, limit=limit)


@st.cache_data(ttl=7 * 24 * 3600, show_spinner=False)
def load_ability_slots(hero_id):
    """{ability_id: 1-4} from the cached hero + item assets."""
    try:
        return ability_slots(hero_id)
    except Exception:
        return {}


@st.cache_data(ttl=7 * 24 * 3600, show_spinner=False)
def load_hero_names():
    """
    Playable heroes only -- the assets also carry in-development and
    disabled ones, which have no match data and only clutter a picker.
    """
    try:
        return playable_hero_names()
    except Exception:
        return {}


# ---- picks and swaps across the ladder, no roster involved
def render_picks(lineups, min_games=10, days=None, badge=None):
    """
    What appears opposite what, which heroes get taken then dropped, and
    who swaps in the pre-game window.

    All three are ladder questions rather than roster ones, so they read a
    sample drawn by rank instead of by account list.

    days/badge, when given, pull hero win rates from the full statistics for
    that same rank rather than deriving them from this sample -- the drop
    classification turns on whether a hero is above or below average, and
    the sample alone is thin once split across every hero.
    """
    st.divider()
    st.subheader("What shows up opposite")
    st.caption("Deadlock has no draft, so picks are blind and raw "
               "co-occurrence is mostly arithmetic: a hero on 30% of "
               "sides lands opposite anything about 30% of the time. "
               "**Excess** is the part above what pick rates alone "
               "predict — that is the only column carrying information.")
    mframe = pd.DataFrame(hero_matchups(lineups, min_games=min_games))
    if mframe.empty:
        st.info("Not enough games yet for a matchup to clear "
                f"{min_games}.")
    else:
        heroes_seen = sorted(mframe["hero"].unique())
        focus = st.selectbox("Hero", ["All"] + heroes_seen,
                             key="matchup_hero")
        only_real = st.checkbox("Above chance only", value=True,
                                key="mu_sig")
        view = mframe if focus == "All" else mframe[mframe["hero"] == focus]
        if only_real and "significant" in view.columns:
            view = view[view["significant"]]
        if view.empty:
            st.info("Nothing clears chance here — which is the expected "
                    "result in a game without a draft. Untick to see the "
                    "raw co-occurrence anyway.")
        else:
            st.dataframe(
                view[["hero", "answer", "games", "answer_rate",
                      "expected_rate", "excess", "win_rate", "q"]],
                hide_index=True, use_container_width=True,
                column_config={
                    "hero": "Picked",
                    "answer": "Faced",
                    "games": st.column_config.NumberColumn("Together"),
                    "answer_rate": st.column_config.NumberColumn(
                        "Observed", format="%.1f%%"),
                    "expected_rate": st.column_config.NumberColumn(
                        "By chance", format="%.1f%%"),
                    "excess": st.column_config.NumberColumn(
                        "Excess", format="%+.1f%%"),
                    "win_rate": st.column_config.NumberColumn(
                        "WR into it", format="%.1f%%"),
                    "q": st.column_config.NumberColumn("q", format="%.4f"),
                })

    st.divider()
    st.subheader("Refused assignments")
    st.caption("The starting hero is **assigned**, not picked — the "
               "matchmaker draws it from the priority list submitted on the "
               "roster screen. So a drop usually just means somebody was "
               "handed a hero further down their own list. Read this as "
               "**revealed preference**: which heroes do people refuse once "
               "they are given them.")
    st.caption("A denial is the rare case, not the default. You cannot "
               "choose to lock the hero you want to deny — you have to be "
               "assigned it, and it costs your only swap — so a drop reads "
               "as a possible soft ban only when the hero is strong enough "
               "to be worth giving up, and it stayed out of the game "
               "afterwards. Enemies never see the swap; teammates do.")
    strength = None
    strength_note = "win rates derived from this sample"
    if days is not None:
        try:
            base, _ = load_hero_win_rates(days, badge or 0)
            strength = strength_from_baselines(base, load_all_hero_names())
            if strength and strength.get("_mean", {}).get("win_rate"):
                strength_note = (f"win rates from full stats at "
                                 f"{badge_label(badge) if badge else 'any rank'}"
                                 f", last {days} days")
            else:
                strength = None
        except Exception:
            strength = None
    sb_rows, sb_summary, sb_triggers = drop_report(lineups, min_locks=10,
                                                   strength=strength)
    if sb_summary["matches_skipped_not_swappable"]:
        st.caption(f"{sb_summary['matches_skipped_not_swappable']} matches "
                   "skipped — the face-off phase is ranked only.")
    if not sb_rows:
        st.info("Not enough pre-game data yet. It comes from demo analysis, "
                "so it is present for some matches and not others.")
    else:
        m1, m2, m3 = st.columns(3)
        m1.metric("Assignments dropped", sb_summary["drops"])
        m2.metric("Denial held", sb_summary["denials_held"],
                  help="Dropped hero ended up played by nobody.")
        m3.metric("Taken by enemy", sb_summary["picked_up_by_enemy"],
                  help="The enemy cannot see your swap, so this should be "
                       "rare. A high number means denials are not sticking.")
        st.caption(f"Baseline: {sb_summary['overall_away_rate']}% of "
                   f"assignments are dropped, across "
                   f"{sb_summary['players_with_pregame']} of them. "
                   f"{sb_summary['picked_up_by_teammate']} were picked up by "
                   f"a teammate, who can see the swap. Hero {strength_note}; "
                   f"average {sb_summary['average_win_rate']}%.")
        if sb_summary["duplicate_hero_matches"]:
            st.warning(f"{sb_summary['duplicate_hero_matches']} matches show "
                       "the same hero twice, which the rules do not allow — "
                       "treat those rows with suspicion.")

        sbf = pd.DataFrame(sb_rows)
        st.dataframe(
            sbf[["hero", "locked", "swapped_away", "away_rate", "excess",
                 "hero_win_rate", "hero_games", "quadrant", "stuck_rate",
                 "destinations", "reads_as"]],
            hide_index=True, use_container_width=True,
            column_config={
                "hero": "Locked hero",
                "locked": st.column_config.NumberColumn("Locks"),
                "swapped_away": st.column_config.NumberColumn("Dropped"),
                "away_rate": st.column_config.ProgressColumn(
                    "Drop rate", format="%.1f%%", min_value=0, max_value=100),
                "excess": st.column_config.NumberColumn(
                    "vs baseline", format="%+.1f%%"),
                "hero_win_rate": st.column_config.NumberColumn(
                    "Hero WR", format="%.1f%%",
                    help="Below average means dropping it is avoidance, not "
                         "a ban. Blank means too few games at this rank to "
                         "have a rate — widen the window or lower the rank."),
                "hero_games": st.column_config.NumberColumn(
                    "WR games", help="Games behind that win rate."),
                "stuck_rate": st.column_config.NumberColumn(
                    "Stayed unplayed", format="%.0f%%"),
                "destinations": st.column_config.NumberColumn("Dests"),
                "quadrant": st.column_config.TextColumn(
                    "Wanted vs strong",
                    help="Drop rate against win rate. 'Strong but refused' "
                         "is the interesting corner."),
                "reads_as": "Reads as",
            })
        unknown = [r["hero"] for r in sb_rows
                   if r.get("hero_win_rate") is None]
        if unknown:
            st.caption("No win rate at this rank for: " + ", ".join(unknown[:8])
                       + ". Too few games to measure, so they cannot be "
                         "sorted into weak or strong — widen the window or "
                         "lower the rank floor.")

        if sb_summary.get("most_refused"):
            st.info("Most refused when assigned: "
                    + ", ".join(sb_summary["most_refused"])
                    + " — the clearest read here, since it needs no "
                      "assumption about intent.")
        if sb_summary["soft_bans"]:
            st.warning("Possibly denied on purpose: "
                       + ", ".join(sb_summary["soft_bans"])
                       + " — strong enough to be worth keeping, given up "
                         "anyway, and the hero stayed out of the game. Treat "
                         "as a hypothesis, not a finding.")
        if sb_summary.get("avoided_weak"):
            st.caption("Dropped often but below the "
                       f"{sb_summary['average_win_rate']}% average win rate, "
                       "so read as avoided rather than banned: "
                       + ", ".join(sb_summary["avoided_weak"]) + ".")

        real = [t for t in sb_triggers if t["significant"] and t["excess"] > 0]
        with st.expander(f"Counter-swaps — {len(real)} triggered drops"):
            st.caption("Conditioned on the enemy's **loaded-in** heroes, "
                       "since their swaps are hidden. A drop that only "
                       "happens against one hero is a reaction, not a ban. "
                       "Heroes are unique match-wide, so comps correlate for "
                       "reasons unrelated to countering — a trigger only "
                       "counts here when the drop rate falls back to normal "
                       "without it.")
            if real:
                tf = pd.DataFrame(real)
                st.dataframe(
                    tf[["hero", "trigger", "rate_seen", "rate_unseen",
                        "excess", "locks_when_seen", "q"]],
                    hide_index=True, use_container_width=True,
                    column_config={
                        "hero": "Dropped", "trigger": "When facing",
                        "rate_seen": st.column_config.NumberColumn(
                            "Drop rate", format="%.1f%%"),
                        "rate_unseen": st.column_config.NumberColumn(
                            "Otherwise", format="%.1f%%"),
                        "excess": st.column_config.NumberColumn(
                            "Difference", format="%+.1f%%"),
                        "locks_when_seen": st.column_config.NumberColumn(
                            "Locks"),
                        "q": st.column_config.NumberColumn("q", format="%.4f"),
                    })
            else:
                st.info("No drop is explained by a specific enemy hero here.")

    st.divider()
    st.subheader("Pre-game swaps")
    st.caption("The one reactive mechanic: `pregame_hero_id` is what a "
               "player locked before the swap window, so a difference "
               "from their final hero is a genuine response to the lobby.")
    swap_rows, swap_summary = hero_swaps(lineups, min_games=2)
    if not swap_summary["with_pregame_data"]:
        st.info("No pre-game hero data in these matches — it comes from "
                "demo analysis and is not present for every game.")
    elif not swap_rows:
        st.info(f"{swap_summary['swaps']} swaps seen, none repeated often "
                "enough to list.")
    else:
        st.caption(f"{swap_summary['swap_rate']}% of players swapped "
                   f"({swap_summary['swaps']} of "
                   f"{swap_summary['with_pregame_data']} with data).")
        st.dataframe(
            pd.DataFrame(swap_rows), hide_index=True,
            use_container_width=True,
            column_config={
                "from": "Locked", "to": "Switched to",
                "count": st.column_config.NumberColumn("Times"),
                "share": st.column_config.NumberColumn(
                    "Of swaps off that hero", format="%.1f%%"),
            })
        st.download_button("Download matchups (CSV)",
                           mframe.to_csv(index=False).encode(),
                           file_name="matchups.csv", mime="text/csv")


# ---- build order: what gets bought, when, and in what sequence


# ---- which heroes help each other, across the whole ladder
def render_synergy():
    """Ladder-wide, so it needs no roster and no report."""
    st.caption("Not your roster — the ladder at large. Pair win rates come "
               "from the API; what is added here is the comparison against "
               "what each pair *should* win given the two solo rates, and a "
               "test of whether the gap is real.")

    choices = load_rank_choices()
    labels_only = ["Any rank"] + [label for label, _ in choices]
    default_label = badge_label(synergy.TOP_BADGE)
    start = (labels_only.index(default_label)
             if default_label in labels_only else 0)

    sy1, sy2 = st.columns([1, 1])
    sy_days = whole_number(sy1.text_input("Days", value="30"),
                           30, "Days", minimum=1, maximum=365)
    picked_rank = sy2.selectbox("Minimum rank", labels_only, index=start,
                                help="The average rank of BOTH teams in a "
                                     "match must be at least this.")
    sy_badge = dict(choices).get(picked_rank, 0)

    sy3, sy4 = st.columns([1, 1])
    sy_min = whole_number(sy3.text_input("Min games per pair", value="50"),
                          50, "Min games", minimum=5, maximum=100000)
    model = sy4.selectbox("Expected from", ["delta", "average"],
                          help="delta: each hero's edge over 50% adds. "
                               "average: the mean of the two solo rates.")

    st.caption("Every step up the ladder shrinks the sample, and synergy "
               "needs games before it can say anything. If little comes back "
               "significant, lower the rank or the per-pair minimum before "
               "concluding there is nothing there.")

    if st.button("Run synergy analysis"):
        st.session_state.synergy_go = True

    if st.session_state.get("synergy_go"):
        try:
            pairs, heroes, context = load_synergy(sy_days, sy_badge, sy_min,
                                                  model)
        except Exception as e:
            pairs, heroes, context = [], [], {}
            st.error(f"Could not run the analysis: {e}")

        if not pairs:
            st.info("No pairs cleared the filters. Lower **Min games per "
                    "pair**, widen the window, or drop the badge floor.")
        else:
            c1, c2, c3, c4 = st.columns(4)
            st.caption(f"{context['min_rank']} and above · last "
                       f"{context['days']} days · "
                       f"{context.get('matches_in_pairs', 0):,} games behind "
                       "the pairs below")
            c1.metric("Heroes", context["heroes"])
            c2.metric("Pairs tested", context["pairs_tested"])
            c3.metric("Significant", context["significant_pairs"])
            c4.metric("Meta defining", len(context["meta_defining"]))

            skipped = [
                ("not in the game", context.get("excluded_not_in_game")),
                ("no games in this window", context.get("excluded_no_data")),
                (f"under {context.get('min_hero_matches')} games",
                 context.get("excluded_too_few_games")),
            ]
            if any(names for _, names in skipped):
                with st.expander("Heroes left out, and why"):
                    st.caption("Assets include heroes that are not in the "
                               "game yet, and a hero with a thin sample "
                               "makes an unstable baseline — which would "
                               "invent synergy rather than find it.")
                    for label, names in skipped:
                        if names:
                            st.markdown(f"**{label}** — {', '.join(names)}")
            st.caption(f"Significance is after Benjamini-Hochberg correction "
                       f"at α={context['alpha']}. Testing "
                       f"{context['pairs_tested']} pairs at once would throw "
                       f"about {int(context['pairs_tested'] * context['alpha'])} "
                       "false positives uncorrected, so the raw p-value is "
                       "not enough on its own.")

            st.subheader("Meta defining")
            st.caption("Heroes lifting **many** partners, not one lucky duo: "
                       "the average lift across all their pairings is itself "
                       "significant, and enough individual pairings clear the "
                       "bar.")
            hframe = pd.DataFrame(heroes)
            flagged = hframe[hframe["meta_defining"]] if not hframe.empty \
                else hframe
            if flagged.empty:
                st.info("Nothing clears the bar in this window — which is a "
                        "real answer, not a failure. A balanced patch should "
                        "look like this.")
            else:
                st.dataframe(
                    flagged[["hero", "avg_lift", "significant_up",
                             "significant_down", "pairs", "games"]],
                    hide_index=True, use_container_width=True,
                    column_config={
                        "hero": "Hero",
                        "avg_lift": st.column_config.NumberColumn(
                            "Avg lift", format="%+.2f%%"),
                        "significant_up": st.column_config.NumberColumn(
                            "Partners lifted"),
                        "significant_down": st.column_config.NumberColumn(
                            "Partners hurt"),
                        "pairs": st.column_config.NumberColumn("Pairings"),
                        "games": st.column_config.NumberColumn("Games"),
                    })

            with st.expander("Every hero, ranked by average lift"):
                st.dataframe(
                    hframe[["hero", "avg_lift", "significant_up",
                            "significant_down", "pairs", "games", "q"]],
                    hide_index=True, use_container_width=True)

            st.divider()
            st.subheader("Pairs")
            only_sig = st.checkbox("Significant only", value=True)
            pframe = pd.DataFrame(pairs)
            heroes_seen = sorted(set(pframe["hero_a"]) | set(pframe["hero_b"]))
            focus = st.selectbox("Filter to a hero", ["All"] + heroes_seen,
                                 key="syn_hero")
            view = pframe[pframe["significant"]] if only_sig else pframe
            if focus != "All":
                view = view[(view["hero_a"] == focus)
                            | (view["hero_b"] == focus)]
            view = view.copy()
            view["pair"] = view["hero_a"] + " + " + view["hero_b"]
            st.dataframe(
                view[["pair", "matches", "win_rate", "expected", "lift",
                      "low", "high", "q"]],
                hide_index=True, use_container_width=True,
                column_config={
                    "pair": "Pair",
                    "matches": st.column_config.NumberColumn("Games"),
                    "win_rate": st.column_config.NumberColumn(
                        "Actual", format="%.1f%%"),
                    "expected": st.column_config.NumberColumn(
                        "Expected", format="%.1f%%"),
                    "lift": st.column_config.NumberColumn(
                        "Lift", format="%+.1f%%"),
                    "low": st.column_config.NumberColumn(
                        "95% low", format="%.1f%%"),
                    "high": st.column_config.NumberColumn(
                        "95% high", format="%.1f%%"),
                    "q": st.column_config.NumberColumn("q", format="%.4f"),
                })
            st.download_button("Download pairs (CSV)",
                               pframe.to_csv(index=False).encode(),
                               file_name="synergy_pairs.csv", mime="text/csv")


    # ---- everything, downloadable


@st.cache_data(ttl=6 * 3600, show_spinner="Reading ladder matches...")
def load_ladder_lineups(days, badge, limit, match_mode):
    """Lineups from the ladder at a rank floor -- no account filter."""
    return bulk_lineups((), days=days, match_mode=match_mode, limit=limit,
                        min_average_badge=badge or None)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def load_hero_baselines(days, badge):
    """
    Ladder-wide solo win rates for the SYNERGY analysis, which drops any
    hero under 500 games -- a thin baseline there corrupts every pair it
    touches. Returns (baselines, dropped).
    """
    return synergy.hero_baselines(days=days, badge=badge)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def load_hero_win_rates(days, badge, floor=30):
    """
    Win rates for the drop analysis, with a much lower floor.

    Here a thin hero is the interesting case, not a hazard: an unpopular,
    weak hero is precisely the one that gets refused. Excluding it at 500
    games leaves its win rate blank and the classification unable to tell
    "bad hero nobody wants" from "denied on purpose".
    """
    base, dropped = synergy.hero_baselines(days=days, badge=badge,
                                           min_hero_matches=floor)
    return base, dropped


@st.cache_data(ttl=7 * 24 * 3600, show_spinner=False)
def load_rank_choices():
    """[(label, badge)] for the rank picker."""
    try:
        return rank_choices()
    except Exception:
        return []


@st.cache_data(ttl=7 * 24 * 3600, show_spinner=False)
def load_all_hero_names():
    """Every hero, playable or not -- for labelling, not for pickers."""
    try:
        return hero_names()
    except Exception:
        return {}


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def load_meta_buy_order(days, hero_id, min_matches):
    """What the whole ladder buys -- no account filter."""
    return buy_order((), hero_id=hero_id, days=days, match_mode=None,
                     min_matches=min_matches)


# --------------------------------------------------------------- sidebar

# Three questions, three shapes of answer:
#   Meta     the ladder at large -- no roster, no report needed
#   Players  whoever you name: rosters, pasted ids, imported pages
#   Pros     the pinned teams, custom games only
MODES = ("Meta", "Players", "Pros")
mode = st.sidebar.radio(
    "Looking at", MODES, index=1,
    help="Meta reads the whole ladder and needs nothing selected. "
         "Players scouts whoever you pick. Pros is the pinned rosters in "
         "their custom games.")
st.sidebar.divider()

# ---- Meta: ladder-wide, so it short-circuits everything roster-shaped
if mode == "Meta":
    st.title("Deadlock Meta")
    st.caption("The ladder at large — no roster required.")
    meta_synergy, meta_picks, meta_heroes, meta_builds = st.tabs(
        ["Synergy", "Picks & swaps", "Hero win rates", "Item meta"])

    with meta_synergy:
        render_synergy()

    with meta_picks:
        st.caption("Blind picks, soft bans, and the pre-game swap window — "
                   "read from a sample of ladder matches, not from any "
                   "roster.")
        p1, p2, p3, p4 = st.columns(4)
        p_days = whole_number(p1.text_input("Days", value="14", key="pk_days"),
                              14, "Days", minimum=1, maximum=365)
        rank_opts = load_rank_choices()
        rank_labels = ["Any rank"] + [lbl for lbl, _ in rank_opts]
        p_rank = p2.selectbox("Minimum rank", rank_labels, key="pk_rank")
        p_badge = dict(rank_opts).get(p_rank, 0)
        p_limit = whole_number(
            p3.text_input("Matches to read", value="2000", key="pk_limit"),
            2000, "Matches", minimum=50, maximum=10000)
        p_min = whole_number(
            p4.text_input("Min games", value="20", key="pk_min"),
            20, "Min games", minimum=2, maximum=100000)

        p_mode = st.radio("Match mode", ["ranked", "unranked", "both"],
                          horizontal=True, key="pk_mode",
                          help="Swaps and soft bans are ranked-only — there "
                               "is no swap window in unranked.")
        st.caption("Reading a few thousand matches takes a moment the first "
                   "time; it is cached for six hours after that.")

        if st.button("Read ladder matches", key="pk_go"):
            st.session_state.picks_go = True

        if st.session_state.get("picks_go"):
            wanted = None if p_mode == "both" else p_mode
            try:
                ladder = load_ladder_lineups(p_days, p_badge, p_limit, wanted)
            except Exception as e:
                ladder = []
                st.error(f"Could not read ladder matches: {e}")
            if not ladder:
                st.info("Nothing came back. Widen the window, lower the rank, "
                        "or raise the match limit.")
            else:
                if len(ladder) >= p_limit:
                    st.warning(f"Hit the {p_limit}-match limit — newest "
                               "first, so older games were left out.")
                st.caption(f"{len(ladder):,} matches · {p_rank} · last "
                           f"{p_days} days")
                render_picks(ladder, min_games=p_min,
                             days=p_days, badge=p_badge)

    with meta_heroes:
        st.caption("Every playable hero's win rate at this skill level.")
        h1, h2 = st.columns(2)
        m_days = whole_number(h1.text_input("Days", value="30", key="mh_days"),
                              30, "Days", minimum=1, maximum=365)
        m_badge = whole_number(
            h2.text_input("Min avg badge", value="0", key="mh_badge"),
            0, "Badge", minimum=0, maximum=120)
        try:
            base, dropped = load_hero_baselines(m_days, m_badge)
        except Exception as e:
            base, dropped = {}, {}
            st.error(f"Could not load hero stats: {e}")
        if base:
            names_all = load_all_hero_names()
            hrows = [{"hero": names_all.get(k, str(k)), "matches": v["matches"],
                      "wins": v["wins"], "win_rate": v["win_rate"]}
                     for k, v in base.items()]
            hrows.sort(key=lambda r: -r["win_rate"])
            st.dataframe(pd.DataFrame(hrows), hide_index=True,
                         use_container_width=True,
                         column_config={"hero": "Hero",
                                        "matches": st.column_config.NumberColumn("Games"),
                                        "wins": st.column_config.NumberColumn("Wins"),
                                        "win_rate": WINRATE_COL})
            left_out = (dropped.get("not_in_game") or []) + \
                       [h for h, _ in (dropped.get("too_few_games") or [])]
            if left_out:
                st.caption(f"{len(left_out)} heroes left out: not in the game, "
                           "or too few games to be meaningful.")

    with meta_builds:
        st.caption("What the ladder buys, in the order it buys it — no "
                   "account filter, so this is everyone.")
        b1, b2, b3 = st.columns(3)
        b_days = whole_number(b1.text_input("Days", value="30", key="mb_days"),
                              30, "Days", minimum=1, maximum=365)
        b_min = whole_number(
            b2.text_input("Min games per item", value="100", key="mb_min"),
            100, "Min games", minimum=1, maximum=1000000)
        names_all = load_all_hero_names()
        b_hero = b3.selectbox("Hero", ["All heroes"]
                              + sorted(load_hero_names().values()),
                              key="mb_hero")
        b_hero_id = next((k for k, v in load_hero_names().items()
                          if v == b_hero), None)
        try:
            meta_rows = load_meta_buy_order(b_days, b_hero_id, b_min)
        except Exception as e:
            meta_rows = []
            st.error(f"Could not load item stats: {e}")
        if not meta_rows:
            st.info("Nothing came back at these filters.")
        else:
            mframe = pd.DataFrame(meta_rows)
            st.dataframe(
                mframe[["buy_time", "item", "buys", "win_rate"]].head(60),
                hide_index=True, use_container_width=True,
                column_config={
                    "buy_time": st.column_config.TextColumn("Bought at",
                                                            width="small"),
                    "item": "Item",
                    "buys": st.column_config.NumberColumn("Buys"),
                    "win_rate": WINRATE_COL})
            st.download_button("Download item meta (CSV)",
                               mframe.to_csv(index=False).encode(),
                               file_name="item_meta.csv", mime="text/csv")

    st.stop()

st.sidebar.header("Who")

# A checklist rather than a dropdown. Rules:
#   - Custom (pasted ids) can be combined with anything.
#   - Ticking individual teams hides the All / NA / EU shortcuts, since
#     those would only overlap with what you already picked.
ALL_TEAMS = sorted(PINNED, key=str.lower)
pros_only = (mode == "Pros")

if pros_only:
    st.sidebar.caption("Pros mode: the pinned rosters, custom games only. "
                       "Switch to Players to scout anyone else.")

use_custom = (False if pros_only
              else st.sidebar.checkbox("Custom — paste IDs below",
                                       value=False))

st.sidebar.markdown("**Teams**")
picked_teams = [name for name in ALL_TEAMS
                if st.sidebar.checkbox(
                    f"{name}  ·  {TEAMS[name].get('region', '')}",
                    key=f"team_{name}")]

# Everything from rosters.json. 180 checkboxes would be unusable, so these
# get a searchable multiselect instead -- ticked ones join picked_teams and
# behave identically from here on.
league_picks = []
if LEAGUE and not pros_only:
    st.sidebar.markdown(f"**League teams**  ·  {len(LEAGUE)} from rosters.json")
    div = st.sidebar.selectbox("Division", ["Any"] + divisions(), key="lg_div")
    pool = sorted(LEAGUE, key=str.lower)
    if div != "Any":
        pool = [n for n in pool if TEAMS[n].get("division") == div]
    league_picks = st.sidebar.multiselect(
        "Pick teams (type to search)", pool, key="lg_teams",
        help="Rosters come from the DSE player portal, built by build_rosters.py.")
    if div != "Any" and st.sidebar.checkbox(f"Whole division ({len(pool)} teams)",
                                            key="lg_whole"):
        league_picks = pool
elif not pros_only:
    st.sidebar.caption("No rosters.json yet — run build_rosters.py to add "
                       "every league team.")

picked_teams = picked_teams + [n for n in league_picks if n not in picked_teams]

shortcuts = []
if not picked_teams:
    st.sidebar.markdown("**Or a group**")
    cols = st.sidebar.columns(3)
    if cols[0].checkbox("All", key="grp_all"):
        shortcuts.append("All (pinned)")
    if cols[1].checkbox("NA", key="grp_na"):
        shortcuts.append("NA")
    if cols[2].checkbox("EU", key="grp_eu"):
        shortcuts.append("EU")
else:
    st.sidebar.caption("Group shortcuts hidden while individual teams are ticked.")

selections = picked_teams or shortcuts

if len(selections) > 8:
    st.sidebar.warning(f"{len(selections)} teams selected — that is a lot of "
                       "API calls on the first build. Cached after that.")
ids, labels = ([], {})
if selections:
    ids, labels = roster_many(selections)

preset = ", ".join(selections) if selections else ""

# ---- pasted / typed players, merged on top
raw = ""
if use_custom:
    raw = st.sidebar.text_area(
        "Account IDs, friend codes, or statlocker URLs",
        height=110,
        placeholder="880934744\nhttps://statlocker.gg/profile/1170456491/matches",
        help="One per line. Steam friend codes work directly.",
    )

    st.sidebar.markdown("**Import a team page**")
    pasted = st.sidebar.text_area(
        "Paste a team page here",
        height=90,
        placeholder="Open the team page, Ctrl+A then Ctrl+C, and paste here",
        help="Works with a plain Ctrl+A/Ctrl+C paste, a saved page, or the "
             "all-teams directory. Nothing is fetched — it only reads what "
             "you paste.",
    )
    uploaded = st.sidebar.file_uploader(
        "...or upload a list / saved page", type=["txt", "csv", "html", "htm"])

    with st.sidebar.expander("If a page will not copy"):
        st.caption("Make a bookmark with this as its URL, open the page, "
                   "click it, then paste above. Only needed if a plain "
                   "Ctrl+A/Ctrl+C comes out without links.")
        st.code(BOOKMARKLET, language="javascript")

    with st.sidebar.expander("Bake in every league team"):
        st.caption("One team at a time is slow. Make a bookmark with this as "
                   "its URL, open the team directory, click it once, and it "
                   "walks every team page in your own session and downloads "
                   "dse_rosters.html. Then run, in the project folder:")
        st.code("python build_rosters.py dse_rosters.html", language="bash")
        st.caption("Commit the rosters.json it writes and every teammate — "
                   "and this app — gets the rosters with no importing.")
        st.code(HARVESTER, language="javascript")

    page_text = pasted or ""
    if uploaded is not None:
        text = uploaded.getvalue().decode("utf-8", errors="replace")
        if uploaded.name.lower().endswith((".html", ".htm")) or "<a " in text.lower():
            page_text = text
        else:
            raw = (raw or "") + "\n" + text

    if page_text.strip():
        result = parse_any(page_text)
        if result["kind"] == "directory":
            st.sidebar.info(f"That is the team directory ({len(result['teams'])} "
                            f"teams). Open a team and paste its page instead.")
            st.session_state.directory = result["teams"]
        elif result["players"]:
            st.sidebar.success(f"{result['team'] or 'Roster'}: "
                               f"{len(result['players'])} players")
            for p in result["players"]:
                if p["account_id"] not in labels:
                    ids.append(p["account_id"])
                labels[p["account_id"]] = {
                    "ign": p["ign"] or p.get("persona", ""),
                    "team": result["team"] or "imported",
                    "region": result.get("region", ""),
                }
            preset = ", ".join(filter(None, [preset, result["team"] or "imported"]))
        else:
            st.sidebar.warning("No players found in that page. If it needs "
                               "JavaScript, use the bookmarklet.")

    for account_id in parse_ids((raw or "").replace(",", " ").split()):
        if account_id not in labels:
            ids.append(account_id)
            labels[account_id] = {"ign": "", "team": "", "region": ""}

# ---- narrow to individual players, once there is a roster to narrow
if len(ids) > 1:
    with st.sidebar.expander(f"Players ({len(ids)})", expanded=False):
        st.caption("Untick to scout a subset. Everything downstream — "
                   "reports, builds, matches — follows this.")

        def _who(account_id):
            info = labels.get(account_id, {})
            name = info.get("ign") or str(account_id)
            team = info.get("team") or ""
            return f"{name}  ·  {team}" if team else name

        if st.button("All / none", use_container_width=True,
                     key="players_toggle"):
            turn_on = not all(st.session_state.get(f"pl_{a}", True)
                              for a in ids)
            for a in ids:
                st.session_state[f"pl_{a}"] = turn_on

        kept = [a for a in ids
                if st.checkbox(_who(a), value=True, key=f"pl_{a}")]

    if kept and len(kept) < len(ids):
        ids = kept
        labels = {a: labels[a] for a in kept if a in labels}
    elif not kept:
        st.sidebar.warning("No players ticked — showing everyone.")

if selections:
    st.sidebar.success(f"{len(ids)} players selected")

preset = preset or "Custom"

st.sidebar.header("Filters")

days = whole_number(
    st.sidebar.text_input("Days to look back", value=str(DEFAULT_DAYS)),
    DEFAULT_DAYS, "Days")

top = whole_number(
    st.sidebar.text_input("Heroes per player", value="5"),
    5, "Heroes per player", minimum=1, maximum=50)

st.sidebar.markdown("**Match modes**  (tick any combination)")
MODE_OPTIONS = [("ranked", True), ("unranked", True),
                ("private_lobby", True), ("coop_bot", False)]
chosen_modes = [name for name, on in MODE_OPTIONS
                if st.sidebar.checkbox(name, value=on, key=f"mode_{name}")]

if not chosen_modes:
    st.sidebar.warning("Pick at least one match mode. Using ranked + unranked.")
    chosen_modes = ["ranked", "unranked"]
match_mode = ",".join(chosen_modes)

if pros_only:
    # scrims are private lobbies; anything else is not the thing being scouted
    match_mode = CUSTOMS_ONLY
    st.sidebar.caption("Match mode is fixed to **private_lobby** in Pros "
                       "mode.")

game_mode = st.sidebar.selectbox("Game mode", ["normal", "street_brawl"], index=0)

together = st.sidebar.checkbox(
    "Only games they played together",
    value=bool(selections),
    help="Keeps only matches containing several of the selected players, "
         "so pugs and inhouses with strangers drop out. Custom lobbies only.",
)
min_players = 4
if together:
    min_players = whole_number(
        st.sidebar.text_input("Minimum players per match", value="4"),
        4, "Minimum players", minimum=2, maximum=12)
    include_subs = st.sidebar.checkbox(
        "Find stand-ins",
        value=False,
        help="Reads each match's lineup to spot players who filled in on "
             "the roster's side. Costs one request per match, so it is "
             "slower.",
    )
else:
    include_subs = False

run = st.sidebar.button("Build report", type="primary", use_container_width=True)

with st.sidebar.expander("Cache"):
    info = cache_info()
    st.caption(f"{info['entries']} cached responses · {info['megabytes']} MB · "
               f"oldest {info['oldest_hours']}h")
    st.caption("Fresh for: assets 7d · match metadata 1y · "
               "steam names 1d · ranks and stats 6h")

    st.markdown("**Save / restore**")
    st.caption("Hosting wipes this app's disk when it restarts. Download a "
               "bundle now, upload it after, and the permanent data comes "
               "straight back.")

    permanent_only = st.checkbox("Permanent data only", value=True,
                                 help="Assets and finished match metadata — "
                                      "never goes stale, so the bundle keeps "
                                      "working forever.")

    if st.button("Prepare bundle", use_container_width=True):
        blob, meta = export_cache(only_permanent=permanent_only)
        st.session_state.bundle = blob
        st.session_state.bundle_meta = meta

    if "bundle" in st.session_state:
        m = st.session_state.bundle_meta
        st.caption(f"{m['entries']} entries · {m['megabytes']} MB"
                   + (f" · {m['skipped']} skipped" if m["skipped"] else ""))
        st.download_button(
            "Download cache bundle",
            st.session_state.bundle,
            file_name="deadlock_cache.json.gz",
            mime="application/gzip",
            use_container_width=True,
        )

    restore = st.file_uploader("Restore a bundle", type=["gz"],
                               key="cache_upload")
    if restore is not None:
        try:
            result = import_cache(restore.getvalue())
            st.success(f"Restored {result['added']} entries "
                       f"({result['skipped_existing']} already present, "
                       f"{result['skipped_stale']} too old).")
        except Exception as e:
            st.error(f"Not a valid cache bundle: {e}")

    try:
        info = store.status()
    except Exception as e:
        info = {"error": str(e)}
    if "error" not in info and info.get("matches"):
        st.caption(f"Local store: {info['matches']} matches · "
                   f"{info['purchases']} purchases · {info['megabytes']} MB · "
                   f"synced {info['last_sync']}")
    else:
        st.caption("No local store. `python store.py sync` builds one, and "
                   "the Build order tab can then read matches with no "
                   "requests at all.")

    if st.button("Clear cache", use_container_width=True):
        removed = clear_cache()
        st.cache_data.clear()
        st.success(f"Removed {removed} entries. Rebuild to pull live data.")


# --------------------------------------------------------------- main

if st.session_state.get("directory"):
    with st.expander(f"Team directory — {len(st.session_state.directory)} teams"):
        directory = st.session_state.directory
        divisions = sorted({t.get("division", "") for t in directory if t.get("division")})
        c1, c2 = st.columns([2, 1])
        q = c1.text_input("Search teams", key="dirsearch")
        div = c2.selectbox("Division", ["All"] + divisions, key="dirdiv")
        rows = find_teams(directory, q)
        if div != "All":
            rows = [t for t in rows if t.get("division") == div]
        st.caption("This page has no account IDs — only team names and IDs. "
                   "Open a team's page in your browser, then paste it in the "
                   "sidebar to load that roster.")
        cols = [c for c in ("team", "division", "team_id", "url")
                if rows and c in rows[0]]
        st.dataframe(pd.DataFrame(rows)[cols],
                     hide_index=True, use_container_width=True)

st.title("Deadlock Scouting Report" if mode == "Players"
         else "Deadlock — Pros")
st.caption("Most-played heroes, win rates, and ranks — from the community "
           "Deadlock API."
           + ("  Custom games only." if pros_only else ""))

if not ids:
    st.info("Tick a team in the sidebar, or use Custom to paste player IDs.")
    st.stop()

st.write(f"**{preset}** · {len(ids)} player(s) · last {days} days · "
         f"`{match_mode}` · `{game_mode}`")

if run:
    label_key = tuple(sorted((k, tuple(sorted(v.items())))
                             for k, v in labels.items()))
    with st.spinner(f"Pulling data for {len(ids)} player(s)..."):
        try:
            if together:
                players, meta = load_team(tuple(ids), days, top,
                                          min_players, label_key, include_subs)
            else:
                players, meta = load(tuple(ids), days, top, match_mode,
                                     game_mode, label_key)
            st.session_state.players = players
            st.session_state.meta = meta
        except Exception as e:
            st.error(f"Could not reach the Deadlock API: {e}")
            st.stop()

if "players" not in st.session_state:
    st.stop()

players = st.session_state.players
meta = st.session_state.get("meta")
rows = flatten(players)

if not rows:
    st.warning("No matches found in this window. Try more days, "
               "or a different match mode.")
    st.stop()

df = pd.DataFrame(rows)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Player slots", df["row_key"].nunique())
c2.metric("Matches", int(df["matches"].sum()))
c3.metric("Distinct heroes", df["hero"].nunique())
c4.metric("Overall win rate",
          f"{df['wins'].sum() / df['matches'].sum() * 100:.1f}%")

if meta:
    if meta.get("subs"):
        st.warning(f"**{len(meta['subs'])} stand-in(s)** detected across "
                   f"{meta['matches_inspected']} matches — shown with a SUB tag.")
    sizes = ", ".join(f"{n} in {c} matches"
                      for n, c in meta["stack_sizes"].items())
    verified = meta.get("sides_known")
    note = (f"**Team games only** — {meta['shared_matches']} matches with at "
            f"least {meta['min_players']} roster members "
            + ("**on the same side**" if verified
               else "in the lobby (sides unverified)")
            + f". Group sizes per team per match: {sizes}.")
    if not verified:
        note += ("\n\nSide data could not be fetched, so players split "
                 "across opposing sides may be counted together.")
    if meta.get("internal_matches"):
        pairs = ", ".join(f"{k} ({v})"
                          for k, v in list(meta["matchups"].items())[:6])
        note += (f"\n\n{meta['internal_matches']} of those are between teams "
                 f"you selected: {pairs}. A side drawn from more than one "
                 "roster is named with both, joined by `/`. Each side counts "
                 "for its own team, so one scrim appears once per team rather "
                 "than as a single twelve-player group.")
    st.info(note)

# Synergy is ladder-wide and lives in Meta mode, so it is not repeated here
(tab_hero, tab_player, tab_team, tab_match, tab_comps, tab_items,
 tab_data) = st.tabs(
    ["By hero", "By player", "By team", "Matches", "Comps", "Build order",
     "Raw data"])

# ---- pooled hero win rates
with tab_hero:
    col_a, col_b = st.columns([2, 1])
    normalize = col_a.toggle(
        "Normalize by player", value=True,
        help="Average each player's own usage rate instead of summing games, "
             "so someone who plays far more than the rest does not dominate.")
    min_games = whole_number(
        col_b.text_input("Min games for avg WR", value="2"),
        2, "Min games", minimum=1, maximum=50)

    totals = pd.DataFrame(hero_totals(players, normalize=normalize,
                                      min_games=min_games))
    order = (["hero", "pick_share", "avg_win_rate", "players", "matches",
              "wins", "win_rate"] if normalize else
             ["hero", "players", "matches", "wins", "win_rate",
              "pick_share", "avg_win_rate"])
    st.dataframe(
        totals[order], hide_index=True, use_container_width=True,
        column_config={
            "hero": "Hero",
            "pick_share": st.column_config.ProgressColumn(
                "Pick share", format="%.1f%%", min_value=0,
                max_value=float(max(totals["pick_share"].max(), 1))),
            "avg_win_rate": st.column_config.NumberColumn(
                "Avg WR", format="%.1f%%",
                help="Mean of individual win rates — equal weight per player"),
            "players": st.column_config.NumberColumn("Players"),
            "matches": st.column_config.NumberColumn("Played"),
            "wins": st.column_config.NumberColumn("Wins"),
            "win_rate": st.column_config.NumberColumn(
                "Pooled WR", format="%.1f%%",
                help="Total wins / total games — weighted by volume"),
        },
    )
    st.caption("**Pick share** averages each player's own usage rate, so every "
               "player counts equally. **Played** is the raw game count for "
               "comparison.")

# ---- per player
with tab_player:
    for p in players:
        who = p.get("ign") or p.get("persona_name") or f"Account {p['account_id']}"
        if p.get("is_sub"):
            home = f" of {p['home_team']}" if p.get("home_team") else ""
            who = f"{who}  ⟨SUB for {p['sub_for']}{home}⟩"
        team = f" · {p['team']}" if p.get("team") else ""
        extra = (f" ({p['team_matches']} of {p['custom_matches']} customs)"
                 if "team_matches" in p else "")
        header = (f"**{who}**{team} — {p['rank_label']} · "
                  f"{p['total_matches']} matches{extra}")
        with st.expander(header, expanded=len(players) <= 6):
            if not p["heroes"]:
                st.write("No matches in this window.")
                continue
            st.dataframe(
                pd.DataFrame(p["heroes"])[["hero", "matches", "wins", "win_rate"]],
                hide_index=True, use_container_width=True,
                column_config={
                    "hero": "Hero",
                    "matches": st.column_config.NumberColumn("Played"),
                    "wins": st.column_config.NumberColumn("Wins"),
                    "win_rate": WINRATE_COL,
                },
            )

# ---- per team
with tab_team:
    if not df["team"].astype(str).str.strip().any():
        st.info("Pick a Pros roster to see team breakdowns.")
    else:
        df["base_team"] = df["team"].str.replace(" (sub)", "", regex=False)
        for team in [t for t in df["base_team"].unique() if t]:
            sub = df[df["base_team"] == team]
            agg = (sub.groupby("hero", as_index=False)
                      .agg(players=("row_key", "nunique"),
                           matches=("matches", "sum"),
                           wins=("wins", "sum")))
            agg["win_rate"] = (agg["wins"] / agg["matches"] * 100).round(1)
            agg = agg.sort_values("matches", ascending=False)

            # distinct matches the team played, not the sum of per-player
            # rows -- six players in ten scrims is ten matches, not sixty
            record = ((meta or {}).get("team_records") or {}).get(team)
            player_games = int(sub["matches"].sum())
            if record and record["matches"]:
                rate = ("" if record["win_rate"] is None
                        else f", {record['win_rate']:.1f}% win rate")
                st.subheader(f"{team} — {record['matches']} matches{rate}")
                st.caption(f"{player_games} player-games across "
                           f"{sub['row_key'].nunique()} players. The table "
                           "below counts player-games, so its total is larger "
                           "than the match count.")
            else:
                wr = (sub["wins"].sum() / player_games * 100
                      if player_games else 0)
                st.subheader(f"{team} — {player_games} player-games, "
                             f"{wr:.1f}% win rate")
                st.caption("Distinct match counts need **Only games they "
                           "played together**; without it this sums each "
                           "player's games, so a six-player scrim counts six "
                           "times.")
            st.dataframe(
                agg, hide_index=True, use_container_width=True,
                column_config={
                    "hero": "Hero",
                    "players": st.column_config.NumberColumn("Players"),
                    "matches": st.column_config.NumberColumn("Played"),
                    "wins": st.column_config.NumberColumn("Wins"),
                    "win_rate": WINRATE_COL,
                },
            )

# ---- individual match lineups
with tab_match:
    match_ids = (meta or {}).get("match_ids") or []
    if not match_ids:
        st.info("Tick **Only games they played together** and rebuild to see "
                "individual match lineups.")
    else:
        how_many = whole_number(
            st.text_input("How many matches to load", value="10"),
            10, "Matches", minimum=1, maximum=60)
        st.caption(f"{len(match_ids)} qualifying matches. Loading lineups "
                   f"costs one request each, cached afterwards.")

        if st.button("Load lineups"):
            with st.spinner("Reading match lineups..."):
                st.session_state.comps = load_comps(
                    tuple(match_ids), tuple(ids),
                    tuple(sorted((k, tuple(sorted(v.items())))
                                 for k, v in labels.items())),
                    how_many)

        comps = st.session_state.get("comps") or []
        if comps:
            mirrors = mirror_matches(comps)
            if mirrors:
                st.info(f"**{len(mirrors)} of {len(comps)} are pro vs pro** — "
                        "both lineups are teams you selected, so both sides "
                        "are counted below. Filter by team to separate them.")

            teams_here = sorted({t for m in comps
                                 for s in m.get("roster_sides", [])
                                 for t in [(m.get("side_names") or {}).get(s)]
                                 if t})
            cc1, cc2 = st.columns([1, 1])
            scope = cc1.selectbox("Count picks for", ["All selected"]
                                  + teams_here, key="cc_team")
            split = cc2.checkbox("One row per team", value=bool(mirrors),
                                 key="cc_split")
            counts = pd.DataFrame(composition_counts(
                comps, ids, by_team=split,
                team=None if scope == "All selected" else scope))
            if not counts.empty:
                st.markdown("**Heroes they build around**")
                cols = (["team"] if split else []) + ["hero", "games", "wins",
                                                     "win_rate"]
                st.dataframe(
                    counts[cols], hide_index=True, use_container_width=True,
                    column_config={
                        "team": "Team",
                        "hero": "Hero",
                        "games": st.column_config.NumberColumn("Games"),
                        "wins": st.column_config.NumberColumn("Wins"),
                        "win_rate": WINRATE_COL,
                    })

            st.markdown("**Lineups**")
            for m in comps:
                when = (time.strftime("%b %d", time.localtime(m["start_time"]))
                        if m.get("start_time") else "")
                mins = f"{m['duration_s'] // 60} min" if m.get("duration_s") else ""
                ours = m.get("side_names", {}).get(m.get("our_side"), "")
                if m.get("mirror"):
                    won_by = (m.get("side_names") or {}).get(m.get("winner"), "")
                    result = f" — {won_by} won" if won_by else ""
                    header = (f"{m.get('matchup') or 'pro vs pro'}{result}"
                              f"  ·  {when} {mins}  ·  {m['match_id']}")
                else:
                    result = ("" if m["winner"] is None else
                              (" — WIN" if m["our_side"] == m["winner"]
                               else " — LOSS"))
                    header = (f"{ours or 'match'} {result}  ·  {when} {mins}"
                              f"  ·  {m['match_id']}")

                with st.expander(header.strip()):
                    order = [m["our_side"]] if m["our_side"] is not None else []
                    order += [x for x in sorted(m["sides"]) if x not in order]
                    cols = st.columns(len(order))
                    for col, side in zip(cols, order):
                        label = m.get("side_names", {}).get(side) or f"Side {side}"
                        won = ("" if m["winner"] is None else
                               ("  ✅" if side == m["winner"] else "  ❌"))
                        col.markdown(f"**{label}**{won}")
                        col.dataframe(
                            pd.DataFrame([{"player": p["name"], "hero": p["hero"]}
                                          for p in m["sides"][side]]),
                            hide_index=True, use_container_width=True)

# ---- how they build a side, and what shows up opposite
with tab_comps:
    st.caption("Both lineups of every match in the window, in one request. "
               "Comps are heroes that appear together on a side; matchups are "
               "what tends to show up across from a pick.")

    gc1, gc2, gc3 = st.columns([1, 1, 1])
    combo_size = gc1.selectbox("Comp size", [2, 3, 4, 5], index=1)
    min_games = whole_number(
        gc2.text_input("Min games", value="3"),
        3, "Min games", minimum=1, maximum=500)
    match_limit = whole_number(
        gc3.text_input("Match limit", value="1000"),
        1000, "Match limit", minimum=1, maximum=10000)

    labels_tuple = tuple((k, tuple(sorted(v.items())))
                         for k, v in sorted(labels.items()))
    try:
        lineups = load_lineups(tuple(ids), labels_tuple, days, match_mode,
                               match_limit)
    except Exception as e:
        lineups, _ = [], st.error(f"Could not load lineups: {e}")

    if not lineups:
        st.info("No lineups in this window. Widen the days or loosen the "
                "match modes in the sidebar.")
    else:
        found = lineup_teams(lineups)
        if len(lineups) >= match_limit:
            st.warning(f"Hit the {match_limit}-match limit — results are "
                       "ordered newest first, so older games in this window "
                       "were left out. Raise the limit for the full picture.")
        st.caption(f"{len(lineups)} matches · sides seen: "
                   + ", ".join(found[:8]) if found else f"{len(lineups)} matches")
        if winner_offset(lineups) is None:
            st.warning("Match outcomes could not be matched to sides in this "
                       "data, so win rates are blank rather than shown as 0%.")

        st.subheader("Comps they run")
        pick_team = st.selectbox("Side", ["Any"] + found, key="comp_team")
        combos = hero_combos(lineups, size=combo_size,
                             team=None if pick_team == "Any" else pick_team,
                             min_games=min_games)
        if not combos:
            st.info(f"No {combo_size}-hero group appears {min_games}+ times. "
                    "Lower **Min games** or shrink the comp size.")
        else:
            cframe = pd.DataFrame(combos)
            st.dataframe(
                cframe[["team", "heroes", "games", "share", "win_rate"]],
                hide_index=True, use_container_width=True,
                column_config={
                    "team": "Side",
                    "heroes": "Together",
                    "games": st.column_config.NumberColumn("Games"),
                    "share": st.column_config.NumberColumn(
                        "Of their games", format="%.1f%%"),
                    "win_rate": st.column_config.ProgressColumn(
                        "Win rate", format="%.1f%%", min_value=0,
                        max_value=100),
                })
            st.download_button("Download comps (CSV)",
                               cframe.to_csv(index=False).encode(),
                               file_name="comps.csv", mime="text/csv")

with tab_items:
    ic1, ic2, ic3 = st.columns([2, 1, 1])
    names_by_id = load_hero_names()
    hero_choice = ic1.selectbox(
        "Hero", ["All heroes"] + [names_by_id[h] for h in sorted(
            names_by_id, key=lambda h: names_by_id[h] or "")])
    hero_id = None
    if hero_choice != "All heroes":
        hero_id = next((h for h, n in names_by_id.items() if n == hero_choice),
                       None)
    min_matches = whole_number(
        ic2.text_input("Min matches per item", value="1"),
        1, "Min matches", minimum=1, maximum=1000)
    top_items = whole_number(
        ic3.text_input("Rows shown", value="40"),
        40, "Rows shown", minimum=1, maximum=500)

    key = (tuple(ids), hero_id, days, match_mode, min_matches)
    view = st.radio("View", ["Buy order", "By phase", "What follows what",
                             "From matches"],
                    horizontal=True, label_visibility="collapsed")

    # ---------------------------------------------------------- buy order
    if view == "Buy order":
        st.caption("From item-stats: every item these players bought, sorted "
                   "by the average clock time they bought it at. Reading down "
                   "the table is reading the build.")
        split = st.checkbox(
            "Split by hero", value=False,
            help="Buckets the same single request by hero — no extra "
                 "requests, one row set per hero they played.")
        try:
            rows = (load_buy_order_bucketed(*key, "hero") if split
                    else load_buy_order(*key))
        except Exception as e:
            rows, _ = [], st.error(f"Could not load buy order: {e}")

        if not rows:
            st.info("Nothing came back. Widen the window, lower **Min "
                    "matches**, or loosen the match modes — custom lobbies "
                    "alone are a thin sample.")
            with st.expander("What the API actually returned"):
                try:
                    st.json(load_buy_raw(*key))
                except Exception as e:
                    st.write(f"(request failed: {e})")
        else:
            frame = pd.DataFrame(rows)
            st.caption(f"{len(frame)} items.")
            cols = (["hero"] if split and frame["hero"].any() else []) + [
                "buy_time", "item", "buys", "players", "win_rate"]
            st.dataframe(
                frame.head(top_items)[cols],
                hide_index=True, use_container_width=True,
                column_config={
                    "hero": st.column_config.TextColumn("Hero", width="small"),
                    "buy_time": st.column_config.TextColumn("Bought at",
                                                            width="small"),
                    "item": "Item",
                    "buys": st.column_config.NumberColumn("Buys"),
                    "players": st.column_config.NumberColumn("Players"),
                    "win_rate": st.column_config.ProgressColumn(
                        "Win rate", format="%.1f%%", min_value=0,
                        max_value=100),
                })
            st.download_button("Download buy order (CSV)",
                               frame.to_csv(index=False).encode(),
                               file_name="buy_order.csv", mime="text/csv")

    # ---------------------------------------------------------- by phase
    elif view == "By phase":
        st.caption("From item-flow-stats: purchases bucketed into 10-minute "
                   "phases. **Adj. WR** re-weights win rate across net-worth "
                   "buckets, so it is not just measuring who was already "
                   "ahead.")
        try:
            raw = load_flow_raw(*key)
            rows = flow_rows(raw)
        except Exception as e:
            raw, rows = None, []
            st.error(f"Could not load build flow: {e}")

        if not rows:
            st.info("Nothing came back at these filters.")
            if raw is not None:
                with st.expander("What the API actually returned"):
                    st.json(raw)
        else:
            frame = pd.DataFrame(rows)
            reached = (raw or {}).get("reached_per_column") or []
            if reached:
                st.caption("Games reaching each phase: "
                           + ", ".join(f"{phase_label(i)}: {n}"
                                       for i, n in enumerate(reached)))
            st.dataframe(
                frame.head(top_items)[["window", "item", "buys", "pick_rate",
                                       "win_rate", "adj_win_rate"]],
                hide_index=True, use_container_width=True,
                column_config={
                    "window": st.column_config.TextColumn("Phase",
                                                          width="small"),
                    "item": "Item",
                    "buys": st.column_config.NumberColumn("Buys"),
                    "pick_rate": st.column_config.NumberColumn(
                        "Pick rate", format="%.1f%%"),
                    "win_rate": st.column_config.ProgressColumn(
                        "Win rate", format="%.1f%%", min_value=0,
                        max_value=100),
                    "adj_win_rate": st.column_config.NumberColumn(
                        "Adj. WR", format="%.1f%%"),
                })
            st.download_button("Download phases (CSV)",
                               frame.to_csv(index=False).encode(),
                               file_name="build_phases.csv", mime="text/csv")

    # ------------------------------------------------- from real matches
    elif view == "From matches":
        st.caption("One game at a time, straight out of match metadata: the "
                   "purchase sequence in a specific game against a specific "
                   "lineup, with items and ability points on one clock. For "
                   "**averages**, use Buy order instead — it covers a larger "
                   "population and costs a fraction of the requests. This "
                   "view is for the matchup, not the mean.")

        info = load_store_status()
        stored = info.get("purchases", 0) if "error" not in info else 0

        mc1, mc2, mc3 = st.columns([1, 1, 1])
        source = mc1.radio(
            "Source", ["Local store", "API (bulk)"] if stored else ["API (bulk)"],
            horizontal=True,
            help="The local store is a SQLite copy synced by store.py. "
                 "Reading it costs no requests at all.")
        how_many = whole_number(
            mc2.text_input("Match limit", value="1000"),
            1000, "Match limit", minimum=1, maximum=10000)
        kind = mc3.radio("Show", ["Items", "Ability points"], horizontal=True)
        kind_key = "item" if kind == "Items" else "ability"

        if stored:
            st.caption(f"Store: {info['matches']} matches, {stored} purchases, "
                       f"{info['megabytes']} MB, last synced {info['last_sync']}. "
                       "Refresh it with `python store.py sync`.")
        else:
            st.caption("No local store yet — run `python store.py sync` to "
                       "build one, then this reads from disk with no requests.")

        labels_tuple = tuple((k, tuple(sorted(v.items())))
                             for k, v in sorted(labels.items()))
        brows = []
        try:
            if source == "Local store":
                brows = load_store_builds(tuple(ids), labels_tuple, days)
            else:
                brows = load_bulk_builds(tuple(ids), labels_tuple, days,
                                         match_mode, how_many)
        except Exception as e:
            st.error(f"Could not read builds: {e}")

        if not brows:
            st.info("No purchases for these players in this window.")
            if source != "Local store":
                with st.expander("Metadata diagnostic"):
                    st.caption("If matches exist but nothing parsed, the "
                               "response shape differs from what this expects.")
                    try:
                        by_player = load_match_ids(tuple(ids), days, match_mode)
                        first = next((m for v in by_player.values()
                                      for m in v), None)
                        st.json(metadata_report(first) if first
                                else {"note": "no matches in window"})
                    except Exception as e:
                        st.write(f"(diagnostic failed: {e})")
        else:
            read = len({r["match_id"] for r in brows})
            if source != "Local store" and read >= how_many:
                st.warning(f"Hit the {how_many}-match limit — newest first, "
                           "so older games were left out.")
            summary = build_summary(brows, kind=kind_key)
            sframe = pd.DataFrame(summary)
            seen = sorted(sframe["player"].unique()) if len(sframe) else []
            st.caption(f"{len(brows)} purchases across "
                       f"{len({r['match_id'] for r in brows})} matches, "
                       f"{len(seen)} players.")
            who = st.multiselect("Players", seen, default=seen,
                                 key="mb_players")
            if len(sframe):
                st.dataframe(
                    sframe[sframe["player"].isin(who)][
                        ["player", "buy_time", "item", "buys", "matches",
                         "win_rate"]],
                    hide_index=True, use_container_width=True,
                    column_config={
                        "player": "Player",
                        "buy_time": st.column_config.TextColumn(
                            "Avg bought", width="small"),
                        "item": "Item",
                        "buys": st.column_config.NumberColumn("Buys"),
                        "matches": st.column_config.NumberColumn("Games"),
                        "win_rate": st.column_config.ProgressColumn(
                            "Win rate", format="%.1f%%", min_value=0,
                            max_value=100),
                    })
            st.download_button(
                "Download match builds (CSV)",
                pd.DataFrame(brows).to_csv(index=False).encode(),
                file_name="match_builds.csv", mime="text/csv")

            st.markdown("**One game at a time**")
            played = sorted({r["match_id"] for r in brows}, reverse=True)
            pick = st.selectbox("Match", played, key="mb_match")
            here = [r for r in brows if r["match_id"] == pick]
            sides_here = sorted({r.get("team") or "" for r in here})
            if len([t for t in sides_here if t]) > 1:
                st.caption("**Pro vs pro** — " + " vs ".join(t for t in
                                                             sides_here if t)
                           + ". Both lineups are yours; the grouping below "
                             "keeps them apart.")
            in_match = sorted({(r.get("team") or "", r["account_id"],
                                r["player"]) for r in here})
            shown_team = object()
            for team_here, account_id, player in in_match:
                if team_here != shown_team:
                    shown_team = team_here
                    if team_here:
                        st.markdown(f"*{team_here}*")
                seq = match_build_order(brows, pick, account_id, kind=kind_key)
                if not seq:
                    continue
                hero = seq[0].get("hero") or "?"
                won = seq[0].get("won")
                tag = "" if won is None else (" · won" if won else " · lost")
                with st.expander(f"{player} — {hero}{tag} "
                                 f"({len(seq)} purchases)"):
                    st.dataframe(
                        pd.DataFrame(seq)[["buy_time", "item"]],
                        hide_index=True, use_container_width=True,
                        column_config={
                            "buy_time": st.column_config.TextColumn(
                                "At", width="small"),
                            "item": "Bought"})

    # ---------------------------------------------------- transitions
    elif view == "What follows what":
        st.caption("From the flow graph's edges: when they bought the item on "
                   "the left, what did they buy next.")
        try:
            edges = flow_edges(load_flow_raw(*key))
        except Exception as e:
            edges, _ = [], st.error(f"Could not load transitions: {e}")
        if not edges:
            st.info("No transitions at these filters — usually too few games.")
        else:
            eframe = pd.DataFrame(edges)
            st.dataframe(
                eframe.head(top_items)[["from", "to", "matches", "win_rate"]],
                hide_index=True, use_container_width=True,
                column_config={
                    "from": "Bought", "to": "Then bought",
                    "matches": st.column_config.NumberColumn("Games"),
                    "win_rate": st.column_config.ProgressColumn(
                        "Win rate", format="%.1f%%", min_value=0,
                        max_value=100),
                })
            st.download_button("Download transitions (CSV)",
                               eframe.to_csv(index=False).encode(),
                               file_name="build_transitions.csv",
                               mime="text/csv")

    # ------------------------------------------------ per-player breakdown
    st.divider()
    st.subheader("Per player")
    st.caption(f"One request per player — {len(ids)} selected, paced under the "
               "analytics rate limit. Cached for an hour once loaded.")
    if st.button("Load per-player buy order"):
        st.session_state.item_players = True

    if st.session_state.get("item_players"):
        labels_tuple = tuple(
            (k, tuple(sorted(v.items()))) for k, v in sorted(labels.items()))
        try:
            prows = load_buy_order_players(tuple(ids), labels_tuple, hero_id,
                                           days, match_mode, min_matches)
        except Exception as e:
            prows, _ = [], st.error(f"Could not load per-player order: {e}")

        if not prows:
            st.info("No per-player rows — usually too thin a sample once it "
                    "is split by player.")
        else:
            pframe = pd.DataFrame(prows)
            everyone = sorted(pframe["player"].unique())
            who = st.multiselect("Players", everyone, default=everyone)
            sub = pframe[pframe["player"].isin(who)]
            sub = sub.sort_values(
                ["player", "avg_buy_s"] if "avg_buy_s" in sub.columns
                else ["player"])
            st.dataframe(
                sub[["player", "buy_time", "item", "buys", "win_rate"]],
                hide_index=True, use_container_width=True,
                column_config={
                    "player": "Player",
                    "buy_time": st.column_config.TextColumn("Bought at",
                                                            width="small"),
                    "item": "Item",
                    "buys": st.column_config.NumberColumn("Buys"),
                    "win_rate": st.column_config.ProgressColumn(
                        "Win rate", format="%.1f%%", min_value=0,
                        max_value=100),
                })
            st.download_button("Download per-player buy order (CSV)",
                               pframe.to_csv(index=False).encode(),
                               file_name="buy_order_by_player.csv",
                               mime="text/csv")

    # ------------------------------------------------ a player's normal build
    st.divider()
    st.subheader("Standard build")
    st.caption("What a player normally buys on one hero — the items they take "
               "in most of their games, in buy order. One request per player.")

    sb1, sb2 = st.columns([2, 1])
    focus = sb1.selectbox(
        "Player", ids,
        format_func=lambda a: labels.get(a, {}).get("ign") or str(a),
        key="sb_player")
    core_share = whole_number(
        sb2.text_input("Core threshold %", value="50"),
        50, "Core threshold", minimum=1, maximum=100)

    if focus is not None:
        try:
            played = load_top_heroes(focus, days, match_mode)
        except Exception as e:
            played, _ = [], st.error(f"Could not list their heroes: {e}")

        if not played:
            st.info("No heroes for that player in this window.")
        else:
            who = labels.get(focus, {}).get("ign") or str(focus)
            choice = st.selectbox(
                "Hero", played,
                format_func=lambda h: (f"{h['hero']} — {h['matches']} games"
                                       + (f", {h['win_rate']:.0f}%"
                                          if h["win_rate"] is not None else "")),
                key="sb_hero")
            labels_tuple = tuple((k, tuple(sorted(v.items())))
                                 for k, v in sorted(labels.items()))
            try:
                builds = load_typical_builds((focus,), labels_tuple,
                                             choice["hero_id"], days,
                                             match_mode, min_matches,
                                             float(core_share))
            except Exception as e:
                builds, _ = [], st.error(f"Could not load that build: {e}")

            if not builds or not (builds[0]["core"] or builds[0]["situational"]):
                st.info(f"No item data for {who} on {choice['hero']} in this "
                        "window.")
            else:
                build = builds[0]
                st.markdown(f"**{who} — {choice['hero']}**  ·  "
                            f"{choice['matches']} games in window")
                cc, sc = st.columns(2)
                with cc:
                    st.markdown(f"**Core** — in {core_share}%+ of games")
                    if build["core"]:
                        st.dataframe(
                            pd.DataFrame(build["core"])[
                                ["buy_time", "item", "share", "win_rate"]],
                            hide_index=True, use_container_width=True,
                            column_config={
                                "buy_time": st.column_config.TextColumn(
                                    "At", width="small"),
                                "item": "Item",
                                "share": st.column_config.NumberColumn(
                                    "Games", format="%.0f%%"),
                                "win_rate": st.column_config.NumberColumn(
                                    "WR", format="%.0f%%"),
                            })
                    else:
                        st.caption("Nothing clears the threshold — lower it.")
                with sc:
                    st.markdown("**Situational** — everything else")
                    if build["situational"]:
                        st.dataframe(
                            pd.DataFrame(build["situational"]).head(15)[
                                ["buy_time", "item", "share", "win_rate"]],
                            hide_index=True, use_container_width=True,
                            column_config={
                                "buy_time": st.column_config.TextColumn(
                                    "At", width="small"),
                                "item": "Item",
                                "share": st.column_config.NumberColumn(
                                    "Games", format="%.0f%%"),
                                "win_rate": st.column_config.NumberColumn(
                                    "WR", format="%.0f%%"),
                            })
                    else:
                        st.caption("Nothing outside the core.")
                st.caption(f"Share is out of {build['games']} — the most-bought "
                           "item's count, since item-stats does not report "
                           "games played directly.")

    # ------------------------------------------------ ability point order
    st.divider()
    st.subheader("Ability point order")
    if hero_id is None:
        st.caption("Pick a hero above — this endpoint needs one.")
    else:
        style = st.radio(
            "Show abilities as", list(ABILITY_STYLES), horizontal=True,
            index=1,
            help="Numbers are the 1/2/3/4 slots from the hero's asset. "
                 "Anything outside those four (an innate, say) keeps its "
                 "name.")
        slots = load_ability_slots(hero_id)
        if style != "Names" and not slots:
            st.warning(
                f"No slot numbers for {hero_choice} — its hero asset does not "
                "name the four signature slots, so this falls back to ability "
                "names.")
        try:
            arows = ability_rows(
                load_ability_raw(tuple(ids), hero_id, days, match_mode,
                                 min_matches),
                hero_id=hero_id, style=style, slots=slots)
        except Exception as e:
            arows, _ = [], st.error(f"Could not load ability order: {e}")
        if not arows:
            st.info(f"No ability orders for {hero_choice} at these filters.")
        else:
            aframe = pd.DataFrame(arows)
            st.dataframe(
                aframe[["order", "matches", "win_rate"]],
                hide_index=True, use_container_width=True,
                column_config={
                    "order": "Upgrade order",
                    "matches": st.column_config.NumberColumn("Games"),
                    "win_rate": st.column_config.ProgressColumn(
                        "Win rate", format="%.1f%%", min_value=0,
                        max_value=100),
                })


with tab_data:
    st.dataframe(df, hide_index=True, use_container_width=True)
    safe = preset.replace(" ", "_").replace("—", "").lower()
    st.download_button(
        "Download CSV",
        df.to_csv(index=False).encode("utf-8"),
        file_name=f"deadlock_{safe}_{days}d.csv",
        mime="text/csv",
    )