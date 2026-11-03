"""
Team standings.

Run from the repo root:  .venv/bin/python -m pytest -q tests
"""

import json
import os
import random
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import team_ranks as tr  # noqa: E402

REAL_BADGES = [t * 10 + s for t in range(12) for s in range(1, 7)]


def _team(division, **players):
    """players: label=(account_id, badge-or-None, role)."""
    return {"division": division,
            "players": {k: v[0] for k, v in players.items()},
            "roles": {k: v[2] for k, v in players.items()}}


def _badges(*teams_):
    return {v[0]: v[1] for t in teams_ for v in t.values() if v[1]}


# ------------------------------------------------------------------ ladder

def test_every_real_badge_survives_the_round_trip():
    assert [tr.badge_at(tr.position(b)) for b in REAL_BADGES] == REAL_BADGES


def test_the_ladder_is_72_rungs_with_no_gaps():
    assert sorted(tr.position(b) for b in REAL_BADGES) == list(range(72))


def test_an_average_is_always_a_real_rank():
    """76 and 81 are adjacent rungs. Their raw mean, 78.5, is no rank."""
    for low in REAL_BADGES:
        for high in REAL_BADGES:
            mean = (tr.position(low) + tr.position(high)) / 2
            assert tr.badge_at(mean) in REAL_BADGES


def test_rung_averaging_orders_teams_the_raw_mean_ties():
    """
    Planted: team B is higher player for player -- Emissary 6 against Oracle 1
    is one rung lower, but Ritualist 6 against Ritualist 1 is five higher. The
    raw badge mean calls them equal (71 each), because it counts a tier as ten
    subranks instead of six.
    """
    a = dict(p1=(1, 81, "Core"), p2=(2, 61, "Core"))
    b = dict(p1=(3, 76, "Core"), p2=(4, 66, "Core"))
    teams = {"A": _team("D", **a), "B": _team("D", **b)}
    raw = {n: sum(v[1] for v in t.values()) / 2 for n, t in (("A", a), ("B", b))}
    assert raw["A"] == raw["B"] == 71
    rows = tr.standings(badges=_badges(a, b), teams=teams, min_ranked=1)
    assert [r["team"] for r in rows] == ["B", "A"]


# ------------------------------------------------------- who is counted

def test_unranked_players_are_left_out_not_counted_as_the_bottom():
    team = dict(p1=(1, 111, "Core"), p2=(2, 111, "Core"), p3=(3, None, "Core"))
    row = tr.team_strength("T", _badges(team), teams={"T": _team("D", **team)})
    assert (row["ranked"], row["unranked"], row["mean_badge"]) == (2, 1, 111)


def test_subs_are_left_out_unless_asked_for():
    team = dict(p1=(1, 111, "Core"), p2=(2, 111, "POC"), sub=(3, 61, "Sub"))
    teams = {"T": _team("D", **team)}
    assert tr.team_strength("T", _badges(team), teams=teams)["mean_badge"] == 111
    with_subs = tr.team_strength("T", _badges(team), include_subs=True,
                                 teams=teams)
    assert with_subs["players"] == 3 and with_subs["mean_badge"] < 111


def test_top_six_takes_the_highest_ranked_players_subs_included():
    team = dict(c1=(1, 101, "Core"), c2=(2, 101, "Core"), c3=(3, 61, "Core"),
                c4=(4, 101, "POC"), c5=(5, 101, "Core"), c6=(6, 101, "Core"),
                s1=(7, 111, "Sub"), s2=(8, 81, "Sub"))
    teams = {"T": _team("D", **team)}
    row = tr.team_strength("T", _badges(team), teams=teams, top_n=6)
    # the Eternus sub is in, the Ritualist core and the Oracle sub are out
    assert (row["ranked"], row["players"], row["low_badge"], row["top_badge"]) == (
        6, 6, 101, 111)
    starters = tr.team_strength("T", _badges(team), teams=teams)
    assert starters["low_badge"] == 61


def test_top_six_never_picks_an_unranked_player():
    team = dict(p1=(1, 111, "Core"), p2=(2, 111, "Core"), p3=(3, 111, "Core"),
                p4=(4, 111, "Core"), u1=(5, None, "Core"), u2=(6, None, "Sub"),
                u3=(7, None, "Sub"))
    row = tr.team_strength("T", _badges(team), teams={"T": _team("D", **team)},
                           top_n=6)
    assert (row["ranked"], row["unranked"], row["mean_badge"]) == (4, 2, 111)


def test_a_thin_team_is_listed_but_not_placed():
    """One Eternus and five unranked accounts is not the best team."""
    thin = dict(p1=(1, 116, "Core"), **{f"u{i}": (10 + i, None, "Core")
                                        for i in range(5)})
    full = {f"p{i}": (20 + i, 91, "Core") for i in range(6)}
    teams = {"Thin": _team("D", **thin), "Full": _team("D", **full)}
    rows = tr.standings(badges=_badges(thin, full), teams=teams)
    assert [(r["team"], r["league_place"]) for r in rows] == [
        ("Full", 1), ("Thin", None)]


# ------------------------------------------------------------- placing

def _flat(division, badge, base):
    return {f"p{i}": (base + i, badge, "Core") for i in range(4)}


def test_ties_share_a_place_and_the_next_place_is_skipped():
    teams_ = {"A": _flat("D", 111, 0), "B": _flat("D", 101, 10),
              "C": _flat("D", 101, 20), "D": _flat("D", 91, 30)}
    teams = {n: _team("D", **p) for n, p in teams_.items()}
    rows = tr.standings(badges=_badges(*teams_.values()), teams=teams)
    assert [(r["team"], r["league_place"]) for r in rows] == [
        ("A", 1), ("B", 2), ("C", 2), ("D", 4)]


def test_division_places_are_within_the_division_and_league_places_are_not():
    teams_ = {"P1": ("Premier", _flat("", 111, 0)),
              "P2": ("Premier", _flat("", 95, 10)),
              "D1": ("Div 1", _flat("", 101, 20)),
              "D2": ("Div 1", _flat("", 91, 30))}
    teams = {n: _team(div, **p) for n, (div, p) in teams_.items()}
    badges = _badges(*(p for _, p in teams_.values()))
    rows = {r["team"]: r for r in tr.standings(badges=badges, teams=teams)}
    assert [rows[n]["league_place"] for n in ("P1", "D1", "P2", "D2")] == [1, 2, 3, 4]
    assert [rows[n]["division_place"] for n in ("P1", "P2", "D1", "D2")] == [1, 2, 1, 2]
    only = tr.standings(division="Div 1", badges=badges, teams=teams)
    assert [(r["team"], r["league_place"]) for r in only] == [("D1", 2), ("D2", 4)]


# ---------------------------------------------- planted-strength simulation

def _kendall(order, truth):
    """Kendall's tau between a returned order and the planted one."""
    where = {t: i for i, t in enumerate(order)}
    pairs = agree = 0
    for i in range(len(truth)):
        for j in range(i + 1, len(truth)):
            pairs += 1
            agree += where[truth[i]] < where[truth[j]]
    return 2 * agree / pairs - 1


def test_standings_recover_planted_team_strength():
    """
    Plant a true strength per team as a ladder rung, draw six players around it
    (sd two rungs -- a team mixes Phantoms with an Ascendant or two), and check
    the standings put the teams back in planted order, across 200 leagues.

    Measured while writing this: Kendall tau 0.94 for rung averaging and 0.94
    for the raw badge mean at this spread (0.97/0.97 at one rung, 0.88/0.88 at
    four). Rung averaging is there to make the shown rank a real one, not to
    order better, so the test does not claim it orders better.
    """
    rng = random.Random(7)
    taus = []
    for _ in range(200):
        truth = {f"T{i:02d}": rng.uniform(40, 70) for i in range(16)}
        teams, badges = {}, {}
        for name, strength in truth.items():
            players = {}
            for k in range(6):
                rung = min(max(round(rng.gauss(strength, 2)), 0), tr.TOP_RUNG)
                account = len(badges) + 1
                badges[account] = tr.badge_at(rung)
                players[f"p{k}"] = (account, badges[account], "Core")
            teams[name] = _team("D", **players)
        planted = sorted(truth, key=lambda n: -truth[n])
        got = [r["team"] for r in tr.standings(badges=badges, teams=teams)]
        taus.append(_kendall(got, planted))
    assert sum(taus) / len(taus) > 0.85


# ---------------------------------------------------------- the fetch

def test_fetch_reads_the_badge_not_the_tier(monkeypatch):
    """In /v1/players/rank, `rank` is the tier. Reading it would make every
    Phantom 3 a 9."""
    calls = []

    def get_json(path, refresh=False, **params):
        ids = [int(a) for a in params["account_ids"].split(",")]
        calls.append(len(ids))
        return [{"account_id": a, "badge": 0 if a == 2 else 93, "rank": 9,
                 "subrank": 3, "last_match": None} for a in ids if a != 3]

    monkeypatch.setitem(sys.modules, "api", types.SimpleNamespace(get_json=get_json))
    got = tr.fetch_badges(list(range(1, 1502)))
    assert got[1] == 93
    assert 2 not in got and 3 not in got, "badge 0 and absent are both unranked"
    assert calls == [1000, 501], "1,000 accounts a call"


# ---------------------------------------------------- the roster builder

def test_an_empty_harvested_page_keeps_the_previous_roster(tmp_path):
    import build_rosters as br

    html = ("<!-- DSE-TEAM | 1 | Gone Quiet | Div 3 -->\n"
            "<!-- DSE-TEAM | 2 | Here | Div 3 -->\n")
    data = {"teams": {"Here": {"players": {"a": 5}}}}
    previous = {"generated": "2026-08-31T16:36:18",
                "teams": {"Gone Quiet": {"division": "Div 3",
                                         "players": {"b": 6}}}}
    assert br.carry_over_empty(data, html, previous) == ["Gone Quiet"]
    assert data["teams"]["Gone Quiet"]["stale_since"] == "2026-08-31"
    assert data["teams"]["Gone Quiet"]["players"] == {"b": 6}
