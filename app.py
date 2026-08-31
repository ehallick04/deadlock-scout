"""
app.py — the Deadlock scouting report as a web app.

Another runner on top of deadlock.py, exactly like main.py. No HTTP and no
game logic lives here; this file only collects input and displays results.

Run locally:
    uv pip install streamlit pandas
    streamlit run app.py

Deploy free: push this folder to GitHub, then connect it at share.streamlit.io
"""

import pandas as pd
import streamlit as st

from api import cache_info, clear_cache
from deadlock import (
    CUSTOMS_ONLY, DEFAULT_DAYS, DEFAULT_GAME_MODE, DEFAULT_MATCH_MODE,
    WITH_CUSTOMS, build_report, build_team_report, flatten, hero_totals,
    parse_ids,
)
from teams import TEAMS, choices, roster

st.set_page_config(page_title="Deadlock Scout", page_icon="🔒", layout="wide")

MATCH_MODES = [
    "private_lobby",
    "ranked,unranked,private_lobby",
    "ranked,unranked",
    "ranked",
    "unranked",
    "coop_bot",
]

CUSTOM_ENTRY = "Custom — paste IDs"


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


# --------------------------------------------------------------- sidebar

st.sidebar.header("Who")

preset = st.sidebar.selectbox(
    "Roster",
    [CUSTOM_ENTRY, *choices()],
    help="Pros presets pull custom-lobby games, where teams scrim.",
)

labels = {}
if preset == CUSTOM_ENTRY:
    raw = st.sidebar.text_area(
        "Account IDs, friend codes, or statlocker URLs",
        height=150,
        placeholder="880934744\nhttps://statlocker.gg/profile/1170456491/matches",
        help="One per line. Steam friend codes work directly.",
    )
    uploaded = st.sidebar.file_uploader("...or upload a list", type=["txt", "csv"])
    if uploaded is not None:
        raw = (raw or "") + "\n" + uploaded.getvalue().decode("utf-8", errors="replace")
    ids = parse_ids((raw or "").replace(",", " ").split())
    default_mode = WITH_CUSTOMS
else:
    ids, labels = roster(preset)
    st.sidebar.success(f"{preset}: {len(ids)} players")
    default_mode = CUSTOMS_ONLY

st.sidebar.header("Filters")

days = whole_number(
    st.sidebar.text_input("Days to look back", value=str(DEFAULT_DAYS)),
    DEFAULT_DAYS, "Days")

top = whole_number(
    st.sidebar.text_input("Heroes per player", value="5"),
    5, "Heroes per player", minimum=1, maximum=50)

match_mode = st.sidebar.selectbox(
    "Match mode", MATCH_MODES, index=MATCH_MODES.index(default_mode))
game_mode = st.sidebar.selectbox("Game mode", ["normal", "street_brawl"], index=0)

together = st.sidebar.checkbox(
    "Only games they played together",
    value=(preset != CUSTOM_ENTRY),
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
    if st.button("Clear cache", use_container_width=True):
        removed = clear_cache()
        st.cache_data.clear()
        st.success(f"Removed {removed} entries. Rebuild to pull live data.")


# --------------------------------------------------------------- main

st.title("Deadlock Scouting Report")
st.caption("Most-played heroes, win rates, and ranks — from the community Deadlock API.")

if not ids:
    st.info("Pick a roster, or paste player IDs in the sidebar to begin.")
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
c1.metric("Players", df["account_id"].nunique())
c2.metric("Matches", int(df["matches"].sum()))
c3.metric("Distinct heroes", df["hero"].nunique())
c4.metric("Overall win rate",
          f"{df['wins'].sum() / df['matches'].sum() * 100:.1f}%")

if meta:
    if meta.get("subs"):
        st.warning(f"**{len(meta['subs'])} stand-in(s)** detected across "
                   f"{meta['matches_inspected']} matches — shown with a SUB tag.")
    st.info(f"**Team games only** — {meta['shared_matches']} matches with at "
            f"least {meta['min_players']} of the selected players. "
            f"Stack sizes across all their customs: "
            + ", ".join(f"{n} players in {c} matches"
                        for n, c in meta["stack_sizes"].items()))

WINRATE_COL = st.column_config.ProgressColumn(
    "Win rate", format="%.1f%%", min_value=0, max_value=100)

tab_hero, tab_player, tab_team, tab_data = st.tabs(
    ["By hero", "By player", "By team", "Raw data"])

# ---- pooled hero win rates
with tab_hero:
    st.caption("Every hero in the group's top picks, pooled across players.")
    totals = pd.DataFrame(hero_totals(players))
    st.dataframe(
        totals, hide_index=True, use_container_width=True,
        column_config={
            "hero": "Hero",
            "players": st.column_config.NumberColumn("Players"),
            "matches": st.column_config.NumberColumn("Played"),
            "wins": st.column_config.NumberColumn("Wins"),
            "win_rate": WINRATE_COL,
        },
    )

# ---- per player
with tab_player:
    for p in players:
        who = p.get("persona_name") or f"Account {p['account_id']}"
        if p.get("ign") and p["ign"] != "sub":
            who = p["ign"]
        if p.get("is_sub"):
            who = f"{who}  ⟨SUB⟩"
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
        for team in [t for t in df["team"].unique() if t]:
            sub = df[df["team"] == team]
            agg = (sub.groupby("hero", as_index=False)
                      .agg(players=("account_id", "nunique"),
                           matches=("matches", "sum"),
                           wins=("wins", "sum")))
            agg["win_rate"] = (agg["wins"] / agg["matches"] * 100).round(1)
            agg = agg.sort_values("matches", ascending=False)

            wr = sub["wins"].sum() / sub["matches"].sum() * 100
            st.subheader(f"{team} — {int(sub['matches'].sum())} matches, {wr:.1f}% win rate")
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

# ---- everything, downloadable
with tab_data:
    st.dataframe(df, hide_index=True, use_container_width=True)
    safe = preset.replace(" ", "_").replace("—", "").lower()
    st.download_button(
        "Download CSV",
        df.to_csv(index=False).encode("utf-8"),
        file_name=f"deadlock_{safe}_{days}d.csv",
        mime="text/csv",
    )
