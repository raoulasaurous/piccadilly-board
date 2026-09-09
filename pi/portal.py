#!/usr/bin/env python3
"""Settings page for the tube board. Runs on the Pi, on the home network.

Open http://tubeboard.local (or the Pi's IP) on a phone. Search for a station,
pick it, pick the line, save. The board redraws with the new station within
one refresh. Nothing here talks to the outside world except TfL's search.

    python3 portal.py            # port 80 (needs root)
    python3 portal.py --port 8080
"""
import argparse
import html
import json
import os
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
    "london-overground": "Overground",
}

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
small{{color:#778}}
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
    with open(tmp, "w") as f:
        json.dump(d, f, indent=2)
    os.replace(tmp, SETTINGS_PATH)


def esc(s):
    return html.escape(str(s), quote=True)


def home(msg=""):
    s = load()
    body = msg
    body += (f'<div class="now"><span>Showing</span><br><b>{esc(s.get("station_name","?"))}</b>'
             f'<br><span>{esc(TUBE_LINES.get(s.get("line",""), s.get("line","?")))} line, '
             f'{esc(s.get("rows",5))} trains each way, refresh every {esc(s.get("refresh_seconds",30))} s</span></div>')
    body += ('<h2>Change station</h2><form method="get" action="/search">'
             '<input type="text" name="q" placeholder="Station name, e.g. Arsenal" autofocus>'
             '<button type="submit">Search</button></form>')
    body += ('<h2>Rows and refresh</h2><form method="post" action="/save-misc">'
             f'<label>Trains per column</label><input type="text" name="rows" value="{esc(s.get("rows",5))}">'
             f'<label>Refresh every (seconds, 20 or more)</label><input type="text" name="refresh_seconds" value="{esc(s.get("refresh_seconds",30))}">'
             '<button type="submit">Save</button></form>')
    return body


def search(q):
    body = f'<h2>Results for "{esc(q)}"</h2>'
    try:
        r = requests.get(f"{TFL}/StopPoint/Search/{up.quote(q)}", params={"modes": "tube,dlr,elizabeth-line,overground"}, timeout=10)
        r.raise_for_status()
        matches = r.json().get("matches", [])
    except Exception as e:
        return body + f'<div class="err">TfL search failed: {esc(e)}</div><a class="btn alt" href="/">Back</a>'
    if not matches:
        return body + '<p>Nothing found. Try a shorter name.</p><a class="btn alt" href="/">Back</a>'
    body += "<ul>"
    for m in matches[:12]:
        name = m.get("name", "").replace(" Underground Station", "")
        body += f'<li><a class="btn" href="/pick?id={esc(m["id"])}&name={up.quote(name)}">{esc(name)}</a></li>'
    body += '</ul><a class="btn alt" href="/">Back</a>'
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


class H(BaseHTTPRequestHandler):
    def _send(self, body, code=200, location=None):
        self.send_response(code)
        if location:
            self.send_header("Location", location)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        data = PAGE.format(body=body).encode()
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
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

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        form = up.parse_qs(self.rfile.read(n).decode())
        g = lambda k, d="": form.get(k, [d])[0].strip()
        s = load()
        if self.path == "/save":
            if not (g("station_id") and g("line")):
                return self._send('<div class="err">Missing station or line.</div>' + home())
            s.update({"station_id": g("station_id"), "station_name": g("station_name"), "line": g("line"),
                      # labels come from TfL again for the new station
                      "columns": [{"direction": "inbound", "label": "", "towards": ""},
                                  {"direction": "outbound", "label": "", "towards": ""}]})
        elif self.path == "/save-misc":
            try:
                s["rows"] = max(1, min(8, int(g("rows", "5"))))
                s["refresh_seconds"] = max(20, min(300, int(g("refresh_seconds", "30"))))
            except ValueError:
                return self._send('<div class="err">Numbers only.</div>' + home())
        else:
            return self._send("<p>Not found.</p>", 404)
        save(s)
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
