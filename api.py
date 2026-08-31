"""
api.py — talking to the Deadlock API. Nothing else.

This layer knows about HTTP: URLs, headers, retries, JSON parsing, and an
on-disk cache. It knows nothing about heroes, ranks, or players.

    from api import get_json
    rank = get_json("/v1/players/104579843/rank")

CACHING
-------
Every response is written to cache/ as JSON, keyed by the full URL. A
repeat request inside the time-to-live is served from disk and never
touches the network. How long each kind of data stays fresh:

    /v1/assets/...        7 days    heroes, items, ranks - patch data
    .../metadata          1 year    a finished match never changes
    /v1/players/steam     1 day     display names
    everything else       6 hours   ranks, hero-stats

Pass refresh=True to force a live fetch, or call clear_cache().
"""

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://api.deadlock-api.com"

# The API sits behind Cloudflare, which blocks Python's default user agent.
# Sending a real one is not optional.
HEADERS = {
    "User-Agent": "my-deadlock-project/0.1",
    "Accept": "application/json",
}

# Optional. An API key raises most rate limits.
#   PowerShell:  $env:DEADLOCK_API_KEY = "your-key"
#   macOS:       export DEADLOCK_API_KEY="your-key"
API_KEY = os.environ.get("DEADLOCK_API_KEY")

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
CACHE_ENABLED = True

HOUR, DAY = 3600, 86400
DEFAULT_TTL = 6 * HOUR

# First matching fragment wins, so order matters.
TTL_RULES = (
    ("/v1/assets/", 7 * DAY),
    ("/metadata", 365 * DAY),        # finished matches are immutable
    ("/v1/players/steam", 1 * DAY),
    ("/v1/matches/active", 5 * 60),  # live data, barely worth caching
)


def ttl_for(path):
    for fragment, ttl in TTL_RULES:
        if fragment in path:
            return ttl
    return DEFAULT_TTL


# --------------------------------------------------------------- cache

def _cache_file(url):
    name = hashlib.sha256(url.encode()).hexdigest()[:32]
    return os.path.join(CACHE_DIR, name + ".json")


def _cache_read(url, ttl):
    if not CACHE_ENABLED:
        return None
    path = _cache_file(url)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            entry = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None                      # corrupt entry: treat as a miss

    if time.time() - entry.get("fetched", 0) > ttl:
        return None                      # stale
    return entry.get("data")


def _cache_write(url, data):
    if not CACHE_ENABLED:
        return
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp = _cache_file(url) + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"url": url, "fetched": time.time(), "data": data}, f)
        os.replace(tmp, _cache_file(url))   # atomic, so a crash can't corrupt
    except OSError:
        pass                                # a read-only disk is not fatal


def cache_info():
    """-> {'entries': n, 'megabytes': x, 'oldest_hours': h}"""
    if not os.path.isdir(CACHE_DIR):
        return {"entries": 0, "megabytes": 0.0, "oldest_hours": 0.0}
    files = [os.path.join(CACHE_DIR, f) for f in os.listdir(CACHE_DIR)
             if f.endswith(".json")]
    size = sum(os.path.getsize(f) for f in files)
    oldest = min((os.path.getmtime(f) for f in files), default=time.time())
    return {
        "entries": len(files),
        "megabytes": round(size / 1_048_576, 2),
        "oldest_hours": round((time.time() - oldest) / 3600, 1),
    }


def clear_cache(older_than_hours=None):
    """Delete cached responses. -> number removed."""
    if not os.path.isdir(CACHE_DIR):
        return 0
    cutoff = time.time() - (older_than_hours or 0) * 3600
    removed = 0
    for name in os.listdir(CACHE_DIR):
        path = os.path.join(CACHE_DIR, name)
        if older_than_hours is None or os.path.getmtime(path) < cutoff:
            try:
                os.remove(path)
                removed += 1
            except OSError:
                pass
    return removed


# --------------------------------------------------------------- http

def _fetch(url, timeout, retries):
    """The actual network call. Everything else is caching around this."""
    headers = dict(HEADERS)
    if API_KEY:
        headers["X-API-Key"] = API_KEY
    req = urllib.request.Request(url, headers=headers)

    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)

        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries:
                wait = int(e.headers.get("Retry-After") or 2 ** (attempt + 1))
                print(f"    rate limited, waiting {wait}s")
                time.sleep(wait)
                continue
            raise

        except urllib.error.URLError:
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
            raise


def get_json(path, timeout=30, retries=2, refresh=False, ttl=None, **params):
    """
    GET any endpoint and return parsed JSON, from cache when it is fresh.

        get_json("/v1/players/104579843/rank")
        get_json("/v1/players/hero-stats", account_ids="1,2")
        get_json("/v1/players/104579843/rank", refresh=True)   # skip cache

    Keyword arguments become the ?key=value query string; None values are
    dropped so optional filters can be left unset. Pass a path, not a
    full URL - though a full URL is accepted too.
    """
    url = path if path.startswith("http") else BASE + path
    clean = {k: v for k, v in params.items() if v is not None}
    if clean:
        url += "?" + urllib.parse.urlencode(clean, doseq=True)

    if not refresh:
        cached = _cache_read(url, ttl if ttl is not None else ttl_for(path))
        if cached is not None:
            return cached

    data = _fetch(url, timeout, retries)
    _cache_write(url, data)
    return data


def get_cached(path, filename, refresh=False):
    """
    Kept for the hero asset file, which is nice to have as readable JSON
    sitting in the project folder rather than buried in cache/.
    """
    if os.path.exists(filename) and not refresh:
        with open(filename, encoding="utf-8") as f:
            return json.load(f)

    data = get_json(path, refresh=refresh)
    save_json(data, filename)
    print(f"fetched and saved {filename}")
    return data


def save_json(data, filename):
    """Write JSON to disk. encoding is explicit because Windows defaults to cp1252."""
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
