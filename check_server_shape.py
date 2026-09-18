#!/usr/bin/env python3
"""
Does what the server returns survive deadlock.py's parsers?

The server stores matches normalised and hands back its own record shape, so
everything downstream depends on server.to_upstream() producing something the
existing readers recognise. They are strict in one place that matters: a
purchase list is only seen as items when every entry carries *both* an id and a
timestamp, so a near-miss here would leave the build and item-meta views empty
with nothing to indicate why.

This runs entirely offline against a synthetic server record. It checks the
conversion, not the network.

    python check_server_shape.py
"""

import sys

import deadlock
import server

RECORD = {
    "match_id": 106338684,
    "start_time": "2026-09-18T12:00:00+00:00",
    "duration_s": 2100,
    "team1_won": False,
    "average_badge": 92,
    "game_mode": "normal",
    "match_mode": "ranked",
    "banned_hero_ids": None,
    "not_scored": False,
    "players": [
        {"account_id": 1000 + i, "hero_id": 10 + i,
         "is_team1": bool(i % 2), "won": not bool(i % 2),
         "kills": i, "deaths": 1, "assists": 2, "net_worth": 20_000,
         "last_hits": 100, "denies": 5, "assigned_lane": (i % 6) + 1,
         "items": [[3000 + i, 120 + i, None], [4000 + i, 900 + i, 1500]]}
        for i in range(12)
    ],
}


def main() -> int:
    match = server.to_upstream(RECORD)
    problems = []

    if deadlock.winning_side(match) != 0:
        problems.append(f"winning_side read {deadlock.winning_side(match)}, "
                        f"expected 0 (team1_won was False)")

    players = [p for p in deadlock._walk_dicts(match)
               if isinstance(p.get("account_id"), int)]
    if len(players) != 12:
        problems.append(f"_walk_dicts found {len(players)} players, expected 12")

    wins = [deadlock.player_won(p) for p in players]
    if sorted(w for w in wins if w is not None).count(True) != 6:
        problems.append(f"player_won gave {wins.count(True)} winners, expected 6")
    if None in wins:
        problems.append("player_won returned None for at least one player")

    sides = [deadlock.norm_side(p.get("team")) for p in players]
    if set(sides) != {0, 1}:
        problems.append(f"norm_side gave {set(sides)}, expected {{0, 1}}")

    # the strict one: items are only recognised with a timestamp on each entry
    entries = [deadlock._item_entries(p) for p in players]
    empty = sum(1 for e in entries if not e)
    if empty:
        problems.append(f"_item_entries found no purchases for {empty}/12 "
                        f"players -- the build views would be blank")

    # bulk_build_rows is the actual path a bulk metadata response takes
    rows = deadlock.bulk_build_rows([match])
    if not rows:
        problems.append("bulk_build_rows produced nothing from a full match")
    else:
        if len(rows) != 24:
            problems.append(f"bulk_build_rows gave {len(rows)} rows, "
                            f"expected 24 (12 players x 2 purchases)")
        if not all(r.get("bought_s") is not None for r in rows):
            problems.append("some purchase rows have no time")
        if {r["won"] for r in rows} != {True, False}:
            problems.append(f"rows carry won={{{', '.join(str(r) for r in {r['won'] for r in rows})}}}, "
                            f"expected both True and False")
        sold = [r for r in rows if r.get("sold_s")]
        if len(sold) != 12:
            problems.append(f"{len(sold)} rows carry a sell time, expected 12")

    if problems:
        print("the conversion does not fit the parsers:")
        for problem in problems:
            print(f"  !! {problem}")
        return 1

    print(f"12 players, {len(rows)} purchase rows, "
          f"sides and outcomes resolved, times intact")
    print(f"  sample row: hero {rows[0]['hero'] or rows[0]['hero_id']} bought "
          f"{rows[0]['item']} at {rows[0]['buy_time']}, won={rows[0]['won']}")
    print("server records parse cleanly through deadlock.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
