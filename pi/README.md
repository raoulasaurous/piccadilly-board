# Tube board on a Raspberry Pi

Live departure board for an Underground station, drawn straight to an HDMI
screen by a Raspberry Pi. No desktop, no browser. Data from TfL's open API,
no key needed. It can show one station or cycle through several.

## Files

| File | What |
|---|---|
| `board.py` | Fetches TfL every 30 s, draws the board with Pillow, writes it to `/dev/fb0` |
| `portal.py` | Settings page on the home network: search a station, pick the line, save |
| `settings.json` | Stations, lines, rows, refresh. The portal writes it, the board reloads it |
| `install.sh` | One-shot install on Raspberry Pi OS Lite |
| `bench.sh` | The power test: logs the Pi's under-voltage flag once a minute |
| `*.service` | systemd units so both start at boot and restart if they die |
| `test_rotation.py` | Offline tests for the rotation. TfL is stubbed, so it runs anywhere |

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

Open http://tubeboard.local:8080, type a station name under **Show one station
only**, tap it, tap the line. Done. The board redraws within a minute.

## Show more than one station

Under **Boards on the screen**, add a station. With two or more the screen cycles
between them, holding each for 20 seconds, and a row of dots under the clock says
which board is up and how many there are. **Remove** takes one off. Eight is the
most, and the last one cannot be removed.

A station on two lines is two boards: Highbury & Islington on the Victoria line
and on the Mildmay line are added separately, and each gets its own roundel,
colour and service status.

Each board fetches on its own 30 s clock, only while it is the one being shown,
so adding stations does not multiply the calls to TfL.

### From a shell, when the page is out of reach

The settings page only answers on the Pi's own network. When the board lives in
someone else's house, a Raspberry Pi Connect shell does the same job:

```bash
cd /opt/tubeboard
python3 portal.py --list-stations
python3 portal.py --add-station "Highbury & Islington" --line victoria
python3 portal.py --drop-station 2
python3 portal.py --rotate 20
```

Run it in `/opt/tubeboard`, not in the git clone: the live settings are there.
Each command edits `settings.json` and exits, and the board picks the change up
within a refresh with no restart. Line ids are the ones in the URL on TfL's own
site: `piccadilly`, `victoria`, `mildmay`, `windrush`, `elizabeth`, `dlr` and so
on. The Overground is six named lines, not one.

Only stops TfL gives arrivals for can be added, which means tube, DLR, Elizabeth
line and Overground. National Rail stations are not in that feed: Drayton Park,
for instance, is Great Northern, and nothing here can show it.

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
python3 board.py --png out.png --view 2   # the second board of the rotation
```
Renders one frame with live data to a file. Works on a Mac.

```bash
python3 test_rotation.py
```
Checks the rotation with every TfL call stubbed, so it runs with no network at
all. Worth running before deploying a change to how the boards are picked.

## A direction is missing from the screen

```bash
python3 board.py --explain
```

Prints what TfL answers for the configured station, and for every board on the
rotation in turn — how many predictions, on
which platforms, with which direction — and then the columns the board makes of
them. That separates the two causes, which have different fixes:

- **TfL sent trains one way only.** Read the status line first: during a closure or a
  suspension there really are no trains the other way, and the board is right to show
  that column empty. At a terminus it is the truth too. Otherwise suspect the station
  id — a station that is one name on the map can be two stop points at TfL, and only
  one of them carries both directions. Search the station again in the portal and pick
  the other result.
- **TfL sent both ways and the board drew one column.** That is a bug here. Keep the
  output — it holds the platform names and directions needed to fix it.

A direction with no trains keeps its column and says "No trains reported" under it,
rather than letting the other direction go full width: an empty column is a fact
about the service, and a board that quietly reshapes itself just looks broken.
