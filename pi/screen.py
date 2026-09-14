#!/usr/bin/env python3
"""Screen brightness, over the HDMI cable.

Once the monitor is sealed in the frame its own buttons are unreachable, so the
Pi drives brightness itself with DDC/CI - the same channel the monitor uses to
tell the Pi what it is. Verified on the bench 2026-09-14: set 30, read back 30.

Nothing here is essential. If ddcutil is missing, the monitor ignores DDC, or
the call times out, every function says so and the board carries on drawing.
"""
import os
import subprocess
import time

VCP_BRIGHTNESS = "10"
VCP_POWER = "d6"
POWER_ON, POWER_OFF = "1", "4"

_last = {"level": None, "power": None, "checked": 0.0}


def _ddc(args, timeout=12):
    """Run ddcutil. Returns (ok, output). Never raises."""
    try:
        r = subprocess.run(["ddcutil", "--brief"] + args,
                           capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, (r.stdout or r.returncode and r.stderr or "").strip()
    except FileNotFoundError:
        return False, "ddcutil not installed"
    except subprocess.TimeoutExpired:
        return False, "the monitor did not answer"
    except Exception as e:                      # noqa: BLE001 - never kill the board
        return False, str(e)


def available():
    ok, _ = _ddc(["detect"], timeout=20)
    return ok


def get_brightness():
    """0-100, or None if the monitor will not say."""
    ok, out = _ddc(["getvcp", VCP_BRIGHTNESS])
    if not ok:
        return None
    # brief format: "VCP 10 C <current> <max>"
    parts = out.split()
    try:
        return int(parts[3])
    except (IndexError, ValueError):
        return None


def set_brightness(level):
    level = max(0, min(100, int(level)))
    ok, out = _ddc(["setvcp", VCP_BRIGHTNESS, str(level)])
    if ok:
        _last["level"] = level
    return ok, out


def set_power(on):
    ok, out = _ddc(["setvcp", VCP_POWER, POWER_ON if on else POWER_OFF])
    if ok:
        _last["power"] = bool(on)
    return ok, out


def wanted(settings, now):
    """What the screen should be doing at this moment.

    Returns (level, powered). Hours wrap past midnight, which is the normal case
    for an 'off overnight' window.
    """
    if not settings.get("dim_enabled", True):
        return int(settings.get("brightness", 100)), True

    def hhmm(v, default):
        try:
            h, m = str(v).split(":")
            return int(h) * 60 + int(m)
        except Exception:                       # noqa: BLE001
            return default

    mins = now.hour * 60 + now.minute
    day = hhmm(settings.get("day_from", "07:00"), 7 * 60)
    dim = hhmm(settings.get("dim_from", "21:00"), 21 * 60)
    off = hhmm(settings.get("off_from", "00:00"), 0)
    on = hhmm(settings.get("off_until", "06:00"), 6 * 60)

    def within(start, end, t):
        return start <= t < end if start <= end else (t >= start or t < end)

    if settings.get("off_overnight", False) and within(off, on, mins):
        return 0, False
    if within(day, dim, mins):
        return int(settings.get("brightness", 100)), True
    return int(settings.get("brightness_dim", 30)), True


def apply(settings, now, force=False):
    """Nudge the screen to where it should be. Cheap to call every loop: it only
    talks to the monitor when something actually needs to change."""
    level, powered = wanted(settings, now)
    changed = []
    if force or _last["power"] != powered:
        ok, err = set_power(powered)
        changed.append(f"power {'on' if powered else 'off'}" + ("" if ok else f" FAILED ({err})"))
    if powered and (force or _last["level"] != level):
        ok, err = set_brightness(level)
        changed.append(f"brightness {level}" + ("" if ok else f" FAILED ({err})"))
    return changed
