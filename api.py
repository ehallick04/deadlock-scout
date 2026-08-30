"""
api.py — talking to the Deadlock API. Nothing else.

This layer knows about HTTP: URLs, headers, retries, JSON parsing, caching.
It knows nothing about heroes, ranks, or players. If you find yourself
writing the word "hero" in this file, it belongs in deadlock.py instead.

    from api import get_json
    rank = get_json("/v1/players/104579843/rank")
"""

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
API_KEY = os.environ.get("DEADLOCK_API_KEY")


def get_json(path, timeout=30, retries=2, **params):
    """
    GET any endpoint and return parsed JSON.

        get_json("/v1/players/104579843/rank")
        get_json("/v1/players/hero-stats", account_ids="1,2", min_unix_timestamp=123)

    Pass a PATH, not a full URL - though a full URL is accepted too.

    Keyword arguments become the ?key=value query string. None values are
    dropped, so optional filters can be passed unset.
    """
    # Accept a path ("/v1/...") or a full URL. Passing a full URL to a
    # function that prepends BASE would produce a nonsense hostname and a
    # confusing "getaddrinfo failed" DNS error, so handle both.
    url = path if path.startswith("http") else BASE + path

    clean = {k: v for k, v in params.items() if v is not None}
    if clean:
        url += "?" + urllib.parse.urlencode(clean, doseq=True)

    headers = dict(HEADERS)
    if API_KEY:
        headers["X-API-Key"] = API_KEY

    req = urllib.request.Request(url, headers=headers)

    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)

        except urllib.error.HTTPError as e:
            # 429 = rate limited. Wait it out rather than crashing.
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


def get_cached(path, filename, refresh=False):
    """
    For static assets (heroes, items, ranks) that only change on patches.
    Reads the local file if it exists, otherwise fetches and saves it.
    """
    if os.path.exists(filename) and not refresh:
        with open(filename, encoding="utf-8") as f:
            return json.load(f)

    data = get_json(path)
    save_json(data, filename)
    print(f"fetched and saved {filename}")
    return data


def save_json(data, filename):
    """Write JSON to disk. encoding is explicit because Windows defaults to cp1252."""
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
