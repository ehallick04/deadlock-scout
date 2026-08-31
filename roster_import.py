"""
roster_import.py — turn a team portal page into a roster.

Handles pages from the DSE player portal (players.dse.gg), which link each
player to their statlocker profile AND their Steam profile, and mark them
core / substitute / point-of-contact. Falls back to a generic link scrape
for any other site.

GETTING THE PAGE
    Open the team page in your browser and either
      - copy it: Ctrl+A, Ctrl+C  (or devtools: right-click <html> -> Copy element)
        and paste it into the app's "Paste page" box, or
      - save it: Ctrl+S -> "Webpage, Complete", then point this at the file.

USAGE
    python roster_import.py page.html              # show what it found
    python roster_import.py page.html --run        # ...and report on them
    python roster_import.py page.html --save       # print a teams.py block
"""

import html as html_mod
import re
import sys

STEAMID64_BASE = 76561197960265728

ROLE_LABELS = {"core": "Core", "substitute": "Sub", "poc": "POC"}

# --- DSE portal: <article class="roster-player roster-player--core"> ...
DSE_PLAYER = re.compile(
    r'<article[^>]*class="[^"]*roster-player[^"]*roster-player--(\w+)[^"]*"[^>]*>(.*?)</article>',
    re.I | re.S)
DSE_NAME = re.compile(
    r'class="roster-player__name"[^>]*href="([^"]*)"[^>]*>(.*?)</a>', re.I | re.S)
DSE_PERSONA = re.compile(r'</a>\s*<p>(.*?)</p>', re.I | re.S)

# --- DSE directory: <a class="team-card" href="/teams/199/"> ... </a>
DSE_TEAM_CARD = re.compile(
    r'<a[^>]*class="[^"]*team-card[^"]*"[^>]*href="([^"]*?/teams/(\d+)/?[^"]*)"[^>]*>(.*?)</a>',
    re.I | re.S)
DSE_TEAM_CARD_ALT = re.compile(          # href before class
    r'<a[^>]*href="([^"]*?/teams/(\d+)/?[^"]*)"[^>]*class="[^"]*team-card[^"]*"[^>]*>(.*?)</a>',
    re.I | re.S)
DSE_TEAM_NAME = re.compile(r'class="team-card__name"[^>]*>(.*?)<', re.I | re.S)

# --- generic fallback
GENERIC = (
    (re.compile(r'href=["\']([^"\']*statlocker\.gg/profile/(\d+)[^"\']*)["\']', re.I), "id3"),
    (re.compile(r'href=["\']([^"\']*deadlock-api\.com/players/(\d+)[^"\']*)["\']', re.I), "id3"),
    (re.compile(r'href=["\']([^"\']*steamcommunity\.com/profiles/(\d{17})[^"\']*)["\']', re.I), "id64"),
    (re.compile(r'href=["\']([^"\']*/profile/(\d{5,})[^"\']*)["\']', re.I), "id3"),
)
ANCHOR = re.compile(
    r'<a\b[^>]*href=["\'][^"\']*(?:statlocker\.gg/profile/|steamcommunity\.com/profiles/'
    r'|deadlock-api\.com/players/|/profile/)(\d{5,})[^"\']*["\'][^>]*>(.*?)</a>',
    re.I | re.S)


def strip_tags(fragment):
    text = re.sub(r"<[^>]+>", " ", fragment or "")
    return re.sub(r"\s+", " ", html_mod.unescape(text)).strip()


def to_account_id(value, kind="id3"):
    n = int(value)
    if kind == "id64" or n > STEAMID64_BASE:
        return n - STEAMID64_BASE
    return n


def team_name_from(html):
    """The <h1> of a portal page is the team name."""
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.I | re.S)
    if m:
        name = strip_tags(m.group(1))
        if name:
            return name
    m = re.search(r"<title>(.*?)</title>", html, re.I | re.S)
    return strip_tags(m.group(1)).split(" - ")[0] if m else ""


# --------------------------------------------------------------- parsers

def parse_dse_team(html):
    """
    A DSE team page.
    -> {'team': str, 'players': [{'account_id','ign','persona','role','url'}]}
    """
    players = []
    for match in DSE_PLAYER.finditer(html):
        role_key, body = match.group(1).lower(), match.group(2)

        name = DSE_NAME.search(body)
        if not name:
            continue
        url, ign = name.group(1), strip_tags(name.group(2))

        account_id = None
        got = re.search(r"/profile/(\d+)", url)
        if got:
            account_id = to_account_id(got.group(1))
        else:                                   # fall back to the Steam link
            steam = re.search(r"steamcommunity\.com/profiles/(\d{17})", body)
            if steam:
                account_id = to_account_id(steam.group(1), "id64")
        if not account_id:
            continue

        persona = DSE_PERSONA.search(body)
        players.append({
            "account_id": account_id,
            "ign": ign,
            "persona": strip_tags(persona.group(1)) if persona else "",
            "role": ROLE_LABELS.get(role_key, role_key.title()),
            "url": url,
        })

    return {"team": team_name_from(html), "players": players}


def parse_dse_directory(html):
    """
    The all-teams directory page.
    -> [{'team': str, 'team_id': int, 'url': str}]
    """
    teams, seen = [], set()
    for pattern in (DSE_TEAM_CARD, DSE_TEAM_CARD_ALT):
        for m in pattern.finditer(html):
            url, team_id, body = m.group(1), int(m.group(2)), m.group(3)
            if team_id in seen:
                continue
            seen.add(team_id)
            name = DSE_TEAM_NAME.search(body)
            teams.append({
                "team": strip_tags(name.group(1)) if name else strip_tags(body)[:40],
                "team_id": team_id,
                "url": url,
            })

    # last resort: any /teams/<id>/ link with text
    if not teams:
        for m in re.finditer(r'<a[^>]*href="([^"]*?/teams/(\d+)/?[^"]*)"[^>]*>(.*?)</a>',
                             html, re.I | re.S):
            tid = int(m.group(2))
            if tid in seen:
                continue
            label = strip_tags(m.group(3))
            if not label:
                continue
            seen.add(tid)
            teams.append({"team": label[:40], "team_id": tid, "url": m.group(1)})

    return sorted(teams, key=lambda t: t["team"].lower())


def parse_roster(html):
    """Generic: any profile link anywhere on the page."""
    names = {}
    for m in ANCHOR.finditer(html):
        text = strip_tags(m.group(2))
        if text:
            names.setdefault(to_account_id(m.group(1)), text[:40])

    found, seen = [], set()
    for pattern, kind in GENERIC:
        for m in pattern.finditer(html):
            account_id = to_account_id(m.group(2), kind)
            if account_id <= 0 or account_id in seen:
                continue
            seen.add(account_id)
            found.append({"account_id": account_id, "ign": names.get(account_id, ""),
                          "persona": "", "role": "", "url": m.group(1)})
    return found


def parse_any(html):
    """
    Work out what kind of page this is.
    -> {'kind': 'team'|'directory'|'generic', 'team': str, 'players': [...],
        'teams': [...]}
    """
    team = parse_dse_team(html)
    if team["players"]:
        return {"kind": "team", "team": team["team"],
                "players": team["players"], "teams": []}

    directory = parse_dse_directory(html)
    if directory:
        return {"kind": "directory", "team": "", "players": [], "teams": directory}

    generic = parse_roster(html)
    return {"kind": "generic", "team": team_name_from(html),
            "players": generic, "teams": []}


# --------------------------------------------------------------- output

def as_teams_entry(team_name, region, players):
    """A block you can paste straight into TEAMS in teams.py."""
    lines = [f'    "{team_name}": {{',
             f'        "region": "{region}",',
             '        "players": {']
    for i, p in enumerate(players, 1):
        label = (p.get("ign") or p.get("persona") or f"player{i}").replace('"', "'")
        note = f'  # {p["role"]}' if p.get("role") else ""
        lines.append(f'            "{label}": {p["account_id"]},{note}')
    lines += ["        },", "    },"]
    return "\n".join(lines)


def read_html(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)

    try:
        page = read_html(args[0])
    except OSError as e:
        sys.exit(f"could not read {args[0]}: {e}")

    result = parse_any(page)

    if result["kind"] == "directory":
        print(f"team directory — {len(result['teams'])} teams:\n")
        for t in result["teams"]:
            print(f"  {t['team_id']:<6} {t['team']}")
        sys.exit()

    players = result["players"]
    if not players:
        sys.exit("No players found. If the page builds itself with JavaScript, "
                 "copy it from devtools (right-click <html> -> Copy element) "
                 "instead of using Ctrl+S.")

    print(f"{result['team'] or 'roster'} — {len(players)} players\n")
    for p in players:
        print(f"  {p['role'] or '-':<6} {p['ign']:<22} {p['account_id']:<12} "
              f"{p['persona']}")

    if "--save" in args:
        i = args.index("--save")
        name = args[i + 1] if len(args) > i + 1 and not args[i + 1].startswith("--") \
            else result["team"] or "New Team"
        region = args[args.index("--region") + 1] if "--region" in args else "NA"
        print("\npaste this into TEAMS in teams.py:\n")
        print(as_teams_entry(name, region, players))

    if "--run" in args:
        from main import run
        ids = [p["account_id"] for p in players]
        labels = {p["account_id"]: {"ign": p["ign"], "team": result["team"],
                                    "region": ""} for p in players}
        print()
        run(ids, days=14, top=5, match_mode="private_lobby", labels=labels)
