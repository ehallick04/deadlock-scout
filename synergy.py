"""
synergy.py — which heroes actually help each other, and which ones bend the
whole meta.

Two questions:

  1. When A and B are on the same team, do they win more than their solo
     rates predict?
  2. Is any hero lifting *many* partners at once? That is what "meta
     defining" should mean -- not one strong pairing, but a hero whose
     presence improves whatever it is played with.

The API already counts pairs (`/v1/analytics/hero-synergy-stats` gives
hero_id1, hero_id2, matches_played, wins), so nothing here re-derives them
from raw matches. What it adds is the part the API does not do: comparing
observed against expected, testing whether the gap could be chance, and
correcting for the fact that ~1,500 pairs get tested at once.

    python synergy.py                 top-skill games, last 30 days
    python synergy.py 90              a 90-day window
"""

import math
import sys
import time

from api import get_json
from deadlock import (DEFAULT_GAME_MODE, badge_for, badge_label,
                      hero_names, playable_hero_ids)

# `average_badge` is tier*10 + subrank, so Phantom 1 is 91. Set the floor by
# rank rather than by a bare number -- see badge_for()/rank_choices().
#
# The floor is a real trade: every step up shrinks the sample, and synergy
# needs games to say anything. Phantom 1 keeps a wide slice of strong play;
# Eternus would leave almost nothing to test.
#
# This beats the leaderboard as a definition of "top players": its entries
# carry `possible_account_ids` -- plural, because Valve publishes names, not
# ids, so resolving them is guesswork.
TOP_BADGE = badge_for(9, 1)          # Phantom 1
DEFAULT_DAYS = 30
MIN_PAIR_MATCHES = 50
ALPHA = 0.05

# A hero needs a solid baseline of its own before it can anchor an
# "expected" win rate. Heroes that are in the assets but not in the game --
# in development, disabled, testing-only -- are dropped outright, and any
# hero left with a thin sample is dropped too: a baseline built on 30 games
# swings wildly and manufactures synergy that is not there.
MIN_HERO_MATCHES = 500


# --------------------------------------------------------------- statistics

def normal_sf(z):
    """P(Z > z) for a standard normal. erfc avoids needing scipy."""
    return 0.5 * math.erfc(z / math.sqrt(2))


def two_sided_p(z):
    return 2.0 * normal_sf(abs(z))


def wilson(wins, n, z=1.96):
    """Wilson score interval -- honest at small n, unlike wins/n +- 1.96se."""
    if not n:
        return (None, None)
    p = wins / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round((centre - half) * 100, 1), round((centre + half) * 100, 1))


def benjamini_hochberg(rows, alpha=ALPHA, key="p"):
    """
    Control the false discovery rate across many simultaneous tests.

    Testing ~1,500 pairs at alpha=0.05 yields ~75 "significant" results from
    pure noise. BH ranks the p-values and keeps only those below an
    increasing threshold, so `significant` means something.

    Adds 'q' and 'significant' in place; returns the rows.
    """
    scored = [r for r in rows if r.get(key) is not None]
    scored.sort(key=lambda r: r[key])
    n = len(scored)
    cutoff = 0
    for i, r in enumerate(scored, 1):
        if r[key] <= alpha * i / n:
            cutoff = i
    for i, r in enumerate(scored, 1):
        r["q"] = min(1.0, r[key] * n / i)
        r["significant"] = i <= cutoff
    for r in rows:
        r.setdefault("q", None)
        r.setdefault("significant", False)
    return rows


# --------------------------------------------------------------- fetching

def _window(days, badge, match_mode, game_mode, min_matches):
    params = {
        "min_unix_timestamp": int(time.time()) - days * 86400,
        "game_mode": game_mode or None,
        "match_mode": match_mode or None,
        "min_matches": min_matches,
    }
    if badge:
        params["min_average_badge"] = badge
    return params


def hero_baselines(days=DEFAULT_DAYS, badge=TOP_BADGE, match_mode=None,
                   game_mode=DEFAULT_GAME_MODE, refresh=False,
                   min_hero_matches=MIN_HERO_MATCHES, playable_only=True):
    """
    {hero_id: {'matches','wins','win_rate'}} solo baseline per hero.

    -> (baselines, dropped) where dropped explains every exclusion, so a
       missing hero is visible rather than silently absent.
    """
    raw = get_json("/v1/analytics/hero-stats", refresh=refresh,
                   **_window(days, badge, match_mode, game_mode, 1))
    totals = {}
    for row in raw if isinstance(raw, list) else []:
        hero_id, matches = row.get("hero_id"), row.get("matches") or 0
        if hero_id is None or not matches:
            continue
        entry = totals.setdefault(hero_id, {"matches": 0, "wins": 0})
        entry["matches"] += matches
        entry["wins"] += row.get("wins") or 0

    live = playable_hero_ids() if playable_only else None
    out, dropped = {}, {"not_in_game": [], "too_few_games": []}
    for hero_id, entry in totals.items():
        if live is not None and hero_id not in live:
            dropped["not_in_game"].append(hero_id)
            continue
        if entry["matches"] < min_hero_matches:
            dropped["too_few_games"].append((hero_id, entry["matches"]))
            continue
        entry["win_rate"] = round(entry["wins"] / entry["matches"] * 100, 1)
        out[hero_id] = entry

    # heroes in the game that returned no rows at all (brand new, or simply
    # unpicked in this window) are worth naming too
    if live is not None:
        dropped["no_data"] = sorted(live - set(totals))
    return out, dropped


def pair_counts(days=DEFAULT_DAYS, badge=TOP_BADGE, match_mode=None,
                game_mode=DEFAULT_GAME_MODE, min_matches=MIN_PAIR_MATCHES,
                refresh=False):
    """Raw pair rows: hero_id1, hero_id2, matches_played, wins."""
    return get_json("/v1/analytics/hero-synergy-stats", refresh=refresh,
                    **_window(days, badge, match_mode, game_mode, min_matches))


def leaderboard(region="NAmerica", refresh=False):
    """
    The published ladder. Entries carry `possible_account_ids` -- plural,
    since Valve exposes names rather than ids, so treat any resolution as a
    guess. Included for reference; the badge filter is the reliable way to
    restrict to strong games.
    """
    raw = get_json(f"/v1/leaderboard/{region}", refresh=refresh)
    entries = raw.get("entries") if isinstance(raw, dict) else raw
    return entries or []


# --------------------------------------------------------------- analysis

def expected_rate(a, b, method="delta"):
    """
    What a pair should win at if the two heroes simply do not interact.

    delta    each hero's edge over 50% adds:  0.5 + (a-0.5) + (b-0.5)
    average  the mean of the two solo rates
    """
    if method == "average":
        return (a + b) / 2
    return min(max(0.5 + (a - 0.5) + (b - 0.5), 0.001), 0.999)


def synergy_table(pairs, baselines, names=None, min_matches=MIN_PAIR_MATCHES,
                  method="delta", alpha=ALPHA):
    """
    -> [{'hero_a','hero_b','matches','wins','win_rate','expected','lift',
         'z','p','q','significant','low','high'}]
    sorted by lift, biggest positive first.
    """
    names = names or {}
    rows = []
    for row in pairs if isinstance(pairs, list) else []:
        a, b = row.get("hero_id1"), row.get("hero_id2")
        n = row.get("matches_played") or 0
        if a is None or b is None or n < min_matches:
            continue
        # a pair is only testable if BOTH heroes have a usable baseline,
        # so anything involving a dropped hero falls out here
        base_a, base_b = baselines.get(a), baselines.get(b)
        if not base_a or not base_b:
            continue

        wins = row.get("wins") or 0
        observed = wins / n
        expect = expected_rate(base_a["win_rate"] / 100,
                               base_b["win_rate"] / 100, method)
        se = math.sqrt(expect * (1 - expect) / n)
        z = (observed - expect) / se if se else 0.0
        low, high = wilson(wins, n)

        rows.append({
            "hero_a": names.get(a, str(a)), "hero_b": names.get(b, str(b)),
            "hero_id_a": a, "hero_id_b": b,
            "matches": n, "wins": wins,
            "win_rate": round(observed * 100, 1),
            "expected": round(expect * 100, 1),
            "lift": round((observed - expect) * 100, 1),
            "z": round(z, 2), "p": two_sided_p(z),
            "low": low, "high": high,
        })

    benjamini_hochberg(rows, alpha)
    rows.sort(key=lambda r: -r["lift"])
    return rows


def meta_defining(rows, baselines, names=None, min_partners=5, alpha=ALPHA):
    """
    Heroes that lift many partners, not just one.

    For each hero: how many of its pairings came out significantly positive,
    and whether its average lift across ALL its pairings is itself
    significant. A hero clears the bar only if both hold -- one lucky
    pairing is not a meta.

    -> [{'hero','pairs','significant_up','significant_down','avg_lift',
         'z','p','meta_defining'}]
    """
    names = names or {}
    per_hero = {}
    for r in rows:
        for hero_id, hero in ((r["hero_id_a"], r["hero_a"]),
                              (r["hero_id_b"], r["hero_b"])):
            entry = per_hero.setdefault(hero_id, {
                "hero": hero, "hero_id": hero_id, "pairs": 0,
                "significant_up": 0, "significant_down": 0,
                "_lift": [], "_n": []})
            entry["pairs"] += 1
            entry["_lift"].append(r["lift"])
            entry["_n"].append(r["matches"])
            if r["significant"]:
                if r["lift"] > 0:
                    entry["significant_up"] += 1
                elif r["lift"] < 0:
                    entry["significant_down"] += 1

    out = []
    for entry in per_hero.values():
        lifts = entry.pop("_lift")
        counts = entry.pop("_n")
        total = sum(counts)
        # weight each pairing by how many games back it
        avg = sum(l * n for l, n in zip(lifts, counts)) / total if total else 0
        # standard error of that weighted mean, from the pooled sample
        se = math.sqrt(0.25 / total) * 100 if total else 0
        z = avg / se if se else 0.0
        entry["hero"] = names.get(entry["hero_id"], entry["hero"])
        entry["avg_lift"] = round(avg, 2)
        entry["games"] = total
        entry["z"] = round(z, 2)
        entry["p"] = two_sided_p(z)
        out.append(entry)

    benjamini_hochberg(out, alpha)
    for entry in out:
        entry["meta_defining"] = bool(
            entry["significant"] and entry["avg_lift"] > 0
            and entry["significant_up"] >= min_partners)
    out.sort(key=lambda r: -r["avg_lift"])
    return out


def report(days=DEFAULT_DAYS, badge=TOP_BADGE, match_mode=None,
           game_mode=DEFAULT_GAME_MODE, min_matches=MIN_PAIR_MATCHES,
           method="delta", alpha=ALPHA, min_partners=5, refresh=False,
           min_hero_matches=MIN_HERO_MATCHES, playable_only=True):
    """Everything in one call. -> (pairs, heroes, context)"""
    names = hero_names()
    baselines, dropped = hero_baselines(days, badge, match_mode, game_mode,
                                        refresh, min_hero_matches,
                                        playable_only)
    pairs = synergy_table(
        pair_counts(days, badge, match_mode, game_mode, min_matches, refresh),
        baselines, names, min_matches, method, alpha)
    heroes = meta_defining(pairs, baselines, names, min_partners, alpha)

    def named(items):
        out = []
        for item in items:
            hero_id, extra = (item if isinstance(item, tuple) else (item, None))
            label = names.get(hero_id, str(hero_id))
            out.append(f"{label} ({extra} games)" if extra else label)
        return sorted(out)

    context = {
        "days": days, "min_average_badge": badge,
        "min_rank": badge_label(badge) if badge else "any rank",
        "matches_in_pairs": sum(r["matches"] for r in pairs),
        "min_pair_matches": min_matches, "min_hero_matches": min_hero_matches,
        "pairs_tested": len(pairs), "heroes": len(baselines),
        "alpha": alpha, "expected_model": method,
        "significant_pairs": sum(1 for r in pairs if r["significant"]),
        "meta_defining": [h["hero"] for h in heroes if h["meta_defining"]],
        "excluded_not_in_game": named(dropped.get("not_in_game", [])),
        "excluded_too_few_games": named(dropped.get("too_few_games", [])),
        "excluded_no_data": named(dropped.get("no_data", [])),
    }
    return pairs, heroes, context


def main(argv):
    days = int(argv[1]) if len(argv) > 1 and argv[1].isdigit() else DEFAULT_DAYS
    pairs, heroes, context = report(days=days)

    print(f"\n  {context['pairs_tested']} pairs tested across "
          f"{context['heroes']} heroes, {context['significant_pairs']} "
          f"significant after FDR correction (alpha={context['alpha']})")
    print(f"  {context['min_rank']}+ , last {days} days, "
          f"{context['min_pair_matches']}+ games per pair, "
          f"{context['matches_in_pairs']:,} games behind the pairs")

    for label, key in (("not in the game", "excluded_not_in_game"),
                       ("no games in this window", "excluded_no_data"),
                       (f"under {context['min_hero_matches']} games",
                        "excluded_too_few_games")):
        names = context.get(key) or []
        if names:
            shown = ", ".join(names[:8])
            more = f" +{len(names) - 8} more" if len(names) > 8 else ""
            print(f"  excluded ({label}): {shown}{more}")
    print()

    print("  BEST PAIRS")
    print(f"  {'pair':<30}{'games':>7}{'WR':>8}{'exp':>8}{'lift':>8}  sig")
    print(f"  {'-' * 68}")
    for r in pairs[:15]:
        print(f"  {r['hero_a'] + ' + ' + r['hero_b']:<30}{r['matches']:>7}"
              f"{r['win_rate']:>7.1f}%{r['expected']:>7.1f}%"
              f"{r['lift']:>+7.1f}%  {'yes' if r['significant'] else ''}")

    print("\n  WORST PAIRS")
    for r in pairs[-10:][::-1]:
        print(f"  {r['hero_a'] + ' + ' + r['hero_b']:<30}{r['matches']:>7}"
              f"{r['win_rate']:>7.1f}%{r['expected']:>7.1f}%"
              f"{r['lift']:>+7.1f}%  {'yes' if r['significant'] else ''}")

    print("\n  META DEFINING")
    flagged = [h for h in heroes if h["meta_defining"]]
    if not flagged:
        print("    none clear the bar in this window")
    for h in flagged:
        print(f"    {h['hero']:<16} lifts {h['significant_up']} partners, "
              f"avg {h['avg_lift']:+.2f}% over {h['games']} games")


if __name__ == "__main__":
    main(sys.argv)