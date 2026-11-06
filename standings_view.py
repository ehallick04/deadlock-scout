"""
standings_view.py -- the Standings mode: every league team ranked by the
average rank of its players, across the league or within a division.

The arithmetic and its caveats live in team_ranks.py. This only draws it.
"""

import pandas as pd
import streamlit as st

import team_ranks
from climb_view import _safe
from teams import LEAGUE, divisions


@st.cache_data(ttl=3600, show_spinner="Reading every rostered player's rank…")
def _badges(ids_tuple):
    # One cache entry for the whole league: two bulk calls, refreshed hourly.
    # Ranks move one game at a time, so an hour-old badge is the same badge.
    return team_ranks.fetch_badges(list(ids_tuple))


# label -> (include_subs, top_n)
COUNTED = {"Core + POC": (False, None),
           "Whole roster": (True, None),
           "Top 6 by rank": (False, 6)}


def render(badge_label) -> None:
    st.title("League standings")
    if not LEAGUE:
        st.info("No rosters.json yet — run build_rosters.py first.")
        return
    st.caption(
        "Each team's players' current ranks, averaged on the ladder and shown "
        "as the nearest real rank. A rank is the badge after the player's "
        "latest ranked game. Unranked players are left out of the average and "
        "counted beside it; a team with too few ranked players is listed "
        "without a place.")

    left, middle, right = st.columns([2, 1, 1])
    scope = left.selectbox("Show", ["Whole league"] + divisions(),
                           key="st_scope")
    counted = middle.radio(
        "Players counted", list(COUNTED), key="st_counted",
        help="Core + POC: the listed starting team. Whole roster: subs too. "
             "Top 6 by rank: the six highest-ranked players on the roster, subs "
             "included -- the strongest lineup the team could field. Top 6 "
             "favours deep rosters a little, since a nine-player team picks "
             "from nine.")
    include_subs, top_n = COUNTED[counted]
    min_ranked = right.number_input("Min ranked players", 1, 9,
                                    team_ranks.MIN_RANKED, key="st_min",
                                    help="Fewer than this and the team is "
                                         "listed but not placed.")

    everyone = sorted({a for t in LEAGUE.values() for a in t["players"].values()})
    try:
        badges = _badges(tuple(everyone))
    except Exception as error:          # the API being down is not a crash
        st.error(f"Could not read ranks: {error}")
        return

    rows = team_ranks.standings(
        division=None if scope == "Whole league" else scope,
        include_subs=include_subs, min_ranked=int(min_ranked), badges=badges,
        top_n=top_n)

    table = pd.DataFrame([{
        "Division place": r["division_place"],
        "League place": r["league_place"],
        "Team": r["team"],
        "Division": r["division"],
        "Average rank": badge_label(r["mean_badge"]) if r["mean_badge"] else "—",
        "Average (rung)": (round(r["mean_rung"], 2)
                           if r["mean_rung"] is not None else None),
        "Median": badge_label(r["median_badge"]) if r["median_badge"] else "—",
        "Highest": badge_label(r["top_badge"]) if r["top_badge"] else "—",
        "Lowest": badge_label(r["low_badge"]) if r["low_badge"] else "—",
        "Ranked": f"{r['ranked']} of {r['players']}",
        "Roster as of": r["stale_since"] or "current",
    } for r in rows])
    if scope == "Whole league":
        table = table.drop(columns=["Division place"])
    else:
        table = table.drop(columns=["Division"])

    st.dataframe(table, hide_index=True, use_container_width=True,
                 column_config={"Average (rung)": st.column_config.NumberColumn(
                     help="0 is Obscurus 1, 71 is Eternus 6; six rungs a tier. "
                          "Two teams a fraction of a rung apart are level.")})

    unplaced = [r["team"] for r in rows if r["league_place"] is None]
    stale = [r["team"] for r in rows if r["stale_since"]]
    if unplaced:
        st.caption(f"Not placed, under {int(min_ranked)} ranked players: "
                   + ", ".join(_safe(n) for n in unplaced))
    if stale:
        st.caption("Roster page came back empty in the latest harvest, so the "
                   "previous roster is used: " + ", ".join(_safe(n) for n in stale))
