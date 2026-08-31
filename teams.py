"""
teams.py — pro team rosters.

Pure data. Edit the dictionary below when rosters change; nothing else in
the project needs to know.

Player ids are Steam friend codes / SteamID3 account ids.
"""

TEAMS = {
    # ---------------------------------------------------------- NA
    "Poppers Pupils": {
        "region": "NA",
        "players": {
            "Zeno": 1730032433,
            "LoMein": 1871021649,
            "Average": 399289886,
            "Lefaa": 114943410,
            "League": 1477660209,
            "Poppers": 1929248273,
        },
    },
    "Melee Creeps": {
        "region": "NA",
        "players": {
            "JonJon": 244109796,
            "Goober": 1285605078,
            "Birdee": 1871217631,
            "Braeden": 225764206,
            "Saiah": 25821887,
            "DMB": 1049966963,
        },
    },
    # ---------------------------------------------------------- EU
    "Buff Enjoyers": {
        "region": "EU",
        "players": {
            "Zerggy": 35187362,
            "Lystic": 97533101,
            "Cosmetical": 483770846,
            "Obikym": 911856667,
            "Vraic": 344946432,
            "Hoot": 87241154,
        },
    },
    "Leviathan": {
        "region": "EU",
        "players": {
            "Irezumi": 136898005,
            "Overseas": 984254212,
            "Empty dreams": 5278896,
            "Dimov": 298305257,
            "Sai": 1045169707,
            "sanya_sniper": 906011648,
        },
    },
    "Abrahams": {
        "region": "EU",
        "players": {
            "Together": 298155885,
            "Saintmxsm": 1055638874,
            "Oses": 1840902834,
            "Sharyk": 81662458,
            "Freemok": 1009703898,
            "Arzo": 124540485,
        },
    },
}

REGIONS = ["NA", "EU"]


def team_names(region=None):
    """Team names, optionally just one region's."""
    return [name for name, t in TEAMS.items()
            if region is None or t["region"] == region]


def roster(selection):
    """
    selection: a team name, a region ("NA" / "EU"), or "All".
    -> (list of account_ids, {account_id: {"ign":..., "team":..., "region":...}})
    """
    if selection == "All":
        names = team_names()
    elif selection in REGIONS:
        names = team_names(selection)
    elif selection in TEAMS:
        names = [selection]
    else:
        raise ValueError(f"unknown selection: {selection!r}")

    ids, labels = [], {}
    for team in names:
        info = TEAMS[team]
        for ign, account_id in info["players"].items():
            ids.append(account_id)
            labels[account_id] = {"ign": ign, "team": team,
                                  "region": info["region"]}
    return ids, labels


def choices():
    """Everything you can pick, in menu order."""
    return ["All", *REGIONS, *team_names()]


def find_player(account_id):
    """
    Is this account on any roster? -> {"ign", "team", "region"} or None.
    Used to recognise a sub who is themselves a pro on another team.
    """
    for team, info in TEAMS.items():
        for ign, aid in info["players"].items():
            if aid == account_id:
                return {"ign": ign, "team": team, "region": info["region"]}
    return None
