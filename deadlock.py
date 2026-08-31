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

from api import get_json, get_cached

HEROES_FILE = "heroes.json"

# Rank tiers in ascending order: rank 1 = Initiate ... rank 11 = Eternus.
# Valve's own names are at /v1/assets/ranks if a patch ever renames these.
RANKS = [
    "Initiate", "Seeker", "Acolyte", "Sentinel", "Mystic", "Ritualist",
    "Emissary", "Oracle", "Phantom", "Ascendant", "Eternus",
]

# player_match_outcome values, per the API docs
WIN, LOSS = 1, 2


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

def hero_stats(account_ids, days=30, match_mode=None):
    """
    Per-hero matches_played / wins for these players over the last `days`.
    ONE batched call — /v1/players/hero-stats accepts multiple account_ids.
    """
    since = int(time.time()) - days * 86400
    return get_json(
        "/v1/players/hero-stats",
        account_ids=",".join(str(i) for i in account_ids),
        min_unix_timestamp=since,
        match_mode=match_mode,
    )


def hero_stats_from_history(account_id, days=30):
    """
    Fallback: aggregate the player's own match history locally.
    Uses player_match_outcome (1 = win, 2 = loss), which correctly
    excludes abandoned and unscored games.
    """
    since = int(time.time()) - days * 86400
    history = get_json(f"/v1/players/{account_id}/match-history")

    tally = {}
    for m in history:
        if m.get("start_time", 0) < since:
            continue
        outcome = m.get("player_match_outcome")
        if outcome not in (WIN, LOSS):
            continue
        row = tally.setdefault(m["hero_id"], {"account_id": account_id,
                                              "hero_id": m["hero_id"],
                                              "matches_played": 0, "wins": 0})
        row["matches_played"] += 1
        row["wins"] += 1 if outcome == WIN else 0
    return list(tally.values())


# --------------------------------------------------------------- report

def build_report(account_ids, days=30, top=5, use_history=False):
    """
    The whole thing, as DATA:

        [{'account_id': 880934744, 'rank_label': 'Phantom 5', 'rank': 9,
          'subrank': 5, 'total_matches': 65,
          'heroes': [{'hero': 'Bebop', 'hero_id': 15, 'matches': 42,
                      'wins': 25, 'win_rate': 59.5}, ...]}, ...]

    Printing is main.py's job.
    """
    names = hero_names()

    if use_history:
        stats = [s for i in account_ids for s in hero_stats_from_history(i, days)]
    else:
        try:
            stats = hero_stats(account_ids, days)
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

        hero_rows = []
        for s in played[:top]:
            n, w = s.get("matches_played", 0), s.get("wins", 0)
            hero_rows.append({
                "hero": names.get(s["hero_id"], f"hero {s['hero_id']}"),
                "hero_id": s["hero_id"],
                "matches": n,
                "wins": w,
                "win_rate": round(w / n * 100, 1) if n else 0.0,
            })

        players.append({
            "account_id": account_id,
            "rank_label": info["rank_label"],
            "rank": info["rank"],
            "subrank": info["subrank"],
            "badge": info["badge"],
            "total_matches": sum(s.get("matches_played", 0) for s in played),
            "heroes": hero_rows,
        })

    return players


def flatten(players):
    """Nested report -> flat rows, one per player+hero. For CSV or pandas."""
    return [
        {"account_id": p["account_id"], "rank": p["rank_label"],
         "rank_num": p["rank"], "subrank": p["subrank"], **h}
        for p in players for h in p["heroes"]
    ]
