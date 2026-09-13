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
