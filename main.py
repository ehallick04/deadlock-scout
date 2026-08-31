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
    flatten, get_rank, hero_totals, match_compositions, parse_ids,
    read_id_file, rank_name,
)
from teams import TEAMS, choices, roster


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
    print(f"\n  pick share = mean share of a player's games on that hero")
    print(f"  avg WR     = mean of individual win rates "
          f"(players with {min_games}+ games)")
    print(f"  pooled WR  = total wins / total games, volume-weighted")


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
        if name == "All":
            n = sum(len(t["players"]) for t in TEAMS.values())
            print(f"    {i:>2}. All teams ({n} players)")
        elif name in ("NA", "EU"):
            n = sum(len(t["players"]) for t in TEAMS.values()
                    if t["region"] == name)
            print(f"    {i:>2}. {name} ({n} players)")
        else:
            t = TEAMS[name]
            print(f"    {i:>2}. {name} [{t['region']}] ({len(t['players'])} players)")

    pick = ask("  number (blank = cancel): ")
    if not pick or not pick.isdigit() or not 1 <= int(pick) <= len(picks):
        return

    selection = picks[int(pick) - 1]
    ids, labels = roster(selection)

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
                        print(f"    {t['team_id']:<6} {t['team']}")
                    print("  open one of these in your browser and save that page")
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
