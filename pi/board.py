#!/usr/bin/env python3
"""Tube departure board for a Raspberry Pi driving an HDMI screen.

Draws the board with Pillow and writes it straight to the framebuffer
(/dev/fb0). No desktop, no browser. Live data from TfL's open API.

    python3 board.py               # run on the Pi (needs /dev/fb0)
    python3 board.py --png out.png # render one frame to a file, for testing anywhere

Settings live in settings.json next to this file. The portal (portal.py)
rewrites that file; the board notices within one refresh.
"""
import argparse
import json
import os
import sys
import time
import urllib.parse as up
import datetime as dt
from collections import Counter

import requests
from PIL import Image, ImageDraw, ImageFont

try:
    import screen
except Exception as e:                          # noqa: BLE001
    screen = None
    print("screen control unavailable:", e, file=sys.stderr, flush=True)

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(HERE, "settings.json")
TFL = "https://api.tfl.gov.uk"

DEFAULTS = {
    "line": "piccadilly",
    "station_id": "940GZZLUASL",
    "station_name": "Arsenal",
    "columns": [
        {"direction": "inbound", "label": "", "towards": ""},
        {"direction": "outbound", "label": "", "towards": ""},
    ],
    "rows": 5,
    "refresh_seconds": 30,
    "app_key": "",
    # Screen brightness, driven over the HDMI cable. Once the monitor is in the
    # frame its own buttons are unreachable, so this is the only way to change it.
    "brightness": 100,
    "brightness_dim": 30,
    "dim_enabled": True,
    "off_overnight": False,
    "day_from": "07:00",
    "dim_from": "21:00",
    "off_from": "00:00",
    "off_until": "06:00",
}

LINE_NAMES = {
    "bakerloo": "Bakerloo line", "central": "Central line", "circle": "Circle line",
    "district": "District line", "hammersmith-city": "Hammersmith & City line",
    "jubilee": "Jubilee line", "metropolitan": "Metropolitan line", "northern": "Northern line",
    "piccadilly": "Piccadilly line", "victoria": "Victoria line", "waterloo-city": "Waterloo & City line",
    "elizabeth": "Elizabeth line", "dlr": "DLR",
    "liberty": "Liberty line", "lioness": "Lioness line", "mildmay": "Mildmay line",
    "suffragette": "Suffragette line", "weaver": "Weaver line", "windrush": "Windrush line",
    # TfL split the Overground into the six lines above in November 2024 and no longer
    # answers to this id. Kept so an old settings.json still draws a name, not "London-Overground line".
    "london-overground": "Overground",
}
# What the roundel bar says. Every tube line is "UNDERGROUND"; the others carry
# their own network name, as their real roundels do.
NETWORK = {
    "dlr": "DLR",
    "london-overground": "OVERGROUND",
    "liberty": "OVERGROUND", "lioness": "OVERGROUND", "mildmay": "OVERGROUND",
    "suffragette": "OVERGROUND", "weaver": "OVERGROUND", "windrush": "OVERGROUND",
    "elizabeth": "ELIZABETH LINE",
}

LINE_COLOURS = {
    "bakerloo": (179, 99, 5), "central": (227, 32, 23), "circle": (255, 211, 0),
    "district": (0, 120, 42), "hammersmith-city": (243, 169, 187), "jubilee": (160, 165, 169),
    "metropolitan": (155, 0, 86), "northern": (120, 120, 120), "piccadilly": (0, 25, 168),
    "victoria": (0, 160, 226), "waterloo-city": (149, 205, 186), "elizabeth": (105, 80, 161),
    "dlr": (0, 164, 167),
    # each named Overground line has its own colour, not the old Overground orange
    "liberty": (93, 96, 97), "lioness": (250, 166, 26), "mildmay": (0, 119, 173),
    "suffragette": (91, 189, 114), "weaver": (130, 58, 98), "windrush": (237, 27, 0),
    "london-overground": (238, 124, 14),
}

BG = (6, 8, 12)
WHITE = (255, 255, 255)
DIM = (150, 156, 170)
ORANGE = (245, 166, 35)
GREEN = (72, 200, 110)
RED = (220, 36, 31)
RULE = (30, 34, 44)


# ---------------------------------------------------------------- settings

class Settings:
    def __init__(self):
        self.mtime = None
        self.data = dict(DEFAULTS)
        self.reload()

    def reload(self):
        try:
            m = os.path.getmtime(SETTINGS_PATH)
        except OSError:
            return False
        if m == self.mtime:
            return False
        try:
            with open(SETTINGS_PATH) as f:
                d = json.load(f)
            merged = dict(DEFAULTS)
            merged.update({k: v for k, v in d.items() if v not in (None, "")})
            self.data = merged
            self.mtime = m
            return True
        except (OSError, ValueError) as e:
            print("settings: could not read, keeping the old ones:", e, file=sys.stderr, flush=True)
            return False

    def __getitem__(self, k):
        return self.data[k]


# ---------------------------------------------------------------- TfL

def tidy_destination(name):
    # one tail only: stripping twice turns "Battersea Power Station Underground
    # Station" into "Battersea Power"
    for tail in (" Underground Station", " Rail Station", " DLR Station", " Station"):
        if name.endswith(tail):
            return name[: -len(tail)]
    return name


def tidy_towards(s):
    """'Heathrow via T4 Loop' -> 'Heathrow'; 'Cockfosters' -> 'Cockfosters'."""
    s = s.split(" via ")[0].strip()
    # TfL's way of saying it does not know; it is not a destination, so it must
    # never become a column heading or split the columns
    if s.lower() == "check front of train":
        return ""
    for tail in (" Terminal 4", " Terminal 5", " Terminals 2 & 3", " T4", " T5"):
        if s.endswith(tail):
            s = s[: -len(tail)]
    return s


COMPASS = ("northbound", "southbound", "eastbound", "westbound", "inner rail", "outer rail")


def heading(a):
    """'Westbound - Platform 2' -> 'Westbound'. Only a real direction counts: outside
    the deep tube the prefix is 'Platform Unknown', 'A' or 'Platform 4', which says
    nothing to a passenger and changes between refreshes."""
    h = (a.get("platformName") or "").split(" - ")[0].strip()
    return h.title() if h.lower() in COMPASS else ""


def row_text(a):
    """The destination, plus the branch when the branch is the fact that matters:
    two Northern line trains to Edgware leave from different platforms."""
    tow = a.get("towards") or ""
    dest = tidy_destination(a.get("destinationName") or "")
    if not dest:
        # some trains have no destination at all; the platform indicator shows the
        # towards text for those, which is usually "Check Front of Train"
        return tow or "?"
    if " via " in tow:
        via = tow.split(" via ", 1)[1].strip()
        # "Morden via Bank" is the whole point; "Heathrow Terminal 4 via T4 Loop"
        # only repeats the destination, so drop a branch the destination names
        flat = dest.lower().replace("terminal ", "t")
        words = [w for w in via.lower().split() if w != "loop"]
        if words and not any(w in flat for w in words):
            dest += " via " + via
    return dest


def dedupe(arrivals):
    """The DLR files one prediction per platform for the same train, and vehicleId is
    empty, so the same train would be drawn twice. Match on destination plus time.

    Never across a direction, though: the two DLR predictions carry no direction and a
    bare "Platform 1", so they still collapse, while two trains that say plainly they
    are going opposite ways are two trains. Without that, the Circle line's rails eat
    each other - both run to Hammersmith, and whenever the pair fell within the same
    few seconds one rail lost the train, and a rail could lose enough of them to
    empty its column."""
    seen, out = [], []
    for a in arrivals:
        k = a.get("destinationNaptanId") or a.get("destinationName", "")
        t = int(a.get("timeToStation", 0))
        h, dirn = heading(a), a.get("direction") or ""
        # a blank on either side is "not stated", which contradicts nothing
        if any(k == sk and abs(t - st) <= 5
               and not (h and sh and h != sh)
               and not (dirn and sd and dirn != sd)
               for sk, st, sh, sd in seen):
            continue
        seen.append((k, t, h, dirn))
        out.append(a)
    return out


def split_by(arrivals, key):
    """Group the arrivals by key(), for the columns. Returns the groups in a fixed
    order, or None if this key cannot carry the board.

    A train the key cannot name does not throw the split away - one train at
    "Platform Unknown" used to cost the whole Eastbound/Westbound split and leave the
    reader one mixed column. It is placed with the trains that already run to its
    destination, or that share its direction. If it can be placed by neither, then the
    split is refused after all: a train under the wrong heading sends someone to the
    wrong platform, which is worse than a board with no headings on it.
    """
    keys = [key(a) for a in arrivals]
    names = sorted({k for k in keys if k})  # sorted, so columns cannot swap sides
    if len(names) < 2:
        return None
    groups = {n: [a for a, k in zip(arrivals, keys) if k == n] for n in names}

    # which group a destination and a direction already belong to, and only where
    # the whole answer is one group - two candidates is not an answer
    by_dest, by_dir = {}, {}
    for n, mine in groups.items():
        for x in mine:
            dest = tidy_destination(x.get("destinationName") or "")
            if dest:
                by_dest.setdefault(dest, set()).add(n)
            if x.get("direction"):
                by_dir.setdefault(x["direction"], set()).add(n)

    for a, k in zip(arrivals, keys):
        if k:
            continue
        for table, v in ((by_dest, tidy_destination(a.get("destinationName") or "")),
                         (by_dir, a.get("direction"))):
            home = table.get(v) if v else None
            if home and len(home) == 1:
                groups[next(iter(home))].append(a)
                break
        else:
            return None
    for mine in groups.values():
        mine.sort(key=lambda x: x.get("timeToStation", 1e9))
    return [groups[n] for n in names]


def group(arrivals, columns):
    """Put every arrival in a column, and never drop one.

    TfL omits "direction" at every terminus, and sends it empty on much of the DLR,
    the Elizabeth line and the Overground, so filtering on it alone leaves a column
    reading "No trains reported" while trains are due. Fall back to the platform
    heading, then to a single full-width column.
    Returns [(configured column or None, arrivals)].
    """
    wanted = [c["direction"] for c in columns]
    # what each heading means at this stop, learnt from the arrivals that do carry a
    # direction, so a mixed stop keeps its columns on the sides the reader expects
    votes = {}
    for a in arrivals:
        h, dirn = heading(a), a.get("direction")
        if h and dirn in wanted:
            votes.setdefault(h, Counter())[dirn] += 1
    head_dir = {h: max(v.items(), key=lambda kv: (kv[1], kv[0]))[0] for h, v in votes.items()}

    buckets = {d: [] for d in wanted}
    homeless = []
    for a in arrivals:
        dirn = a.get("direction")
        if dirn not in buckets:
            dirn = head_dir.get(heading(a))
        (buckets[dirn] if dirn in buckets else homeless).append(a)
    if not arrivals or (not homeless and all(buckets[d] for d in wanted)):
        return [(c, buckets[c["direction"]]) for c in columns]

    # the direction split failed; regroup everything by the first key that splits it
    for key in (heading,
                lambda a: tidy_towards(a.get("towards", "") or ""),
                lambda a: tidy_destination(a.get("destinationName", "") or "")):
        parts = split_by(arrivals, key)
        if parts and 2 <= len(parts) <= max(2, len(columns)):
            return [(None, p) for p in parts]
    return [(None, list(arrivals))]  # nothing splits them: one column, drawn full width


def column_label(c, mine):
    """Headings a passenger can act on, most specific first. A value that only some of
    the trains share is a lie, so each step needs one value for the whole column."""
    heads = {h for h in map(heading, mine) if h}
    tows = {t for t in (tidy_towards(a.get("towards") or "") for a in mine) if t}
    dests = {d for d in (tidy_destination(a.get("destinationName") or "") for a in mine) if d}
    chain = [next(iter(heads)) if len(heads) == 1 else "",
             (c or {}).get("label", ""),
             ("Towards " + next(iter(tows))) if len(tows) == 1 else "",
             next(iter(dests)) if len(dests) == 1 else "",
             "Departures"]
    return [x.upper() for x in chain if x]


def tidy_reason(reason, line):
    """TfL's reason reads e.g. "Piccadilly Line: Severe delays due to an earlier
    signal failure at Bounds Green. London Buses, Great Northern ... are accepting
    tickets via any reasonable route." On a wall we want the cause, not the line
    name we already show, and not the ticket-acceptance boilerplate."""
    r = (reason or "").strip()
    if not r:
        return ""
    # drop the "<Line> Line: " prefix
    if ":" in r[:40]:
        r = r.split(":", 1)[1].strip()
    # keep sentences until the boilerplate starts
    keep = []
    for sentence in r.replace("\n", " ").split(". "):
        s = sentence.strip()
        if not s:
            continue
        low = s.lower()
        if "accepting tickets" in low or "valid on" in low or "reasonable route" in low:
            break
        keep.append(s)
        if len(". ".join(keep)) > 110:
            break
    out = ". ".join(keep).rstrip(" .")
    # The severity is already on the line in colour, so drop that word - but keep
    # the "due to", which is what makes the line read as a sentence after the dash.
    for lead in ("Severe delays ", "Minor delays ", "Delays ", "Part suspended ",
                 "Suspended ", "Part closure ", "Part closed ", "Reduced service "):
        if out.lower().startswith(lead.lower()):
            out = out[len(lead):]
            break
    return out


def fetch(settings):
    """Returns (columns, status_text, status_ok). Each column: label, towards, rows[(dest, secs)]."""
    q = {"app_key": settings["app_key"]} if settings["app_key"] else {}
    # quote both: the settings file can hold anything, and a stray / or ? would
    # rewrite the path or the query instead of failing
    line = up.quote(settings["line"], safe="")
    stop = up.quote(settings["station_id"], safe="")
    arrivals = requests.get(f"{TFL}/Line/{line}/Arrivals/{stop}", params=q, timeout=10)
    arrivals.raise_for_status()
    arrivals = arrivals.json()
    if not isinstance(arrivals, list):
        raise ValueError("arrivals: unexpected response")
    arrivals.sort(key=lambda a: a.get("timeToStation", 1e9))
    arrivals = dedupe(arrivals)

    status_text, status_ok, status_why = None, False, ""
    try:
        r = requests.get(f"{TFL}/Line/{line}/Status", params=q, timeout=10)
        r.raise_for_status()
        sts = [s for s in (r.json()[0].get("lineStatuses") or []) if s.get("statusSeverityDescription")]
        if sts:
            # the array is not ordered by severity, and severity 11 and up (Part Closed,
            # Not Running) sit above 10 Good Service, so rank disruption before the number
            worst = min(sts, key=lambda s: (1 if s.get("statusSeverity", 10) in (10, 18) else 0,
                                            s.get("statusSeverity", 10)))
            status_text, status_ok = worst["statusSeverityDescription"], True
        status_why = tidy_reason(worst.get("reason") or "", line)
    except Exception as e:
        print("status fetch failed:", e, file=sys.stderr, flush=True)

    groups = group(arrivals, settings["columns"])
    chains = [column_label(c, mine) for c, mine in groups]
    labels = [ch[0] for ch in chains]
    for i, ch in enumerate(chains):
        # two columns under one heading tell the reader nothing: take the next step
        if labels.count(labels[i]) > 1:
            labels[i] = next((x for x in ch[ch.index(labels[i]) + 1:] if x not in labels), labels[i])

    cols = []
    for (c, mine), label in zip(groups, labels):
        tows = {t for t in (tidy_towards(a.get("towards") or "") for a in mine) if t}
        towards = (c or {}).get("towards") or (next(iter(tows)) if len(tows) == 1 else "")
        if "TOWARDS" in label:
            towards = ""  # already in the heading
        rows = [(row_text(a), int(a.get("timeToStation", 0))) for a in mine]
        cols.append({"label": label, "towards": towards, "rows": rows[: settings["rows"]]})
    return cols, status_text, status_ok, status_why


def explain(settings):
    """Say what TfL answers for the configured station and what the board makes of it.

    The first thing to run when a direction is missing from the screen: it separates
    "TfL is not telling us about those trains" from "we were told and mislaid them",
    and those have very different fixes."""
    q = {"app_key": settings["app_key"]} if settings["app_key"] else {}
    line = up.quote(settings["line"], safe="")
    stop = up.quote(settings["station_id"], safe="")
    url = f"{TFL}/Line/{line}/Arrivals/{stop}"
    print(f'{settings["station_name"]}  [{settings["station_id"]}]  {settings["line"]} line')
    print(url + "\n")

    r = requests.get(url, params=q, timeout=10)
    r.raise_for_status()
    raw = r.json()
    if not isinstance(raw, list):
        raise ValueError("arrivals: unexpected response")
    print(f"TfL returned {len(raw)} prediction(s)")
    tally = Counter((a.get("platformName") or "(no platform)",
                     a.get("direction") or "(no direction)") for a in raw)
    for (plat, dirn), n in sorted(tally.items()):
        print(f"  {n:3d}  {plat:<26}  direction={dirn}")

    raw.sort(key=lambda a: a.get("timeToStation", 1e9))
    lost = len(raw) - len(dedupe(raw))
    if lost:
        print(f"\n  {lost} of those are duplicate predictions for the same train")

    cols, _, _, _ = fetch(settings)
    print(f"\nThe board draws {len(cols)} column(s):")
    for c in cols:
        print("  " + c["label"] + (f'  towards {c["towards"]}' if c["towards"] else ""))
        for dest, secs in c["rows"]:
            print(f'      {label_mins(secs):>6}  {dest}')

    if len(cols) < 2:
        print("\nOne column means TfL reported trains going one way only. At a terminus")
        print("that is the truth. Anywhere else, check the station id above: a station")
        print("that is one name on the map can be two stop points at TfL, and only one")
        print("of them carries both directions. Search it again in the portal and pick")
        print("the other result, or put the id straight into settings.json.")


# ---------------------------------------------------------------- drawing

_font_cache = {}
# DejaVu, for its three real weights. Hammersmith One (the closest freely-licensed
# face to TfL's proprietary Johnston) was tried and dropped: one weight only, so
# the board lost its light/regular/bold separation.
FONT_CANDIDATES = {
    "regular": ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "/System/Library/Fonts/HelveticaNeue.ttc"],
    "bold": ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
             ("/System/Library/Fonts/HelveticaNeue.ttc", 1)],
    "light": ["/usr/share/fonts/truetype/dejavu/DejaVuSans-ExtraLight.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              ("/System/Library/Fonts/HelveticaNeue.ttc", 7)],
}


def font(kind, size):
    size = max(8, int(size))
    key = (kind, size)
    if key in _font_cache:
        return _font_cache[key]
    f = None
    for cand in FONT_CANDIDATES[kind]:
        path, idx = (cand, 0) if isinstance(cand, str) else cand
        if os.path.exists(path):
            try:
                f = ImageFont.truetype(path, size, index=idx)
                break
            except OSError:
                continue
    if f is None:
        f = ImageFont.load_default()
    _font_cache[key] = f
    return f


def text_w(d, s, f):
    return d.textlength(s, font=f)


def label_mins(secs):
    m = round(secs / 60)
    return "due" if m <= 0 else f"{m} min"


def ago(then, now):
    """How long since the last good fetch, in words. This is the honest health
    line on the board: if TfL goes quiet the number grows and keeps growing,
    where a clock time just sits there looking plausible."""
    if then is None:
        return "never"
    secs = int((now - then).total_seconds())
    if secs < 0:
        return "just now"
    if secs < 10:
        return "just now"
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


def clip(d, text, fnt, room):
    """Trim `text` with an ellipsis until it fits in `room` pixels. TfL's reasons
    are free text with no length limit - today's longest raw reason is 204
    characters - so nothing on this line may assume it fits."""
    if room <= 0:
        return ""
    if d.textlength(text, font=fnt) <= room:
        return text
    while text and d.textlength(text + "...", font=fnt) > room:
        text = text[:-1].rstrip() if " " not in text else text.rsplit(" ", 1)[0]
    return (text + "...") if text else ""


def paste_roundel(img, cx, cy, r, bar_colour, scale=3, label=""):
    """The Underground roundel: a red ring with a coloured bar across it.
    Against the ring's outer diameter D: bar width 1.05 D, bar height 0.22 D,
    ring thickness 0.17 D. Drawn big and shrunk, because PIL draws hard-edged
    circles. This is a drawing to those ratios, not TfL's own artwork file."""
    D = 2 * r
    half_w, half_h = 1.05 * D / 2, 0.22 * D / 2
    ring = max(1, round(0.17 * D * scale))
    pad_px = 4
    w = round((2 * half_w) * scale) + pad_px * 2
    h = round(D * scale) + pad_px * 2
    big = Image.new("RGB", (w, h), BG)
    bd = ImageDraw.Draw(big)
    ox, oy = w / 2, h / 2
    rs = r * scale
    bd.ellipse([ox - rs + ring / 2, oy - rs + ring / 2, ox + rs - ring / 2, oy + rs - ring / 2],
               outline=RED, width=ring)
    bd.rectangle([ox - half_w * scale, oy - half_h * scale,
                  ox + half_w * scale, oy + half_h * scale], fill=bar_colour)
    if label:
        size = max(6, int(half_h * 2 * scale * 0.80))
        f = font("bold", size)
        while bd.textlength(label, font=f) > (half_w * 2 * scale) * 0.88 and size > 6:
            size -= 2
            f = font("bold", size)
        bd.text((ox, oy), label, font=f, fill=WHITE, anchor="mm")
    small = big.resize((round(w / scale), round(h / scale)), Image.LANCZOS)
    img.paste(small, (round(cx - small.width / 2), round(cy - small.height / 2)))


def render(W, H, settings, cols, status_text, status_ok, status_why, now, updated, live,
           ss=2):
    """Draw the board. Everything is a fraction of the width, so the whole frame
    is drawn at `ss` times size and box-reduced back down. PIL draws hard-edged
    shapes; a 2x reduction is an exact 2x2 average, which is real antialiasing
    for every circle and diagonal on the screen, not just the ones we remembered.
    ss=1 skips it, for a slow machine."""
    if ss > 1:
        big = render(W * ss, H * ss, settings, cols, status_text, status_ok,
                     status_why, now, updated, live, ss=1)
        return big.reduce(ss)
    u = W / 100.0
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    line = settings["line"]
    line_colour = LINE_COLOURS.get(line, (0, 25, 168))
    line_name = LINE_NAMES.get(line, line.title() + " line")

    pad = 2.5 * u
    # --- header: roundel, line name, station; clock on the right
    # The roundel stands the full height of the line name plus the station line,
    # so it reads as the mark it is rather than a bullet point. Drawn to TfL's
    # published proportions and supersampled: PIL's circles are jagged at this
    # size, and a ragged roundel is the first thing a Londoner would notice.
    r = 3.6 * u
    cx, cy = pad + r * 1.05, pad + 3.1 * u
    # The bar says what the real roundel outside a station says: the network, not
    # the line. The line name lives in the heading beside it, so nothing repeats.
    paste_roundel(img, cx, cy, r, line_colour, label=NETWORK.get(line, "UNDERGROUND"))
    tx = cx + r * 1.05 + 1.4 * u
    d.text((tx, pad + 0.4 * u), line_name.upper(), font=font("regular", 3.2 * u), fill=WHITE)
    d.text((tx, pad + 3.9 * u), f"From {settings['station_name']}", font=font("light", 2.0 * u), fill=DIM)
    clock = now.strftime("%H:%M")
    fc = font("light", 5.4 * u)
    d.text((W - pad, cy), clock, font=fc, fill=WHITE, anchor="rm")
    rule_y = pad + 7.3 * u
    d.rectangle([pad, rule_y, W - pad, rule_y + 0.22 * u], fill=line_colour)

    # --- footer
    foot_rule = H - pad - 4.9 * u
    d.rectangle([pad, foot_rule, W - pad, foot_rule + 1], fill=RULE)
    # Centre the status in the strip between the rule and the bottom of the
    # screen rather than hanging it off the rule: on a wall this line is read
    # from across the room, so it gets room around it.
    fs = font("regular", 1.9 * u)
    # Two lines in the footer strip now: the status, and under it when we last
    # heard from TfL. Split the strip between them.
    # One line, centred between the rule and the bottom of the glass: status on
    # the left, when we last heard from TfL on the right. From a sofa the eye
    # measures to the edge of the screen, not to the text margin.
    fy = uy = (foot_rule + H) / 2
    upd = "Updated " + ago(updated, now)
    fupd = font("regular", 1.35 * u)
    d.text((W - pad, uy), upd, font=fupd, fill=DIM, anchor="rm")
    # Everything on the left of this line has to stop before the timestamp.
    right_edge = W - pad - text_w(d, upd, fupd) - 2.0 * u
    x = pad
    d.text((x, fy), "Status:", font=fs, fill=DIM, anchor="lm")
    x += text_w(d, "Status:", fs) + 0.8 * u
    if not live and updated is None:
        # Cold boot: the Pi is up before the network is, so the first fetch always
        # fails. There is no last update to show, and saying there is reads as a
        # fault to anyone walking past. Say what is actually happening instead.
        d.text((x, fy), clip(d, "Starting up, waiting for Transport for London", fs, right_edge - x), font=fs, fill=DIM, anchor="lm")
    elif not live:
        d.text((x, fy), clip(d, "No live data, showing the last update", fs, right_edge - x), font=fs, fill=ORANGE, anchor="lm")
    elif not status_ok:
        # a green tick we never checked is worse than saying we do not know
        d.text((x, fy), clip(d, "Service status unknown", font("bold", 1.9 * u), right_edge - x), font=font("bold", 1.9 * u), fill=ORANGE, anchor="lm")
    else:
        good = status_text.lower() in ("good service", "no issues")
        col = GREEN if good else ORANGE
        # The tick and the bang are drawn, not typed: a font without the glyph
        # would put an empty box on the wall and nobody would know why.
        r = 1.25 * u
        cy = fy
        d.ellipse([x, cy - r, x + 2 * r, cy + r], outline=col, width=max(1, round(0.17 * u)))
        if good:
            d.line([(x + 0.52 * r, cy + 0.05 * r), (x + 0.88 * r, cy + 0.55 * r),
                    (x + 1.5 * r, cy - 0.52 * r)], fill=col, width=max(1, round(0.19 * u)),
                   joint="curve")
        else:
            d.line([(x + r, cy - 0.52 * r), (x + r, cy + 0.12 * r)], fill=col, width=max(1, round(0.19 * u)))
            d.ellipse([x + r - 0.11 * u, cy + 0.42 * r, x + r + 0.11 * u, cy + 0.42 * r + 0.22 * u], fill=col)
        x2 = x + 2 * r + 0.7 * u
        d.text((x2, fy), status_text, font=font("bold", 1.9 * u), fill=col, anchor="lm")
        # why, in the reader's own words, trimmed to what fits on the line
        if status_why:
            x2 += text_w(d, status_text, font("bold", 1.9 * u)) + 0.7 * u
            why = clip(d, "- " + status_why, fs, right_edge - x2)
            if why and why != "-...":
                d.text((x2, fy), why, font=fs, fill=DIM, anchor="lm")

    # --- two columns
    top = rule_y + 1.8 * u
    # A wide channel each side of the divider: the minutes in the left column and
    # the destination in the right must not read as one line from across a room.
    gap = 7.0 * u
    n = max(1, len(cols))
    col_w = (W - 2 * pad - gap * (n - 1)) / n
    rows_n = max(1, settings["rows"])
    rows_top = top + 3.2 * u
    step = (foot_rule - 1.2 * u - rows_top) / rows_n
    for i, c in enumerate(cols):
        x0 = pad + i * (col_w + gap)
        if i:
            xd = x0 - gap / 2
            d.rectangle([xd, top, xd + 1, foot_rule - 1.5 * u], fill=RULE)
        fh = font("bold", 2.6 * u)
        d.text((x0, top), c["label"], font=fh, fill=WHITE)
        if c["towards"]:
            d.text((x0 + text_w(d, c["label"], fh) + 1.0 * u, top + 0.35 * u),
                   f"towards {c['towards']}", font=font("light", 1.7 * u), fill=DIM)
        rows = c["rows"]
        if not rows:
            msg = "No trains reported" if updated else ""
            if msg:
                d.text((x0, rows_top + 0.5 * u), msg, font=font("light", 1.8 * u), fill=DIM)
            continue
        dot_x = x0 + 0.5 * u
        fd, fm = font("regular", 2.7 * u), font("regular", 2.7 * u)
        for j, (dest, secs) in enumerate(rows):
            yc = rows_top + step * j + step / 2
            if j < len(rows) - 1:
                d.rectangle([dot_x - 1, yc, dot_x + 1, yc + step], fill=(0, 40, 140))
            rr = 0.62 * u
            d.ellipse([dot_x - rr, yc - rr, dot_x + rr, yc + rr], fill=line_colour if line != "northern" else WHITE)
            d.text((dot_x + 1.9 * u, yc), dest, font=fd, fill=WHITE, anchor="lm")
            m = label_mins(secs)
            d.text((x0 + col_w, yc), m, font=fm, fill=ORANGE, anchor="rm")
    return img


# ---------------------------------------------------------------- framebuffer

class Framebuffer:
    """The screen, as raw pixels. Waits for it to appear: at boot this service can
    start before the graphics driver has made /dev/fb0."""

    def __init__(self, dev="/dev/fb0", wait_seconds=60):
        base = "/sys/class/graphics/" + os.path.basename(dev)
        deadline = time.monotonic() + wait_seconds
        while True:
            try:
                w, h = (int(v) for v in open(base + "/virtual_size").read().split(","))
                self.bpp = int(open(base + "/bits_per_pixel").read())
                self.stride = int(open(base + "/stride").read())
                self.f = open(dev, "rb+", buffering=0)
                break
            except OSError as e:
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"{dev} never appeared: {e}")
                print(f"waiting for {dev} ...", flush=True)
                time.sleep(2)
        self.w, self.h = w, h
        self.row = self.w * (4 if self.bpp == 32 else 2)
        if self.bpp not in (32, 16):
            raise RuntimeError(f"unsupported framebuffer depth {self.bpp}; "
                               f"set framebuffer_depth=32 in config.txt")
        if self.bpp == 16:
            # Pillow has no packer for 16-bit raw, so we pack RGB565 ourselves.
            try:
                import numpy  # noqa: F401
            except ImportError:
                raise RuntimeError("16-bit screen needs numpy: sudo apt install python3-numpy "
                                   "(or set framebuffer_depth=32 in config.txt)")
        print(f"framebuffer {w}x{h} {self.bpp}bpp stride {self.stride}", flush=True)

    def _pack(self, img):
        if self.bpp == 32:
            return img.convert("RGBA").tobytes("raw", "BGRA")
        # 16bpp: RGB565, little endian. This is not a rare fallback - the vc4
        # driver's framebuffer emulation picks 16-bit on a Pi 3, and neither
        # config.txt nor a -32 on the video= line overrides it. Verified on the
        # real board 2026-09-14: 1920x1080 at 16bpp. So this is THE path here.
        import numpy as np
        a = np.asarray(img.convert("RGB"), dtype=np.uint16)
        v = ((a[:, :, 0] >> 3) << 11) | ((a[:, :, 1] >> 2) << 5) | (a[:, :, 2] >> 3)
        return v.astype("<u2").tobytes()

    def show(self, img):
        raw = self._pack(img)
        if self.stride != self.row:  # pad each line out to the stride
            pad = b"\0" * (self.stride - self.row)
            raw = b"".join(raw[i:i + self.row] + pad for i in range(0, len(raw), self.row))
        self.f.seek(0)
        self.f.write(raw)


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--png", help="render one frame to this file and exit")
    ap.add_argument("--size", default="1920x1080", help="frame size for --png")
    ap.add_argument("--explain", action="store_true",
                    help="print what TfL returns for this station and how it is split "
                         "into columns, then exit")
    args = ap.parse_args()

    settings = Settings()
    if args.explain:
        explain(settings)
        return
    if args.png:
        W, H = (int(v) for v in args.size.split("x"))
        cols, status, status_ok, status_why = fetch(settings)
        now = dt.datetime.now()
        render(W, H, settings, cols, status, status_ok, status_why, now, now, True).save(args.png)
        print("wrote", args.png, flush=True)
        return

    fb = Framebuffer()
    if screen:
        # Talk to the monitor once at startup so a restart re-asserts whatever the
        # schedule says, even if someone poked the buttons before it was framed.
        for msg in screen.apply(settings.data, dt.datetime.now(), force=True):
            print("screen:", msg, flush=True)
    last_screen = 0.0
    cols, status, status_ok, status_why, updated, live = [], None, False, "", None, False
    # draw once before the first fetch: a blank wall screen reads as a dead unit, and
    # with no WiFi the first request can hold for its full ten seconds
    fb.show(render(fb.w, fb.h, settings, cols, status, status_ok, status_why, dt.datetime.now(), updated, live))
    last_fetch = 0.0
    failures = 0
    draw_failures = 0
    while True:
        changed_settings = settings.reload()
        t = time.time()
        # Nudge the screen every 30 s, and at once if the settings just changed.
        if screen and (changed_settings or t - last_screen >= 30):
            last_screen = t
            for msg in screen.apply(settings.data, dt.datetime.now(),
                                    force=changed_settings):
                print("screen:", msg, flush=True)
        if t - last_fetch >= settings["refresh_seconds"] or not cols:
            last_fetch = t
            try:
                cols, status, status_ok, status_why = fetch(settings)
                updated, live, failures = dt.datetime.now(), True, 0
            except Exception as e:
                failures += 1
                print("fetch failed:", e, file=sys.stderr, flush=True)
                # keep showing the last board; after ~3 minutes of failures say so
                if failures >= max(1, 180 // settings["refresh_seconds"]):
                    live = False
        try:
            fb.show(render(fb.w, fb.h, settings, cols, status, status_ok, status_why, dt.datetime.now(), updated, live))
            draw_failures = 0
        except Exception as e:
            draw_failures += 1
            print(f"draw failed ({draw_failures}):", e, file=sys.stderr, flush=True)
            # A black screen with a healthy-looking service is the worst outcome.
            # Bail out and let systemd restart us; if it is permanent the journal says why.
            if draw_failures >= 5:
                print("giving up on the screen, restarting", file=sys.stderr, flush=True)
                raise SystemExit(1)
        # Redraw every 10 s. The clock only needs a minute, but the "updated Xs
        # ago" line has to keep up or it is quietly lying, and a frame costs
        # about a third of a second on a Pi 3.
        time.sleep(max(1.0, min(10.0,
                                60.0 - dt.datetime.now().second,
                                settings["refresh_seconds"] - (time.time() - last_fetch))))


if __name__ == "__main__":
    main()
