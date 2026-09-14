#!/usr/bin/env python3
"""Settings page for the tube board. Runs on the Pi, on the home network.

Open http://tubeboard.local:8080 (or the Pi's IP) on a phone. Search for a station,
pick it, pick the line, save. The board redraws with the new station within
one refresh. Nothing here talks to the outside world except TfL's search.

    python3 portal.py --port 8080   # what the service runs; port 80 belongs to
                                    # comitup's WiFi setup page
    python3 portal.py               # port 80 (needs root)
"""
import argparse
import datetime as dt
import html
import json
import os
import re
import sys
import urllib.parse as up
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

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

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Tube Board settings</title>
<style>
body{{font-family:-apple-system,Helvetica,Arial,sans-serif;background:#0b0d12;color:#eef;margin:0;padding:24px;max-width:520px}}
h1{{font-size:22px;margin:0 0 4px}} h2{{font-size:15px;color:#9aa;margin:26px 0 8px;text-transform:uppercase;letter-spacing:.08em}}
.now{{background:#141a26;border-radius:10px;padding:14px 16px;margin:14px 0}}
.now b{{font-size:18px}} .now span{{color:#9aa}}
input[type=text]{{width:100%;box-sizing:border-box;font-size:17px;padding:12px;border-radius:8px;border:1px solid #334;background:#10141c;color:#fff}}
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
    tmp = SETTINGS_PATH + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(d, f, indent=2)
            # the Pi loses power without warning, so put the bytes on the card
            # before the rename makes them the live settings
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, SETTINGS_PATH)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def esc(s):
    return html.escape(str(s), quote=True)


def home(msg=""):
    s = load()
    body = msg
    body += (f'<div class="now"><span>Showing</span><br><b>{esc(s.get("station_name","?"))}</b>'
             f'<br><span>{esc(TUBE_LINES.get(s.get("line",""), s.get("line","?")))} line, '
             f'{esc(s.get("rows",5))} trains each way, refresh every {esc(s.get("refresh_seconds",30))} s'
             f'<br>brightness {esc(s.get("brightness",100))}%'
             + (f', dimming to {esc(s.get("brightness_dim",30))}% at {esc(s.get("dim_from","21:00"))}'
                if s.get("dim_enabled", True) else ', no dimming')
             + (f', off {esc(s.get("off_from","00:00"))}-{esc(s.get("off_until","06:00"))}'
                if s.get("off_overnight") else '')
             + '</span></div>')
    body += ('<h2>Change station</h2><form method="get" action="/search">'
             '<input type="text" name="q" placeholder="Station name, e.g. Arsenal" autofocus>'
             '<button type="submit">Search</button></form>')
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


def search(q):
    body = f'<h2>Results for "{esc(q)}"</h2>'
    if not q:
        # an empty term makes TfL answer 404, which reads as a broken board
        return '<div class="err">Type a station name first.</div><a class="btn alt" href="/">Back</a>'
    try:
        r = requests.get(f"{TFL}/StopPoint/Search/{up.quote(q)}", params={"modes": "tube,dlr,elizabeth-line,overground"}, timeout=10)
        r.raise_for_status()
        matches = r.json().get("matches", [])
    except Exception as e:
        return body + f'<div class="err">TfL search failed: {esc(e)}</div><a class="btn alt" href="/">Back</a>'
    if not matches:
        return body + '<p>Nothing found. Try a shorter name.</p><a class="btn alt" href="/">Back</a>'
    body += "<ul>"
    for m in matches[:LIMIT]:
        if not m.get("id"):
            continue
        name = m.get("name", "").replace(" Underground Station", "")
        body += f'<li><a class="btn" href="/pick?id={esc(m["id"])}&name={up.quote(name)}">{esc(name)}</a></li>'
    body += "</ul>"
    if len(matches) > LIMIT:
        # a cut list that looks complete makes the user retype the same search
        body += f'<p><small>Showing the first {LIMIT} of {len(matches)}. Type more of the name.</small></p>'
    body += '<a class="btn alt" href="/">Back</a>'
    return body


def pick(stop_id, name):
    body = f"<h2>{esc(name)}</h2><p>Which line?</p>"
    try:
        r = requests.get(f"{TFL}/StopPoint/{up.quote(stop_id)}", timeout=10)
        r.raise_for_status()
        lines = [l["id"] for l in r.json().get("lines", []) if l["id"] in TUBE_LINES]
    except Exception as e:
        return body + f'<div class="err">TfL lookup failed: {esc(e)}</div><a class="btn alt" href="/">Back</a>'
    if not lines:
        return body + '<p>No tube lines at this stop.</p><a class="btn alt" href="/">Back</a>'
    body += '<form method="post" action="/save">'
    body += f'<input type="hidden" name="station_id" value="{esc(stop_id)}"><input type="hidden" name="station_name" value="{esc(name)}">'
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
                self._send(search(qs.get("q", [""])[0].strip()))
            elif u.path == "/pick":
                self._send(pick(qs.get("id", [""])[0], qs.get("name", [""])[0]))
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
            s.update({"station_id": stop_id, "station_name": name, "line": line,
                      # labels come from TfL again for the new station
                      "columns": [{"direction": "inbound", "label": "", "towards": ""},
                                  {"direction": "outbound", "label": "", "towards": ""}]})
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=80)
    a = ap.parse_args()
    print(f"portal on http://0.0.0.0:{a.port}")
    ThreadingHTTPServer(("0.0.0.0", a.port), H).serve_forever()


if __name__ == "__main__":
    main()
