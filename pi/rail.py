#!/usr/bin/env python3
"""National Rail departures, for the stations TfL's feed does not carry.

TfL's unified API answers arrivals for the tube, the DLR, the Elizabeth line and
the Overground, and for nothing else. Drayton Park is Great Northern, so to put
it on the board the departures come from National Rail's own Live Departure
Board service, through the Rail Data Marketplace. That needs a key: a free
account at raildata.org.uk, subscribed to the "Live Departure Board" product,
and the consumer key pasted into settings.json as rail_api_key.

Everything here turns one departure board into predictions in the shape TfL
sends, so the grouping, the labels and the drawing in board.py never learned
there is a second feed. Pure functions parse; one function talks to the network.

    python3 rail.py DYP <key>      # print what the feed says for a station
"""
import datetime as dt
import html
import re
import sys
import urllib.parse as up

import requests

# The REST version of the old OpenLDBWS SOAP service, same field names. The product
# path has moved once already (from "-dep" to "-dep1_2"; the old one answers 401
# "Invalid ApiKey for given resource"), so the base is a setting (rail_api_url) and
# this is only its default. Seen live from the Mac on 7 Oct 2026.
DEFAULT_URL = "https://api1.raildata.org.uk/1010-live-departure-board-dep1_2/LDBWS/api/20220120"
USER_AGENT = "tubeboard/1.0 (+https://github.com/raoulasaurous/piccadilly-board)"

class KeyProblem(ValueError):
    """No key, or one the feed refused. A settings problem, not a network one: the
    board must not blame the feed or the WiFi for it."""


# TfL's own ids for the National Rail operators, which TfL does carry line status
# for. Keeping to TfL's ids means the status call in board.py is the same one for
# every board. The compass is what the two columns are called: on the Great
# Northern out of Moorgate and King's Cross the London end is the south end all
# the way up, so the pair reads the way TfL's own boards do. Colours are the
# operators' brand colours, approximately: nothing on the wall checks them.
LINES = {
    "great-northern": {"name": "Great Northern", "colour": (99, 41, 107),
                       "inbound": "Southbound", "outbound": "Northbound"},
    # No Thameslink: it runs through London, so "a train to a London terminus is
    # inbound" cannot name its directions. At Finsbury Park a Horsham train is
    # heading into town and that rule would have called it northbound.
    "southern": {"name": "Southern", "colour": (140, 198, 62),
                 "inbound": "Northbound", "outbound": "Southbound"},
    "southeastern": {"name": "Southeastern", "colour": (0, 175, 230),
                     "inbound": "Westbound", "outbound": "Eastbound"},
    "south-western-railway": {"name": "South Western Railway", "colour": (36, 57, 141),
                              "inbound": "Eastbound", "outbound": "Westbound"},
    "greater-anglia": {"name": "Greater Anglia", "colour": (215, 25, 32),
                       "inbound": "Westbound", "outbound": "Eastbound"},
    "c2c": {"name": "c2c", "colour": (177, 21, 123),
            "inbound": "Westbound", "outbound": "Eastbound"},
    "chiltern-railways": {"name": "Chiltern Railways", "colour": (0, 191, 243),
                          "inbound": "Southbound", "outbound": "Northbound"},
    "london-northwestern-railway": {"name": "London Northwestern Railway", "colour": (0, 191, 112),
                                    "inbound": "Southbound", "outbound": "Northbound"},
}

# A train to one of these is heading into town, whatever else is true of it. The
# list only has to be right for the London end of each line above.
LONDON_TERMINI = {
    "moorgate", "london kings cross", "london king's cross", "london st pancras",
    "london st pancras international", "london st pancras (intl)", "london st pancras intl",
    "london euston", "london liverpool street",
    "london bridge", "london victoria", "london waterloo", "london charing cross",
    "london cannon street", "london fenchurch street", "london marylebone",
    "london paddington", "london blackfriars", "city thameslink", "farringdon",
}


def url_for(base, crs, rows=15):
    """The departure board for one station. CRS is the three-letter code on every
    National Rail timetable, DYP for Drayton Park; the feed knows nothing else."""
    base = (base or DEFAULT_URL).rstrip("/")
    return f"{base}/GetDepartureBoard/{up.quote(crs.upper(), safe='')}?numRows={int(rows)}"


def fetch(crs, key, base=None, rows=15, timeout=10):
    """One board, as the feed sends it. Raises on anything but a 200 with JSON."""
    if not key:
        raise KeyProblem("no rail key: get one free at raildata.org.uk (Live Departure Board), "
                         "then sudo python3 portal.py --rail-key YOURKEY")
    # The marketplace gateway answers a plain "python-requests" user agent with a
    # 403 page and nothing else (seen 7 Oct 2026); any other name gets the board.
    r = requests.get(url_for(base, crs, rows), headers={"x-apikey": key, "User-Agent": USER_AGENT},
                     timeout=timeout)
    if r.status_code in (401, 403):
        raise KeyProblem(f"the rail feed refused the key (HTTP {r.status_code})")
    r.raise_for_status()
    board = r.json()
    if not isinstance(board, dict):
        raise ValueError("rail board: unexpected response")
    return board


def _get(d, *names, default=None):
    """The feed's JSON has been camelCase and PascalCase in different versions,
    so take the first spelling that is there."""
    for n in names:
        if isinstance(d, dict) and n in d and d[n] is not None:
            return d[n]
        if isinstance(d, dict):
            cap = n[:1].upper() + n[1:]
            if cap in d and d[cap] is not None:
                return d[cap]
    return default


def _hhmm(s):
    """'08:12' -> (8, 12), else None. The feed writes 'On time', 'Delayed',
    'Cancelled' and 'No report' in the same field."""
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", s or "")
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    return (h, mi) if h < 24 and mi < 60 else None


def secs_until(hhmm, now):
    """Seconds from now to a clock time on the board, across midnight. A time more
    than twelve hours away is read as already gone, because the board shows the next
    two hours and nothing is scheduled half a day ahead."""
    if hhmm is None:
        return None
    then = now.replace(hour=hhmm[0], minute=hhmm[1], second=0, microsecond=0)
    diff = (then - now).total_seconds()
    if diff < -12 * 3600:
        diff += 24 * 3600
    elif diff > 12 * 3600:
        diff -= 24 * 3600
    return int(diff)


def _destinations(service):
    out = []
    for d in _get(service, "destination", default=[]) or []:
        name = (_get(d, "locationName") or "").strip()
        # the feed's via is display text and already starts with the word, "via
        # Hertford North". Keep the place; the row adds the word back.
        via = re.sub(r"^\s*via\s+", "", (_get(d, "via") or "").strip(), flags=re.I)
        if name:
            out.append((name, via))
    return out


def feed_time(board):
    """The feed's own clock as wall time, or None. The Pi has no clock battery: for
    the first moments after a power cut its clock is whatever was saved at the last
    shutdown, and a board measured against that would read "182 min" until NTP steps
    it. The feed knows what time it is; the Pi only thinks it does.

    The feed writes generatedAt with its own offset ("...09:08:24.5485962+01:00") and
    std/etd as bare London clock times. The offset is dropped, not converted: that
    keeps the two on the same clock whatever timezone the Pi was set up in. Converted
    through a Pi left on UTC, every train would read an hour away."""
    try:
        t = dt.datetime.fromisoformat(str(_get(board, "generatedAt") or ""))
    except ValueError:
        return None
    return t.replace(tzinfo=None)


def predictions(board, now, line):
    """The trains on a board, in the shape TfL's Arrivals endpoint uses, so that
    board.py's group(), dedupe() and row_text() work on them unchanged.

    inbound/outbound is decided by the destination: a train to a London terminus is
    inbound. The platform name carries the line's compass word for that direction,
    which is what the two column headings are made from. A cancelled train stays,
    in its timetable place, marked cancelled: dropped, the 08:20 someone is waiting
    for just vanished from the board, and they kept waiting for it. A delayed train
    with no estimate keeps its timetable time."""
    tab = LINES.get(line, {})
    now = feed_time(board) or now
    out = []
    for s in _get(board, "trainServices", default=[]) or []:
        std, etd = _get(s, "std") or "", _get(s, "etd") or ""
        cancelled = bool(_get(s, "isCancelled", default=False)) or etd.strip().lower() == "cancelled"
        delayed = not cancelled and etd.strip().lower() == "delayed"
        # a cancelled train has no estimate; its place is its timetable time
        when = _hhmm(std) if cancelled else (_hhmm(etd) or _hhmm(std))
        secs = secs_until(when, now)
        # A train that left more than two minutes ago is still on the feed for a
        # moment; drawn, it would sit at "due" under a train that has gone. A train
        # the feed calls "Delayed" with no estimate has not left: its timetable time
        # passing is the delay, so it stays, and the row says "delayed", not "due".
        # A cancelled one goes on the same two minutes past its timetable time: by
        # then nobody on the platform is still waiting for it.
        if secs is None or (secs < -120 and not delayed):
            continue
        dests = _destinations(s)
        if not dests:
            continue
        dest, via = dests[0]
        inbound = dest.lower() in LONDON_TERMINI
        direction = "inbound" if inbound else "outbound"
        compass = tab.get(direction, "")
        platform = (_get(s, "platform") or "").strip()
        plat = f"Platform {platform}" if platform else ""
        platform_name = " - ".join(x for x in (compass, plat) if x)
        sid = str(_get(s, "serviceID", "serviceId", default="") or "")
        out.append({
            "id": sid,
            "vehicleId": sid,
            "platformName": platform_name,
            "direction": direction,
            "destinationName": dest,
            "towards": dest + (f" via {via}" if via else ""),
            "timeToStation": max(0, secs),
            "rail": {"std": std, "etd": etd, "delayed": delayed, "cancelled": cancelled,
                     # past its timetable time with no estimate: there are no minutes to show
                     "overdue": delayed and secs < 0,
                     "operator": _get(s, "operator") or ""},
        })
    out.sort(key=lambda a: a["timeToStation"])
    return out


def messages(board):
    """Station notices, as plain text. The feed sends them as HTML fragments, each
    under a "Value" key (seen live; the SOAP schema called it xhtmlMessage)."""
    out = []
    for m in _get(board, "nrccMessages", default=[]) or []:
        text = m if isinstance(m, str) else (_get(m, "xhtmlMessage", "value", "Value") or "")
        text = html.unescape(re.sub(r"<[^>]+>", " ", str(text)))   # the feed writes &nbsp;
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            out.append(text)
    return out


# Sentences in a notice that only send the reader somewhere else. A wall has
# nowhere to click, and on 7 Oct 2026 one was half of Drayton Park's notice.
ELSEWHERE = ("latest information", "national rail website", "nationalrail.co.uk", "journey planner")
NOTICE_SEP = "  ·  "
NOTICE_CHARS = 300


def notice(board, limit=NOTICE_CHARS):
    """The station's notices as one reason for the status line, or "" when nothing
    in them is left to say. On 7 Oct 2026 TfL's status for Great Northern said Good
    Service while this feed's notice said trains were delayed by up to ten minutes.

    Sentences that only point elsewhere are dropped, and several notices are
    joined with a dot between. Whole sentences are kept up to about `limit`
    characters: the footer scrolls a long line, but it draws the whole line as one
    strip first, and a notice with no full stop in it could run to thousands."""
    notices = []
    for m in messages(board):
        # a link's closing tag leaves a space before the full stop: "website ."
        m = re.sub(r"\s+([.,;:!?])", r"\1", m)
        keep = [s for s in re.split(r"(?<=[.!?])\s+", m)
                if s and not any(w in s.lower() for w in ELSEWHERE)]
        if keep and keep not in notices:
            notices.append(keep)
    text, last = "", None
    for i, s in ((i, s) for i, keep in enumerate(notices) for s in keep):
        joined = s if last is None else text + (" " if i == last else NOTICE_SEP) + s
        if last is not None and len(joined) > limit:
            break
        text, last = joined, i
    if len(text) > limit:
        # one sentence longer than the whole allowance: cut it at a word
        text = text[:limit].rsplit(" ", 1)[0].rstrip(" .,;:") + "..."
    return text


def explain(board, now, line, out=print):
    """What the feed said and what the board makes of it, for --explain."""
    name = _get(board, "locationName") or "?"
    crs = _get(board, "crs") or "?"
    services = _get(board, "trainServices", default=[]) or []
    out(f"{name} [{crs}]  National Rail, {LINES.get(line, {}).get('name', line)}")
    out(f"the feed returned {len(services)} service(s), generated {_get(board, 'generatedAt') or '?'}")
    for s in services:
        dests = ", ".join(d for d, _ in _destinations(s)) or "?"
        out(f"  {_get(s, 'std') or '--:--'}  {str(_get(s, 'etd') or ''):<9}  plat {str(_get(s, 'platform') or '-'):<3}  {dests}")
    preds = predictions(board, now, line)
    out(f"{len(preds)} of those become predictions:")
    for a in preds:
        r = a["rail"]
        mins = ("Cancelled" if r["cancelled"] else "delayed" if r["overdue"]
                else f"{a['timeToStation'] // 60:3d} min")
        out(f"  {mins:>9}  {a['platformName']:<24}  {a['towards']}")
    for m in messages(board):
        out("  notice: " + m[:160])
    reason = notice(board)
    if reason:
        out("the status reason, unless TfL reports trouble: " + reason)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit("usage: rail.py CRS KEY [LINE]")
    crs, key = sys.argv[1], sys.argv[2]
    line = sys.argv[3] if len(sys.argv) > 3 else "great-northern"
    explain(fetch(crs, key), dt.datetime.now(), line)
