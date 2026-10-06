#!/usr/bin/env python3
"""Settings page for the tube board. Runs on the Pi, on the home network.

Open http://tubeboard.local:8080 (or the Pi's IP) on a phone. Search for a station,
pick it, pick the line, save. The board redraws with the new station within
one refresh. Nothing here talks to the outside world except TfL's search.

    python3 portal.py --port 8080   # what the service runs; port 80 belongs to
                                    # comitup's WiFi setup page
    python3 portal.py               # port 80 (needs root)

The same settings from a shell, for when the board is in someone else's house and
this page is not reachable from yours (Raspberry Pi Connect gives that shell):

    python3 portal.py --list-stations
    sudo python3 portal.py --add-station "Highbury & Islington" --line victoria
    sudo python3 portal.py --rail-key YOURKEY
    sudo python3 portal.py --add-rail DYP --line great-northern
    sudo python3 portal.py --drop-station 2
    sudo python3 portal.py --rotate 30

The sudo is because the installer and the service write settings.json as root;
reading it needs nothing. A National Rail station (one TfL's feed does not carry,
such as Drayton Park) is added by its three-letter code and needs the key: a free
account at raildata.org.uk, subscribed to "Live Departure Board".
"""
import argparse
import datetime as dt
import html
import json
import os
import re
import sys
import tempfile
import urllib.parse as up
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

try:
    import rail                                 # National Rail departures, for stations TfL does not carry
except Exception:                               # noqa: BLE001
    rail = None

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(HERE, "settings.json")
TFL = "https://api.tfl.gov.uk"
TUBE_LINES = {
    "bakerloo": "Bakerloo", "central": "Central", "circle": "Circle", "district": "District",
    "hammersmith-city": "Hammersmith & City", "jubilee": "Jubilee", "metropolitan": "Metropolitan",
    "northern": "Northern", "piccadilly": "Piccadilly", "victoria": "Victoria",
    "waterloo-city": "Waterloo & City", "elizabeth": "Elizabeth line", "dlr": "DLR",
    # TfL split the Overground into six named lines in November 2024; the old
    # "london-overground" id is not recognised any more, so it must not be offered
    "liberty": "Liberty", "lioness": "Lioness", "mildmay": "Mildmay",
    "suffragette": "Suffragette", "weaver": "Weaver", "windrush": "Windrush",
}
LIMIT = 25  # search results shown; the loop and the "showing the first N" notice share it
# The National Rail operators, for the boards TfL's feed does not carry.
RAIL_LINES = rail.LINES if rail else {}


def line_name(line):
    return TUBE_LINES.get(line) or RAIL_LINES.get(line, {}).get("name") or line

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Tube Board settings</title>
<style>
body{{font-family:-apple-system,Helvetica,Arial,sans-serif;background:#0b0d12;color:#eef;margin:0;padding:24px;max-width:520px}}
h1{{font-size:22px;margin:0 0 4px}} h2{{font-size:15px;color:#9aa;margin:26px 0 8px;text-transform:uppercase;letter-spacing:.08em}}
.now{{background:#141a26;border-radius:10px;padding:14px 16px;margin:14px 0}}
.now b{{font-size:18px}} .now span{{color:#9aa}}
input[type=text],select{{width:100%;box-sizing:border-box;font-size:17px;padding:12px;border-radius:8px;border:1px solid #334;background:#10141c;color:#fff}}
button,.btn{{display:inline-block;font-size:16px;padding:11px 16px;border-radius:8px;border:0;background:#0019a8;color:#fff;text-decoration:none;margin:6px 6px 0 0;cursor:pointer}}
.btn.alt{{background:#222a38}}
ul{{list-style:none;padding:0;margin:0}} li{{margin:8px 0}}
.ok{{background:#173d2a;color:#9fe0b6;padding:10px 14px;border-radius:8px;margin:12px 0}}
.err{{background:#4a1c1c;color:#f6b6b6;padding:10px 14px;border-radius:8px;margin:12px 0}}
label{{display:block;color:#9aa;font-size:14px;margin:12px 0 4px}}
small{{color:#778;display:block;margin-top:10px;line-height:1.45}}
input[type=range]{{width:100%;margin:2px 0 6px;accent-color:#5a78ff}}
input[type=checkbox]{{width:18px;height:18px;vertical-align:-3px;margin-right:8px;accent-color:#5a78ff}}
</style></head><body>
<h1>Tube Board</h1><small>Settings for the board on the wall</small>
{body}
</body></html>"""


def load():
    try:
        with open(SETTINGS_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save(d):
    # Two request threads, or a request and a shell command, can save at once. A
    # scratch file of its own keeps one from truncating the other's half-written bytes.
    fd, tmp = tempfile.mkstemp(dir=HERE, prefix="settings.json.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(d, f, indent=2)
            # the Pi loses power without warning, so put the bytes on the card
            # before the rename makes them the live settings
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o644)        # mkstemp makes it private; the board and --list-stations read it
        os.replace(tmp, SETTINGS_PATH)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def esc(s):
    return html.escape(str(s), quote=True)


DEFAULT_COLUMNS = [{"direction": "inbound", "label": "", "towards": ""},
                   {"direction": "outbound", "label": "", "towards": ""}]


def rotation(s):
    """The boards on the screen, in order.

    Seeded from the single station when nothing has been added yet, so adding a
    second station keeps the first instead of quietly replacing it."""
    stations = s.get("stations")
    if not isinstance(stations, list):
        # a hand edit can leave anything here; reading it as empty lets --add-station
        # write it back as a proper list instead of dying before it can save
        stations = []
    # a hand-written entry may carry no name. The board shows the id, so do the same,
    # or this page answers 500 until the file is fixed, and the page is the fix.
    r = [{"source": x.get("source") or "tfl", "line": x["line"], "station_id": x["station_id"],
          "station_name": x.get("station_name") or x["station_id"]}
         for x in stations if isinstance(x, dict) and x.get("line") and x.get("station_id")]
    if not r and s.get("line") and s.get("station_id"):
        r = [{"source": s.get("source") or "tfl", "line": s["line"], "station_id": s["station_id"],
              "station_name": s.get("station_name") or s["station_id"]}]
    return r


def set_rotation(s, r):
    """Write the rotation back.

    The single-station keys are kept pointing at the first board: an older board.py,
    or a half-finished deploy with the new page and the old board, reads only those,
    and they must never name a station that is not on the screen. One board means no
    rotation at all, which is the shape every install had before this."""
    r = [{"source": x.get("source") or "tfl", "line": x["line"], "station_id": x["station_id"],
          "station_name": x.get("station_name") or x["station_id"]} for x in r]
    if r:
        # the single-station keys point at a TfL board when there is one: an older
        # board.py reads only those, and only knows TfL's feed
        lead = next((x for x in r if x["source"] == "tfl"), r[0])
        s["source"] = lead["source"]
        s["line"] = lead["line"]
        s["station_id"] = lead["station_id"]
        s["station_name"] = lead["station_name"]
        # labels come from TfL again for whichever station leads
        s["columns"] = [dict(c) for c in DEFAULT_COLUMNS]
    s["stations"] = r if len(r) > 1 else []
    return s


def find(q):
    """TfL's station search, as a list. The page and the shell both go through here."""
    r = requests.get(f"{TFL}/StopPoint/Search/{up.quote(q)}",
                     params={"modes": "tube,dlr,elizabeth-line,overground"}, timeout=10)
    r.raise_for_status()
    return [m for m in r.json().get("matches", []) if m.get("id")]


def stop_name(m):
    return (m.get("name") or "").replace(" Underground Station", "")


def rail_station_name(crs, line, key):
    """What the feed calls the station, and a warning if it could not be asked. With
    no key the code stands in for the name; the screen says what is missing."""
    if not rail:
        raise RuntimeError("rail.py is missing next to portal.py")
    if not key:
        return crs, "no key"
    board = rail.fetch(crs, key)
    return (rail._get(board, "locationName") or crs), ""


def stop_lines(stop_id):
    """The line ids TfL lists at a stop. Only the ones this board can draw."""
    r = requests.get(f"{TFL}/StopPoint/{up.quote(stop_id, safe='')}", timeout=10)
    r.raise_for_status()
    return [l["id"] for l in r.json().get("lines", []) if l.get("id") in TUBE_LINES]


def home(msg=""):
    s = load()
    body = msg
    shown = rotation(s)
    if len(shown) > 1:
        # naming them all is the only way to tell a rotation from a board that is
        # changing station on its own
        head = (f'<span>Showing {len(shown)} boards, {esc(s.get("rotate_seconds",20))} s each</span>'
                f'<br><b>{esc(", ".join(x["station_name"] for x in shown))}</b><br><span>')
    else:
        head = (f'<span>Showing</span><br><b>{esc(s.get("station_name","?"))}</b>'
                f'<br><span>{esc(line_name(s.get("line","?")))} line, ')
    body += (f'<div class="now">{head}'
             f'{esc(s.get("rows",5))} trains each way, refresh every {esc(s.get("refresh_seconds",30))} s'
             f'<br>brightness {esc(s.get("brightness",100))}%'
             + (f', dimming to {esc(s.get("brightness_dim",30))}% at {esc(s.get("dim_from","21:00"))}'
                if s.get("dim_enabled", True) else ', no dimming')
             + (f', off {esc(s.get("off_from","00:00"))}-{esc(s.get("off_until","06:00"))}'
                if s.get("off_overnight") else '')
             + '</span></div>')
    r = rotation(s)
    body += '<h2>Boards on the screen</h2>'
    if len(r) < 2:
        body += '<p><small>One board. Add a second station and the screen starts cycling between them.</small></p>'
    body += "<ul>"
    for i, x in enumerate(r):
        body += ('<li><form method="post" action="/drop" '
                 'style="display:flex;gap:10px;align-items:center">'
                 f'<input type="hidden" name="line" value="{esc(x["line"])}">'
                 f'<input type="hidden" name="station_id" value="{esc(x["station_id"])}">'
                 f'<span style="flex:1">{esc(x["station_name"])}<br>'
                 f'<small style="margin:0">{esc(line_name(x["line"]))} line'
                 f'{" (National Rail)" if x.get("source") == "national-rail" else ""}</small></span>'
                 # the last board cannot be removed: an empty rotation is a blank screen
                 + ('<button class="btn alt" type="submit">Remove</button>' if len(r) > 1 else '')
                 + '</form></li>')
    body += "</ul>"
    body += ('<form method="get" action="/search"><input type="hidden" name="add" value="1">'
             '<input type="text" name="q" placeholder="Add a station, e.g. Highbury">'
             '<button type="submit">Add</button></form>')
    if len(r) > 1:
        body += ('<form method="post" action="/rotate">'
                 '<label>Seconds on each board (5 to 300)</label>'
                 f'<input type="text" name="rotate_seconds" value="{esc(s.get("rotate_seconds",20))}">'
                 '<button type="submit">Save</button></form>')
    if RAIL_LINES:
        opts = "".join(f'<option value="{esc(k)}"{" selected" if k == "great-northern" else ""}>'
                       f'{esc(t["name"])}</option>'
                       for k, t in sorted(RAIL_LINES.items(), key=lambda kv: kv[1]["name"]))
        body += ('<h2>National Rail</h2><form method="post" action="/save-rail">'
                 '<label>Station code (three letters, DYP for Drayton Park)</label>'
                 '<input type="text" name="crs" maxlength="3" placeholder="DYP">'
                 f'<label>Operator</label><select name="line">{opts}</select>'
                 '<label>Rail Data Marketplace key</label>'
                 f'<input type="text" name="rail_api_key" value="{esc(s.get("rail_api_key", ""))}">'
                 '<button type="submit">Add board</button></form>'
                 "<small>Stations TfL does not serve come from National Rail's own feed. "
                 'It needs a free key: an account at raildata.org.uk, subscribed to '
                 '"Live Departure Board". Saved once, it is kept.</small>')
    body += ('<h2>Show one station only</h2><form method="get" action="/search">'
             '<input type="text" name="q" placeholder="Station name, e.g. Arsenal">'
             '<button type="submit">Search</button></form>'
             '<small>This replaces everything above with the one station you pick.</small>')
    on = "checked" if s.get("dim_enabled", True) else ""
    off = "checked" if s.get("off_overnight", False) else ""
    body += ('<h2>Brightness</h2><form method="post" action="/save-screen">'
             f'<label>Daytime brightness: <b id="bv">{esc(s.get("brightness",100))}</b>%</label>'
             f'<input type="range" name="brightness" min="10" max="100" step="5" '
             f'value="{esc(s.get("brightness",100))}" oninput="bv.textContent=this.value">'
             f'<label>Evening brightness: <b id="dv">{esc(s.get("brightness_dim",30))}</b>%</label>'
             f'<input type="range" name="brightness_dim" min="0" max="100" step="5" '
             f'value="{esc(s.get("brightness_dim",30))}" oninput="dv.textContent=this.value">'
             f'<label><input type="checkbox" name="dim_enabled" value="1" {on}> '
             'Dim in the evening</label>'
             f'<label>Full brightness from</label><input type="text" name="day_from" value="{esc(s.get("day_from","07:00"))}">'
             f'<label>Dim from</label><input type="text" name="dim_from" value="{esc(s.get("dim_from","21:00"))}">'
             f'<label><input type="checkbox" name="off_overnight" value="1" {off}> '
             'Switch the screen off overnight</label>'
             f'<label>Off from</label><input type="text" name="off_from" value="{esc(s.get("off_from","00:00"))}">'
             f'<label>Back on at</label><input type="text" name="off_until" value="{esc(s.get("off_until","06:00"))}">'
             '<button type="submit">Save</button></form>'
             '<small>The monitor\'s own buttons are unreachable once it is in the '
             'frame, so this is how brightness is set. Changes apply within half a minute.</small>')

    body += ('<h2>Rows and refresh</h2><form method="post" action="/save-misc">'
             f'<label>Trains per column</label><input type="text" name="rows" value="{esc(s.get("rows",5))}">'
             f'<label>Refresh every (seconds, 20 or more)</label><input type="text" name="refresh_seconds" value="{esc(s.get("refresh_seconds",30))}">'
             '<button type="submit">Save</button></form>')
    return body


def search(q, add=False):
    body = f'<h2>{"Add" if add else "Results"} for "{esc(q)}"</h2>'
    if not q:
        # an empty term makes TfL answer 404, which reads as a broken board
        return '<div class="err">Type a station name first.</div><a class="btn alt" href="/">Back</a>'
    try:
        matches = find(q)
    except Exception as e:
        return body + f'<div class="err">TfL search failed: {esc(e)}</div><a class="btn alt" href="/">Back</a>'
    if not matches:
        return body + '<p>Nothing found. Try a shorter name.</p><a class="btn alt" href="/">Back</a>'
    body += "<ul>"
    a = "&add=1" if add else ""
    for m in matches[:LIMIT]:
        name = stop_name(m)
        body += f'<li><a class="btn" href="/pick?id={esc(m["id"])}&name={up.quote(name)}{a}">{esc(name)}</a></li>'
    body += "</ul>"
    if len(matches) > LIMIT:
        # a cut list that looks complete makes the user retype the same search
        body += f'<p><small>Showing the first {LIMIT} of {len(matches)}. Type more of the name.</small></p>'
    body += '<a class="btn alt" href="/">Back</a>'
    return body


def pick(stop_id, name, add=False):
    body = (f"<h2>{esc(name)}</h2>"
            f'<p>Which line?{" It joins the rotation as another board." if add else ""}</p>')
    try:
        lines = stop_lines(stop_id)
    except Exception as e:
        return body + f'<div class="err">TfL lookup failed: {esc(e)}</div><a class="btn alt" href="/">Back</a>'
    if not lines:
        return body + '<p>No tube lines at this stop.</p><a class="btn alt" href="/">Back</a>'
    body += '<form method="post" action="/save">'
    body += (f'<input type="hidden" name="station_id" value="{esc(stop_id)}">'
             f'<input type="hidden" name="station_name" value="{esc(name)}">'
             + ('<input type="hidden" name="add" value="1">' if add else ''))
    for l in lines:
        body += f'<button type="submit" name="line" value="{esc(l)}">{esc(TUBE_LINES[l])}</button>'
    body += '</form><a class="btn alt" href="/">Back</a>'
    return body


def resolve_hub(stop_id, line, name):
    """Search gives a hub id (HUBKGX) for every big interchange, and
    /Line/<line>/Arrivals/HUBKGX answers 200 with an empty list for ever. Swap it for
    the child stop that carries the chosen line. Returns (id, name), or (None, None)."""
    r = requests.get(f"{TFL}/StopPoint/{up.quote(stop_id, safe='')}", timeout=10)
    r.raise_for_status()
    kids = [c for c in r.json().get("children", [])
            if any(l.get("id") == line for l in c.get("lines", []))]
    if not kids:
        return None, None
    def key(c):
        cid = str(c.get("naptanId") or c.get("id") or "")
        # a hub can hold two stops with the same line: the tube platforms first,
        # then the DLR platforms, then the National Rail ones
        for i, pref in enumerate(("940GZZLU", "940GZZDL", "910G")):
            if cid.startswith(pref):
                return i
        return 9
    c = min(kids, key=key)
    kid_name = c.get("commonName") or name
    for tail in (" Underground Station", " Rail Station", " DLR Station"):
        kid_name = kid_name.replace(tail, "")
    return str(c.get("naptanId") or c.get("id") or ""), kid_name[:64]


def has_arrivals(stop_id, line):
    """Last gate before saving: a stop the line does not serve answers with an empty
    list, not an error. An empty list at 03:00 is honest, so only ask in the daytime."""
    if not 6 <= dt.datetime.now().hour < 23:
        return True
    try:
        r = requests.get(f"{TFL}/Line/{up.quote(line, safe='')}/Arrivals/{up.quote(stop_id, safe='')}",
                         timeout=10)
        r.raise_for_status()
        return bool(r.json())
    except Exception:
        return True  # a wobble at TfL must not stop the user changing station


class H(BaseHTTPRequestHandler):
    timeout = 30  # a phone that opens a socket and says nothing must not park a thread

    def __init__(self, *a, **kw):
        self._sent = False
        super().__init__(*a, **kw)

    def _send(self, body, code=200, location=None):
        self._sent = True
        self.send_response(code)
        if location:
            self.send_header("Location", location)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        data = PAGE.format(body=body).encode()
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _fail(self, e):
        """An unhandled exception kills the thread and the phone sees only "cannot open
        the page". Say what happened instead - but never answer twice, and never
        answer a socket the phone has already dropped."""
        if self._sent or isinstance(e, (BrokenPipeError, ConnectionResetError)):
            return
        try:
            self._send('<div class="err">' + esc(e) + '</div><a class="btn alt" href="/">Back</a>', 500)
        except Exception:
            pass

    def do_GET(self):
        try:
            u = up.urlsplit(self.path)
            qs = up.parse_qs(u.query)
            if u.path == "/":
                self._send(home('<div class="ok">Saved. The board updates within a minute.</div>' if "saved" in qs else ""))
            elif u.path == "/search":
                self._send(search(qs.get("q", [""])[0].strip(), qs.get("add", [""])[0] == "1"))
            elif u.path == "/pick":
                self._send(pick(qs.get("id", [""])[0], qs.get("name", [""])[0],
                                qs.get("add", [""])[0] == "1"))
            else:
                self._send("<p>Not found.</p>", 404)
        except Exception as e:
            self._fail(e)

    def do_POST(self):
        try:
            self._post()
        except Exception as e:
            self._fail(e)

    def _post(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
        except ValueError:
            n = 0
        form = up.parse_qs(self.rfile.read(n).decode())
        g = lambda k, d="": form.get(k, [d])[0].strip()
        s = load()
        if self.path == "/save":
            line, stop_id, name = g("line"), g("station_id"), g("station_name")[:64]
            # anything on the home network can post here, and the board puts both
            # values straight in a URL, so check them the same way the page does
            if line not in TUBE_LINES or not re.fullmatch(r"[A-Za-z0-9]{1,32}", stop_id):
                return self._send('<div class="err">Bad station or line.</div>' + home())
            if stop_id.startswith("HUB"):
                try:
                    stop_id, name = resolve_hub(stop_id, line, name)
                except Exception as e:
                    return self._send(f'<div class="err">TfL lookup failed: {esc(e)}</div>' + home())
                if not stop_id:
                    return self._send('<div class="err">That line does not stop here. Pick another line.'
                                      '</div>' + home())
            if not has_arrivals(stop_id, line):
                return self._send('<div class="err">TfL reports no trains at all for that station on that '
                                  'line, so the board would stay empty. Nothing saved.</div>' + home())
            if g("add") == "1":
                r = rotation(s)
                if any(x["line"] == line and x["station_id"] == stop_id for x in r):
                    return self._send('<div class="err">That station and line are already on the '
                                      'rotation.</div>' + home())
                if len(r) >= 8:
                    # eight boards at 20 s is nearly three minutes before a station
                    # comes round again, which is longer than anyone stands there
                    return self._send('<div class="err">Eight boards is the most. Remove one '
                                      'first.</div>' + home())
                r.append({"line": line, "station_id": stop_id, "station_name": name})
                set_rotation(s, r)
            else:
                set_rotation(s, [{"line": line, "station_id": stop_id, "station_name": name}])
        elif self.path == "/save-screen":
            def hhmm(v, fallback):
                v = (v or "").strip()
                try:
                    h, m = v.split(":")
                    h, m = int(h), int(m)
                    if 0 <= h < 24 and 0 <= m < 60:
                        return f"{h:02d}:{m:02d}"
                except ValueError:
                    pass
                return fallback
            try:
                s["brightness"] = max(10, min(100, int(g("brightness", "100"))))
                s["brightness_dim"] = max(0, min(100, int(g("brightness_dim", "30"))))
            except ValueError:
                return self._send('<div class="err">Brightness must be a number.</div>' + home())
            s["dim_enabled"] = g("dim_enabled") == "1"
            s["off_overnight"] = g("off_overnight") == "1"
            for key, default in (("day_from", "07:00"), ("dim_from", "21:00"),
                                 ("off_from", "00:00"), ("off_until", "06:00")):
                s[key] = hhmm(g(key), s.get(key, default))
        elif self.path == "/drop":
            r = rotation(s)
            # by identity, not position: a page loaded before a shell edit would
            # otherwise remove whichever board had slid into that slot
            keep = [x for x in r if not (x["line"] == g("line") and x["station_id"] == g("station_id"))]
            if len(keep) == len(r):
                return self._send('<div class="err">No such board.</div>' + home())
            if len(r) < 2:
                return self._send('<div class="err">That is the only board. Pick another station '
                                  'instead of removing this one.</div>' + home())
            set_rotation(s, keep)
        elif self.path == "/save-rail":
            crs, line, key = g("crs").upper(), g("line"), g("rail_api_key")
            if key:
                s["rail_api_key"] = key
            if not re.fullmatch(r"[A-Z]{3}", crs) or line not in RAIL_LINES:
                return self._send('<div class="err">The station code is three letters, and the '
                                  'operator one from the list.</div>' + home())
            try:
                name, _ = rail_station_name(crs, line, s.get("rail_api_key") or "")
            except Exception as e:
                return self._send(f'<div class="err">The rail feed did not answer: {esc(e)}</div>' + home())
            r = rotation(s)
            if any(x["line"] == line and x["station_id"] == crs for x in r):
                return self._send('<div class="err">That station is already on the rotation.</div>' + home())
            if len(r) >= 8:
                return self._send('<div class="err">Eight boards is the most. Remove one first.</div>' + home())
            r.append({"source": "national-rail", "line": line, "station_id": crs, "station_name": name})
            set_rotation(s, r)
        elif self.path == "/rotate":
            try:
                s["rotate_seconds"] = max(5, min(300, int(g("rotate_seconds", "20"))))
            except ValueError:
                return self._send('<div class="err">Seconds must be a number.</div>' + home())
        elif self.path == "/save-misc":
            try:
                s["rows"] = max(1, min(8, int(g("rows", "5"))))
                s["refresh_seconds"] = max(20, min(300, int(g("refresh_seconds", "30"))))
            except ValueError:
                return self._send('<div class="err">Numbers only.</div>' + home())
        else:
            return self._send("<p>Not found.</p>", 404)
        try:
            save(s)
        except OSError as e:
            return self._send('<div class="err">Could not write the settings file (' + esc(e) +
                              '). The SD card may be read-only.</div>' + home())
        self._send("", 303, "/?saved=1")

    def log_message(self, fmt, *args):
        sys.stderr.write("portal: " + fmt % args + "\n")


# ---------------------------------------------------------------- from a shell

def cli_list():
    s = load()
    r = rotation(s)
    if not r:
        print("No station set.")
        return
    if len(r) < 2:
        print("One board, no rotation:")
    else:
        print(f'{len(r)} boards, {s.get("rotate_seconds", 20)} s each:')
    for i, x in enumerate(r, 1):
        rail_tag = " (National Rail)" if x.get("source") == "national-rail" else ""
        print(f'  {i}. {x["station_name"]}, {line_name(x["line"])} line{rail_tag}  [{x["station_id"]}]')
    if any(x.get("source") == "national-rail" for x in r) and not s.get("rail_api_key"):
        print("No rail key saved, so the National Rail board(s) cannot fetch. "
              "Get one at raildata.org.uk, then: sudo python3 portal.py --rail-key YOURKEY")


def cli_resolve(q, line):
    """Name typed by a person -> the stop id TfL answers arrivals for."""
    matches = find(q)
    if not matches:
        raise SystemExit(f'Nothing found for "{q}". TfL only knows tube, DLR, '
                         'Elizabeth line and Overground stops here.')
    exact = [m for m in matches if stop_name(m).lower() == q.strip().lower()]
    pool = exact or matches
    # a hub and its own child can share a display name, and no typing tells them
    # apart; keep the hub, because resolve_hub() knows how to narrow it to the line
    byname = {}
    for m in pool:
        k = stop_name(m).lower()
        if k not in byname or str(m["id"]).startswith("HUB"):
            byname[k] = m
    pool = list(byname.values())
    if len(pool) > 1:
        print(f'"{q}" matches several stops. Type one of these in full:')
        for m in pool[:LIMIT]:
            print("  " + stop_name(m))
        raise SystemExit(1)
    m = pool[0]
    stop_id, name = str(m["id"]), stop_name(m)
    if stop_id.startswith("HUB"):
        stop_id, name = resolve_hub(stop_id, line, name)
        if not stop_id:
            raise SystemExit(f'The {TUBE_LINES[line]} line does not stop at {stop_name(m)}.')
    elif line not in stop_lines(stop_id):
        # the page only offers the lines TfL lists at the stop; the shell has to ask,
        # because has_arrivals() takes an empty list at night on trust
        raise SystemExit(f'The {TUBE_LINES[line]} line does not stop at {name}.')
    return stop_id, name


def cli_add(q, line):
    if line not in TUBE_LINES:
        raise SystemExit("--line must be one of: " + ", ".join(sorted(TUBE_LINES)))
    stop_id, name = cli_resolve(q, line)
    if not has_arrivals(stop_id, line):
        raise SystemExit(f'TfL reports no trains at all at {name} on the {TUBE_LINES[line]} '
                         'line, so that board would stay empty. Nothing added.')
    s = load()
    r = rotation(s)
    if any(x["line"] == line and x["station_id"] == stop_id for x in r):
        raise SystemExit(f'{name} on the {TUBE_LINES[line]} line is already on the rotation.')
    if len(r) >= 8:
        raise SystemExit("Eight boards is the most. Drop one first.")
    r.append({"line": line, "station_id": stop_id, "station_name": name})
    save(set_rotation(s, r))
    print(f'Added {name}, {TUBE_LINES[line]} line.')
    cli_list()


def cli_add_rail(crs, line):
    if not rail:
        raise SystemExit("rail.py is missing next to portal.py, so there are no National Rail boards.")
    crs = crs.strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", crs):
        raise SystemExit("A National Rail station is its three-letter code, e.g. DYP for Drayton Park.")
    if line not in RAIL_LINES:
        raise SystemExit("--line must be one of: " + ", ".join(sorted(RAIL_LINES)))
    s = load()
    name, warn = rail_station_name(crs, line, s.get("rail_api_key") or "")
    r = rotation(s)
    if any(x["line"] == line and x["station_id"] == crs for x in r):
        raise SystemExit(f"{name} on {line_name(line)} is already on the rotation.")
    if len(r) >= 8:
        raise SystemExit("Eight boards is the most. Drop one first.")
    r.append({"source": "national-rail", "line": line, "station_id": crs, "station_name": name})
    save(set_rotation(s, r))
    print(f'Added {name}, {line_name(line)} (National Rail).')
    if warn:
        print("No rail key saved yet, so this board will say so on the screen until there is one:")
        print("  sudo python3 portal.py --rail-key YOURKEY")
    cli_list()


def cli_rail_key(key):
    s = load()
    s["rail_api_key"] = key.strip()
    save(s)
    print("Rail key saved." if s["rail_api_key"] else "Rail key cleared.")
    cli_list()


def cli_drop(n):
    s = load()
    r = rotation(s)
    if not 1 <= n <= len(r):
        raise SystemExit(f"There is no board {n}. Run --list-stations.")
    if len(r) < 2:
        raise SystemExit("That is the only board. Add another before dropping this one.")
    gone = r.pop(n - 1)
    save(set_rotation(s, r))
    print(f'Dropped {gone["station_name"]}, {TUBE_LINES.get(gone["line"], gone["line"])} line.')
    cli_list()


def cli_rotate(secs):
    s = load()
    s["rotate_seconds"] = max(5, min(300, secs))
    save(s)
    print(f'Each board now holds the screen for {s["rotate_seconds"]} s.')


def main():
    ap = argparse.ArgumentParser(description="Tube board settings page, and the same "
                                             "settings from a shell.")
    ap.add_argument("--port", type=int, default=80)
    ap.add_argument("--list-stations", action="store_true", help="print the rotation and exit")
    ap.add_argument("--add-station", metavar="NAME",
                    help="add a station to the rotation (needs --line)")
    ap.add_argument("--line", help="line id for --add-station or --add-rail, e.g. victoria, great-northern")
    ap.add_argument("--add-rail", metavar="CRS",
                    help="add a National Rail station by its three-letter code (needs --line)")
    ap.add_argument("--rail-key", metavar="KEY",
                    help="save the Rail Data Marketplace key the National Rail boards need")
    ap.add_argument("--drop-station", type=int, metavar="N",
                    help="remove board N, as numbered by --list-stations")
    ap.add_argument("--rotate", type=int, metavar="SECONDS",
                    help="how long each board holds the screen")
    a = ap.parse_args()

    # Every one of these edits settings.json and exits. The board picks the change up
    # within a refresh, with no restart: it watches the file.
    did = False
    try:
        if a.line and a.add_station is None and a.add_rail is None:
            raise SystemExit("--line goes with --add-station or --add-rail")
        if a.rail_key is not None:
            cli_rail_key(a.rail_key)
            did = True
        if a.add_station is not None:
            if not a.add_station.strip() or not a.line:
                raise SystemExit("--add-station needs a name and --line, e.g. --line victoria")
            cli_add(a.add_station, a.line)
            did = True
        if a.add_rail is not None:
            if not a.add_rail.strip() or not a.line:
                raise SystemExit("--add-rail needs a three-letter code and --line, "
                                 "e.g. --add-rail DYP --line great-northern")
            cli_add_rail(a.add_rail, a.line)
            did = True
        if a.drop_station is not None:
            cli_drop(a.drop_station)
            did = True
        if a.rotate is not None:
            cli_rotate(a.rotate)
            did = True
        if a.list_stations and not did:
            cli_list()
            did = True
    except requests.RequestException as e:
        # the page says "TfL search failed"; a shell deserves one line too
        raise SystemExit(f"TfL did not answer: {e}")
    except OSError as e:
        # the installer and the service write this file as root, so a shell
        # without sudo can read it but not replace it
        raise SystemExit(f"could not write {SETTINGS_PATH}: {e}. Run this with sudo.")
    if did:
        return

    print(f"portal on http://0.0.0.0:{a.port}")
    ThreadingHTTPServer(("0.0.0.0", a.port), H).serve_forever()


if __name__ == "__main__":
    main()
