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
    sudo python3 portal.py --forget-wifi     # before the board goes to someone else
    sudo python3 portal.py --handover-check  # what is left before that; it only reads

The sudo is because the installer and the service write settings.json as root;
reading it needs nothing. A National Rail station (one TfL's feed does not carry,
such as Drayton Park) is added by its three-letter code and needs the key: a free
account at raildata.org.uk, subscribed to "Live Departure Board".
"""
import argparse
import collections
import datetime as dt
import html
import json
import os
import pwd
import re
import subprocess
import sys
import tempfile
import threading
import urllib.parse as up
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

try:
    import rail                                 # National Rail departures, for stations TfL does not carry
except Exception:                               # noqa: BLE001
    rail = None
try:
    import netdiag                              # knows the setup hotspot's name
except Exception:                               # noqa: BLE001
    netdiag = None

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
# board.py's default. The live file can predate the key, and the page must not say a
# number the screen is not using, nor write one by letting Save go through unchanged.
ROTATE_DEFAULT = 30
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
            d = json.load(f)
    except FileNotFoundError:
        return {}
    except ValueError as e:
        # A hand edit broke the JSON. The board is still running on the settings it
        # read before, so refuse, rather than let the next save replace the whole
        # file with one form's keys and hand the board a file with no station in it.
        raise RuntimeError(f"{SETTINGS_PATH} is not valid JSON ({e}). Fix it by hand first.")
    if not isinstance(d, dict):
        raise RuntimeError(f"{SETTINGS_PATH} is not a JSON object. Fix it by hand first.")
    return d


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
          "station_name": x.get("station_name") or x["station_id"],
          # board.py reads per-station platform labels from here; only a hand edit sets them
          **({"columns": x["columns"]} if isinstance(x.get("columns"), list) else {})}
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
          "station_name": x.get("station_name") or x["station_id"],
          **({"columns": x["columns"]} if isinstance(x.get("columns"), list) else {})} for x in r]
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


Ran = collections.namedtuple("Ran", "state out err")


def run_command(args, timeout=10):
    """The one door every outside command goes through, so a test can stand in for
    it. It never raises. state is "ok" (exit 0), "failed" (any other exit),
    "missing" (no such program; runuser and env exit 127 for that too) or
    "timeout". Nothing is fed to the command's stdin, so one that asks a question
    gets no answer and ends, instead of waiting for a person who is not there."""
    try:
        r = subprocess.run(args, capture_output=True, text=True, errors="replace",
                           timeout=timeout, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        return Ran("missing", "", "")
    except subprocess.TimeoutExpired:
        return Ran("timeout", "", "")
    except OSError as e:
        return Ran("failed", "", str(e))
    if r.returncode == 127:
        return Ran("missing", r.stdout or "", r.stderr or "")
    return Ran("ok" if r.returncode == 0 else "failed", r.stdout or "", r.stderr or "")


def _nm(args, timeout=15):
    """nmcli, or a RuntimeError with its own words. The portal runs as root, which
    is what deleting a connection needs."""
    r = run_command(["nmcli"] + args, timeout)
    if r.state == "missing":
        raise RuntimeError("nmcli is not installed")
    if r.state == "timeout":
        raise RuntimeError("nmcli did not answer")
    if r.state == "failed":
        raise RuntimeError((r.err or r.out or "nmcli failed").strip())
    return r.out


def hotspot_name():
    return netdiag.hotspot_name() if netdiag else "TubeBoard-setup"


def wifi_connections():
    """The WiFi networks the Pi has saved, as (uuid, name). comitup's own hotspot is
    left out: deleting that would take away the way back in, until a power cycle."""
    found = []
    for line in _nm(["-t", "-f", "UUID,TYPE,NAME", "connection", "show"]).splitlines():
        parts = line.split(":", 2)     # NAME last, because a name can hold a colon
        if len(parts) != 3 or parts[1] != "802-11-wireless":
            continue
        uuid, _, name = parts
        name = re.sub(r"\\(.)", r"\1", name)   # nmcli -t writes ':' and '\' in a name as '\:' and '\\'
        # comitup names its hotspot connection "<ap_name>-<hash>", not the ssid, and
        # only recreates it when its service starts. An access point is never a
        # network the Pi joined, so ask NetworkManager rather than guess from the name.
        mode = _nm(["-g", "802-11-wireless.mode", "connection", "show", "uuid", uuid]).strip()
        if mode == "ap" or name.lower().startswith("comitup") or name.startswith(hotspot_name()):
            continue
        found.append((uuid, name))
    return found


def forget_wifi():
    """Delete every saved WiFi network. comitup then has nothing to join and raises
    the setup hotspot within a minute. The board carries on drawing throughout.
    Returns the names forgotten."""
    gone = []
    for uuid, name in wifi_connections():
        _nm(["connection", "delete", "uuid", uuid])
        gone.append(name)
    return gone


def rail_station_name(crs, line, key, base=None):
    """What the feed calls the station, and a warning if it could not be asked. With
    no key the code stands in for the name; the screen says what is missing."""
    if not rail:
        raise RuntimeError("rail.py is missing next to portal.py")
    if not key:
        return crs, "no key"
    board = rail.fetch(crs, key, base)
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
        head = (f'<span>Showing {len(shown)} boards, {esc(s.get("rotate_seconds", ROTATE_DEFAULT))} s each</span>'
                f'<br><b>{esc(", ".join(x["station_name"] for x in shown))}</b><br><span>')
    else:
        head = (f'<span>Showing</span><br><b>{esc(s.get("station_name","?"))}</b>'
                f'<br><span>{esc(line_name(s.get("line","?")))} line, ')
    body += (f'<div class="now">{head}'
             f'{esc(s.get("rows",4))} trains each way, refresh every {esc(s.get("refresh_seconds",30))} s'
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
                 f'<input type="text" name="rotate_seconds" value="{esc(s.get("rotate_seconds", ROTATE_DEFAULT))}">'
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
    try:
        nets = wifi_connections()
    except Exception as e:                      # noqa: BLE001
        nets, net_err = [], str(e)
    else:
        net_err = ""
    body += '<h2>WiFi</h2>'
    if net_err:
        body += f'<p><small>Could not list the saved networks: {esc(net_err)}</small></p>'
    elif nets:
        body += ('<p><small>Saved networks: ' + esc(", ".join(n for _, n in nets)) + '</small></p>'
                 '<form method="post" action="/forget-wifi">'
                 '<label>Type FORGET to forget them all</label>'
                 '<input type="text" name="confirm" autocomplete="off">'
                 '<button type="submit" class="btn alt">Forget the WiFi</button></form>'
                 f'<small>Before the board goes to someone else: the Pi forgets every network it knows, '
                 f'and within a minute the screen shows how to join <b>{esc(hotspot_name())}</b> '
                 'and set up the new one. The board keeps drawing. This page stops answering '
                 'until the Pi is on a network again.</small>')
    else:
        body += '<p><small>No WiFi network is saved.</small></p>'
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
             f'<label>Trains per column</label><input type="text" name="rows" value="{esc(s.get("rows",4))}">'
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


def _forget_later():
    try:
        gone = forget_wifi()
        print("portal: forgot wifi:", ", ".join(gone) or "(none saved)", file=sys.stderr, flush=True)
    except Exception as e:                      # noqa: BLE001
        print("portal: forget wifi failed:", e, file=sys.stderr, flush=True)


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
                banner = ""
                if "saved" in qs:
                    banner = '<div class="ok">Saved. The board updates within a minute.</div>'
                elif "forgot" in qs:
                    banner = ('<div class="ok">Forgetting the WiFi. In about a minute the screen shows '
                              f'the setup hotspot, {esc(hotspot_name())}.</div>')
                self._send(home(banner))
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
        n = max(0, min(n, 1 << 16))   # a form here is a few hundred bytes; anything bigger is not from this page
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
            new_key = bool(key) and key != (s.get("rail_api_key") or "")
            if key:
                s["rail_api_key"] = key
            if not re.fullmatch(r"[A-Z]{3}", crs) or line not in RAIL_LINES:
                return self._send('<div class="err">The station code is three letters, and the '
                                  'operator one from the list.</div>' + home())
            try:
                name, _ = rail_station_name(crs, line, s.get("rail_api_key") or "", s.get("rail_api_url") or None)
            except Exception as e:
                return self._send(f'<div class="err">The rail feed did not answer: {esc(e)}</div>' + home())
            r = rotation(s)
            if any(x["line"] == line and x["station_id"] == crs for x in r):
                if not new_key:
                    return self._send('<div class="err">That station is already on the rotation.</div>' + home())
                # The station was there already: the key is what was being saved, and
                # the feed has just accepted it. It also named the station, which an
                # entry added without a key only knew by its code.
                for x in r:
                    if x["line"] == line and x["station_id"] == crs:
                        x["station_name"] = name
            else:
                if len(r) >= 8:
                    return self._send('<div class="err">Eight boards is the most. Remove one first.</div>' + home())
                r.append({"source": "national-rail", "line": line, "station_id": crs, "station_name": name})
            set_rotation(s, r)
        elif self.path == "/forget-wifi":
            if g("confirm").strip().upper() != "FORGET":
                return self._send('<div class="err">Type FORGET in the box to forget the WiFi. '
                                  'Nothing was changed.</div>' + home())
            # answer first: the delete takes this very network away under the phone
            threading.Timer(2.0, _forget_later).start()
            return self._send("", 303, "/?forgot=1")
        elif self.path == "/rotate":
            try:
                s["rotate_seconds"] = max(5, min(300, int(g("rotate_seconds", str(ROTATE_DEFAULT)))))
            except ValueError:
                return self._send('<div class="err">Seconds must be a number.</div>' + home())
        elif self.path == "/save-misc":
            try:
                s["rows"] = max(1, min(8, int(g("rows", "4"))))
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
        print(f'{len(r)} boards, {s.get("rotate_seconds", ROTATE_DEFAULT)} s each:')
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
    name, warn = rail_station_name(crs, line, s.get("rail_api_key") or "", s.get("rail_api_url") or None)
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


def cli_forget_wifi():
    nets = wifi_connections()
    if not nets:
        print("No WiFi network is saved.")
        return
    print("Forgetting: " + ", ".join(n for _, n in nets))
    print(f"The screen will show the setup hotspot, {hotspot_name()}, within a minute.")
    print("If you are on this Pi over the network, this is where you lose it.")
    for name in forget_wifi():
        print("  forgot " + name)


# ------------------------------------------------- the hand-over check
#
# Read-only. It runs commands that look and never one that changes anything, and it
# writes no file: the saved WiFi names go to the terminal and nowhere else. It never
# reads a password: passwd -S prints a date, and the key file is read for its
# comments only.

# The console password was typed into a chat once, before this day. One last changed
# before it is taken to be that one; one changed on or after it is a new one.
PASSWORD_EXPOSED_ON = dt.date(2026, 10, 9)
SETUP_HOTSPOT = "TubeBoard-setup"
COMITUP_CONF = "/etc/comitup.conf"
LIVE_DIR = "/opt/tubeboard"                     # the clone's settings.json is not the board's
UPDATE_TIMER = "tubeboard-update.timer"         # a self-updater may add this; none is not a fault
PHONE_STEP = "look at the setup screen and the hotspot on a phone once (this check cannot see that)"

Item = collections.namedtuple("Item", "todo text step group")   # group: before, password, wifi or ""


def _ok(text):
    return Item(False, text, "", "")


def _todo(text, step, group=""):
    return Item(True, text, step, group)


def _plain(s, limit=80):
    """A name or a comment, safe to print on one line: a control character in it
    must not reach the terminal as an escape sequence, and a line break or a
    right-to-left mark must not move the text round."""
    s = " ".join(str(s).split())
    s = re.sub(r"[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]", "?", s)
    return s if len(s) <= limit else s[:limit - 3] + "..."


def find_board_user():
    """(name, uid, home) of the login the board belongs to, or None. Inside a git
    clone it is the clone's owner; run from /opt/tubeboard with sudo it is the person
    who ran sudo. Root is never the answer: the console password and the keys that
    matter are the login's."""
    def entry(get, key):
        try:
            p = get(key)
        except KeyError:
            return None
        if p.pw_uid == 0 or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*\$?", p.pw_name):
            return None
        return p.pw_name, p.pw_uid, p.pw_dir
    clone = os.path.dirname(HERE)
    if os.path.exists(os.path.join(clone, ".git")):        # a file, not a folder, in a worktree
        try:
            found = entry(pwd.getpwuid, os.stat(clone).st_uid)
        except OSError:
            found = None
        if found:
            return found
    sudo_user = os.environ.get("SUDO_USER")
    return entry(pwd.getpwnam, sudo_user) if sudo_user else None


def check_wifi():
    try:
        nets = wifi_connections()
    except Exception as e:                      # noqa: BLE001
        # _nm's two short sentences are said as they are; nmcli's own words are not
        # copied out, because this check has no need of anything but the names
        why = str(e) if str(e) in ("nmcli is not installed", "nmcli did not answer") else "nmcli failed"
        return _todo(f"WiFi: could not list the saved networks ({why}). Check by hand, "
                     "and forget the WiFi last.",
                     "check the saved WiFi by hand, and forget it last", "wifi")
    if not nets:
        return _ok("WiFi: no saved network that is not an access point.")
    names = ", ".join(_plain(n) for _, n in nets)
    return _todo(f"WiFi: {len(nets)} saved network{'' if len(nets) == 1 else 's'} ({names}). "
                 "Forget the WiFi last, it cuts this connection.",
                 "forget the WiFi last, it cuts this connection", "wifi")


def _shadow_date(word):
    """The day passwd -S prints. Debian 13 prints 2026-10-09; older shadow prints 10/09/2026."""
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return dt.datetime.strptime(word, fmt).date()
        except ValueError:
            pass
    return None


def check_password(user):
    name = user[0]
    step = "reset the console password"
    r = run_command(["passwd", "-S", name])
    if r.state != "ok":
        why = {"missing": "passwd is not installed", "timeout": "passwd did not answer"}.get(
            r.state, "passwd -S failed; it needs sudo")
        return _todo(f"Console password: could not read when {name}'s was changed ({why}). "
                     "Reset it anyway.", step, "password")
    words = r.out.split()        # login, P or L or NP, last change, then four numbers
    status = words[1] if len(words) > 1 else ""
    changed = _shadow_date(words[2]) if len(words) > 2 else None
    if status == "NP":
        return _todo(f"Console password: {name} has no password at all. Set one with passwd.",
                     step, "password")
    if changed is None:
        return _todo(f"Console password: passwd -S gave no change date this check can read. "
                     f"Check it by hand: sudo passwd -S {name}", step, "password")
    locked = " The account is locked." if status == "L" else ""
    if changed < PASSWORD_EXPOSED_ON:
        return _todo(f"Console password: {name}'s was last changed on {changed}, before "
                     f"{PASSWORD_EXPOSED_ON}. It was typed into a chat once. Reset it with passwd."
                     + locked, step, "password")
    return _ok(f"Console password: {name}'s was last changed on {changed}, on or after "
               f"{PASSWORD_EXPOSED_ON}, so it is newer than the one typed into a chat." + locked)


def _connect_signed_in(text):
    """True, False, or None when the words are not ones this knows."""
    t = text.lower()
    m = re.search(r"signed[ _-]?in\s*[:=]\s*(yes|no|true|false)\b", t)
    if m:
        return m.group(1) in ("yes", "true")
    if re.search(r"not signed in|signed out|not logged in", t):
        return False
    return True if re.search(r"\bsigned in\b", t) else None


def check_connect(user):
    name, uid, home = user
    # Pi Connect is a user service. Under runuser the login has no session, so name
    # the places its runtime folder and bus live (lingering keeps them there).
    r = run_command(["runuser", "-u", name, "--", "env", f"HOME={home}",
                     f"XDG_RUNTIME_DIR=/run/user/{uid}",
                     f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus",
                     "rpi-connect", "status"])
    if r.state == "missing":
        return _todo("Raspberry Pi Connect: rpi-connect (or runuser) is not installed, so there is "
                     "no remote shell and this check cannot tell.",
                     "check Raspberry Pi Connect by hand")
    if r.state == "timeout":
        return _todo("Raspberry Pi Connect: rpi-connect status did not answer, so this check "
                     "cannot tell.", "check Raspberry Pi Connect by hand")
    signed = _connect_signed_in(r.out + "\n" + r.err)
    if signed is True:
        return _ok("Raspberry Pi Connect: signed in, so Raoul can log in remotely. "
                   "The recipient must be told.")
    if signed is False:
        return _todo("Raspberry Pi Connect: not signed in, so Raoul cannot log in remotely. "
                     "Sign in with rpi-connect signin, run under setsid nohup, if that is wanted.",
                     "sign in to Raspberry Pi Connect, or decide to go without it")
    return _todo(f"Raspberry Pi Connect: rpi-connect status gave no answer this check can read. "
                 f"Run it by hand as {name}.", "check Raspberry Pi Connect by hand")


def _words(line):
    """The words of an authorized_keys line. A quote can hold spaces (an option's value)."""
    words, cur, quoted = [], "", False
    for ch in line.strip():
        if ch == '"':
            quoted = not quoted
            cur += ch
        elif ch.isspace() and not quoted:
            if cur:
                words.append(cur)
                cur = ""
        else:
            cur += ch
    if cur:
        words.append(cur)
    return words


def key_comment(line):
    """The comment on one authorized_keys line, and nothing else of it. The comment is
    what follows the key type and the key itself; options come before those."""
    words = _words(line)
    for i, w in enumerate(words):
        if re.fullmatch(r"(ssh|ecdsa|sk)-[a-z0-9@.-]+", w) and i + 1 < len(words):
            return " ".join(words[i + 2:])
    return None


def check_ssh(user):
    name, _, home = user
    path = os.path.join(home, ".ssh", "authorized_keys")
    step = "decide whether Raoul's SSH key stays"
    try:
        with open(path, errors="replace") as f:
            lines = [x for x in f.read().splitlines() if x.strip() and not x.lstrip().startswith("#")]
    except FileNotFoundError:
        return _ok(f"SSH: {name} has no authorized_keys file, so no key can log in.")
    except OSError as e:
        return _todo(f"SSH: could not read {path} ({_plain(e.strerror or e)}). Run this check with sudo.",
                     step)
    if not lines:
        return _ok(f"SSH: no key is authorised for {name}.")
    comments = []
    for x in lines[:10]:
        c = key_comment(x)
        if c is not None:
            # a malformed line can leave key text where a comment goes; no real comment is that long a word
            c = re.sub(r"[A-Za-z0-9+/=_-]{40,}", "?", c)
        comments.append("unreadable line" if c is None else (_plain(c, 60) if c else "no comment"))
    more = f", and {len(lines) - 10} more" if len(lines) > 10 else ""
    return _todo(f"SSH: {len(lines)} key{'' if len(lines) == 1 else 's'} can log in as {name} "
                 f"(comments: {', '.join(comments)}{more}). Decide whether Raoul's key stays.", step)


def check_hotspot(conf):
    if not netdiag:
        return _todo("Hotspot: netdiag.py is missing next to portal.py, so the name cannot be read.",
                     "check the hotspot's name by hand", "before")
    name = netdiag.hotspot_name(conf, default="")
    fix = (f"Set ap_name: {SETUP_HOTSPOT} in {conf} and restart comitup "
           "(a full install.sh run does both).")
    if name == SETUP_HOTSPOT:
        return _ok(f"Hotspot: comitup raises {SETUP_HOTSPOT}.")
    if not name:
        return _todo(f"Hotspot: {conf} sets no ap_name, or cannot be read, so comitup uses its stock "
                     f"name. {fix}", f"set the hotspot's name to {SETUP_HOTSPOT}", "before")
    return _todo(f"Hotspot: comitup raises {_plain(name)}, not {SETUP_HOTSPOT}. {fix}",
                 f"set the hotspot's name to {SETUP_HOTSPOT}", "before")


def _clamped(value, lo, hi, default):
    """The number board.py would use for a setting: its own default when the file holds
    something that is not a number, and its limits otherwise."""
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def check_settings(live_dir):
    """The live settings: where they are read from, the rail key, and what is on the screen."""
    items = []
    if os.path.realpath(os.path.dirname(SETTINGS_PATH)) != os.path.realpath(live_dir):
        items.append(_todo(f"Settings: this reads {SETTINGS_PATH}, not the live file in {live_dir}. "
                           f"Run {live_dir}/portal.py instead.", f"run this check from {live_dir}"))
    try:
        s = load()
    except (RuntimeError, OSError) as e:
        items.append(_todo(f"Settings: {_plain(e, 200)}", "fix settings.json by hand"))
        return items
    boards = rotation(s)
    rail_boards = [x for x in boards if x.get("source") == "national-rail"]
    key = s.get("rail_api_key")
    has_key = isinstance(key, str) and bool(key.strip())
    if has_key:
        items.append(_ok("Rail key: saved."))
    elif rail_boards:
        n = len(rail_boards)
        items.append(_todo(f"Rail key: not saved, and {n} National Rail board{'' if n == 1 else 's'} "
                           f"on the rotation {'needs' if n == 1 else 'need'} one. "
                           "Run: sudo python3 portal.py --rail-key YOURKEY",
                           "save the rail key"))
    else:
        items.append(_ok("Rail key: none saved, and no National Rail board needs one."))
    if not boards:
        items.append(_todo("Boards: no station is set.", "add a station to the board"))
    else:
        names = ", ".join(_plain(x["station_name"], 40) for x in boards)
        rows = _clamped(s.get("rows"), 1, 8, 4)
        secs = _clamped(s.get("rotate_seconds"), 5, 300, ROTATE_DEFAULT)
        if len(boards) == 1:
            items.append(_ok(f"Boards: one board ({names}), {rows} trains each way. With one board "
                             f"there is no rotation; the rotation time is {secs} s."))
        else:
            items.append(_ok(f"Boards: {len(boards)} on the rotation ({names}), {rows} trains each way, "
                             f"{secs} s each."))
    return items


def check_updater():
    # "show" answers LoadState=not-found with exit 0 for a unit that does not exist,
    # where "is-enabled" and "list-unit-files" end with an error: absence is not a fault.
    r = run_command(["systemctl", "show", "--property=LoadState,UnitFileState", UPDATE_TIMER])
    step = "check the self-updater by hand"
    if r.state != "ok":
        why = {"missing": "systemctl is not installed", "timeout": "systemctl did not answer"}.get(
            r.state, "systemctl failed")
        return _todo(f"Self-updater: could not ask systemd ({why}).", step)
    got = dict(x.split("=", 1) for x in r.out.splitlines() if "=" in x)
    load_state, enabled = got.get("LoadState", ""), got.get("UnitFileState", "")
    if load_state == "not-found":
        return _ok(f"Self-updater: none installed (no {UPDATE_TIMER}), so updates are by hand.")
    if not load_state:
        return _todo("Self-updater: systemctl gave no answer this check can read.", step)
    if enabled.startswith("enabled"):
        return _ok(f"Self-updater: {UPDATE_TIMER} is enabled.")
    return _todo(f"Self-updater: {UPDATE_TIMER} exists but is not enabled ({_plain(enabled or load_state, 20)}). "
                 f"Enable it with: sudo systemctl enable --now {UPDATE_TIMER}",
                 "enable the self-updater")


def next_line(items):
    """What to do next, in the order it has to be done. A fix the phone will show comes
    first, then the phone, then the password, then the rest, and the WiFi last because
    forgetting it cuts the connection this check runs over. The phone is always a step:
    this check cannot see a phone."""
    todo = [i for i in items if i.todo]

    def of(group):
        return [i.step for i in todo if i.group == group]
    steps = of("before") + [PHONE_STEP] + of("password") + of("") + of("wifi")
    if len(steps) == 1:
        return f"Next: {steps[0]}. Nothing else is left."
    return "Next, in this order: " + "; ".join(f"{n}) {x}" for n, x in enumerate(steps, 1)) + "."


def handover_report(user=None, conf=None, live_dir=None, root=None):
    """The check as a list of Items and the last line. user, conf, live_dir and root
    are there so a test can say where it is standing."""
    conf = conf or COMITUP_CONF
    live_dir = live_dir or LIVE_DIR
    if root is None:
        root = os.geteuid() == 0
    if user is None:
        user = find_board_user()
    items = []
    if not root:
        items.append(_todo("This was not run as root. Some lines below may say they could not "
                           "read what they need. Run it again with sudo.",
                           "run this check again with sudo", "before"))
    items.append(check_wifi())
    if user:
        items += [check_password(user), check_connect(user), check_ssh(user)]
    else:
        for what, step, group in (("Console password", "reset the console password", "password"),
                                  ("Raspberry Pi Connect", "check Raspberry Pi Connect by hand", ""),
                                  ("SSH", "decide whether Raoul's SSH key stays", "")):
            items.append(_todo(f"{what}: could not tell which login the board belongs to. Run this "
                               "with sudo from that login.", step, group))
    items.append(check_hotspot(conf))
    items += check_settings(live_dir)
    items.append(check_updater())
    return items, next_line(items)


def cli_handover_check(**where):
    items, last = handover_report(**where)
    for i in items:
        print(("TO DO  " if i.todo else "OK     ") + i.text)
    print(last)


def cli_drop(n):
    s = load()
    r = rotation(s)
    if not 1 <= n <= len(r):
        raise SystemExit(f"There is no board {n}. Run --list-stations.")
    if len(r) < 2:
        raise SystemExit("That is the only board. Add another before dropping this one.")
    gone = r.pop(n - 1)
    save(set_rotation(s, r))
    print(f'Dropped {gone["station_name"]}, {line_name(gone["line"])} line.')
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
    ap.add_argument("--forget-wifi", action="store_true",
                    help="forget every saved WiFi network, so the setup hotspot comes up")
    ap.add_argument("--handover-check", action="store_true",
                    help="say what is left before the board goes to the recipient; only reads")
    a = ap.parse_args()

    # Every one of these edits settings.json and exits. The board picks the change up
    # within a refresh, with no restart: it watches the file.
    did = False
    try:
        if a.handover_check:
            # it reads and the rest write, so mixing them would make "reads only" untrue
            if (a.forget_wifi or a.list_stations or a.line or a.add_station is not None
                    or a.add_rail is not None or a.rail_key is not None
                    or a.drop_station is not None or a.rotate is not None):
                raise SystemExit("--handover-check stands alone. It only reads, "
                                 "and the other options change things.")
            cli_handover_check()
            did = True
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
        if a.forget_wifi:
            cli_forget_wifi()
            did = True
        if a.list_stations and not did:
            cli_list()
            did = True
    except requests.RequestException as e:
        # the page says "TfL search failed"; a shell deserves one line too
        raise SystemExit(f"TfL did not answer: {e}")
    except (RuntimeError, ValueError) as e:
        # rail.fetch's own words for a key the feed refused, and load()'s for a broken file
        raise SystemExit(str(e))
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
