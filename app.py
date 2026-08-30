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

from deadlock import (
    DEFAULT_GAME_MODE, DEFAULT_MATCH_MODE, WITH_CUSTOMS,
    build_report, flatten, parse_ids,
)

st.set_page_config(page_title="Deadlock Scout", page_icon="🔒", layout="wide")

MATCH_MODES = [
    "ranked,unranked",
    "ranked,unranked,private_lobby",
    "private_lobby",
    "ranked",
    "unranked",
    "coop_bot",
]


# Cache results so re-sorting a table doesn't re-hit the API.
# The key is the arguments, so changing any input triggers a fresh pull.
@st.cache_data(ttl=900, show_spinner=False)
def load(ids_tuple, days, top, match_mode, game_mode):
    return build_report(list(ids_tuple), days=days, top=top,
                        match_mode=match_mode, game_mode=game_mode)


# --------------------------------------------------------------- sidebar

st.sidebar.header("Players")
raw = st.sidebar.text_area(
    "Account IDs or statlocker URLs",
    height=160,
    placeholder="880934744\nhttps://statlocker.gg/profile/1170456491/matches",
    help="One per line. Full profile URLs work — the id gets pulled out.",
)

uploaded = st.sidebar.file_uploader("...or upload a list", type=["txt", "csv"])
if uploaded is not None:
    raw = (raw or "") + "\n" + uploaded.getvalue().decode("utf-8", errors="replace")

st.sidebar.header("Filters")
days = st.sidebar.slider("Days to look back", 7, 180, 30, step=7)
top = st.sidebar.slider("Heroes per player", 3, 15, 5)

include_customs = st.sidebar.checkbox("Include custom games", value=True)
match_mode = st.sidebar.selectbox(
    "Match mode",
    MATCH_MODES,
    index=MATCH_MODES.index(WITH_CUSTOMS if include_customs else DEFAULT_MATCH_MODE),
)
game_mode = st.sidebar.selectbox("Game mode", ["normal", "street_brawl"], index=0)

run = st.sidebar.button("Build report", type="primary", use_container_width=True)


# --------------------------------------------------------------- main

st.title("Deadlock Scouting Report")
st.caption("Most-played heroes, win rates, and ranks — from the community Deadlock API.")

ids = parse_ids((raw or "").replace(",", " ").split())

if not ids:
    st.info("Paste player IDs or statlocker profile URLs in the sidebar to begin.")
    st.stop()

st.write(f"**{len(ids)} player(s)** · last {days} days · `{match_mode}` · `{game_mode}`")

if not run and "players" not in st.session_state:
    st.stop()

if run:
    with st.spinner(f"Pulling data for {len(ids)} player(s)..."):
        try:
            st.session_state.players = load(tuple(ids), days, top, match_mode, game_mode)
            st.session_state.meta = (days, match_mode, game_mode)
        except Exception as e:
            st.error(f"Could not reach the Deadlock API: {e}")
            st.stop()

players = st.session_state.get("players", [])
rows = flatten(players)

if not rows:
    st.warning("No matches found for these players in this window. "
               "Try a longer time window, or a different match mode.")
    st.stop()

df = pd.DataFrame(rows)

# ---- summary numbers
c1, c2, c3, c4 = st.columns(4)
c1.metric("Players", df["account_id"].nunique())
c2.metric("Matches", int(df["matches"].sum()))
c3.metric("Distinct heroes", df["hero"].nunique())
c4.metric("Overall win rate",
          f"{df['wins'].sum() / df['matches'].sum() * 100:.1f}%")

tab_players, tab_heroes, tab_data = st.tabs(["By player", "By hero", "Raw data"])

# ---- per player
with tab_players:
    for p in players:
        name = p.get("persona_name") or f"Account {p['account_id']}"
        header = f"**{name}** — {p['rank_label']} · {p['total_matches']} matches"
        with st.expander(header, expanded=len(players) <= 4):
            if not p["heroes"]:
                st.write("No matches in this window.")
                continue
            st.dataframe(
                pd.DataFrame(p["heroes"])[["hero", "matches", "wins", "win_rate"]],
                hide_index=True,
                use_container_width=True,
                column_config={
                    "hero": "Hero",
                    "matches": st.column_config.NumberColumn("Played"),
                    "wins": st.column_config.NumberColumn("Wins"),
                    "win_rate": st.column_config.ProgressColumn(
                        "Win rate", format="%.1f%%", min_value=0, max_value=100),
                },
            )

# ---- aggregated across everyone
with tab_heroes:
    agg = (df.groupby("hero", as_index=False)
             .agg(players=("account_id", "nunique"),
                  matches=("matches", "sum"),
                  wins=("wins", "sum")))
    agg["win_rate"] = (agg["wins"] / agg["matches"] * 100).round(1)
    agg = agg.sort_values("matches", ascending=False)

    st.caption("Every hero in the group's top picks, pooled.")
    st.dataframe(
        agg, hide_index=True, use_container_width=True,
        column_config={
            "hero": "Hero",
            "players": st.column_config.NumberColumn("Players"),
            "matches": st.column_config.NumberColumn("Played"),
            "wins": st.column_config.NumberColumn("Wins"),
            "win_rate": st.column_config.ProgressColumn(
                "Win rate", format="%.1f%%", min_value=0, max_value=100),
        },
    )

# ---- everything, downloadable
with tab_data:
    st.dataframe(df, hide_index=True, use_container_width=True)
    st.download_button(
        "Download CSV",
        df.to_csv(index=False).encode("utf-8"),
        file_name=f"deadlock_report_{days}d.csv",
        mime="text/csv",
    )
