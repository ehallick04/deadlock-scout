"""
diagnose.py — show what the API actually returns, so field names can be
pinned instead of guessed.

    python diagnose.py                 uses the pinned pro rosters
    python diagnose.py 880934744 ...   specific account ids

Prints a short report: which keys hold the match outcome, which hold a
player's side, and whether the two can be reconciled. Paste the output back
and the parser can be matched to it exactly.
"""

import json
import sys

from api import get_json
from deadlock import (CUSTOMS_ONLY, DEFAULT_DAYS, TEAM_KEYS, WINNER_KEYS,
                      _walk_dicts, winner_offset)

CANDIDATE_WINNER = ("winning_team", "match_winner", "winner", "match_result",
                    "match_outcome", "team_winner", "result")
CANDIDATE_SIDE = ("team", "player_team", "team_number", "player_slot", "slot")


def scalars(node, keys):
    """Every {key: value} among `keys` found anywhere, with where it sat."""
    found = {}
    for d in _walk_dicts(node):
        is_player = isinstance(d.get("account_id"), int)
        for k in keys:
            if k in d:
                where = "player" if is_player else "match"
                found.setdefault(f"{where}.{k}", []).append(d[k])
    return {k: v[:6] for k, v in found.items()}


def probe(account_ids, days=DEFAULT_DAYS, match_mode=CUSTOMS_ONLY):
    report = {}

    for label, extra in (("without include_more_info", {}),
                         ("with include_more_info",
                          {"include_more_info": "true"})):
        params = {"include_info": "true", "include_player_info": "true",
                  "limit": 3, "order_by": "match_id",
                  "order_direction": "desc",
                  "match_mode": match_mode or None,
                  "account_ids": ",".join(str(a) for a in account_ids)}
        params.update(extra)
        try:
            raw = get_json("/v1/matches/metadata", refresh=True, **params)
        except Exception as e:
            report[label] = {"error": str(e)}
            continue

        matches = raw if isinstance(raw, list) else [raw]
        matches = [m for m in matches if isinstance(m, dict)]
        if not matches:
            report[label] = {"matches": 0}
            continue

        first = matches[0]
        players = [d for d in _walk_dicts(first)
                   if isinstance(d.get("account_id"), int)
                   and d["account_id"] > 0]
        sides = sorted({p.get(k) for p in players for k in CANDIDATE_SIDE
                        if isinstance(p.get(k), int)})

        report[label] = {
            "matches": len(matches),
            "match_level_keys": sorted(k for k in first if not isinstance(
                first[k], (list, dict)))[:25],
            "outcome_fields": scalars(first, CANDIDATE_WINNER),
            "side_fields": scalars(first, CANDIDATE_SIDE),
            "players_found": len(players),
            "player_keys": sorted(players[0])[:25] if players else [],
            "distinct_side_values": sides[:10],
            "keys_this_parser_looks_for": {
                "winner": list(WINNER_KEYS), "side": list(TEAM_KEYS)},
        }

        lineups = []
        for m in matches:
            by_side = {}
            for p in _walk_dicts(m):
                if isinstance(p.get("account_id"), int) and p["account_id"] > 0:
                    by_side.setdefault(p.get("team"), []).append(p["account_id"])
            win = next((m[k] for k in CANDIDATE_WINNER if k in m), None)
            lineups.append({"winner": win, "sides": by_side})
        report[label]["winner_offset"] = winner_offset(lineups)
        report[label]["verdict"] = (
            "outcomes resolve" if report[label]["winner_offset"] is not None
            else "NO usable outcome field found — win rates will be blank")

    return report


def main(argv):
    ids = [int(a) for a in argv[1:] if a.isdigit()]
    if not ids:
        from teams import PINNED, roster_many
        ids, _ = roster_many(sorted(PINNED))
        print(f"# no ids given — using the {len(ids)} pinned pros\n")
    print(json.dumps(probe(ids), indent=1, default=str))


if __name__ == "__main__":
    main(sys.argv)