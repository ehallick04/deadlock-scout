"""
draft.py -- read a Deadlock Night Shift pick/ban graphic, and keep the result.

The picks carry their hero's name in text under the portrait, so this is an OCR
problem rather than an image-matching one -- which is lucky, because the
tournament has run for a year and the portrait art changed underneath it.
Matching last season's portraits against this season's reference sheets scored
10/32; reading the labels does far better.

Nothing here assumes where anything sits. An earlier version swept a list of
candidate bands and panels expressed as fractions of the image, which works
right up until someone crops the graphic out of a stream -- and every real
screenshot is a crop, each a different shape. So instead one sparse-text pass
locates every word in the image, the row holding the most hero names is the
pick row by definition, and the column pitch is measured from the names that
were found. Missing names are then re-read from the cells that measurement
predicts. The layout is discovered, not configured.

What it deliberately does not do is guess. The first-pick badge is stylised
enough that OCR returns "IST PICK" on a good day, and the entire draft order
hangs on which side wears it, so anything short of exactly one side showing it
returns None and the question goes to whoever is reviewing. Bans are portraits
with no text at all; the reader locates the squares and hands back the pictures,
but naming them is a person's job until there is a labelled library.

Readings are kept in drafts.jsonl -- a line per draft, committed to the repo,
because they exist nowhere else and cannot be re-fetched.

CLI:
    python draft.py read shots/*.png
    python draft.py list
    python draft.py nights
    python draft.py merge other-drafts.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import statistics
import sys
from datetime import date, datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path

from PIL import Image, ImageChops, ImageOps

try:
    import pytesseract
except Exception:  # pragma: no cover - the page reports this to the user
    pytesseract = None


HERE = Path(__file__).resolve().parent

PICKS_PER_SIDE = 6
BANS_PER_TEAM = 2

# Phase 1 is one ban each, then 1-2-2-1 picks; Phase 2 mirrors it. "first" and
# "second" are the first- and second-picking teams, not the left and right ones.
PICK_SEQUENCE = (
    ("first", 1), ("second", 2), ("second", 3), ("first", 4),
    ("first", 5), ("second", 6), ("second", 7), ("first", 8),
    ("first", 9), ("second", 10), ("second", 11), ("first", 12),
)

# OCR reads the stylised badge as "IST PICK" about as often as "1ST PICK", so
# the leading glyph is left open. Only ever used as one half of a two-sided test.
PICK_BADGE = re.compile(r"[1lI]\s*s?\s*t?\s*p\s*[il1]\s*c\s*k", re.I)

# Words that share the team-name row but are not team names.
FURNITURE = re.compile(r"^(bans?|picks?|vs\.?|and|match|[-–—|:.]+)$", re.I)

REGIONS = {"na": "NA", "eu": "EU", "oce": "OCE", "sa": "SA",
           "asia": "Asia", "sea": "SEA"}

STAGES = ("grand final", "semi final", "semifinal", "quarter final",
          "quarterfinal", "final", "challenger", "group", "showmatch",
          "qualifier")

# Ban squares, measured against the pick-column pitch rather than against the
# "BANS" label: the pitch is derived from six repeats and is stable, whereas the
# label's own OCR box varies by a factor of two between the two sides.
BAN_WIDTH = 0.35
BAN_HEIGHT = 0.48
BAN_GAP = 0.22

# Only a backstop for when heroes.json is missing; deadlock.py is the real list.
FALLBACK_HEROES = (
    "Abrams", "Bebop", "Billy", "Calico", "Celeste", "Drifter", "Dynamo",
    "Grey Talon", "Haze", "Holliday", "Infernus", "Ivy", "Kelvin", "Lady Geist",
    "Lash", "McGinnis", "Mirage", "Mo & Krill", "Paige", "Paradox", "Pocket",
    "Rem", "Seven", "Shiv", "Sinclair", "Venator", "Victor", "Vindicta",
    "Viscous", "Vyper", "Warden", "Wraith", "Yamato",
)

_HERO_CACHE: list[str] = []
_KEY_CACHE: dict[str, str] = {}
_STOPWORDS = {"and", "the", "of"}


def key(name: str) -> str:
    """
    A comparison key that survives OCR: letters only, joining words dropped, so
    the overlay's "Mo and Krill" and the API's "Mo & Krill" land on one string.
    """
    return "".join(w for w in re.findall(r"[a-z]+", str(name).lower())
                   if w not in _STOPWORDS)


def hero_names() -> list[str]:
    """The playable roster, from the domain layer when it will load."""
    global _HERO_CACHE, _KEY_CACHE
    if _HERO_CACHE:
        return _HERO_CACHE
    try:
        from deadlock import playable_hero_names
        # playable_hero_names() is {hero_id: name}; the names are the half we want
        names = sorted({str(n) for n in playable_hero_names().values() if n})
    except Exception:
        names = []
    _HERO_CACHE = names or list(FALLBACK_HEROES)
    _KEY_CACHE = {key(n): n for n in _HERO_CACHE if key(n)}
    return _HERO_CACHE


def _index() -> dict[str, str]:
    if not _KEY_CACHE:
        hero_names()
    return _KEY_CACHE


def snap(text: str) -> tuple[str | None, float]:
    """
    Fit a scrap of OCR output to a hero name. Exact key first, then containment
    (tesseract likes to bolt stray marks onto a word), then edit distance. Below
    the floor it returns None: a blank the reviewer fills in beats a confident
    wrong name they might not look twice at.
    """
    index = _index()
    k = key(text)
    if not k:
        return None, 0.0
    if k in index:
        return index[k], 1.0

    best, score = None, 0.0
    for hero_key, name in index.items():
        if len(hero_key) >= 3 and (hero_key in k or k in hero_key):
            ratio = min(len(hero_key), len(k)) / max(len(hero_key), len(k))
            if ratio > score:
                best, score = name, ratio
    if best and score >= 0.5:
        return best, round(score, 2)

    best, score = None, 0.0
    for hero_key, name in index.items():
        ratio = SequenceMatcher(None, k, hero_key).ratio()
        if ratio > score:
            best, score = name, ratio
    return (best, round(score, 2)) if score >= 0.62 else (None, round(score, 2))


# ------------------------------------------------------------------ imaging

SCALE = 2  # tesseract wants more pixels than a stream crop gives it


def _as_image(source) -> Image.Image:
    if isinstance(source, Image.Image):
        return source.convert("RGB")
    if isinstance(source, (bytes, bytearray)):
        import io
        return Image.open(io.BytesIO(source)).convert("RGB")
    return Image.open(str(source)).convert("RGB")


def _grey(image: Image.Image, scale: int = SCALE) -> Image.Image:
    grey = ImageOps.autocontrast(ImageOps.grayscale(image), cutoff=2)
    if scale != 1:
        grey = grey.resize((grey.width * scale, grey.height * scale), Image.LANCZOS)
    return grey


def _require_ocr() -> None:
    if pytesseract is None:
        raise RuntimeError(
            "pytesseract is not installed -- pip install pytesseract, and "
            "install the tesseract binary (brew install tesseract).")


def _text(crop: Image.Image, psm: int, whitelist: str = "",
          scale: int | None = None) -> str:
    """
    Both polarities, most-confident wins. Labels are light on dark and tesseract
    wants the opposite, but overlays differ and the gold furniture inverts
    differently from the white text, so it is cheaper to read twice than to
    reason about it.
    """
    _require_ocr()
    config = f"--psm {psm}"
    if whitelist:
        config += f" -c tessedit_char_whitelist={whitelist}"
    grey = _grey(crop, scale=scale or (4 if max(crop.size) < 400 else SCALE))
    out = []
    for variant in (grey, ImageOps.invert(grey)):
        try:
            out.append(" ".join(pytesseract.image_to_string(
                variant, config=config).split()))
        except Exception:
            out.append("")
    return max(out, key=_quality)


def _quality(text: str) -> int:
    """
    Rough "does this look like words". A correctly polarised read comes out as a
    few long alphanumeric runs, a wrongly polarised one as short fragments and
    punctuation; squaring the run lengths separates them where counting
    characters does not -- "PAN Ste] iim ate 4" has as many letters as
    "AEGIS 1ST PICK".
    """
    runs = re.findall(r"[A-Za-z0-9]{2,}", text)
    junk = sum(1 for ch in text if not (ch.isalnum() or ch.isspace()))
    return sum(len(run) ** 2 for run in runs) - 3 * junk


def words(image: Image.Image, min_conf: float = 40.0, scale: int = SCALE,
          origin: tuple[float, float] = (0.0, 0.0)) -> list[dict]:
    """
    Every word tesseract can find, in original-image pixels.

    Run in both polarities and merged, because neither alone is enough: on a
    real graphic the plain pass found "Mo" and "and" while only the inverted one
    found "Abrams" and "NA FINALS MATCH".

    `scale` and `origin` exist so a caller can come back for a second, closer
    look at one strip and get coordinates in the full image's frame.
    """
    _require_ocr()
    grey = _grey(image, scale=scale)
    found = []
    for variant in (grey, ImageOps.invert(grey)):
        try:
            data = pytesseract.image_to_data(
                variant, config="--psm 11", output_type=pytesseract.Output.DICT)
        except Exception:
            continue
        for i, text in enumerate(data["text"]):
            text = text.strip()
            try:
                conf = float(data["conf"][i])
            except (TypeError, ValueError):
                continue
            if not text or conf < min_conf:
                continue
            found.append({
                "text": text, "conf": conf,
                "x0": origin[0] + data["left"][i] / scale,
                "y0": origin[1] + data["top"][i] / scale,
                "x1": origin[0] + (data["left"][i] + data["width"][i]) / scale,
                "y1": origin[1] + (data["top"][i] + data["height"][i]) / scale,
            })
    return _dedupe(found)


def _area(word: dict) -> float:
    return max(0.0, word["x1"] - word["x0"]) * max(0.0, word["y1"] - word["y0"])


def _overlap(a: dict, b: dict) -> float:
    wide = min(a["x1"], b["x1"]) - max(a["x0"], b["x0"])
    tall = min(a["y1"], b["y1"]) - max(a["y0"], b["y0"])
    if wide <= 0 or tall <= 0:
        return 0.0
    smaller = min(_area(a), _area(b)) or 1.0
    return (wide * tall) / smaller


def _dedupe(found: list[dict]) -> list[dict]:
    """The two polarities read much of the same text; keep the surer copy."""
    kept: list[dict] = []
    for word in sorted(found, key=lambda w: -w["conf"]):
        if not any(_overlap(word, other) > 0.5 for other in kept):
            kept.append(word)
    return sorted(kept, key=lambda w: (w["y0"], w["x0"]))


def _height(word: dict) -> float:
    return word["y1"] - word["y0"]


def _mid_y(word: dict) -> float:
    return (word["y0"] + word["y1"]) / 2


def rows(found: list[dict]) -> list[list[dict]]:
    """Words gathered into text rows by vertical overlap."""
    out: list[list[dict]] = []
    for word in sorted(found, key=_mid_y):
        for row in out:
            span = statistics.median([_height(w) for w in row])
            if abs(_mid_y(row[0]) - _mid_y(word)) <= 0.6 * span:
                row.append(word)
                break
        else:
            out.append([word])
    return [sorted(row, key=lambda w: w["x0"]) for row in out]


# ------------------------------------------------------------------ pick row


def _heroes_in(row: list[dict]) -> list[dict]:
    """
    Hero names found in one text row, left to right.

    Labels run to three words ("Mo and Krill"), so runs of up to three adjacent
    words are tried longest-first. Adjacency is the guard against gluing two
    columns together: within a label the words nearly touch, while between
    columns the gap is several times the letter height.
    """
    out, i = [], 0
    while i < len(row):
        taken = 0
        for span in (3, 2, 1):
            if i + span > len(row):
                continue
            chunk = row[i:i + span]
            if any(chunk[j + 1]["x0"] - chunk[j]["x1"] > 1.2 * _height(chunk[j])
                   for j in range(span - 1)):
                continue
            hero, score = snap(" ".join(w["text"] for w in chunk))
            if hero:
                out.append({"hero": hero, "score": score,
                            "cx": (chunk[0]["x0"] + chunk[-1]["x1"]) / 2,
                            "y0": min(w["y0"] for w in chunk),
                            "y1": max(w["y1"] for w in chunk)})
                taken = span
                break
        i += taken or 1
    return out


def pick_row(found: list[dict]) -> list[dict]:
    """The row holding the most hero names -- which is what a pick row is."""
    best: list[dict] = []
    for row in rows(found):
        heroes = _heroes_in(row)
        # ties go to the lower row: names sit under portraits, furniture above
        if len(heroes) > len(best) or (len(heroes) == len(best) and heroes
                                       and best and heroes[0]["y0"] > best[0]["y0"]):
            best = heroes
    return best


def _pitch(centres: list[float], cut: int) -> float | None:
    """
    Column spacing, from whatever names were found.

    A gap where a name went unread is a whole multiple of the pitch, so each gap
    is divided by its nearest multiple of the smallest one before taking the
    median. Panels are handled separately: the space across the scoreboard is
    not a column gap.
    """
    diffs = []
    for group in (centres[:cut + 1], centres[cut + 1:]):
        diffs += [b - a for a, b in zip(group, group[1:])]
    diffs = [d for d in diffs if d > 0]
    if not diffs:
        return None
    base = min(diffs)
    return statistics.median([d / max(1, round(d / base)) for d in diffs])


def geometry(found_heroes: list[dict], width: float,
             obstacles: list[dict] | None = None) -> dict | None:
    """
    Where all twelve pick columns are, measured from the ones that were read.

    The pitch is easy -- six repeats make it obvious. Placing the panels is the
    hard half, and anchoring them on the outermost name read is wrong: on a shot
    where both end columns went unread, that slid every slot inward by one and
    quietly mislabelled ten picks. So the offset is fitted instead. Each
    candidate offset is scored by how many names land on a predicted column,
    which is only enough on its own when nothing is missing -- with five of six
    read a side, an offset one whole pitch out fits exactly as well.

    The tiebreak is what sits between the panels. The scoreboard, the shift
    number and the map line live in that gap, so an offset wide enough to swallow
    them has to be wrong. Only words in the gap count: the event watermark sits
    inside the right-hand panel on some graphics and would otherwise argue
    against the correct answer.
    """
    if len(found_heroes) < 4:
        return None
    centres = sorted(h["cx"] for h in found_heroes)
    gaps = [(b - a, i) for i, (a, b) in enumerate(zip(centres, centres[1:]))]
    if not gaps:
        return None
    _, cut = max(gaps)
    if not centres[:cut + 1] or not centres[cut + 1:]:
        return None

    pitch = _pitch(centres, cut)
    if not pitch or pitch * PICKS_PER_SIDE > width * 0.55:
        return None

    inner_left, inner_right = centres[cut], centres[cut + 1]
    blockers = [w for w in (obstacles or [])
                if inner_left < (w["x0"] + w["x1"]) / 2 < inner_right]

    panel = PICKS_PER_SIDE * pitch
    limit = int(width / 2 - panel)
    if limit < 0:
        return None

    best = None
    for start in range(0, limit + 1):
        predicted = [start + (k + 0.5) * pitch for k in range(PICKS_PER_SIDE)]
        predicted += [width - start - panel + (k + 0.5) * pitch
                      for k in range(PICKS_PER_SIDE)]
        fit = sum(1 for c in centres
                  if min(abs(c - p) for p in predicted) < pitch * 0.35)
        clashes = sum(1 for w in blockers
                      if _inside((w["x0"] + w["x1"]) / 2, start, panel, width))
        score = (fit, -clashes, -start)
        if best is None or score > best[0]:
            best = (score, start)

    start = float(best[1])
    lefts = {"left": start, "right": width - start - panel}
    return {
        "pitch": pitch,
        "panels": lefts,
        "fitted": best[0][0],
        "centres": {side: [lefts[side] + (k + 0.5) * pitch
                           for k in range(PICKS_PER_SIDE)]
                    for side in ("left", "right")},
    }


def _inside(x: float, start: float, panel: float, width: float) -> bool:
    return (start <= x <= start + panel
            or width - start - panel <= x <= width - start)


def _cell(image: Image.Image, cx: float, pitch: float,
          y0: float, y1: float) -> Image.Image:
    pad = max(4.0, (y1 - y0) * 0.4)
    return image.crop((int(max(0, cx - pitch * 0.47)), int(max(0, y0 - pad)),
                       int(min(image.width, cx + pitch * 0.47)),
                       int(min(image.height, y1 + pad))))


def read_picks(image, found: list[dict] | None = None) -> dict:
    """
    Six slots a side, each (hero, confidence), in draft order.

    Picks are laid out outside to inside, so the left panel already reads in
    order while the right panel reverses: its outermost column -- the rightmost
    on screen -- is that team's first pick.
    """
    image = _as_image(image)
    found = words(image) if found is None else found
    heroes = pick_row(found)
    heroes = _closer_look(image, heroes) or heroes
    top, bottom = _row_span(heroes)
    obstacles = [w for w in found if w["y1"] < top and not snap(w["text"])[0]]
    plan = geometry(heroes, image.width, obstacles)

    if plan is None:
        # nothing to measure from; fall back to whatever was read, in order
        ordered = sorted(heroes, key=lambda h: h["cx"])
        half = len(ordered) // 2
        slots = {"left": [(h["hero"], h["score"]) for h in ordered[:half]],
                 "right": [(h["hero"], h["score"]) for h in ordered[half:]]}
        for side in slots:
            slots[side] += [(None, 0.0)] * (PICKS_PER_SIDE - len(slots[side]))
            slots[side] = slots[side][:PICKS_PER_SIDE]
        return {"picks": _draft_order(slots), "geometry": None,
                "row": _row_span(heroes)}

    pitch = plan["pitch"]
    slots = {side: [(None, 0.0)] * PICKS_PER_SIDE for side in ("left", "right")}
    for hero in heroes:
        for side in ("left", "right"):
            offset = (hero["cx"] - plan["panels"][side] - pitch / 2) / pitch
            index = round(offset)
            if 0 <= index < PICKS_PER_SIDE and abs(offset - index) < 0.35:
                slots[side][index] = (hero["hero"], hero["score"])
                break

    # only the cells nothing was read in need a second, closer look
    for side in ("left", "right"):
        for index, (hero, _score) in enumerate(slots[side]):
            if hero is None:
                cell = _cell(image, plan["centres"][side][index], pitch, top, bottom)
                slots[side][index] = snap(_text(cell, 7))

    return {"picks": _draft_order(slots), "geometry": plan,
            "row": (top, bottom)}


def _closer_look(image: Image.Image, heroes: list[dict]) -> list[dict]:
    """
    Re-read the name strip on its own, at twice the resolution.

    The whole-image pass is run at a scale that keeps it quick, and on the older
    graphics that costs names: one shot gave up three of twelve, which is too
    few to measure a column pitch from -- the fit came out 30% wide and the
    remaining reads landed between cells. Once the coarse pass has shown roughly
    where the row is, re-reading that strip alone is cheap and recovers them.
    """
    if not heroes:
        return []
    top, bottom = _row_span(heroes)
    pad = max((bottom - top) * 1.2, image.height * 0.02)
    y0 = int(max(0, top - pad))
    y1 = int(min(image.height, bottom + pad))
    if y1 - y0 < 8:
        return []
    strip = image.crop((0, y0, image.width, y1))
    closer = words(strip, scale=4, origin=(0.0, float(y0)))
    best: list[dict] = []
    for row in rows(closer):
        names = _heroes_in(row)
        if len(names) > len(best):
            best = names
    return best if len(best) >= len(heroes) else heroes


def _row_span(heroes: list[dict]) -> tuple[float, float]:
    if not heroes:
        return 0.0, 0.0
    return (min(h["y0"] for h in heroes), max(h["y1"] for h in heroes))


def _draft_order(slots: dict) -> dict:
    return {"left": list(slots["left"]), "right": list(reversed(slots["right"]))}


# ------------------------------------------------------------------ header


def _is_bans(word: dict) -> bool:
    return word["text"].strip().lower().rstrip(":") in ("bans", "ban")


def _title_row(found: list[dict], above: float, width: float) -> list[dict]:
    """
    The row the team names sit on.

    Both teams have a "BANS" caption level with their name, so when those are
    read the row is settled. They are not always read, and the obvious fallback
    -- the biggest text above the picks -- is wrong twice over: the tournament
    wordmark in the middle is larger than either team name, and so is the
    occasional icon that OCRs as a tall letter. What actually distinguishes the
    row is having a name on *each* side of the middle, so that is tested first
    and size only breaks the tie -- which is what separates the team row from
    the roster of player handles printed just above it in the same capitals.
    """
    candidates = [row for row in rows(found)
                  if row and max(w["y1"] for w in row) < above]
    if not candidates:
        return []

    def rank(row: list[dict]) -> tuple:
        captions = sum(_is_bans(w) for w in row)
        named = {side: _team_words(row, side, width) for side in ("left", "right")}
        both = 1 if named["left"] and named["right"] else 0
        sized = [w for side in named.values() for w in side] or row
        return captions, both, statistics.median([_height(w) for w in sized])

    return max(candidates, key=rank)


def _team_words(row: list[dict], side: str, width: float) -> list[dict]:
    """
    The team name out of a row that also holds icons, a badge and a caption.

    Team names are set in capitals at the row's own size, which is enough to
    separate them from everything else sharing the row: the icons OCR into
    mixed-case scraps ("wom", "iM", "aS"), the furniture is a known word list,
    and the flourishes carry no letters at all.
    """
    if side == "left":
        half = [w for w in row if w["x1"] <= width / 2]
    else:
        half = [w for w in row if w["x0"] >= width / 2]

    named = []
    for word in half:
        text = word["text"].strip()
        letters = re.sub(r"[^A-Za-z]", "", text)
        if len(letters) < 2 or letters != letters.upper():
            continue
        if FURNITURE.match(text) or PICK_BADGE.fullmatch(text):
            continue
        named.append(word)
    if not named:
        return []
    tallest = max(_height(w) for w in named)
    return [w for w in named if _height(w) >= 0.7 * tallest]


def read_teams(found: list[dict], width: float, above: float) -> dict:
    row = _title_row(found, above, width)
    out = {}
    for side in ("left", "right"):
        out[f"{side}_team"] = " ".join(
            w["text"] for w in _team_words(row, side, width)).strip()
    out["_title_row"] = row
    return out


def centre_strip(image: Image.Image, plan: dict | None, above: float) -> str:
    """
    A close read of the gap between the panels.

    The shift number, the stage and the map line all sit there in small caps,
    and the whole-image pass drops them about half the time -- shot after shot
    came back with the event named and the shift missing. Reading that strip on
    its own costs one crop and gets them back.
    """
    if not plan or above <= 2:
        return ""
    x0 = plan["panels"]["left"] + PICKS_PER_SIDE * plan["pitch"]
    x1 = plan["panels"]["right"]
    if x1 - x0 < 20:
        return ""
    crop = image.crop((int(x0), 0, int(x1), int(above)))
    # a tall narrow crop holding a wordmark, a scoreboard and two lines of small
    # caps defeats a single block read ("PAE ova"); sparse mode copes, and at
    # this scale "SHIFT" stops coming back as "IFT"
    return " ".join(w["text"] for w in words(crop, scale=4))


def read_shift(image: Image.Image, plan: dict | None, above: float) -> str:
    """
    The shift number, read from beside its own caption.

    Left to the general passes it comes back as "#0" for shift 01 about as often
    as not -- the sparse reader keeps dropping the last digit of a two-digit
    number set in small caps. Cropping to the right of the word "SHIFT" and
    asking for digits only recovers it.
    """
    if not plan or above <= 2:
        return ""
    x0 = plan["panels"]["left"] + PICKS_PER_SIDE * plan["pitch"]
    x1 = plan["panels"]["right"]
    if x1 - x0 < 20:
        return ""
    strip = image.crop((int(x0), 0, int(x1), int(above)))
    caption = next((w for w in words(strip, scale=4)
                    if w["text"].strip().lower().startswith("shif")), None)
    if caption is None:
        return ""
    pad = _height(caption) * 0.5
    box = (int(caption["x1"]), int(max(0, caption["y0"] - pad)),
           int(strip.width), int(caption["y1"] + pad))
    if box[2] - box[0] < 8:
        return ""
    digits = re.sub(r"\D", "", _text(strip.crop(box), 7, whitelist="0123456789#",
                                     scale=6))
    return digits[:3]


def read_meta(found: list[dict], above: float, extra: str = "") -> dict:
    """Shift number, stage and region, from the header words and centre strip."""
    text = " ".join(w["text"] for w in found if w["y1"] < above) + " " + extra
    flat = " ".join(text.split())
    low = flat.lower()

    shift = ""
    found_shift = re.search(r"shift\s*#?\s*(\d{1,3})", low)
    if not found_shift and "shift" in low:
        # the word and its number are often read as separate fragments
        found_shift = re.search(r"#\s*(\d{1,3})", low)
    if found_shift:
        shift = found_shift.group(1)

    stage = ""
    for candidate in STAGES:
        if candidate in low.replace("-", " "):
            stage = candidate.title()
            break

    region = ""
    for token, label in REGIONS.items():
        if re.search(rf"\b{token}\b", low):
            region = label
            break

    map_label = ""
    found_map = re.search(r"map\s*(\d)\s*[-–—]?\s*(best\s*of\s*\d)?", low)
    if found_map:
        map_label = f"Map {found_map.group(1)}"
        if found_map.group(2):
            map_label += f" · {found_map.group(2).title()}"

    return {"shift": shift, "match_type": stage, "region": region,
            "map_label": map_label, "header_text": flat}


# The badge is gold on dark teal. Red-minus-blue is close to zero everywhere on
# the header bar and around 100 on the badge, which makes it a far better
# detector than reading the text: the pill outline and the low contrast defeat
# OCR often enough that on five shots it managed two.
# Measured at full resolution: on the badge side red-minus-blue clears 20 for
# 2.6-5.8% of the strip, and on the other side for exactly none of it.
BADGE_CHANNEL = 20
BADGE_FLOOR = 0.008

_BADGE_TEXT = re.compile(r"p\s*[il1|!]\s*c\s*[k<]", re.I)


def _badge_strip(row: list[dict], side: str, width: float,
                 pitch: float) -> tuple[int, int, int, int] | None:
    """
    The sliver between a team's name and its "BANS" caption, where the badge
    sits on every version of the graphic. Keeping it tight is the whole trick:
    widened to the full half of the image it takes in the ban portraits, which
    are gold-ish enough to swamp the test and busy enough to defeat the reading.
    """
    team = _team_words(row, side, width)
    if not team:
        return None
    caption = next((w for w in row if _is_bans(w)
                    and (w["x0"] < width / 2) == (side == "left")), None)
    if side == "left":
        x0 = max(w["x1"] for w in team)
        x1 = caption["x0"] if caption else x0 + 2.4 * pitch
    else:
        x1 = min(w["x0"] for w in team)
        x0 = caption["x1"] if caption else x1 - 2.4 * pitch
    if x1 - x0 < 20:
        return None
    top = min(w["y0"] for w in team)
    bottom = max(w["y1"] for w in team)
    pad = (bottom - top) * 0.55
    return (int(max(0, x0)), int(max(0, top - pad)),
            int(min(width, x1)), int(bottom + pad))


def _gold(crop: Image.Image) -> Image.Image:
    red, _green, blue = crop.convert("RGB").split()
    return ImageChops.subtract(red, blue)


def _pixels(image: Image.Image) -> list:
    getter = getattr(image, "get_flattened_data", None)
    return list(getter() if getter else image.getdata())


def _gold_fraction(crop: Image.Image) -> float:
    """Measured at full resolution: downsampling first averaged the thin gold
    strokes away and flattened a clean 30-to-1 margin into nothing."""
    data = _pixels(_gold(crop))
    if not data:
        return 0.0
    return sum(1 for v in data if v > BADGE_CHANNEL) / len(data)


def _badge_reads(crop: Image.Image) -> bool:
    mask = ImageOps.autocontrast(_gold(crop), cutoff=1)
    mask = mask.resize((mask.width * 4, mask.height * 4), Image.LANCZOS)
    mask = ImageOps.invert(mask.point(lambda v: 255 if v > 110 else 0))
    mask = ImageOps.expand(mask, border=25, fill=255)
    return bool(_BADGE_TEXT.search(_text(mask, 7, scale=1)))


def first_pick_side(image: Image.Image, row: list[dict], width: float,
                    pitch: float) -> str | None:
    """
    Read, not inferred.

    Two independent tests on the same sliver: how much gold is in it, and
    whether the gold spells something ending in "PICK". Gold is the stronger of
    the two -- across five graphics it came out around 0.03 on the badge side
    and 0.000 on the other, while OCR rendered the pill "ST ACK", "YST PIC K"
    and, twice, nothing at all.

    Either test can decide alone, but they must not contradict each other, and
    an unclear read returns None. The draft order hangs on this, and a coin flip
    dressed up as a reading is worse than an empty field for someone to fill.
    """
    if not row or not pitch:
        return None
    gold, reads = {}, {}
    for side in ("left", "right"):
        box = _badge_strip(row, side, width, pitch)
        if box is None:
            gold[side], reads[side] = 0.0, False
            continue
        crop = image.crop(box)
        gold[side] = _gold_fraction(crop)
        reads[side] = _badge_reads(crop)

    by_gold = None
    high, low = max(gold, key=gold.get), min(gold, key=gold.get)
    if high != low and gold[high] >= BADGE_FLOOR and gold[low] < gold[high] * 0.4:
        by_gold = high
    by_text = None
    if reads["left"] != reads["right"]:
        by_text = "left" if reads["left"] else "right"

    # gold decides when it is clear; the reading is only a fallback, since it
    # produced no answer at all on three of five graphics
    return by_gold or by_text


# ------------------------------------------------------------------ bans


def ban_boxes(found: list[dict], plan: dict | None) -> dict[str, list[tuple]]:
    """
    Where each team's ban squares are, anchored on their "BANS" caption and
    scaled by the pick pitch. The caption's own OCR box is not trustworthy for
    size -- it came back twice as tall on one side as the other in the same
    image -- but its position is fine, and the pitch is measured from six
    repeats.
    """
    if not plan:
        return {"left": [], "right": []}
    pitch = plan["pitch"]
    width, height = BAN_WIDTH * pitch, BAN_HEIGHT * pitch
    gap = BAN_GAP * pitch

    captions = [w for w in found if w["text"].strip().lower().rstrip(":") == "bans"]
    out: dict[str, list[tuple]] = {"left": [], "right": []}
    for caption in captions:
        side = "left" if caption["x0"] < sum(plan["panels"].values()) / 2 + pitch * 3 else "right"
        if out[side]:
            continue
        centre = _mid_y(caption)
        top, bottom = centre - height / 2, centre + height / 2
        if side == "left":  # squares sit inboard of the caption
            first = caption["x1"] + gap
            out[side] = [(first + i * width, top, first + (i + 1) * width, bottom)
                         for i in range(BANS_PER_TEAM)]
        else:
            last = caption["x0"] - gap
            out[side] = [(last - (BANS_PER_TEAM - i) * width, top,
                          last - (BANS_PER_TEAM - i - 1) * width, bottom)
                         for i in range(BANS_PER_TEAM)]
    return out


def ban_strip(image, boxes: dict[str, list[tuple]], side: str,
              margin: float = 0.12):
    """
    One picture of a team's whole ban area.

    The individual squares are placed by scaling off the column pitch, which is
    near enough to guide the eye and not near enough to crop cleanly -- the
    tiles came out with slivers of their neighbours in them. Since a person is
    naming these anyway, showing the strip whole is both more honest and easier
    to read than four almost-right thumbnails.
    """
    image = _as_image(image)
    squares = boxes.get(side) or []
    if not squares:
        return None
    x0 = min(b[0] for b in squares)
    x1 = max(b[2] for b in squares)
    y0 = min(b[1] for b in squares)
    y1 = max(b[3] for b in squares)
    pad_x, pad_y = (x1 - x0) * margin, (y1 - y0) * margin
    return image.crop((int(max(0, x0 - pad_x)), int(max(0, y0 - pad_y)),
                       int(min(image.width, x1 + pad_x)),
                       int(min(image.height, y1 + pad_y))))


def ban_crops(image, boxes: dict[str, list[tuple]] | None = None,
              found: list[dict] | None = None,
              plan: dict | None = None) -> dict[str, list]:
    """The ban portraits themselves, so a reviewer can name them by looking."""
    image = _as_image(image)
    if boxes is None:
        found = words(image) if found is None else found
        boxes = ban_boxes(found, plan)
    out: dict[str, list] = {}
    for side, squares in boxes.items():
        out[side] = [image.crop((int(max(0, x0)), int(max(0, y0)),
                                 int(min(image.width, x1)),
                                 int(min(image.height, y1))))
                     for x0, y0, x1, y1 in squares]
    return out


# ------------------------------------------------------------------ parse


def parse(source) -> dict:
    """Everything readable from one graphic, with nothing filled in by guess."""
    image = _as_image(source)
    found = words(image)
    picks = read_picks(image, found)
    top = picks["row"][0] or image.height
    plan = picks["geometry"]

    teams = read_teams(found, image.width, top)
    row = teams.pop("_title_row")
    result = read_meta(found, top, centre_strip(image, plan, top))
    result["shift"] = read_shift(image, plan, top) or result["shift"]
    result.update(teams)
    result.update({
        # the graphic is taken before the map is played, so the scoreboard is
        # 0-0 whenever it is legible at all; reading the stylised numerals
        # produced confident nonsense, so it is left to the reviewer
        "left_score": 0,
        "right_score": 0,
        "first_pick": first_pick_side(image, row, image.width,
                                     plan["pitch"] if plan else 0.0),
        "left_picks": picks["picks"]["left"],
        "right_picks": picks["picks"]["right"],
        "left_bans": [(None, None)] * BANS_PER_TEAM,
        "right_bans": [(None, None)] * BANS_PER_TEAM,
        "ban_boxes": ban_boxes(found, plan),
        "layout": {
            "pitch": round(plan["pitch"], 1) if plan else None,
            "panels": {k: round(v, 1) for k, v in plan["panels"].items()} if plan else None,
            "names_found": sum(1 for side in ("left", "right")
                               for hero, _ in picks["picks"][side] if hero),
        },
    })
    return result


def sides_for(first_side: str) -> list[dict]:
    """The twelve picks in draft order, each tagged with side and row position."""
    second = "right" if first_side == "left" else "left"
    seen = {"left": 0, "right": 0}
    order = []
    for who, number in PICK_SEQUENCE:
        side = first_side if who == "first" else second
        order.append({"number": number, "side": side, "slot": seen[side],
                      "phase": 1 if number <= 6 else 2})
        seen[side] += 1
    return order


# ------------------------------------------------------------------ storage

# A file, not a database. Drafts are hand-checked readings of a broadcast that
# is not archived anywhere else, so the store has to survive a redeploy, travel
# in a commit, and be reviewable in a diff -- none of which a SQLite file does
# well, and the last of which it cannot do at all. One JSON object per line
# gives all three: adding a night's matches appends a handful of lines, and
# correcting one draft changes exactly one.
DRAFT_FILE = os.environ.get("DRAFT_FILE") or str(HERE / "drafts.jsonl")

# Night Shift runs weekly on Wednesdays. Shift 54 was Wednesday 2 September
# 2026, which puts shift 1 on Wednesday 27 August 2025 -- 53 weeks, matching a
# tournament that has been running a little over a year. Dates are derived from
# this rather than stored blindly, but each record keeps its own date so that a
# skipped or moved week is a one-line correction and not a broken formula.
SHIFT_ANCHOR = (54, date(2026, 9, 2))


def date_for(shift) -> str:
    """The Wednesday a given shift ran on, as an ISO date. '' if not a number."""
    try:
        number = int(str(shift).strip())
    except (TypeError, ValueError):
        return ""
    anchor_number, anchor_date = SHIFT_ANCHOR
    return (anchor_date + timedelta(weeks=number - anchor_number)).isoformat()


def shift_number(shift) -> str:
    """
    Canonical form of a shift number.

    The graphic prints "SHIFT #01" and OCR sometimes returns "01" and sometimes
    "1"; stored as written, one night would split into two in every per-shift
    view. Anything non-numeric is left alone rather than discarded.
    """
    text = str(shift or "").strip()
    return str(int(text)) if text.isdigit() else text


def shift_for(when) -> str:
    """The shift number that ran on a given date, for reading a date back."""
    if isinstance(when, str):
        try:
            when = date.fromisoformat(when.strip())
        except ValueError:
            return ""
    anchor_number, anchor_date = SHIFT_ANCHOR
    return str(anchor_number + round((when - anchor_date).days / 7))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _identity(record: dict) -> str:
    """
    What makes two readings the same draft.

    The source filename is part of it on purpose. Teams and map alone would be
    the tidier key, but the map label is the field most often misread, and two
    maps of one pairing whose labels both came back blank would then silently
    overwrite each other. Keyed on the file as well, re-saving a screenshot
    corrects its row and a different screenshot always gets its own.
    """
    parts = [shift_number(record.get("shift")),
             str(record.get("left_team", "")).strip().casefold(),
             str(record.get("right_team", "")).strip().casefold(),
             str(record.get("map_label", "")).strip().casefold(),
             str(record.get("source", "")).strip().casefold()]
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]


def _order(record: dict) -> tuple:
    try:
        shift = int(str(record.get("shift", "")).strip())
    except (TypeError, ValueError):
        shift = 0
    return shift, str(record.get("map_label", "")), str(record.get("source", ""))


def load(path: str | None = None) -> list[dict]:
    """Every draft on file. A malformed line is skipped, not fatal."""
    target = Path(path or DRAFT_FILE)
    if not target.is_file():
        return []
    out = []
    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            out.append(record)
    return out


def write_all(records: list[dict], path: str | None = None) -> str:
    """Replace the file, via a temporary one so a crash cannot truncate it."""
    target = Path(path or DRAFT_FILE)
    target.parent.mkdir(parents=True, exist_ok=True)
    scratch = target.with_name(target.name + ".tmp")
    with scratch.open("w", encoding="utf-8") as handle:
        for record in sorted(records, key=_order):
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(scratch, target)
    return str(target)


def save(record: dict, path: str | None = None) -> str:
    """
    Write one reviewed draft, and return its id.

    Re-saving the same screenshot replaces its record rather than piling up
    duplicates, so fixing a typo is just saving again. The event date is filled
    in from the shift number when the caller has not set one; when the caller
    has, theirs stands.
    """
    entry = dict(record)
    entry["shift"] = shift_number(entry.get("shift"))
    entry["id"] = _identity(entry)
    entry.setdefault("date", date_for(entry.get("shift")))
    if not entry.get("date"):
        entry["date"] = date_for(entry.get("shift"))
    entry["recorded_at"] = _now()
    entry["picks"] = [_slim(p, "pick") for p in entry.get("picks") or ()]
    entry["bans"] = [_slim(b, "ban") for b in entry.get("bans") or ()]

    records = [r for r in load(path) if r.get("id") != entry["id"]]
    records.append(entry)
    write_all(records, path)
    return entry["id"]


def _slim(entry: dict, action: str) -> dict:
    """One pick or ban, keyed the same way however the caller labelled it."""
    def pick(*names, default=None):
        for name in names:
            if name in entry and entry[name] not in (None, ""):
                return entry[name]
        return default
    return {
        "ordinal": int(pick("ordinal", "number", "#", default=0) or 0),
        "phase": int(pick("phase", default=0) or 0),
        "action": pick("action", default=action),
        "side": pick("side", default=""),
        "team": pick("team", default=""),
        "hero": pick("hero", default=""),
    }


def drafts(path: str | None = None) -> list[dict]:
    """One summary row per draft, newest shift first."""
    rows = []
    for record in load(path):
        row = {k: v for k, v in record.items() if k not in ("picks", "bans")}
        row["picks"] = len(record.get("picks") or ())
        row["bans"] = len(record.get("bans") or ())
        rows.append(row)
    rows.sort(key=_order, reverse=True)
    return rows


def heroes_for(draft_id: str, path: str | None = None) -> list[dict]:
    for record in load(path):
        if record.get("id") == draft_id:
            entries = list(record.get("bans") or ()) + list(record.get("picks") or ())
            return sorted(entries, key=lambda e: (e.get("action") != "ban",
                                                  e.get("ordinal", 0)))
    return []


def delete(draft_id: str, path: str | None = None) -> None:
    records = load(path)
    write_all([r for r in records if r.get("id") != draft_id], path)


def merge(incoming: list[dict], path: str | None = None) -> tuple[int, int]:
    """
    Fold another file's drafts into this one. -> (added, replaced)

    Entering a night on one machine and the next on another is the normal way
    this gets used, so the two files have to be reconcilable. Where both hold
    the same draft the later reading wins, since the only reason to record one
    twice is to correct it.
    """
    existing = {r.get("id") or _identity(r): r for r in load(path)}
    added = replaced = 0
    for record in incoming:
        if not isinstance(record, dict):
            continue
        entry = dict(record)
        entry["id"] = entry.get("id") or _identity(entry)
        current = existing.get(entry["id"])
        if current is None:
            added += 1
        elif str(entry.get("recorded_at", "")) <= str(current.get("recorded_at", "")):
            continue
        else:
            replaced += 1
        existing[entry["id"]] = entry
    write_all(list(existing.values()), path)
    return added, replaced


def export_rows(path: str | None = None) -> list[dict]:
    """One flat row per hero, for a spreadsheet or pandas."""
    out = []
    for record in sorted(load(path), key=_order):
        head = {k: record.get(k) for k in
                ("shift", "date", "match_type", "map_label", "region",
                 "left_team", "right_team", "left_score", "right_score",
                 "first_pick", "source", "recorded_at")}
        for entry in (list(record.get("bans") or ())
                      + list(record.get("picks") or ())):
            out.append({**head, **entry})
    return out


def shift_summary(path: str | None = None) -> list[dict]:
    """How many drafts are on file for each night, most recent first."""
    nights: dict[str, dict] = {}
    for record in load(path):
        shift = shift_number(record.get("shift"))
        night = nights.setdefault(shift, {
            "shift": shift, "date": record.get("date") or date_for(shift),
            "drafts": 0, "teams": set()})
        night["drafts"] += 1
        night["teams"].update(t for t in (record.get("left_team"),
                                          record.get("right_team")) if t)
    rows = [{"shift": n["shift"], "date": n["date"], "drafts": n["drafts"],
             "teams": len(n["teams"])} for n in nights.values()]
    rows.sort(key=lambda r: int(r["shift"]) if r["shift"].isdigit() else 0,
              reverse=True)
    return rows


# ------------------------------------------------------------------ CLI


def _show(path: str) -> None:
    result = parse(path)
    layout = result["layout"]
    print(Path(path).name)
    print(f"  shift {result['shift'] or '?'} · {result['match_type'] or '?'} · "
          f"{result['region'] or '?'} · {result['map_label'] or '?'}")
    print(f"  {result['left_team'] or '?'} {result['left_score']}-"
          f"{result['right_score']} {result['right_team'] or '?'}")
    print(f"  first pick: {result['first_pick'] or 'unreadable'}")
    print(f"  pitch {layout['pitch']} · panels {layout['panels']} · "
          f"{layout['names_found']}/12 names")
    for side in ("left", "right"):
        named = [h or "?" for h, _ in result[f"{side}_picks"]]
        print(f"  {side:<5} picks: " + ", ".join(named))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    sub = parser.add_subparsers(dest="command", required=True)
    read_cmd = sub.add_parser("read", help="parse a graphic and print it")
    read_cmd.add_argument("images", nargs="+")
    sub.add_parser("list", help="list saved drafts")
    sub.add_parser("nights", help="how many drafts are on file per shift")
    drop = sub.add_parser("delete", help="remove a saved draft")
    drop.add_argument("draft_id")
    take = sub.add_parser("merge", help="fold another drafts file into this one")
    take.add_argument("other")
    when = sub.add_parser("date", help="the Wednesday a shift ran on")
    when.add_argument("shift")

    args = parser.parse_args(argv)
    if args.command == "read":
        for path in args.images:
            _show(path)
        return 0
    if args.command == "list":
        for row in drafts():
            print(f"{row['id']}  shift {str(row.get('shift') or '?'):<3} "
                  f"{row.get('date') or '?':<10} "
                  f"{row.get('left_team')} vs {row.get('right_team')}  "
                  f"({row['picks']} picks, {row['bans']} bans)")
        return 0
    if args.command == "nights":
        for row in shift_summary():
            print(f"shift {row['shift']:<3} {row['date']:<10} "
                  f"{row['drafts']:>2} drafts · {row['teams']} teams")
        return 0
    if args.command == "delete":
        delete(args.draft_id)
        print(f"deleted {args.draft_id}")
        return 0
    if args.command == "merge":
        added, replaced = merge(load(args.other))
        print(f"{added} added, {replaced} replaced -> {DRAFT_FILE}")
        return 0
    if args.command == "date":
        print(date_for(args.shift) or "not a shift number")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
