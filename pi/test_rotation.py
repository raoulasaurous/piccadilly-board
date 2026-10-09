#!/usr/bin/env python3
"""Offline tests for the station rotation, in board.py and in portal.py, and for
the nightly updater (updater.py) against a model of the Pi.

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
import fcntl
import http.client
import io
import json
import os
import re
import pwd
import shutil
import subprocess
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
import updater                                                      # noqa: E402

board.SETTINGS_PATH = os.path.join(TMP, "board-settings.json")
portal.SETTINGS_PATH = os.path.join(TMP, "portal-settings.json")
# never the Pi's own: run as root there, the loop tests would write the live health file
board.HEALTH_PATH = os.path.join(TMP, "health.json")
board.VERSION_PATH = os.path.join(TMP, "VERSION")

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

    def rail_fetch(view, feed=None, statuses=None):
        """board.fetch for a rail board at 08:10, with this feed in place of
        RAIL_BOARD and these line statuses in place of TfL's Good Service."""
        saved = fake_clock[0], board.requests, rail.requests

        def feed_get(url, params=None, headers=None, timeout=None):
            r = Resp(dict(feed)); r.status_code = 200
            return r

        def status_get(url, params=None, timeout=None):
            if url.endswith("/Status"):
                board_calls.append(url)
                return Resp([{"lineStatuses": statuses}])
            return board_get(url, params, timeout)
        try:
            # the stub board is for 08:10; predictions are relative to now
            board.dt = types.SimpleNamespace(datetime=FakeDT, timedelta=dt.timedelta)
            fake_clock[0] = dt.datetime(2026, 10, 7, 8, 10).timestamp()
            if feed is not None:
                rail.requests = stub(feed_get)
            if statuses is not None:
                board.requests = stub(status_get)
            return board.fetch(view)
        finally:
            board.dt = dt
            fake_clock[0], board.requests, rail.requests = saved

    asked = len(rail_calls)
    cols, status, ok, why = rail_fetch(v[3])
    check("one pass asks the feed once: the trains and the notices come in the same answer",
          len(rail_calls) == asked + 1, rail_calls[asked:])
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
    check("the cancelled train keeps its timetable place and says so; the overdue one says delayed; "
          "via is not doubled",
          cols[1]["rows"] == [("Hertford North", None), ("Welwyn Garden City", 420),
                              ("Hertford North", board.CANCELLED),
                              ("Stevenage via Hertford North", 720)], cols[1]["rows"])
    check("a row with no minutes is drawn as delayed", board.label_mins(None) == "delayed")
    check("a cancelled row is drawn as Cancelled, and the marker is not a number",
          board.label_mins(board.CANCELLED) == "Cancelled" and not isinstance(board.CANCELLED, (int, float)))
    three = rail_fetch(views_for({"stations": [RAIL_STATION], "rail_api_key": "k-test", "rows": 3})[0])[0]
    check("a cancelled train takes one of the rows: with three, the train after it is left off",
          three[1]["rows"] == [("Hertford North", None), ("Welwyn Garden City", 420),
                               ("Hertford North", board.CANCELLED)], three[1]["rows"])
    gone = {"generatedAt": "2026-10-07T08:10:00.0000000+01:00", "trainServices": [
        {"std": "08:07", "etd": "Cancelled", "isCancelled": True, "platform": "1", "serviceID": "x1",
         "destination": [{"locationName": "Hertford North"}]},
        {"std": "08:09", "etd": "Cancelled", "isCancelled": True, "platform": "1", "serviceID": "x2",
         "destination": [{"locationName": "Hertford North"}]},
        # isCancelled left false, the word in etd: still cancelled
        {"std": "08:30", "etd": "Cancelled", "isCancelled": False, "platform": "1", "serviceID": "x3",
         "destination": [{"locationName": "Stevenage"}]}]}
    left = rail.predictions(gone, dt.datetime(2026, 10, 7, 8, 10), "great-northern")
    check("a cancelled train three minutes past its time is dropped; one minute past, it stays",
          [(a["id"], a["rail"]["cancelled"], a["timeToStation"]) for a in left]
          == [("x2", True, 0), ("x3", True, 1200)], [(a["id"], a["timeToStation"]) for a in left])

    # the feed's notices stand in for TfL's status when TfL says all is well
    check("TfL says Good Service and the feed has a notice: the status is the notice, tidied",
          status == "Notice" and ok and not board.good_status(status)
          and why == "Lifts at Highbury are out of order.", (status, why))
    quiet = dict(RAIL_BOARD, nrccMessages=None)
    _, status2, ok2, why2 = rail_fetch(v[3], feed=quiet)
    check("the status line is TfL's for the operator",
          status2 == "Good Service" and ok2 and why2 == ""
          and any("/Line/great-northern/Status" in u for u in board_calls), (status2, why2))
    trouble = {"statusSeverity": 9, "statusSeverityDescription": "Minor Delays",
               "reason": "Great Northern: Minor delays due to a signal failure at Finsbury Park."}
    _, status3, ok3, why3 = rail_fetch(v[3], statuses=[trouble])
    check("TfL reporting trouble keeps its own status and reason over the notice",
          status3 == "Minor Delays" and ok3 and why3 == "due to a signal failure at Finsbury Park",
          (status3, why3))
    _, status4, ok4, why4 = rail_fetch(v[3], statuses=[])
    check("TfL saying nothing, the notice still shows, not 'status unknown'",
          status4 == "Notice" and ok4 and why4.startswith("Lifts"), (status4, why4))
    pointers = dict(RAIL_BOARD, nrccMessages=[
        {"Value": "<p>Plan ahead with the journey planner.</p>"},
        {"Value": "<p>Check&nbsp;nationalrail.co.uk/service-disruptions before you travel.</p>"}])
    _, status5, _, why5 = rail_fetch(v[3], feed=pointers)
    check("a notice that only points elsewhere is no notice: TfL's Good Service stands",
          status5 == "Good Service" and why5 == "", (status5, why5))
    buses = dict(RAIL_BOARD, nrccMessages=[
        {"Value": "<p>Buses replace trains between Hertford North and Stevenage until 14:00, "
                  "so please check the journey planner before you travel.</p>"}])
    _, status6, _, why6 = rail_fetch(v[3], feed=buses)
    check("news and a pointer in one sentence: the news shows, the pointer goes",
          status6 == "Notice"
          and why6 == "Buses replace trains between Hertford North and Stevenage until 14:00.",
          (status6, why6))

    def say(*texts):
        return rail.notice({"nrccMessages": [{"Value": t} for t in texts]})
    oct7 = ("<p>Trains running between Welwyn Garden City and Potters Bar may be delayed by up to "
            "10&nbsp;minutes. Latest information can be found in the Disruptions area of the "
            "<a href=\"https://www.nationalrail.co.uk/service-disruptions/\">National Rail website</a>.</p>")
    check("the 7 Oct notice loses its pointer to the website",
          say(oct7) == "Trains running between Welwyn Garden City and Potters Bar may be delayed "
                       "by up to 10 minutes.", say(oct7))
    dash = say("<p>Trains are delayed by up to 10 minutes - see the National Rail website for details.</p>")
    lead = say("<p>If you are travelling today, please check the journey planner.</p>")
    check("a sentence is cut at the clause that points elsewhere; a clause that only leads in to it goes too",
          dash == "Trains are delayed by up to 10 minutes." and lead == "", (dash, lead))
    check("two notices are joined with a dot between, and the same one twice is said once",
          say("<p>Lifts out of order.</p>", "<p>Ticket office closed.</p>", "<p>Lifts out of order.</p>")
          == "Lifts out of order.  ·  Ticket office closed.",
          say("<p>Lifts out of order.</p>", "<p>Ticket office closed.</p>", "<p>Lifts out of order.</p>"))
    long = say(" ".join(f"Sentence number {n} of a long notice about the line today." for n in range(12)))
    endless = say("word " * 400)
    check("a long notice is cut at about 300 characters, at a sentence; one with no full stop, at a word",
          250 < len(long) <= 300 and long.endswith("today.") and long.startswith("Sentence number 0 ")
          and len(endless) <= 303 and endless.endswith("word..."), (len(long), long[-20:], endless[-12:]))

    at = dt.datetime(2026, 10, 7, 8, 10)
    img = board.render(1920, 1080, v[3], cols, status, ok, why, at, at, True, rotation=(3, 4))
    img.save(os.path.join(TMP, "rail.png"))
    check("it draws", img.size == (1920, 1080))
    # "Cancelled" is the widest thing the minutes slot holds, and DejaVu on the Pi is
    # wider than the Mac's font: the longest destination on the line beside it must
    # still say where the train was going
    shown, real_clip = [], board.clip

    def clip(d, text, fnt, room):
        out = real_clip(d, text, fnt, room)
        shown.append((text, out))
        return out
    board.clip = clip
    try:
        board.render(1920, 1080, v[3], [cols[0], dict(cols[1], rows=[("Welwyn Garden City", board.CANCELLED)])],
                     status, ok, why, at, at, True)
    finally:
        board.clip = real_clip
    wgc = [out for text, out in shown if text == "Welwyn Garden City"]
    check("beside Cancelled, Welwyn Garden City still reads as Welwyn Garden",
          len(wgc) == 1 and wgc[0].startswith("Welwyn Garden"), wgc)
    spec = {}
    board.render(1920, 1080, v[3], cols, "Notice", True, say(oct7), at, at, True, ticker=spec)
    check("the 7 Oct notice is longer than the line, so it scrolls", spec.get("strip") is not None)
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
    check("--explain shows the cancelled row, and the notice as the status",
          "Cancelled  Hertford North" in text
          and "Status: Notice - Lifts at Highbury are out of order." in text, text)
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


def read_health():
    """The health file, or {} when the board wrote none (a FAIL below, not a crash)."""
    try:
        with open(board.HEALTH_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def health_run(frames, **kw):
    """run_loop with no health file before it, quietly; the frames and the file after."""
    if os.path.exists(board.HEALTH_PATH):
        os.unlink(board.HEALTH_PATH)
    try:
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            shown = run_loop(THREE, frames=frames, **kw)
    except Exception as e:                      # noqa: BLE001
        # the health file must never stop the board: an escape is a FAIL, not a crash
        print(f"  (the loop raised: {e!r})")
        shown = []
    return shown, read_health()


def test_health():
    print("\nthe board's health file, which the updater reads")
    with open(board.VERSION_PATH, "w") as f:
        f.write("c0ffee" * 6 + "abcd\n")
    shown, h = health_run(30)
    check("the loop writes it, naming this process and the commit in VERSION",
          h.get("pid") == os.getpid() and h.get("version") == "c0ffee" * 6 + "abcd", h)
    # the 30th frame is the test's own stop, not a draw
    check("every frame drawn is counted, and none failed",
          h.get("draws") == len(shown) - 1 == 29 and h.get("failed_draws") == 0, h)
    check("with wall times: when it started, its last frame, its last good fetch",
          h.get("started") == 1000.0 and (h.get("last_draw") or 0) > 1000.0
          and h.get("last_fetch_ok") is not None and h["last_fetch_ok"] <= h["last_draw"], h)
    check("written whole and renamed into place: no scratch file left",
          not os.path.exists(board.HEALTH_PATH + ".tmp"))
    check("it holds every field the updater reads",
          {"pid", "version", "draws", "failed_draws", "last_draw", "last_fetch_ok"} <= set(h), sorted(h))

    def boom(n):
        if n == 5:
            raise RuntimeError("a frame that fails")
    shown, h = health_run(20, on_frame=boom)
    check("a failed draw is counted as one, and the loop carries on",
          len(shown) == 20 and h.get("failed_draws") == 1 and h.get("draws") == 18, h)

    outage["lines"] = {"piccadilly", "victoria", "mildmay"}
    try:
        shown, h = health_run(10)
    finally:
        outage["lines"] = set()
    check("no good fetch, no fetch time: the updater can tell a board that is not live",
          h.get("draws") == 9 and "last_fetch_ok" in h and h["last_fetch_ok"] is None, h)

    os.unlink(board.VERSION_PATH)
    shown, h = health_run(5)
    check("no VERSION file (a clone, a Mac): the version is null, not a crash",
          len(shown) == 5 and "version" in h and h["version"] is None, h)

    saved = board.HEALTH_PATH
    board.HEALTH_PATH = os.path.join(TMP, "no-such-dir", "health.json")
    try:
        shown, _ = health_run(20)
    finally:
        board.HEALTH_PATH = saved
    check("where it cannot be written, the board draws on without it", len(shown) == 20, len(shown))


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


HC_KEY = "AAAAC3NzaC1lZDI1NTE5AAAAIKEYMATERIAL0123456789abcdef"
HC_PASSWORD = "CORRECT-HORSE-BATTERY"
HC_RAIL = "RAILKEYSECRET0123"
Ran = portal.Ran
HC_KEYS = (
    "# a comment line, which is not a key\n"
    f"ssh-ed25519 {HC_KEY} owner@laptop\n"
    f'command="/bin/true",no-pty ssh-rsa {HC_KEY}RSA second key with spaces\n'
    f"ecdsa-sha2-nistp256 {HC_KEY}ECDSA\n"
    f"{HC_KEY}BARE\n"
    f"ssh-ed25519 {HC_KEY}ED2 {HC_KEY}ED2\n")


def hc_runner(calls, wifi=("HomeNet", "Mums:House"), passwd=None, connect=None, updater=None,
              everything=None):
    """A stand-in for portal.run_command that answers like the Pi would, and writes down
    every command it was asked for. everything= answers all of them the same way."""
    passwd = passwd or Ran("ok", "pi P 2026-09-01 0 99999 7 -1\n", "")
    connect = connect or Ran("ok", "Signed in: no\nRemote shell: disabled\n", "")
    updater = updater or Ran("ok", "LoadState=not-found\nUnitFileState=\n", "")

    def run(args, timeout=10):
        calls.append(list(args))
        if everything is not None:
            return everything
        if args[0] == "nmcli" and args[1:3] == ["-t", "-f"]:
            rows = [f"1{n}-uuid:802-11-wireless:" + name.replace("\\", "\\\\").replace(":", "\\:")
                    for n, name in enumerate(wifi)]
            rows += ["22-uuid:ethernet:Wired connection 1",
                     "33-uuid:802-11-wireless:TubeBoard-setup-0000"]
            return Ran("ok", "\n".join(rows) + "\n", "")
        if args[0] == "nmcli" and args[1] == "-g":
            return Ran("ok", "ap\n" if "33-uuid" in args else "infrastructure\n", "")
        if args[0] == "passwd":
            return passwd
        if args[0] == "runuser":
            return connect
        if args[0] == "systemctl":
            return updater
        return Ran("missing", "", "")
    return run


def hc_find(lines, label):
    return next((x for x in lines if label in x), "")


def test_handover_check():
    print("\nthe hand-over check")
    home = os.path.join(TMP, "home", "pi")
    os.makedirs(os.path.join(home, ".ssh"), exist_ok=True)
    keys = os.path.join(home, ".ssh", "authorized_keys")
    conf = os.path.join(TMP, "comitup.conf")
    user = ("pi", 1000, home)
    tfl = {"line": "piccadilly", "station_id": "940GZZLUASL", "station_name": "Arsenal"}
    saved = portal.run_command, portal.hotspot_name
    portal.hotspot_name = lambda: "TubeBoard-setup"
    calls = []

    def run(root=True, where=None, **world):
        calls.clear()
        portal.run_command = hc_runner(calls, **world)
        out = quiet(lambda: portal.cli_handover_check(user=user, conf=conf, live_dir=TMP, root=root,
                                                      **(where or {})))
        return out, out.splitlines()

    def tree():
        return sorted((os.path.join(d, f), os.stat(os.path.join(d, f)).st_mtime_ns)
                      for d, _, fs in os.walk(TMP) for f in fs)

    try:
        # --- nothing done yet
        write_portal({"stations": [tfl, RAIL_STATION], "rows": 4, "rotate_seconds": 20})
        with open(keys, "w") as f:
            f.write(HC_KEYS)
        with open(conf, "w") as f:
            f.write("ap_name: comitup-123\n")
        before_files, before_settings = tree(), open(portal.SETTINGS_PATH, "rb").read()
        out, lines = run(updater=Ran("ok", "LoadState=loaded\nUnitFileState=disabled\n", ""))
        check("it changes nothing: the files and the settings are as they were",
              tree() == before_files and open(portal.SETTINGS_PATH, "rb").read() == before_settings)
        check("every command it ran only looks",
              all((c[0] == "nmcli" and "show" in c and not {"delete", "modify", "up", "down"} & set(c))
                  or c[:2] == ["passwd", "-S"] or c[:2] == ["systemctl", "show"]
                  or (c[0] == "runuser" and c[-2:] == ["rpi-connect", "status"]) for c in calls), calls)
        check("every line but the last starts with OK or TO DO, and the last says what is next",
              all(x.startswith(("OK ", "TO DO ")) for x in lines[:-1]) and lines[-1].startswith("Next"), out)
        for label in ("WiFi:", "Console password:", "Raspberry Pi Connect:", "SSH:", "Hotspot:",
                      "Rail key:", "Self-updater:"):
            check(f"nothing done: {label} is a TO DO", hc_find(lines, label).startswith("TO DO"), out)
        w = hc_find(lines, "WiFi:")
        check("the WiFi line counts the saved networks, names them, and says to forget them last",
              "2 saved networks" in w and "HomeNet" in w and "Mums:House" in w
              and "Forget the WiFi last, it cuts this connection" in w
              and "Wired" not in w and "TubeBoard-setup-0000" not in w, w)
        pw = hc_find(lines, "Console password:")
        check("the password line says it is older than the day and was typed into a chat",
              "2026-09-01" in pw and "before 2026-10-09" in pw and "typed into a chat" in pw, pw)
        check("it asked about the login's password with passwd -S",
              ["passwd", "-S", "pi"] in calls)
        rc = [c for c in calls if c[0] == "runuser"]
        check("it asked Pi Connect as that login, with that login's own runtime folder",
              len(rc) == 1 and rc[0][:4] == ["runuser", "-u", "pi", "--"]
              and "XDG_RUNTIME_DIR=/run/user/1000" in rc[0], rc)
        check("not signed in to Pi Connect is said plainly", "not signed in" in hc_find(lines, "Raspberry Pi Connect:"))
        ssh = hc_find(lines, "SSH:")
        check("the keys are counted and named by comment, and the decision is Raoul's",
              "5 keys" in ssh and "owner@laptop" in ssh and "second key with spaces" in ssh
              and "no comment" in ssh and "unreadable line" in ssh
              and "Decide whether Raoul's key stays" in ssh, ssh)
        check("the hotspot line gives the name comitup has and the name wanted",
              "comitup-123" in hc_find(lines, "Hotspot:") and "TubeBoard-setup" in hc_find(lines, "Hotspot:"))
        rk = hc_find(lines, "Rail key:")
        check("the missing rail key is a TO DO because a National Rail board needs it",
              "not saved" in rk and "1 National Rail board" in rk, rk)
        bd = hc_find(lines, "Boards:")
        check("the boards line gives the stations, the rows and the rotation seconds",
              bd.startswith("OK") and "Arsenal, Drayton Park" in bd and "4 trains" in bd and "20 s each" in bd, bd)
        check("the missing self-updater timer is not enabled", "not enabled" in hc_find(lines, "Self-updater:"))
        last = lines[-1]
        steps = last.split("; ")
        check("the last line puts the hotspot's name first, then the phone, then the password",
              last.index("hotspot's name") < last.index("on a phone") < last.index("console password"), last)
        check("and the WiFi last, because forgetting it cuts the connection",
              steps[-1].rstrip(".").endswith("forget the WiFi last, it cuts this connection")
              and last.count("forget the WiFi") == 1, last)
        check("no key text, no password text and no rail key in what it printed",
              "AAAA" not in out and "KEYMATERIAL" not in out and HC_PASSWORD not in out and HC_RAIL not in out)

        # --- everything done
        write_portal({"stations": [tfl, RAIL_STATION], "rows": 4, "rotate_seconds": 30,
                      "rail_api_key": HC_RAIL})
        os.remove(keys)
        with open(conf, "w") as f:
            f.write("# comitup\nap_name: TubeBoard-setup\n")
        out, lines = run(wifi=(), passwd=Ran("ok", "pi P 2026-10-10 0 99999 7 -1\n", ""),
                         connect=Ran("ok", "Signed in: yes\nRemote shell: allowed\n", ""),
                         updater=Ran("ok", "LoadState=loaded\nUnitFileState=enabled\n", ""))
        check("all done: every line but the last is OK", all(x.startswith("OK ") for x in lines[:-1]) and len(lines) > 6, out)
        check("all done: the last line leaves only the phone, which this cannot see",
              lines[-1].startswith("Next: look at the setup screen") and "on a phone" in lines[-1]
              and lines[-1].endswith("Nothing else is left."), lines[-1])
        check("the rail key is reported saved, and never printed",
              "Rail key: saved" in out and HC_RAIL not in out)
        check("no saved WiFi, and a password changed after the day, are both OK",
              "no saved network" in hc_find(lines, "WiFi:") and "2026-10-10" in hc_find(lines, "Console password:"))
        check("no authorized_keys file means no key", "no key can log in" in hc_find(lines, "SSH:"))
        check("a signed-in Pi Connect is OK and reminds Raoul to tell the recipient",
              "signed in, so Raoul can log in remotely" in hc_find(lines, "Raspberry Pi Connect:")
              and "The recipient must be told" in hc_find(lines, "Raspberry Pi Connect:"))
        with open(keys, "w") as f:
            f.write("# nothing here\n\n")
        out, lines = run(wifi=())
        check("a key file with only comments has no key", "no key is authorised" in hc_find(lines, "SSH:"))

        # --- the commands are not there, or do not answer
        os.remove(keys)
        os.remove(conf)
        os.remove(portal.SETTINGS_PATH)
        for what, ran, words in (("missing", Ran("missing", "", ""), "not installed"),
                                 ("hung", Ran("timeout", "", ""), "did not answer")):
            out, lines = run(everything=ran)
            check(f"commands {what}: it still finishes and ends with what is next",
                  len(lines) > 6 and lines[-1].startswith("Next"), out)
            for label in ("WiFi:", "Console password:", "Raspberry Pi Connect:", "Self-updater:"):
                x = hc_find(lines, label)
                check(f"commands {what}: {label} is a TO DO that says so", x.startswith("TO DO") and words in x, x)
            check(f"commands {what}: the missing file and settings are said, not crashed on",
                  hc_find(lines, "Hotspot:").startswith("TO DO") and hc_find(lines, "Boards:").startswith("TO DO"), out)
        out, lines = run(everything=Ran("failed", "", f"permission denied {HC_PASSWORD}"))
        check("a refused command is a TO DO, and its words are not copied out",
              hc_find(lines, "Console password:").startswith("TO DO") and "needs sudo" in hc_find(lines, "Console password:")
              and HC_PASSWORD not in out, out)

        # --- not root
        out, lines = run(root=False)
        check("without root it says so first, and the last line starts with running it again",
              lines[0].startswith("TO DO") and "not run as root" in lines[0]
              and lines[-1].startswith("Next, in this order: 1) run this check again with sudo"), out)

        # --- the password line, one case at a time
        def pw(answer, **kw):
            return hc_find(run(passwd=answer, **kw)[1], "Console password:")
        check("a password changed the day before is a TO DO",
              pw(Ran("ok", "pi P 2026-10-08 0 99999 7 -1", "")).startswith("TO DO"))
        check("a password changed on the day is OK",
              pw(Ran("ok", "pi P 2026-10-09 0 99999 7 -1", "")).startswith("OK"))
        check("the older shadow date format is read too",
              pw(Ran("ok", "pi P 10/08/2026 0 99999 7 -1", "")).startswith("TO DO")
              and pw(Ran("ok", "pi P 10/12/2026 0 99999 7 -1", "")).startswith("OK"))
        check("a locked account is said to be locked", "locked" in pw(Ran("ok", "pi L 2026-10-20 0 99999 7 -1", "")))
        check("no password at all is a TO DO", "no password" in pw(Ran("ok", "pi NP 2026-10-20 0 99999 7 -1", "")))
        x = pw(Ran("ok", f"pi P {HC_PASSWORD} 0 99999 7 -1", ""))
        check("an answer it cannot read is a TO DO and is not echoed",
              x.startswith("TO DO") and HC_PASSWORD not in x and "no change date" in x, x)

        # --- Pi Connect, one answer at a time
        def pc(answer):
            return hc_find(run(connect=answer)[1], "Raspberry Pi Connect:")
        check("signed in is OK", pc(Ran("ok", "Signed in: yes\n", "")).startswith("OK"))
        check("an answer on stderr from a failed status is still read",
              "not signed in" in pc(Ran("failed", "", "Signed in: no")))
        x = pc(Ran("failed", "", f"Failed to connect to bus {HC_PASSWORD}"))
        check("an answer it does not know is a TO DO, and is not echoed",
              x.startswith("TO DO") and HC_PASSWORD not in x and "no answer" in x, x)
        check("a command that is not installed is said so",
              "not installed" in pc(Ran("missing", "env: 'rpi-connect': No such file", "")))

        # --- the self-updater
        def up(answer):
            return hc_find(run(updater=answer)[1], "Self-updater:")
        check("no timer at all is OK, because the updater is another change",
              up(Ran("ok", "LoadState=not-found\nUnitFileState=\n", "")).startswith("OK"))
        check("an enabled timer is OK", up(Ran("ok", "LoadState=loaded\nUnitFileState=enabled\n", "")).startswith("OK"))
        check("a disabled timer is a TO DO that gives the command",
              "systemctl enable --now tubeboard-update.timer" in up(Ran("ok", "LoadState=loaded\nUnitFileState=disabled\n", "")))

        # --- the hotspot and the settings, one case at a time
        write_portal({"stations": [tfl], "rows": 12, "rotate_seconds": "soon"})
        with open(conf, "w") as f:
            f.write("ap_name: TubeBoard-setup\n")
        lines = run()[1]
        check("rows are shown as the board clamps them, and the one board has no rotation",
              "8 trains" in hc_find(lines, "Boards:") and "no rotation" in hc_find(lines, "Boards:")
              and "30 s" in hc_find(lines, "Boards:"), hc_find(lines, "Boards:"))
        # json reads Infinity and 1e400 as inf, and int(inf) is an OverflowError. The board
        # survives that, so the check must too, and must still print every line.
        for field, text in (("rows", "Infinity"), ("rows", "1e400"), ("rows", "NaN"),
                            ("rotate_seconds", "Infinity"), ("rotate_seconds", "-1e400")):
            body = json.dumps({"stations": [tfl, RAIL_STATION], "rows": 4, "rotate_seconds": 20})
            with open(portal.SETTINGS_PATH, "w") as f:
                f.write(body.replace(f'"{field}": {4 if field == "rows" else 20}', f'"{field}": {text}'))
            try:
                lines = run()[1]
                bd = hc_find(lines, "Boards:")
                crashed = ""
            except Exception as e:                                  # noqa: BLE001
                bd, lines, crashed = "", [], f"{type(e).__name__}: {e}"
            want = "4 trains each way" if field == "rows" else "30 s each"
            check(f"{field} {text} in settings.json gives the board's default, not a crash",
                  not crashed and want in bd and lines[-1].startswith("Next"), crashed or bd)
        write_portal({"stations": [tfl], "rows": 12, "rotate_seconds": "soon"})
        lines = run()[1]
        check("no rail key and no rail board is OK", hc_find(lines, "Rail key:").startswith("OK"))
        check("the right hotspot name is OK", hc_find(lines, "Hotspot:").startswith("OK"))
        lines = quiet(lambda: portal.cli_handover_check(user=user, conf=conf, live_dir="/opt/elsewhere",
                                                        root=True)).splitlines()
        check("settings read from somewhere other than the live folder are a TO DO",
              hc_find(lines, "Settings:").startswith("TO DO") and "/opt/elsewhere" in hc_find(lines, "Settings:"))
        with open(portal.SETTINGS_PATH, "w") as f:
            f.write("{not json")
        lines = run()[1]
        check("a broken settings file is a TO DO and not a crash",
              hc_find(lines, "Settings:").startswith("TO DO") and lines[-1].startswith("Next"))
        write_portal(dict(BASE))

        # --- a name with a control character cannot move the cursor
        out, lines = run(wifi=("Evil\x1b[2JNet\nTO DO fake",))
        check("a saved name with an escape or a line break stays on one printable line",
              "\x1b" not in out and sum(x.startswith("TO DO fake") for x in lines) == 0, repr(out))
    finally:
        portal.run_command, portal.hotspot_name = saved


def test_run_command():
    print("\nthe one door for outside commands")
    rc = portal.run_command
    check("a program that is not there is missing", rc(["tubeboard-no-such-program"]).state == "missing")
    check("exit 127 is missing too, which is what runuser and env say", rc(["sh", "-c", "exit 127"]).state == "missing")
    r = rc(["sh", "-c", "echo out; echo err >&2; exit 3"])
    check("any other exit is failed, with both streams kept", r.state == "failed" and r.out == "out\n" and r.err == "err\n", r)
    check("exit 0 is ok", rc(["sh", "-c", "echo hello"]) == portal.Ran("ok", "hello\n", ""))
    check("a command that hangs is a timeout", rc(["sh", "-c", "sleep 5"], timeout=0.2).state == "timeout")
    check("a command that asks a question gets no answer and ends",
          rc(["sh", "-c", "read x; echo got:$x"], timeout=5) == portal.Ran("ok", "got:\n", ""))


def test_board_user():
    print("\nwhich login the board belongs to")
    saved = portal.pwd, portal.HERE, os.environ.get("SUDO_USER")
    me = os.getuid()
    entries = {me: ("owner", me, "/home/owner"), 0: ("root", 0, "/root")}
    names = {"sudoer": ("sudoer", 1234, "/home/sudoer"), "root": ("root", 0, "/root")}

    def fake_pwd(uid_table):
        def entry(t):
            return types.SimpleNamespace(pw_name=t[0], pw_uid=t[1], pw_dir=t[2])

        def getpwuid(uid):
            if uid not in uid_table:
                raise KeyError(uid)
            return entry(uid_table[uid])

        def getpwnam(n):
            if n not in names:
                raise KeyError(n)
            return entry(names[n])
        return types.SimpleNamespace(getpwuid=getpwuid, getpwnam=getpwnam)
    clone = os.path.join(TMP, "clone")
    os.makedirs(os.path.join(clone, ".git"), exist_ok=True)
    os.makedirs(os.path.join(clone, "pi"), exist_ok=True)
    try:
        portal.pwd = fake_pwd(entries)
        portal.HERE = os.path.join(clone, "pi")
        os.environ["SUDO_USER"] = "sudoer"
        check("inside a clone it is the clone's owner", portal.find_board_user() == ("owner", me, "/home/owner"))
        portal.HERE = os.path.join(TMP, "opt-tubeboard")
        check("outside a clone it is the person who ran sudo", portal.find_board_user() == ("sudoer", 1234, "/home/sudoer"))
        os.environ["SUDO_USER"] = "root"
        check("root is never the answer", portal.find_board_user() is None)
        os.environ["SUDO_USER"] = "no such; user"
        check("a name that is not a login name is not looked up", portal.find_board_user() is None)
        del os.environ["SUDO_USER"]
        check("with no clone and no sudo it says it cannot tell", portal.find_board_user() is None)
        portal.HERE = os.path.join(clone, "pi")
        portal.pwd = fake_pwd({me: ("root", 0, "/root")})
        os.environ["SUDO_USER"] = "sudoer"
        check("a clone owned by root falls back to the person who ran sudo",
              portal.find_board_user() == ("sudoer", 1234, "/home/sudoer"))
    finally:
        portal.pwd, portal.HERE = saved[0], saved[1]
        if saved[2] is None:
            os.environ.pop("SUDO_USER", None)
        else:
            os.environ["SUDO_USER"] = saved[2]


def test_handover_flag():
    print("\n--handover-check from the shell")
    write_portal(dict(BASE))
    calls = []
    saved = portal.run_command, portal.find_board_user, portal.COMITUP_CONF, portal.LIVE_DIR, portal.hotspot_name
    portal.run_command = hc_runner(calls)
    portal.find_board_user = lambda: ("pi", 1000, os.path.join(TMP, "home", "pi"))
    portal.COMITUP_CONF, portal.LIVE_DIR = os.path.join(TMP, "no-comitup.conf"), TMP
    portal.hotspot_name = lambda: "TubeBoard-setup"
    argv = sys.argv
    try:
        sys.argv = ["portal.py", "--handover-check"]
        out = quiet(portal.main)
        check("the flag prints the check and exits without starting the page",
              "WiFi:" in out and out.splitlines()[-1].startswith("Next"), out)
        with open(portal.SETTINGS_PATH, "w") as f:
            f.write('{"line": "piccadilly", "station_id": "940GZZLUASL", "station_name": "Arsenal", '
                    '"rows": Infinity, "rotate_seconds": 1e400}')
        try:
            out = quiet(portal.main)
        except Exception as e:                                      # noqa: BLE001
            out = f"{type(e).__name__}: {e}"
        check("the flag still prints every line when the file holds Infinity",
              "WiFi:" in out and "Boards:" in out and out.splitlines()[-1].startswith("Next"), out)
        write_portal(dict(BASE))
        del calls[:]
        msg = main_with("--handover-check", "--forget-wifi")
        check("mixed with a flag that changes things, it refuses and changes nothing",
              "stands alone" in msg and not [c for c in calls if "delete" in c], (msg, calls))
        msg = main_with("--handover-check", "--rail-key", HC_RAIL)
        check("and a rail key is not saved by that mix", "stands alone" in msg and "rail_api_key" not in open(portal.SETTINGS_PATH).read())
    finally:
        sys.argv = argv
        (portal.run_command, portal.find_board_user, portal.COMITUP_CONF, portal.LIVE_DIR,
         portal.hotspot_name) = saved


def test_handover_doc():
    print("\nHANDOVER.md lists the hand-over steps in the order the check gives")
    path = os.path.join(HERE, "..", "HANDOVER.md")
    if not os.path.exists(path):
        print("  skipped: HANDOVER.md is not next to this folder")
        return
    text = open(path, encoding="utf-8").read()
    part = text.split("## Before it goes to the recipient", 1)[1].split("\n## ", 1)[0]
    steps = [x.lower() for x in re.findall(r"^\d+\. (.*(?:\n   .*)*)", part, re.M)]
    at = {k: next((n for n, x in enumerate(steps) if k in x), None)
          for k in ("phone", "password", "remotely", "forget the wifi", "first power-on")}
    check("every step is in the list", None not in at.values(), at)
    check("the phone, then the password, then the rest, then the WiFi, then the first power-on",
          at["phone"] < at["password"] < at["remotely"] < at["forget the wifi"] < at["first power-on"], at)
    check("the WiFi is the last step before the first power-on",
          at["forget the wifi"] + 1 == at["first power-on"], at)


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
    # a reason about the line's length: the same decision whatever the update age says
    edge_case = None
    for n in range(40, 260, 2):
        probe_why = ("i" * n)                   # a narrow letter: steps finer than the change
        t1, t2 = {}, {}
        board.render(1920, 1080, v, cols, "Minor Delays", True, probe_why, at, at, True, ticker=t1)
        board.render(1920, 1080, v, cols, "Minor Delays", True, probe_why, at + dt.timedelta(seconds=300), at,
                     True, ticker=t2)
        if ("strip" in t1) != ("strip" in t2):
            edge_case = n
    check("scrolling or not does not change with 'just now' and '5m ago'", edge_case is None, edge_case)
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
    check("four rows: four trains, evenly spaced, a little further apart than five",
          len(four) == 4 and len(five) == 5 and max(gaps4) - min(gaps4) <= 1
          and 1.06 <= gaps4[0] / gaps5[0] <= 1.10, (gaps5, gaps4))
    check("under their heading, a little lower than five's first row, clear of the dots",
          15 <= four[0] - five[0] <= 40 and four[-1] + gaps4[0] / 2 < 1080 - 7.4 * 19.2 - 1.3 * 19.2 - 8,
          (four, five))

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

    # with the QR library (the Pi has it; this test brings a stand-in): a QR code in
    # the corner for the IP's settings page, crisp, clear of the trains, no dots under it
    class FakeQR:
        made = []

        def __init__(self, border=4, box_size=10, error_correction=None):
            self.border = border

        def add_data(self, data):
            FakeQR.made.append(data)

        def make(self, fit=True):
            pass

        def make_image(self):
            n = 25 + 2 * self.border
            im = board.Image.new("1", (n, n), 1)
            for yy in range(self.border, n - self.border):
                for xx in range(self.border, n - self.border):
                    if (xx * 7 + yy * 3) % 5 < 2:
                        im.putpixel((xx, yy), 0)
            return im
    fake_qrcode = types.SimpleNamespace(QRCode=FakeQR, constants=types.SimpleNamespace(ERROR_CORRECT_M=0))
    saved_qr = board.qrcode
    board.qrcode = fake_qrcode
    try:
        qcard = {}
        cs3 = (board.LINE_COLOURS["piccadilly"], board.LINE_COLOURS["victoria"], board.LINE_COLOURS["great-northern"])
        qshot = board.render(1920, 1080, v, cols, "Good Service", True, "", at, at, True, rotation=(1, 3, cs3),
                             address=("tubeboard", "192.168.1.23"), address_left=42.3, ticker=qcard)
        check("the QR carries the settings page on the board's IP", FakeQR.made[-1:] == ["http://192.168.1.23:8080"],
              FakeQR.made[-1:])
        arr = np.asarray(qshot)
        white = np.all(arr == 255, axis=2)
        ys, xs = np.nonzero(white[700:, 1500:])
        qx0, qy0, qx1, qy1 = 1500 + xs.min(), 700 + ys.min(), 1500 + xs.max() + 1, 700 + ys.max() + 1
        qbox = qshot.crop((qx0, qy0, qx1, qy1))
        check("in the bottom right corner, about a tenth of the width",
              qx1 == round(1920 - 2.5 * 19.2) and abs(qy1 - (1080 - 0.6 * 19.2)) <= 1 and 150 <= qx1 - qx0 <= 192,
              (qx0, qy0, qx1, qy1))
        check("crisp: only black and white inside it",
              {c for _, c in qbox.getcolors(maxcolors=1 << 16)} <= {(0, 0, 0), (255, 255, 255)},
              {c for _, c in qbox.getcolors(maxcolors=1 << 16)})
        bare = board.render(1920, 1080, v, cols, "Good Service", True, "", at, at, True)
        check("clear of the trains: nothing was under it",
              {c for _, c in bare.crop((qx0, qy0, qx1, min(qy1, 937))).getcolors(maxcolors=1 << 16)} <= {board.BG, board.RULE})
        check("the dots wait while it has the corner",
              all(qshot.getpixel((round(x), round(dy))) not in cs3 for x in xs_dots)
              if (xs_dots := [1920 - 2.5 * 19.2 - 0.42 * 19.2 - (2 - i) * 1.5 * 19.2 for i in range(3)]) else False)
        check("the countdown sits left of it, and the frame's seconds are the ticker's",
              qcard["count"]["box"][2] <= qx0 and
              qshot.crop(qcard["count"]["box"]).tobytes() ==
              (lambda tt: (setattr(tt, "count", qcard["count"]), setattr(tt, "count_n", 43), tt.countdown())[2])(
                  board.Ticker(None, 19.2)).tobytes())
        eight = board.render(1920, 1080, dict(v, rows=8), [{"label": "NORTHBOUND", "towards": "",
                             "rows": [("Uxbridge", 60 * k) for k in range(8)]}] * 2, "Good Service", True, "", at, at,
                             True, address=("tubeboard", "192.168.1.23"), address_left=30)
        bare8 = board.render(1920, 1080, dict(v, rows=8), [{"label": "NORTHBOUND", "towards": "",
                             "rows": [("Uxbridge", 60 * k) for k in range(8)]}] * 2, "Good Service", True, "", at, at, True)
        diff = np.nonzero(np.any(np.asarray(eight) != np.asarray(bare8), axis=2)[:930, 1500:])
        hidden = {tuple(np.asarray(bare8)[y, 1500 + x]) for y, x in zip(*diff)}
        check("with eight rows it shrinks rather than cover a train", hidden <= {board.BG, board.RULE},
              list(hidden)[:4])
    finally:
        board.qrcode = saved_qr

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
        n, wrapped = 0, False
        while n < 10000:
            before = t.off
            t.tick()
            n += 1
            if t.off < before:
                wrapped = True
                break
        period = t.spec[2]
        check("a pass comes round to the start and carries straight on, no rest",
              wrapped and t.off == (before + t.step) % period and t.tick() and t.tick(), (n, t.off))
        check("seamless: the window at the wrap is the strip from its start again",
              np.array_equal(t.window(), board.pack565(strip)[:, t.off: t.off + x1 - x0]))
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


# ------------------------------------------------------------------ updater.py

OLD, NEW = "a" * 40, "b" * 40


class FakePi:
    """The Pi as the updater sees it, in a temp dir: the clone (git), install.sh,
    systemctl, the settings page, the board's health file and the clock, all
    scripted, and nothing real run. The board is a small model: restarted, it reads
    VERSION from the fake /opt, then works, hangs, fails its draws, crashes, or
    draws with no good fetch, as `behaviour` says for that commit."""
    n = 0

    def __init__(self, **settings):
        FakePi.n += 1
        root = os.path.join(TMP, f"pi{FakePi.n}")
        self.opt = os.path.join(root, "opt", "tubeboard")
        self.backup = self.opt + ".bak-update"
        self.state = os.path.join(root, "var", "lib", "tubeboard")
        self.units = os.path.join(root, "etc", "systemd", "system")
        self.clone = os.path.join(root, "home", "piccadilly-board")
        self.health = os.path.join(root, "run", "tubeboard", "health.json")
        for d in (self.opt, self.state, self.units, os.path.join(self.clone, ".git"),
                  os.path.dirname(self.health)):
            os.makedirs(d)
        self.put(self.opt, "VERSION", OLD + "\n")
        self.put(self.opt, "board.py", "# the board at " + OLD)
        self.put(self.opt, "settings.json", json.dumps(dict(board.DEFAULTS, **settings)))
        self.put(self.opt + ".bak-2026-10-09", "board.py", "# a dated backup, a person's")
        self.put(self.units, "tubeboard.service", "# unit at " + OLD)
        self.head, self.origin, self.branch = OLD, NEW, "main"
        self.dirty, self.ahead = "", False
        self.fetch_rc = self.install_rc = 0
        self.during_install = None     # what else happens while install.sh runs
        # commit -> "hung", "failing", "crashing", "offline", "late-failing" (fine until the
        # settings card goes, then every draw fails), "one-board" (only the first board
        # of the rotation ever fetches) or "late-crash" (exits at 90 s, systemd restarts it)
        self.behaviour = {}
        self.boards = len(settings.get("stations") or []) or 1
        self.portal_down = set()       # commits whose settings page does not answer
        self.updater_broken = False
        self.calls, self.lines = [], []
        self.clock = [1_800_000_000.0]
        self.pid = 4000
        self.restart_board()
        self.tick(30)                  # the old board has drawn and fetched

    @staticmethod
    def put(d, name, text):
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, name), "w") as f:
            f.write(text)

    @staticmethod
    def get(d, name):
        try:
            with open(os.path.join(d, name)) as f:
                return f.read()
        except OSError:
            return None

    def version(self):
        return (self.get(self.opt, "VERSION") or "").strip() or None

    def restart_board(self):
        self.pid += 1
        self.board = {"pid": self.pid, "started": self.clock[0], "version": self.version(),
                      "draws": 0, "failed_draws": 0, "last_draw": None, "last_fetch_ok": None,
                      "boards": self.boards, "boards_ok": 0}
        self.write_health()

    def write_health(self):
        with open(self.health, "w") as f:
            json.dump(self.board, f)

    def tick(self, s):
        self.clock[0] += s
        how = self.behaviour.get(self.board["version"])
        age = self.clock[0] - self.board["started"]
        if how == "late-failing" and age > 60:
            how = "failing"
        if how == "late-crash" and age > 90:
            how = "crashing"
        if how == "crashing":
            self.restart_board()       # dies after its first frame; systemd starts another
            self.board["draws"] = 1
        elif how == "failing":
            self.board["failed_draws"] += 1
        elif how != "hung":
            self.board["draws"] += 1
            self.board["last_draw"] = self.clock[0]
            if how != "offline":
                self.board["last_fetch_ok"] = self.clock[0]
                # boards come up one per 30 s turn of the rotation and fetch as they do
                up = 1 if how == "one-board" else min(self.boards, 1 + int(age // 30))
                self.board["boards_ok"] = max(self.board["boards_ok"], up)
        self.write_health()

    def run(self, cmd, user=None, timeout=None):
        self.calls.append((list(cmd), user, timeout))
        if cmd[0] == "git":
            a = cmd[3:]
            if a[0] == "fetch":
                return self.fetch_rc, "" if not self.fetch_rc else "fatal: unable to access the remote"
            if a[0] == "rev-parse":
                return 0, (self.origin if a[-1].startswith("origin/") else self.head) + "\n"
            if a[0] == "symbolic-ref":
                return (0, self.branch + "\n") if self.branch else (1, "")
            if a[0] == "status":
                return 0, self.dirty
            if a[0] == "merge-base":
                return (1 if self.ahead else 0), ""
            if a[0] == "log":
                return 0, f"{a[-1].split('..')[1][:7]} board: something new\n"
            if a[0] == "merge":
                if self.ahead:
                    return 128, "fatal: Not possible to fast-forward, aborting."
                self.head = a[-1]
                return 0, "Fast-forward\n"
            if a[0] == "reset":
                self.head = a[-1]
                return 0, f"HEAD is now at {a[-1][:7]}\n"
        if cmd[-1].endswith("/install.sh"):
            if self.during_install:
                self.during_install()
            if self.install_rc:
                return self.install_rc, "E: Unable to locate package python3-something"
            self.put(self.opt, "board.py", "# the board at " + self.head)
            self.put(self.opt, "VERSION", self.head + "\n")
            self.put(self.units, "tubeboard.service", "# unit at " + self.head)
            self.restart_board()
            return 0, "Installed. Settings page: http://tubeboard.local:8080"
        if cmd[-1] == "--help":
            broken = self.updater_broken and self.version() == NEW
            return (1, "SyntaxError: invalid syntax") if broken else (0, "usage: updater.py")
        if cmd[:2] == ["systemctl", "restart"]:
            self.restart_board()
            return 0, ""
        if cmd[:2] == ["systemctl", "daemon-reload"]:
            return 0, ""
        return 127, "not in the model: " + " ".join(cmd)

    def http_get(self, url, timeout=15):
        return None if self.board["version"] in self.portal_down else 200

    def go(self, mode="timer", clone=True):
        self.lines.clear()
        u = updater.Updater(clone=self.clone if clone else None, run=self.run,
                            now=lambda: self.clock[0], sleep=self.tick, http_get=self.http_get,
                            log=self.lines.append, opt=self.opt, backup=self.backup,
                            state=self.state, health=self.health, units=self.units,
                            python="python3")
        return u.main(mode)

    def ran(self, word):
        """Whether the updater ran a command with this word, or a path ending in it."""
        return any(t == word or t.endswith("/" + word) for c, _, _ in self.calls for t in c)

    def log(self):
        return "\n".join(self.lines)

    def bad(self):
        return self.get(self.state, "bad-commits") or ""

    def history(self):
        return self.get(self.state, "update-history") or ""


def snapshot(d):
    out = {}
    for root, _, files in os.walk(d):
        for name in files:
            with open(os.path.join(root, name), "rb") as f:
                out[os.path.relpath(os.path.join(root, name), d)] = f.read()
    return out


def test_updater():
    print("\nthe nightly update")
    me = pwd.getpwuid(os.getuid()).pw_name

    pi = FakePi()
    pi.origin = OLD
    rc = pi.go()
    check("nothing new: exit 0, nothing merged, installed or backed up",
          rc == 0 and not pi.ran("merge") and not pi.ran("install.sh")
          and not os.path.exists(pi.backup), pi.log())
    check("and the history on the card says it ran", "nothing new: aaaaaaa is installed" in pi.history())

    pi = FakePi(auto_update=False)
    rc = pi.go()
    check("auto_update false: the nightly run does nothing, not even a fetch",
          rc == 0 and not pi.calls and pi.version() == OLD, pi.calls[:2])
    rc = pi.go("now")
    check("but --now, typed by a person, goes ahead", rc == 0 and pi.version() == NEW, pi.log()[-300:])
    check("a hand-written \"off\" is off too", FakePi(auto_update="off").go() == 0)
    pi = FakePi()
    s = json.loads(pi.get(pi.opt, "settings.json"))
    del s["auto_update"]
    pi.put(pi.opt, "settings.json", json.dumps(s))
    check("an older settings.json with no auto_update key is on", pi.go() == 0 and pi.version() == NEW)
    check("auto_update is true by default in the board and in the shipped settings.json",
          board.DEFAULTS["auto_update"] is True
          and json.load(open(os.path.join(HERE, "settings.json")))["auto_update"] is True)

    pi = FakePi()
    pi.dirty = " M pi/board.py\n?? notes.txt\n"
    rc = pi.go()
    check("local changes in the clone: stopped, and the clone and the board left alone",
          rc == 1 and not pi.ran("merge") and not pi.ran("reset") and not pi.ran("install.sh")
          and pi.head == OLD and pi.version() == OLD and not os.path.exists(pi.backup), pi.log()[-300:])
    check("and the log names what was changed", "pi/board.py" in pi.log() and "notes.txt" in pi.log())
    pi = FakePi()
    pi.branch = "experiment"
    check("a clone on another branch is left alone",
          pi.go() == 1 and not pi.ran("merge") and "experiment" in pi.log())
    pi = FakePi()
    pi.ahead = True
    check("a clone with commits of its own is left alone",
          pi.go() == 1 and not pi.ran("merge") and not pi.ran("install.sh"))
    pi = FakePi()
    pi.fetch_rc = 128
    check("a fetch that fails stops the run", pi.go() == 1 and not pi.ran("install.sh")
          and "fetch failed" in pi.log())

    pi = FakePi()
    pi.put(pi.state, "bad-commits", f"{NEW} 2026-10-09 04:10:00 the board failed 3 draw(s)\n")
    rc = pi.go()
    check("a commit recorded as bad is not tried again, and the log says how to retry it",
          rc == 1 and not pi.ran("install.sh") and pi.version() == OLD
          and "failed here before" in pi.log() and "bad-commits" in pi.log(), pi.log()[-300:])

    # a good commit, all the way
    pi = FakePi()
    t0 = pi.clock[0]
    rc = pi.go()
    check("a good commit: exit 0, installed, and the board runs it",
          rc == 0 and pi.version() == NEW and pi.board["version"] == NEW and pi.head == NEW,
          pi.log()[-400:])
    merges = [(c, u) for c, u, _ in pi.calls if "merge" in c]
    check("merged as the clone's owner, fast-forward only, the very commit it checked",
          merges == [(["git", "-C", pi.clone, "merge", "--ff-only", NEW], me)], merges)
    check("every git command runs as the clone's owner",
          all(u == me for c, u, _ in pi.calls if c[0] == "git"))
    inst = [(c, u, t) for c, u, t in pi.calls if c[-1].endswith("/install.sh")]
    check("install.sh as root, with SKIP_COMITUP=1 and a time limit",
          len(inst) == 1 and inst[0][0][:3] == ["env", "SKIP_COMITUP=1", "bash"]
          and inst[0][1] is None and inst[0][2] == updater.INSTALL_SECONDS, inst)
    check("the backup holds the old code, the old settings and the old units",
          pi.get(pi.backup, "VERSION").strip() == OLD and pi.get(pi.backup, "board.py") == "# the board at " + OLD
          and pi.get(os.path.join(pi.backup, ".systemd"), "tubeboard.service") == "# unit at " + OLD
          and pi.get(pi.backup, "settings.json") == pi.get(pi.opt, "settings.json"))
    check("and the dated backup is left alone",
          pi.get(pi.opt + ".bak-2026-10-09", "board.py") == "# a dated backup, a person's")
    check("it watched the new board past the settings card's minute before trusting it, no longer",
          updater.OBSERVE_MIN <= pi.clock[0] - t0 <= updater.OBSERVE_MIN + 30, pi.clock[0] - t0)
    check("it checked the new updater starts", pi.ran("--help"))
    check("nothing recorded as bad, nothing left half done",
          pi.bad() == "" and not os.path.exists(os.path.join(pi.state, "updating.json")))
    check("every step is in the log, and the history says what was installed",
          all(w in pi.log() for w in ("auto_update is on", "new on main", "board before", "backup:",
                                      "merged", "install:", "health: good"))
          and "UPDATED: installed bbbbbbb (was aaaaaaa)" in pi.history(), pi.log())
    rc = pi.go()
    check("and the next night there is nothing new", rc == 0 and "nothing new: bbbbbbb" in pi.log())
    pi.origin = "c" * 40
    pi.go()
    check("a second update replaces the update backup, not adds one",
          pi.get(pi.backup, "VERSION").strip() == NEW
          and sorted(x for x in os.listdir(os.path.dirname(pi.opt)) if x.startswith("tubeboard"))
          == ["tubeboard", "tubeboard.bak-2026-10-09", "tubeboard.bak-update"],
          os.listdir(os.path.dirname(pi.opt)))

    # install.sh fails: everything goes back, and a setting saved meanwhile stays
    pi = FakePi()

    def meanwhile():
        s = json.loads(pi.get(pi.opt, "settings.json"))
        s["brightness"] = 55                      # someone on the settings page
        pi.put(pi.opt, "settings.json", json.dumps(s))
        pi.put(pi.opt, "board.py", "# half of " + NEW)   # install.sh got as far as the copy
        pi.put(pi.units, "tubeboard.service", "# unit at " + NEW)
    pi.during_install, pi.install_rc = meanwhile, 100
    rc = pi.go()
    check("install.sh fails: rolled back, exit 3, said loudly",
          rc == 3 and "ROLLING BACK" in pi.log() and "ROLLED BACK bbbbbbb" in pi.log(), pi.log()[-400:])
    check("the old code is back in /opt, and the old unit in systemd's directory",
          pi.version() == OLD and pi.get(pi.opt, "board.py") == "# the board at " + OLD
          and pi.get(pi.units, "tubeboard.service") == "# unit at " + OLD)
    check("with the settings.json saved during the update, not the backup's",
          json.loads(pi.get(pi.opt, "settings.json"))["brightness"] == 55)
    check("both services restarted, and the board runs the old code again",
          any(c == ["systemctl", "restart", "tubeboard.service", "tubeboard-portal.service"]
              for c, _, _ in pi.calls) and pi.board["version"] == OLD
          and "the old code is drawing again" in pi.log())
    check("the clone is reset to the commit before, as its owner",
          pi.head == OLD and any(c[3:] == ["reset", "--hard", OLD] and u == me for c, u, _ in pi.calls))
    check("an install that fails may be the night (apt, the network): not bad yet, tried again",
          pi.bad() == "" and "tried again tomorrow night (try 1 of 3)" in pi.log()
          and "ROLLED BACK bbbbbbb" in pi.history(), pi.log()[-300:])
    check("no scratch directories left in /opt",
          sorted(x for x in os.listdir(os.path.dirname(pi.opt)))
          == ["tubeboard", "tubeboard.bak-2026-10-09", "tubeboard.bak-update"])
    pi.during_install = None
    pi.go()
    rc = pi.go()
    check("the third night it fails, the commit is recorded as bad, with why",
          rc == 3 and pi.bad().startswith(NEW + " ") and "install.sh failed" in pi.bad(), pi.log()[-300:])
    installs = lambda: len([1 for c, _, _ in pi.calls if c[-1].endswith("/install.sh")])   # noqa: E731
    n = installs()
    rc = pi.go()
    check("and the next night that commit is skipped", rc == 1 and installs() == n, (rc, installs(), n))
    pi.origin, pi.install_rc, pi.during_install = "c" * 40, 0, None
    check("and a newer one on main is tried as usual", pi.go() == 0 and pi.version() == "c" * 40)

    # the new board installs but does not work
    for how, words in (("hung", "not drawing"), ("failing", "failed 1 draw"),
                       ("crashing", "restarted"),
                       # the review's cases: fine while the settings card shows, then not
                       ("late-failing", "failed 1 draw"), ("late-crash", "restarted")):
        pi = FakePi()
        pi.behaviour[NEW] = how
        rc = pi.go()
        check(f"a new board that is {how}: rolled back, and the commit recorded as bad",
              rc == 3 and pi.version() == OLD and pi.board["version"] == OLD and pi.head == OLD
              and pi.bad().startswith(NEW) and words in pi.bad(), (rc, pi.bad(), pi.log()[-300:]))
    for how, words, extra in (("offline", "no good fetch", {}),
                              ("one-board", "only 1 of the 3", {"stations": THREE["stations"]})):
        pi = FakePi(**extra)
        pi.behaviour[NEW] = how
        rc = pi.go()
        first = (rc == 3 and pi.version() == OLD and pi.bad() == "" and words in pi.log())
        pi.go()
        pi.go()
        check(f"a new board that is {how}: rolled back, tried again two more nights, then bad",
              first and pi.bad().startswith(NEW) and words in pi.bad(), (rc, pi.bad(), pi.log()[-300:]))
    pi = FakePi(stations=THREE["stations"])
    t0 = pi.clock[0]
    pi.go()
    check("three boards at 30 s: watched for the card's minute, one turn of the rotation and a margin",
          "watching it for 180 s" in pi.log() and pi.version() == NEW, pi.log()[-300:])
    pi = FakePi()
    pi.behaviour[NEW] = "failing"
    t0 = pi.clock[0]
    pi.go()
    # 20 s of it is watching the old code come back
    check("a failed draw is enough at once: no three-minute wait", pi.clock[0] - t0 < 60, pi.clock[0] - t0)
    pi = FakePi()
    pi.behaviour[OLD] = pi.behaviour[NEW] = "offline"
    pi.restart_board()
    pi.tick(30)
    check("a board that was not live before is not asked to be live after",
          pi.go() == 0 and pi.version() == NEW, pi.log()[-300:])
    pi = FakePi()
    pi.portal_down.add(NEW)
    check("a settings page that stops answering is rolled back",
          pi.go() == 3 and "settings page" in pi.bad() and pi.version() == OLD)
    pi = FakePi()
    pi.portal_down.update({OLD, NEW})
    check("one that did not answer before is not held against the new code", pi.go() == 0)
    pi = FakePi()
    pi.updater_broken = True
    check("a new updater.py that does not start is rolled back: the next update depends on it",
          pi.go() == 3 and "updater.py does not start" in pi.bad() and pi.version() == OLD)

    # --dry-run
    pi = FakePi()
    opt_before, state_before = snapshot(pi.opt), snapshot(pi.state)
    rc = pi.go("dry-run")
    changing = [c for c, _, _ in pi.calls if c[0] != "git" or c[3] in ("merge", "reset")]
    check("--dry-run: exit 0, and no merge, reset, install or restart", rc == 0 and not changing, changing)
    check("and nothing on disk changed: no backup, no lock, no history, no bad commit",
          snapshot(pi.opt) == opt_before and snapshot(pi.state) == state_before == {}
          and not os.path.exists(pi.backup))
    check("it says what a real run would do", "a real run would now back up" in pi.log()
          and "bbbbbbb board: something new" in pi.log(), pi.log())

    # a run that died part-way, after install.sh had copied half of the new code
    pi = FakePi(auto_update=False)
    shutil.copytree(pi.opt, pi.backup)
    pi.put(os.path.join(pi.backup, ".systemd"), "tubeboard.service", "# unit at " + OLD)
    pi.put(pi.opt, "board.py", "# half of " + NEW)
    pi.head = NEW
    pi.put(pi.state, "updating.json", json.dumps({"head": OLD, "installed": OLD, "new": NEW,
                                                  "started": "2026-10-10 04:12:00"}))
    rc = pi.go()
    check("the next run puts it right first, even with auto_update off",
          rc == 0 and pi.get(pi.opt, "board.py") == "# the board at " + OLD and pi.head == OLD
          and pi.board["version"] == OLD and not os.path.exists(os.path.join(pi.state, "updating.json"))
          and "DID NOT FINISH" in pi.log(), pi.log()[-400:])
    check("without marking the commit bad: a power cut is not its fault", pi.bad() == "")
    pi = FakePi(auto_update=False)
    shutil.copytree(pi.opt, pi.backup)
    pi.put(pi.opt, "VERSION", "c" * 40 + "\n")      # a person deployed by hand since
    pi.put(pi.opt, "board.py", "# the board at " + "c" * 40)
    pi.put(pi.state, "updating.json", json.dumps({"head": OLD, "installed": OLD, "new": NEW}))
    pi.go()
    check("but what a person installed since is left as it is",
          pi.get(pi.opt, "board.py") == "# the board at " + "c" * 40
          and not os.path.exists(os.path.join(pi.state, "updating.json")) and "left as it is" in pi.log())
    pi = FakePi()
    pi.put(pi.state, "updating.json", json.dumps({"head": OLD, "installed": OLD, "new": NEW}))
    shutil.rmtree(pi.backup, ignore_errors=True)
    check("with no backup to put back, it stops for a person rather than back up the half install",
          pi.go() == 1 and not pi.ran("install.sh") and "person" in pi.log())

    pi = FakePi()
    with open(os.path.join(pi.state, "lock"), "a") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        rc = pi.go("now")
    check("a run while another is going stops at once", rc == 1 and not pi.calls
          and "another update" in pi.log())

    pi = FakePi()
    pi.origin = OLD
    pi.put(pi.state, "clone", pi.clone + "\n")
    check("the clone is the one install.sh was last run from",
          pi.go(clone=False) == 0 and pi.clone in pi.log())
    pi = FakePi()
    check("no clone known: stopped, saying how to fix it",
          pi.go(clone=False) == 1 and "install.sh" in pi.log())

    check("as root, git runs as the owner, with the owner's HOME and no password prompt",
          updater.as_user(["git", "fetch"], "pi", euid=0, home="/home/pi")
          == ["runuser", "-u", "pi", "--", "env", "HOME=/home/pi", "GIT_TERMINAL_PROMPT=0", "git", "fetch"])
    check("not as root (a dry run by the owner), as it is",
          updater.as_user(["git", "fetch"], "pi", euid=1000) == ["git", "fetch"])
    rc, out = updater.run_command([sys.executable, "-c", "import time; print('started', flush=True); time.sleep(30)"],
                                  timeout=1)
    check("a command past its time limit is stopped, and says so", rc == 124 and "started" in out
          and "stopped after 1 s" in out, (rc, out))
    out = subprocess.run([sys.executable, os.path.join(HERE, "updater.py")], capture_output=True, text=True)
    check("run with no flag it only prints how to use it", out.returncode == 2 and "--now" in out.stderr)

    def text(name):
        with open(os.path.join(HERE, name)) as f:
            return f.read()
    tmr = text("tubeboard-update.timer")
    check("the timer: 04:00 local, up to half an hour later, catching up after a power cut",
          "OnCalendar=*-*-* 04:00:00" in tmr and "RandomizedDelaySec=30min" in tmr and "Persistent=true" in tmr)
    svc = text("tubeboard-update.service")
    check("the service runs the nightly mode as root, with time for an install, two checks and a fetch",
          "ExecStart=/usr/bin/python3 /opt/tubeboard/updater.py --timer" in svc and "Type=oneshot" in svc
          and "TimeoutStartSec=40min" in svc
          and updater.INSTALL_SECONDS + 2 * updater.HEALTH_SECONDS + 180 + 300 < 40 * 60)
    check("the board's unit makes /run/tubeboard for the health file",
          "RuntimeDirectory=tubeboard" in text("tubeboard.service")
          and os.path.dirname(updater.HEALTH) == "/run/tubeboard" and board.HEALTH_PATH != updater.HEALTH)
    inst = text("install.sh")
    check("install.sh copies the updater, writes VERSION, and installs and starts the timer",
          "updater.py /opt/tubeboard/" in inst and "rev-parse HEAD > /opt/tubeboard/VERSION" in inst
          and "tubeboard-update.service tubeboard-update.timer" in inst
          and "systemctl enable --now tubeboard-update.timer" in inst)
    check("and still parses", subprocess.run(["bash", "-n", os.path.join(HERE, "install.sh")]).returncode == 0)

    # the switch, on the settings page and from a shell
    write_portal(dict(BASE))
    check("the page offers the switch, ticked when the file does not say",
          'name="auto_update" value="1" checked' in portal.home())
    main_with("--auto-update", "off")
    check("--auto-update off saves false, and the page shows it unticked",
          portal.load().get("auto_update") is False and 'name="auto_update" value="1" >' in portal.home())
    main_with("--auto-update", "on")
    check("--auto-update on saves true", portal.load().get("auto_update") is True)
    srv = portal.ThreadingHTTPServer(("127.0.0.1", 0), portal.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
        c.request("POST", "/save-updates", "", {"Content-Type": "application/x-www-form-urlencoded"})
        r = c.getresponse(); r.read(); c.close()
        check("unticking it on the page saves false", r.status == 303 and portal.load()["auto_update"] is False)
        c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
        c.request("POST", "/save-updates", "auto_update=1", {"Content-Type": "application/x-www-form-urlencoded"})
        r = c.getresponse(); r.read(); c.close()
        check("and ticking it saves true", r.status == 303 and portal.load()["auto_update"] is True)
    finally:
        srv.shutdown()


if __name__ == "__main__":
    test_views()
    test_rail_board()
    test_fetch_and_render()
    test_ticker()
    test_loop()
    test_outage()
    test_wifi()
    test_cli_flags()
    test_health()
    test_shell()
    test_page()
    test_forget_wifi()
    test_run_command()
    test_board_user()
    test_handover_check()
    test_handover_flag()
    test_handover_doc()
    test_both_ends()
    test_updater()
    print()
    if FAILS:
        print(f"{len(FAILS)} FAILED: " + ", ".join(FAILS))
        sys.exit(1)
    print("all passed")
