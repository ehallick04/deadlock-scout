"""
Measure souls-over-time curves per hero and rank band from the server's store.

    .venv/bin/python -m theorycraft.measure_souls ~/deadlock-server/deadlock_server.db

Writes theorycraft/souls_curves.json: aggregated medians only, no account ids,
so it is safe for this public repo.

The sample times of `net_worth_series` were established on 6 Nov 2026 against
upstream `stats[].time_stamp_s` (and hold for all 480k stored player rows):
every 180 s up to 900 s, every 300 s after that, plus a final sample at the
match's duration.

Each band reads ONE sweep frame (the server's rule: frames are never mixed):
Phantom and above from the badge-91 sweep, everything below from the badge-0
sweep's ranked games. Scouted matches are excluded: they are whoever someone
asked about, not a sample.
"""

import json
import os
import sqlite3
import statistics
import sys
from collections import defaultdict

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "souls_curves.json")

# band -> (sweep frame, tiers). Tiers: 1 Initiate .. 11 Eternus.
BANDS = {
    "Initiate-Acolyte": (0, range(1, 4)),
    "Sentinel-Ritualist": (0, range(4, 7)),
    "Emissary-Oracle": (0, range(7, 9)),
    "Phantom": (91, range(9, 10)),
    "Ascendant": (91, range(10, 11)),
    "Eternus": (91, range(11, 12)),
}
MIN_PLAYERS = 30        # per hero, band and time point; thinner points are dropped
START_SOULS = 600


def sample_times(duration_s, n):
    """The schedule upstream samples on, cut to this match."""
    sched, t = [], 180
    while t < duration_s:
        sched.append(t)
        t += 180 if t < 900 else 300
    sched.append(duration_s)
    return sched if len(sched) == n else None      # refuse rows that do not fit


def measure(db_path):
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    rows = con.execute("""
        select p.hero_id, m.average_badge, m.sweep_min_badge, m.duration_s, p.net_worth_series
        from match_players p join matches m using(match_id)
        where m.ingest_reason = 'sweep' and m.match_mode = 'ranked'
          and m.game_mode = 'normal' and coalesce(m.not_scored, 0) = 0
          and p.net_worth_series is not null and p.hero_id is not null""")
    # (band, hero) -> t -> [net worth]; the match-end sample is skipped because
    # its time differs per match and it would pile every game's end onto odd seconds.
    acc = defaultdict(lambda: defaultdict(list))
    misfit = 0
    for hero, badge, frame, dur, series in rows:
        if not badge or not dur:
            continue
        tier = badge // 10
        band = next((b for b, (f, tiers) in BANDS.items() if f == frame and tier in tiers), None)
        if band is None:
            continue
        vals = json.loads(series) if isinstance(series, str) else series
        times = sample_times(dur, len(vals))
        if times is None:
            misfit += 1
            continue
        for t, v in zip(times[:-1], vals[:-1]):
            acc[(band, hero)][t].append(v)
            acc[(band, "all")][t].append(v)

    out = {"_about": {
        "measured": "2026-11-06", "source": "deadlock-server sweep matches, ranked normal games",
        "statistic": "median net worth at each sample time; t=0 is the 600 starting souls",
        "min_players_per_point": MIN_PLAYERS, "rows_not_fitting_schedule": misfit,
        "bands": {b: {"frame": f, "tiers": list(t)} for b, (f, t) in BANDS.items()}},
        "curves": {}}
    for (band, hero), by_t in sorted(acc.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))):
        pts = [[0, START_SOULS, None]]
        for t in sorted(by_t):
            v = by_t[t]
            if len(v) >= MIN_PLAYERS:
                pts.append([t, round(statistics.median(v)), len(v)])
        if len(pts) >= 4:
            out["curves"].setdefault(band, {})[str(hero)] = pts
    return out


if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser(
        "~/deadlock-server/deadlock_server.db")
    data = measure(db)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    c = data["curves"]
    print(f"wrote {OUT}: {sum(len(v) for v in c.values())} curves across {len(c)} bands; "
          f"{data['_about']['rows_not_fitting_schedule']} rows did not fit the schedule")
