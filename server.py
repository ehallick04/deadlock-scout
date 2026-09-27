"""
server.py — routing match metadata through the Deadlock Scout server.

The server is the only thing that talks to api.deadlock-api.com, holds the
credentials, keeps the accumulated match store and enforces who may see what.
This module is the client half of that: it logs in, asks for matches, and
converts what comes back into the shape the rest of this project already parses.

**It is entirely opt-in.** With DEADLOCK_SERVER unset, `configured()` is False,
api.py never calls in here, and the app behaves exactly as it did before — which
is the point: pointing at the server should never be able to break a working
report.

Configure by environment, or by Streamlit secrets when deployed:

    export DEADLOCK_SERVER="http://localhost:8000"
    export DEADLOCK_SERVER_EMAIL="you@example.com"
    export DEADLOCK_SERVER_PASSWORD="..."

A password in the environment is fine for your own machine. A distributed
client should put the refresh token in the OS keychain instead and never hold
the password at all.
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT = 60

# The server returns its own normalised records rather than echoing the API.
# These are the field names the rest of the project reads, so this module
# converts once, here, and nothing downstream has to know the server exists.
_SIDE = {True: 1, False: 0}


def _secret(name, default=None):
    """Environment first, then Streamlit secrets when deployed."""
    value = os.environ.get(name)
    if value:
        return value
    try:
        import streamlit as st
        return st.secrets.get(name, default)  # type: ignore[no-any-return]
    except Exception:
        return default


def base_url():
    url = _secret("DEADLOCK_SERVER")
    return url.rstrip("/") if url else None


def configured():
    return bool(base_url())


# --------------------------------------------------------------- auth

_token = {"access": None, "refresh": None, "expires": 0.0}


def _post(path, body=None, token=None):
    url = base_url() + path
    data = json.dumps(body or {}).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, headers=headers,
                                     method="POST")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        detail = ""
        try:
            detail = json.load(error).get("detail", "")
        except Exception:
            pass
        raise ServerError(f"{path} returned {error.code}"
                          + (f": {detail}" if detail else "")) from error
    except urllib.error.URLError as error:
        raise ServerError(f"could not reach {base_url()}: {error.reason}") from error


class ServerError(RuntimeError):
    """The server refused or could not be reached. Distinct from an upstream
    failure, which the server reports with source='upstream'."""


def _login():
    email = _secret("DEADLOCK_SERVER_EMAIL")
    password = _secret("DEADLOCK_SERVER_PASSWORD")
    if not (email and password):
        raise ServerError("DEADLOCK_SERVER is set but "
                          "DEADLOCK_SERVER_EMAIL/PASSWORD are not")
    got = _post("/auth/login", {"email": email, "password": password})
    _remember(got)


def _remember(got):
    _token["access"] = got["access_token"]
    _token["refresh"] = got.get("refresh_token")
    # renew a little early rather than discovering expiry mid-report
    _token["expires"] = time.time() + max(30, int(got.get("expires_in", 0)) - 60)


def token():
    if _token["access"] and time.time() < _token["expires"]:
        return _token["access"]
    if _token["refresh"]:
        try:
            _remember(_post("/auth/refresh",
                            {"refresh_token": _token["refresh"]}))
            return _token["access"]
        except ServerError:
            # refresh tokens rotate and are single-use; a failure here just
            # means starting again, not that anything is wrong
            _token["refresh"] = None
    _login()
    return _token["access"]


def whoami():
    url = base_url() + "/auth/me"
    request = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {token()}"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.load(response)


# --------------------------------------------------------------- matches

# What the last ladder read could actually answer, as opposed to what was asked
# for. Kept here so the page can say so without threading a return value
# through every loader.
last_sample = {"min_badge": None, "frame_badge": None, "count": 0,
               "from": "", "to": ""}


def sample_note(asked_badge=0):
    """
    What the sample actually was, when that differs from what was asked.

    Two things can differ and both matter. The floor: ask for "any rank" against
    a Phantom-plus sweep and you get a Phantom-plus answer. And the window: each
    sweep frame covers however many days its own matches took to fill, so a
    ladder-wide frame spans far less time than a high-badge one at the same
    size. -> a string, or None when there is nothing to flag.
    """
    floor = last_sample.get("min_badge")
    if floor is None:
        return None
    asked = int(asked_badge or 0)
    window = ""
    if last_sample.get("from") and last_sample.get("to"):
        window = (f" They run {last_sample['from']} to {last_sample['to']}, "
                  f"which is the whole window this sample covers.")
    if floor > asked:
        return (f"These are the {last_sample['count']:,} matches the shared "
                f"store holds, and the lowest average badge among them is "
                f"{floor} — not the floor you asked for. The sweep has only "
                f"reached that far down, so read this as that skill band "
                f"rather than the whole ladder.{window}")
    if window:
        return None
    return None


def sample_line():
    """A short description of the sample behind the current numbers."""
    frame = last_sample.get("frame_badge")
    if frame is None or not last_sample.get("count"):
        return None
    span = ""
    if last_sample.get("from") and last_sample.get("to"):
        span = f" · {last_sample['from']} to {last_sample['to']}"
    return (f"Drawn from the badge {frame}+ sweep: "
            f"{last_sample['count']:,} matches{span}")


def _as_list(value):
    if value in (None, "", []):
        return None
    if isinstance(value, (list, tuple, set)):
        return [int(v) for v in value]
    return [int(v) for v in str(value).split(",") if v.strip()]


def metadata(**params):
    """
    Answer a /v1/matches/metadata-shaped request from the server.

    Which endpoint depends on what was asked, and the difference matters:

    * named match ids       -> /matches/by-id, which fetches anything missing
    * named accounts        -> /matches/by-account, scope-checked, also fetches
    * a badge floor only    -> /matches/ladder, which reads the deliberate
                               sample and never fetches

    That last one is deliberate. A ladder-wide question must be answered from
    the badge-filtered sweep, not from whatever happens to be in the store,
    because the store also holds matches pulled to scout particular players --
    a sample of scouting interest rather than of the ladder.
    """
    match_ids = _as_list(params.get("match_ids"))
    account_ids = _as_list(params.get("account_ids"))
    limit = int(params.get("limit") or 1000)

    if match_ids:
        got = _post("/matches/by-id", {"match_ids": match_ids}, token())
    elif account_ids:
        got = _post("/matches/by-account", {
            "account_ids": account_ids,
            "days": _days_from(params),
            "game_mode": params.get("game_mode"),
            "match_mode": params.get("match_mode"),
            "limit": limit,
        }, token())
    else:
        query = {"min_average_badge": int(params.get("min_average_badge") or 0),
                 "limit": limit}
        if params.get("min_unix_timestamp"):
            query["min_unix_timestamp"] = int(params["min_unix_timestamp"])
        got = _post("/matches/ladder?" + urllib.parse.urlencode(query),
                    None, token())
        last_sample.update({
            "min_badge": got.get("sample_min_badge"),
            "frame_badge": got.get("sample_frame_badge"),
            "count": len(got.get("matches") or []),
            "from": (got.get("sample_from") or "")[:10],
            "to": (got.get("sample_to") or "")[:10],
        })

    return [to_upstream(record) for record in got.get("matches", [])]


def _days_from(params):
    floor = params.get("min_unix_timestamp")
    if not floor:
        return 30
    days = (time.time() - int(floor)) / 86400
    return max(1, min(365, int(round(days))))


def to_upstream(record):
    """
    A server record in the shape the rest of this project parses.

    deadlock.py reads sides and outcomes through norm_side/player_won, which
    accept the integer convention as readily as the API's strings, so the
    booleans go back out as 0/1 and 1/2 rather than being re-stringified.

    Items are rebuilt as dicts carrying a time, because a purchase list is
    recognised by each entry having *both* an id and a timestamp — a list of
    bare ids is not seen as items at all.
    """
    players = []
    for player in record.get("players") or []:
        out = {
            "account_id": player.get("account_id"),
            "hero_id": player.get("hero_id"),
            "kills": player.get("kills"),
            "deaths": player.get("deaths"),
            "assists": player.get("assists"),
            "net_worth": player.get("net_worth"),
            "last_hits": player.get("last_hits"),
            "denies": player.get("denies"),
            "assigned_lane": player.get("assigned_lane"),
        }
        if player.get("is_team1") is not None:
            out["team"] = _SIDE[bool(player["is_team1"])]
        if player.get("won") is not None:
            out["player_match_outcome"] = 1 if player["won"] else 2
        items = []
        for row in player.get("items") or []:
            item_id = row[0] if len(row) > 0 else None
            bought = row[1] if len(row) > 1 else None
            sold = row[2] if len(row) > 2 else None
            if item_id is None:
                continue
            entry = {"item_id": item_id}
            if bought is not None:
                entry["game_time_s"] = bought
            if sold is not None:
                entry["sold_time_s"] = sold
            items.append(entry)
        if items:
            out["items"] = items
        players.append(out)

    match = {
        "match_id": record.get("match_id"),
        "start_time": record.get("start_time"),
        "duration_s": record.get("duration_s"),
        "average_badge": record.get("average_badge"),
        "game_mode": record.get("game_mode"),
        "match_mode": record.get("match_mode"),
        "banned_hero_ids": record.get("banned_hero_ids"),
        "not_scored": record.get("not_scored"),
        "players": players,
    }
    if record.get("team1_won") is not None:
        match["winning_team"] = _SIDE[bool(record["team1_won"])]
    return match


def stats():
    url = base_url() + "/matches/stats"
    request = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {token()}"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.load(response)


def objectives(account_ids=None, days=90, match_mode=None, game_mode=None,
               min_average_badge=0, limit=4000, min_events=30):
    """
    The objective report, from the server.

    With no account_ids this describes the deliberate ladder sample. With them
    it describes those players' games and returns the ladder alongside as a
    baseline, because a few hundred tournament customs cannot support a
    conditional win rate but can certainly support a timing comparison.
    """
    body = {"days": int(days), "min_average_badge": int(min_average_badge),
            "limit": int(limit), "min_events": int(min_events)}
    if account_ids:
        body["account_ids"] = [int(a) for a in account_ids][:200]
    if match_mode:
        body["match_mode"] = match_mode
    if game_mode:
        body["game_mode"] = game_mode
    return _post("/matches/objectives", body, token())


def status():
    """A line for the sidebar: where we are pointed and what the store holds."""
    if not configured():
        return "direct to api.deadlock-api.com"
    try:
        me = whoami()
    except ServerError as error:
        return f"{base_url()} — not reachable ({error})"
    scopes = len(me.get("permissions") or [])
    role = "admin" if me.get("is_admin") else f"{scopes} grant(s)"
    line = f"{base_url()} — {me.get('email')} ({role})"
    try:
        held = stats()
    except Exception:
        return line
    floor = held.get("sample_min_badge")
    parts = [f"{held.get('matches_total', 0):,} matches"]
    if floor is not None:
        parts.append(f"badge {floor}+")
    span = (held.get("earliest_start"), held.get("latest_start"))
    if all(span):
        parts.append(f"{span[0][:10]} to {span[1][:10]}")
    return line + "  ·  store: " + ", ".join(parts)


# --------------------------------------------------------------- players

def find_players(badge, spread=10, days=14, match_mode="ranked",
                 min_games=10, min_games_per_week=0.0,
                 sort_by="shrunk_win_rate", limit=50, pool_limit=2000,
                 include_names=True, include_ranks=True,
                 own_rank_in_band=True, rank_check_limit=600):
    """
    Active players in a badge band, ranked by what the data can support.

    A band rather than a floor, because a floor includes everything above it and
    the population thins sharply upwards -- at a middling rank that returns a
    sample with almost nobody in it twice over.

    The ordering is a shrunk win rate by default, not the observed one. On the
    accumulated sweep, the top decile by win rate over half a window averaged
    75.2% there and 51.9% over the other half; the observed rate is very nearly
    all sampling. `sort_by="win_rate"` is available so the two orders can be
    compared, which is a more convincing argument than the caveat is.
    """
    return _post("/players/find", {
        "badge": int(badge), "spread": int(spread), "days": int(days),
        "match_mode": match_mode, "min_games": int(min_games),
        "min_games_per_week": float(min_games_per_week),
        "sort_by": sort_by, "limit": int(limit),
        "pool_limit": int(pool_limit),
        # Personanames and each candidate's own badge. One extra call each, made
        # only for the rows that survive the gates, and neither can fail the
        # scan -- a lookup that breaks reports itself in `label_problems` and
        # leaves that column blank.
        "include_names": bool(include_names),
        "include_ranks": bool(include_ranks),
        # The band matches on each game's average badge, so without this a
        # player two tiers up qualifies by playing a few games down -- and the
        # win-rate sort promotes them, because they win the games they play
        # down. On by default: "at my rank" is what the question means.
        "own_rank_in_band": bool(own_rank_in_band),
        "rank_check_limit": int(rank_check_limit),
    }, token())


def profile_players(account_ids, days=90, match_mode="ranked",
                    include_history=True, include_names=True):
    """
    The drill-down: top heroes, ranked win rate, activity and rank trajectory.

    Capped at 25 accounts server-side -- the history and rank reads are one call
    each, and this is a proxy for somebody else's free service. `days` defaults
    longer than the scan's window because a win rate needs the games while the
    activity numbers are counts.
    """
    return _post("/players/profile", {
        "account_ids": [int(a) for a in account_ids][:25],
        "days": int(days), "match_mode": match_mode,
        "include_history": bool(include_history),
        "include_names": bool(include_names),
    }, token())
