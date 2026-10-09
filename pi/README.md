# Tube board on a Raspberry Pi

Live departure board for a London station, drawn straight to an HDMI screen by a
Raspberry Pi. No desktop, no browser. One station, or several in turn. Tube, DLR,
Elizabeth line and Overground come from TfL's open API with no key; National Rail
stations come from National Rail's own feed with a free key.

## Files

| File | What |
|---|---|
| `board.py` | Fetches the trains, draws the board with Pillow, writes it to `/dev/fb0`. Also the WiFi setup screen |
| `portal.py` | Settings page on the home network, and the same settings from a shell |
| `rail.py` | National Rail departures, turned into the shape TfL sends so the rest of the board never notices |
| `netdiag.py` | Why there are no trains, in words: no WiFi, needs sign-in, no internet, or the feed is down |
| `screen.py` | Brightness and power over the HDMI cable (DDC/CI) |
| `updater.py` | The nightly update: installs `main` if it moved, checks the board still works, puts the old code back if not |
| `settings.json` | Stations, lines, rows, refresh, rotation, the rail key. The portal writes it, the board reloads it |
| `install.sh` | One-shot install on Raspberry Pi OS Lite. Also what the updater runs to install a change |
| `bench.sh` | The power test: logs the Pi's under-voltage flag once a minute |
| `test_rotation.py` | 284 offline checks. Every feed and every command is stubbed, so it runs anywhere |
| `*.service`, `*.timer` | systemd units: both programs start at boot and restart if they die, and the update runs each night |

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
at **http://tubeboard.local:8080** on any phone on the same WiFi, and for the
first minute after it joins the WiFi the board's footer says so, with the IP, in
a box with a countdown.

## Deploy a change

**A change merged to `main` is on the board the next night.** Around 4 am
`tubeboard-update.timer` runs `updater.py`. If `main` has moved, it backs up
`/opt/tubeboard`, pulls `main` into the clone (fast-forward only, never over a
local change), runs `install.sh` with `SKIP_COMITUP=1`, and watches the board
until the settings card has gone and every board has come round once (2 to 6
minutes). If the board crashes, fails a draw, stops drawing, or stops fetching when
it fetched before, it puts the old code back and restarts both services. A crash or
a failed draw means that commit is never tried again; an install or a fetch that
fails may be the night, so it is tried again the next night, three times at most.

```bash
sudo python3 /opt/tubeboard/updater.py --now      # update now, by hand
python3 /opt/tubeboard/updater.py --dry-run       # what it would do; changes nothing
journalctl -u tubeboard-update                    # what it did, since the last boot
cat /var/lib/tubeboard/update-history             # one line per night, kept across reboots
sudo python3 /opt/tubeboard/portal.py --auto-update off   # stop it (or untick it on the settings page)
```

By hand, as before (the first install of the updater comes this way):

```bash
sudo cp -a /opt/tubeboard "/opt/tubeboard.bak-$(date +%F)"   # the way back
cd ~/piccadilly-board && git pull
cd pi && sudo SKIP_COMITUP=1 bash install.sh
```

The installer copies the programs, keeps `settings.json`, restarts both
services and turns on the nightly timer. `SKIP_COMITUP=1` leaves the WiFi
hotspot alone. To roll back by hand:

```bash
sudo cp /opt/tubeboard.bak-<date>/*.py /opt/tubeboard/ && sudo systemctl restart tubeboard tubeboard-portal
```

## Change station

Open http://tubeboard.local:8080, type a station name under **Show one station
only**, tap it, tap the line. Done. The board redraws within a minute.

## Show more than one station

Under **Boards on the screen**, add a station. With two or more the screen cycles
between them, 30 seconds each by default (a box under the list sets anything
from 5 to 300), and a row of dots at the bottom right, in each board's line
colour, says which board is up and how many there are. **Remove** takes one off. Eight is the most, and the last one
cannot be removed.

A station on two lines is two boards: Highbury & Islington on the Victoria line
and on the Mildmay line are added separately, each with its own roundel, colour
and service status.

Each board fetches on its own 30 s clock, only while it is the one being shown.
At the default 30 s rotation three boards cost the same number of calls as one;
at short intervals they cost more.

## National Rail stations

TfL's feed does not carry National Rail. Drayton Park is Great Northern, so it
comes from National Rail's Live Departure Board, through the Rail Data
Marketplace. That needs a free key:

1. Make an account at **raildata.org.uk**.
2. Subscribe to the product called **Live Departure Board** (the public one).
3. Copy the consumer key it gives you.

Then under **National Rail** on the settings page: the station's three-letter
code (DYP for Drayton Park; every National Rail timetable shows them), the
operator, and the key. The key is saved once and kept (typing it later, for a
station already on the list, saves it too). Trains to a London terminus are the
southbound (or inbound) column; the rest are the other. A cancelled train stays
in its place and its row says "Cancelled" until two minutes after its time; a
delayed one with no estimate stays and its row says "delayed" once its time has
gone. When TfL says Good Service but National Rail has a notice for the station
(trains delayed, say), the status line says "Notice" and gives it. If the key is
missing or wrong the screen says so in those words; if the feed does not know
the station code, likewise.

## From a shell, when the page is out of reach

The settings page only answers on the Pi's own network. When the board lives in
someone else's house, a Raspberry Pi Connect shell does the same job:

```bash
cd /opt/tubeboard
python3 portal.py --list-stations
sudo python3 portal.py --add-station "Highbury & Islington" --line victoria
sudo python3 portal.py --rail-key YOURKEY
sudo python3 portal.py --add-rail DYP --line great-northern
sudo python3 portal.py --drop-station 2
sudo python3 portal.py --rotate 30
sudo python3 portal.py --handover-check      # what is left before the hand-over; it only reads
sudo python3 portal.py --auto-update off      # no nightly updates; "on" turns them back on
```

Run it in `/opt/tubeboard`, not in the git clone: the live settings are there.
The `sudo` is because the installer and the service write `settings.json` as
root; listing needs none. Each command edits the file and exits, and the board
picks the change up within a refresh, no restart. Line ids are the ones in the
URL on TfL's own site: `piccadilly`, `victoria`, `mildmay`, `windrush`,
`elizabeth`, `dlr`, and for National Rail `great-northern`, `thameslink`,
`southern` and so on. The Overground is six named lines, not one.

## WiFi

With no known WiFi the Pi starts its own hotspot, **TubeBoard-setup**, and
after a minute the screen says so: JOIN WIFI, the name large, a QR code a phone
camera reads as an offer to join, and the three steps. Join it, and a page
appears to choose the home WiFi and type its password. While that join is
happening the screen says WIFI OK and which network. Then the trains come up.
(The minute's wait is because the Pi raises its hotspot briefly on every boot
before joining the known WiFi; a power cut should not show setup instructions.)

When the WiFi is there but the trains are not, the footer says which it is:
**No internet** on that network, **WiFi needs sign-in** (a cafe or hotel page
is in the way), or **Transport for London not answering** when the internet is
fine and the feed is not.

Before the board goes to someone else, **WiFi > Forget the WiFi** on the
settings page (type FORGET) makes the Pi forget every network it knows, so the
setup screen comes up at their house. From a shell: `sudo python3 portal.py
--forget-wifi`. The board keeps drawing throughout.

## Before the board goes to someone else

```bash
sudo python3 /opt/tubeboard/portal.py --handover-check
```

The check only reads. It changes nothing and writes no file. It prints one line
for each thing that may be left, and each line starts with `OK` or `TO DO`:

- the saved WiFi networks that are not access points: how many, and their names
  (to the terminal only);
- the day the console password last changed, from `passwd -S`, against the day
  set in `portal.py` (`PASSWORD_EXPOSED_ON`). A password older than that day is
  the one that was exposed. The check never reads or prints a password;
- whether Raspberry Pi Connect is signed in, asked as the board's login;
- the SSH keys that can log in as that login: how many, and their comments. The
  key itself is never printed;
- the hotspot's name from `/etc/comitup.conf`, which must be `TubeBoard-setup`;
- whether the rail key is saved (never shown), the boards on the rotation, the
  rows and the rotation seconds;
- the self-updater timer, `tubeboard-update.timer`, if there is one. With none,
  the line is `OK`: updates are by hand.

The last line says what to do next, in order: a fix that the phone will show,
then the phone test of the setup screen, then the password, then the rest, and
the WiFi last. Forgetting the WiFi cuts the connection the check runs over.

The board's login is the owner of the git clone, or the person who ran `sudo`.
Run the check with `sudo`: `passwd -S`, `runuser` and the key file need it. A
command that is missing or does not answer is reported on its line, and the
check goes on. Run it from `/opt/tubeboard`, where the live settings are.

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
Any line saying "under-voltage" means the power is not enough at that
brightness. Then: pull the plug, put it back, and the board must come back
with no button pressed on the screen.

## Test the drawing anywhere

```bash
python3 board.py --png out.png                  # the first board, live data
python3 board.py --png out.png --view 2         # the second board of the rotation
python3 board.py --png out.png --setup          # the WiFi setup screen
python3 rail.py DYP YOURKEY                     # what the rail feed says for a station
python3 netdiag.py                              # the network verdict, on this machine
```
All work on a Mac. In the clone `settings.json` holds one board, so `--view 2`
there draws board 1 and says so; the rotation lives in `/opt/tubeboard`.

```bash
python3 test_rotation.py
```
284 checks with every feed and command stubbed, so it runs with no network at
all. The updater's are run against a model of the Pi: git, install.sh, systemctl
and the board are all stand-ins. Run it before deploying a change to how the boards are picked or drawn.

## A direction is missing from the screen

```bash
cd /opt/tubeboard && python3 board.py --explain
```

Prints, for every board on the rotation in turn, what the feed answered for the
station (how many predictions, on which platforms, with which direction) and
then the columns the board makes of them. That separates the two causes, which
have different fixes:

- **The feed sent trains one way only.** Read the status line first: during a
  closure or a suspension there really are no trains the other way, and the board
  is right to show that column empty. At a terminus it is the truth too. Otherwise
  suspect the station id: a station that is one name on the map can be two stop
  points at TfL, and only one of them carries both directions. Search the station
  again in the portal and pick the other result.
- **The feed sent both ways and the board drew one column.** That is a bug here.
  Keep the output: it holds the platform names and directions needed to fix it.

A direction with no trains keeps its column and says "No trains reported" under it,
rather than letting the other direction go full width: an empty column is a fact
about the service, and a board that quietly reshapes itself just looks broken.
