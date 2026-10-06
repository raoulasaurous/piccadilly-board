#!/usr/bin/env python3
"""Offline tests for the station rotation, in board.py and in portal.py.

    cd pi && python3 test_rotation.py

Every TfL call is stubbed, so this runs anywhere, including in a cloud session
where api.tfl.gov.uk is blocked. That is the point of it: the rotation is the one
part of the board that cannot be checked by looking at the screen, because a
mistake in it shows up as the wrong station a minute later rather than as a
broken frame. Nothing here touches the live settings file.
"""
import datetime as dt
import http.client
import json
import os
import sys
import tempfile
import threading
import types
import urllib.parse as up

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
TMP = tempfile.mkdtemp(prefix="tubeboard-test-")

import board                                                        # noqa: E402
import portal                                                       # noqa: E402

board.SETTINGS_PATH = os.path.join(TMP, "board-settings.json")
portal.SETTINGS_PATH = os.path.join(TMP, "portal-settings.json")

FAILS = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + (f"  {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


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

board_calls = []


def board_get(url, params=None, timeout=None):
    board_calls.append(url)
    parts = url.split("/")
    if url.endswith("/Status"):
        return Resp([{"lineStatuses": [{"statusSeverity": 10,
                                        "statusSeverityDescription": "Good Service"}]}])
    return Resp(list(ARRIVALS.get((parts[4], parts[6]), [])))


board.requests = types.SimpleNamespace(get=board_get)

THREE = {"stations": [
    {"line": "piccadilly", "station_id": "940GZZLUASL", "station_name": "Arsenal"},
    {"line": "victoria", "station_id": "940GZZLUHAI", "station_name": "Highbury & Islington"},
    {"line": "mildmay", "station_id": "910GHGHI", "station_name": "Highbury & Islington"},
], "rotate_seconds": 20}


def write_board(extra):
    d = dict(board.DEFAULTS)
    d.update(extra)
    with open(board.SETTINGS_PATH, "w") as f:
        json.dump(d, f)


def test_views():
    print("which boards to draw")
    write_board({})
    v = board.station_views(board.Settings())
    check("no stations gives one board", len(v) == 1 and v[0]["station_name"] == "Arsenal")

    write_board(THREE)
    v = board.station_views(board.Settings())
    check("three stations give three boards", len(v) == 3, len(v))
    check("they keep their order",
          [x["station_name"] for x in v] == ["Arsenal", "Highbury & Islington",
                                             "Highbury & Islington"])
    check("each keeps its own line", [x["line"] for x in v] == ["piccadilly", "victoria", "mildmay"])
    check("two lines at one station are two boards", board.view_key(v[1]) != board.view_key(v[2]))
    check("the shared settings carry over", all(x["rows"] == 5 for x in v))

    write_board({"stations": [{"line": "victoria"}, {"station_id": "940GZZLUASL"}, "nonsense",
                              {"line": "victoria", "station_id": "940GZZLUHAI",
                               "station_name": "H&I"}]})
    v = board.station_views(board.Settings())
    check("half-written entries are skipped and the good one kept",
          len(v) == 1 and v[0]["station_name"] == "H&I", [x["station_name"] for x in v])

    write_board({"stations": [{"line": "victoria", "station_id": "940GZZLUHAI"}]})
    check("a station with no name falls back to its id",
          board.station_views(board.Settings())[0]["station_name"] == "940GZZLUHAI")


def test_fetch_and_render():
    print("\nfetching and drawing each board")
    write_board(THREE)
    views = board.station_views(board.Settings())
    for v in views:
        cols, status, ok, why = board.fetch(v)
        check(f'{v["station_name"]} on the {v["line"]} line draws two columns '
              f'{[c["label"] for c in cols]}', len(cols) == 2)
    check("every line was asked for separately",
          all(any(f"/Line/{l}/Arrivals" in c for c in board_calls)
              for l in ("piccadilly", "victoria", "mildmay")))

    at = dt.datetime(2026, 10, 6, 19, 30)
    def frame(rotation):
        return list(board.render(640, 360, views[0], board.fetch(views[0])[0], "Good Service",
                                 True, "", at, at, True, rotation=rotation, ss=1).getdata())
    check("the dots say which board is showing", frame((0, 3)) != frame((1, 3)))
    check("a rotation draws dots a single board does not", frame((0, 3)) != frame(None))
    check("a one-board rotation draws no dots", frame((0, 1)) == frame(None))


def test_loop():
    print("\nthe loop")

    class Clock:
        """sleep() moves the clock, so the rotation is deterministic and instant."""
        def __init__(self):
            self.t = 1000.0

        def time(self):
            return self.t

        def sleep(self, s):
            self.t += s

    shown = []

    def spy_render(W, H, settings, *a, **kw):
        shown.append((settings["station_name"], settings["line"], kw.get("rotation")))
        if len(shown) > 400:
            raise SystemExit("enough")
        return "frame"

    class FakeFB:
        w, h = 1920, 1080

        def show(self, img):
            pass

    write_board(THREE)
    real_render, real_fb, real_screen, real_time = (board.render, board.Framebuffer,
                                                    board.screen, board.time)
    board.render, board.Framebuffer, board.screen = spy_render, FakeFB, None
    clock = Clock()
    board.time = types.SimpleNamespace(time=clock.time, sleep=clock.sleep)
    try:
        board.main()
    except SystemExit:
        pass
    finally:
        board.render, board.Framebuffer, board.screen, board.time = (real_render, real_fb,
                                                                     real_screen, real_time)

    seq = [x[1] for x in shown]
    # a board is drawn twice when its own refresh lands, so squash the repeats
    run = [k for i, k in enumerate(seq) if i == 0 or seq[i - 1] != k]
    check("it visits all three", set(seq) == {"piccadilly", "victoria", "mildmay"}, set(seq))
    check("it cycles in order and comes back round",
          run[:7] == ["piccadilly", "victoria", "mildmay"] * 2 + ["piccadilly"], run[:7])
    check("the dot follows the station",
          all(x[2][0] == {"piccadilly": 0, "victoria": 1, "mildmay": 2}[x[1]] for x in shown))
    check("every frame knows how many boards there are", all(x[2][1] == 3 for x in shown))


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
        if "king" in q:
            return Resp({"matches": [{"id": "A", "name": "Kings Cross"},
                                     {"id": "B", "name": "Kingsbury"}]})
        return Resp({"matches": []})
    if "/StopPoint/HUBHHY" in url:
        # a real hub answers with both the lines calling there and the child stops.
        # great-northern is in that list and must never be offered: TfL has no
        # arrivals for it, so the board would sit empty for ever.
        return Resp({"lines": [{"id": "victoria"}, {"id": "mildmay"}, {"id": "windrush"},
                               {"id": "great-northern"}],
                     "children": CHILDREN})
    if "/Arrivals/" in url:
        return Resp([{"id": "1", "timeToStation": 60}])
    return Resp({})


portal.requests = types.SimpleNamespace(get=portal_get)

BASE = {"line": "piccadilly", "station_id": "940GZZLUASL", "station_name": "Arsenal",
        "rows": 5, "refresh_seconds": 30, "brightness": 100}


def write_portal(d):
    with open(portal.SETTINGS_PATH, "w") as f:
        json.dump(d, f)


def refused(fn, *a):
    try:
        fn(*a)
        return ""
    except SystemExit as e:
        return str(e)


def test_shell():
    print("\nediting the rotation from a shell")
    write_portal(dict(BASE))
    portal.cli_add("Highbury & Islington", "victoria")
    s = portal.load()
    check("a second board keeps the first",
          [x["station_name"] for x in s["stations"]] == ["Arsenal", "Highbury & Islington"],
          s.get("stations"))
    check("the hub id is swapped for the stop that carries the line",
          s["stations"][1]["station_id"] == "940GZZLUHAI", s["stations"][1])
    check("the single-station keys still name the first board",
          (s["station_name"], s["line"]) == ("Arsenal", "piccadilly"))

    portal.cli_add("Highbury & Islington", "mildmay")
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

    portal.cli_rotate(45)
    check("the interval saves", portal.load()["rotate_seconds"] == 45)
    portal.cli_rotate(2)
    check("too short an interval is clamped", portal.load()["rotate_seconds"] == 5)

    portal.cli_drop(2)
    s = portal.load()
    check("dropping one leaves the rest",
          [x["line"] for x in s["stations"]] == ["piccadilly", "mildmay"], s["stations"])
    portal.cli_drop(1)
    s = portal.load()
    check("dropping back to one clears the rotation", s["stations"] == [], s.get("stations"))
    check("the last one standing becomes the single station",
          (s["station_name"], s["line"], s["station_id"]) ==
          ("Highbury & Islington", "mildmay", "910GHGHI"))
    check("the only board cannot be dropped", "only board" in refused(portal.cli_drop, 1))


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

        req("POST", "/rotate", "rotate_seconds=35")
        check("the interval saves from the page", portal.load()["rotate_seconds"] == 35)
        req("POST", "/rotate", "rotate_seconds=banana")
        check("a non-number interval changes nothing", portal.load()["rotate_seconds"] == 35)

        req("POST", "/drop", "i=0")
        check("removing from the page works", portal.load()["stations"] == [])
        # the hub lookup renames the stop to what TfL calls it, so compare with
        # whatever is actually left rather than with what was typed
        left = portal.load()["station_name"]
        req("POST", "/drop", "i=0")
        check("the page keeps the only board", portal.load()["station_name"] == left, left)

        req("POST", "/save", "line=piccadilly&station_id=940GZZLUASL&station_name=Arsenal")
        s = portal.load()
        check("saving without add goes back to one station",
              s["stations"] == [] and s["station_name"] == "Arsenal", s)

        code, page = req("POST", "/save", "line=victoria&station_id=../etc&station_name=x&add=1")
        check("a junk stop id is still refused", "Bad station or line" in page)
    finally:
        srv.shutdown()


def test_both_ends():
    print("\nthe page and the board agree on the file")
    write_portal(dict(BASE))
    portal.cli_add("Highbury & Islington", "victoria")
    portal.cli_add("Highbury & Islington", "mildmay")
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
    test_shell()
    test_page()
    test_both_ends()
    print()
    if FAILS:
        print(f"{len(FAILS)} FAILED: " + ", ".join(FAILS))
        sys.exit(1)
    print("all passed")
