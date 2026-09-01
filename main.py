"""
main.py — the runner. Menu, command-line arguments, and printing.

This is the only file that talks to the user. It calls deadlock.py for
data and formats it. No HTTP and no game logic lives here.

    python main.py                                  # interactive menu
    python main.py 880934744 104579843
    python main.py https://statlocker.gg/profile/880934744/matches?mode=standard
    python main.py --file ids.txt --days 30 --top 5 --csv players.csv
    python main.py --pros All                      # customs where 4+ teammates played together
    python main.py --pros Leviathan --together 5   # stricter: 5+ of the roster
    python main.py --pros Leviathan --subs         # also report stand-ins
    python main.py --pros Leviathan --matches 10   # show 10 match lineups
    python main.py --pros All --raw                # rank heroes by raw game count
    python main.py --pros Leviathan --solo         # all their customs, no grouping
    python main.py --pros NA --days 14
    python main.py --pros Leviathan --days 30
    python main.py 880934744 --with-customs        # ranked + unranked + customs
    python main.py 880934744 --mode ranked         # ranked only
    python main.py 880934744 --mode private_lobby  # custom games only
    python main.py 880934744 --game-mode street_brawl
    python main.py 880934744 --history             # cross-check via match history
    python main.py --pros NA --refresh             # ignore the cache, pull live
    python main.py --clear-cache                   # empty cache/ and exit
    python main.py --export-cache                  # save permanent data to a bundle
    python main.py --export-cache all.gz --all     # save everything
    python main.py --import-cache deadlock_cache.json.gz

Responses are cached in cache/ and reused: assets for 7 days, finished match
metadata for a year, steam names for a day, ranks and hero stats for 6 hours.

Defaults: game_mode=normal, match_mode=ranked,unranked
(customs, bot games and tutorials are excluded)
"""

import csv
import os
import sys
import time

from api import cache_info, clear_cache, export_cache, import_cache

from deadlock import (
    CUSTOMS_ONLY, DEFAULT_DAYS, DEFAULT_GAME_MODE, DEFAULT_MATCH_MODE,
    WITH_CUSTOMS, build_report, build_team_report, composition_counts,
    ability_order, ability_rows, build_summary, buy_order,
    buy_order_by_player, custom_match_ids, flatten,
    match_build_order, match_builds, metadata_report,
    top_heroes_for, typical_builds,
    flow_edges, flow_rows, get_rank, hero_names, hero_totals, item_flow,
    parse_ids, read_id_file,
)
from teams import LEAGUE, PINNED, TEAMS, choices, roster, search


# --------------------------------------------------------------- output

def print_report(players, days, match_mode=DEFAULT_MATCH_MODE,
                 game_mode=DEFAULT_GAME_MODE):
    print(f"\nfilter: game_mode={game_mode}  match_mode={match_mode}  "
          f"last {days} days")
    for p in players:
        print(f"\n{'=' * 58}")
        who = p.get("ign") or p.get("persona_name") or "(name unavailable)"
        team = f"  ({p['team']})" if p.get("team") else ""
        print(f"  {who}{team}   [{p['account_id']}]   {p['rank_label']}")
        print(f"  {p['total_matches']} matches in the last {days} days")
        print(f"{'=' * 58}")

        if not p["heroes"]:
            print("  no matches in this window")
            continue

        print(f"  {'hero':<16}{'played':>8}{'wins':>7}{'win rate':>11}")
        print(f"  {'-' * 42}")
        for h in p["heroes"]:
            print(f"  {h['hero']:<16}{h['matches']:>8}{h['wins']:>7}"
                  f"{h['win_rate']:>10.1f}%")


def write_csv(players, path):
    rows = flatten(players)
    if not rows:
        print("nothing to write")
        return

    # A file with no extension is one Windows can't open by double-clicking,
    # so make sure it ends in .csv.
    if not os.path.splitext(path)[1]:
        path += ".csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nwrote {len(rows)} rows to {path}")


def run(ids, days=DEFAULT_DAYS, top=5, use_history=False, csv_path=None,
        match_mode=DEFAULT_MATCH_MODE, game_mode=DEFAULT_GAME_MODE,
        labels=None, show_totals=False):
    players = build_report(ids, days=days, top=top, use_history=use_history,
                           match_mode=match_mode, game_mode=game_mode,
                           labels=labels)
    print_report(players, days, match_mode, game_mode)
    if show_totals:
        print_hero_totals(players, normalize="--raw" not in sys.argv)
    if csv_path:
        write_csv(players, csv_path)
    return players


def print_compositions(comps, limit=None):
    """Both lineups for each match, roster side first."""
    if not comps:
        return
    shown = comps[:limit] if limit else comps
    print(f"\n{'=' * 62}")
    print(f"  MATCH COMPOSITIONS  ({len(shown)} of {len(comps)} matches)")
    print(f"{'=' * 62}")

    for m in shown:
        when = (time.strftime("%b %d", time.localtime(m["start_time"]))
                if m.get("start_time") else "")
        mins = f"{m['duration_s'] // 60}m" if m.get("duration_s") else ""
        head = f"  match {m['match_id']}"
        if when or mins:
            head += f"   {when} {mins}".rstrip()
        print(f"\n{head}")

        order = [m["our_side"]] if m["our_side"] is not None else []
        order += [s for s in sorted(m["sides"]) if s not in order]

        for side in order:
            group = m["sides"][side]
            won = "" if m["winner"] is None else (
                "  WIN" if side == m["winner"] else "  LOSS")
            label = m.get("side_names", {}).get(side) or f"side {side}"
            tag = "  <-" if side == m["our_side"] else ""
            print(f"    {label}{won}{tag}")
            for p in group:
                mark = "*" if p["is_roster"] else " "
                print(f"      {mark} {p['name'][:20]:<20} {p['hero']}")


def print_composition_counts(comps, roster_ids):
    rows = composition_counts(comps, roster_ids)
    if not rows:
        return
    print(f"\n  HEROES THEY BUILD AROUND")
    print(f"  {'hero':<16}{'games':>7}{'wins':>6}{'win rate':>11}")
    print(f"  {'-' * 40}")
    for r in rows:
        print(f"  {r['hero']:<16}{r['games']:>7}{r['wins']:>6}"
              f"{r['win_rate']:>10.1f}%")


def run_team(ids, days, top, min_players, labels, csv_path=None,
             include_subs=False, show_matches=0):
    """Only matches where the roster played together."""
    players, meta = build_team_report(ids, days=days, top=top,
                                      min_players=min_players, labels=labels,
                                      include_subs=include_subs)

    print(f"\n{'=' * 58}")
    print(f"  TEAM GAMES ONLY — at least {min_players} roster members per match")
    print(f"  {meta['shared_matches']} qualifying matches in the last {days} days")
    if meta["stack_sizes"]:
        sizes = "  ".join(f"{n} players: {c} matches"
                          for n, c in meta["stack_sizes"].items())
        print(f"  stack sizes across all their customs -> {sizes}")
    if meta.get("subs"):
        print(f"  {len(meta['subs'])} stand-in(s) found across "
              f"{meta['matches_inspected']} inspected matches")
    print(f"{'=' * 58}")

    last_team = None
    for p in players:
        base = p["team"].replace(" (sub)", "")
        if base != last_team:
            print(f"\n  --- {base or 'ungrouped'} ---")
            last_team = base

        who = p.get("ign") or p.get("persona_name") or str(p["account_id"])
        if p.get("is_sub"):
            home = f" of {p['home_team']}" if p.get("home_team") else ""
            tag = f"  *SUB for {p['sub_for']}{home}*"
        else:
            tag = ""
        print(f"\n  {who}{tag}   [{p['account_id']}]   {p['rank_label']}")
        print(f"  {p['team_matches']} team games of {p['custom_matches']} customs")
        if not p["heroes"]:
            print("    no qualifying matches")
            continue
        print(f"    {'hero':<16}{'played':>8}{'wins':>7}{'win rate':>11}")
        print(f"    {'-' * 42}")
        for h in p["heroes"]:
            print(f"    {h['hero']:<16}{h['matches']:>8}{h['wins']:>7}"
                  f"{h['win_rate']:>10.1f}%")

    print_hero_totals(players, normalize="--raw" not in sys.argv)
    if csv_path:
        write_csv(players, csv_path)
    return players


def print_hero_totals(players, normalize=True, min_games=2):
    """
    Hero usage across everyone in the report.

    pick share = average of each player's own usage rate, so a player with
    far more games than the rest does not swing the aggregate.
    """
    totals = hero_totals(players, normalize=normalize, min_games=min_games)
    if not totals:
        return
    print(f"\n{'=' * 70}")
    print("  HERO TOTALS" + ("  (normalized — one vote per player)"
                             if normalize else "  (raw totals)"))
    print(f"{'=' * 70}")
    print(f"  {'hero':<16}{'pick share':>12}{'avg WR':>9}"
          f"{'players':>9}{'played':>8}{'pooled WR':>11}")
    print(f"  {'-' * 66}")
    for t in totals:
        avg = f"{t['avg_win_rate']:.1f}%" if t["avg_win_rate"] is not None else "-"
        print(f"  {t['hero']:<16}{t['pick_share']:>11.1f}%{avg:>9}"
              f"{t['players']:>9}{t['matches']:>8}{t['win_rate']:>10.1f}%")




# --------------------------------------------------------------- menu

def ask(prompt, default=""):
    """input() that survives Ctrl-C / Ctrl-Z without a traceback."""
    try:
        return input(prompt).strip() or default
    except (EOFError, KeyboardInterrupt):
        print()
        return None


def clean_path(text):
    """
    Make a pasted path usable. Handles the two things people always do:
    wrapping it in quotes (needed in PowerShell, wrong at an input() prompt)
    and using ~ for the home folder.
    """
    path = text.strip().strip('"').strip("'")
    path = os.path.expanduser(os.path.expandvars(path))
    return path


def find_file(text):
    """
    Resolve a path the user typed. Returns the path, or None with an
    explanation of everywhere it looked.
    """
    path = clean_path(text)
    tried = [path]

    if os.path.exists(path):
        return path

    # maybe they meant a file sitting next to the scripts
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        os.path.basename(path))
    tried.append(here)
    if os.path.exists(here):
        return here

    # Windows hides known extensions, so "Ids.txt" is often really "Ids.txt.txt"
    for candidate in (path + ".txt", path.removesuffix(".txt")):
        tried.append(candidate)
        if os.path.exists(candidate):
            return candidate

    print("  file not found. looked for:")
    for t in dict.fromkeys(tried):
        print(f"    {t}")
    folder = os.path.dirname(path) or "."
    if os.path.isdir(folder):
        nearby = [f for f in os.listdir(folder) if f.lower().endswith((".txt", ".csv"))]
        if nearby:
            print(f"  text files actually in {folder}:")
            for f in nearby[:10]:
                print(f"    {f}")
    else:
        print(f"  (the folder {folder} does not exist either)")
    return None


def pros_menu(days, top):
    """Preset rosters. Custom games only, since that is where pros scrim."""
    picks = choices()

    print("\n  --- Pros: pick a roster ---")
    for i, name in enumerate(picks, 1):
        if name.startswith("All"):
            n = sum(len(TEAMS[t]["players"]) for t in PINNED)
            print(f"    {i:>2}. {name} ({n} players)")
        elif name in ("NA", "EU"):
            n = sum(len(t["players"]) for t in TEAMS.values()
                    if t.get("region") == name)
            print(f"    {i:>2}. {name} ({n} players)")
        else:
            t = TEAMS[name]
            tag = t.get("region") or t.get("division") or "-"
            print(f"    {i:>2}. {name} [{tag}] ({len(t['players'])} players)")

    if LEAGUE:
        print(f"     S. search the {len(LEAGUE)} league teams from rosters.json")

    pick = ask("  number, or S to search (blank = cancel): ")

    if pick.strip().lower() == "s" and LEAGUE:
        hits = search(ask("  team name: "))
        if not hits:
            print("  no team matched")
            return
        for i, name in enumerate(hits, 1):
            t = TEAMS[name]
            tag = t.get("division") or t.get("region") or "-"
            print(f"    {i:>2}. {name} [{tag}] ({len(t['players'])} players)")
        pick = ask("  number (blank = cancel): ")
        if not pick or not pick.isdigit() or not 1 <= int(pick) <= len(hits):
            return
        selection = hits[int(pick) - 1]
    else:
        if not pick or not pick.isdigit() or not 1 <= int(pick) <= len(picks):
            return
        selection = picks[int(pick) - 1]
    ids, labels = roster(selection)

    if ask("  narrow to specific players? (y/N): ",
           "n").lower().startswith("y"):
        ids, labels = pick_players(ids, labels)

    d = ask(f"  days to look back (blank = {days}): ", str(days))
    days = int(d) if d and d.isdigit() else days
    t = ask(f"  heroes per player (blank = {top}): ", str(top))
    top = int(t) if t and t.isdigit() else top

    tg = ask("  only games where they played TOGETHER? (Y/n): ", "y")
    together = not tg.lower().startswith("n")

    min_players, include_subs, show_matches = 4, False, 0
    if together:
        v = ask("  minimum roster members per match (blank = 4): ", "4")
        min_players = int(v) if v and v.isdigit() else 4
        sb = ask("  include stand-ins / subs? (y/N): ", "n")
        include_subs = sb.lower().startswith("y")
        mv = ask("  show match compositions? how many (blank = none): ")
        show_matches = int(mv) if mv and mv.isdigit() else 0

    safe = selection.replace(" ", "_").lower()
    suffix = f"_together{min_players}" if together else ""
    path = f"pros_{safe}_{days}d{suffix}.csv"

    print(f"\n  {selection}: {len(ids)} players, custom games, last {days} days")
    if together:
        run_team(ids, days, top, min_players, labels, csv_path=path,
                 include_subs=include_subs, show_matches=show_matches)
    else:
        run(ids, days=days, top=top, match_mode=CUSTOMS_ONLY,
            labels=labels, csv_path=path, show_totals=True)

    if ask("\n  show build order too? (y/N): ", "n").lower().startswith("y"):
        item_order_menu(ids, labels, days, CUSTOMS_ONLY)

    if ask("  show builds from the real matches? (y/N): ",
           "n").lower().startswith("y"):
        match_builds_menu(ids, labels, days, CUSTOMS_ONLY)


def print_buy_order(rows, title="BUY ORDER", limit=40):
    """Items in the order they get bought, with the clock time."""
    if not rows:
        print("  nothing came back — widen the window, lower min matches, "
              "or loosen the match mode")
        return
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print(f"{'=' * 70}")
    split = any(r.get("hero") for r in rows)
    print(f"  {'bought':>7}  {'item':<30}{'buys':>6}{'WR':>9}")
    print(f"  {'-' * 60}")
    hero = object()
    for r in rows[:limit]:
        if split and r.get("hero") != hero:
            hero = r.get("hero")
            print(f"\n  {hero or '(unknown hero)'}")
        wr = f"{r['win_rate']:.1f}%" if r["win_rate"] is not None else "-"
        print(f"  {r['buy_time']:>7}  {r['item']:<30}{r['buys']:>6}{wr:>9}")


def print_flow(rows, limit=40):
    """The same purchases grouped into phases."""
    if not rows:
        print("  no phase data at these filters")
        return
    print(f"\n{'=' * 70}")
    print("  BY PHASE  (adj. WR removes the richer-buyers-win confound)")
    print(f"{'=' * 70}")
    window = object()
    for r in rows[:limit]:
        if r["window"] != window:
            window = r["window"]
            print(f"\n  {window}")
            print(f"  {'-' * 60}")
        wr = f"{r['win_rate']:.1f}%" if r["win_rate"] is not None else "-"
        adj = f"{r['adj_win_rate']:.1f}%" if r["adj_win_rate"] is not None else "-"
        pick = f"{r['pick_rate']:.1f}%" if r["pick_rate"] is not None else "-"
        print(f"    {r['item']:<28}{r['buys']:>6}{pick:>9}{wr:>9}{adj:>9}")


def print_transitions(edges, limit=25):
    if not edges:
        return
    print(f"\n{'=' * 70}")
    print("  WHAT FOLLOWS WHAT")
    print(f"{'=' * 70}")
    for e in edges[:limit]:
        wr = f"{e['win_rate']:.1f}%" if e["win_rate"] is not None else "-"
        print(f"  {e['from']:<26} -> {e['to']:<26}{e['matches']:>6}{wr:>9}")


def print_ability_order(rows, hero_label, limit=15):
    if not rows:
        print(f"  no ability orders for {hero_label} at these filters")
        return
    print(f"\n{'=' * 70}")
    print(f"  ABILITY POINT ORDER — {hero_label}")
    print(f"{'=' * 70}")
    for r in rows[:limit]:
        wr = f"{r['win_rate']:.1f}%" if r["win_rate"] is not None else "-"
        print(f"  {r['matches']:>5} games{wr:>9}   {r['order']}")


def print_match_builds(rows, kind="item", limit=40):
    """Per-player summary of what they actually bought in real games."""
    summary = build_summary(rows, kind=kind)
    if not summary:
        print(f"  no {kind} purchases found in those matches")
        return
    games = len({r["match_id"] for r in rows})
    what = "items" if kind == "item" else "ability points"
    print(f"\n{'=' * 70}")
    print(f"  FROM MATCHES — {what} across {games} games")
    print(f"{'=' * 70}")
    player = object()
    for s in summary[:limit]:
        if s["player"] != player:
            player = s["player"]
            print(f"\n  {player}")
            print(f"  {'-' * 56}")
        wr = f"{s['win_rate']:.1f}%" if s["win_rate"] is not None else "-"
        print(f"    {s['buy_time']:>6}  {s['item']:<28}"
              f"{s['buys']:>5}{wr:>9}")


def print_one_match(rows, match_id, kind=None):
    """One game, every roster player in it, purchases in order."""
    people = sorted({(r["account_id"], r["player"]) for r in rows
                     if r["match_id"] == match_id})
    if not people:
        print(f"  nothing recorded for match {match_id}")
        return
    print(f"\n{'=' * 70}")
    print(f"  MATCH {match_id}")
    print(f"{'=' * 70}")
    for account_id, player in people:
        seq = match_build_order(rows, match_id, account_id, kind=kind)
        if not seq:
            continue
        hero = seq[0].get("hero") or "?"
        won = seq[0].get("won")
        tag = "" if won is None else ("  won" if won else "  lost")
        print(f"\n  {player} — {hero}{tag}")
        print(f"  {'-' * 56}")
        for r in seq:
            print(f"    {r['buy_time']:>6}  {r['kind']:<8}{r['item']}")


def match_builds_menu(ids, labels, days, match_mode):
    """Builds read out of match metadata rather than the analytics endpoints."""
    if not ids:
        print("  add some players first")
        return
    try:
        by_player = custom_match_ids(ids, days=days, match_mode=match_mode)
    except Exception as e:
        print(f"  could not list matches: {e}")
        return

    every = sorted({m for v in by_player.values() for m in v}, reverse=True)
    if not every:
        print("  no matches for these players in this window")
        return

    n = ask(f"  {len(every)} matches in window. read how many "
            f"(blank = 20): ", "20")
    limit = int(n) if n.isdigit() else 20
    print(f"  reading {min(limit, len(every))} matches "
          "(cached ones are instant) ...")
    try:
        rows = match_builds(every, account_ids=ids, labels=labels,
                            limit=limit)
    except Exception as e:
        print(f"  request failed: {e}")
        return

    if not rows:
        print("  no purchases found. what one blob actually contains:")
        try:
            for k, v in metadata_report(every[0]).items():
                print(f"    {k}: {v}")
        except Exception as e:
            print(f"    (diagnostic failed: {e})")
        return

    print_match_builds(rows, kind="item")
    if ask("\n  show ability points too? (y/N): ", "n").lower().startswith("y"):
        print_match_builds(rows, kind="ability")

    played = sorted({r["match_id"] for r in rows}, reverse=True)
    print(f"\n  matches read: {', '.join(str(m) for m in played[:12])}"
          + (" ..." if len(played) > 12 else ""))
    want = ask("  show one match in full? (match id, blank = no): ")
    if want and want.isdigit() and int(want) in played:
        print_one_match(rows, int(want))


def pick_players(ids, labels):
    """
    Narrow a roster to specific players.
    -> (ids, labels), unchanged when the answer is blank.
    """
    if len(ids) < 2:
        return ids, labels
    print("\n  players:")
    for i, account_id in enumerate(ids, 1):
        info = labels.get(account_id, {})
        name = info.get("ign") or str(account_id)
        team = info.get("team") or ""
        print(f"    {i:>2}. {name}" + (f"  [{team}]" if team else ""))

    want = ask("  numbers to keep, comma separated (blank = all): ")
    if not want:
        return ids, labels
    keep = []
    for part in want.replace(",", " ").split():
        if part.isdigit() and 1 <= int(part) <= len(ids):
            keep.append(ids[int(part) - 1])
    if not keep:
        print("  nothing valid picked — keeping everyone")
        return ids, labels
    keep = list(dict.fromkeys(keep))
    print(f"  keeping {len(keep)}: "
          + ", ".join(labels.get(a, {}).get("ign") or str(a) for a in keep))
    return keep, {a: labels[a] for a in keep if a in labels}


def standard_build_menu(ids, labels, days, match_mode):
    """One player's normal build on one hero."""
    if not ids:
        print("  add some players first")
        return
    picked, picked_labels = pick_players(ids, labels)
    focus = picked[0]
    if len(picked) > 1:
        print("  (using the first of those)")
    who = picked_labels.get(focus, labels.get(focus, {})).get("ign") or str(focus)

    try:
        played = top_heroes_for(focus, days=days, match_mode=match_mode)
    except Exception as e:
        print(f"  could not list their heroes: {e}")
        return
    if not played:
        print(f"  no heroes for {who} in this window")
        return

    print(f"\n  {who} played:")
    for i, h in enumerate(played, 1):
        wr = f"{h['win_rate']:.0f}%" if h["win_rate"] is not None else "-"
        print(f"    {i:>2}. {h['hero']:<16}{h['matches']:>4} games{wr:>8}")
    pick = ask("  number (blank = most played): ", "1")
    if not pick.isdigit() or not 1 <= int(pick) <= len(played):
        pick = "1"
    hero = played[int(pick) - 1]

    share = ask("  core threshold %% (blank = 50): ", "50")
    core_share = float(share) if share.replace(".", "", 1).isdigit() else 50.0

    try:
        builds = typical_builds([focus], hero["hero_id"], labels=labels,
                                days=days, match_mode=match_mode,
                                core_share=core_share)
    except Exception as e:
        print(f"  request failed: {e}")
        return
    if not builds:
        print("  nothing came back")
        return

    build = builds[0]
    print(f"\n{'=' * 70}")
    print(f"  STANDARD BUILD — {who} on {hero['hero']} "
          f"({hero['matches']} games)")
    print(f"{'=' * 70}")
    print(f"\n  core (in {core_share:.0f}%+ of games)")
    print(f"  {'-' * 56}")
    if build["core"]:
        for r in build["core"]:
            wr = f"{r['win_rate']:.0f}%" if r["win_rate"] is not None else "-"
            print(f"    {r['buy_time']:>6}  {r['item']:<28}"
                  f"{r['share']:>6.0f}%{wr:>8}")
    else:
        print("    nothing clears the threshold — try a lower one")

    if build["situational"]:
        print("\n  situational")
        print(f"  {'-' * 56}")
        for r in build["situational"][:12]:
            wr = f"{r['win_rate']:.0f}%" if r["win_rate"] is not None else "-"
            print(f"    {r['buy_time']:>6}  {r['item']:<28}"
                  f"{r['share']:>6.0f}%{wr:>8}")
    print(f"\n  share is out of {build['games']} — the most-bought item's "
          "count, since\n  item-stats does not report games played directly")


def pick_hero():
    """-> (hero_id, label). Blank means every hero pooled."""
    want = ask("  one hero only? (name, blank = all): ")
    if not want:
        return None, "all heroes"
    try:
        names = hero_names()
    except Exception as e:
        print(f"  (could not load hero names: {e})")
        return None, "all heroes"
    hits = [h for h, n in names.items() if n and want.lower() in n.lower()]
    if len(hits) == 1:
        return hits[0], names[hits[0]]
    if len(hits) > 1:
        print(f"  {len(hits)} heroes match: "
              f"{', '.join(names[h] for h in hits[:8])}")
        return None, "all heroes"
    print("  no hero by that name — using all heroes")
    return None, "all heroes"


def item_order_menu(ids, labels, days, match_mode):
    """Buy order, phases, transitions, then optionally per player."""
    if not ids:
        print("  add some players first")
        return

    hero_id, hero_label = pick_hero()
    mm = ask("  min matches per item (blank = 1): ", "1")
    min_matches = int(mm) if mm.isdigit() else 1

    split = ask("  split by hero? (y/N): ", "n").lower().startswith("y")

    print(f"  pulling build data for {len(ids)} player(s), {hero_label}, "
          f"last {days} days ...")
    try:
        rows = buy_order(ids, hero_id=hero_id, days=days,
                         match_mode=match_mode, min_matches=min_matches,
                         bucket="hero" if split else None)
    except Exception as e:
        print(f"  request failed: {e}")
        return
    print_buy_order(rows, f"BUY ORDER — {hero_label}, last {days} days")

    if ask("\n  show phases and transitions too? (y/N): ",
           "n").lower().startswith("y"):
        try:
            raw = item_flow(ids, hero_id=hero_id, days=days,
                            match_mode=match_mode, min_matches=min_matches)
            print_flow(flow_rows(raw))
            print_transitions(flow_edges(raw))
        except Exception as e:
            print(f"  flow request failed: {e}")

    if hero_id is not None and ask("  show ability point order? (y/N): ",
                                   "n").lower().startswith("y"):
        pick = ask("  show abilities as 1=numbers 2=names 3=both "
                   "(blank = numbers): ", "1")
        style = {"1": "Numbers", "2": "Names", "3": "Both"}.get(pick.strip(),
                                                                "Numbers")
        try:
            print_ability_order(
                ability_rows(ability_order(hero_id, ids, days=days,
                                           match_mode=match_mode,
                                           min_matches=min_matches),
                             hero_id=hero_id, style=style),
                f"{hero_label} ({style.lower()})")
        except Exception as e:
            print(f"  ability request failed: {e}")

    if ask("\n  show one player's standard build on a hero? (y/N): ",
           "n").lower().startswith("y"):
        standard_build_menu(ids, labels, days, match_mode)

    if not ask("\n  break the buy order down per player? (y/N): ",
               "n").lower().startswith("y"):
        return
    print(f"  one request per player, {len(ids)} to go ...")
    try:
        prows = buy_order_by_player(ids, labels, hero_id=hero_id, days=days,
                                    match_mode=match_mode,
                                    min_matches=min_matches)
    except Exception as e:
        print(f"  request failed: {e}")
        return
    if not prows:
        print("  no per-player rows — usually too thin a sample once split")
        return
    for who in dict.fromkeys(r["player"] for r in prows):
        print_buy_order([r for r in prows if r["player"] == who],
                        f"{who} — {hero_label}")


MATCH_MODES = [
    "ranked,unranked",                    # the API default
    "ranked,unranked,private_lobby",      # + custom games
    "private_lobby",                      # customs only
    "ranked",
    "unranked",
    "coop_bot",
    "tutorial",
    "hero_labs",
    "server_test",
]


def menu():
    ids, days, top = [], DEFAULT_DAYS, 5
    match_mode, game_mode = DEFAULT_MATCH_MODE, DEFAULT_GAME_MODE

    while True:
        print(f"""
============== DEADLOCK PLAYER REPORT ==============
  P. Pros  (preset team rosters, custom games)
  C. Cache (status / clear)
  H. Import a roster from a saved team page (.html)
  B. Bake in every league team (harvester bookmarklet)
  I. Build order (items, phases, ability points)
  M. Builds from real matches (uses cached match data)
  S. Standard build for one player on one hero
  1. Add players (ids or statlocker URLs)
  2. Load ids from a file
  3. Set time window        (now: last {days} days)
  4. Set heroes shown       (now: top {top})
  5. Set match mode         (now: {match_mode})
  6. Set game mode          (now: {game_mode})
  7. Run report
  8. Run report + save CSV
  9. Look up one rank
 10. Clear player list
  0. Quit

  players: {', '.join(map(str, ids)) or '(none yet)'}""")

        choice = ask("\n  choose: ")
        if choice is None or choice == "0" or choice.lower() in ("q", "quit", "exit"):
            print("  bye")
            return

        if choice.lower() == "p":
            pros_menu(days, top)

        elif choice.lower() == "i":
            item_order_menu(ids, {}, days, match_mode)

        elif choice.lower() == "m":
            match_builds_menu(ids, {}, days, match_mode)

        elif choice.lower() == "s":
            standard_build_menu(ids, {}, days, match_mode)

        elif choice.lower() == "b":
            from roster_import import HARVESTER
            print("""
  Baking in every league team, once:

    1. Make a browser bookmark. Put the line below in as its URL.
    2. Open https://players.dse.gg/teams/ and click that bookmark.
       It walks every team page in your own logged-in session and
       downloads dse_rosters.html. Give it a minute.
    3. In this project folder, run:

           python build_rosters.py dse_rosters.html

    4. Commit the rosters.json it writes. Every teammate and the
       deployed app then has all the rosters with nothing to import.
""")
            print(HARVESTER)
            print("\n  (a folder of saved team pages works too: "
                  "python build_rosters.py pages/)")

        elif choice.lower() == "h":
            from roster_import import as_teams_entry, parse_any, read_html
            raw = ask("  path to the saved team page (.html): ")
            found_path = find_file(raw) if raw else None
            if found_path:
                try:
                    result = parse_any(read_html(found_path))
                except OSError as e:
                    result, _ = {"kind": "generic", "players": [], "teams": []}, print(f"  {e}")

                if result["kind"] == "directory":
                    print(f"  team directory — {len(result['teams'])} teams:")
                    for t in result["teams"]:
                        print(f"    {t['team_id']:<6} {t['team']:<34} "
                              f"{t.get('division', '')}")
                    print("  no account IDs on this page — open one of these "
                          "team pages in your browser and paste/save that page")
                elif not result["players"]:
                    print("  no players found — if the page needs JavaScript, copy "
                          "it from devtools (right-click <html> -> Copy element)")
                else:
                    print(f"  {result['team'] or 'roster'}: "
                          f"{len(result['players'])} players")
                    for p_ in result["players"]:
                        print(f"    {p_['role'] or '-':<6} {p_['ign']:<22} "
                              f"{p_['account_id']:<12} {p_['persona']}")
                    ids = list(dict.fromkeys(
                        ids + [p_["account_id"] for p_ in result["players"]]))
                    print(f"  added to the player list ({len(ids)} total)")
                    if ask("  print a teams.py block? (y/N): ", "n").lower().startswith("y"):
                        region = ask("  region (NA/EU): ", "NA").upper()
                        print()
                        print(as_teams_entry(result["team"] or "New Team",
                                             region, result["players"]))

        elif choice.lower() == "c":
            info = cache_info()
            print(f"\n  {info['entries']} cached responses, "
                  f"{info['megabytes']} MB, oldest {info['oldest_hours']}h old")
            print("  fresh for: assets 7d · match metadata 1y · "
                  "steam names 1d · everything else 6h")
            what = ask("  (e)xport  (i)mport  (a)ll-clear  (s)tale-clear  "
                       "blank=keep: ")
            w = (what or "").lower()[:1]
            if w == "a":
                print(f"  removed {clear_cache()} entries")
            elif w == "s":
                print(f"  removed {clear_cache(older_than_hours=6)} entries")
            elif w == "e":
                only = not (ask("  everything, or permanent only? (P/e): ", "p")
                            ).lower().startswith("e")
                blob, m = export_cache(only_permanent=only)
                with open("deadlock_cache.json.gz", "wb") as f:
                    f.write(blob)
                print(f"  wrote deadlock_cache.json.gz "
                      f"({m['entries']} entries, {m['megabytes']} MB)")
            elif w == "i":
                path = ask("  bundle path (blank = deadlock_cache.json.gz): ",
                           "deadlock_cache.json.gz")
                found = find_file(path)
                if found:
                    try:
                        r = import_cache(found)
                        print(f"  restored {r['added']} "
                              f"({r['skipped_existing']} present, "
                              f"{r['skipped_stale']} too old)")
                    except Exception as e:
                        print(f"  not a valid bundle: {e}")

        elif choice == "1":
            raw = ask("  paste ids or URLs (space or comma separated): ")
            if raw:
                found = parse_ids(raw.replace(",", " ").split())
                ids = list(dict.fromkeys(ids + found))
                print(f"  added {len(found)}; list is now {len(ids)}")

        elif choice == "2":
            raw = ask("  file path (no quotes needed): ")
            path = find_file(raw) if raw else None
            if path:
                ids = list(dict.fromkeys(ids + read_id_file(path)))
                print(f"  read {path}; list is now {len(ids)}")

        elif choice == "3":
            v = ask("  days to look back (default 30): ", "30")
            days = int(v) if v and v.isdigit() else 30

        elif choice == "4":
            v = ask("  how many heroes per player (default 5): ", "5")
            top = int(v) if v and v.isdigit() else 5

        elif choice == "5":
            for i, m in enumerate(MATCH_MODES, 1):
                print(f"    {i}. {m}")
            pick = ask("  number: ")
            if pick and pick.isdigit() and 1 <= int(pick) <= len(MATCH_MODES):
                match_mode = MATCH_MODES[int(pick) - 1]
                print(f"  match mode is now {match_mode}")

        elif choice == "6":
            game_mode = "street_brawl" if game_mode == "normal" else "normal"
            print(f"  game mode is now {game_mode}")

        elif choice in ("7", "8"):
            if not ids:
                print("  add some players first")
            else:
                path = ask("  csv filename (default players.csv): ", "players.csv") \
                    if choice == "8" else None
                run(ids, days=days, top=top, csv_path=path,
                    match_mode=match_mode, game_mode=game_mode)

        elif choice == "9":
            one = ask("  id or URL: ")
            found = parse_ids([one]) if one else []
            if found:
                info = get_rank(found[0])
                print(f"  {found[0]}: {info['rank_label']}   "
                      f"(rank {info['rank']}, subrank {info['subrank']}, "
                      f"badge {info['badge']})")

        elif choice == "10":
            ids = []
            print("  cleared")

        else:
            print("  pick a number from the menu.")

        if ask("\n  [enter] to continue ") is None:
            return


# --------------------------------------------------------------- entry point

def parse_args(args):
    """Split command-line arguments into ids and options."""
    def flag(name, default=None, cast=str):
        return cast(args[args.index(name) + 1]) if name in args else default

    days = flag("--days", DEFAULT_DAYS, int)
    top = flag("--top", 5, int)
    csv_path = flag("--csv")
    match_mode = flag("--mode", DEFAULT_MATCH_MODE)
    if "--with-customs" in args:
        match_mode = WITH_CUSTOMS
    game_mode = flag("--game-mode", DEFAULT_GAME_MODE)

    ids = []
    if "--file" in args:
        found = find_file(flag("--file"))
        if found:
            ids = read_id_file(found)

    # skip the VALUE that follows each flag, so it isn't parsed as an id
    skip = {args.index(f) + 1 for f in
            ("--days", "--top", "--csv", "--file", "--mode", "--game-mode")
            if f in args}
    ids += parse_ids([a for i, a in enumerate(args)
                      if not a.startswith("--") and i not in skip])

    return list(dict.fromkeys(ids)), days, top, csv_path, match_mode, game_mode


if __name__ == "__main__":
    args = sys.argv[1:]

    if "--export-cache" in args:
        i = args.index("--export-cache")
        out = (args[i + 1] if len(args) > i + 1 and not args[i + 1].startswith("--")
               else "deadlock_cache.json.gz")
        blob, m = export_cache(only_permanent="--all" not in args)
        with open(out, "wb") as f:
            f.write(blob)
        print(f"wrote {out} ({m['entries']} entries, {m['megabytes']} MB)")
        sys.exit()

    if "--import-cache" in args:
        path = args[args.index("--import-cache") + 1]
        r = import_cache(path)
        print(f"restored {r['added']} entries "
              f"({r['skipped_existing']} already present, "
              f"{r['skipped_stale']} too old)")
        sys.exit()

    if "--clear-cache" in args:
        print(f"removed {clear_cache()} cached responses")
        sys.exit()

    if "--refresh" in args:
        # skip the cache for this run by expiring everything first
        print(f"cleared {clear_cache()} cached responses (forcing live data)")
        args = [a for a in args if a != "--refresh"]

    if not args:
        menu()
        sys.exit()

    # --pros "Leviathan" | --pros NA | --pros All
    if "--pros" in args:
        selection = args[args.index("--pros") + 1]
        ids, labels = roster(selection)
        days = int(args[args.index("--days") + 1]) if "--days" in args else DEFAULT_DAYS
        top = int(args[args.index("--top") + 1]) if "--top" in args else 5
        safe = selection.replace(" ", "_").lower()

        if "--solo" in args:
            run(ids, days=days, top=top, match_mode=CUSTOMS_ONLY, labels=labels,
                csv_path=f"pros_{safe}_{days}d.csv", show_totals=True)
        else:
            n = int(args[args.index("--together") + 1]) if "--together" in args else 4
            show = (int(args[args.index("--matches") + 1])
                    if "--matches" in args else 0)
            run_team(ids, days, top, n, labels,
                     csv_path=f"pros_{safe}_{days}d_together{n}.csv",
                     include_subs="--subs" in args, show_matches=show)
        sys.exit()

    ids, days, top, csv_path, match_mode, game_mode = parse_args(args)
    if not ids:
        print(__doc__)
        sys.exit(1)

    run(ids, days=days, top=top, use_history="--history" in args,
        csv_path=csv_path, match_mode=match_mode, game_mode=game_mode)