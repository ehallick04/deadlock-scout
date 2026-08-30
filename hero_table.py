"""
hero_table.py — turn heroes.json into one flat table, then sort/filter anything.

Flatten the nested JSON ONCE into rows x columns, and every question after
that is a generic table operation instead of custom code.

    uv pip install pandas

Run with no arguments for an interactive menu:

    python hero_table.py

Or drive it straight from the command line:

    python hero_table.py cols speed          # columns matching a word
    python hero_table.py sort max_health     # sort by any column
    python hero_table.py top max_move_speed 10
    python hero_table.py vary                # which stats actually differ
    python hero_table.py show Bebop          # one hero, every field
    python hero_table.py csv                 # dump to heroes.csv
    python hero_table.py sort id --all       # include disabled / in-dev heroes
"""

import difflib
import json
import os
import sys

import pandas as pd

HEROES_FILE = "heroes.json"


def build(playable_only=True, tidy=True):
    """Load heroes.json and flatten it into a DataFrame."""
    with open(HEROES_FILE, encoding="utf-8") as f:
        heroes = json.load(f)

    if playable_only:
        heroes = [h for h in heroes
                  if h.get("player_selectable") and not h.get("disabled")]

    # json_normalize walks the nested dicts and makes dotted column names:
    #   starting_stats.max_health.value  ->  one column
    df = pd.json_normalize(heroes)

    if tidy:
        # drop the label columns that ride along with every stat
        df = df[[c for c in df.columns if not c.endswith(".display_stat_name")]]
        # 'starting_stats.max_health.value' -> 'max_health'
        df.columns = [
            c.replace("starting_stats.", "").replace(".value", "")
            for c in df.columns
        ]
        # columns that are empty for everyone tell you nothing
        df = df.dropna(axis=1, how="all")

    if "name" in df.columns:
        df = df.set_index("name")
    return df


def numeric(df):
    """Only the columns you can meaningfully sort as numbers."""
    return df.select_dtypes("number")


# --------------------------------------------------------------- actions

def cols(df, needle=None):
    names = [c for c in df.columns if not needle or needle.lower() in c.lower()]
    num = set(numeric(df).columns)
    print(f"{len(names)} columns" + (f" matching '{needle}'" if needle else "") + ":\n")
    for c in sorted(names):
        print(f"  [{'num ' if c in num else 'text'}] {c}")


def sort(df, column, ascending=False, limit=None):
    if column in ("name", df.index.name):
        s = df.index.to_series().sort_values(ascending=ascending)
        print(s.to_string(index=False))
        return

    if column not in df.columns:
        near = [c for c in df.columns if column.lower() in c.lower()]
        near += difflib.get_close_matches(column, df.columns, n=5, cutoff=0.5)
        seen = list(dict.fromkeys(near))[:5]
        print(f"no column '{column}'."
              + (f"\ndid you mean: {', '.join(seen)}" if seen else " run 'cols' to list them."))
        return

    s = df[column].dropna().sort_values(ascending=ascending)
    if limit:
        s = s.head(limit)
    print(s.to_string())
    print(f"\n{len(s)} heroes, {s.nunique()} distinct values")


def vary(df):
    """Rank stats by how much they discriminate between heroes."""
    num = numeric(df)
    report = pd.DataFrame({
        "distinct": num.nunique(),
        "min": num.min(),
        "max": num.max(),
        "spread": num.max() - num.min(),
    })
    report = report[report["distinct"] > 1].sort_values("distinct", ascending=False)
    print("stats worth sorting on (most discriminating first):\n")
    print(report.to_string())

    flat = num.nunique()
    dead = list(flat[flat <= 1].index)
    if dead:
        print(f"\nidentical for every hero ({len(dead)}): {', '.join(dead[:15])}"
              + (" ..." if len(dead) > 15 else ""))


def show(df, hero):
    matches = [h for h in df.index if hero.lower() in str(h).lower()]
    if not matches:
        print(f"no hero matching '{hero}'")
        return
    row = df.loc[matches[0]].dropna()
    print(f"--- {matches[0]} ---")
    print(row.to_string())


def group(df, column):
    counts = df[column].astype(str).value_counts()
    print(counts.to_string())
    print(f"\n{len(counts)} groups")


def to_csv(df, path="heroes.csv"):
    df.to_csv(path, encoding="utf-8")
    print(f"wrote {len(df)} rows x {len(df.columns)} columns to {path}")


# --------------------------------------------------------------- menu

def ask(prompt, default=""):
    """input() that survives Ctrl-C / Ctrl-Z without a traceback."""
    try:
        answer = input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    return answer or default


def pick_column(df, purpose="sort by"):
    """Search columns by keyword, then choose one by number."""
    needle = ask(f"  keyword to find a column to {purpose} (blank = list all): ")
    if needle is None:
        return None

    matches = [c for c in df.columns if needle.lower() in c.lower()]
    if not matches:
        matches = difflib.get_close_matches(needle, df.columns, n=8, cutoff=0.4)
        if not matches:
            print("  nothing matched.")
            return None
        print("  no exact match, closest:")

    num = set(numeric(df).columns)
    for i, c in enumerate(matches[:40], 1):
        print(f"    {i:>3}. [{'num ' if c in num else 'text'}] {c}")
    if len(matches) > 40:
        print(f"    ... and {len(matches) - 40} more, narrow your keyword")

    choice = ask("  number (blank = cancel): ")
    if not choice:
        return None
    try:
        return matches[int(choice) - 1]
    except (ValueError, IndexError):
        print("  not a valid number.")
        return None


MENU = """
=============== DEADLOCK HERO TABLE ===============
  1. List columns
  2. Sort by a column
  3. Top N by a column
  4. Which stats actually vary
  5. Show every field for one hero
  6. Group by a column
  7. Export to heroes.csv
  8. Toggle playable-only / all heroes
  0. Quit
"""


def menu():
    playable = True
    df = build(playable_only=playable)

    while True:
        print(MENU)
        print(f"  loaded: {len(df)} heroes x {len(df.columns)} columns"
              f"  ({'playable only' if playable else 'all heroes'})")

        choice = ask("\n  choose: ")
        if choice is None or choice == "0" or choice.lower() in ("q", "quit", "exit"):
            print("  bye")
            return

        if choice == "1":
            needle = ask("  filter by keyword (blank = all): ")
            cols(df, needle or None)

        elif choice == "2":
            col = pick_column(df, "sort by")
            if col:
                asc = (ask("  ascending? (y/N): ", "n")).lower().startswith("y")
                sort(df, col, ascending=asc)

        elif choice == "3":
            col = pick_column(df, "rank by")
            if col:
                n = ask("  how many rows (default 10): ", "10")
                asc = (ask("  ascending? (y/N): ", "n")).lower().startswith("y")
                sort(df, col, ascending=asc, limit=int(n) if n.isdigit() else 10)

        elif choice == "4":
            vary(df)

        elif choice == "5":
            who = ask("  hero name (partial is fine): ")
            if who:
                show(df, who)

        elif choice == "6":
            col = pick_column(df, "group by")
            if col:
                group(df, col)

        elif choice == "7":
            to_csv(df)

        elif choice == "8":
            playable = not playable
            df = build(playable_only=playable)
            print(f"  reloaded: {len(df)} heroes")

        else:
            print("  pick a number from the menu.")

        if ask("\n  [enter] to continue ") is None:
            return


# --------------------------------------------------------------- entry point

if __name__ == "__main__":
    args = sys.argv[1:]
    if not os.path.exists(HEROES_FILE):
        sys.exit(f"{HEROES_FILE} not found - run api.py first to download it")

    # no arguments -> interactive menu
    if not args or args[0] in ("menu", "-i"):
        menu()
        sys.exit()

    df = build(playable_only="--all" not in args)
    cmd, asc = args[0], "--asc" in args

    if cmd == "cols":
        cols(df, args[1] if len(args) > 1 and not args[1].startswith("--") else None)
    elif cmd == "sort" and len(args) > 1:
        sort(df, args[1], ascending=asc)
    elif cmd == "top" and len(args) > 2:
        sort(df, args[1], ascending=asc, limit=int(args[2]))
    elif cmd == "vary":
        vary(df)
    elif cmd == "show" and len(args) > 1:
        show(df, args[1])
    elif cmd == "csv":
        to_csv(df)
    else:
        print(__doc__)
