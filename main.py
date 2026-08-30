"""
main.py — the runner. Menu, command-line arguments, and printing.

This is the only file that talks to the user. It calls deadlock.py for
data and formats it. No HTTP and no game logic lives here.

    python main.py                                  # interactive menu
    python main.py 880934744 104579843
    python main.py https://statlocker.gg/profile/880934744/matches?mode=standard
    python main.py --file ids.txt --days 30 --top 5 --csv players.csv
    python main.py 880934744 --with-customs        # ranked + unranked + customs
    python main.py 880934744 --mode ranked         # ranked only
    python main.py 880934744 --mode private_lobby  # custom games only
    python main.py 880934744 --game-mode street_brawl
    python main.py 880934744 --history             # cross-check via match history

Defaults: game_mode=normal, match_mode=ranked,unranked
(customs, bot games and tutorials are excluded)
"""

import csv
import os
import sys

from deadlock import (
    DEFAULT_GAME_MODE, DEFAULT_MATCH_MODE, WITH_CUSTOMS,
    build_report, flatten, get_rank, parse_ids, read_id_file, rank_name,
)


# --------------------------------------------------------------- output

def print_report(players, days, match_mode=DEFAULT_MATCH_MODE,
                 game_mode=DEFAULT_GAME_MODE):
    print(f"\nfilter: game_mode={game_mode}  match_mode={match_mode}  "
          f"last {days} days")
    for p in players:
        print(f"\n{'=' * 58}")
        who = p.get("persona_name") or "(name unavailable)"
        print(f"  {who}   [{p['account_id']}]   {p['rank_label']}")
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


def run(ids, days=30, top=5, use_history=False, csv_path=None,
        match_mode=DEFAULT_MATCH_MODE, game_mode=DEFAULT_GAME_MODE):
    players = build_report(ids, days=days, top=top, use_history=use_history,
                           match_mode=match_mode, game_mode=game_mode)
    print_report(players, days, match_mode, game_mode)
    if csv_path:
        write_csv(players, csv_path)
    return players


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
    ids, days, top = [], 30, 5
    match_mode, game_mode = DEFAULT_MATCH_MODE, DEFAULT_GAME_MODE

    while True:
        print(f"""
============== DEADLOCK PLAYER REPORT ==============
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

        if choice == "1":
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

    days = flag("--days", 30, int)
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

    if not args:
        menu()
        sys.exit()

    ids, days, top, csv_path, match_mode, game_mode = parse_args(args)
    if not ids:
        print(__doc__)
        sys.exit(1)

    run(ids, days=days, top=top, use_history="--history" in args,
        csv_path=csv_path, match_mode=match_mode, game_mode=game_mode)