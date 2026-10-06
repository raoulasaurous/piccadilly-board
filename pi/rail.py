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
import re
import sys
import urllib.parse as up

import requests

# The REST version of the old OpenLDBWS SOAP service, same field names. The product
# path has moved once already (a "_2" was added for a while), so the base is a
# setting (rail_api_url) and this is only its default.
DEFAULT_URL = "https://api1.raildata.org.uk/1010-live-departure-board-dep/LDBWS/api/20220120"

# TfL's own ids for the National Rail operators, which TfL does carry line status
# for. Keeping to TfL's ids means the status call in board.py is the same one for
# every board. The compass is what the two columns are called: on the Great
# Northern out of Moorgate and King's Cross the London end is the south end all
# the way up, so the pair reads the way TfL's own boards do. Colours are the
# operators' brand colours, approximately: nothing on the wall checks them.
LINES = {
    "great-northern": {"name": "Great Northern", "colour": (99, 41, 107),
                       "inbound": "Southbound", "outbound": "Northbound"},
    "thameslink": {"name": "Thameslink", "colour": (233, 67, 141),
                   "inbound": "Southbound", "outbound": "Northbound"},
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
    "london st pancras international", "london euston", "london liverpool street",
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
        raise ValueError("no rail_api_key: get one at raildata.org.uk (Live Departure Board)")
    r = requests.get(url_for(base, crs, rows), headers={"x-apikey": key}, timeout=timeout)
    if r.status_code in (401, 403):
        raise ValueError(f"the rail feed refused the key (HTTP {r.status_code})")
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
        if name:
            out.append((name, (_get(d, "via") or "").strip()))
    return out


def predictions(board, now, line):
    """The trains on a board, in the shape TfL's Arrivals endpoint uses, so that
    board.py's group(), dedupe() and row_text() work on them unchanged.

    inbound/outbound is decided by the destination: a train to a London terminus is
    inbound. The platform name carries the line's compass word for that direction,
    which is what the two column headings are made from. Cancelled trains are left
    out: a cancelled train is not one anyone can catch, and the status line is where
    disruption belongs. A delayed train with no estimate keeps its timetable time."""
    tab = LINES.get(line, {})
    out = []
    for s in _get(board, "trainServices", default=[]) or []:
        if _get(s, "isCancelled", default=False) or (_get(s, "etd") or "").strip().lower() == "cancelled":
            continue
        std, etd = _get(s, "std") or "", _get(s, "etd") or ""
        when = _hhmm(etd) or _hhmm(std)
        secs = secs_until(when, now)
        # a train that left more than two minutes ago is still on the feed for a
        # moment; drawn, it would sit at "due" under a train that has gone
        if secs is None or secs < -120:
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
            # kept for --explain and for anyone reading the journal; nothing draws them
            "rail": {"std": std, "etd": etd,
                     "delayed": etd.strip().lower() == "delayed",
                     "operator": _get(s, "operator") or ""},
        })
    out.sort(key=lambda a: a["timeToStation"])
    return out


def messages(board):
    """Station notices, as plain text. The feed sends them as HTML fragments."""
    out = []
    for m in _get(board, "nrccMessages", default=[]) or []:
        text = m if isinstance(m, str) else (_get(m, "xhtmlMessage", "value", "Value") or "")
        text = re.sub(r"<[^>]+>", " ", str(text))
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            out.append(text)
    return out


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
        out(f"  {a['timeToStation'] // 60:3d} min  {a['platformName']:<24}  {a['towards']}")
    for m in messages(board):
        out("  notice: " + m[:160])


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit("usage: rail.py CRS KEY [LINE]")
    crs, key = sys.argv[1], sys.argv[2]
    line = sys.argv[3] if len(sys.argv) > 3 else "great-northern"
    explain(fetch(crs, key), dt.datetime.now(), line)
