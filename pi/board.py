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
    empty, so the same train would be drawn twice. Match on destination plus time."""
    seen, out = [], []
    for a in arrivals:
        k = a.get("destinationNaptanId") or a.get("destinationName", "")
        t = int(a.get("timeToStation", 0))
        if any(k == sk and abs(t - st) <= 5 for sk, st in seen):
            continue
        seen.append((k, t))
        out.append(a)
    return out


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
        keys = [key(a) for a in arrivals]
        if not all(keys):
            continue  # a key that some arrivals lack would drop those trains
        names = sorted(set(keys))  # sorted, so the columns cannot swap sides on a refresh
        if 2 <= len(names) <= max(2, len(columns)):
            return [(None, [a for a, k in zip(arrivals, keys) if k == n]) for n in names]
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
    # the severity word is already on the line in colour; do not say it twice
    for lead in ("Severe delays due to ", "Minor delays due to ", "Delays due to ",
                 "Part suspended due to ", "Suspended due to ", "Part closure due to "):
        if out.lower().startswith(lead.lower()):
            out = out[len(lead):]
            break
    return out[:1].upper() + out[1:] if out else ""


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


# ---------------------------------------------------------------- drawing

_font_cache = {}
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


def paste_roundel(img, cx, cy, r, bar_colour, scale=4):
    """The Underground roundel: a red ring with a coloured bar across it.
    TfL's proportions, against the ring's outer diameter D: bar width 1.25 D,
    bar height 0.275 D, ring thickness 0.15 D. Drawn big and shrunk, because
    PIL draws hard-edged circles."""
    D = 2 * r
    half_w, half_h = 1.25 * D / 2, 0.275 * D / 2
    ring = max(1, round(0.15 * D * scale))
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
    small = big.resize((round(w / scale), round(h / scale)), Image.LANCZOS)
    img.paste(small, (round(cx - small.width / 2), round(cy - small.height / 2)))


def render(W, H, settings, cols, status_text, status_ok, status_why, now, updated, live):
    """Same proportions as the web page: everything is a fraction of the width."""
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
    r = 3.0 * u
    cx, cy = pad + r * 1.25, pad + 2.8 * u
    paste_roundel(img, cx, cy, r, line_colour)
    tx = cx + r * 1.25 + 1.4 * u
    d.text((tx, pad - 0.2 * u), line_name.upper(), font=font("regular", 3.2 * u), fill=WHITE)
    d.text((tx, pad + 3.1 * u), f"From {settings['station_name']}", font=font("light", 2.2 * u), fill=DIM)
    clock = now.strftime("%H:%M")
    fc = font("light", 5.4 * u)
    d.text((W - pad, cy), clock, font=fc, fill=WHITE, anchor="rm")
    rule_y = pad + 6.6 * u
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
    # Centred between the rule and the bottom of the glass, not the text margin:
    # from a sofa the eye measures to the edge of the screen.
    mid = (foot_rule + H) / 2
    fy = mid - 0.82 * u
    uy = mid + 1.09 * u
    upd = f"Last updated: {updated.strftime('%H:%M') if updated else '--:--'}"
    d.text((pad, uy), upd, font=font("regular", 1.35 * u), fill=DIM, anchor="lm")
    x = pad
    d.text((x, fy), "Status:", font=fs, fill=DIM, anchor="lm")
    x += text_w(d, "Status:", fs) + 0.8 * u
    if not live and updated is None:
        # Cold boot: the Pi is up before the network is, so the first fetch always
        # fails. There is no last update to show, and saying there is reads as a
        # fault to anyone walking past. Say what is actually happening instead.
        d.text((x, fy), "Starting up, waiting for Transport for London", font=fs, fill=DIM, anchor="lm")
    elif not live:
        d.text((x, fy), "No live data, showing the last update", font=fs, fill=ORANGE, anchor="lm")
    elif not status_ok:
        # a green tick we never checked is worse than saying we do not know
        d.text((x, fy), "Service status unknown", font=font("bold", 1.9 * u), fill=ORANGE, anchor="lm")
    else:
        good = status_text.lower() in ("good service", "no issues")
        col = GREEN if good else ORANGE
        # The tick and the bang are drawn, not typed: a font without the glyph
        # would put an empty box on the wall and nobody would know why.
        r = 0.9 * u
        cy = fy
        d.ellipse([x, cy - r, x + 2 * r, cy + r], outline=col, width=max(1, int(0.13 * u)))
        if good:
            d.line([(x + 0.55 * r, cy + 0.05 * r), (x + 0.9 * r, cy + 0.55 * r),
                    (x + 1.5 * r, cy - 0.5 * r)], fill=col, width=max(1, int(0.15 * u)))
        else:
            d.line([(x + r, cy - 0.5 * r), (x + r, cy + 0.15 * r)], fill=col, width=max(1, int(0.15 * u)))
            d.line([(x + r, cy + 0.45 * r), (x + r, cy + 0.5 * r)], fill=col, width=max(1, int(0.15 * u)))
        x2 = x + 2 * r + 0.7 * u
        d.text((x2, fy), status_text, font=font("bold", 1.9 * u), fill=col, anchor="lm")
        # why, in the reader's own words, trimmed to what fits on the line
        if status_why:
            x2 += text_w(d, status_text, font("bold", 1.9 * u)) + 1.0 * u
            why = status_why
            room = (W - pad) - x2
            while why and text_w(d, why + "...", fs) > room:
                why = why.rsplit(" ", 1)[0]
            if why:
                d.text((x2, fy), why + ("..." if why != status_why else ""),
                       font=fs, fill=DIM, anchor="lm")

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
        fd, fm = font("regular", 2.0 * u), font("regular", 2.0 * u)
        for j, (dest, secs) in enumerate(rows):
            yc = rows_top + step * j + step / 2
            if j < len(rows) - 1:
                d.rectangle([dot_x - 1, yc, dot_x + 1, yc + step], fill=(0, 40, 140))
            rr = 0.45 * u
            d.ellipse([dot_x - rr, yc - rr, dot_x + rr, yc + rr], fill=line_colour if line != "northern" else WHITE)
            d.text((dot_x + 1.6 * u, yc - 1.15 * u), dest, font=fd, fill=WHITE)
            m = label_mins(secs)
            d.text((x0 + col_w - text_w(d, m, fm), yc - 1.15 * u), m, font=fm, fill=ORANGE)
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
    args = ap.parse_args()

    settings = Settings()
    if args.png:
        W, H = (int(v) for v in args.size.split("x"))
        cols, status, status_ok, status_why = fetch(settings)
        now = dt.datetime.now()
        render(W, H, settings, cols, status, status_ok, status_why, now, now, True).save(args.png)
        print("wrote", args.png, flush=True)
        return

    fb = Framebuffer()
    cols, status, status_ok, status_why, updated, live = [], None, False, "", None, False
    # draw once before the first fetch: a blank wall screen reads as a dead unit, and
    # with no WiFi the first request can hold for its full ten seconds
    fb.show(render(fb.w, fb.h, settings, cols, status, status_ok, status_why, dt.datetime.now(), updated, live))
    last_fetch = 0.0
    failures = 0
    draw_failures = 0
    while True:
        settings.reload()
        t = time.time()
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
        # redraw once a minute for the clock, sooner if a fetch is due
        time.sleep(max(1.0, min(60.0 - dt.datetime.now().second, settings["refresh_seconds"] - (time.time() - last_fetch))))


if __name__ == "__main__":
    main()
