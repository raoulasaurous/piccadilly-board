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
    "elizabeth": "Elizabeth line", "dlr": "DLR", "london-overground": "Overground",
}
LINE_COLOURS = {
    "bakerloo": (179, 99, 5), "central": (227, 32, 23), "circle": (255, 211, 0),
    "district": (0, 120, 42), "hammersmith-city": (243, 169, 187), "jubilee": (160, 165, 169),
    "metropolitan": (155, 0, 86), "northern": (120, 120, 120), "piccadilly": (0, 25, 168),
    "victoria": (0, 160, 226), "waterloo-city": (149, 205, 186), "elizabeth": (105, 80, 161),
    "dlr": (0, 164, 167), "london-overground": (238, 124, 14),
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
            print("settings: could not read, keeping the old ones:", e, file=sys.stderr)
            return False

    def __getitem__(self, k):
        return self.data[k]


# ---------------------------------------------------------------- TfL

def tidy_destination(name):
    for tail in (" Underground Station", " Rail Station", " DLR Station", " Station"):
        if name.endswith(tail):
            name = name[: -len(tail)]
    return name


def tidy_towards(s):
    """'Heathrow via T4 Loop' -> 'Heathrow'; 'Cockfosters' -> 'Cockfosters'."""
    s = s.split(" via ")[0].strip()
    for tail in (" Terminal 4", " Terminal 5", " Terminals 2 & 3", " T4", " T5"):
        if s.endswith(tail):
            s = s[: -len(tail)]
    return s


def fetch(settings):
    """Returns (columns, status_text, ok). Each column: label, towards, rows[(dest, secs)]."""
    q = {"app_key": settings["app_key"]} if settings["app_key"] else {}
    line, stop = settings["line"], settings["station_id"]
    arrivals = requests.get(f"{TFL}/Line/{line}/Arrivals/{stop}", params=q, timeout=10)
    arrivals.raise_for_status()
    arrivals = arrivals.json()
    if not isinstance(arrivals, list):
        raise ValueError("arrivals: unexpected response")
    arrivals.sort(key=lambda a: a.get("timeToStation", 1e9))

    status_text = "Good Service"
    try:
        st = requests.get(f"{TFL}/Line/{line}/Status", params=q, timeout=10).json()
        status_text = st[0]["lineStatuses"][0]["statusSeverityDescription"]
    except Exception:
        pass

    cols = []
    for c in settings["columns"]:
        mine = [a for a in arrivals if a.get("direction") == c["direction"]]
        # the API names the platform "Westbound - Platform 2"; the first word is the heading
        plat = Counter(a.get("platformName", "").split(" - ")[0] for a in mine if a.get("platformName"))
        label = c.get("label") or (plat.most_common(1)[0][0] if plat else c["direction"].title())
        tow = Counter(tidy_towards(a.get("towards", "")) for a in mine if a.get("towards"))
        towards = c.get("towards") or (tow.most_common(1)[0][0] if tow else "")
        rows = [(tidy_destination(a.get("destinationName", "?")), int(a.get("timeToStation", 0))) for a in mine]
        cols.append({"label": label.upper(), "towards": towards, "rows": rows[: settings["rows"]]})
    return cols, status_text


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


def render(W, H, settings, cols, status_text, now, updated, live):
    """Same proportions as the web page: everything is a fraction of the width."""
    u = W / 100.0
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    line = settings["line"]
    line_colour = LINE_COLOURS.get(line, (0, 25, 168))
    line_name = LINE_NAMES.get(line, line.title() + " line")

    pad = 2.5 * u
    # --- header: roundel, line name, station; clock on the right
    cx, cy, r = pad + 1.6 * u, pad + 1.9 * u, 1.6 * u
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=RED, width=int(0.55 * u))
    d.rectangle([cx - r * 1.2, cy - 0.38 * u, cx + r * 1.2, cy + 0.38 * u], fill=line_colour)
    tx = cx + r + 1.2 * u
    d.text((tx, pad - 0.2 * u), line_name.upper(), font=font("regular", 3.2 * u), fill=WHITE)
    d.text((tx, pad + 3.0 * u), f"From {settings['station_name']}", font=font("light", 1.5 * u), fill=DIM)
    clock = now.strftime("%H:%M")
    fc = font("light", 3.6 * u)
    d.text((W - pad - text_w(d, clock, fc), pad - 0.4 * u), clock, font=fc, fill=WHITE)
    upd = f"Last updated: {updated.strftime('%H:%M') if updated else '--:--'}"
    fu = font("regular", 1.1 * u)
    d.text((W - pad - text_w(d, upd, fu), pad + 3.6 * u), upd, font=fu, fill=DIM)
    rule_y = pad + 5.9 * u
    d.rectangle([pad, rule_y, W - pad, rule_y + 0.22 * u], fill=line_colour)

    # --- footer
    foot_rule = H - pad - 3.0 * u
    d.rectangle([pad, foot_rule, W - pad, foot_rule + 1], fill=RULE)
    fy = foot_rule + 1.0 * u
    fs = font("regular", 1.3 * u)
    x = pad
    d.text((x, fy), "Status:", font=fs, fill=DIM)
    x += text_w(d, "Status:", fs) + 0.8 * u
    if not live:
        d.text((x, fy), "No live data, showing the last update", font=fs, fill=ORANGE)
    else:
        good = status_text.lower() == "good service"
        col = GREEN if good else ORANGE
        d.text((x, fy), ("✓ " if good else "! ") + status_text, font=font("bold", 1.3 * u), fill=col)
    tag = "tfl.gov.uk"
    d.text((W - pad - text_w(d, tag, fs), fy), tag, font=fs, fill=DIM)

    # --- two columns
    top = rule_y + 1.8 * u
    gap = 3.0 * u
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
        fh = font("bold", 1.7 * u)
        d.text((x0, top), c["label"], font=fh, fill=WHITE)
        if c["towards"]:
            d.text((x0 + text_w(d, c["label"], fh) + 1.0 * u, top + 0.35 * u),
                   f"towards {c['towards']}", font=font("light", 1.3 * u), fill=DIM)
        rows = c["rows"]
        if not rows:
            d.text((x0, rows_top + 0.5 * u), "No trains reported", font=font("light", 1.8 * u), fill=DIM)
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
    def __init__(self, dev="/dev/fb0"):
        base = "/sys/class/graphics/" + os.path.basename(dev)
        w, h = (int(v) for v in open(base + "/virtual_size").read().split(","))
        self.bpp = int(open(base + "/bits_per_pixel").read())
        self.stride = int(open(base + "/stride").read())
        self.w, self.h = w, h
        self.f = open(dev, "rb+", buffering=0)
        print(f"framebuffer {w}x{h} {self.bpp}bpp stride {self.stride}")

    def show(self, img):
        if self.bpp == 32:
            raw = img.convert("RGBA").tobytes("raw", "BGRA")
            row = self.w * 4
        elif self.bpp == 16:
            raw = img.convert("RGB").tobytes("raw", "BGR;16")
            row = self.w * 2
        else:
            raise RuntimeError(f"unsupported framebuffer depth {self.bpp}")
        if self.stride != row:  # pad each line out to the stride
            pad = b"\0" * (self.stride - row)
            raw = b"".join(raw[i:i + row] + pad for i in range(0, len(raw), row))
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
        cols, status = fetch(settings)
        now = dt.datetime.now()
        render(W, H, settings, cols, status, now, now, True).save(args.png)
        print("wrote", args.png)
        return

    fb = Framebuffer()
    cols, status, updated, live = [], "Good Service", None, False
    last_fetch = 0.0
    failures = 0
    while True:
        settings.reload()
        t = time.time()
        if t - last_fetch >= settings["refresh_seconds"] or not cols:
            last_fetch = t
            try:
                cols, status = fetch(settings)
                updated, live, failures = dt.datetime.now(), True, 0
            except Exception as e:
                failures += 1
                print("fetch failed:", e, file=sys.stderr)
                # keep showing the last board; after ~3 minutes of failures say so
                if failures >= max(1, 180 // settings["refresh_seconds"]):
                    live = False
        try:
            fb.show(render(fb.w, fb.h, settings, cols, status, dt.datetime.now(), updated, live))
        except Exception as e:
            print("render failed:", e, file=sys.stderr)
        # redraw once a minute for the clock, sooner if a fetch is due
        time.sleep(max(1.0, min(60.0 - dt.datetime.now().second, settings["refresh_seconds"] - (time.time() - last_fetch))))


if __name__ == "__main__":
    main()
