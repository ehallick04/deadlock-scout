"""
players.py — most-played heroes, win rates, and ranks for a list of players.

Accepts raw account ids OR statlocker profile URLs; the id is pulled out either way:
    https://statlocker.gg/profile/880934744/matches?mode=standard  ->  880934744

    python players.py                                   # interactive menu
    python players.py 880934744 104579843               # ids
    python players.py https://statlocker.gg/profile/880934744/matches?mode=standard
    python players.py --file ids.txt --days 30 --top 5
    python players.py 880934744 --csv players.csv
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://api.deadlock-api.com"
HEADERS = {"User-Agent": "my-deadlock-project/0.1", "Accept": "application/json"}
API_KEY = os.environ.get("DEADLOCK_API_KEY")

# Rank tiers in ascending order. rank 1 = Initiate ... rank 11 = Eternus.
# Valve's own names are also available at /v1/assets/ranks if you'd rather pull them live.
RANKS = [
    "Initiate", "Seeker", "Acolyte", "Sentinel", "Mystic", "Ritualist",
    "Emissary", "Oracle", "Phantom", "Ascendant", "Eternus",
]


# --------------------------------------------------------------- http

def get_json(path, **params):
    """GET any endpoint. Returns parsed JSON."""
    url = BASE + path
    clean = {k: v for k, v in params.items() if v is not None}
    if clean:
        url += "?" + urllib.parse.urlencode(clean, doseq=True)

    headers = dict(HEADERS)
    if API_KEY:
        headers["X-API-Key"] = API_KEY

    req = urllib.request.Request(url, headers=headers)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 2:
                wait = int(e.headers.get("Retry-After") or 2 ** (attempt + 1))
                print(f"    rate limited, waiting {wait}s")
                time.sleep(wait)
                continue
            raise


# --------------------------------------------------------------- inputs

def parse_ids(items):
    """
    Pull account ids out of whatever you throw at this: bare numbers,
    statlocker profile URLs, comma- or space-separated lists.
    """
    ids = []
    for item in items:
        text = str(item)
        # a statlocker/deadlock profile URL: take the id after /profile/
        m = re.search(r"/profile/(\d+)", text)
        if m:
            ids.append(int(m.group(1)))
            continue
        # otherwise grab every run of digits that looks like an account id
        for n in re.findall(r"\d{5,}", text):
            ids.append(int(n))
    return list(dict.fromkeys(ids))          # de-duplicate, keep order


def read_id_file(path):
    with open(path, encoding="utf-8") as f:
        return parse_ids(f.read().split())


# --------------------------------------------------------------- lookups

def rank_name(rank, subrank=None):
    """9, 5 -> 'Phantom 5'.  Subrank stays a number."""
    if rank is None:
        return "Unknown"
    if rank <= 0:
        return "Unranked"
    tier = RANKS[rank - 1] if rank <= len(RANKS) else f"Rank {rank}"
    return f"{tier} {subrank}" if subrank else tier


def get_rank(account_id):
    """-> dict with tier name, numbers, and the raw payload."""
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


def hero_names():
    """{hero_id: name}, from local heroes.json if present, else fetched once."""
    if os.path.exists("heroes.json"):
        with open("heroes.json", encoding="utf-8") as f:
            heroes = json.load(f)
    else:
        heroes = get_json("/v1/assets/heroes")
        with open("heroes.json", "w", encoding="utf-8") as f:
            json.dump(heroes, f, ensure_ascii=False)
    return {h["id"]: h.get("name") or h.get("class_name") for h in heroes}


# --------------------------------------------------------------- hero stats

def hero_stats(account_ids, days=30, match_mode=None):
    """
    Per-hero matches_played / wins for these players over the last `days`.
    One batched call: /v1/players/hero-stats takes multiple account_ids.
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
    player_match_outcome is authoritative: 1 = win, 2 = loss.
    """
    since = int(time.time()) - days * 86400
    history = get_json(f"/v1/players/{account_id}/match-history")

    tally = {}
    for m in history:
        if m.get("start_time", 0) < since:
            continue
        outcome = m.get("player_match_outcome")
        if outcome not in (1, 2):          # skip abandons / unscored
            continue
        row = tally.setdefault(m["hero_id"], {"hero_id": m["hero_id"],
                                              "matches_played": 0, "wins": 0})
        row["matches_played"] += 1
        row["wins"] += 1 if outcome == 1 else 0

    for row in tally.values():
        row["account_id"] = account_id
    return list(tally.values())


# --------------------------------------------------------------- report

def report(account_ids, days=30, top=5, use_history=False, csv_path=None):
    names = hero_names()

    if use_history:
        stats = []
        for i in account_ids:
            stats += hero_stats_from_history(i, days)
    else:
        try:
            stats = hero_stats(account_ids, days)
        except urllib.error.HTTPError as e:
            print(f"hero-stats endpoint returned HTTP {e.code}; "
                  f"falling back to match history")
            stats = []
            for i in account_ids:
                stats += hero_stats_from_history(i, days)

    by_player = {}
    for s in stats:
        by_player.setdefault(s["account_id"], []).append(s)

    rows = []
    for account_id in account_ids:
        info = get_rank(account_id)
        played = sorted(by_player.get(account_id, []),
                        key=lambda s: -s.get("matches_played", 0))
        total = sum(s.get("matches_played", 0) for s in played)

        print(f"\n{'=' * 58}")
        print(f"  {account_id}   {info['rank_label']}"
              f"   ({total} matches in the last {days} days)")
        print(f"{'=' * 58}")

        if not played:
            print("  no matches in this window")
            continue

        print(f"  {'hero':<16}{'played':>8}{'wins':>7}{'win rate':>11}")
        print(f"  {'-' * 42}")
        for s in played[:top]:
            n, w = s.get("matches_played", 0), s.get("wins", 0)
            wr = (w / n * 100) if n else 0
            hero = names.get(s["hero_id"], f"hero {s['hero_id']}")
            print(f"  {hero:<16}{n:>8}{w:>7}{wr:>10.1f}%")
            rows.append({
                "account_id": account_id,
                "rank": info["rank_label"],
                "rank_num": info["rank"],
                "subrank": info["subrank"],
                "hero": hero,
                "hero_id": s["hero_id"],
                "matches": n,
                "wins": w,
                "win_rate": round(wr, 1),
            })

    if csv_path and rows:
        import csv as _csv
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = _csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        print(f"\nwrote {len(rows)} rows to {csv_path}")

    return rows


# --------------------------------------------------------------- menu

def ask(prompt, default=""):
    try:
        return input(prompt).strip() or default
    except (EOFError, KeyboardInterrupt):
        print()
        return None


def menu():
    ids, days, top = [], 30, 5

    while True:
        print(f"""
============== DEADLOCK PLAYER REPORT ==============
  1. Add players (ids or statlocker URLs)
  2. Load ids from a file
  3. Set time window        (now: last {days} days)
  4. Set heroes shown       (now: top {top})
  5. Run report
  6. Run report + save CSV
  7. Look up one rank
  8. Clear player list
  0. Quit

  players: {', '.join(map(str, ids)) or '(none yet)'}""")

        choice = ask("\n  choose: ")
        if choice is None or choice == "0" or choice.lower() in ("q", "quit", "exit"):
            print("  bye")
            return

        if choice == "1":
            raw = ask("  paste ids or URLs (space or comma separated): ")
            if raw:
                found = parse_ids(raw.replace(",", " ").split())
                ids = list(dict.fromkeys(ids + found))
                print(f"  added {len(found)}; list is now {len(ids)}")

        elif choice == "2":
            path = ask("  file path: ")
            if path and os.path.exists(path):
                ids = list(dict.fromkeys(ids + read_id_file(path)))
                print(f"  list is now {len(ids)}")
            else:
                print("  file not found")

        elif choice == "3":
            v = ask("  days to look back (default 30): ", "30")
            days = int(v) if v and v.isdigit() else 30

        elif choice == "4":
            v = ask("  how many heroes per player (default 5): ", "5")
            top = int(v) if v and v.isdigit() else 5

        elif choice in ("5", "6"):
            if not ids:
                print("  add some players first")
            else:
                path = ask("  csv filename (default players.csv): ", "players.csv") \
                    if choice == "6" else None
                report(ids, days=days, top=top, csv_path=path)

        elif choice == "7":
            one = ask("  id or URL: ")
            got = parse_ids([one]) if one else []
            if got:
                info = get_rank(got[0])
                print(f"  {got[0]}: {info['rank_label']}   "
                      f"(rank {info['rank']}, subrank {info['subrank']}, badge {info['badge']})")

        elif choice == "8":
            ids = []
            print("  cleared")

        else:
            print("  pick a number from the menu.")

        if ask("\n  [enter] to continue ") is None:
            return


# --------------------------------------------------------------- entry point

if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        menu()
        sys.exit()

    days = int(args[args.index("--days") + 1]) if "--days" in args else 30
    top = int(args[args.index("--top") + 1]) if "--top" in args else 5
    csv_path = args[args.index("--csv") + 1] if "--csv" in args else None

    ids = []
    if "--file" in args:
        ids += read_id_file(args[args.index("--file") + 1])

    flag_values = {"--days", "--top", "--csv", "--file"}
    skip = set()
    for f in flag_values:
        if f in args:
            skip.add(args.index(f) + 1)
    ids += parse_ids([a for i, a in enumerate(args)
                      if not a.startswith("--") and i not in skip])

    if not ids:
        print(__doc__)
        sys.exit(1)

    report(list(dict.fromkeys(ids)), days=days, top=top,
           use_history="--history" in args, csv_path=csv_path)
