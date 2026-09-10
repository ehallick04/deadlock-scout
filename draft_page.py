"""
draft_page.py -- the Streamlit front end for turning a Night Shift graphic into
a database row.

Nothing reaches the database without a person looking at it first. On the five
graphics this was built against the reader gets all twelve picks and the
first-pick side right every time, but it is reading a stream overlay that has
changed twice in a year and will change again, and the failure it would produce
is a confidently wrong hero name rather than an obviously blank one. So the flow
is always: parse, show the read beside the picture, let the reviewer fix what is
wrong, then save.

draft.py owns the reading and the schema; this module only presents them.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
from PIL import Image

import draft

try:
    from deadlock import playable_hero_names
except Exception:  # pragma: no cover - app.py always has it, scripts may not
    playable_hero_names = None


UNKNOWN = "— not read —"


# ---------------------------------------------------------------- hero choices


@st.cache_data(show_spinner=False)
def hero_options() -> list[str]:
    names = ()
    if playable_hero_names is not None:
        try:
            names = playable_hero_names().values()
        except Exception:
            names = ()
    if not names:
        try:
            names = draft.hero_names()
        except Exception:
            names = ()
    return [UNKNOWN] + sorted({str(n) for n in names if n})


def _index_of(hero: str | None, options: list[str]) -> int:
    if not hero:
        return 0
    if hero in options:
        return options.index(hero)
    folded = {o.casefold(): i for i, o in enumerate(options)}
    return folded.get(str(hero).casefold(), 0)


def _slot(entries: list, index: int) -> tuple[str | None, float | None]:
    if index < len(entries) and entries[index]:
        hero, score = entries[index]
        return hero, score
    return None, None


# ---------------------------------------------------------------- parsing


@st.cache_data(show_spinner="Reading the graphic…", max_entries=32)
def _parse(data: bytes) -> dict:
    return draft.parse(Image.open(io.BytesIO(data)).convert("RGB"))


def ban_sequence(first_side: str) -> list[dict]:
    """Phase 1 bans first team then second; Phase 2 mirrors that."""
    second = "right" if first_side == "left" else "left"
    seen = {"left": 0, "right": 0}
    order: list[dict] = []
    for phase, sides in ((1, (first_side, second)), (2, (second, first_side))):
        for side in sides:
            order.append({"number": len(order) + 1, "side": side,
                          "slot": seen[side], "phase": phase})
            seen[side] += 1
    return order


# ---------------------------------------------------------------- review form


def _review(uploaded) -> None:
    data = uploaded.getvalue()
    key = hashlib.sha1(data).hexdigest()[:12]
    image = Image.open(io.BytesIO(data)).convert("RGB")

    try:
        parsed = _parse(data)
    except Exception as exc:  # a bad crop should not take the page down
        st.error(f"Could not read this image: {exc}")
        parsed = {}

    st.image(data, use_container_width=True)

    layout = parsed.get("layout") or {}
    if layout.get("names_found") is not None:
        st.caption(f"Read {layout['names_found']} of 12 hero names · column "
                   f"pitch {layout.get('pitch')}px")

    options = hero_options()

    # ---- what match this is
    st.subheader("Match")
    c1, c2, c3, c4, c5 = st.columns([1, 1.3, 1.3, 1.3, 1])
    shift = c1.text_input("Shift", value=str(parsed.get("shift") or ""),
                          key=f"shift{key}")
    # Night Shift runs weekly, so the date follows from the number. The key
    # carries the shift so that correcting the number re-derives the date
    # instead of leaving last shift's Wednesday sitting in the box.
    when = c2.text_input("Date", value=draft.date_for(shift),
                         key=f"date{key}{shift}",
                         help="Derived from the shift number — every Wednesday. "
                              "Edit it if a week was moved or skipped.")
    match_type = c3.text_input("Stage", value=str(parsed.get("match_type") or ""),
                               key=f"type{key}",
                               help="Finals, Challenger, Group stage…")
    map_label = c4.text_input("Map", value=str(parsed.get("map_label") or ""),
                              key=f"map{key}")
    region = c5.text_input("Region", value=str(parsed.get("region") or ""),
                           key=f"region{key}")

    c1, c2, c3, c4 = st.columns([3, 1, 1, 3])
    left_team = c1.text_input("Left team", value=str(parsed.get("left_team") or ""),
                              key=f"lteam{key}")
    left_score = c2.number_input("Score", 0, 9, int(parsed.get("left_score") or 0),
                                 key=f"lscore{key}")
    right_score = c3.number_input("Score ", 0, 9, int(parsed.get("right_score") or 0),
                                  key=f"rscore{key}")
    right_team = c4.text_input("Right team", value=str(parsed.get("right_team") or ""),
                               key=f"rteam{key}")

    names = {"left": left_team or "Left", "right": right_team or "Right"}

    # ---- first pick decides the whole draft order, so it is never guessed
    read_first = parsed.get("first_pick")
    read_first = read_first if read_first in ("left", "right") else None
    first_side = st.radio(
        "First pick", ("left", "right"),
        index=("left", "right").index(read_first) if read_first else None,
        format_func=lambda s: names[s], horizontal=True, key=f"first{key}")
    if read_first:
        st.caption(f"Read from the badge as **{names[read_first]}** — change it if wrong.")
    else:
        st.warning("The first-pick badge did not read cleanly. Choose a side — "
                   "the pick order depends on it.")

    # ---- bans: no text to read, so the pictures go next to the dropdowns
    st.subheader("Bans")
    st.caption("Ban portraits carry no label, so these are named by eye.")
    boxes = parsed.get("ban_boxes") or {}
    ban_choice: dict[tuple[str, int], str] = {}
    for side in ("left", "right"):
        st.markdown(f"**{names[side]}**")
        strip = draft.ban_strip(image, boxes, side) if boxes else None
        picture, *fields = st.columns([2] + [1] * draft.BANS_PER_TEAM)
        if strip is not None:
            picture.image(strip, use_container_width=True)
        else:
            picture.caption("ban area not located")
        for slot in range(draft.BANS_PER_TEAM):
            ban_choice[(side, slot)] = fields[slot].selectbox(
                f"Ban {slot + 1}", options, key=f"ban{key}{side}{slot}")

    # ---- picks, in the order they were taken
    st.subheader("Picks")
    if not first_side:
        st.info("Choose a first-pick side above to lay the picks out in draft order.")
        return

    pick_choice: dict[tuple[str, int], str] = {}
    for step in draft.sides_for(first_side):
        side, index = step["side"], step["slot"]
        hero, score = _slot(parsed.get(f"{side}_picks") or [], index)
        c1, c2, c3, c4 = st.columns([1, 2, 3, 2])
        c1.markdown(f"**{step['number']}**  \n<small>phase {step['phase']}</small>",
                    unsafe_allow_html=True)
        c2.markdown(f"<div style='padding-top:0.4rem'>{names[side]}</div>",
                    unsafe_allow_html=True)
        pick_choice[(side, index)] = c3.selectbox(
            f"Pick {step['number']}", options, index=_index_of(hero, options),
            key=f"pick{key}{side}{index}", label_visibility="collapsed")
        if hero is None:
            c4.caption("not read")
        else:
            c4.caption(f"read: {hero}" + (f" ({score:.2f})" if score else ""))

    # ---- what will be written
    rows = []
    for step in ban_sequence(first_side):
        rows.append({"#": step["number"], "phase": step["phase"], "action": "ban",
                     "team": names[step["side"]], "side": step["side"],
                     "hero": ban_choice.get((step["side"], step["slot"]), UNKNOWN)})
    for step in draft.sides_for(first_side):
        rows.append({"#": step["number"], "phase": step["phase"], "action": "pick",
                     "team": names[step["side"]], "side": step["side"],
                     "hero": pick_choice.get((step["side"], step["slot"]), UNKNOWN)})

    with st.expander("What will be saved"):
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

    chosen = [r["hero"] for r in rows if r["hero"] != UNKNOWN]
    if len(set(chosen)) != len(chosen):
        repeated = sorted({h for h in chosen if chosen.count(h) > 1})
        st.error("Heroes are unique across a match, so these cannot all be "
                 "right: " + ", ".join(repeated))
        return

    problems = []
    if not left_team.strip() or not right_team.strip():
        problems.append("both team names")
    if not shift.strip():
        problems.append("the shift number")
    missing = sum(1 for r in rows if r["action"] == "pick" and r["hero"] == UNKNOWN)
    if missing:
        problems.append(f"{missing} pick{'s' if missing > 1 else ''}")
    if problems:
        st.info("Still to fill in: " + ", ".join(problems) + ".")

    record = {
        "shift": shift.strip(), "date": when.strip(),
        "match_type": match_type.strip(),
        "map_label": map_label.strip(), "region": region.strip(),
        "left_team": left_team.strip(), "right_team": right_team.strip(),
        "left_score": int(left_score), "right_score": int(right_score),
        "first_pick": first_side, "source": uploaded.name,
        "picks": [r for r in rows if r["action"] == "pick" and r["hero"] != UNKNOWN],
        "bans": [r for r in rows if r["action"] == "ban" and r["hero"] != UNKNOWN],
    }

    if st.button("Save to database", type="primary", key=f"save{key}",
                 disabled=bool(problems)):
        try:
            saved = draft.save(record)
        except Exception as exc:
            st.exception(exc)
        else:
            st.success(f"Saved `{saved}` — {names['left']} vs {names['right']}, "
                       f"shift {shift}. Saving this same image again replaces "
                       "that line rather than duplicating it.")


# ---------------------------------------------------------------- browsing


def _browse() -> None:
    path = Path(draft.DRAFT_FILE)
    st.caption(f"Everything lives in `{path.name}` — one line per draft, "
               "committed to the repo. Nothing here depends on a cache.")

    try:
        rows = draft.drafts()
    except Exception as exc:
        st.error(f"Could not read {path.name}: {exc}")
        return

    nights = draft.shift_summary()
    if nights:
        st.markdown("**Nights on file**")
        st.dataframe(pd.DataFrame(nights), hide_index=True,
                     use_container_width=True)

    if not rows:
        st.info("No drafts saved yet.")
        _import_box()
        return

    st.markdown("**Drafts**")
    table = pd.DataFrame(rows)
    st.dataframe(table, hide_index=True, use_container_width=True)

    c1, c2 = st.columns(2)
    c1.download_button(
        f"Download {path.name}",
        path.read_bytes() if path.is_file() else b"",
        file_name=path.name, mime="application/x-ndjson",
        help="The file itself. Commit this after a night's entries, or carry "
             "it to another machine and import it there.")
    c2.download_button("Download as CSV",
                       pd.DataFrame(draft.export_rows()).to_csv(index=False),
                       file_name="drafts.csv", mime="text/csv",
                       help="One row per hero — for a spreadsheet, not for "
                            "reading back in.")

    chosen = st.selectbox("Show the draft for", table["id"].tolist(),
                          format_func=lambda i: _describe(table, i))
    st.dataframe(pd.DataFrame(draft.heroes_for(chosen)), hide_index=True,
                 use_container_width=True)
    if st.button("Delete this draft"):
        draft.delete(chosen)
        st.rerun()

    _import_box()


def _import_box() -> None:
    """Fold in a file entered elsewhere -- another machine, or a cloud session
    whose filesystem does not survive a redeploy."""
    with st.expander("Import a drafts file"):
        incoming = st.file_uploader("drafts.jsonl", type=["jsonl", "json"],
                                    key="import_drafts")
        if incoming is None:
            return
        try:
            records = [json.loads(line) for line
                       in incoming.getvalue().decode("utf-8").splitlines()
                       if line.strip()]
        except Exception as exc:
            st.error(f"Could not read that file: {exc}")
            return
        st.caption(f"{len(records)} drafts in the uploaded file.")
        if st.button("Merge into this file"):
            added, replaced = draft.merge(records)
            st.success(f"{added} added, {replaced} replaced.")
            st.rerun()


def _describe(table: pd.DataFrame, value: Any) -> str:
    row = table.loc[table["id"] == value].iloc[0].to_dict()
    return (f"shift {row.get('shift') or '?'} · {row.get('date') or '?'} · "
            f"{row.get('left_team') or '?'} vs {row.get('right_team') or '?'}"
            + (f" ({row['map_label']})" if row.get("map_label") else ""))


# ---------------------------------------------------------------- entry point


def render() -> None:
    st.header("Draft capture")
    st.caption("Night Shift pick/ban graphics, read and then checked by you "
               "before anything is written down.")

    upload, browse = st.tabs(["Upload a graphic", "Saved drafts"])

    with upload:
        if getattr(draft, "pytesseract", None) is None:
            st.error("OCR is not available here — install pytesseract and the "
                     "tesseract binary. Drafts can still be entered by hand.")
        files = st.file_uploader("Screenshot", type=["png", "jpg", "jpeg", "webp"],
                                 accept_multiple_files=True)
        if not files:
            st.info("Drop in one or more graphics. Each is read, shown for "
                    "checking, and only written to the database when you save.")
        for uploaded in files:
            with st.expander(uploaded.name, expanded=len(files) == 1):
                _review(uploaded)

    with browse:
        _browse()
