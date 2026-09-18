"""
objectives_view.py — the objectives report, rendered.

One function serves both call sites: the ladder sample in Meta, and a roster's
custom games in Pros. They ask different questions of the same tables, and the
difference in sample size is what shapes the display.

For the ladder there are tens of thousands of matches, so the conditional win
rates are worth reading. For a roster's tournament customs there are a few
hundred at best — across five state bands and a dozen objective types that is
single digits a cell, and a win rate built on nine matches is a number-shaped
rumour. So the pro view leads with timing, which *is* estimable at that size,
and shows the win rates only where the count supports them.
"""

import pandas as pd
import streamlit as st

BANDS = ["far behind", "behind", "even", "ahead", "far ahead"]

# Fallbacks only. The server sends the ranges with the payload so the two can
# never drift apart -- a band labelled "±2k" in the app while the server bins
# at 3k would be worse than no label at all.
BAND_LABELS = {
    "far behind": "behind 6k+", "behind": "behind 2–6k",
    "even": "within ±2k", "ahead": "ahead 2–6k", "far ahead": "ahead 6k+",
}


def _labels(payload: dict) -> dict:
    sent = {b["name"]: b["label"] for b in (payload.get("bands") or [])
            if b.get("name") and b.get("label")}
    return {**BAND_LABELS, **sent}

PREAMBLE = """
Taking objectives and winning are both caused by being ahead, so "how often does
the team that killed mid boss win?" is a large number that means nothing —
teams already winning take mid boss. Every rate below is therefore split by the
**soul difference when the objective fell**, from the point of view of the team
that took it.

**Read the `within ±2k` column.** The outer columns mostly restate who was
already winning. An objective that reads high in every band is a scoreboard,
not a lever.
"""


def _timing_frame(objectives: dict) -> pd.DataFrame:
    rows = []
    for kind, row in objectives.items():
        rows.append({
            "objective": row.get("label", kind),
            "seen": row["seen"],
            "fell": row["destroyed"],
            "median fall": _clock(row["median_destroy_s"]),
            "contested": (f"{int(row['median_contest_s'])}s"
                          if row["median_contest_s"] is not None else "—"),
            "fell to creeps": (f"{row['creep_led_pct']}%"
                               if row["creep_led_pct"] is not None else "—"),
        })
    return pd.DataFrame(rows)


def _clock(seconds) -> str:
    if not seconds:
        return "—"
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


def _rates_frame(objectives: dict, min_events: int,
                 labels: dict | None = None) -> pd.DataFrame:
    labels = labels or BAND_LABELS
    rows = []
    for kind, row in objectives.items():
        entry = {"objective": row.get("label", kind)}
        for band in BANDS:
            cell = (row.get("bands") or {}).get(band)
            name = labels.get(band, band)
            entry[name] = (round(100 * cell["wins"] / cell["n"], 1)
                           if cell and cell["n"] else None)
            entry[f"n ({name})"] = cell["n"] if cell else 0
        entry["even 95% CI"] = (
            f"{row['even_ci'][0]:.0f}–{row['even_ci'][1]:.0f}%"
            if row["even_n"] else "—")
        entry["holds up"] = ("yes" if row["significant"]
                             else "" if row["even_n"] >= min_events else "too few")
        rows.append(entry)
    return pd.DataFrame(rows)


def render(payload: dict, *, min_events: int = 30, comparison: bool = False):
    """
    Draw the report. `payload` is what the server returned.

    `comparison` switches to the named-population layout: timing against the
    ladder baseline first, win rates second and only where they are supported.
    """
    if comparison:
        return _render_comparison(payload, min_events)
    return _render_population(payload, min_events)


def _render_population(out: dict, min_events: int):
    if not out.get("matches"):
        st.info("No matches with objective detail yet. The nightly sweep "
                "collects them; a store swept before objectives were switched "
                "on will not have them until it turns over.")
        return

    sampling = out.get("sampling") or {}
    line = f"{out['matches']:,} matches · {out['objective_rows']:,} objectives"
    if out.get("deaths"):
        line += f" · {out['deaths']:,} deaths"
    if sampling.get("samples"):
        line += (f" · souls sampled every ~{sampling['interval_mean_s']:.0f}s "
                 f"({sampling['verdict']})")
    st.caption(line)
    st.markdown(PREAMBLE)

    if out.get("underpowered"):
        st.warning(
            "No objective has enough even-state cases to support a win rate "
            f"here (fewer than {out.get('min_events', min_events)}). The "
            "timing below is still meaningful; the rates are not.")

    st.markdown("**When they fall, and how**")
    st.dataframe(_timing_frame(out["objectives"]), hide_index=True,
                 use_container_width=True)

    st.markdown("**Win rate of the team that took it, by soul difference**")
    st.dataframe(_rates_frame(out["objectives"],
                              out.get("min_events", min_events),
                              _labels(out)),
                 hide_index=True, use_container_width=True)

    boss = out.get("mid_boss") or {}
    if boss.get("kills"):
        steals = boss.get("steals", 0)
        st.markdown(
            f"**Mid boss** — {boss['kills']:,} kills, {steals:,} stolen "
            f"({100 * steals / boss['kills']:.1f}%). A steal is "
            "`killed_by != claimed_by`, which is the only way it shows up.")
        labels = _labels(out)
        rows = [{"soul difference": labels.get(band, band),
                 "win rate": round(100 * cell["wins"] / cell["n"], 1),
                 "n": cell["n"]}
                for band in BANDS
                for cell in [(boss.get("bands") or {}).get(band)] if cell and cell["n"]]
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True)

    st.caption(
        "“Holds up” means the even-state rate survives Benjamini–Hochberg "
        "across every objective tested — a dozen objectives across five bands "
        "is sixty tests, and three look significant from noise alone. None of "
        "this is causal: objectives arrive bundled with the fight that won "
        "them, and an objective's own soul reward is part of the swing it "
        "appears to cause.")


def _render_comparison(out: dict, min_events: int):
    theirs = out.get("population") or {}
    ladder = out.get("baseline") or {}
    if not theirs.get("matches"):
        st.info("No matches found for these players in the window. Custom "
                "games need `private_lobby`, and a longer window than the "
                "ladder default.")
        return

    st.caption(f"{theirs['matches']:,} matches for these players · "
               f"{ladder.get('matches', 0):,} in the ladder baseline")

    st.markdown(
        "At this sample size the **timing** is what can be read, not the win "
        "rates. A few hundred tournament games across five state bands and a "
        "dozen objective types is single digits a cell. Timing differences, "
        "though, are exactly where tournament play departs from the ladder.")

    rows = []
    for kind, row in sorted((out.get("timing") or {}).items(),
                            key=lambda kv: kv[1].get("order", 99)):
        rows.append({
            "objective": row.get("label", kind),
            "fell": row["destroyed"],
            "these players": _clock(row["median_destroy_s"]),
            "ladder": _clock(row["baseline_destroy_s"]),
            "difference": (f"{row['destroy_delta_s']:+d}s"
                           if row["destroy_delta_s"] is not None else "—"),
            "contested": (f"{int(row['median_contest_s'])}s"
                          if row["median_contest_s"] is not None else "—"),
            "ladder contested": (f"{int(row['baseline_contest_s'])}s"
                                 if row["baseline_contest_s"] is not None else "—"),
            "to creeps": (f"{row['creep_led_pct']}%"
                          if row["creep_led_pct"] is not None else "—"),
            "ladder to creeps": (f"{row['baseline_creep_led_pct']}%"
                                 if row["baseline_creep_led_pct"] is not None else "—"),
        })
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True,
                     use_container_width=True)
        st.caption("A negative difference means they take it earlier than the "
                   "ladder does.")

    supported = {k: v for k, v in (theirs.get("objectives") or {}).items()
                 if v["even_n"] >= theirs.get("min_events", min_events)}
    with st.expander(f"Win rates by soul difference "
                     f"({len(supported)} objective(s) with enough cases)"):
        if supported:
            st.dataframe(_rates_frame(supported,
                                      theirs.get("min_events", min_events),
                                      _labels(theirs)),
                         hide_index=True, use_container_width=True)
        else:
            st.info("Nothing here yet has enough even-state cases to report a "
                    "rate. Widen the window, or add more of the roster.")

    boss = theirs.get("mid_boss") or {}
    if boss.get("kills"):
        base = (ladder.get("mid_boss") or {})
        theirs_rate = 100 * boss.get("steals", 0) / boss["kills"]
        line = (f"**Mid boss** — {boss['kills']:,} kills, "
                f"{boss.get('steals', 0):,} stolen ({theirs_rate:.1f}%)")
        if base.get("kills"):
            line += (f", against {100 * base.get('steals', 0) / base['kills']:.1f}% "
                     f"on the ladder")
        st.markdown(line)
