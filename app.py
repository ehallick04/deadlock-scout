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
    CUSTOMS_ONLY, DEFAULT_DAYS, DEFAULT_GAME_MODE, DEFAULT_MATCH_MODE,
    WITH_CUSTOMS, build_report, build_team_report, composition_counts,
    ABILITY_STYLES, ability_order, ability_rows, ability_slots,
    build_summary, buy_order, custom_match_ids, match_build_order,
    match_builds, match_builds_bulk, metadata_report,
    top_heroes_for, typical_builds,
    buy_order_by_player, flatten,
    flow_edges, flow_rows, hero_names, hero_totals, item_flow, match_compositions,
    parse_ids, phase_label,
)
from roster_import import BOOKMARKLET, HARVESTER, find_teams, parse_any
import store
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


# --------------------------------------------------------------- sidebar

st.sidebar.header("Who")

# A checklist rather than a dropdown. Rules:
#   - Custom (pasted ids) can be combined with anything.
#   - Ticking individual teams hides the All / NA / EU shortcuts, since
#     those would only overlap with what you already picked.
ALL_TEAMS = sorted(PINNED, key=str.lower)

use_custom = st.sidebar.checkbox("Custom — paste IDs below", value=False)

st.sidebar.markdown("**Teams**")
picked_teams = [name for name in ALL_TEAMS
                if st.sidebar.checkbox(
                    f"{name}  ·  {TEAMS[name].get('region', '')}",
                    key=f"team_{name}")]

# Everything from rosters.json. 180 checkboxes would be unusable, so these
# get a searchable multiselect instead -- ticked ones join picked_teams and
# behave identically from here on.
league_picks = []
if LEAGUE:
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
else:
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

st.title("Deadlock Scouting Report")
st.caption("Most-played heroes, win rates, and ranks — from the community Deadlock API.")

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
    st.info(f"**Team games only** — {meta['shared_matches']} matches with at "
            f"least {meta['min_players']} of the selected players. "
            f"Stack sizes across all their customs: "
            + ", ".join(f"{n} players in {c} matches"
                        for n, c in meta["stack_sizes"].items()))

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
    try:
        return hero_names()
    except Exception:
        return {}


tab_hero, tab_player, tab_team, tab_match, tab_items, tab_data = st.tabs(
    ["By hero", "By player", "By team", "Matches", "Build order", "Raw data"])

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
            counts = pd.DataFrame(composition_counts(comps, ids))
            if not counts.empty:
                st.markdown("**Heroes they build around**")
                st.dataframe(
                    counts, hide_index=True, use_container_width=True,
                    column_config={
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
                result = ("" if m["winner"] is None else
                          (" — WIN" if m["our_side"] == m["winner"] else " — LOSS"))
                header = f"{ours or 'match'} {result}  ·  {when} {mins}  ·  {m['match_id']}"

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

# ---- everything, downloadable
# ---- build order: what gets bought, when, and in what sequence
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
            in_match = sorted({(r["account_id"], r["player"]) for r in brows
                               if r["match_id"] == pick})
            for account_id, player in in_match:
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