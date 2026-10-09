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
import math
import os
import re
import sys
import threading
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
try:
    import rail                                 # National Rail departures, for stations TfL does not carry
except Exception as e:                          # noqa: BLE001
    rail = None
    print("National Rail boards unavailable:", e, file=sys.stderr, flush=True)
try:
    import netdiag                              # why there are no trains, in words
except Exception as e:                          # noqa: BLE001
    netdiag = None
    print("network diagnosis unavailable:", e, file=sys.stderr, flush=True)
try:
    import qrcode                               # the setup screen's "scan to join"
except Exception:                               # noqa: BLE001
    qrcode = None

HOTSPOT_IP = "10.41.0.1"                        # where comitup's own page answers

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(HERE, "settings.json")
# The board's account of itself, for the updater, which restarts it on new code and
# must see that code drawing before it keeps it. /run is tmpfs (tubeboard.service's
# RuntimeDirectory), so a write every frame costs the SD card nothing.
HEALTH_PATH = "/run/tubeboard/health.json"
VERSION_PATH = os.path.join(HERE, "VERSION")    # the commit install.sh installed
TFL = "https://api.tfl.gov.uk"

DEFAULTS = {
    # Where the trains come from. "tfl" is the unified API, which carries the tube,
    # the DLR, the Elizabeth line and the Overground. "national-rail" is the Live
    # Departure Board feed, for everything else (Drayton Park is Great Northern),
    # and then station_id is the station's three-letter CRS code.
    "source": "tfl",
    "line": "piccadilly",
    "station_id": "940GZZLUASL",
    "station_name": "Arsenal",
    "columns": [
        {"direction": "inbound", "label": "", "towards": ""},
        {"direction": "outbound", "label": "", "towards": ""},
    ],
    "rows": 4,
    "refresh_seconds": 30,
    # More than one board. Each entry is {"line", "station_id", "station_name"} and
    # the screen shows each in turn for rotate_seconds. An empty list means one
    # board, built from the line and station_id above, which is what every install
    # before the rotation had.
    "stations": [],
    "rotate_seconds": 30,
    "app_key": "",
    # National Rail's feed needs a key: a free account at raildata.org.uk, subscribed
    # to "Live Departure Board". The url is only for when the product path moves.
    "rail_api_key": "",
    "rail_api_url": "",
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
    # Install main overnight (updater.py, from tubeboard-update.timer); false stops it.
    # The updater reads this from settings.json itself; the board never uses it.
    "auto_update": True,
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

if rail:
    for _lid, _t in rail.LINES.items():
        LINE_NAMES.setdefault(_lid, _t["name"])
        LINE_COLOURS.setdefault(_lid, _t["colour"])
        # not a TfL roundel's network, but the bar has to say something true and
        # short, and "NATIONAL RAIL" is what the signs outside the station say
        NETWORK.setdefault(_lid, "NATIONAL RAIL")

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
            if not isinstance(d, dict):
                # a hand edit can leave a list or null here; json.load is happy with
                # those and the merge below would not be, on a wall nobody can reach
                raise ValueError("settings.json is not an object")
            merged = dict(DEFAULTS)
            merged.update({k: v for k, v in d.items() if v not in (None, "")})
            # rows is read raw by fetch() and render(): a hand-edited "5" (quoted), 5.0
            # or 0 is a TypeError in both or an empty board, and five failed draws is a
            # restart loop. Coerce and clamp once, here, where every view inherits it.
            try:
                merged["rows"] = max(1, min(8, int(merged["rows"])))
            except (TypeError, ValueError, OverflowError):
                merged["rows"] = DEFAULTS["rows"]
            self.data = merged
            self.mtime = m
            return True
        except (OSError, ValueError) as e:
            print("settings: could not read, keeping the old ones:", e, file=sys.stderr, flush=True)
            return False

    def __getitem__(self, k):
        return self.data[k]

    def get(self, k, default=None):
        return self.data.get(k, default)


def station_views(settings):
    """The boards to show, in order, one flat settings dict each.

    fetch() and render() both read the station out of a settings dict, so a rotation
    is just a list of those dicts with the station keys swapped. Nothing downstream
    needs to know whether there is one board or five. An empty "stations" list gives
    a single view built from the top-level line and station_id, which is every
    install made before this existed."""
    views = []
    stations = settings["stations"]
    if not isinstance(stations, list):
        # settings.json can be edited by hand, and this runs before the first frame:
        # a number here would be a restart loop on a wall nobody can reach
        print("settings: stations is not a list, showing one board:", stations,
              file=sys.stderr, flush=True)
        stations = []
    for st in stations:
        if not isinstance(st, dict):
            continue
        line = str(st.get("line") or "").strip()
        stop = str(st.get("station_id") or "").strip()
        if not line or not stop:
            # silently skipping it would draw an empty board with nothing to explain it
            print("settings: station with no line or id, skipped:", st,
                  file=sys.stderr, flush=True)
            continue
        v = dict(settings.data)
        v["source"] = str(st.get("source") or "tfl")
        v["line"] = line
        v["station_id"] = stop
        v["station_name"] = str(st.get("station_name") or stop)
        # Platform labels are per station: TfL names them differently at each, so a
        # rotation cannot share one set of columns.
        v["columns"] = st.get("columns") or [dict(c) for c in DEFAULTS["columns"]]
        views.append(v)
    if not views:
        views.append(dict(settings.data))
    return views


def view_key(v):
    """What makes two boards the same board, for the per-board data cache."""
    return v.get("source") or "tfl", v["line"], v["station_id"]


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
OPPOSITE = {"Northbound": "Southbound", "Eastbound": "Westbound",
            "Inner Rail": "Outer Rail"}
OPPOSITE.update({v: k for k, v in OPPOSITE.items()})


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
        h, dirn, vid = heading(a), a.get("direction") or "", str(a.get("vehicleId") or "")
        # A blank on either side is "not stated", which contradicts nothing. Two
        # stated, different train ids are two trains: rail times are whole minutes,
        # so two services to Moorgate both expected at 08:12 would otherwise be one row.
        if any(k == sk and abs(t - st) <= 5
               and not (h and sh and h != sh)
               and not (dirn and sd and dirn != sd)
               and not (vid and sv and vid != sv)
               for sk, st, sh, sd, sv in seen):
            continue
        seen.append((k, t, h, dirn, vid))
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
    if not homeless:
        # Every train states its direction and they all go one way: a closure, or a
        # terminus that still fills the field. That is one column, not a puzzle to
        # solve by destination: splitting eastbound trains into "towards Cockfosters"
        # and "towards Arnos Grove" would lose the westbound column that the empty
        # direction is the whole point of showing. fetch() names it from the compass.
        return [(None, list(arrivals))]

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
    # A planned closure leads with its dates - "Saturday 19 September, from 0130 and
    # all day Sunday 20 September, no service between Acton Town and Uxbridge" - and
    # the dates are the part a reader standing in front of the board already knows.
    # The clause that says what is shut is the one that explains the empty column, so
    # start there, and let the clip take the dates instead.
    m = re.search(r"\b(no service|no through service|severe delays|minor delays|"
                  r"suspended|part suspended|closed|part closure|reduced service|"
                  r"replacement bus)\b", r, re.I)
    if m and m.start():
        r = r[m.start():]
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


def arrivals_for(settings):
    """The raw predictions for one board, in TfL's shape, from whichever feed
    carries the station."""
    if settings.get("source") == "national-rail":
        if rail is None:
            raise RuntimeError("rail.py is missing: the installer copies it next to board.py")
        board = rail.fetch(settings["station_id"], settings.get("rail_api_key") or "",
                           settings.get("rail_api_url") or None)
        return rail.predictions(board, dt.datetime.now(), settings["line"])
    q = {"app_key": settings["app_key"]} if settings["app_key"] else {}
    # quote both: the settings file can hold anything, and a stray / or ? would
    # rewrite the path or the query instead of failing
    line = up.quote(settings["line"], safe="")
    stop = up.quote(settings["station_id"], safe="")
    r = requests.get(f"{TFL}/Line/{line}/Arrivals/{stop}", params=q, timeout=10)
    r.raise_for_status()
    arrivals = r.json()
    if not isinstance(arrivals, list):
        raise ValueError("arrivals: unexpected response")
    return arrivals


def fetch(settings):
    """Returns (columns, status_text, status_ok). Each column: label, towards, rows[(dest, secs)]."""
    arrivals = arrivals_for(settings)
    arrivals.sort(key=lambda a: a.get("timeToStation", 1e9))
    arrivals = dedupe(arrivals)

    # The status line is TfL's for every board. TfL publishes a status for the
    # National Rail operators too, under the same line ids, so a Great Northern
    # board gets a Great Northern status the same way the Piccadilly one does.
    q = {"app_key": settings["app_key"]} if settings["app_key"] else {}
    line = up.quote(settings["line"], safe="")
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
    # A rail board knows its compass words without a train to read them from, so at
    # 02:00 its two empty columns say SOUTHBOUND and NORTHBOUND, not DEPARTURES twice.
    # TfL boards have no such table: their platform words only come from the trains.
    known = rail.LINES.get(settings["line"], {}) if rail and settings.get("source") == "national-rail" else {}
    for i, (c, mine) in enumerate(groups):
        if not mine and c and known.get(c.get("direction")):
            chains[i] = [known[c["direction"]].upper()] + chains[i]
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
        # a rail train past its timetable time with no estimate has no minutes to show
        rows = [(row_text(a), None if (a.get("rail") or {}).get("overdue") else int(a.get("timeToStation", 0)))
                for a in mine]
        cols.append({"label": label, "towards": towards, "rows": rows[: settings["rows"]]})

    # One direction running, and we can name the other one: keep the second column and
    # let it say it is empty. Collapsing to a single full-width column reads as a
    # broken board, and it is usually a closure - which the status line is already
    # carrying, so the two halves explain each other.
    if len(cols) == 1 and len(settings["columns"]) == 2:
        other = OPPOSITE.get(cols[0]["label"].title())
        if other:
            cols.append({"label": other.upper(), "towards": "", "rows": []})
    return cols, status_text, status_ok, status_why


def explain(settings):
    """Say what TfL answers for the configured station and what the board makes of it.

    The first thing to run when a direction is missing from the screen: it separates
    "TfL is not telling us about those trains" from "we were told and mislaid them",
    and those have very different fixes."""
    if settings.get("source") == "national-rail":
        if rail is None:
            raise RuntimeError("rail.py is missing: the installer copies it next to board.py")
        key = settings.get("rail_api_key") or ""
        print(f'{settings["station_name"]}  [{settings["station_id"]}]  National Rail, {settings["line"]}')
        print(rail.url_for(settings.get("rail_api_url"), settings["station_id"]) + "\n")
        if not key:
            print("No rail_api_key in settings.json, so this board cannot fetch anything.")
            print("Get one free at raildata.org.uk (subscribe to Live Departure Board), then:")
            print("  sudo python3 portal.py --rail-key YOURKEY")
            return
        board = rail.fetch(settings["station_id"], key, settings.get("rail_api_url") or None)
        rail.explain(board, dt.datetime.now(), settings["line"])
        raw = rail.predictions(board, dt.datetime.now(), settings["line"])
    else:
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

    empty = [c["label"] for c in cols if not c["rows"]]
    if empty and len(cols) > 1:
        print(f"\n{', '.join(empty)} is empty because TfL sent no trains that way. Check the")
        print("status line above first - a closure or a suspension is the usual reason,")
        print("and then the board is right. Otherwise check the station id: a station")
        print("that is one name on the map can be two stop points at TfL, and only one")
        print("of them carries both directions. Search it again in the portal and pick")
        print("the other result, or put the id straight into settings.json.")
    elif len(cols) < 2:
        print("\nOne column, and the board could not name the missing direction, so the")
        print("trains it did get do not agree on a platform heading. The platform list")
        print("above says what TfL actually sent.")


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
    if secs is None:
        return "delayed"
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
    # a clause that already ends in a full stop would otherwise show four dots
    return (text.rstrip(".") + "...") if text else ""


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
           rotation=None, diag=None, address=None, ss=2, ticker=None, address_left=None, _align=1,
           _out=None):
    """Draw the board. Everything is a fraction of the width, so the whole frame
    is drawn at `ss` times size and box-reduced back down. PIL draws hard-edged
    shapes; a 2x reduction is an exact 2x2 average, which is real antialiasing
    for every circle and diagonal on the screen, not just the ones we remembered.
    ss=1 skips it, for a slow machine.

    `ticker`, a dict, asks for a status line too long for its space to be handed
    back for scrolling instead of cut: render fills in "box" (where it sits on the
    screen), "strip" (one full pass of it, as an image the box's height) and "key"
    (what it says). The frame then carries the start of the strip, so the ticker's
    first window and the frame are the same pixels. Without it the line is cut
    with an ellipsis, as a still image (--png) needs."""
    if ss > 1:
        inner = {"align": ss} if ticker is not None else None
        out = {}
        big = render(W * ss, H * ss, settings, cols, status_text, status_ok,
                     status_why, now, updated, live, rotation=rotation, diag=diag,
                     address=address, ss=1, ticker=inner, address_left=address_left, _align=ss,
                     _out=out)
        if inner and inner.get("strip") is not None:
            x0, y0, x1, y1 = inner["box"]
            ticker.update(box=(x0 // ss, y0 // ss, x1 // ss, y1 // ss),
                          strip=inner["strip"].reduce(ss), key=inner["key"])
        if inner and inner.get("count"):
            x0, y0, x1, y1 = inner["count"]["box"]
            ticker["count"] = dict(inner["count"], box=(x0 // ss, y0 // ss, x1 // ss, y1 // ss), ss=ss)
        small = big.reduce(ss)
        if out.get("qr"):
            # a QR code is drawn at the screen's own resolution: reduced, its module
            # edges would blur, and a phone reads hard edges best
            url, right, bottom, side = out["qr"]
            paste_qr(small, url, right / ss, bottom / ss, side / ss)
        return small
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
    # One dot per board on the rotation, filled in each board's line colour, the
    # current one larger and ringed in white: hollow rings in the Piccadilly's dark
    # blue or Great Northern purple all but vanished on black. Without them a station that changes on its own looks like the
    # board losing its place, and someone waiting for their own station cannot tell
    # whether it is still coming. Bottom right, just above the footer rule: under
    # the clock they crowded the header (Raoul, 9 Oct 2026).
    # the settings QR takes the bottom right corner for its minute; the dots wait
    qr_card = bool(address and (address[0] or address[1]) and qrcode is not None)
    if rotation and rotation[1] > 1 and not qr_card:
        here, total = rotation[0], rotation[1]
        colours = list(rotation[2]) if len(rotation) > 2 else [DIM] * total
        dr = 0.42 * u
        step_d = 1.5 * u
        dy = foot_rule - 1.3 * u
        last = W - pad - dr
        for i in range(total):
            dx = last - (total - 1 - i) * step_d
            c = colours[i] if i < len(colours) else DIM
            if i == here:
                d.ellipse([dx - dr * 1.25, dy - dr * 1.25, dx + dr * 1.25, dy + dr * 1.25], fill=WHITE)
                d.ellipse([dx - dr * 0.8, dy - dr * 0.8, dx + dr * 0.8, dy + dr * 0.8], fill=c)
            else:
                d.ellipse([dx - dr, dy - dr, dx + dr, dy + dr], fill=c)
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
    fupd = font("regular", 1.35 * u)
    qr_req = None
    if address and (address[0] or address[1]):
        # For the first minute after the board has an IP, this corner says where the
        # settings page is. With the QR library (the Pi has it) that is a QR code in
        # the corner, "Scan for settings" beside it and how long it will stay: the
        # code carries the IP address, which is right on whatever network the board
        # is on now, so nobody types anything (Raoul, 9 Oct 2026). Without the library,
        # the addresses as text in a slim white box, as before.
        host, ip = address
        url = f"http://{ip}:8080" if ip else f"http://{host}.local:8080"
        card_right = W - pad
        if qr_card:
            side = QR_SIDE * u
            card_right = W - pad - side - 1.4 * u
            qr_req = (url, W - pad, H - 0.6 * u, side)
        if address_left is not None:
            n = max(1, math.ceil(address_left))
            d.text((card_right, uy), f"Hides in {n}s", font=fupd, fill=DIM, anchor="rm")
            widest = text_w(d, f"Hides in {ADDRESS_SECONDS}s", fupd)
            if ticker is not None:
                # The ticker redraws the seconds once a second; it gets the box they
                # live in, on multiples of the supersampling factor like the strip's.
                a = ticker.get("align", 1)
                cx0 = int((card_right - widest - 0.3 * u) // a * a)
                cx1 = int(-(-min(W, card_right + 0.3 * u) // a) * a)
                cy0, cy1 = int((uy - 1.1 * u) // a * a), int(-(-(uy + 1.1 * u) // a) * a)
                ticker["count"] = {"box": (cx0, cy0, cx1, cy1), "right": card_right - cx0,
                                   "mid": uy - cy0, "size": 1.35 * u}
            card_right -= widest + 1.4 * u
        if qr_card:
            d.text((card_right, uy), "Scan for settings", font=fupd, fill=DIM, anchor="rm")
            right_edge = card_right - text_w(d, "Scan for settings", fupd) - 2.0 * u
        else:
            card = "Settings: " + " or ".join(x for x in (f"http://{host}.local:8080" if host else "",
                                                          f"http://{ip}:8080" if ip else "") if x)
            bp = 0.6 * u
            bx0 = card_right - text_w(d, card, fupd) - 2 * bp
            # One screen pixel of white: drawn _align pixels wide on _align boundaries,
            # or the reduction smears a 2-px line across two pixels as two greys.
            al = _align
            d.rectangle([int(bx0 // al * al), int((uy - 1.25 * u) // al * al),
                         int(-(-card_right // al) * al) - 1, int(-(-(uy + 1.25 * u) // al) * al) - 1],
                        outline=WHITE, width=al)
            d.text((card_right - bp, uy), card, font=fupd, fill=DIM, anchor="rm")
            right_edge = bx0 - 2.0 * u
    else:
        upd = "Updated " + ago(updated, now)
        d.text((W - pad, uy), upd, font=fupd, fill=DIM, anchor="rm")
        # Everything on the left of this line has to stop before the timestamp. It
        # stops before the widest the timestamp gets, not this frame's: measured each
        # time, "just now" and "10s ago" moved the edge every redraw, and a reason
        # about the line's length flipped between scrolling and standing still.
        widest = max(text_w(d, x, fupd) for x in (upd, "Updated just now", "Updated 59s ago", "Updated 59m ago"))
        right_edge = W - pad - widest - 2.0 * u
    x = pad
    d.text((x, fy), "Status:", font=fs, fill=DIM, anchor="lm")
    x += text_w(d, "Status:", fs) + 0.8 * u
    feed = "National Rail" if settings.get("source") == "national-rail" else "Transport for London"
    if not live and diag and diag[0]:
        # The network check knows which failure this is. Say that, in the words a
        # person in the room can act on, rather than "no data".
        fbold = font("bold", 1.9 * u)
        d.text((x, fy), diag[0], font=fbold, fill=ORANGE, anchor="lm")
        x2 = x + text_w(d, diag[0], fbold) + 0.8 * u
        # there is no last update to show before the first good fetch
        why = diag[1] if updated else re.sub(r"\.?\s*Showing the last update", "", diag[1]).strip()
        why = clip(d, "- " + why, fs, right_edge - x2) if why else ""
        if why and why not in ("-...", "- ..."):
            d.text((x2, fy), why, font=fs, fill=DIM, anchor="lm")
    elif not live and updated is None:
        # Cold boot: the Pi is up before the network is, so the first fetch always
        # fails. There is no last update to show, and saying there is reads as a
        # fault to anyone walking past. Say what is actually happening instead.
        d.text((x, fy), clip(d, f"Starting up, waiting for {feed}", fs, right_edge - x), font=fs, fill=DIM, anchor="lm")
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
        fbold = font("bold", 1.9 * u)

        def status_line(dd, x, cy, room=None):
            """The mark, the status and the reason after it, from x. With room, the
            reason is cut to end there; without, the whole line is drawn."""
            dd.ellipse([x, cy - r, x + 2 * r, cy + r], outline=col, width=max(1, round(0.17 * u)))
            if good:
                dd.line([(x + 0.52 * r, cy + 0.05 * r), (x + 0.88 * r, cy + 0.55 * r),
                         (x + 1.5 * r, cy - 0.52 * r)], fill=col, width=max(1, round(0.19 * u)),
                        joint="curve")
            else:
                dd.line([(x + r, cy - 0.52 * r), (x + r, cy + 0.12 * r)], fill=col, width=max(1, round(0.19 * u)))
                dd.ellipse([x + r - 0.11 * u, cy + 0.42 * r, x + r + 0.11 * u, cy + 0.42 * r + 0.22 * u], fill=col)
            x2 = x + 2 * r + 0.7 * u
            dd.text((x2, cy), status_text, font=fbold, fill=col, anchor="lm")
            # why, in the reader's own words
            if status_why:
                x2 += text_w(dd, status_text, fbold) + 0.7 * u
                why = "- " + status_why if room is None else clip(dd, "- " + status_why, fs, room - x2)
                if why and why != "-...":
                    dd.text((x2, cy), why, font=fs, fill=DIM, anchor="lm")

        whole = 2 * r + 0.7 * u + text_w(d, status_text, fbold) + (
            0.7 * u + text_w(d, "- " + status_why, fs) if status_why else 0)
        a = (ticker or {}).get("align", 1)
        bx0, bx1 = int(x // a * a), int(right_edge // a * a)
        if ticker is not None and status_why and x + whole > right_edge and bx1 - bx0 >= 8 * u:
            # Too long for the line: the whole of it after "Status:" goes to the ticker,
            # the mark and "Minor Delays" with the reason (Raoul, 9 Oct 2026: the status
            # standing still while its reason moved looked wrong). The box starts at
            # the mark, so the moving text stops the same gap after "Status:" as the
            # mark sits at rest. Every edge is on a multiple of the supersampling
            # factor, so the strip, reduced on its own, is the same pixels as the frame
            # reduced around it.
            by0, by1 = int((fy - 1.7 * u) // a * a), int(-(-(fy + 1.7 * u) // a) * a)
            lead = x - bx0
            # one pass: the line, then a gap the width of a few words before it comes round
            period = int(-(-(lead + whole + 6.0 * u) // a) * a)
            strip = Image.new("RGB", (period, by1 - by0), BG)
            status_line(ImageDraw.Draw(strip), lead, fy - by0)
            img.paste(strip.crop((0, 0, bx1 - bx0, by1 - by0)), (bx0, by0))
            # the board is in the key: another board with the same status starts over too
            ticker.update(box=(bx0, by0, bx1, by1), strip=strip,
                          key=(settings.get("line"), settings.get("station_id"), status_text, status_why))
        else:
            status_line(d, x, fy, room=right_edge)

    # --- two columns
    top = rule_y + 1.8 * u
    # A wide channel each side of the divider: the minutes in the left column and
    # the destination in the right must not read as one line from across a room.
    gap = 7.0 * u
    n = max(1, len(cols))
    col_w = (W - 2 * pad - gap * (n - 1)) / n
    rows_n = max(1, settings["rows"])
    rows_top = top + 3.2 * u
    # The text is the same size whatever the count. Fewer than five rows sit under
    # their heading a little lower than the five did, and a little further apart;
    # the spare space falls at the bottom, where the rotation's dots sit. Raoul,
    # 9 Oct 2026, on the real screen: four trains, not five; centred they floated
    # away from their headings; at five's spacing, tight under the heading, they
    # wanted to come down a little and open up a little.
    area = foot_rule - 1.2 * u - rows_top
    step = area / max(5, rows_n)
    if rows_n < 5:
        step = min(area / rows_n, step * ROW_OPEN)
        rows_top += min(ROW_DROP * u, (area - step * rows_n) / 2)
    for i, c in enumerate(cols):
        x0 = pad + i * (col_w + gap)
        if i:
            xd = x0 - gap / 2
            d.rectangle([xd, top, xd + 1, foot_rule - 1.5 * u], fill=RULE)
        fh = font("bold", 2.6 * u)
        # The heading is the direction and nothing else. A "towards X" beside it
        # (TfL's platform-sign wording) showed only when every train in the column
        # went to X, which the rows below already said five times, and vanished
        # when they did not: it never told the reader anything new, and it made
        # the boards look inconsistent with each other. Raoul, 9 Oct 2026: remove it.
        # The column still carries "towards" for --explain.
        d.text((x0, top), c["label"], font=fh, fill=WHITE)
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
                # the connector between dots, in a shade of the line: Piccadilly blue
                # between Great Northern purple dots read as a mistake
                d.rectangle([dot_x - 1, yc, dot_x + 1, yc + step], fill=tuple(int(c * 0.75) for c in line_colour))
            rr = 0.62 * u
            d.ellipse([dot_x - rr, yc - rr, dot_x + rr, yc + rr], fill=line_colour if line != "northern" else WHITE)
            m = label_mins(secs)
            # the destination stops short of the minutes: "Hainault via Newbury Park"
            # and "Stevenage via Hertford North" both used to run into them
            room = (x0 + col_w) - text_w(d, m, fm) - 1.5 * u - (dot_x + 1.9 * u)
            shown = clip(d, dest, fd, room)
            if shown.endswith(" via..."):
                # "Stevenage via..." says less than "Stevenage": lose the branch whole.
                # "Edgware via Charing..." still names it, so that one stays as it is.
                shown = clip(d, dest.split(" via ", 1)[0], fd, room)
            d.text((dot_x + 1.9 * u, yc), shown, font=fd, fill=WHITE, anchor="lm")
            d.text((x0 + col_w, yc), m, font=fm, fill=ORANGE, anchor="rm")
    if qr_req:
        url, right, bottom, side = qr_req
        # never over a train: as tall as the space under the last row allows
        last_row_bottom = rows_top + step * (rows_n - 0.5) + 1.5 * u
        side = min(side, bottom - last_row_bottom)
        if _out is not None and _align > 1:
            _out["qr"] = (url, right, bottom, side)         # pasted after the reduction
        else:
            paste_qr(img, url, right, bottom, side)
    return img


def paste_qr(img, url, right, bottom, side):
    """A QR code for url, at most side pixels, its bottom right corner at (right,
    bottom). The code brings its own light margin, the quiet zone a phone needs."""
    qr = qr_image(url, side)
    if qr is not None:
        img.paste(qr, (int(right - qr.width), int(bottom - qr.height)))


def qr_image(data, size):
    """A QR code, dark on light, `size` pixels square. None without the library.
    Dark on light on purpose: phone cameras read an inverted code badly or not at all."""
    if qrcode is None:
        return None
    try:
        q = qrcode.QRCode(border=2, box_size=1, error_correction=qrcode.constants.ERROR_CORRECT_M)
        q.add_data(data)
        q.make(fit=True)
        im = q.make_image().convert("RGB")
        # a whole number of pixels per module, so every module is the same size and
        # the edges are hard; the code comes out at most `size`, never larger
        k = max(1, int(size) // im.width)
        return im.resize((im.width * k, im.height * k), Image.NEAREST)
    except Exception as e:                      # noqa: BLE001
        print("qr failed:", e, file=sys.stderr, flush=True)
        return None


def render_setup(W, H, state, ssid, hotspot, now, password="", ss=2):
    """The screen while the board has no WiFi: what to join, and what to open.

    One still screen, large, for someone holding a phone across the room. The
    ticker taught this: setup worked first time with no instructions once the
    device gave them itself. "CONNECTING" is the moment after a successful join,
    so the person knows it took and can put the phone down."""
    if ss > 1:
        return render_setup(W * ss, H * ss, state, ssid, hotspot, now, password=password, ss=1).reduce(ss)
    u = W / 100.0
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    pad = 2.5 * u
    blue = LINE_COLOURS["piccadilly"]
    r = 3.6 * u
    cx, cy = pad + r * 1.05, pad + 3.1 * u
    paste_roundel(img, cx, cy, r, blue, label="UNDERGROUND")
    tx = cx + r * 1.05 + 1.4 * u
    d.text((tx, pad + 0.4 * u), "TUBE BOARD", font=font("regular", 3.2 * u), fill=WHITE)
    d.text((tx, pad + 3.9 * u), "Setting up", font=font("light", 2.0 * u), fill=DIM)
    d.text((W - pad, cy), now.strftime("%H:%M"), font=font("light", 5.4 * u), fill=WHITE, anchor="rm")
    rule_y = pad + 7.3 * u
    d.rectangle([pad, rule_y, W - pad, rule_y + 0.22 * u], fill=blue)

    left, y = pad, rule_y + 6.0 * u
    qr_side = 26 * u
    text_room = W - pad - qr_side - 4 * u - left
    if state == "CONNECTING":
        d.text((left, y), "WIFI OK", font=font("bold", 7.0 * u), fill=GREEN)
        who = f"Connecting to {ssid}" if ssid else "Connecting to the WiFi"
        d.text((left, y + 10 * u), clip(d, who, font("regular", 3.4 * u), W - 2 * pad),
               font=font("regular", 3.4 * u), fill=WHITE)
        d.text((left, y + 15 * u), "The trains will be up in a moment.",
               font=font("light", 2.4 * u), fill=DIM)
        return img
    d.text((left, y), "JOIN WIFI:", font=font("bold", 5.0 * u), fill=ORANGE)
    d.text((left, y + 6.5 * u), clip(d, hotspot, font("bold", 6.2 * u), text_room),
           font=font("bold", 6.2 * u), fill=WHITE)
    steps = [f"1. On a phone, join that WiFi network. Password: {password}" if password
             else "1. On a phone, join that WiFi network. No password.",
             f"2. If no page opens by itself, open  http://{HOTSPOT_IP}",
             "3. Pick your own WiFi there and type its password.",
             "The board joins it and the trains come up."]
    yy = y + 16 * u
    f_step = font("light", 2.3 * u)
    for line in steps:
        d.text((left, yy), clip(d, line, f_step, text_room), font=f_step, fill=DIM)
        yy += 3.3 * u
    # the QR carries the join details; a phone camera reads it and offers to join
    # the join details a phone camera reads; ; , : \ and " are special in this format
    esc = lambda t: re.sub(r'([\\;,:"])', r"\\\1", t)             # noqa: E731
    wifi = (f"WIFI:T:WPA;S:{esc(hotspot)};P:{esc(password)};;" if password
            else f"WIFI:T:nopass;S:{esc(hotspot)};;")
    qr = qr_image(wifi, qr_side)
    if qr is not None:
        qx, qy = int(W - pad - qr.width), int(rule_y + 4.5 * u)
        img.paste(qr, (qx, qy))
        d.text((qx + qr.width / 2, qy + qr.height + 1.8 * u), "Scan to join",
               font=font("light", 2.0 * u), fill=DIM, anchor="mm")
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

    def _pack(self, img, patch=None):
        if self.bpp == 32:
            return img.convert("RGBA").tobytes("raw", "BGRA")
        # 16bpp: RGB565, little endian. This is not a rare fallback - the vc4
        # driver's framebuffer emulation picks 16-bit on a Pi 3, and neither
        # config.txt nor a -32 on the video= line overrides it. Verified on the
        # real board 2026-09-14: 1920x1080 at 16bpp. So this is THE path here.
        v = pack565(img)
        if patch:
            patch(v)        # the ticker's current window, so a redraw never jumps it back
        return v.astype("<u2").tobytes()

    def write_frame(self, v):
        """A frame already packed as RGB565 (pack565), onto the screen."""
        self._write(v.astype("<u2").tobytes())

    def show(self, img, patch=None):
        self._write(self._pack(img, patch))

    def _write(self, raw):
        if self.stride != self.row:  # pad each line out to the stride
            pad = b"\0" * (self.stride - self.row)
            raw = b"".join(raw[i:i + self.row] + pad for i in range(0, len(raw), self.row))
        self.f.seek(0)
        self.f.write(raw)

    def write_box(self, box, v):
        """One rectangle of RGB565 straight onto the screen, a row at a time. pwrite
        leaves the file position alone, so it cannot upset show()."""
        x0, y0, x1, y1 = box
        raw = v.astype("<u2").tobytes()
        n = (x1 - x0) * 2
        fd = self.f.fileno()
        for i in range(y1 - y0):
            os.pwrite(fd, raw[i * n:(i + 1) * n], (y0 + i) * self.stride + x0 * 2)


def pack565(img):
    """An image as RGB565 values, one uint16 per pixel, (height, width)."""
    import numpy as np
    a = np.asarray(img.convert("RGB"), dtype=np.uint16)
    return ((a[:, :, 0] >> 3) << 11) | ((a[:, :, 1] >> 2) << 5) | (a[:, :, 2] >> 3)


# How long the footer shows the settings address once the board has one (Raoul, 9 Oct
# 2026: one minute; it was three, and squeezed the status line for all of them).
ADDRESS_SECONDS = 60
# The settings QR's side, in hundredths of the screen's width (about 190 px at 1080p).
QR_SIDE = 10.0

# Fewer than five rows: how much further apart than five's spacing, and how far
# down from where five's first row sits, in hundredths of the screen's width.
ROW_OPEN = 1.08
ROW_DROP = 1.0


class Ticker:
    """Slides a status line that is too long for its space, the way the dot-matrix
    boards on a platform do. TfL's reasons run to 200 characters and the line
    holds about 60, so cutting them lost the part that says what to do.

    Only the status box is redrawn, 30 times a second, from a strip made once per
    status: a slice and a few row writes, a few per cent of one core on a Pi 3.
    The 305 ms full frame could never move this smoothly, and does not try. The
    line rests for PAUSE seconds with its start in place, then slides left and
    comes round again and again without stopping, a gap of a few words between
    passes. A new status, or another board, starts over with the rest.

    Everything touching the screen happens under `lock`; the main loop holds it
    from set() to the end of the frame's write, so a redraw and a slide never
    interleave. The slow parts of a redraw (packing the frame, 105 ms on the Pi,
    and the strip, 12 ms) happen before the lock: held through them, the slide
    stopped for up to 190 ms every 10 s, which the eye catches."""
    FPS = 30
    PAUSE = 3.0

    def __init__(self, fb, u):
        self.fb = fb
        self.lock = threading.Lock()
        self.count = None         # the settings card's "Hides in Ns", redrawn as N changes
        self.count_n = None
        self.count_px = None
        self.step = max(1, round(6.0 * u / self.FPS))    # about 6% of the width a second
        self.spec = None
        self.key = None
        self.off = 0
        self.rest_until = 0.0
        self.failed = False

    @staticmethod
    def prepare(spec):
        """The slow half of set(): pack and tile the strip. Done before taking the
        lock, so the slide does not stop for it (12 ms on the Pi)."""
        if spec and spec.get("strip") is not None:
            import numpy as np
            one = pack565(spec["strip"])
            w = spec["box"][2] - spec["box"][0]
            spec = dict(spec, packed=(np.concatenate([one] * (2 + w // max(1, one.shape[1])), axis=1),
                                      one.shape[1]))
        return spec

    def set(self, spec):
        """What render() handed back for the frame about to be shown. Lock held."""
        c = (spec or {}).get("count")
        until = (spec or {}).get("count_until")
        self.count = dict(c, until=until) if c and until is not None else None
        self.count_n = self.count_px = None
        if not spec or spec.get("strip") is None:
            self.spec = self.key = None
            return
        if "packed" not in spec:
            spec = self.prepare(spec)
        tiled, period = spec["packed"]
        if spec["key"] != self.key:
            self.key, self.off = spec["key"], 0
            self.rest_until = time.monotonic() + self.PAUSE
        self.off %= period
        self.spec = (spec["box"], tiled, period)

    def window(self):
        box, tiled, _ = self.spec
        return tiled[:, self.off:self.off + box[2] - box[0]]

    def patch(self, v):
        """Put the current window into a frame before it is written. Lock held."""
        if self.spec:
            x0, y0, x1, y1 = self.spec[0]
            v[y0:y1, x0:x1] = self.window()

    def countdown(self):
        """The seconds box for the current N, drawn as render() draws it: at the
        frame's supersampling and reduced, so the digits match the frame's."""
        c, ss = self.count, self.count.get("ss", 1)
        x0, y0, x1, y1 = c["box"]
        img = Image.new("RGB", ((x1 - x0) * ss, (y1 - y0) * ss), BG)
        ImageDraw.Draw(img).text((c["right"], c["mid"]), f"Hides in {self.count_n}s",
                                 font=font("regular", c["size"]), fill=DIM, anchor="rm")
        return img.reduce(ss) if ss > 1 else img

    def tick(self):
        """One step. Lock held. True if the screen was written."""
        wrote = False
        if self.count is not None:
            n = max(1, math.ceil(self.count["until"] - time.monotonic()))
            if n != self.count_n:
                self.count_n = n
                self.count_px = pack565(self.countdown())
                self.fb.write_box(self.count["box"], self.count_px)
                wrote = True
        if not self.spec or time.monotonic() < self.rest_until:
            return wrote
        # Round and round without stopping (Raoul, 9 Oct 2026: a stop after each pass
        # looked like a stall). It rests only when a board or a status first shows.
        self.off = (self.off + self.step) % self.spec[2]
        self.fb.write_box(self.spec[0], self.window())
        return True

    def run(self):
        # On a schedule, not a fixed sleep after each step: sleep(1/FPS) plus the
        # step's own time ran slow and uneven. Behind (a redraw held the lock), it
        # carries on from now rather than racing to catch up.
        period = 1.0 / self.FPS
        nxt = time.monotonic()
        while True:
            nxt += period
            delay = nxt - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                nxt = time.monotonic()
            with self.lock:
                try:
                    self.tick()
                except Exception as e:          # noqa: BLE001
                    # a ticker that cannot write leaves the line where it is, cut off
                    # but readable; the board itself carries on
                    if not self.failed:
                        print("ticker failed:", e, file=sys.stderr, flush=True)
                    self.failed, self.spec, self.key = True, None, None


class Health:
    """What the updater reads to decide whether new code works: which process, which
    commit, how many frames drawn and failed, and when the last frame and the last
    good fetch were (wall times, so another process can compare them with its own
    clock). Written whole and renamed into place, so a reader never sees half a
    file. A board that cannot write it (a Mac, an old unit with no /run/tubeboard)
    carries on without it."""

    def __init__(self):
        try:
            with open(VERSION_PATH) as f:
                version = f.read().strip() or None
        except Exception:                       # noqa: BLE001
            # missing, or not text: either way this runs before the first frame, and
            # a raise here would be a restart loop
            version = None
        self.data = {"pid": os.getpid(), "started": time.time(), "version": version,
                     "draws": 0, "failed_draws": 0, "last_draw": None, "last_fetch_ok": None}

    def fetched(self):
        self.data["last_fetch_ok"] = time.time()

    def drew(self, ok):
        if ok:
            self.data["draws"] += 1
            self.data["last_draw"] = time.time()
        else:
            self.data["failed_draws"] += 1
        try:
            tmp = HEALTH_PATH + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.data, f)
            os.replace(tmp, HEALTH_PATH)
        except Exception:                       # noqa: BLE001
            pass


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--png", help="render one frame to this file and exit")
    ap.add_argument("--size", default="1920x1080", help="frame size for --png")
    ap.add_argument("--explain", action="store_true",
                    help="print what TfL returns for this station and how it is split "
                         "into columns, then exit. Every board on the rotation, in turn")
    ap.add_argument("--view", type=int, default=1,
                    help="which board of the rotation --png draws (1 is the first)")
    ap.add_argument("--setup", action="store_true",
                    help="with --png: draw the WiFi setup screen instead of a board")
    args = ap.parse_args()

    settings = Settings()
    views = station_views(settings)
    if args.explain:
        for i, v in enumerate(views):
            if i:
                print("\n" + "-" * 70 + "\n")
            if len(views) > 1:
                print(f"Board {i + 1} of {len(views)}")
            try:
                explain(v)
            except Exception as e:                  # noqa: BLE001
                # a station TfL will not answer for is the usual reason to run this,
                # so it must not hide the boards after it
                print("could not explain this board:", e)
        return
    if args.png and args.setup:
        W, H = (int(n) for n in args.size.split("x"))
        hotspot = netdiag.hotspot_name() if netdiag else "TubeBoard-setup"
        password = netdiag.hotspot_password() if netdiag else ""
        render_setup(W, H, "HOTSPOT", "", hotspot, dt.datetime.now(), password=password).save(args.png)
        print(f"wrote {args.png} (the setup screen)", flush=True)
        return
    if args.png:
        i = max(0, min(len(views) - 1, args.view - 1))
        if i != args.view - 1:
            # the clone's settings.json holds one board, so --view 2 there is a common slip
            print(f"only {len(views)} board(s) in settings, drawing board {i + 1}",
                  file=sys.stderr, flush=True)
        v = views[i]
        W, H = (int(n) for n in args.size.split("x"))
        cols, status, status_ok, status_why = fetch(v)
        now = dt.datetime.now()
        render(W, H, v, cols, status, status_ok, status_why, now, now, True,
               rotation=(i, len(views))).save(args.png)
        print(f"wrote {args.png} (board {i + 1} of {len(views)})", flush=True)
        return

    fb = Framebuffer()
    health = Health()
    ticker = None
    if getattr(fb, "bpp", 0) == 16 and hasattr(fb, "write_box"):
        ticker = Ticker(fb, fb.w / 100.0)
        threading.Thread(target=ticker.run, name="ticker", daemon=True).start()

    def nudge_screen(force):
        # screen.py promises never to raise, but it reads brightness values that a
        # hand-edited settings.json can hold as anything; that must not restart us
        try:
            for msg in screen.apply(settings.data, dt.datetime.now(), force=force):
                print("screen:", msg, flush=True)
        except Exception as e:                  # noqa: BLE001
            print("screen control failed:", e, file=sys.stderr, flush=True)

    if screen:
        # Talk to the monitor once at startup so a restart re-asserts whatever the
        # schedule says, even if someone poked the buttons before it was framed.
        nudge_screen(True)
    # Every interval runs on the monotonic clock. A Pi has no clock battery: at boot
    # the wall clock is whatever was saved at the last shutdown, and NTP steps it
    # forward by however long the Pi was off as soon as the WiFi is up, which on
    # time.time() would end the address card's three minutes the moment it had a network.
    boot = time.monotonic()
    last_screen = 0.0
    idx = 0
    last_rotate = boot
    # One entry per board, keyed by source, line and stop rather than by position, so
    # editing the rotation keeps the data for the stations that stayed in it.
    boards = {}
    draw_failures = 0
    hotspot = netdiag.hotspot_name() if netdiag else "TubeBoard-setup"
    password = netdiag.hotspot_password() if netdiag else ""
    # What the network is doing, asked only after a fetch has failed for a reason the
    # fetch could not name itself, and at most every 30 s.
    net = {"state": "", "ssid": "", "diag": None, "checked": 0.0, "said": None}
    # The settings address shows for ADDRESS_SECONDS from the moment the board first
    # has an IP, not from boot: a slow WiFi join, or the WiFi hand-over in someone
    # else's house, still gets its full minute on the screen.
    addr = {"value": None, "checked": 0.0, "since": None}

    def network_check(v):
        if netdiag is None or time.monotonic() - net["checked"] < 30:
            return
        net["checked"] = time.monotonic()
        try:
            diag, st, ssid = netdiag.diagnose(hotspot, v.get("source") or "tfl")
            net.update(state=st, ssid=ssid, diag=diag)
            said = (st, ssid, diag[0])
            if said != net["said"]:             # the journal gets changes, not a line every 30 s
                net["said"] = said
                print(f"network: {st} {ssid or ''} - {diag[0]}", flush=True)
        except Exception as e:                  # noqa: BLE001
            print("network check failed:", e, file=sys.stderr, flush=True)

    def refusal(v, e):
        """The feed answered, and the answer was no. That is this board's problem,
        not the network's: it is said here, in the fetch's own words, and the
        network is not asked. "Not answering" would have someone checking the
        router when the fix is a key or a station id."""
        feed = "National Rail" if v.get("source") == "national-rail" else "Transport for London"
        if rail and isinstance(e, rail.KeyProblem):
            return ("Rail key needed", str(e))
        if isinstance(e, requests.exceptions.HTTPError):
            code = getattr(getattr(e, "response", None), "status_code", None)
            # requests' own text carries the URL and with it the app key: not for the wall
            if code == 404:
                return (f"{feed} does not know this stop", f"HTTP 404 for {v['station_id']}. Check the station in the settings")
            if code in (401, 403):
                return (f"{feed} refused the request", f"HTTP {code}. Check the key in the settings")
            if code == 429:
                return (f"{feed} is rate limiting", "Too many requests from here. It clears in a minute")
            if code is not None:
                return (f"{feed} answered HTTP {code}", "Showing the last update")
        return None

    def cached(v):
        return boards.setdefault(view_key(v), {
            "cols": [], "status": None, "status_ok": False, "status_why": "",
            "updated": None, "live": False, "last_fetch": 0.0, "failures": 0,
            "diag": None, "said": None})

    def draw(v, b, rotation):
        """Put one board on the screen. Draw failures are counted here because the
        loop draws twice a refresh, and five in a row is still the give-up point."""
        nonlocal draw_failures
        try:
            now = dt.datetime.now()
            up = time.monotonic() - boot
            # No WiFi to speak of: the screen's job is to get someone through setup.
            # HOTSPOT waits a minute into the process, because comitup raises its
            # hotspot first on every boot and only then joins the known network, and a
            # passer-by should not read setup instructions after every power cut.
            setup = net["state"] == "CONNECTING" or (net["state"] == "HOTSPOT" and up >= 60)
            spec = {} if ticker else None
            if setup and not b["live"]:
                frame = render_setup(fb.w, fb.h, net["state"], net["ssid"], hotspot, now,
                                     password=password)
                spec = None
            else:
                if netdiag and addr["since"] is None and time.monotonic() - addr["checked"] >= 5:
                    addr["checked"] = time.monotonic()
                    host_ip = netdiag.address()    # the IP can arrive a while after boot
                    if host_ip[1]:
                        addr["value"], addr["since"] = host_ip, time.monotonic()
                left = None if addr["since"] is None else ADDRESS_SECONDS - (time.monotonic() - addr["since"])
                card = left is not None and left > 0
                if card and spec is not None:
                    spec["count_until"] = addr["since"] + ADDRESS_SECONDS
                # this board's own refusal first, the network's verdict otherwise
                diag = None if b["live"] else (b["diag"] or net["diag"])
                frame = render(fb.w, fb.h, v, b["cols"], b["status"], b["status_ok"],
                               b["status_why"], now, b["updated"], b["live"],
                               rotation=rotation, diag=diag,
                               address=addr["value"] if card else None, ticker=spec,
                               address_left=left if card else None)
            if ticker:
                v = pack565(frame)
                ready = Ticker.prepare(spec)
                with ticker.lock:
                    ticker.set(ready)
                    ticker.patch(v)
                    fb.write_frame(v)
            else:
                fb.show(frame)
            draw_failures = 0
        except Exception as e:
            draw_failures += 1
            health.drew(False)
            print(f"draw failed ({draw_failures}):", e, file=sys.stderr, flush=True)
            # A black screen with a healthy-looking service is the worst outcome.
            # Bail out and let systemd restart us; if it is permanent the journal says why.
            if draw_failures >= 5:
                print("giving up on the screen, restarting", file=sys.stderr, flush=True)
                raise SystemExit(1)
            return
        health.drew(True)

    while True:
        changed_settings = settings.reload()
        if changed_settings:
            views = station_views(settings)
            if idx >= len(views):
                idx = 0
        t = time.monotonic()
        # Nudge the screen every 30 s, and at once if the settings just changed.
        if screen and (changed_settings or t - last_screen >= 30):
            last_screen = t
            nudge_screen(changed_settings)
        # settings.json can be edited by hand. A bad number in either of these must
        # not put the board in a restart loop on a wall nobody can reach. (json reads
        # Infinity, and int(inf) is an OverflowError, not a ValueError.)
        try:
            rotate_s = max(5, min(300, int(settings["rotate_seconds"])))
        except (TypeError, ValueError, OverflowError):
            rotate_s = 30
        try:
            refresh_s = max(5, int(settings["refresh_seconds"]))
        except (TypeError, ValueError, OverflowError):
            refresh_s = 30
        if len(views) > 1 and t - last_rotate >= rotate_s:
            idx = (idx + 1) % len(views)
            last_rotate = t
        v = views[idx]
        b = cached(v)
        rotation = (idx, len(views), tuple(LINE_COLOURS.get(x["line"], DIM) for x in views))
        # Draw first, fetch second. Switching boards then never waits on the network:
        # the station that comes up is the one already in hand, and its own refresh
        # lands straight after. Fetching first would let a ten-second TfL timeout
        # hold the old station on the screen and stop the clock mid-rotation. On a
        # cold boot this is also the blank-looking first frame, which beats a dead
        # screen while the first request runs.
        draw(v, b, rotation)
        if t - b["last_fetch"] >= refresh_s or not b["cols"]:
            b["last_fetch"] = t
            try:
                b["cols"], b["status"], b["status_ok"], b["status_why"] = fetch(v)
                b["updated"], b["live"], b["diag"] = dt.datetime.now(), True, None
                health.fetched()
                if b["failures"]:
                    print(f'fetch ok again ({v["station_name"]}, {v["line"]})', flush=True)
                b["failures"], b["said"] = 0, None
                net.update(state="", diag=None)   # a good fetch is the whole network diagnosis
            except Exception as e:
                b["failures"] += 1
                # name the board: with a rotation, "fetch failed" alone does not say
                # which station is the one that cannot be reached. The same failure
                # every pass is one journal line, not a thousand a day.
                if str(e) != b["said"]:
                    b["said"] = str(e)
                    print(f'fetch failed ({v["station_name"]}, {v["line"]}):', e,
                          file=sys.stderr, flush=True)
                b["diag"] = refusal(v, e)
                if b["diag"] is None:
                    network_check(v)
                # Keep showing the last board, and after three minutes without a good
                # fetch say so. Measured in time, not in failures: a board is only
                # fetched while it is on screen, so counting failures would wait three
                # minutes for every board on the rotation before admitting anything.
                if b["updated"] is None or (dt.datetime.now() - b["updated"]).total_seconds() >= 180:
                    b["live"] = False
            # Draw again whatever happened: the frame before the fetch is as old as the
            # fetch took, and a failure that changed the status line has to show.
            draw(v, b, rotation)
        # Redraw every 10 s. The clock only needs a minute, but the "updated Xs
        # ago" line has to keep up or it is quietly lying, and a frame costs
        # about a third of a second on a Pi 3. With a rotation, wake for the
        # switch too, or a 30 s rotation drifts by up to ten.
        waits = [10.0, 60.0 - dt.datetime.now().second,
                 refresh_s - (time.monotonic() - b["last_fetch"])]
        if len(views) > 1:
            waits.append(rotate_s - (time.monotonic() - last_rotate))
        if addr["since"] is not None:
            # redraw as the settings card runs out, not up to ten seconds later
            card_end = addr["since"] + ADDRESS_SECONDS - time.monotonic()
            if card_end > 0:
                waits.append(card_end)
        time.sleep(max(1.0, min(waits)))

if __name__ == "__main__":
    main()
