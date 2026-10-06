#!/usr/bin/env python3
"""Offline tests for the station rotation, in board.py and in portal.py.

    cd pi && python3 test_rotation.py

Every TfL call is stubbed, so this runs anywhere, including in a cloud session
where api.tfl.gov.uk is blocked. That is the point of it: the rotation is the one
part of the board that cannot be checked by looking at the screen, because a
mistake in it shows up as the wrong station a minute later rather than as a
broken frame. Nothing here touches the live settings file.

Not covered: anything that needs the real feed, the real screen, or a user
without root (the "run this with sudo" message when settings.json is not
writable).
"""
import atexit
import contextlib
import datetime as dt
import http.client
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import types
import urllib.parse as up

import requests as real_requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
TMP = tempfile.mkdtemp(prefix="tubeboard-test-")
# a run leaves a few small files; on the Pi that is the SD card, so clean up
atexit.register(shutil.rmtree, TMP, ignore_errors=True)

import board                                                        # noqa: E402
import portal                                                       # noqa: E402

board.SETTINGS_PATH = os.path.join(TMP, "board-settings.json")
portal.SETTINGS_PATH = os.path.join(TMP, "portal-settings.json")

FAILS = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + (f"  {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def stub(get):
    """A stand-in for the requests module: get() is ours, the exception classes are
    the real ones, because portal.main() names them in an except clause."""
    return types.SimpleNamespace(get=get, RequestException=real_requests.RequestException,
                                 exceptions=real_requests.exceptions)


class Resp:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


# ------------------------------------------------------------------ board.py

ARRIVALS = {
    ("piccadilly", "940GZZLUASL"): [
        {"platformName": "Eastbound - Platform 1", "direction": "inbound",
         "towards": "Cockfosters", "destinationName": "Cockfosters Underground Station",
         "timeToStation": 120, "id": "1", "vehicleId": "101"},
        {"platformName": "Westbound - Platform 2", "direction": "outbound",
         "towards": "Heathrow via T4 Loop",
         "destinationName": "Heathrow Terminal 4 Underground Station",
         "timeToStation": 300, "id": "2", "vehicleId": "102"},
    ],
    ("victoria", "940GZZLUHAI"): [
        {"platformName": "Southbound - Platform 2", "direction": "outbound",
         "towards": "Brixton", "destinationName": "Brixton Underground Station",
         "timeToStation": 90, "id": "3", "vehicleId": "103"},
        {"platformName": "Northbound - Platform 1", "direction": "inbound",
         "towards": "Walthamstow Central",
         "destinationName": "Walthamstow Central Underground Station",
         "timeToStation": 240, "id": "4", "vehicleId": "104"},
    ],
    # no direction and no compass platform, which is how TfL answers for much of
    # the Overground: the board has to fall back to where the train is going
    ("mildmay", "910GHGHI"): [
        {"platformName": "Platform 7", "direction": "", "towards": "Stratford",
         "destinationName": "Stratford Rail Station", "timeToStation": 150,
         "id": "5", "vehicleId": "105"},
        {"platformName": "Platform 8", "direction": "", "towards": "Richmond",
         "destinationName": "Richmond Rail Station", "timeToStation": 420,
         "id": "6", "vehicleId": "106"},
    ],
}

board_calls = []                 # every Arrivals URL asked for, in order
fetch_log = []                   # (fake clock, line, board on screen) per Arrivals call
outage = {"lines": set()}        # lines whose Arrivals raise, for the failure tests
last_shown = ["", ""]            # (station, line) of the most recent frame drawn
fake_clock = [1000.0]


def board_get(url, params=None, timeout=None):
    board_calls.append(url)
    parts = url.split("/")
    if url.endswith("/Status"):
        return Resp([{"lineStatuses": [{"statusSeverity": 10,
                                        "statusSeverityDescription": "Good Service"}]}])
    line, stop = parts[4], parts[6]
    fetch_log.append((fake_clock[0], line, tuple(last_shown)))
    if line in outage["lines"]:
        raise ConnectionError("stubbed outage")
    return Resp(list(ARRIVALS.get((line, stop), [])))


board.requests = stub(board_get)

THREE = {"stations": [
    {"line": "piccadilly", "station_id": "940GZZLUASL", "station_name": "Arsenal"},
    {"line": "victoria", "station_id": "940GZZLUHAI", "station_name": "Highbury & Islington"},
    {"line": "mildmay", "station_id": "910GHGHI", "station_name": "Highbury & Islington"},
], "rotate_seconds": 20}


def write_board(extra, bump=0):
    d = dict(board.DEFAULTS)
    d.update(extra)
    with open(board.SETTINGS_PATH, "w") as f:
        json.dump(d, f)
    if bump:
        # Settings.reload keys off mtime; two writes in one second look like none
        st = os.stat(board.SETTINGS_PATH)
        os.utime(board.SETTINGS_PATH, (st.st_atime, st.st_mtime + bump))


def views_for(extra):
    write_board(extra)
    return board.station_views(board.Settings())


def test_views():
    print("which boards to draw")
    v = views_for({})
    check("no stations gives one board", len(v) == 1 and v[0]["station_name"] == "Arsenal")

    v = views_for(dict(THREE, rows=7))
    check("three stations give three boards", len(v) == 3, len(v))
    check("they keep their order",
          [x["station_name"] for x in v] == ["Arsenal", "Highbury & Islington",
                                             "Highbury & Islington"])
    check("each keeps its own line", [x["line"] for x in v] == ["piccadilly", "victoria", "mildmay"])
    check("two lines at one station are two boards", board.view_key(v[1]) != board.view_key(v[2]))
    check("the shared settings carry over", all(x["rows"] == 7 for x in v))

    v = views_for({"stations": [{"line": "victoria"}, {"station_id": "940GZZLUASL"}, "nonsense",
                                {"line": "victoria", "station_id": "940GZZLUHAI",
                                 "station_name": "H&I"}]})
    check("half-written entries are skipped and the good one kept",
          len(v) == 1 and v[0]["station_name"] == "H&I", [x["station_name"] for x in v])

    v = views_for({"stations": [{"line": "victoria", "station_id": "940GZZLUHAI"}]})
    check("a station with no name falls back to its id", v[0]["station_name"] == "940GZZLUHAI")

    # a hand edit can leave a number where the list goes; that used to be a crash
    # loop before the first frame
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        v5, vt = views_for({"stations": 5}), views_for({"stations": True})
    check("a number or a bool where the list should be gives one board",
          len(v5) == 1 and len(vt) == 1 and v5[0]["station_name"] == "Arsenal")
    check("and says so in the journal", "not a list" in err.getvalue())


def test_fetch_and_render():
    print("\nfetching and drawing each board")
    views = views_for(THREE)
    for v in views:
        cols, status, ok, why = board.fetch(v)
        check(f'{v["station_name"]} on the {v["line"]} line draws two columns '
              f'{[c["label"] for c in cols]}', len(cols) == 2)
    check("every line was asked for separately",
          all(any(f"/Line/{l}/Arrivals" in c for c in board_calls)
              for l in ("piccadilly", "victoria", "mildmay")))

    at = dt.datetime(2026, 10, 6, 19, 30)
    def frame(rotation):
        return board.render(640, 360, views[0], board.fetch(views[0])[0], "Good Service",
                            True, "", at, at, True, rotation=rotation, ss=1).tobytes()
    check("the dots say which board is showing", frame((0, 3)) != frame((1, 3)))
    check("a rotation draws dots a single board does not", frame((0, 3)) != frame(None))
    check("a one-board rotation draws no dots", frame((0, 1)) == frame(None))

    # a line with an empty status array used to raise inside the status block and
    # log "status fetch failed" for a response that was fine
    def no_statuses(url, params=None, timeout=None):
        if url.endswith("/Status"):
            return Resp([{"lineStatuses": []}])
        return board_get(url, params, timeout)
    board.requests = stub(no_statuses)
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            cols, status, ok, why = board.fetch(views[0])
    finally:
        board.requests = stub(board_get)
    check("an empty status list is 'unknown', not a logged failure",
          status is None and not ok and why == "" and "status fetch failed" not in err.getvalue(),
          err.getvalue())


class FakeDT(dt.datetime):
    """dt.datetime.now() reads the fake clock, so the 'three minutes without a good
    fetch' rule can be exercised in milliseconds."""
    @classmethod
    def now(cls, tz=None):
        return cls.fromtimestamp(fake_clock[0])


def run_loop(extra, frames=400, on_frame=None, argv=None, spy=True):
    """Run board.main() against a settings file, with a fake clock that sleep()
    advances, a fake screen, and a spy in place of render. Returns the frames drawn
    as (station, line, rotation, live, updated, now)."""
    shown = []

    def spy_render(W, H, settings, *a, **kw):
        last_shown[0], last_shown[1] = settings["station_name"], settings["line"]
        # positional after settings: cols, status_text, status_ok, status_why, now, updated, live
        shown.append((settings["station_name"], settings["line"], kw.get("rotation"),
                      a[6], a[5], a[4]))
        if on_frame:
            on_frame(len(shown))
        if len(shown) >= frames:
            raise SystemExit("enough")
        return "frame"

    class FakeFB:
        w, h = 1920, 1080

        def show(self, img):
            pass

    write_board(extra)
    fake_clock[0] = 1000.0
    saved = (board.render, board.Framebuffer, board.screen, board.time, board.dt, sys.argv)
    board.Framebuffer, board.screen = FakeFB, None
    if spy:
        board.render = spy_render           # --png needs the real one: it saves the frame
    board.time = types.SimpleNamespace(time=lambda: fake_clock[0],
                                       sleep=lambda s: fake_clock.__setitem__(0, fake_clock[0] + s))
    board.dt = types.SimpleNamespace(datetime=FakeDT, timedelta=dt.timedelta)
    # main() parses sys.argv; a flag meant for this test is not for it
    sys.argv = argv or ["board.py"]
    try:
        board.main()
    except SystemExit:
        pass
    finally:
        board.render, board.Framebuffer, board.screen, board.time, board.dt, sys.argv = saved
    return shown


def test_loop():
    print("\nthe loop")
    fetch_log.clear()
    shown = run_loop(THREE)
    seq = [x[1] for x in shown]
    # a board is drawn twice when its own refresh lands, so squash the repeats
    run = [k for i, k in enumerate(seq) if i == 0 or seq[i - 1] != k]
    check("it visits all three", set(seq) == {"piccadilly", "victoria", "mildmay"}, set(seq))
    check("it cycles in order and comes back round",
          run[:7] == ["piccadilly", "victoria", "mildmay"] * 2 + ["piccadilly"], run[:7])
    check("the dot follows the station",
          all(x[2][0] == {"piccadilly": 0, "victoria": 1, "mildmay": 2}[x[1]] for x in shown))
    check("every frame knows how many boards there are", all(x[2][1] == 3 for x in shown))
    check("only the board on screen is fetched",
          fetch_log and all(line == on_screen[1] for _, line, on_screen in fetch_log),
          [(l, s) for _, l, s in fetch_log if l != s[1]][:3])
    # three boards at 20 s, each on its own 30 s clock: once per 60 s cycle each
    per_line = {}
    for t, line, _ in fetch_log:
        per_line.setdefault(line, []).append(t)
    gaps = [b - a for ts in per_line.values() for a, b in zip(ts, ts[1:])]
    check("each board is refetched once a cycle, not once a slot",
          bool(gaps) and min(gaps) >= 55, (min(gaps) if gaps else None))

    # hand-edited intervals must not stop the loop
    for bad in ({"rotate_seconds": float("inf")}, {"rotate_seconds": "abc"},
                {"refresh_seconds": "30"}, {"refresh_seconds": 0}):
        shown = run_loop(dict(THREE, **bad), frames=40)
        check(f"the loop survives {bad}", len(shown) >= 40, len(shown))

    # a shrinking list resets the index instead of running off the end
    def shrink(n):
        if n == 30:
            write_board({"stations": THREE["stations"][:2], "rotate_seconds": 20}, bump=5)
    shown = run_loop(THREE, frames=120, on_frame=shrink)
    tail = shown[40:]
    check("a settings change mid-run takes effect",
          bool(tail) and all(x[2][1] == 2 and x[1] in ("piccadilly", "victoria") for x in tail),
          sorted({(x[1], x[2]) for x in tail}))


def test_outage():
    print("\nwhen the feed goes away")
    fetch_log.clear()
    outage["lines"] = set()
    start = [None]

    def cut(n):
        # one good round for every board, then every fetch fails
        if n == 12 and start[0] is None:
            outage["lines"] = {"piccadilly", "victoria", "mildmay"}
            start[0] = fake_clock[0]
    try:
        with contextlib.redirect_stderr(io.StringIO()):     # one "fetch failed" per slot
            shown = run_loop(THREE, frames=260, on_frame=cut)
    finally:
        outage["lines"] = set()
    first_dead = {}
    for station, line, rot, live, updated, now in shown:
        if start[0] is not None and not live and line not in first_dead:
            first_dead[line] = now.timestamp() - start[0]
    check("every board admits the outage", set(first_dead) == {"piccadilly", "victoria", "mildmay"},
          first_dead)
    check("within about three minutes of its last good fetch, not one cycle per failure",
          bool(first_dead) and max(first_dead.values()) <= 200, first_dead)
    # the frame right after the failed fetch is the one that says so: there is a dead
    # frame within a second of a failed fetch for that line, not one slot later
    fails = [(t, line) for t, line, _ in fetch_log if t >= (start[0] or 0)]
    dead = [(now.timestamp(), line) for _, line, _, live, _, now in shown if not live]
    prompt = any(abs(tn - tf) < 1.0 and ln == lf for tf, lf in fails for tn, ln in dead)
    check("a failed fetch is redrawn at once", prompt)
    check("the stale 'Updated' time is still the real one",
          all(upd is not None for _, _, _, live, upd, now in shown
              if not live and now.timestamp() >= start[0]))


def test_cli_flags():
    print("\n--explain and --png")
    outage["lines"] = {"victoria"}
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            run_loop(THREE, argv=["board.py", "--explain"])
    finally:
        outage["lines"] = set()
    text = out.getvalue()
    check("--explain prints every board", "Board 1 of 3" in text and "Board 3 of 3" in text)
    check("and carries on past the one that fails", "could not explain this board" in text
          and text.index("could not explain") < text.index("Board 3 of 3"))
    png = os.path.join(TMP, "view.png")
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        run_loop(THREE, argv=["board.py", "--png", png, "--view", "9"], spy=False)
    check("--png --view past the end says which board it drew",
          "board 3 of 3" in out.getvalue() and "only 3 board(s)" in err.getvalue(),
          (out.getvalue(), err.getvalue()))
    check("and wrote the file", os.path.exists(png) and os.path.getsize(png) > 1000)


# ------------------------------------------------------------------ portal.py

HUB = {"id": "HUBHHY", "name": "Highbury & Islington"}
CHILDREN = [
    {"naptanId": "940GZZLUHAI", "commonName": "Highbury & Islington Underground Station",
     "lines": [{"id": "victoria"}]},
    {"naptanId": "910GHGHI", "commonName": "Highbury & Islington Rail Station",
     "lines": [{"id": "mildmay"}, {"id": "windrush"}]},
]


def portal_get(url, params=None, timeout=None):
    if "/StopPoint/Search/" in url:
        q = up.unquote(url.split("/StopPoint/Search/")[1]).lower()
        if "highbury" in q:
            return Resp({"matches": [HUB]})
        if "arsenal" in q:
            return Resp({"matches": [{"id": "940GZZLUASL", "name": "Arsenal Underground Station"}]})
        if q == "victoria":
            # a hub and its own child, which read the same once the suffix is stripped
            return Resp({"matches": [{"id": "940GZZLUVIC", "name": "Victoria Underground Station"},
                                     {"id": "HUBVIC", "name": "Victoria"}]})
        if "king" in q:
            return Resp({"matches": [{"id": "A", "name": "Kings Cross"},
                                     {"id": "B", "name": "Kingsbury"}]})
        if q.startswith("stop"):
            return Resp({"matches": [{"id": "940GZZLU" + q.upper(), "name": q.title()}]})
        return Resp({"matches": []})
    if "/StopPoint/HUBHHY" in url:
        # a real hub answers with both the lines calling there and the child stops.
        # great-northern is in that list and must never be offered: TfL has no
        # arrivals for it, so the board would sit empty for ever.
        return Resp({"lines": [{"id": "victoria"}, {"id": "mildmay"}, {"id": "windrush"},
                               {"id": "great-northern"}],
                     "children": CHILDREN})
    if "/StopPoint/HUBVIC" in url:
        return Resp({"lines": [{"id": "victoria"}, {"id": "district"}],
                     "children": [{"naptanId": "940GZZLUVIC",
                                   "commonName": "Victoria Underground Station",
                                   "lines": [{"id": "victoria"}, {"id": "district"}]}]})
    if "/StopPoint/940GZZLUASL" in url:
        return Resp({"lines": [{"id": "piccadilly"}]})
    if "/StopPoint/940GZZLUSTOP" in url:
        return Resp({"lines": [{"id": "piccadilly"}]})
    if "/Arrivals/" in url:
        return Resp([{"id": "1", "timeToStation": 60}])
    return Resp({})


portal.requests = stub(portal_get)

BASE = {"line": "piccadilly", "station_id": "940GZZLUASL", "station_name": "Arsenal",
        "rows": 5, "refresh_seconds": 30, "brightness": 100}


def write_portal(d):
    with open(portal.SETTINGS_PATH, "w") as f:
        json.dump(d, f)


def refused(fn, *a):
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            fn(*a)
        return ""
    except SystemExit as e:
        return str(e)


def quiet(fn, *a):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        fn(*a)
    return out.getvalue()


def main_with(*argv):
    saved = sys.argv
    sys.argv = ["portal.py", *argv]
    try:
        return refused(portal.main)
    finally:
        sys.argv = saved


def test_shell():
    print("\nediting the rotation from a shell")
    write_portal(dict(BASE))
    quiet(portal.cli_add, "Highbury & Islington", "victoria")
    s = portal.load()
    check("a second board keeps the first",
          [x["station_name"] for x in s["stations"]] == ["Arsenal", "Highbury & Islington"],
          s.get("stations"))
    check("the hub id is swapped for the stop that carries the line",
          s["stations"][1]["station_id"] == "940GZZLUHAI", s["stations"][1])
    check("the single-station keys still name the first board",
          (s["station_name"], s["line"]) == ("Arsenal", "piccadilly"))
    check("the file is readable without sudo afterwards",
          os.stat(portal.SETTINGS_PATH).st_mode & 0o044 == 0o044)

    quiet(portal.cli_add, "Highbury & Islington", "mildmay")
    s = portal.load()
    check("the same station on another line is another board",
          s["stations"][2] == {"line": "mildmay", "station_id": "910GHGHI",
                               "station_name": "Highbury & Islington"}, s["stations"][2])

    check("the same board twice is refused",
          "already" in refused(portal.cli_add, "Highbury & Islington", "victoria"))
    check("an ambiguous name is refused", refused(portal.cli_add, "Kings", "victoria") != "")
    check("an unknown name is refused",
          "Nothing found" in refused(portal.cli_add, "Nowhere", "victoria"))
    check("a line that is not a line is refused",
          "--line must be one of" in refused(portal.cli_add, "Arsenal", "not-a-line"))
    check("a line that does not stop there is refused, whatever the hour",
          "does not stop at Arsenal" in refused(portal.cli_add, "Arsenal", "victoria"))
    check("a hub and its own child are one choice, and the hub wins",
          refused(portal.cli_add, "Victoria", "victoria") == ""
          and portal.load()["stations"][-1]["station_id"] == "940GZZLUVIC",
          portal.load()["stations"][-1])

    quiet(portal.cli_rotate, 45)
    check("the interval saves", portal.load()["rotate_seconds"] == 45)
    quiet(portal.cli_rotate, 2)
    check("too short an interval is clamped", portal.load()["rotate_seconds"] == 5)

    quiet(portal.cli_drop, 2)
    s = portal.load()
    check("dropping one leaves the rest",
          [x["line"] for x in s["stations"]] == ["piccadilly", "mildmay", "victoria"], s["stations"])
    quiet(portal.cli_drop, 3)
    quiet(portal.cli_drop, 1)
    s = portal.load()
    check("dropping back to one clears the rotation", s["stations"] == [], s.get("stations"))
    check("the last one standing becomes the single station",
          (s["station_name"], s["line"], s["station_id"]) ==
          ("Highbury & Islington", "mildmay", "910GHGHI"))
    check("the only board cannot be dropped", "only board" in refused(portal.cli_drop, 1))

    write_portal(dict(BASE))
    for n in range(1, 8):
        quiet(portal.cli_add, f"stop{n}", "piccadilly")
    check("eight boards is the most", len(portal.load()["stations"]) == 8
          and "Eight" in refused(portal.cli_add, "stop8", "piccadilly"))

    # a file edited by hand: no name on an entry, or a number where the list goes
    write_portal(dict(BASE, stations=[dict(BASE), {"line": "victoria", "station_id": "940GZZLUHAI"}]))
    check("--list-stations copes with an entry that has no name",
          "940GZZLUHAI" in quiet(portal.cli_list))
    check("and the page does too", "940GZZLUHAI" in portal.home())
    write_portal(dict(BASE, stations=5))
    check("a number where the list should be reads as one board",
          len(portal.rotation(portal.load())) == 1 and "Arsenal" in portal.home())

    # bad arguments are errors, not a web server on port 80
    check("an empty --add-station is refused",
          "needs a name" in main_with("--add-station", "", "--line", "victoria"))
    check("--line on its own is refused", "goes with" in main_with("--line", "victoria"))

    # TfL unreachable from the shell is one line, not a traceback
    def down(url, params=None, timeout=None):
        raise real_requests.ConnectionError("no route to host")
    portal.requests = stub(down)
    try:
        msg = main_with("--add-station", "Arsenal", "--line", "piccadilly")
    finally:
        portal.requests = stub(portal_get)
    check("TfL down is one sentence from the shell", msg.startswith("TfL did not answer"), msg)


def test_page():
    print("\nediting the rotation from the page")
    write_portal(dict(BASE))
    srv = portal.ThreadingHTTPServer(("127.0.0.1", 0), portal.H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def req(method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.request(method, path, body,
                  {"Content-Type": "application/x-www-form-urlencoded"} if body else {})
        r = c.getresponse()
        out = (r.status, r.read().decode())
        c.close()
        return out

    try:
        code, page = req("GET", "/")
        check("the page loads", code == 200 and "Boards on the screen" in page, code)
        check("one board says so", "One board" in page)

        code, page = req("GET", "/search?q=Highbury&add=1")
        check("an add search stays an add search", "add=1" in page)

        code, page = req("GET", "/pick?id=HUBHHY&name=Highbury&add=1")
        check("the lines at that stop are offered", "victoria" in page and "mildmay" in page)
        check("a line TfL has no arrivals for is not offered", "great-northern" not in page)

        req("POST", "/save", "line=victoria&station_id=HUBHHY&station_name=Highbury&add=1")
        check("adding from the page appends", len(portal.load().get("stations", [])) == 2,
              portal.load().get("stations"))

        code, page = req("GET", "/")
        check("the page lists both", "Arsenal" in page and "Highbury" in page)
        check("two boards get an interval box", "Seconds on each board" in page)
        check("Remove carries the board's identity, not its position",
              'name="station_id" value="940GZZLUHAI"' in page and 'name="i"' not in page)

        req("POST", "/rotate", "rotate_seconds=35")
        check("the interval saves from the page", portal.load()["rotate_seconds"] == 35)
        req("POST", "/rotate", "rotate_seconds=banana")
        check("a non-number interval changes nothing", portal.load()["rotate_seconds"] == 35)

        req("POST", "/drop", "line=victoria&station_id=NOPE")
        check("removing a board that is not there changes nothing", len(portal.load()["stations"]) == 2)
        req("POST", "/drop", "line=piccadilly&station_id=940GZZLUASL")
        check("removing from the page works", portal.load()["stations"] == [])
        left = portal.load()
        req("POST", "/drop", f'line={left["line"]}&station_id={left["station_id"]}')
        check("the page keeps the only board", portal.load()["station_name"] == left["station_name"], left)

        req("POST", "/save", "line=piccadilly&station_id=940GZZLUASL&station_name=Arsenal")
        s = portal.load()
        check("saving without add goes back to one station",
              s["stations"] == [] and s["station_name"] == "Arsenal", s)

        code, page = req("POST", "/save", "line=victoria&station_id=../etc&station_name=x&add=1")
        check("a junk stop id is still refused", "Bad station or line" in page)

        write_portal(dict(BASE, stations=[dict(BASE), {"line": "victoria", "station_id": "940GZZLUHAI"}]))
        code, page = req("GET", "/")
        check("a hand-edited entry with no name does not 500 the page",
              code == 200 and "940GZZLUHAI" in page)
    finally:
        srv.shutdown()


def test_both_ends():
    print("\nthe page and the board agree on the file")
    write_portal(dict(BASE))
    quiet(portal.cli_add, "Highbury & Islington", "victoria")
    quiet(portal.cli_add, "Highbury & Islington", "mildmay")
    # the same bytes the portal just wrote, read back by the board
    board.SETTINGS_PATH = portal.SETTINGS_PATH
    try:
        views = board.station_views(board.Settings())
    finally:
        board.SETTINGS_PATH = os.path.join(TMP, "board-settings.json")
    check("the board draws what the portal saved",
          [(v["station_name"], v["line"]) for v in views] ==
          [("Arsenal", "piccadilly"), ("Highbury & Islington", "victoria"),
           ("Highbury & Islington", "mildmay")],
          [(v["station_name"], v["line"]) for v in views])


if __name__ == "__main__":
    test_views()
    test_fetch_and_render()
    test_loop()
    test_outage()
    test_cli_flags()
    test_shell()
    test_page()
    test_both_ends()
    print()
    if FAILS:
        print(f"{len(FAILS)} FAILED: " + ", ".join(FAILS))
        sys.exit(1)
    print("all passed")
