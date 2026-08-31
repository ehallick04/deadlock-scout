"""
build_rosters.py — turn saved DSE pages into rosters.json.

Two ways to feed it:

  1. A harvested bundle (one file, all teams). On the directory page in your
     browser, click the harvester bookmarklet (main.py option B prints it,
     or the app's "Bake in every league team" sidebar panel),
     save dse_rosters.html, then:

         python build_rosters.py dse_rosters.html

  2. A folder of saved team pages (File > Save Page As on each team page).
     Drop the directory page in there too and divisions get filled in:

         python build_rosters.py pages/

rosters.json is plain data and is meant to be committed, so teammates and
the deployed app get the rosters without re-saving anything.
"""

import datetime
import json
import os
import sys

from roster_import import (is_bundle, parse_bundle, parse_dse_directory,
                           parse_dse_team, read_html, team_name_from)

OUT = "rosters.json"


def _entry(team, team_id, division, players):
    """One team -> the shape teams.py expects."""
    names, roles, seen = {}, {}, {}
    for i, p in enumerate(players, 1):
        label = p.get("ign") or p.get("persona") or f"player{i}"
        if label in seen and seen[label] != p["account_id"]:
            label = f"{label} ({p['account_id']})"      # two players, one name
        seen[label] = p["account_id"]
        names[label] = p["account_id"]
        if p.get("role"):
            roles[label] = p["role"]
    return {"team_id": team_id, "division": division, "region": "",
            "players": names, "roles": roles}


def from_bundle(html):
    return {t["team"]: _entry(t["team"], t["team_id"], t["division"],
                              t["players"])
            for t in parse_bundle(html) if t["players"]}


def from_folder(path):
    """Every .html in a folder: team pages, plus the directory for divisions."""
    teams, divisions = {}, {}
    files = sorted(f for f in os.listdir(path)
                   if f.lower().endswith((".html", ".htm", ".txt")))
    for name in files:
        full = os.path.join(path, name)
        try:
            html = read_html(full)
        except OSError as e:
            print(f"  skip {name}: {e}")
            continue

        if is_bundle(html):
            teams.update(from_bundle(html))
            print(f"  {name}: bundle")
            continue

        listing = parse_dse_directory(html)
        parsed = parse_dse_team(html)
        if listing and not parsed["players"]:
            for t in listing:
                divisions[t["team_id"]] = t["division"]
                divisions[t["team"].lower()] = t["division"]
            print(f"  {name}: directory — {len(listing)} teams")
            continue

        if not parsed["players"]:
            print(f"  {name}: no players found")
            continue

        # a saved team page does not carry its own team id (the only
        # /teams/<id>/ link on it is the "My team" nav link), so it stays 0
        # and the division is matched by name against the directory page
        team = parsed["team"] or team_name_from(html) or name
        teams[team] = _entry(team, 0, "", parsed["players"])
        print(f"  {name}: {team} — {len(parsed['players'])} players")

    for team, info in teams.items():
        if not info["division"]:
            info["division"] = (divisions.get(info["team_id"])
                                or divisions.get(team.lower(), ""))
    return teams


def build(source):
    if os.path.isdir(source):
        teams = from_folder(source)
    else:
        html = read_html(source)
        if is_bundle(html):
            teams = from_bundle(html)
        else:
            parsed = parse_dse_team(html)
            if not parsed["players"]:
                raise SystemExit("no rosters in that file — is it a team page?")
            teams = {parsed["team"]: _entry(parsed["team"], 0, "",
                                            parsed["players"])}
    return {
        "generated": datetime.datetime.now().isoformat(timespec="seconds"),
        "source": os.path.abspath(source),
        "teams": dict(sorted(teams.items(), key=lambda kv: kv[0].lower())),
    }


def main(argv):
    if len(argv) < 2:
        raise SystemExit(__doc__)
    data = build(argv[1])
    out = argv[2] if len(argv) > 2 else OUT
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)

    teams = data["teams"]
    players = sum(len(t["players"]) for t in teams.values())
    empty = [n for n, t in teams.items() if not t["players"]]
    print(f"\nwrote {out}: {len(teams)} teams, {players} players")
    if empty:
        print(f"  {len(empty)} with no ids: {', '.join(empty[:5])}"
              + (" ..." if len(empty) > 5 else ""))
    print("  commit rosters.json so the deployed app picks it up")


if __name__ == "__main__":
    main(sys.argv)
