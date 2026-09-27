"""
climb_view.py — finding people to climb with.

Two stages, and the page is built around keeping them apart. The scan is one
aggregated read over a whole badge band; the drill-down is a pair of calls per
account. Presenting them as one button would mean either profiling a thousand
strangers or scanning a handful, and neither answers the question.

The other thing the layout is for is honesty about what the numbers are worth.
A win rate over a fortnight is mostly noise -- measured on the sweep, the top
decile by first-half win rate went 75.2% to 51.9% in the second half -- so the
shrunk estimate is the column that sorts, the raw one sits next to it, and the
gap between them is on screen rather than in a footnote. A reader who never
looks at the caveat still reads the right column, because it is the one the
table is ordered by.
"""

import pandas as pd
import streamlit as st

SORTS = {
    "Best estimate of skill (recommended)": "shrunk_win_rate",
    "Raw win rate — see what it would have said": "win_rate",
    "Most active": "games",
    "Rising fastest (needs a look-closer first)": "rank_progress",
}

PREAMBLE = """
**Sorting a rank by win rate does not work, and this page is built around that.**

Split-half on 3,246 accounts in a Phantom+ sweep: the correlation between a
player's win rate in the first half of their games and the second half is
**+0.07**. The top decile averaged 75.2% in the first half and **51.9%** in the
second. True spread in win rate *within* a band is about **4 points** — nobody
at a fixed rank genuinely wins 70%.

Worse, it is not a sample-size problem that more games would fix: games played
correlates **−0.11** with win rate inside a band, because a player who really
wins 60% *leaves the rank*. The high win rates still sitting at your rank belong
disproportionately to people who got lucky and people on their way out.

So the table sorts on a **shrunk** win rate — the observed rate pulled towards
even by how little the sample is worth. Activity is measured honestly by
comparison (split-half r = **+0.47**), because a count has almost no sampling
error. And the column worth the most is **rank progress**, from the game's own
`ranked_delta`: it draws on the player's whole ranked history rather than our
slice of it, and it is the one signal the band-escape effect does not suppress.
"""


def _pct(value):
    return "—" if value is None else f"{100 * value:.1f}%"


# Personanames are arbitrary player-controlled text arriving from a third party,
# and Streamlit renders markdown in captions, expander labels and metric help.
# A name of "**x**" would render bold, and "[click](http://...)" would render a
# link somebody else chose -- so every name is escaped before it reaches any of
# those. Dataframe cells are not markdown and need no escaping, but they get it
# anyway rather than depending on which widget a value ends up in.
_MARKDOWN = "\\`*_{}[]()#+-.!|<>$~"


def _safe(text):
    if not text:
        return ""
    out = "".join("\\" + c if c in _MARKDOWN else c for c in str(text))
    return out.replace("\n", " ").replace("\r", " ")


def _label(row, how, badge_label=None):
    """
    How to print one account, given the display toggle.

    The id is never thrown away. Personanames are not unique -- two candidates
    can share one -- and they change whenever the player feels like it, so a name
    labels a row and an id identifies it. "Both" is the default for that reason.
    """
    account = row.get("account_id")
    persona = row.get("persona")
    if how == "Account ID" or not persona:
        return str(account)
    if how == "Persona":
        return persona
    return f"{persona} ({account})"


def _rank(row, badge_label=None):
    """Their own badge, in the app's usual wording. Blank means unranked."""
    badge = row.get("badge")
    if badge is None:
        return "—"
    if badge_label:
        try:
            return badge_label(badge)
        except Exception:
            pass
    return str(badge)


def _reliability(note: dict) -> None:
    """The caveat, as a number rather than a hedge."""
    if not note:
        return
    weight = note.get("median_shrink_weight") or 0.0
    st.info(
        f"**What this sample is worth.** At the median account's "
        f"{note.get('median_games', 0)} games, an observed win rate carries "
        f"**{100 * weight:.0f}%** of its face value. "
        f"{note.get('example', '')} "
        f"It takes about **{note.get('shrink_games')}** games to be worth half, "
        f"and **{note.get('games_for_quarter_weight')}** to be worth a quarter."
    )


def _candidates_frame(rows: list, how="Both", badge_label=None) -> pd.DataFrame:
    return pd.DataFrame([{
        "player": _label(row, how),
        # Their own rank, which is a different question from the band scanned:
        # the band filters on the *match* average badge, so a candidate found in
        # it need not be ranked in it.
        "rank": _rank(row, badge_label),
        "games": row["games"],
        "per week": round(row["games_per_week"], 1),
        # The shrunk estimate first, because it is what the order means.
        "best estimate": _pct(row["shrunk_win_rate"]),
        "raw": _pct(row["win_rate"]),
        "worth": f"{100 * row['shrink_weight']:.0f}%",
        "95% CI": f"{_pct(row['win_rate_low'])} – {_pct(row['win_rate_high'])}",
    } for row in rows])


def _hero_frame(rows: list, hero_names: dict) -> pd.DataFrame:
    return pd.DataFrame([{
        "hero": hero_names.get(str(row["hero_id"]),
                               hero_names.get(row["hero_id"],
                                              f"#{row['hero_id']}")),
        "games": row["games"],
        "share": f"{100 * row['share']:.0f}%",
        "best estimate": _pct(row["shrunk_win_rate"]),
        "raw": _pct(row["win_rate"]),
    } for row in rows])


def _profile_card(entry: dict, hero_names: dict, how="Both",
                  badge_label=None) -> None:
    track = entry.get("trajectory") or {}
    steady = entry.get("consistency") or {}
    rank = entry.get("rank") or {}

    header = _safe(_label(entry, how))
    badge = entry.get("badge") or rank.get("badge")
    if badge:
        header += f" · {_rank({'badge': badge}, badge_label)}"
    header += f" · {entry['games']} ranked games"

    with st.expander(header):
        left, middle, right = st.columns(3)
        left.metric("Best estimate", _pct(entry["shrunk_win_rate"]),
                    help=f"Raw: {_pct(entry['win_rate'])} over "
                         f"{entry['games']} games, worth "
                         f"{100 * entry['shrink_weight']:.0f}% of face value.")
        # Progress per game rather than the total: two players who gained the
        # same rank over 20 and 80 games are not doing the same thing.
        middle.metric("Rank progress / game",
                      f"{track.get('progress_per_game', 0):+.0f}",
                      help="From the game's own ranked_delta. A subrank spans "
                           "1000 points, so +50 a game is a subrank every "
                           "20 games.")
        right.metric("Games / week",
                     f"{steady.get('games_per_week', 0):.1f}",
                     help=f"Active on {steady.get('active_days', 0)} days; "
                          f"longest gap "
                          f"{steady.get('longest_gap_days', 0):.1f} days.")

        if track.get("badge_change") is not None:
            direction = "up" if track["badge_change"] > 0 else "down"
            st.caption(f"Badge went from {track['badge_first']} to "
                       f"{track['badge_last']} over the window — "
                       f"{abs(track['badge_change'])} {direction}.")
        if track.get("demotion_protected"):
            # The one case where a loss did not cost what it looks like it cost.
            st.caption(f"{track['demotion_protected']} loss(es) absorbed by "
                       f"demotion protection, so the rank is being held up "
                       f"slightly more than the record suggests.")
        if steady.get("days_since_last") is not None and steady["days_since_last"] > 7:
            st.warning(f"Last seen {steady['days_since_last']:.0f} days ago.")

        heroes = entry.get("heroes") or []
        if heroes:
            st.caption(
                f"Plays about **{entry.get('effective_heroes', 0):.1f}** heroes "
                f"effectively — top three are "
                f"{100 * (entry.get('top3_share') or 0):.0f}% of their games. "
                f"(A one-trick scores 1.0; an even four-hero pool scores 4.0.)")
            st.dataframe(_hero_frame(heroes, hero_names),
                         hide_index=True, width="stretch")
        else:
            st.caption("No hero data in this window.")


def render(rank_choices=None, hero_names=None, badge_label=None) -> None:
    """
    The Climb page.

    Takes its lookups as arguments rather than importing deadlock.py, so the
    view stays renderable from a test or a different front end -- the same reason
    objectives_view does.
    """
    rank_choices = rank_choices or []
    hero_names = hero_names or {}

    st.title("Climb")
    st.caption("Find people at your rank worth queueing with.")
    with st.expander("Why this does not just sort by win rate", expanded=False):
        st.markdown(PREAMBLE)

    try:
        import server as scout_server
    except Exception as error:
        st.error(f"Could not load the server client: {error}")
        return
    if not scout_server.configured():
        st.info("This needs the scouting server — it reads the ladder "
                "scoreboard and per-account histories, which the app does not "
                "do directly. See DEADLOCK_SERVER in the README.")
        return

    # ---------------------------------------------------------- the scan
    st.subheader("1. Scan a rank")
    top = st.columns([2, 1, 1, 1])
    if rank_choices:
        labels = [label for label, _ in rank_choices]
        default = next((i for i, (label, _) in enumerate(rank_choices)
                        if "Phantom" in label), len(labels) // 2)
        chosen = top[0].selectbox("Your rank", labels, index=default,
                                  key="climb_rank")
        badge = dict((label, value) for label, value in rank_choices)[chosen]
    else:
        badge = top[0].number_input("Your badge (tier × 10 + subrank)",
                                    min_value=0, max_value=120, value=91,
                                    key="climb_badge")
    spread = top[1].slider("Band", 0, 40, 10, key="climb_spread",
                           help="Badge points either side. 10 is one full tier "
                                "each way — wide enough to find people, narrow "
                                "enough that they are in lobbies like yours.")
    days = top[2].number_input("Days", min_value=1, max_value=365, value=14,
                               key="climb_days")
    min_games = top[3].number_input("Min games", min_value=1, max_value=500,
                                    value=10, key="climb_min_games",
                                    help="A gate, not a quality bar. At 10 "
                                         "games a win rate is almost entirely "
                                         "the prior.")

    lower = st.columns([2, 1, 1, 1])
    sort_label = lower[0].selectbox("Order by", list(SORTS), index=0,
                                    key="climb_sort")
    per_week = lower[1].number_input("Min games/week", min_value=0.0,
                                     max_value=100.0, value=0.0, step=1.0,
                                     key="climb_per_week")
    limit = lower[2].number_input("Show", min_value=5, max_value=200, value=50,
                                  key="climb_limit")
    # Both by default: a personaname is not unique and changes at will, so it
    # labels a row while the id is what you copy, paste and look up.
    how = lower[3].radio("Show as", ("Both", "Persona", "Account ID"), index=0,
                         key="climb_label",
                         help="Personanames come from Steam and are display "
                              "only — two players can share one. The id is "
                              "what identifies an account.")

    if st.button("Scan", type="primary", key="climb_scan"):
        with st.spinner("Reading the band…"):
            try:
                st.session_state["climb_found"] = scout_server.find_players(
                    badge=int(badge), spread=int(spread), days=int(days),
                    min_games=int(min_games),
                    min_games_per_week=float(per_week),
                    sort_by=SORTS[sort_label], limit=int(limit))
            except Exception as error:
                st.error(f"Scan failed: {error}")
                st.session_state.pop("climb_found", None)

    found = st.session_state.get("climb_found")
    if not found:
        return

    rows = found.get("candidates") or []
    band = ""
    if badge_label:
        try:
            band = (f" ({badge_label(found['badge_low'])} – "
                    f"{badge_label(found['badge_high'])})")
        except Exception:
            band = ""
    st.caption(
        f"Badge {found.get('badge_low')}–{found.get('badge_high')}{band} · "
        f"{found.get('pool_size', 0):,} accounts in the band, "
        f"{found.get('eligible', 0):,} with enough games "
        f"({found.get('skipped_too_few', 0):,} too few) · "
        f"{found.get('upstream_calls', 0)} upstream call(s)")
    _reliability(found.get("reliability") or {})

    if not rows:
        st.warning("Nobody in that band cleared the gates. Widen the band, "
                   "lengthen the window, or drop the minimum games.")
        return

    if found.get("label_problems"):
        # A label lookup failing must not look like a broken scan.
        for problem in found["label_problems"]:
            st.caption(f"⚠ {_safe(problem)} — that column is blank; "
                       f"the scan itself is unaffected.")
    st.dataframe(_candidates_frame(rows, how, badge_label),
                 hide_index=True, width="stretch")
    unranked = sum(1 for row in rows if row.get("badge") is None)
    if unranked:
        st.caption(f"{unranked} of {len(rows)} have no ranked badge on record — "
                   f"they play in these lobbies without a rank of their own.")

    # ---------------------------------------------------------- the drill-down
    st.subheader("2. Look closer")
    st.caption("Two calls per account, so pick a shortlist. This is where the "
               "hero pool, the ranked win rate over a longer period and the "
               "rank trajectory come from.")

    # The options stay account ids -- they are what the profile call takes -- and
    # format_func does the labelling, so switching the toggle cannot change which
    # accounts are selected.
    ids = [row["account_id"] for row in rows]
    shown = {row["account_id"]: _label(row, how) for row in rows}
    picks = st.multiselect("Accounts", ids, default=ids[:5],
                           max_selections=25, key="climb_picks",
                           format_func=lambda a: shown.get(a, str(a)))
    closer = st.columns([1, 1, 2])
    long_days = closer[0].number_input(
        "Days of history", min_value=7, max_value=365, value=90,
        key="climb_long_days",
        help="Longer than the scan on purpose. A win rate needs the games; the "
             "activity numbers are counts and hold up at any length.")
    with_history = closer[1].checkbox(
        "Rank trajectory", value=True, key="climb_history",
        help="Off skips the per-account calls: hero tables only, no trajectory "
             "and no confirmed badge.")

    if st.button("Look closer", key="climb_profile", disabled=not picks):
        with st.spinner(f"Reading {len(picks)} account(s)…"):
            try:
                st.session_state["climb_profiles"] = scout_server.profile_players(
                    picks, days=int(long_days), include_history=with_history)
            except Exception as error:
                st.error(f"Could not read those accounts: {error}")
                st.session_state.pop("climb_profiles", None)

    got = st.session_state.get("climb_profiles")
    if not got:
        return

    summary = got.get("summary") or {}
    if summary.get("order_changed_by_shrinking"):
        # The shrinking earning its keep, stated rather than implied.
        st.caption(
            f"{summary['order_changed_by_shrinking']} of "
            f"{summary.get('profiled', 0)} would be ranked differently by raw "
            f"win rate — that disagreement is the correction working.")
    if summary.get("climbing") is not None:
        st.caption(f"{summary['climbing']} of {summary.get('profiled', 0)} are "
                   f"gaining rank over the window.")

    for entry in sorted(got.get("profiles") or [],
                        key=lambda p: -((p.get("trajectory") or {})
                                        .get("progress_per_game") or 0)):
        _profile_card(entry, hero_names, how, badge_label)

    for problem in got.get("label_problems") or []:
        st.caption(f"⚠ {_safe(problem)}")
    for missing in got.get("unavailable") or []:
        st.caption(f"Account {missing['account_id']}: {_safe(missing['reason'])}")
