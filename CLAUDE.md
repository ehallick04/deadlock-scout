# deadlock-scout

**Public repo.** Streamlit client for a Deadlock esports scouting tool: the
domain layer, the Night Shift draft reader, and the UI. It reads from the
private `deadlock-server`, which owns the match store and every call to
`api.deadlock-api.com`.

Because this repo is public, nothing secret may land in it — see Secrets.

## Commands

```bash
./run.sh                 # streamlit run app.py, via .venv
./run.sh --server.port 8502
.venv/bin/python -m pytest -q tests     # pytest is dev-only, not in requirements.txt
.venv/bin/python build_rosters.py ~/Downloads/dse_rosters.html   # refresh rosters.json
```

**Always use `run.sh`.** A bare `streamlit run` picks up the Anaconda base
environment, whose pyarrow/numpy pairing is broken and crashes on import. This
has been hit three times; `app.py` carries a startup guard that turns the
traceback into a sentence.

## Shape

- `app.py` — the UI. Modes: **Meta · Players · Pros · Drafts · Climb · Standings**. Meta, Drafts, Climb and Standings short-circuit the roster sidebar; the rest share it.
- `team_ranks.py`, `standings_view.py` — league teams ranked by their players' average rank, averaged on ladder rungs (never raw badges) with unranked players excluded and thin teams unplaced. Ranks come from the bulk `/v1/players/rank`, where `rank` is the **tier** and `badge` the badge.
- `deadlock.py` — the domain layer and most API parsing. Months of empirically-derived field aliases live in its key tuples; prefer extending them to adding a parser.
- `api.py` — HTTP plus the disk cache, and the routing seam to the server.
- `server.py` — the client half of the server integration. **Entirely opt-in**: with `DEADLOCK_SERVER` unset, `configured()` is False, `api.py` never calls in, and the app behaves exactly as it did before the server existed. Keep that property.
- `synergy.py`, `objectives_view.py`, `climb_view.py` — analysis and views.
- `draft.py`, `draft_page.py` — the Night Shift pick/ban reader.
- `gate.py` — PBKDF2 password gate for the deployed app.

Only `/v1/matches/metadata` routes through the server. Assets, ranks and
hero-stats still go direct, so the Hero win rates tab is unaffected. The
objectives and players endpoints are server-only analyses and call `server.py`
directly.

## The rules that are load-bearing

**`average_badge` is not a number you can do arithmetic on.** Badge =
tier × 10 + subrank and **subranks run 1–6**, so 76 is Emissary 6 and the next
rank up is 81 — 77–80 map to no rank. `climb_view._preview_band` mirrors
`players.rank_band` on the server; **the server is authoritative**, and the two
are checked against each other across every badge and spread. If they ever
disagree, believe the server. Badge 0 is the unranked sentinel, not Obscurus 1.

Tier order, which is not guessable: Obscurus, Initiate, Seeker, Acolyte,
Sentinel, Mystic, Ritualist, **Emissary, Oracle**, Phantom, Ascendant, Eternus.
`ranks.json` is the authority; `RANKS` in `deadlock.py` omits Obscurus.

**Personanames are player-controlled text.** Streamlit renders markdown in
captions, expander labels and metric help, so everything that reaches those goes
through `climb_view._safe` first. A name of `[click](http://…)` would otherwise
render as a link someone else chose.

**Nothing in the draft reader is saved unreviewed.** First pick returns None
unless exactly one side shows the gold badge; the page will not lay out picks
until a side is chosen; save is blocked on a duplicate hero.

**Draft layout is discovered, not configured.** Every real screenshot is a
differently-shaped crop of a stream overlay, so fractional bands do not work.
One sparse OCR pass finds every word; the row with the most hero names *is* the
pick row; column pitch is measured from the names found. Details that cost real
debugging: the panel offset is **fitted**, not anchored on the outermost name;
the name strip is re-read alone at 4×; the title row is found by its BANS
captions, not by text size; first pick is detected by **colour** (red-minus-blue
gold), never OCR.

**`drafts.jsonl` is authoritative and committed.** It holds hand-checked
readings of a broadcast archived nowhere else — it cannot be re-fetched, so it
travels in a commit and reads as a diff. Mirroring it into the database is fine;
making the database authoritative is a downgrade.

## Secrets

This repo is **public**.

- `.streamlit/secrets.toml` is gitignored and holds `DEADLOCK_SERVER`, the server login and the gate keys. Never commit it, never `export` the password (shell history, `ps`), never paste one into chat.
- `build_rosters.py` keeps the previous roster for any team whose harvested page came back empty, marked `stale_since`; a failed fetch otherwise looks exactly like a team leaving the league.
- `rosters.json` — ~1,290 league players' account IDs — **is** committed, which was defensible when the tool was one person's and is not as the backing store for a multi-team service. Moving it server-side is an open item; do not add to it.
- Probe dumps carry real account IDs and are gitignored.

## Gotchas

- OCR needs `brew install tesseract` plus `pillow` and `pytesseract`; `packages.txt` carries `tesseract-ocr` for Streamlit Cloud.
- `playable_hero_names()` returns `{hero_id: name}` — iterate `.values()`, not the keys.
- Night Shift runs weekly on Wednesdays. Anchor: **shift 54 = 2 September 2026** (`draft.SHIFT_ANCHOR`). Shift numbers are normalised on save (`01` → `1`) or one night splits into two.
- A draft's identity includes the source filename, because the map label is the most-misread field.

## Deeper context

Three documents in the claude.ai project "Deadlock data stuff" carry the full
history: `deadlock-scout-record.md` (architecture and measured results),
`deadlock-api-metadata.md` (the API's real contract, including why region is not
obtainable), `deadlock-team-scouting-handoff.md` (the current goal). Ask for
them if something here seems arbitrary — most of it was expensive to learn.
