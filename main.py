"""
main.py — the runner. Menu, command-line arguments, and printing.

This is the only file that talks to the user. It calls deadlock.py for
data and formats it. No HTTP and no game logic lives here.

    python main.py                                  # interactive menu
    python main.py 880934744 104579843
    python main.py https://statlocker.gg/profile/880934744/matches?mode=standard
    python main.py --file ids.txt --days 30 --top 5 --csv players.csv
    python main.py 880934744 --history             # cross-check via match history
"""

import csv
import os
import sys

from deadlock import (
    build_report, flatten, get_rank, parse_ids, read_id_file, rank_name,
)


# --------------------------------------------------------------- output

def print_report(players, days):
    for p in players:
        print(f"\n{'=' * 58}")
        print(f"  {p['account_id']}   {p['rank_label']}"
              f"   ({p['total_matches']} matches in the last {days} days)")
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
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nwrote {len(rows)} rows to {path}")


def run(ids, days=30, top=5, use_history=False, csv_path=None):
    players = build_report(ids, days=days, top=top, use_history=use_history)
    print_report(players, days)
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


def menu():
    ids, days, top = [], 30, 5

    while True:
        print(f"""
============== DEADLOCK PLAYER REPORT ==============
  1. Add players (ids or statlocker URLs)
  2. Load ids from a file
  3. Set time window        (now: last {days} days)
  4. Set heroes shown       (now: top {top})
  5. Run report
  6. Run report + save CSV
  7. Look up one rank
  8. Clear player list
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
            path = ask("  file path: ")
            if path and os.path.exists(path):
                ids = list(dict.fromkeys(ids + read_id_file(path)))
                print(f"  list is now {len(ids)}")
            else:
                print("  file not found")

        elif choice == "3":
            v = ask("  days to look back (default 30): ", "30")
            days = int(v) if v and v.isdigit() else 30

        elif choice == "4":
            v = ask("  how many heroes per player (default 5): ", "5")
            top = int(v) if v and v.isdigit() else 5

        elif choice in ("5", "6"):
            if not ids:
                print("  add some players first")
            else:
                path = ask("  csv filename (default players.csv): ", "players.csv") \
                    if choice == "6" else None
                run(ids, days=days, top=top, csv_path=path)

        elif choice == "7":
            one = ask("  id or URL: ")
            found = parse_ids([one]) if one else []
            if found:
                info = get_rank(found[0])
                print(f"  {found[0]}: {info['rank_label']}   "
                      f"(rank {info['rank']}, subrank {info['subrank']}, "
                      f"badge {info['badge']})")

        elif choice == "8":
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

    ids = read_id_file(flag("--file")) if "--file" in args else []

    # skip the VALUE that follows each flag, so it isn't parsed as an id
    skip = {args.index(f) + 1 for f in ("--days", "--top", "--csv", "--file")
            if f in args}
    ids += parse_ids([a for i, a in enumerate(args)
                      if not a.startswith("--") and i not in skip])

    return list(dict.fromkeys(ids)), days, top, csv_path


if __name__ == "__main__":
    args = sys.argv[1:]

    if not args:
        menu()
        sys.exit()

    ids, days, top, csv_path = parse_args(args)
    if not ids:
        print(__doc__)
        sys.exit(1)

    run(ids, days=days, top=top,
        use_history="--history" in args, csv_path=csv_path)
