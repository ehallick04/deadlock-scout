"""
store.py — a local SQLite copy of the matches you care about.

The API is a shared, rate-limited, free service. Nothing here tries to mirror
all of it; that is millions of matches and would be both impossible under the
rate limits and rude. What this does mirror is your slice: the matches your
roster actually played, pulled through the bulk endpoint (up to 10,000
matches per request) and written to one file you can query instantly, offline,
as often as you like.

    python store.py sync              sync the pinned pro rosters
    python store.py sync 880934744 1170456491
    python store.py status
    python store.py assets            refresh the hero/item name tables

Everything downstream reads rows in the same shape match_builds() returns, so
the app and the menu do not care where they came from.
"""

import json
import os
import sqlite3
import sys
import time

from deadlock import (CUSTOMS_ONLY, DEFAULT_DAYS, DEFAULT_GAME_MODE,
                      all_ability_ids, bulk_build_rows, bulk_match_metadata,
                      bulk_matches, hero_names, hit_limit, item_names, mmss)

DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "deadlock.sqlite")

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS matches (
    match_id INTEGER PRIMARY KEY,
    start_time INTEGER,
    duration_s INTEGER,
    match_mode TEXT,
    game_mode TEXT,
    winning_team INTEGER,
    average_badge INTEGER
);
CREATE TABLE IF NOT EXISTS match_players (
    match_id INTEGER,
    account_id INTEGER,
    hero_id INTEGER,
    team INTEGER,
    won INTEGER,
    PRIMARY KEY (match_id, account_id)
);
CREATE TABLE IF NOT EXISTS purchases (
    match_id INTEGER,
    account_id INTEGER,
    item_id INTEGER,
    kind TEXT,
    bought_s REAL,
    sold_s REAL,
    PRIMARY KEY (match_id, account_id, item_id, bought_s)
);
CREATE TABLE IF NOT EXISTS heroes (hero_id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE IF NOT EXISTS items  (item_id INTEGER PRIMARY KEY, name TEXT);

CREATE INDEX IF NOT EXISTS ix_players_account ON match_players (account_id);
CREATE INDEX IF NOT EXISTS ix_purchases_account ON purchases (account_id);
CREATE INDEX IF NOT EXISTS ix_matches_start ON matches (start_time);
"""


def connect(path=None):
    # resolved at call time, not import time, so DB_FILE can be repointed
    conn = sqlite3.connect(path or DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def get_meta(conn, key, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn, key, value):
    conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (key, str(value)))


def sync_assets(conn, refresh=False):
    """Hero and item names, so the store can label rows without the API."""
    try:
        heroes = hero_names(refresh)
        items = item_names(refresh)
    except Exception as e:
        return {"error": str(e)}
    conn.executemany("INSERT INTO heroes (hero_id, name) VALUES (?, ?) "
                     "ON CONFLICT(hero_id) DO UPDATE SET name = excluded.name",
                     list(heroes.items()))
    conn.executemany("INSERT INTO items (item_id, name) VALUES (?, ?) "
                     "ON CONFLICT(item_id) DO UPDATE SET name = excluded.name",
                     list(items.items()))
    set_meta(conn, "assets_synced", int(time.time()))
    conn.commit()
    return {"heroes": len(heroes), "items": len(items)}


def sync(account_ids, days=DEFAULT_DAYS, match_mode=CUSTOMS_ONLY, limit=1000,
         conn=None, incremental=True, refresh=False,
         game_mode=DEFAULT_GAME_MODE):
    """
    Pull matches for these players and write them in.

    incremental starts from the highest match_id already stored, so a second
    sync only asks for what is new.

    -> {'matches','players','purchases','new_matches','watermark'}
    """
    close_after = conn is None
    conn = conn or connect()
    try:
        account_ids = [int(a) for a in account_ids]
        mark = get_meta(conn, "watermark")
        min_match_id = int(mark) if (incremental and mark) else None

        raw = bulk_match_metadata(account_ids, days=days,
                                  match_mode=match_mode, limit=limit,
                                  min_match_id=min_match_id, refresh=refresh,
                                  game_mode=game_mode)
        truncated = hit_limit(raw, limit)

        matches = bulk_matches(raw)
        before = conn.execute("SELECT COUNT(*) c FROM matches").fetchone()["c"]

        for m in matches:
            conn.execute(
                "INSERT INTO matches (match_id, start_time, duration_s, "
                "match_mode, game_mode, winning_team, average_badge) "
                "VALUES (?,?,?,?,?,?,?) ON CONFLICT(match_id) DO UPDATE SET "
                "start_time=excluded.start_time, duration_s=excluded.duration_s,"
                "winning_team=excluded.winning_team",
                (m["match_id"], m["start_time"], m["duration_s"],
                 m["match_mode"], m["game_mode"], m["winning_team"],
                 m["average_badge"]))
            for p in m["players"]:
                # the player's own outcome first; sides only as a fallback
                won = p.get("won")
                if won is None and (m["winning_team"] is not None
                                    and p["team"] is not None):
                    won = p["team"] == m["winning_team"]
                won = None if won is None else int(bool(won))
                conn.execute(
                    "INSERT INTO match_players (match_id, account_id, hero_id, "
                    "team, won) VALUES (?,?,?,?,?) "
                    "ON CONFLICT(match_id, account_id) DO UPDATE SET "
                    "hero_id=excluded.hero_id, team=excluded.team, "
                    "won=excluded.won",
                    (m["match_id"], p["account_id"], p["hero_id"], p["team"],
                     won))

        try:
            abilities = all_ability_ids()
        except Exception:
            abilities = set()

        rows = bulk_build_rows(raw, names={}, hero_lookup={},
                               ability_ids=abilities)
        for r in rows:
            conn.execute(
                "INSERT OR IGNORE INTO purchases (match_id, account_id, "
                "item_id, kind, bought_s, sold_s) VALUES (?,?,?,?,?,?)",
                (r["match_id"], r["account_id"], r["item_id"], r["kind"],
                 r["bought_s"], r["sold_s"]))

        high = max((m["match_id"] for m in matches), default=None)
        if high is not None:
            current = int(get_meta(conn, "watermark") or 0)
            set_meta(conn, "watermark", max(current, int(high)))
        set_meta(conn, "last_sync", int(time.time()))
        conn.commit()

        after = conn.execute("SELECT COUNT(*) c FROM matches").fetchone()["c"]
        return {"matches": len(matches), "truncated": truncated,
                "new_matches": after - before,
                "purchases": len(rows),
                "players": sum(len(m["players"]) for m in matches),
                "watermark": get_meta(conn, "watermark")}
    finally:
        if close_after:
            conn.close()


def status(conn=None):
    """What is in the store right now."""
    close_after = conn is None
    conn = conn or connect()
    try:
        def one(sql):
            return conn.execute(sql).fetchone()[0]
        last = get_meta(conn, "last_sync")
        path = DB_FILE
        size = os.path.getsize(path) if os.path.exists(path) else 0
        return {
            "file": path,
            "megabytes": round(size / 1_048_576, 2),
            "matches": one("SELECT COUNT(*) FROM matches"),
            "players": one("SELECT COUNT(DISTINCT account_id) FROM match_players"),
            "purchases": one("SELECT COUNT(*) FROM purchases"),
            "heroes": one("SELECT COUNT(*) FROM heroes"),
            "items": one("SELECT COUNT(*) FROM items"),
            "watermark": get_meta(conn, "watermark"),
            "last_sync": (time.strftime("%Y-%m-%d %H:%M",
                                        time.localtime(int(last)))
                          if last else "never"),
        }
    finally:
        if close_after:
            conn.close()


def has_data(conn=None):
    try:
        return status(conn)["purchases"] > 0
    except Exception:
        return False


def purchases(account_ids=(), days=None, labels=None, kind=None, conn=None,
              limit=200000):
    """
    Stored purchases, in the row shape match_builds() returns, so the app and
    the menu can use them interchangeably.
    """
    close_after = conn is None
    conn = conn or connect()
    labels = labels or {}
    try:
        sql = ["""
            SELECT p.match_id, p.account_id, p.item_id, p.kind, p.bought_s,
                   p.sold_s, mp.hero_id, mp.won, m.start_time,
                   COALESCE(i.name, 'item ' || p.item_id) AS item_name,
                   COALESCE(h.name, '') AS hero_name
            FROM purchases p
            JOIN match_players mp
              ON mp.match_id = p.match_id AND mp.account_id = p.account_id
            LEFT JOIN matches m ON m.match_id = p.match_id
            LEFT JOIN items i ON i.item_id = p.item_id
            LEFT JOIN heroes h ON h.hero_id = mp.hero_id
        """]
        where, args = [], []
        if account_ids:
            where.append("p.account_id IN (%s)"
                         % ",".join("?" * len(account_ids)))
            args += [int(a) for a in account_ids]
        if kind:
            where.append("p.kind = ?")
            args.append(kind)
        if days:
            where.append("m.start_time >= ?")
            args.append(int(time.time()) - int(days) * 86400)
        if where:
            sql.append("WHERE " + " AND ".join(where))
        sql.append("ORDER BY p.match_id DESC, p.account_id, p.bought_s")
        sql.append("LIMIT ?")
        args.append(limit)

        out = []
        for r in conn.execute(" ".join(sql), args):
            who = labels.get(r["account_id"], {})
            out.append({
                "match_id": r["match_id"],
                "start_time": r["start_time"],
                "account_id": r["account_id"],
                "player": who.get("ign") or str(r["account_id"]),
                "team": who.get("team", ""),
                "hero_id": r["hero_id"],
                "hero": r["hero_name"],
                "kind": r["kind"],
                "item": r["item_name"],
                "item_id": r["item_id"],
                "bought_s": r["bought_s"],
                "buy_time": mmss(r["bought_s"]),
                "sold_s": r["sold_s"],
                "won": None if r["won"] is None else bool(r["won"]),
            })
        return out
    finally:
        if close_after:
            conn.close()


def stored_players(conn=None):
    """-> [{'account_id','matches'}] most games first."""
    close_after = conn is None
    conn = conn or connect()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT account_id, COUNT(*) AS matches FROM match_players "
            "GROUP BY account_id ORDER BY matches DESC")]
    finally:
        if close_after:
            conn.close()


def query(sql, args=(), conn=None):
    """Read-only escape hatch for one-off questions."""
    if not sql.lstrip().lower().startswith(("select", "with")):
        raise ValueError("read-only: use SELECT")
    close_after = conn is None
    conn = conn or connect()
    try:
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        if close_after:
            conn.close()


def main(argv):
    action = argv[1] if len(argv) > 1 else "status"
    conn = connect()

    if action == "status":
        for k, v in status(conn).items():
            print(f"  {k:12} {v}")

    elif action == "assets":
        print("  syncing hero and item names ...")
        print(" ", sync_assets(conn, refresh=True))

    elif action == "sync":
        ids = [int(a) for a in argv[2:] if a.isdigit()]
        if not ids:
            from teams import PINNED, roster_many
            ids, _ = roster_many(sorted(PINNED))
            print(f"  no ids given — using the {len(ids)} pinned pros")
        if not get_meta(conn, "assets_synced"):
            print(" ", sync_assets(conn))
        print(f"  syncing {len(ids)} players ...")
        result = sync(ids, conn=conn)
        for k, v in result.items():
            print(f"  {k:12} {v}")
        if result.get("truncated"):
            print("  NOTE: the response came back full, newest first — run "
                  "sync again to pull the next batch")
        print("\n  now:")
        for k, v in status(conn).items():
            print(f"  {k:12} {v}")

    else:
        print(__doc__)
    conn.close()


if __name__ == "__main__":
    main(sys.argv)