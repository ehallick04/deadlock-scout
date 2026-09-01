"""
deadlock.py — what the data MEANS. Ranks, heroes, players, win rates.

Every function here RETURNS data. None of them print. That's what makes
them reusable: main.py prints them, a future script could chart them or
write them to a database, and neither one has to change this file.

    from deadlock import build_report, rank_name
"""

import os
import re
import time
import urllib.error
from collections import Counter

from api import cache_info, clear_cache, get_json, get_cached

HEROES_FILE = "heroes.json"

# Rank tiers in ascending order: rank 1 = Initiate ... rank 11 = Eternus.
# Valve's own names are at /v1/assets/ranks if a patch ever renames these.
RANKS = [
    "Initiate", "Seeker", "Acolyte", "Sentinel", "Mystic", "Ritualist",
    "Emissary", "Oracle", "Phantom", "Ascendant", "Eternus",
]

# Steam has two id formats. The API speaks SteamID3 (the short one in a
# statlocker URL); SteamID64 is the 17-digit one you see on Steam itself.
STEAMID64_BASE = 76561197960265728

# player_match_outcome values, per the API docs
WIN, LOSS = 1, 2

# What counts as a "real" match. These are the API's own defaults, spelled
# out here so the choice is visible rather than implied.
#   game_mode:  normal | street_brawl
#   match_mode: unranked | private_lobby | coop_bot | ranked |
#               server_test | tutorial | hero_labs
# private_lobby is custom games; coop_bot is vs AI. Both excluded by default.
DEFAULT_GAME_MODE = "normal"
DEFAULT_MATCH_MODE = "ranked,unranked"

# Same, plus custom games. Make this the DEFAULT_MATCH_MODE value above if
# you want customs counted everywhere without passing a flag.
WITH_CUSTOMS = "ranked,unranked,private_lobby"
CUSTOMS_ONLY = "private_lobby"

# Pro scrims happen in custom lobbies and turn over fast, so two weeks is
# the useful default rather than a month.
DEFAULT_DAYS = 14


# --------------------------------------------------------------- inputs

def parse_ids(items):
    """
    Pull account ids out of whatever you throw at this: bare numbers,
    statlocker profile URLs, comma- or space-separated lists.

        parse_ids(["https://statlocker.gg/profile/880934744/matches?mode=standard"])
        -> [880934744]
    """
    ids = []
    for item in items:
        text = str(item)
        m = re.search(r"/profile/(\d+)", text)      # a profile URL
        if m:
            ids.append(int(m.group(1)))
            continue
        for n in re.findall(r"\d{5,}", text):       # any account-id-looking number
            ids.append(int(n))
    return list(dict.fromkeys(ids))                 # de-duplicate, keep order


def read_id_file(path):
    """One id or URL per line (or space separated)."""
    with open(path, encoding="utf-8") as f:
        return parse_ids(f.read().split())


# --------------------------------------------------------------- ranks

def rank_name(rank, subrank=None):
    """9, 5 -> 'Phantom 5'. The subrank stays a number."""
    if rank is None:
        return "Unknown"
    if rank <= 0:
        return "Unranked"
    tier = RANKS[rank - 1] if rank <= len(RANKS) else f"Rank {rank}"
    return f"{tier} {subrank}" if subrank else tier


def to_steamid64(account_id):
    """SteamID3 -> SteamID64.  880934744 -> 76562078841200472"""
    return int(account_id) + STEAMID64_BASE


def steam_profiles(account_ids):
    """
    Display names for a batch of players. -> {account_id: SteamProfile dict}
    One call for the whole list; 100 req/s, so this is cheap.
    """
    try:
        profiles = get_json(
            "/v1/players/steam",
            account_ids=",".join(str(i) for i in account_ids),
        )
    except urllib.error.HTTPError as e:
        print(f"steam profiles unavailable (HTTP {e.code}); names will be blank")
        return {}
    return {p["account_id"]: p for p in profiles}


def get_rank(account_id):
    """-> {'account_id', 'rank_label', 'rank', 'subrank', 'badge'}"""
    try:
        r = get_json(f"/v1/players/{account_id}/rank")
    except urllib.error.HTTPError as e:
        return {"account_id": account_id, "rank_label": f"none (HTTP {e.code})",
                "rank": None, "subrank": None, "badge": None}
    return {
        "account_id": account_id,
        "rank_label": rank_name(r.get("rank"), r.get("subrank")),
        "rank": r.get("rank"),
        "subrank": r.get("subrank"),
        "badge": r.get("badge"),
    }


# --------------------------------------------------------------- heroes

def heroes(refresh=False):
    """The full hero asset list, cached to heroes.json."""
    return get_cached("/v1/assets/heroes", HEROES_FILE, refresh=refresh)


def hero_names(refresh=False):
    """{hero_id: name}"""
    return {h["id"]: h.get("name") or h.get("class_name") for h in heroes(refresh)}


# --------------------------------------------------------------- hero stats

def hero_stats(account_ids, days=30,
               match_mode=DEFAULT_MATCH_MODE, game_mode=DEFAULT_GAME_MODE):
    """
    Per-hero matches_played / wins for these players over the last `days`.
    ONE batched call — /v1/players/hero-stats accepts multiple account_ids.

        hero_stats(ids, match_mode="ranked")                  # ranked only
        hero_stats(ids, match_mode="private_lobby")           # customs only
        hero_stats(ids, game_mode="street_brawl")             # brawl instead
    """
    since = int(time.time()) - days * 86400
    return get_json(
        "/v1/players/hero-stats",
        account_ids=",".join(str(i) for i in account_ids),
        min_unix_timestamp=since,
        match_mode=match_mode,
        game_mode=game_mode,
    )


def hero_stats_from_history(account_id, days=30, match_mode_ids=None,
                            report_modes=True):
    """
    Fallback: aggregate the player's own match history locally.
    Uses player_match_outcome (1 = win, 2 = loss), which correctly
    excludes abandoned and unscored games.

    WARNING: match-history returns EVERY mode, including custom and bot
    games, and reports match_mode as an integer rather than a name. The
    integer meanings are not documented, so this does not filter by mode
    unless you pass match_mode_ids yourself. With report_modes=True it
    prints which values it saw, so you can work out the mapping by
    comparing against a known match.
    """
    since = int(time.time()) - days * 86400
    history = get_json(f"/v1/players/{account_id}/match-history")

    seen_modes = {}
    tally = {}
    for m in history:
        if m.get("start_time", 0) < since:
            continue

        mode = m.get("match_mode")
        seen_modes[mode] = seen_modes.get(mode, 0) + 1
        if match_mode_ids is not None and mode not in match_mode_ids:
            continue

        outcome = m.get("player_match_outcome")
        if outcome not in (WIN, LOSS):
            continue
        row = tally.setdefault(m["hero_id"], {"account_id": account_id,
                                              "hero_id": m["hero_id"],
                                              "matches_played": 0, "wins": 0})
        row["matches_played"] += 1
        row["wins"] += 1 if outcome == WIN else 0

    if report_modes and seen_modes:
        breakdown = ", ".join(f"match_mode={k}: {v}" for k, v in sorted(
            seen_modes.items(), key=lambda kv: -kv[1]))
        print(f"  [{account_id}] modes in window -> {breakdown}")

    return list(tally.values())


# --------------------------------------------------------------- report

def build_report(account_ids, days=DEFAULT_DAYS, top=5, use_history=False,
                 match_mode=DEFAULT_MATCH_MODE, game_mode=DEFAULT_GAME_MODE,
                 labels=None):
    """
    The whole thing, as DATA:

        [{'account_id': 880934744, 'rank_label': 'Phantom 5', 'rank': 9,
          'subrank': 5, 'total_matches': 65,
          'heroes': [{'hero': 'Bebop', 'hero_id': 15, 'matches': 42,
                      'wins': 25, 'win_rate': 59.5}, ...]}, ...]

    `labels` optionally maps account_id -> {"ign", "team", "region"}, so a
    known roster name can sit alongside whatever Steam persona is set today.

    Printing is main.py's job.
    """
    labels = labels or {}
    names = hero_names()
    profiles = steam_profiles(account_ids)

    if use_history:
        stats = [s for i in account_ids for s in hero_stats_from_history(i, days)]
    else:
        try:
            stats = hero_stats(account_ids, days, match_mode, game_mode)
        except urllib.error.HTTPError as e:
            print(f"hero-stats returned HTTP {e.code}; falling back to match history")
            stats = [s for i in account_ids for s in hero_stats_from_history(i, days)]

    by_player = {}
    for s in stats:
        by_player.setdefault(s["account_id"], []).append(s)

    players = []
    for account_id in account_ids:
        info = get_rank(account_id)
        played = sorted(by_player.get(account_id, []),
                        key=lambda s: -s.get("matches_played", 0))

        def _r(s):
            n, w = s.get("matches_played", 0), s.get("wins", 0)
            return {
                "hero": names.get(s["hero_id"], f"hero {s['hero_id']}"),
                "hero_id": s["hero_id"],
                "matches": n,
                "wins": w,
                "win_rate": round(w / n * 100, 1) if n else 0.0,
            }

        all_rows = [_r(s) for s in played]
        hero_rows = all_rows[:top]

        profile = profiles.get(account_id, {})
        label = labels.get(account_id, {})
        players.append({
            "account_id": account_id,
            "ign": label.get("ign", ""),
            "team": label.get("team", ""),
            "region": label.get("region", ""),
            "persona_name": profile.get("personaname", ""),
            "steam_id64": to_steamid64(account_id),
            "profile_url": profile.get("profileurl", ""),
            "rank_label": info["rank_label"],
            "rank": info["rank"],
            "subrank": info["subrank"],
            "badge": info["badge"],
            "total_matches": sum(s.get("matches_played", 0) for s in played),
            "heroes": hero_rows,
            "all_heroes": all_rows,
        })

    return players


def flatten(players):
    """Nested report -> flat rows, one per player+hero. For CSV or pandas."""
    return [
        {"account_id": p["account_id"],
         "row_key": p.get("row_key", str(p["account_id"])),
         "ign": p.get("ign", ""),
         "team": p.get("team", ""),
         "sub_for": p.get("sub_for", ""),
         "home_team": p.get("home_team", ""),
         "region": p.get("region", ""),
         "is_sub": p.get("is_sub", False),
         "persona_name": p.get("persona_name", ""),
         "rank": p["rank_label"],
         "rank_num": p["rank"], "subrank": p["subrank"], **h}
        for p in players for h in p["heroes"]
    ]


def hero_totals(players, normalize=True, min_games=1):
    """
    Hero usage pooled across everyone in the report.

    Raw totals let one high-volume player dominate: someone with 40 games
    contributes five times what a player with 8 does. `pick_share` fixes
    that by asking each player what FRACTION of their own games were on a
    hero, then averaging those fractions - every player gets one equal
    vote regardless of how much they played.

    -> [{'hero', 'players', 'matches', 'wins', 'win_rate',
         'pick_share', 'avg_win_rate'}]

    win_rate      pooled, weighted by games (the raw view)
    avg_win_rate  mean of each player's own win rate (the equal-weight view)
    pick_share    mean share of a player's games spent on this hero, %
    min_games     ignore a player's contribution to a hero below this many
                  games, which keeps 1-game 0%/100% noise out of averages
    """
    pool, shares, rates = {}, {}, {}
    contributing = 0

    for p in players:
        rows = p.get("all_heroes") or p.get("heroes") or []
        total = sum(r["matches"] for r in rows)
        if not total:
            continue
        contributing += 1

        for r in rows:
            row = pool.setdefault(r["hero"], {"hero": r["hero"],
                                              "players": set(),
                                              "matches": 0, "wins": 0})
            row["players"].add(p.get("row_key", p["account_id"]))
            row["matches"] += r["matches"]
            row["wins"] += r["wins"]

            shares.setdefault(r["hero"], []).append(r["matches"] / total)
            if r["matches"] >= min_games:
                rates.setdefault(r["hero"], []).append(r["win_rate"])

    out = []
    for hero, row in pool.items():
        picks = shares.get(hero, [])
        wr = rates.get(hero, [])
        out.append({
            "hero": hero,
            "players": len(row["players"]),
            "matches": row["matches"],
            "wins": row["wins"],
            "win_rate": round(row["wins"] / row["matches"] * 100, 1)
            if row["matches"] else 0.0,
            # divide by EVERY contributing player, not just those who
            # picked the hero, so shares across all heroes sum to 100%
            "pick_share": round(sum(picks) / contributing * 100, 1)
            if contributing else 0.0,
            "avg_win_rate": round(sum(wr) / len(wr), 1) if wr else None,
        })

    key = "pick_share" if normalize else "matches"
    return sorted(out, key=lambda r: -r[key])


# =====================================================================
# Team games: matches where a roster actually played TOGETHER
# =====================================================================
#
# /v1/players/hero-stats returns a `matches` list of match ids alongside
# each hero row. Collect those per player, count how many roster members
# appear in each match id, and keep only the ids that several of them
# share. Those are the games the team played together, as opposed to pugs
# and inhouses with strangers.

def custom_match_ids(account_ids, days=DEFAULT_DAYS, match_mode=CUSTOMS_ONLY):
    """-> {account_id: set(match_id)} for the window."""
    stats = hero_stats(account_ids, days, match_mode=match_mode)
    out = {}
    for s in stats:
        out.setdefault(s["account_id"], set()).update(s.get("matches") or [])
    return out


def shared_match_ids(by_player, min_players=4):
    """
    -> (set of match ids with at least min_players roster members,
        Counter of match_id -> how many roster members were in it)
    """
    counts = Counter()
    for ids in by_player.values():
        counts.update(ids)
    return {mid for mid, n in counts.items() if n >= min_players}, counts


def _team_block(members, team_label, region, days, top, min_players,
                match_mode, include_subs, min_sub_games, by_player, names,
                labels=None):
    """
    One team's report. `members` are that team's roster ids ONLY, so a pro
    from another team who stands in here is correctly seen as an outsider.
    """
    from teams import find_player

    labels = labels or {}
    own = {aid: by_player.get(aid, set()) for aid in members}
    shared, counts = shared_match_ids(own, min_players)

    subs, participants = {}, {}
    rows = list(members)
    sub_meta = {}

    if include_subs and shared:
        participants = match_participants(shared)
        subs = find_subs(participants, members, min_sub_games)
        for aid, row in sorted(subs.items(), key=lambda kv: -kv[1]["games"]):
            rows.append(aid)
            by_player[aid] = by_player.get(aid, set()) | row["with"]
            home = find_player(aid)
            sub_meta[aid] = {
                "ign": home["ign"] if home else "",
                "home_team": home["team"] if home else "",
            }

    since = int(time.time()) - days * 86400
    players = []

    for account_id in rows:
        is_sub = account_id in subs
        own_customs = by_player.get(account_id, set())
        own_shared = (subs[account_id]["with"] if is_sub
                      else own_customs & shared)

        tally = {}
        if own_shared:
            try:
                history = get_json(f"/v1/players/{account_id}/match-history")
            except urllib.error.HTTPError:
                history = []
            for m in history:
                if m.get("match_id") not in own_shared:
                    continue
                if m.get("start_time", 0) < since:
                    continue
                outcome = m.get("player_match_outcome")
                if outcome not in (WIN, LOSS):
                    continue
                row = tally.setdefault(m["hero_id"], {"hero_id": m["hero_id"],
                                                      "matches_played": 0,
                                                      "wins": 0})
                row["matches_played"] += 1
                row["wins"] += 1 if outcome == WIN else 0

        played = sorted(tally.values(), key=lambda r: -r["matches_played"])

        def _row(r):
            n, w = r["matches_played"], r["wins"]
            return {
                "hero": names.get(r["hero_id"], f"hero {r['hero_id']}"),
                "hero_id": r["hero_id"],
                "matches": n,
                "wins": w,
                "win_rate": round(w / n * 100, 1) if n else 0.0,
            }

        all_rows = [_row(r) for r in played]     # every hero, for aggregates
        hero_rows = all_rows[:top]               # trimmed, for display

        info = get_rank(account_id)
        meta = sub_meta.get(account_id, {})

        # Roster name first (teams.py), then anything passed in labels,
        # and only fall back to the Steam persona if we know neither.
        home = find_player(account_id)
        ign = (labels.get(account_id, {}).get("ign")
               or meta.get("ign")
               or (home["ign"] if home else ""))

        players.append({
            "account_id": account_id,
            # a player can appear once per team context, so identity is the
            # PAIR of account and team - not the account alone
            "row_key": f"{account_id}@{team_label}" + ("+sub" if is_sub else ""),
            "ign": ign,
            "team": f"{team_label} (sub)" if is_sub else team_label,
            "sub_for": team_label if is_sub else "",
            "home_team": meta.get("home_team", "") if is_sub else team_label,
            "region": region,
            "is_sub": is_sub,
            "rank_label": info["rank_label"],
            "rank": info["rank"],
            "subrank": info["subrank"],
            "badge": info["badge"],
            "total_matches": sum(r["matches_played"] for r in played),
            "custom_matches": len(own_customs),
            "team_matches": len(own_shared),
            "heroes": hero_rows,
            "all_heroes": all_rows,
        })

    return players, shared, counts, subs, len(participants)


def build_team_report(account_ids, days=DEFAULT_DAYS, top=5, min_players=4,
                      labels=None, match_mode=CUSTOMS_ONLY, include_subs=False,
                      min_sub_games=1):
    """
    Matches where a roster played together, computed PER TEAM.

    Running per team matters once more than one team is selected: a pro who
    stands in for another team must be an outsider relative to THAT team,
    otherwise their sub games get absorbed into their own team's numbers and
    no sub is ever reported. It also means one person can legitimately appear
    twice - once for their own team, once as a stand-in elsewhere - as
    separate rows rather than one merged player.
    """
    labels = labels or {}
    names = hero_names()

    # group the selection by team
    groups = {}
    for aid in account_ids:
        groups.setdefault(labels.get(aid, {}).get("team", ""), []).append(aid)

    by_player = custom_match_ids(account_ids, days, match_mode)

    all_players, all_shared, all_counts, all_subs = [], set(), Counter(), {}
    inspected = 0

    for team_label, members in groups.items():
        region = next((labels[a].get("region", "") for a in members
                       if labels.get(a)), "")
        players, shared, counts, subs, n = _team_block(
            members, team_label, region, days, top, min_players, match_mode,
            include_subs, min_sub_games, by_player, names, labels)

        all_players += players
        all_shared |= shared
        all_counts.update(counts)
        inspected += n
        for aid, row in subs.items():
            all_subs.setdefault((aid, team_label), row["games"])

    # names are looked up once, for everyone in the final report
    profiles = steam_profiles([p["account_id"] for p in all_players])
    for p in all_players:
        p["persona_name"] = profiles.get(p["account_id"], {}).get("personaname", "")

    meta = {
        "shared_matches": len(all_shared),
        "min_players": min_players,
        "stack_sizes": dict(sorted(Counter(all_counts.values()).items(),
                                   reverse=True)),
        "subs": {f"{aid}@{team}": n for (aid, team), n in all_subs.items()},
        "matches_inspected": inspected,
        "teams": list(groups),
        "match_ids": sorted(all_shared),
    }
    return all_players, meta


# =====================================================================
# Substitutes: who filled in when a roster player was missing
# =====================================================================
#
# A custom lobby has two teams. Every player on the OTHER team is also
# "not on the roster", so we cannot just call every unknown account a
# sub. For each match we work out which side most roster members are on,
# then anyone else on THAT side is a stand-in.

def _walk_dicts(node):
    """Yield every dict nested anywhere inside a JSON structure."""
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk_dicts(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk_dicts(v)


def match_participants(match_ids, limit=60, pause=0.05):
    """
    -> {match_id: {account_id: {"hero_id": int|None, "team": int|None}}}

    Reads /v1/matches/{id}/metadata. The extractor walks the JSON looking
    for any object carrying an account_id, so it survives changes to the
    surrounding shape.
    """
    ids = list(match_ids)[:limit]
    if len(match_ids) > limit:
        print(f"  (only inspecting the first {limit} of {len(match_ids)} matches)")

    out = {}
    for mid in ids:
        try:
            md = get_json(f"/v1/matches/{mid}/metadata", disable_steam="true")
        except urllib.error.HTTPError:
            continue

        players = {}
        for node in _walk_dicts(md):
            aid = node.get("account_id")
            if isinstance(aid, int) and aid > 0:
                players[aid] = {
                    "hero_id": node.get("hero_id"),
                    "team": node.get("team", node.get("player_team")),
                }
        if players:
            out[mid] = players
        time.sleep(pause)
    return out


def find_subs(participants, roster_ids, min_games=1):
    """
    Non-roster players who appeared ON THE ROSTER'S SIDE.
    -> {account_id: {"games": n, "with": set(match_ids)}}
    """
    roster_ids = set(roster_ids)
    found = {}

    for mid, players in participants.items():
        # group the lobby by side
        sides = {}
        for aid, info in players.items():
            sides.setdefault(info.get("team"), []).append(aid)

        if len(sides) < 2:
            continue    # no usable team field; skip rather than guess

        # the side holding the most roster members is "their" side
        our_side = max(sides,
                       key=lambda t: sum(1 for a in sides[t] if a in roster_ids))
        if not any(a in roster_ids for a in sides[our_side]):
            continue

        for aid in sides[our_side]:
            if aid in roster_ids:
                continue
            row = found.setdefault(aid, {"games": 0, "with": set()})
            row["games"] += 1
            row["with"].add(mid)

    return {aid: row for aid, row in found.items() if row["games"] >= min_games}


# =====================================================================
# Match compositions: who played what, on both sides
# =====================================================================

def _find_scalar(node, *names):
    """First value in a nested structure whose key is one of `names`."""
    for d in _walk_dicts(node):
        for name in names:
            v = d.get(name)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return v
    return None


def match_compositions(match_ids, roster_ids=(), labels=None, limit=60,
                       name_lookup=True):
    """
    Full lineups for a set of matches.

    -> [{'match_id', 'start_time', 'duration_s', 'winner', 'our_side',
         'sides': {0: [player...], 1: [player...]}}]
    where player = {'account_id','name','hero','hero_id','is_roster','won'}

    Outcome comes from the match history of whichever roster player was in
    the game, which is more dependable than guessing at a winner field.
    """
    from teams import find_player

    labels = labels or {}
    roster_ids = set(roster_ids)
    ids = sorted(match_ids)[:limit]
    if len(match_ids) > limit:
        print(f"  (showing the first {limit} of {len(match_ids)} matches)")

    participants = match_participants(ids)
    names = hero_names()

    # which side won, learned from a roster player's own record
    outcomes = {}
    for account_id in roster_ids:
        try:
            history = get_json(f"/v1/players/{account_id}/match-history")
        except urllib.error.HTTPError:
            continue
        for m in history:
            mid = m.get("match_id")
            if mid in participants and mid not in outcomes:
                side = participants[mid].get(account_id, {}).get("team")
                result = m.get("player_match_outcome")
                if side is not None and result in (WIN, LOSS):
                    outcomes[mid] = side if result == WIN else (1 - side)

    # names for anyone not on a roster we know
    personas = {}
    if name_lookup:
        unknown = {aid for players in participants.values() for aid in players
                   if aid not in roster_ids and not find_player(aid)}
        if unknown:
            personas = steam_profiles(sorted(unknown))

    out = []
    for mid in ids:
        players = participants.get(mid)
        if not players:
            continue

        sides = {}
        for account_id, info in players.items():
            side = info.get("team")
            home = find_player(account_id)
            name = (labels.get(account_id, {}).get("ign")
                    or (home["ign"] if home else "")
                    or personas.get(account_id, {}).get("personaname", "")
                    or str(account_id))
            sides.setdefault(side, []).append({
                "account_id": account_id,
                "name": name,
                "hero_id": info.get("hero_id"),
                "hero": names.get(info.get("hero_id"), "?"),
                "is_roster": account_id in roster_ids,
                "team": home["team"] if home else "",
            })

        winner = outcomes.get(mid)
        our_side = None
        for side, group in sides.items():
            if any(p["is_roster"] for p in group):
                our_side = side
                break

        for side, group in sides.items():
            group.sort(key=lambda p: (not p["is_roster"], p["name"].lower()))
            for p in group:
                p["won"] = None if winner is None else (side == winner)

        # name a side after whichever roster most of it belongs to
        side_names = {}
        for side, group in sides.items():
            teams_seen = Counter(p["team"] for p in group if p["team"])
            side_names[side] = teams_seen.most_common(1)[0][0] if teams_seen else ""

        out.append({
            "match_id": mid,
            "start_time": _find_scalar(players, "start_time"),
            "duration_s": _find_scalar(players, "duration_s", "match_duration_s"),
            "winner": winner,
            "our_side": our_side,
            "sides": sides,
            "side_names": side_names,
        })

    return out


def composition_counts(compositions, roster_ids=()):
    """
    How often each hero appears on the roster's side, and how it did.
    -> [{'hero','games','wins','win_rate'}] sorted by games.
    """
    roster_ids = set(roster_ids)
    tally = {}
    for m in compositions:
        side = m.get("our_side")
        if side is None:
            continue
        for p in m["sides"].get(side, []):
            row = tally.setdefault(p["hero"], {"hero": p["hero"], "games": 0,
                                               "wins": 0})
            row["games"] += 1
            if p.get("won"):
                row["wins"] += 1
    for row in tally.values():
        row["win_rate"] = round(row["wins"] / row["games"] * 100, 1) if row["games"] else 0.0
    return sorted(tally.values(), key=lambda r: -r["games"])


# --------------------------------------------------------------- items

ITEMS_FILE = "items.json"

# Field names below are taken from the API's own OpenAPI schemas:
#   ItemStats      item_id bucket wins losses matches players
#                  avg_buy_time_s avg_sell_time_s
#                  avg_buy_time_relative avg_sell_time_relative
#   ItemFlowNode   column item_id wins losses matches players
#                  adjusted_win_rate avg_net_worth_at_buy
#   ItemFlowEdge   from_column from_item_id to_item_id wins losses matches
#   ItemFlowStats  nodes edges summary baseline reached_per_column
#   AnalyticsAbilityOrderStats  abilities[] wins losses matches players
# _pick() still tries a few aliases so a rename upstream degrades instead
# of crashing.

PHASE_KEYS = ("column", "phase", "phase_index", "stage")
ITEM_KEYS = ("item_id", "id", "upgrade_id", "ability_id")
COUNT_KEYS = ("matches", "games", "count", "total")
WIN_KEYS = ("wins", "win_count")
RATE_KEYS = ("win_rate", "winrate")

# analytics endpoints share one budget: 200 req/min per IP
ANALYTICS_PAUSE = 0.35


def items(refresh=False):
    """The full item asset list, cached to items.json. Abilities included."""
    return get_cached("/v1/assets/items", ITEMS_FILE, refresh=refresh)


def item_names(refresh=False):
    """{item_id: name} covering both shop items and hero abilities."""
    out = {}
    for it in _walk_dicts(items(refresh)):
        iid = it.get("id")
        name = it.get("name") or it.get("class_name")
        if isinstance(iid, int) and name:
            out.setdefault(iid, name)
    return out


def _pick(node, keys, default=None):
    """First present, non-None value among `keys`."""
    for k in keys:
        v = node.get(k)
        if v is not None:
            return v
    return default


def _win_rate(node, games, wins):
    """Win rate as a percentage, however the endpoint expressed it."""
    raw = _pick(node, RATE_KEYS)
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return round(raw * 100 if raw <= 1 else raw, 1)
    if games:
        return round((wins or 0) / games * 100, 1)
    return None


def _adjusted(node):
    """adjusted_win_rate, normalised to a percentage. May be absent."""
    raw = node.get("adjusted_win_rate")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return round(raw * 100 if raw <= 1 else raw, 1)
    return None


def mmss(seconds):
    """1234.5 -> '20:34'. Buy times read better as clock time."""
    if seconds is None:
        return ""
    s = int(round(seconds))
    return f"{s // 60}:{s % 60:02d}"


def _window(days, match_mode, min_matches, account_ids=(), hero_id=None,
            hero_key="hero_id"):
    params = {
        "min_unix_timestamp": int(time.time()) - days * 86400,
        "min_matches": min_matches,
        "match_mode": match_mode or None,
    }
    if account_ids:
        params["account_ids"] = ",".join(str(a) for a in account_ids)
    if hero_id is not None:
        params[hero_key] = hero_id
    return params


# ---- buy order: item-stats, sorted by when the item is actually bought

def buy_order(account_ids=(), hero_id=None, days=DEFAULT_DAYS,
              match_mode=CUSTOMS_ONLY, min_matches=1, refresh=False,
              names=None):
    """
    What gets bought, in the order it gets bought.

    Reads /v1/analytics/item-stats, which carries `avg_buy_time_s` per item,
    so sorting by it gives a real timeline rather than a bucket.

    -> [{'item','item_id','buys','players','avg_buy_s','buy_time',
         'buy_pct','wins','win_rate','avg_sell_s'}]
    """
    raw = get_json("/v1/analytics/item-stats", refresh=refresh,
                   **_window(days, match_mode, min_matches, account_ids,
                             hero_id))
    return buy_rows(raw, names)


def buy_rows(raw, names=None):
    """Normalise an item-stats response. Safe on an unexpected shape."""
    if names is None:
        try:
            names = item_names()
        except Exception:
            names = {}

    nodes = raw if isinstance(raw, list) else None
    if nodes is None:
        nodes = [d for d in _walk_dicts(raw) if _pick(d, ITEM_KEYS) is not None]

    rows = []
    for n in nodes:
        if not isinstance(n, dict):
            continue
        item_id = _pick(n, ITEM_KEYS)
        if item_id is None:
            continue
        games = _pick(n, COUNT_KEYS, 0) or 0
        wins = _pick(n, WIN_KEYS) or 0
        buy_s = n.get("avg_buy_time_s")
        rows.append({
            "item": names.get(item_id, f"item {item_id}"),
            "item_id": item_id,
            "buys": games,
            "players": n.get("players"),
            "avg_buy_s": buy_s,
            "buy_time": mmss(buy_s),
            "buy_pct": n.get("avg_buy_time_relative"),
            "avg_sell_s": n.get("avg_sell_time_s"),
            "wins": wins,
            "win_rate": _win_rate(n, games, wins),
        })

    # unknown buy time sorts last rather than first
    rows.sort(key=lambda r: (r["avg_buy_s"] is None,
                             r["avg_buy_s"] or 0, -r["buys"]))
    return rows


def buy_order_by_player(account_ids, labels=None, hero_id=None,
                        days=DEFAULT_DAYS, match_mode=CUSTOMS_ONLY,
                        min_matches=1, pause=ANALYTICS_PAUSE, refresh=False):
    """
    buy_order() computed one player at a time.
    -> the same rows, each carrying 'player' and 'account_id'.
    """
    labels = labels or {}
    try:
        names = item_names()
    except Exception:
        names = {}

    out = []
    for account_id in account_ids:
        try:
            rows = buy_order([account_id], hero_id, days, match_mode,
                             min_matches, refresh, names)
        except urllib.error.HTTPError:
            continue
        who = labels.get(account_id, {})
        for r in rows:
            out.append({"player": who.get("ign") or str(account_id),
                        "account_id": account_id, **r})
        time.sleep(pause)
    return out


# ---- build flow: the same purchases grouped into phases, plus transitions

def item_flow(account_ids=(), hero_id=None, days=DEFAULT_DAYS,
              match_mode=CUSTOMS_ONLY, min_matches=1, phase_count=4,
              phase_interval_s=600, refresh=False):
    """Raw /v1/analytics/item-flow-stats response (nodes, edges, summary)."""
    params = _window(days, match_mode, min_matches, account_ids, hero_id,
                     hero_key="hero_ids")
    params["phase_count"] = phase_count
    params["phase_interval_s"] = phase_interval_s
    return get_json("/v1/analytics/item-flow-stats", refresh=refresh, **params)


def phase_label(column, phase_interval_s=600):
    """Column 1 with a 600s interval -> '10-20 min'."""
    lo = column * phase_interval_s // 60
    hi = (column + 1) * phase_interval_s // 60
    return f"{lo}-{hi} min"


def flow_rows(raw, names=None, phase_interval_s=600):
    """
    item-flow nodes as rows.
    -> [{'phase','window','item','item_id','buys','players','pick_rate',
         'win_rate','adj_win_rate','avg_net_worth_at_buy'}]
    """
    if names is None:
        try:
            names = item_names()
        except Exception:
            names = {}

    nodes = raw.get("nodes") if isinstance(raw, dict) else None
    if not isinstance(nodes, list):
        nodes = [d for d in _walk_dicts(raw)
                 if _pick(d, ITEM_KEYS) is not None
                 and _pick(d, PHASE_KEYS) is not None]

    summary = raw.get("summary") if isinstance(raw, dict) else None
    pool = (summary or {}).get("matches") if isinstance(summary, dict) else None

    rows = []
    for n in nodes:
        if not isinstance(n, dict):
            continue
        item_id = _pick(n, ITEM_KEYS)
        if item_id is None:
            continue
        games = _pick(n, COUNT_KEYS, 0) or 0
        wins = _pick(n, WIN_KEYS) or 0
        column = _pick(n, PHASE_KEYS, 0)
        rows.append({
            "phase": column,
            "window": phase_label(column, phase_interval_s),
            "item": names.get(item_id, f"item {item_id}"),
            "item_id": item_id,
            "buys": games,
            "players": n.get("players"),
            "pick_rate": round(games / pool * 100, 1) if pool else None,
            "win_rate": _win_rate(n, games, wins),
            "adj_win_rate": _adjusted(n),
            "avg_net_worth_at_buy": n.get("avg_net_worth_at_buy"),
        })

    rows.sort(key=lambda r: (r["phase"], -r["buys"]))
    return rows


def flow_edges(raw, names=None, min_matches=1):
    """
    Which item tends to follow which.
    -> [{'from','to','from_phase','matches','win_rate'}] most common first.
    """
    if names is None:
        try:
            names = item_names()
        except Exception:
            names = {}

    edges = raw.get("edges") if isinstance(raw, dict) else None
    if not isinstance(edges, list):
        return []

    out = []
    for e in edges:
        if not isinstance(e, dict):
            continue
        games = _pick(e, COUNT_KEYS, 0) or 0
        if games < min_matches:
            continue
        wins = _pick(e, WIN_KEYS) or 0
        src, dst = e.get("from_item_id"), e.get("to_item_id")
        out.append({
            "from": names.get(src, f"item {src}"),
            "to": names.get(dst, f"item {dst}"),
            "from_phase": e.get("from_column"),
            "matches": games,
            "win_rate": _win_rate(e, games, wins),
        })
    out.sort(key=lambda r: -r["matches"])
    return out


# ---- ability point order

def ability_order(hero_id, account_ids=(), days=DEFAULT_DAYS,
                  match_mode=CUSTOMS_ONLY, min_matches=1, refresh=False):
    """
    Ability-point order for one hero. hero_id is required by the endpoint.
    -> raw response; ability_rows() flattens it.
    """
    if hero_id is None:
        raise ValueError("ability_order needs a hero_id")
    return get_json("/v1/analytics/ability-order-stats", refresh=refresh,
                    **_window(days, match_mode, min_matches, account_ids,
                              hero_id))


# Hero assets map a slot name to an ability's class_name; the item assets map
# that class_name to its id. Chain them and an ability id becomes the 1/2/3/4
# a player actually presses. Slot names come from the API's own enum
# (HashMap_HeroItemType_String): Signature1..Signature4 are the four upgradable
# abilities; innates, mantle, jump, slide and zipline share the same map and
# have no number.
SIGNATURE_SLOTS = ("Signature1", "Signature2", "Signature3", "Signature4")

ABILITY_STYLES = ("Names", "Numbers", "Both")


def _class_to_id(refresh=False):
    """{class_name: item_id} across every asset, abilities included."""
    out = {}
    for it in _walk_dicts(items(refresh)):
        iid, cls = it.get("id"), it.get("class_name")
        if isinstance(iid, int) and isinstance(cls, str):
            out.setdefault(cls, iid)
    return out


def ability_slots(hero_id, refresh=False):
    """
    {ability_id: 1|2|3|4} for one hero.

    Empty when the hero is unknown or the assets do not carry slot names --
    callers fall back to ability names, so an empty map degrades to today's
    behaviour rather than breaking.
    """
    hero = next((h for h in _walk_dicts(heroes(refresh))
                 if h.get("id") == hero_id and isinstance(h.get("items"), dict)),
                None)
    if not hero:
        return {}

    by_class = _class_to_id(refresh)
    slots = {}
    for position, slot in enumerate(SIGNATURE_SLOTS, start=1):
        class_name = hero["items"].get(slot)
        if not isinstance(class_name, str):
            continue
        ability_id = by_class.get(class_name)
        if isinstance(ability_id, int):
            slots[ability_id] = position
    return slots


def format_order(ability_ids, names=None, slots=None, style="Names"):
    """
    One upgrade order, rendered.

        Names    Bomb > Hook > Bomb > Uppercut
        Numbers  1 > 2 > 1 > 3
        Both     1 Bomb > 2 Hook > 1 Bomb > 3 Uppercut

    An id with no signature slot (an innate, say) keeps its name in every
    style, so nothing silently disappears.
    """
    names, slots = names or {}, slots or {}
    parts = []
    for a in ability_ids:
        number, name = slots.get(a), names.get(a, str(a))
        if style == "Numbers":
            parts.append(str(number) if number else name)
        elif style == "Both":
            parts.append(f"{number} {name}" if number else name)
        else:
            parts.append(name)
    return " > ".join(parts)


def ability_rows(raw, names=None, top=25, hero_id=None, style="Names"):
    """
    AnalyticsAbilityOrderStats carries `abilities` -- the upgrade order as a
    list of ability ids. One row per distinct order.

    style is one of ABILITY_STYLES. Numbers need hero_id, since a slot number
    only means anything relative to one hero.

    -> [{'order','ability_ids','matches','players','win_rate'}]
    """
    if names is None:
        try:
            names = item_names()
        except Exception:
            names = {}

    slots = {}
    if style in ("Numbers", "Both") and hero_id is not None:
        try:
            slots = ability_slots(hero_id)
        except Exception:
            slots = {}

    nodes = raw if isinstance(raw, list) else None
    if nodes is None:
        nodes = [d for d in _walk_dicts(raw) if isinstance(d.get("abilities"),
                                                           list)]
    rows = []
    for n in nodes:
        if not isinstance(n, dict):
            continue
        order = n.get("abilities")
        if not isinstance(order, list):
            continue
        games = _pick(n, COUNT_KEYS, 0) or 0
        wins = _pick(n, WIN_KEYS) or 0
        rows.append({
            "order": format_order(order, names, slots, style),
            "ability_ids": order,
            "matches": games,
            "players": n.get("players"),
            "win_rate": _win_rate(n, games, wins),
        })
    rows.sort(key=lambda r: -r["matches"])
    return rows[:top]