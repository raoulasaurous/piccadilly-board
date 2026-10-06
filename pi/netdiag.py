#!/usr/bin/env python3
"""Why the board has no trains, in words someone standing in the room can act on.

The lesson from the ticker, learnt at a relative's house: the device knew exactly
which failure it had and wrote it to a port nobody could see, while the screen
said "...". Here the screen is 15 inches wide, so it can say the thing.

The decision is a pure table, verdict(), so it can be tested without a network.
Three cheap checks feed it: what comitup says the WiFi is doing, whether names
resolve, and whether a plain-http request for a known 204 gets a 204 back.
Anything else answering that request is something intercepting it - a hotel or
cafe sign-in page - which is a different fix from "no internet" and sends the
person to a different place.

    python3 netdiag.py      # run the checks and print the verdict
"""
import re
import socket
import subprocess

import requests

HOTSPOT_IP = "10.41.0.1"           # comitup's hotspot address since its 1.7
# Plain http on purpose: a sign-in portal intercepts cleartext far more reliably
# than it manages TLS, and 204 is the one answer nothing in the way would send.
PROBE_URL = "http://connectivitycheck.gstatic.com/generate_204"
SIGN_IN_CODES = {200, 301, 302, 303, 307, 308, 511}

HOTSPOT, CONNECTING, CONNECTED, OFFLINE, UNKNOWN = "HOTSPOT", "CONNECTING", "CONNECTED", "OFFLINE", "UNKNOWN"


def _run(args, timeout=5):
    """stdout of a command, or '' if it is missing, fails or hangs. Never raises:
    this runs inside the board's loop and a diagnosis must not take the screen down."""
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return r.stdout or ""
    except Exception:                               # noqa: BLE001
        return ""


def state():
    """(state, ssid). comitup knows best, because HOTSPOT and CONNECTING are its
    words; failing that, the hotspot address on an interface means HOTSPOT, and
    NetworkManager's own state covers the rest."""
    out = _run(["comitup-cli", "i"])
    m = re.search(r"State:\s*([A-Za-z]+)", out)
    if m and m.group(1).upper() in (HOTSPOT, CONNECTING, CONNECTED):
        c = re.search(r"Connection:\s*(.+)", out)
        ssid = c.group(1).strip() if c else ""
        st = m.group(1).upper()
        # comitup names its own hotspot as the connection while in HOTSPOT mode
        return st, ("" if st == HOTSPOT else ssid)
    if HOTSPOT_IP in _run(["ip", "-4", "-o", "addr"]):
        return HOTSPOT, ""
    nm = _run(["nmcli", "-t", "-f", "STATE", "general"]).strip().lower()
    ssid = ""
    for line in _run(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show", "--active"]).splitlines():
        parts = line.split(":")
        if len(parts) >= 2 and parts[1] == "802-11-wireless":
            ssid = parts[0]
            break
    if nm.startswith("connected"):
        return CONNECTED, ssid
    if nm.startswith("connecting"):
        return CONNECTING, ssid
    if nm in ("disconnected", "asleep", "disconnecting"):
        return OFFLINE, ""
    return UNKNOWN, ssid


def probe(timeout=5):
    """204, a sign-in page's status, 'dns' or 'noroute'. One request, no redirects:
    the redirect is the finding."""
    try:
        r = requests.get(PROBE_URL, timeout=timeout, allow_redirects=False)
        return r.status_code
    except requests.exceptions.ConnectionError as e:
        s = str(e).lower()
        if any(k in s for k in ("resolution", "name or service", "nodename", "getaddrinfo", "no address")):
            return "dns"
        return "noroute"
    except Exception:                               # noqa: BLE001
        return "noroute"


def verdict(st, ssid, probe_result, hotspot_name="TubeBoard-setup", source="tfl"):
    """The table. Returns (short, long): the short goes where the status word goes,
    the long after it. Both plain, both about what to do next."""
    feed = "National Rail" if source == "national-rail" else "Transport for London"
    where = f"on {ssid}" if ssid else "on this WiFi"
    if st == HOTSPOT:
        return ("No WiFi", f"On a phone, join the WiFi network {hotspot_name}, then open http://{HOTSPOT_IP}")
    if st == CONNECTING:
        return ("Joining WiFi", f"Connecting to {ssid}" if ssid else "Connecting to the WiFi")
    if st == OFFLINE:
        return ("No WiFi", "The board is not on any WiFi network")
    if st == CONNECTED:
        if probe_result == "dns":
            return ("No internet", f"Connected {where}, but nothing resolves. Is the router's internet down?")
        if probe_result == "noroute":
            return ("No internet", f"Connected {where}, but nothing is reachable. Is the router's internet down?")
        if probe_result in SIGN_IN_CODES:
            return ("WiFi needs sign-in", f"{ssid or 'This WiFi'} wants a web sign-in before it lets anything through")
        if probe_result == 204:
            return (f"{feed} not answering", "The internet is fine. Showing the last update")
        return ("No live data", "Showing the last update")
    return ("No live data", "Showing the last update")


def diagnose(hotspot_name="TubeBoard-setup", source="tfl"):
    """Run the checks and return the verdict. Call it only when a fetch has failed:
    a board that is fine has nothing to diagnose and no reason to make requests."""
    st, ssid = state()
    pr = probe() if st == CONNECTED else None
    return verdict(st, ssid, pr, hotspot_name, source), st


def address():
    """(hostname, first IPv4) for the address card, or ('', '') with no network."""
    host = (_run(["hostname"]).strip() or socket.gethostname() or "").split(".")[0]
    ip = (_run(["hostname", "-I"]).split() or [""])[0]
    if ip.startswith(HOTSPOT_IP.rsplit(".", 1)[0]):
        ip = ""                                     # the hotspot's own address is not a LAN address
    return host, ip


if __name__ == "__main__":
    (short, long), st = diagnose()
    print(f"state: {st}")
    print(f"{short}: {long}" if short else "no finding")
    print("address:", address())
