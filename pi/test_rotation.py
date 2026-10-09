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
import time
import types
import urllib.parse as up

import requests as real_requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
TMP = tempfile.mkdtemp(prefix="tubeboard-test-")
# a run leaves a few small files; on the Pi that is the SD card, so clean up
atexit.register(shutil.rmtree, TMP, ignore_errors=True)

import board                                                        # noqa: E402
import netdiag                                                      # noqa: E402
import portal                                                       # noqa: E402
import rail                                                         # noqa: E402

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

# The National Rail feed, as the Rail Data Marketplace sends it, for Drayton Park.
# The shape the live feed sent for DYP on 7 Oct 2026 (field names, the "Value"
# notice, generatedAt with its offset and seven decimals), with the trains made up
# to cover the cases: two the same minute, a cancellation, "Delayed" with and
# without its time gone, and a via.
RAIL_BOARD = {
    "locationName": "Drayton Park", "crs": "DYP", "generatedAt": "2026-10-07T08:10:00.0000000+01:00",
    "filterType": "to", "areServicesAvailable": True, "platformAvailable": True,
    "nrccMessages": [{"Value": "<p>Lifts at&nbsp;Highbury are out of order. See the "
                               "<a href=\"https://www.nationalrail.co.uk/\">National Rail website</a>.</p>"}],
    "trainServices": [
        {"std": "08:12", "etd": "On time", "platform": "2", "operator": "Great Northern",
         "operatorCode": "GN", "isCancelled": False, "serviceType": "train", "length": 6,
         "serviceID": "a1", "origin": [{"locationName": "Welwyn Garden City", "crs": "WGC"}],
         "destination": [{"locationName": "Moorgate", "crs": "MOG", "assocIsCancelled": False}]},
        # a second service to Moorgate expected the same minute: two trains, not one
        {"std": "08:11", "etd": "08:12", "platform": "2", "operator": "Great Northern",
         "operatorCode": "GN", "isCancelled": False,
         "serviceID": "a0", "destination": [{"locationName": "Moorgate", "crs": "MOG"}]},
        {"std": "08:14", "etd": "08:17", "platform": "1", "operator": "Great Northern",
         "operatorCode": "GN", "isCancelled": False,
         "serviceID": "b2", "destination": [{"locationName": "Welwyn Garden City", "crs": "WGC"}]},
        {"std": "08:20", "etd": "Cancelled", "platform": "1", "isCancelled": True,
         "operatorCode": "GN",
         "serviceID": "c3", "destination": [{"locationName": "Hertford North", "crs": "HFN"}]},
        # "Delayed" with no estimate, and its timetable time has gone: still a train
        {"std": "08:05", "etd": "Delayed", "platform": "1", "serviceID": "e5", "isCancelled": False,
         "destination": [{"locationName": "Hertford North", "crs": "HFN"}]},
        # the feed's via text already carries the word (live: "via Hertford North")
        {"std": "08:22", "etd": "Delayed", "platform": "1", "serviceID": "d4", "isCancelled": False,
         "destination": [{"locationName": "Stevenage", "crs": "SVG", "via": "via Hertford North"}]},
    ]}
rail_calls = []


def rail_get(url, params=None, headers=None, timeout=None):
    rail_calls.append((url, dict(headers or {})))
    r = Resp(dict(RAIL_BOARD) if (headers or {}).get("x-apikey") == "k-test" else {})
    r.status_code = 200 if (headers or {}).get("x-apikey") == "k-test" else 403
    return r


rail.requests = stub(rail_get)
RAIL_STATION = {"source": "national-rail", "line": "great-northern", "station_id": "DYP",
                "station_name": "Drayton Park"}

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
    check("rows is coerced and clamped: a quoted number, a zero, a hundred",
          (views_for({"rows": "5"})[0]["rows"], views_for({"rows": 0})[0]["rows"],
           views_for({"rows": 99})[0]["rows"], views_for({"rows": "lots"})[0]["rows"]) == (5, 1, 8, 4))
    with open(board.SETTINGS_PATH, "w") as f:
        f.write("[1, 2, 3]")
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        st = board.Settings()
    check("a file that is a list, not an object, keeps the defaults and says so",
          st["station_name"] == "Arsenal" and "not an object" in err.getvalue(), err.getvalue())


def test_rail_board():
    print("\na National Rail board")
    v = views_for({"stations": THREE["stations"] + [RAIL_STATION], "rail_api_key": "k-test"})
    check("the rail station is a fourth board with its own source",
          len(v) == 4 and v[3]["source"] == "national-rail" and v[3]["line"] == "great-northern")
    check("the cache tells it apart from a TfL board with the same ids",
          board.view_key(v[3]) != board.view_key(dict(v[3], source="tfl")))
    saved = fake_clock[0]
    try:
        # the stub board is for 08:10; predictions are relative to now
        board.dt = types.SimpleNamespace(datetime=FakeDT, timedelta=dt.timedelta)
        fake_clock[0] = dt.datetime(2026, 10, 7, 8, 10).timestamp()
        cols, status, ok, why = board.fetch(v[3])
    finally:
        board.dt = dt
        fake_clock[0] = saved
    check("the feed was asked with the key in the header",
          bool(rail_calls) and rail_calls[-1][1].get("x-apikey") == "k-test"
          and "/GetDepartureBoard/DYP" in rail_calls[-1][0], rail_calls[-1:])
    check("and under the board's own name: the gateway answers python-requests with a 403 page",
          rail_calls[-1][1].get("User-Agent", "").startswith("tubeboard/"), rail_calls[-1][1])
    check("the default path is the one the marketplace publishes",
          "/1010-live-departure-board-dep1_2/LDBWS/api/20220120/" in rail_calls[-1][0], rail_calls[-1][0])
    # the feed's clock is read on the feed's own zone, whatever the Pi was set up in
    saved_tz = os.environ.get("TZ")
    try:
        os.environ["TZ"] = "UTC"; time.tzset()
        on_utc = rail.feed_time(RAIL_BOARD)
        os.environ["TZ"] = "Europe/London"; time.tzset()
        on_london = rail.feed_time(RAIL_BOARD)
    finally:
        if saved_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = saved_tz
        time.tzset()
    check("the feed's own clock is the same on a UTC Pi and a London one",
          on_utc == on_london == dt.datetime(2026, 10, 7, 8, 10, 0, 0), (on_utc, on_london))
    check("a notice reads as text: tags and entities gone",
          rail.messages(RAIL_BOARD) == ["Lifts at Highbury are out of order. See the National Rail website ."],
          rail.messages(RAIL_BOARD))
    check("two columns, named by the compass for the line",
          [c["label"] for c in cols] == ["SOUTHBOUND", "NORTHBOUND"], [c["label"] for c in cols])
    check("the southbound column says where it goes",
          cols[0]["towards"] == "Moorgate" and cols[0]["rows"][0] == ("Moorgate", 120), cols[0])
    check("two services to Moorgate in the same minute are two rows", len(cols[0]["rows"]) == 2, cols[0]["rows"])
    check("the cancelled train is not drawn; the overdue one says delayed; via is not doubled",
          cols[1]["rows"] == [("Hertford North", None), ("Welwyn Garden City", 420),
                              ("Stevenage via Hertford North", 720)], cols[1]["rows"])
    check("a row with no minutes is drawn as delayed", board.label_mins(None) == "delayed")
    check("the status line is TfL's for the operator",
          status == "Good Service" and ok and any("/Line/great-northern/Status" in u for u in board_calls))
    at = dt.datetime(2026, 10, 7, 8, 10)
    img = board.render(1920, 1080, v[3], cols, status, ok, why, at, at, True, rotation=(3, 4))
    img.save(os.path.join(TMP, "rail.png"))
    check("it draws", img.size == (1920, 1080))
    check("the header knows the operator",
          board.LINE_NAMES["great-northern"] == "Great Northern"
          and board.NETWORK["great-northern"] == "NATIONAL RAIL")

    # no key: the board says what is missing instead of asking with nothing
    v = views_for({"stations": THREE["stations"] + [RAIL_STATION]})
    try:
        board.fetch(v[3])
        check("no key is a clear refusal", False)
    except Exception as e:                      # noqa: BLE001
        check("no key is a clear refusal", "raildata.org.uk" in str(e), e)
    v = views_for({"stations": THREE["stations"] + [RAIL_STATION], "rail_api_key": "wrong"})
    try:
        board.fetch(v[3])
        check("a refused key says so", False)
    except Exception as e:                      # noqa: BLE001
        check("a refused key says so", "refused the key" in str(e), e)

    # on the screen a missing or refused key is named as such, not as the feed being down
    network.update(state="CONNECTED", ssid="HomeNet", probe=204, asked=0)
    diags.clear()
    with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
        run_loop({"stations": [RAIL_STATION], "rail_api_key": "wrong"}, frames=8)
    check("a refused key is the diagnosis, and the network is not blamed",
          any(d and d[0] == "Rail key needed" for d, _ in diags) and network["asked"] == 0,
          (diags[:3], network["asked"]))
    # a feed that answers 404 for the stop says so too
    def nope(url, params=None, timeout=None):
        if "/Arrivals/" in url:
            r = real_requests.Response(); r.status_code = 404; r.url = url + "?app_key=SECRET"
            raise real_requests.exceptions.HTTPError("404 Client Error", response=r)
        return board_get(url, params, timeout)
    board.requests = stub(nope); diags.clear()
    try:
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            run_loop({}, frames=8)
    finally:
        board.requests = stub(board_get)
    check("a 404 for the stop is named, and the key stays off the wall",
          any(d and d[0].endswith("does not know this stop") and "SECRET" not in d[1] for d, _ in diags), diags[:3])
    # an empty rail board at night still names its two columns
    def empty_rail(url, params=None, headers=None, timeout=None):
        r = Resp({"locationName": "Drayton Park", "crs": "DYP", "trainServices": None}); r.status_code = 200
        return r
    rail.requests = stub(empty_rail)
    try:
        cols = board.fetch(views_for({"stations": [RAIL_STATION], "rail_api_key": "k-test"})[0])[0]
    finally:
        rail.requests = stub(rail_get)
    check("no trains at 02:00 still says SOUTHBOUND and NORTHBOUND, not DEPARTURES twice",
          [c["label"] for c in cols] == ["SOUTHBOUND", "NORTHBOUND"], [c["label"] for c in cols])

    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        run_loop({"stations": [RAIL_STATION], "rail_api_key": "k-test"}, argv=["board.py", "--explain"])
    text = out.getvalue()
    check("--explain shows the feed's own account and the columns",
          "Drayton Park [DYP]" in text and "Cancelled" in text and "The board draws 2 column(s)" in text,
          text[:400])
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        run_loop({"stations": [RAIL_STATION]}, argv=["board.py", "--explain"])
    check("--explain with no key says how to get one", "raildata.org.uk" in out.getvalue())


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

    # a part closure with eastbound trains to two destinations: one direction, kept
    # as one column, and the westbound column stays and says it is empty. Splitting
    # them by destination instead lost the westbound column.
    closure = [{"platformName": "Eastbound - Platform 1", "direction": "inbound", "towards": "Cockfosters",
                "destinationName": "Cockfosters Underground Station", "timeToStation": 120, "id": "1", "vehicleId": "1"},
               {"platformName": "Eastbound - Platform 1", "direction": "inbound", "towards": "Arnos Grove",
                "destinationName": "Arnos Grove Underground Station", "timeToStation": 400, "id": "3", "vehicleId": "3"}]
    groups = board.group(closure, board.DEFAULTS["columns"])
    check("one stated direction to two destinations is one column, not two",
          len(groups) == 1 and len(groups[0][1]) == 2, [(c, len(m)) for c, m in groups])
    saved_get = board.requests
    board.requests = stub(lambda url, params=None, timeout=None: Resp(closure) if "/Arrivals/" in url
                          else Resp([{"lineStatuses": [{"statusSeverity": 14, "statusSeverityDescription": "Part Closure"}]}]))
    try:
        cols = board.fetch(views[0])[0]
    finally:
        board.requests = saved_get
    check("and the board keeps the westbound column, empty",
          [c["label"] for c in cols] == ["EASTBOUND", "WESTBOUND"] and cols[1]["rows"] == []
          and len(cols[0]["rows"]) == 2, cols)

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


network = {"state": "CONNECTED", "ssid": "HomeNet", "probe": 204, "asked": 0}
setup_frames = []        # (state, ssid, hotspot) each time the setup screen was drawn
diags = []               # the diag kwarg of each board frame


def fake_diagnose(hotspot, source="tfl"):
    network["asked"] += 1
    v = netdiag.verdict(network["state"], network["ssid"], network["probe"], hotspot, source)
    return v, network["state"], network["ssid"]


def run_loop(extra, frames=400, on_frame=None, argv=None, spy=True, address=None):
    """Run board.main() against a settings file, with a fake clock that sleep()
    advances, a fake screen, a spy in place of render and a scripted network.
    Returns the frames drawn as (station, line, rotation, live, updated, now)."""
    shown = []

    def spy_render(W, H, settings, *a, **kw):
        last_shown[0], last_shown[1] = settings["station_name"], settings["line"]
        # positional after settings: cols, status_text, status_ok, status_why, now, updated, live
        shown.append((settings["station_name"], settings["line"], kw.get("rotation"),
                      a[6], a[5], a[4]))
        diags.append((kw.get("diag"), kw.get("address")))
        if on_frame:
            on_frame(len(shown))
        if len(shown) >= frames:
            raise SystemExit("enough")
        return "frame"

    def spy_setup(W, H, state, ssid, hotspot, now, **kw):
        setup_frames.append((state, ssid, hotspot))
        shown.append(("(setup)", "(setup)", None, False, None, now))
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
    saved = (board.render, board.render_setup, board.Framebuffer, board.screen, board.time,
             board.dt, sys.argv, netdiag.diagnose, netdiag.address, netdiag.hotspot_name)
    board.Framebuffer, board.screen = FakeFB, None
    netdiag.diagnose = fake_diagnose
    netdiag.address = address or (lambda: ("tubeboard", "192.168.1.23"))
    netdiag.hotspot_name = lambda *a, **k: "TubeBoard-setup"
    if spy:
        board.render = spy_render           # --png needs the real one: it saves the frame
        board.render_setup = spy_setup
    board.time = types.SimpleNamespace(time=lambda: fake_clock[0], monotonic=lambda: fake_clock[0],
                                       sleep=lambda s: fake_clock.__setitem__(0, fake_clock[0] + s))
    board.dt = types.SimpleNamespace(datetime=FakeDT, timedelta=dt.timedelta)
    # main() parses sys.argv; a flag meant for this test is not for it
    sys.argv = argv or ["board.py"]
    try:
        board.main()
    except SystemExit:
        pass
    finally:
        (board.render, board.render_setup, board.Framebuffer, board.screen, board.time,
         board.dt, sys.argv, netdiag.diagnose, netdiag.address, netdiag.hotspot_name) = saved
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


def test_wifi():
    print("\nwhen there is no WiFi, or a bad one")
    # the table that turns the checks into words is covered in its own right
    V = netdiag.verdict
    check("hotspot: join and open", V("HOTSPOT", "", None)[0] == "No WiFi"
          and "TubeBoard-setup" in V("HOTSPOT", "", None)[1] and "10.41.0.1" in V("HOTSPOT", "", None)[1])
    check("connecting names the network", V("CONNECTING", "HomeNet", None) == ("Joining WiFi", "Connecting to HomeNet"))
    check("a sign-in page is told apart from no internet",
          V("CONNECTED", "Cafe", 302)[0] == "WiFi needs sign-in" and V("CONNECTED", "Cafe", "dns")[0] == "No internet"
          and V("CONNECTED", "Cafe", "noroute")[0] == "No internet")
    check("internet fine means the feed is at fault, and the feed is named",
          V("CONNECTED", "HomeNet", 204)[0] == "Transport for London not answering"
          and V("CONNECTED", "HomeNet", 204, source="national-rail")[0] == "National Rail not answering")
    real_run = netdiag._run
    try:
        one_shot = "Host tubeboard.local on comitup version 1.47\n'single' mode\n%s state\n"
        netdiag._run = lambda args, timeout=5: {"comitup-cli": one_shot % "HOTSPOT"}.get(args[0], "")
        check("comitup's one-shot word is taken first", netdiag.state() == ("HOTSPOT", ""))
        netdiag._run = lambda args, timeout=5: {"comitup-cli": one_shot % "CONNECTING",
                                                "nmcli": "Mums\\:House:802-11-wireless\n"}.get(args[0], "")
        check("connecting, with the ssid from NetworkManager, unescaped",
              netdiag.state() == ("CONNECTING", "Mums:House"), netdiag.state())
        netdiag._run = lambda args, timeout=5: {"comitup-cli": "State: HOTSPOT\nConnection: comitup-680\n"}.get(args[0], "")
        check("and the interactive spelling still reads", netdiag.state() == ("HOTSPOT", ""))
        netdiag._run = lambda args, timeout=5: {"ip": "3: wlan0 inet 10.41.0.1/24 scope global wlan0"}.get(args[0], "")
        check("the hotspot address alone means HOTSPOT", netdiag.state() == ("HOTSPOT", ""))
        netdiag._run = lambda args, timeout=5: {"nmcli": "connected\n" if "general" in args else "HomeNet:802-11-wireless\n"}.get(args[0], "")
        check("NetworkManager covers the rest", netdiag.state() == ("CONNECTED", "HomeNet"))
        netdiag._run = lambda args, timeout=5: ""
        check("nothing answering is UNKNOWN, not a crash", netdiag.state() == ("UNKNOWN", ""))
    finally:
        netdiag._run = real_run
    conf = os.path.join(TMP, "comitup.conf")
    with open(conf, "w") as f:
        f.write("# comment\nap_name: Jamies-Board\nweb_service: x\n")
    check("the hotspot name comes from comitup's config", netdiag.hotspot_name(conf) == "Jamies-Board")
    check("and has a default when there is none", netdiag.hotspot_name(os.path.join(TMP, "nope")) == "TubeBoard-setup")
    check("no password unless comitup sets one", netdiag.hotspot_password(conf) == "")
    with open(conf, "a") as f:
        f.write("ap_password: letmein\n")
    check("and the password when it does", netdiag.hotspot_password(conf) == "letmein")

    # the screen: no WiFi at boot shows the setup screen, not "Starting up" for ever
    network.update(state="HOTSPOT", ssid="", probe=None, asked=0)
    setup_frames.clear()
    outage["lines"] = {"piccadilly", "victoria", "mildmay"}
    try:
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            shown = run_loop(THREE, frames=30)
    finally:
        outage["lines"] = set()
    check("a failed fetch asks the network what is wrong", network["asked"] >= 1)
    check("in hotspot mode the screen is the setup screen",
          setup_frames and all(st == "HOTSPOT" and hs == "TubeBoard-setup" for st, _, hs in setup_frames),
          setup_frames[:2])
    elapsed = shown[-1][5].timestamp() - shown[0][5].timestamp()
    check("and it is asked again only every half minute, not every frame",
          network["asked"] <= elapsed / 30 + 1, (network["asked"], elapsed))

    # a join in progress: the screen says it took
    network.update(state="CONNECTING", ssid="HomeNet", probe=None, asked=0)
    setup_frames.clear()
    outage["lines"] = {"piccadilly", "victoria", "mildmay"}
    try:
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            run_loop(THREE, frames=12)
    finally:
        outage["lines"] = set()
    check("while joining, the screen says WIFI OK and which network",
          setup_frames and setup_frames[-1][:2] == ("CONNECTING", "HomeNet"), setup_frames[-1:])

    # on WiFi, no internet: the board stays a board and the footer says what is wrong
    network.update(state="CONNECTED", ssid="HomeNet", probe="dns", asked=0)
    setup_frames.clear(); diags.clear()
    outage["lines"] = {"piccadilly", "victoria", "mildmay"}
    try:
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            run_loop(THREE, frames=12)
    finally:
        outage["lines"] = set()
    check("no internet is a board with a diagnosis in the footer, not the setup screen",
          not setup_frames and any(d and d[0] == "No internet" for d, _ in diags), diags[:3])

    # and once a fetch works, the diagnosis is gone
    network.update(state="CONNECTED", ssid="HomeNet", probe=204, asked=0)
    diags.clear()
    with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
        run_loop(THREE, frames=20)
    check("a good fetch clears it", all(d is None for d, _ in diags[3:]), diags[3:6])
    check("the address card shows for the first minutes after boot",
          any(a == ("tubeboard", "192.168.1.23") for _, a in diags), diags[:3])
    diags.clear()
    with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
        shown = run_loop(THREE, frames=120)
    times = [(s[5] - shown[0][5]).total_seconds() for s in shown]
    carded = [t for t, (_, a) in zip(times, diags) if a]
    check("for one minute", carded and max(carded) < 60 and any(a is None for _, a in diags[-10:]),
          (carded[:1], carded[-1:]))
    # the minute starts when the board has an IP, not at boot: a slow WiFi join keeps it
    diags.clear()
    late = lambda: ("tubeboard", "192.168.1.23" if fake_clock[0] >= 1300 else "")
    with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
        shown = run_loop(THREE, frames=200, address=late)
    times = [(s[5] - shown[0][5]).total_seconds() for s in shown]
    carded = [t for t, (_, a) in zip(times, diags) if a]
    check("an IP that arrives late still gets its minute",
          carded and 300 <= carded[0] < 312 and 45 <= carded[-1] - carded[0] < 60, (carded[:1], carded[-1:]))

    # the frames themselves draw
    at = dt.datetime(2026, 10, 6, 19, 30)
    img = board.render_setup(1920, 1080, "HOTSPOT", "", "TubeBoard-setup", at)
    check("the setup screen draws at 1080p", img.size == (1920, 1080))
    img.save(os.path.join(TMP, "setup.png"))
    img2 = board.render_setup(1920, 1080, "CONNECTING", "HomeNet", "TubeBoard-setup", at)
    check("and the joining screen", img2.size == (1920, 1080) and img2.tobytes() != img.tobytes())
    img3 = board.render_setup(1920, 1080, "HOTSPOT", "", "TubeBoard-setup", at, password="letmein")
    check("a hotspot with a password draws differently", img3.tobytes() != img.tobytes())
    saved_qr = board.qrcode
    try:
        board.qrcode = None
        check("no QR library still gives a setup screen",
              board.render_setup(1920, 1080, "HOTSPOT", "", "TubeBoard-setup", at).size == (1920, 1080))
    finally:
        board.qrcode = saved_qr
    v = views_for(THREE)
    cols = board.fetch(v[0])[0]
    plain = board.render(960, 540, v[0], cols, None, False, "", at, at, False, ss=1).tobytes()
    told = board.render(960, 540, v[0], cols, None, False, "", at, at, False,
                        diag=("No internet", "Connected on HomeNet, but nothing resolves"), ss=1).tobytes()
    card = board.render(960, 540, v[0], cols, "Good Service", True, "", at, at, True,
                        address=("tubeboard", "192.168.1.23"), ss=1).tobytes()
    check("the footer changes with a diagnosis and with the address card",
          plain != told and card != board.render(960, 540, v[0], cols, "Good Service", True, "", at, at, True, ss=1).tobytes())
    long_dest = [{"platformName": "Eastbound - Platform 1", "direction": "inbound", "towards": "Hainault via Newbury Park",
                  "destinationName": "Hainault Underground Station", "timeToStation": 420, "id": "9", "vehicleId": "9"}]
    rows = [(board.row_text(long_dest[0]), 420)]
    img = board.render(960, 540, v[0], [{"label": "EASTBOUND", "towards": "", "rows": rows}] * 2,
                       "Good Service", True, "", at, at, True, ss=1)
    check("a long destination is clipped rather than run into the minutes", img.size == (960, 540))
    d = board.ImageDraw.Draw(img)
    # the heading is the direction alone: no "towards", however long or short
    far = "Heathrow Terminals 2 & 3 via Hounslow West, Hatton Cross and the long way round"
    u = 1920 / 100.0
    with_tow = board.render(1920, 1080, v[0], [{"label": "NORTHBOUND", "towards": far, "rows": rows}] * 2,
                            "Good Service", True, "", at, at, True, ss=1)
    without = board.render(1920, 1080, v[0], [{"label": "NORTHBOUND", "towards": "", "rows": rows}] * 2,
                           "Good Service", True, "", at, at, True, ss=1)
    check("the column heading draws no 'towards' text", with_tow.tobytes() == without.tobytes())
    check("clip never leaves four dots",
          board.clip(d, "No service between Hyde Park Corner and Acton Town. More words here to make it long.",
                     board.font("regular", 18), 300).count("....") == 0)


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
          s["stations"][2] == {"source": "tfl", "line": "mildmay", "station_id": "910GHGHI",
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

    # a National Rail board: by code, with the key saved once
    write_portal(dict(BASE))
    msg = quiet(portal.cli_add_rail, "dyp", "great-northern")
    s = portal.load()
    check("a rail board is added by its code, named by the code until there is a key",
          s["stations"][1] == {"source": "national-rail", "line": "great-northern",
                               "station_id": "DYP", "station_name": "DYP"}, s.get("stations"))
    check("and the shell says the key is missing", "rail-key" in msg)
    check("--list-stations says so too", "No rail key" in quiet(portal.cli_list))
    quiet(portal.cli_rail_key, "k-test")
    check("the key saves", portal.load()["rail_api_key"] == "k-test")
    quiet(portal.cli_drop, 2)
    quiet(portal.cli_add_rail, "DYP", "great-northern")
    s = portal.load()
    check("with a key the feed names the station",
          s["stations"][1]["station_name"] == "Drayton Park", s["stations"][1])
    check("the single-station keys stay on the TfL board",
          (s["source"], s["station_id"]) == ("tfl", "940GZZLUASL"))
    write_portal(dict(BASE, rail_api_key="k-test"))
    quiet(portal.cli_add_rail, "DYP", "great-northern")
    quiet(portal.cli_drop, 1)
    s = portal.load()
    check("a lone rail board is the single station, source and all",
          s["stations"] == [] and s["source"] == "national-rail" and s["station_id"] == "DYP", s)
    check("a bad code is refused", "three-letter" in refused(portal.cli_add_rail, "Drayton", "great-northern"))
    check("a tube line is not a rail operator",
          "--line must be one of" in refused(portal.cli_add_rail, "DYP", "victoria"))
    check("--line alone is refused for rail too", "goes with" in main_with("--line", "great-northern"))

    write_portal(dict(BASE, stations=[dict(BASE), {"line": "victoria", "station_id": "940GZZLUHAI", "station_name": "H&I"}]))
    check("the page says 30 s each when the file does not say, as the board does",
          "30 s each" in portal.home() and "2 boards, 30 s each" in quiet(portal.cli_list))
    write_portal(dict(BASE, stations=[dict(BASE, columns=[{"direction": "inbound", "label": "To town", "towards": ""},
                                                             {"direction": "outbound", "label": "Away", "towards": ""}]),
                                       {"line": "victoria", "station_id": "940GZZLUHAI", "station_name": "H&I"}]))
    quiet(portal.cli_rotate, 40)
    check("a hand-set per-station columns survives a save",
          portal.load()["stations"][0].get("columns", [{}])[0].get("label") == "To town")
    with open(portal.SETTINGS_PATH, "w") as f:
        f.write('{"line": "piccadilly", "station_id": "940GZZLUASL",}')
    check("a broken file is refused from the shell, in one line",
          "not valid JSON" in main_with("--list-stations") and "not valid JSON" in main_with("--rotate", "30"))
    with open(portal.SETTINGS_PATH) as f:
        check("and nothing was written over it", f.read().endswith(",}"))

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

        write_portal(dict(BASE))
        code, page = req("GET", "/")
        check("the page offers National Rail", "National Rail" in page and 'name="crs"' in page)
        req("POST", "/save-rail", "crs=dyp&line=great-northern&rail_api_key=k-test")
        s = portal.load()
        check("adding a rail board from the page works and keeps the key",
              s.get("rail_api_key") == "k-test" and len(s["stations"]) == 2
              and s["stations"][1]["station_name"] == "Drayton Park", s)
        code, page = req("POST", "/save-rail", "crs=dy&line=great-northern")
        check("a bad code from the page is refused", "three letters" in page)
        # the key, typed later for a station that is already there, is kept and the
        # station gets its name
        write_portal(dict(BASE, stations=[dict(BASE), {"source": "national-rail", "line": "great-northern",
                                                        "station_id": "DYP", "station_name": "DYP"}]))
        code, page = req("POST", "/save-rail", "crs=DYP&line=great-northern&rail_api_key=k-test")
        s = portal.load()
        check("a key typed for a station already on the rotation is saved, and names it",
              code in (200, 303) and s.get("rail_api_key") == "k-test"
              and s["stations"][1]["station_name"] == "Drayton Park" and len(s["stations"]) == 2, s)
        code, page = req("POST", "/save-rail", "crs=DYP&line=great-northern&rail_api_key=k-test")
        check("and the same again is the duplicate it is", "already on the rotation" in page)
        code, page = req("GET", "/")
        check("the list says which board is National Rail", "(National Rail)" in page)

        with open(portal.SETTINGS_PATH, "w") as f:
            f.write("{broken")
        code, page = req("GET", "/")
        check("a broken file is a readable error page, not a form that would overwrite it",
              code == 500 and "not valid JSON" in page, (code, page[-200:]))
    finally:
        srv.shutdown()


def test_forget_wifi():
    print("\nforgetting the WiFi")
    write_portal(dict(BASE))            # the page test leaves its file deliberately broken
    calls = []
    saved_nm, saved_hs = portal._nm, portal.hotspot_name

    def fake_nm(args, timeout=15):
        calls.append(args)
        if args[:2] == ["-t", "-f"]:
            # what nmcli -t prints: comitup's hotspot connection is "<ap_name>-0000", and
            # a colon inside a name comes escaped
            return ("11-11:802-11-wireless:HomeNet\n22-22:ethernet:Wired connection 1\n"
                    "33-33:802-11-wireless:TubeBoard-setup-0000\n44-44:802-11-wireless:comitup-680\n"
                    "55-55:802-11-wireless:Mums\\:House\n66-66:802-11-wireless:Renamed-hotspot\n")
        if args[0] == "-g":
            return "ap\n" if "33-33" in args or "66-66" in args else "infrastructure\n"
        return ""
    portal._nm, portal.hotspot_name = fake_nm, lambda: "TubeBoard-setup"
    try:
        nets = portal.wifi_connections()
        check("saved WiFi is listed; wired, comitup's hotspot and any access point left out",
              [n for _, n in nets] == ["HomeNet", "Mums:House"], nets)
        gone = portal.forget_wifi()
        deletes = [c for c in calls if c[:3] == ["connection", "delete", "uuid"]]
        check("forgetting deletes exactly those", gone == ["HomeNet", "Mums:House"]
              and [c[3] for c in deletes] == ["11-11", "55-55"], deletes)
        check("the shell says what it did", "forgot HomeNet" in quiet(portal.cli_forget_wifi))
        page = portal.home()
        check("the page lists the networks and asks for the word",
              "HomeNet" in page and "FORGET" in page and 'action="/forget-wifi"' in page)
        srv = portal.ThreadingHTTPServer(("127.0.0.1", 0), portal.H)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            dels_before = len([x for x in calls if x[:2] == ["connection", "delete"]])
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            c.request("POST", "/forget-wifi", "confirm=yes+please",
                      {"Content-Type": "application/x-www-form-urlencoded"})
            r = c.getresponse(); body = r.read().decode(); c.close()
            dels = lambda: len([x for x in calls if x[:2] == ["connection", "delete"]])   # noqa: E731
            check("the wrong word changes nothing", "Nothing was changed" in body and dels() == dels_before)
            before = len(calls)
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            c.request("POST", "/forget-wifi", "confirm=FORGET",
                      {"Content-Type": "application/x-www-form-urlencoded"})
            r = c.getresponse(); r.read(); c.close()
            check("the right word answers first and forgets after", r.status == 303
                  and r.getheader("Location") == "/?forgot=1" and len(calls) == before)
            # the real handler waits two seconds so the phone gets its answer; the timer
            # would fire later and call the stubbed nmcli, which is harmless, but do not
            # leave it to run after the stubs are gone
            for t in threading.enumerate():
                if isinstance(t, threading.Timer):
                    t.cancel()
        finally:
            srv.shutdown()
        portal._nm = lambda args, timeout=15: (_ for _ in ()).throw(RuntimeError("nmcli is not installed"))
        check("no nmcli is one sentence", "not installed" in main_with("--forget-wifi"))
        check("and the page still loads without it", "Could not list" in portal.home())
    finally:
        portal._nm, portal.hotspot_name = saved_nm, saved_hs


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


def test_ticker():
    print("\na status too long for its line scrolls")
    import numpy as np
    v = views_for({})[0]
    at = dt.datetime(2026, 10, 9, 14, 0)
    cols = [{"label": "WESTBOUND", "towards": "", "rows": [("Uxbridge", 60)]},
            {"label": "EASTBOUND", "towards": "", "rows": [("Cockfosters", 120)]}]
    why = ("Severe delays between Acton Town and Uxbridge while we fix a signal failure "
           "at Ealing Common. Tickets are accepted on London Buses and the Central line.")
    spec = {}
    img = board.render(1920, 1080, v, cols, "Severe Delays", True, why, at, at, True, ticker=spec)
    check("a long status hands its line to the ticker", spec.get("strip") is not None, sorted(spec))
    x0, y0, x1, y1 = spec["box"]
    u = 19.2
    check("the box sits in the footer, after 'Status:', clear of the right-hand text",
          150 < x0 < 300 and x1 < 1920 - 2.5 * u - 100 and 950 < y0 < y1 < 1080, spec["box"])
    left = {c for _, c in img.crop((0, y0, x0, y1)).getcolors(maxcolors=1 << 16)}
    check("'Status:' stays put; the mark and 'Severe Delays' scroll with the reason",
          board.ORANGE not in left and board.ORANGE in {c for _, c in spec["strip"].getcolors(maxcolors=1 << 16)})
    strip = spec["strip"]
    check("the strip is the box's height and one pass is longer than the box",
          strip.height == y1 - y0 and strip.width > x1 - x0, (strip.size, spec["box"]))
    check("the frame carries the start of the strip, the same pixels",
          img.crop(spec["box"]).tobytes() == strip.crop((0, 0, x1 - x0, y1 - y0)).tobytes())
    check("and says the whole reason, not cut at the box",
          strip.width - (x1 - x0) > 500, strip.width)
    plain = board.render(1920, 1080, v, cols, "Severe Delays", True, why, at, at, True)
    check("a still frame (--png) still cuts it with an ellipsis instead",
          plain.crop(spec["box"]).tobytes() != img.crop(spec["box"]).tobytes())

    short = {}
    a = board.render(1920, 1080, v, cols, "Minor Delays", True, "Signal failure", at, at, True, ticker=short)
    b = board.render(1920, 1080, v, cols, "Minor Delays", True, "Signal failure", at, at, True)
    check("a status that fits does not scroll, and draws as before",
          "strip" not in short and a.tobytes() == b.tobytes())
    other_board = {}
    board.render(1920, 1080, dict(v, station_id="940GZZLUHBN", station_name="Holborn"), cols,
                 "Severe Delays", True, why, at, at, True, ticker=other_board)
    check("another board with the same status is another key, so it starts over",
          other_board["key"] != spec["key"])
    gone = {}
    board.render(1920, 1080, v, cols, "Severe Delays", True, why, at, None, False, ticker=gone)
    check("no live data: nothing to scroll", "strip" not in gone)

    # four rows: five-row spacing, the same text, centred under the headings
    def dots(rows_n):
        im = board.render(1920, 1080, dict(v, rows=rows_n), [{"label": "NORTHBOUND", "towards": "",
                          "rows": [("Uxbridge", 60 * k) for k in range(rows_n)]}] * 2,
                          "Good Service", True, "", at, at, True)
        colour = board.LINE_COLOURS[v["line"]]
        ys = [y for y in range(280, 930) if im.getpixel((58, y)) == colour]   # the rows, not the roundel
        runs = []
        for y in ys:
            if runs and y == runs[-1][-1] + 1:
                runs[-1].append(y)
            else:
                runs.append([y])
        return [sum(r) / len(r) for r in runs]
    five, four = dots(5), dots(4)
    gaps5 = [b - a for a, b in zip(five, five[1:])]
    gaps4 = [b - a for a, b in zip(four, four[1:])]
    check("four rows: four trains, at the five-row spacing", len(four) == 4 and len(five) == 5
          and max(abs(g - gaps5[0]) for g in gaps4 + gaps5) <= 1, (gaps5, gaps4))
    check("under their heading, where the first four were with five",
          abs(four[0] - five[0]) <= 1 and abs(four[-1] - five[3]) <= 1, (four, five))

    # the rotation's dots: bottom right above the footer rule, one per board in its
    # line's colour, the current one larger and ringed in white
    cs = (board.LINE_COLOURS["piccadilly"], board.LINE_COLOURS["victoria"], board.LINE_COLOURS["great-northern"])
    dot = board.render(1920, 1080, v, cols, "Good Service", True, "", at, at, True, rotation=(1, 3, cs))
    u1, dr = 19.2, 0.42 * 19.2
    dy, last = 1080 - 2.5 * u1 - 4.9 * u1 - 1.3 * u1, 1920 - 2.5 * u1 - dr
    xs = [last - (2 - i) * 1.5 * u1 for i in range(3)]
    check("the dots sit above the footer rule, each in its board's line colour",
          [dot.getpixel((round(x), round(dy))) for x in xs] == list(cs),
          [dot.getpixel((round(x), round(dy))) for x in xs])
    check("and the current board's is ringed in white",
          dot.getpixel((round(xs[1] + dr), round(dy))) == board.WHITE
          and dot.getpixel((round(xs[0] + dr), round(dy))) != board.WHITE)
    check("nothing is left under the clock", dot.crop((1700, 150, 1900, 185)).getcolors(maxcolors=1 << 16)
          == [(200 * 35, board.BG)])

    # the settings card: boxed, with its seconds counting down beside it
    card = {}
    shot = board.render(1920, 1080, v, cols, "Good Service", True, "", at, at, True,
                        address=("tubeboard", "192.168.1.23"), address_left=42.3, ticker=card)
    c = card.get("count")
    check("the settings card hands its seconds to the ticker", c is not None and c["box"][2] <= 1920, card)
    probe_t = board.Ticker(None, 19.2)
    probe_t.count, probe_t.count_n = c, 43
    check("and the frame's '43s' is the ticker's '43s', the same pixels",
          shot.crop(c["box"]).tobytes() == probe_t.countdown().tobytes())
    probe_t.count_n = 9
    check("which changes when the number does", shot.crop(c["box"]).tobytes() != probe_t.countdown().tobytes())
    row = shot.crop((1000, int((1080 - 2.5 * 19.2 - 4.9 * 19.2 + 1080) / 2) - 25, c["box"][0], 1080))
    check("the address sits in a white box", board.WHITE in {k for _, k in row.getcolors(maxcolors=1 << 16)})
    edge = [shot.getpixel((1300, y)) for y in range(960, 1000)]
    check("drawn as one crisp pixel of white, not two greys",
          edge.count(board.WHITE) == 1 and all(p in (board.WHITE, board.BG) for p in edge), edge)

    # the ticker against a screen made of a file, on a clock the test turns
    class FB:
        w, h, bpp = 1920, 1080, 16
        stride = 3840
        row = 3840

        def __init__(self):
            self.f = open(os.path.join(TMP, "fb0"), "wb+", buffering=0)
            self.f.write(b"\0" * (self.stride * self.h))
            self.writes = 0

        def write_box(self, box, v):
            self.writes += 1
            board.Framebuffer.write_box(self, box, v)

        def read(self, box):
            x0, y0, x1, y1 = box
            out = []
            for y in range(y0, y1):
                self.f.seek(y * self.stride + x0 * 2)
                out.append(np.frombuffer(self.f.read((x1 - x0) * 2), dtype="<u2"))
            return np.array(out)

    clock = [5000.0]
    saved = board.time
    board.time = types.SimpleNamespace(monotonic=lambda: clock[0], time=lambda: clock[0], sleep=lambda s: None)
    try:
        fb = FB()
        t = board.Ticker(fb, 19.2)
        with t.lock:
            t.set(spec)
        start = board.pack565(strip)[:, : x1 - x0]
        check("it starts at rest with the line's start in place",
              t.off == 0 and not t.tick() and fb.writes == 0)
        clock[0] += board.Ticker.PAUSE + 0.01
        check("then slides", t.tick() and t.off == t.step and fb.writes == 1)
        check("and what it wrote is the strip, moved by one step",
              np.array_equal(fb.read(spec["box"]), board.pack565(strip)[:, t.step: t.step + x1 - x0]))
        check("about 120 px a second on a 1080p screen", 3 <= t.step <= 5 and t.step * t.FPS >= 90, t.step)
        frame = np.zeros((1080, 1920), dtype=np.uint16)
        t.patch(frame)
        check("a full redraw gets the current window patched in, so it never jumps back",
              np.array_equal(frame[y0:y1, x0:x1], t.window()) and not np.array_equal(t.window(), start))
        with t.lock:
            t.set(board.Ticker.prepare(dict(spec)))
        check("the same status redrawn keeps its place, prepared outside the lock", t.off == t.step
              and np.array_equal(t.window(), board.pack565(strip)[:, t.step: t.step + x1 - x0]))
        n = 0
        while t.off != 0 and n < 10000:
            t.tick()
            n += 1
        check("one pass comes round to the start and rests there",
              t.off == 0 and np.array_equal(fb.read(spec["box"]), start) and not t.tick(), n)
        clock[0] += board.Ticker.PAUSE + 0.01
        t.tick()
        other = dict(spec, key=("Minor Delays", "something else"))
        with t.lock:
            t.set(other)
        check("a different status starts over, at rest", t.off == 0 and not t.tick())
        with t.lock:
            t.set({})
        clock[0] += 60
        check("nothing to scroll: the ticker writes nothing", not t.tick())
        # the countdown, once a second as the number changes
        with t.lock:
            t.set(dict(card, count_until=clock[0] + 42.3))
        check("the seconds are drawn as soon as the card is up",
              t.tick() and t.count_n == 43 and np.array_equal(fb.read(c["box"]), t.count_px))
        check("and not again within the same second", not t.tick())
        clock[0] += 1.0
        check("a second later, one less", t.tick() and t.count_n == 42)
        clock[0] += 60
        t.tick()
        check("it never shows 0s; the board redraws without the card at the end", t.count_n == 1)

        # show() with the patch: the frame on the screen has the window in it
        real = object.__new__(board.Framebuffer)
        real.f, real.bpp, real.w, real.h, real.stride, real.row = fb.f, 16, 1920, 1080, 3840, 3840
        with t.lock:
            t.set(spec)
        clock[0] += board.Ticker.PAUSE + 0.01
        for _ in range(5):
            t.tick()
        real.show(img, patch=t.patch)
        check("show() writes the frame with the ticker's window in place",
              np.array_equal(fb.read(spec["box"]), t.window())
              and np.array_equal(fb.read((0, 0, 200, 50)), board.pack565(img.crop((0, 0, 200, 50)))))
        fb.f.close()
    finally:
        board.time = saved


if __name__ == "__main__":
    test_views()
    test_rail_board()
    test_fetch_and_render()
    test_ticker()
    test_loop()
    test_outage()
    test_wifi()
    test_cli_flags()
    test_shell()
    test_page()
    test_forget_wifi()
    test_both_ends()
    print()
    if FAILS:
        print(f"{len(FAILS)} FAILED: " + ", ".join(FAILS))
        sys.exit(1)
    print("all passed")
