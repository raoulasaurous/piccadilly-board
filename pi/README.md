# Tube board on a Raspberry Pi

Live departure board for one Underground station, drawn straight to an HDMI
screen by a Raspberry Pi. No desktop, no browser. Data from TfL's open API,
no key needed.

## Files

| File | What |
|---|---|
| `board.py` | Fetches TfL every 30 s, draws the board with Pillow, writes it to `/dev/fb0` |
| `portal.py` | Settings page on the home network: search a station, pick the line, save |
| `settings.json` | Station, line, rows, refresh. The portal writes it, the board reloads it |
| `install.sh` | One-shot install on Raspberry Pi OS Lite |
| `bench.sh` | The power test: logs the Pi's under-voltage flag once a minute |
| `*.service` | systemd units so both start at boot and restart if they die |

## Set up the card (on a Mac or PC)

1. Raspberry Pi Imager, choose **Raspberry Pi OS Lite (64-bit)**.
2. In the settings gear: hostname `tubeboard`, enable SSH, your WiFi name and
   password, a username and password.
3. Write the card, put it in the Pi, power up, wait two minutes.

## Install

```bash
ssh <user>@tubeboard.local
git clone https://github.com/raoulasaurous/piccadilly-board.git
cd piccadilly-board/pi
sudo bash install.sh
sudo reboot
```

The board comes up on the screen about 30 s after power. The settings page is
at **http://tubeboard.local:8080** on any phone on the same WiFi.

## Change station

Open http://tubeboard.local:8080, type a station name, tap it, tap the line. Done.
The board redraws within a minute.

## If the WiFi changes

With no known WiFi the Pi starts its own hotspot, **TubeBoard-setup**. Join it
from a phone and a page appears to enter the new WiFi name and password. The
Pi then reboots on to the new network.

## The screen's identity is pinned

`install.sh` saves a copy of the screen's EDID to `/lib/firmware/edid/tubeboard.bin`
and tells the kernel to use that instead of asking the screen. Without it, cutting
power to the monitor leaves the board drawn at 1920x1080 but displayed at 1024x768,
zoomed into its own top-left corner, until someone reboots the Pi.

**So install with the screen plugged in and switched on.** If you swap the monitor
for a different one, delete that file and run the installer again.

## Bench tests before framing

```bash
bash bench.sh        # leave running an hour at the brightness you want
```
Any line saying "under-voltage" means the single lead is not enough at that
brightness. Then: pull the plug, put it back, and the board must come back
with no button pressed on the screen.

## Test the drawing anywhere

```bash
python3 board.py --png out.png
```
Renders one frame with live data to a file. Works on a Mac.

## A direction is missing from the screen

```bash
python3 board.py --explain
```

Prints what TfL answers for the configured station — how many predictions, on
which platforms, with which direction — and then the columns the board makes of
them. That separates the two causes, which have different fixes:

- **TfL sent trains one way only.** At a terminus that is simply the truth. Anywhere
  else, suspect the station id: a station that is one name on the map can be two
  stop points at TfL, and only one of them carries both directions. Search the
  station again in the portal and pick the other result.
- **TfL sent both ways and the board drew one column.** That is a bug here. Keep the
  output — it holds the platform names and directions needed to fix it.
