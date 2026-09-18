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
last_sample = {"min_badge": None, "count": 0}


def sample_note(asked_badge=0):
    """
    A warning when the store cannot answer the question that was asked.

    A ladder read is served from the deliberate sample, and the sample has its
    own badge floor. Ask for "any rank" against a Phantom-plus sweep and you get
    a Phantom-plus answer with nothing in the numbers to say so -- which is the
    same failure as mixing scouted matches into a ladder baseline, arriving by a
    different route. -> a string, or None when there is nothing to flag.
    """
    floor = last_sample.get("min_badge")
    if floor is None or floor <= int(asked_badge or 0):
        return None
    return (f"These are the {last_sample['count']:,} matches in the shared "
            f"store, and the lowest average badge in them is {floor} — not the "
            f"floor you asked for. The sample has only been swept that far "
            f"down, so read this as that skill band, not as the whole ladder.")


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
        last_sample["min_badge"] = got.get("sample_min_badge")
        last_sample["count"] = len(got.get("matches") or [])

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
