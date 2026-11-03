"""
team_ranks.py -- every league team's average rank, and standings from it.

    standings()                         # every team, ranked in league and division
    standings(division="DSL 5 - Div 1")
    team_strength("Team Lash", badges)  # one team, given a {account_id: badge} map

Two things here are easy to get wrong in a way that still prints a plausible
table.

**A badge is not a number you can average.** Badge = tier * 10 + subrank, and
subranks run 1-6, so 76 is Emissary 6 and the next rank up is 81, Oracle 1.
The mean of 76 and 81 is 78.5 -- a rank that does not exist, so the label on a
team would be wrong. So badges are turned into ladder rungs (0 = Obscurus 1,
71 = Eternus 6), averaged there, and turned back to the nearest real rank.

The raw mean also weights a tier as ten subranks when it is six, and can order
two specific teams wrongly (Oracle 1 + Ritualist 1 against Emissary 6 +
Ritualist 6: raw means tie at 71, though the second pair is four rungs higher).
But measured over simulated leagues with planted team strengths, that does not
make the *standings* more accurate: rung and raw means recover the planted
order equally well (Kendall tau 0.94 against 0.94 at a two-rung spread within
teams). The reason to average on rungs is that the number shown is a real rank,
not that the order is better.

**Unranked is not the bottom rank.** The API reports a player with no ranked
game as badge 0, and leaves protected accounts out entirely. Counting either as
Obscurus would drag a team down for a player who simply has not played, so they
are excluded from the average and counted beside it. A team whose average rests
on fewer than `min_ranked` players is listed but not placed: one Eternus and
five unranked accounts is not the best team in the league.

Subs are left out by default: the question is how strong the team that plays
is. Pass include_subs=True to count the whole roster.

Ranks come from the bulk `/v1/players/rank`, 1,000 accounts a call, so the
whole league is two calls. In that response `rank` is the *tier* and `badge` is
the badge; this reads `badge`. Ranks are the badge after each player's latest
ranked game -- a season reset (5 Nov 2026 22:00 UTC, per
/v1/assets/ranked-seasons) recalibrates everyone and compresses these averages
for a while afterwards.
"""

from __future__ import annotations

from statistics import median

SUBRANKS = 6
TOP_RUNG = 12 * SUBRANKS - 1          # Eternus 6
RANKS_PER_CALL = 1000                 # /v1/players/rank maxItems
MIN_RANKED = 4
PLAYING_ROLES = {"Core", "POC"}       # POC is a rostered player, not a manager


def position(badge):
    """Badge -> ladder rung, 0 (Obscurus 1) to 71 (Eternus 6)."""
    tier, sub = divmod(int(badge), 10)
    tier = min(max(tier, 0), 11)
    sub = min(max(sub or 1, 1), SUBRANKS)
    return tier * SUBRANKS + sub - 1


def badge_at(rung):
    """Ladder rung -> badge. Fractional rungs round to the nearest real rank."""
    rung = min(max(int(round(rung)), 0), TOP_RUNG)
    tier, index = divmod(rung, SUBRANKS)
    return tier * 10 + index + 1


def fetch_badges(account_ids, refresh=False):
    """
    {account_id: badge} for every ranked account among these. Unranked
    (badge 0) and protected (absent) accounts are simply not in the result.
    """
    from api import get_json

    ids = sorted({int(a) for a in account_ids})
    out = {}
    for start in range(0, len(ids), RANKS_PER_CALL):
        batch = ids[start:start + RANKS_PER_CALL]
        rows = get_json("/v1/players/rank", refresh=refresh,
                        account_ids=",".join(map(str, batch)))
        for row in rows or []:
            # `badge`, never `rank`: in this response rank is the tier
            account, badge = row.get("account_id"), row.get("badge")
            if account is not None and badge:
                out[int(account)] = int(badge)
    return out


def _counted(team, include_subs):
    """The account ids whose rank counts toward the team's average."""
    roles = team.get("roles") or {}
    return [account for label, account in team["players"].items()
            if include_subs or roles.get(label, "Core") in PLAYING_ROLES]


def team_strength(name, badges, include_subs=False, teams=None):
    """
    One team's rank, averaged on the ladder.
    -> {'team','division','players','ranked','unranked','mean_rung',
        'mean_badge','median_badge','top_badge','low_badge','stale_since'}
    `mean_badge` is the nearest real rank to the mean; `mean_rung` is the
    unrounded mean, for sorting and for seeing how close two teams are.
    """
    if teams is None:
        from teams import TEAMS as teams
    team = teams[name]
    counted = _counted(team, include_subs)
    have = [badges[a] for a in counted if badges.get(a)]
    rungs = [position(b) for b in have]
    mean_rung = sum(rungs) / len(rungs) if rungs else None
    return {
        "team": name,
        "division": team.get("division") or "",
        "players": len(counted),
        "ranked": len(have),
        "unranked": len(counted) - len(have),
        "mean_rung": mean_rung,
        "mean_badge": badge_at(mean_rung) if rungs else None,
        "median_badge": badge_at(median(rungs)) if rungs else None,
        "top_badge": max(have) if have else None,
        "low_badge": min(have) if have else None,
        "stale_since": team.get("stale_since"),
    }


def _place(rows, key):
    """Competition ranking on mean rung, two decimals: 1, 2, 2, 4."""
    placed, last, place = 0, None, None
    for row in rows:
        placed += 1
        value = round(row["mean_rung"], 2)
        if value != last:
            place, last = placed, value
        row[key] = place


def standings(division=None, include_subs=False, min_ranked=MIN_RANKED,
              badges=None, teams=None, refresh=False):
    """
    Every team's average rank, placed across the league and within its
    division. Teams with fewer than `min_ranked` ranked players are returned
    after the placed ones with no place, rather than ranked on a thin average.

    `division` filters the rows returned; league places are still computed
    across the whole league, so a Div 1 team's league place means the same
    thing whether or not you filtered.
    """
    if teams is None:
        from teams import LEAGUE
        teams = LEAGUE
    if badges is None:
        everyone = [a for t in teams.values() for a in t["players"].values()]
        badges = fetch_badges(everyone, refresh=refresh)

    rows = [team_strength(name, badges, include_subs, teams) for name in teams]
    placed = sorted((r for r in rows if r["ranked"] >= min_ranked),
                    key=lambda r: (-r["mean_rung"], r["team"].lower()))
    thin = sorted((r for r in rows if r["ranked"] < min_ranked),
                  key=lambda r: r["team"].lower())
    for row in thin:
        row["league_place"] = row["division_place"] = None

    _place(placed, "league_place")
    for div in {r["division"] for r in placed}:
        _place([r for r in placed if r["division"] == div], "division_place")

    out = placed + thin
    if division is not None:
        out = [r for r in out if r["division"] == division]
    return out
