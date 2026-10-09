# Handover - the Tube Board

Written 19 Sept 2026, rewritten in the small hours of 7 Oct 2026 by a cloud
session that could not reach the Pi, TfL or National Rail, and brought up to
date on 9 Oct 2026, the first day the new code ran on the Pi, for whoever picks
this up next, human or model. It covers the whole project. Most of it exists
nowhere else in the repo.

**This repo is PUBLIC** (GitHub Pages serves `index.html`). Keep WiFi names,
passwords, addresses and personal details out of it, including out of this file.

**Sister project:** `raoulasaurous/esp32-matrix-ticker` has its own
`docs/HANDOVER.md`. Its WiFi setup is the model this board's now follows; the
two share nothing else.

## What this is

A live departure board for a Raspberry Pi 3 A+ driving a 15.6" 1080p monitor:
no desktop, no browser. Built by Raoul as a gift for a friend (called "the
recipient" here). The web version (`index.html`) came first; the Pi board copies
its layout. It shows one station or cycles through several, from TfL's feed
(tube, DLR, Elizabeth line, Overground) and from National Rail's (everything
else). It will hang on a wall in a deep box frame with a card mount and no glass.

## State on 9 Oct 2026

| Area | State |
|---|---|
| `main` | PRs #1-#4, then the 7 Oct rail fixes and the 9 Oct screen changes (below), all pushed. |
| **What the Pi runs** | **`main` as of 9 Oct** (`git log -1` in `~/piccadilly-board` on the Pi says which commit), installed into `/opt/tubeboard`. Dated backup of the pre-PR-#1 code: `/opt/tubeboard.bak-2026-10-09`. **No self-updater yet**: it arrives with the next deploy by hand (see "Deploying"). |
| The Pi itself | Plugged in at Raoul's on 9 Oct. It came up after the 6 Oct unplug with no card trouble. `throttled=0x0`, 47 C with the scroll running. |
| On the screen | Arsenal (Piccadilly), Highbury & Islington (Victoria), Drayton Park (Great Northern, National Rail), 30 s each. All three draw both columns, checked with `--explain` and from the framebuffer. |
| National Rail key | Raoul's raildata.org.uk account, Live Departure Board product. Saved on the Pi in `/opt/tubeboard/settings.json`; not in the repo. |
| Tested | 284 offline checks (`pi/test_rotation.py`, the updater's included), 18 in headless Chrome (`test_index.py`). A three-lens review of the 9 Oct scroll, each finding verified, some on the Pi; all fixed. |
| Power | Settled 17 Sept. Do not re-test. One brick, two cables. |
| Brightness and night dimming | Works, over the HDMI cable (DDC/CI) |
| Remote access | Pi Connect (remote shell), from anywhere. SSH from Raoul's Mac on the same WiFi: `ssh locklinestudio@tubeboard.local` worked on 9 Oct, which is how the 9 Oct deploys were done. |
| WiFi hand-over flow | The setup screen draws on the Pi with the real name and a QR (9 Oct, `--png --setup`). Still unseen on a phone. |
| Pi mounting sled (CAD) | Designed 17 Sept, not printed. `case/sled.3mf` (sled and clamp, flat on the bed) made 7 Oct. |
| Frame and mount | Measured 7 Oct, order **parked by Raoul**: see "Physical build". |

## Start here, on the Mac

```bash
cd ~/Downloads/piccadilly-board && git checkout main && git pull
cd pi && python3 test_rotation.py            # 284 checks, no network needed
python3 board.py --png /tmp/b.png            # live TfL: the Mac can reach it, this session could not
python3 board.py --png /tmp/s.png --setup    # the WiFi setup screen
cd .. && python3 test_index.py               # the web version, in headless Chrome (18 checks)
```

Then, in this order:

1. ~~Get the rail key.~~ Done 7 Oct. `python3 rail.py DYP THEKEY` prints the
   feed's own account of Drayton Park and what the board makes of it; it worked
   from the Mac. If the URL is ever refused again, the product path has moved: see
   "National Rail boards" below.
2. **Deploy by hand once** (next section). The Pi must be plugged in and online.
   That deploy installs the nightly updater; after it, merging to `main` is deploying.
3. **Add the stations** over the same shell (in the deploy section).
4. Ask for a photo of the screen. Nobody has seen any of this on the real one.

## Deploying: what is merged to main reaches the wall the next night

**Warning: whatever is merged to `main` is on the recipient's wall the next
night**, with nobody touching the Pi. Merge only what has passed
`test_rotation.py` and has been seen to work (a `--png` at the least). The health
check below catches a board that stops drawing or fetching. It does not catch a
board that draws the wrong thing.

The board runs from `/opt/tubeboard/`, which `install.sh` copies into. It does
not run from the git clone. Each night `tubeboard-update.timer` runs
`/opt/tubeboard/updater.py --timer` as root, at 04:00 local and up to 30 minutes
later. A Pi that was off at that time runs it when it next starts. An install
restarts the board, so the settings card shows for a minute then.

**The Pi does not have it yet.** `install.sh` installs it, so it arrives with the
next deploy by hand (below). From then on the timer does the deploys.

What one run does, in order:

1. `"auto_update": false` in `/opt/tubeboard/settings.json`: it stops here.
2. As the clone's owner (`runuser`): `git fetch origin main`. If `main`, the
   clone and `/opt/tubeboard/VERSION` are the same commit, it stops. It also
   stops, and changes nothing, for local changes in the clone, a branch other
   than `main`, commits that are not on `main`, or a commit it rolled back before.
3. It notes whether the board is live (a good fetch in the last 5 minutes) and
   whether the settings page answers on port 8080.
4. It copies `/opt/tubeboard` and the four systemd units to
   `/opt/tubeboard.bak-update`. That one backup is replaced each time; the dated
   backups are never touched.
5. `git merge --ff-only` to that exact commit, as the clone's owner.
6. `SKIP_COMITUP=1 bash install.sh` as root, with a 15-minute limit. A full run
   restarts comitup, which can drop the WiFi.
7. The health check, for up to 3 minutes. The new `updater.py` must start. The
   board must be a new process that runs the new commit, must draw for 20 s with
   no failed draw, and must be live again if it was live before. The settings
   page must answer if it answered before.
8. If the install or the check fails, it rolls back (below).

The board writes `/run/tubeboard/health.json` on every frame: pid, start time,
commit (from `/opt/tubeboard/VERSION`, which `install.sh` writes), draws, failed
draws, last draw, last good fetch. It is on tmpfs, so the SD card sees none of it.

**Rollback** happens by itself. The updater records the commit in
`/var/lib/tubeboard/bad-commits` with the reason. It puts
`/opt/tubeboard.bak-update` back as `/opt/tubeboard`, keeps the `settings.json`
that is there now, and puts the old units back. It restarts both services, resets
the clone to the commit before, and logs whether the old code draws again. The
run ends with `ROLLED BACK` and the reason in the log, and exit code 3. A bad
commit is never tried again; the next commit merged to `main` is. A run that a
power cut stops part-way is put right at the start of the next run, and that
commit is not marked bad.

An install that fails for a reason that is not the commit's (apt, the network)
also marks the commit bad. To try it again, delete its line in `bad-commits` and
run the updater by hand. A commit that changes the kernel command line needs a
reboot: `install.sh` says so in the log, and the updater does not reboot.

**Stop it:** untick **Install new versions overnight** on the settings page, or
`sudo python3 /opt/tubeboard/portal.py --auto-update off` (`on` starts it again).
`sudo systemctl disable --now tubeboard-update.timer` also stops it, but the next
`install.sh` turns the timer on again; the setting stays.

**Run it by hand:**

```bash
sudo python3 /opt/tubeboard/updater.py --now   # update now; runs even when auto_update is off
python3 /opt/tubeboard/updater.py --dry-run    # what a run would do; it fetches main and changes nothing else
```

**See what it did:**

```bash
journalctl -u tubeboard-update                 # every step, since the last boot only (journald is volatile)
cat /var/lib/tubeboard/update-history          # one line per run, kept across reboots
cat /var/lib/tubeboard/bad-commits             # the commits it rolled back, and why
systemctl list-timers tubeboard-update.timer   # when it runs next
cat /run/tubeboard/health.json                 # the board's own account, now
```

Exit codes: 0 updated or nothing to do, 1 stopped or an error, 3 rolled back.

### By hand: the first time, and when the updater stops

**Nobody needs to be in the house.** Raoul opens connect.raspberrypi.com, picks
`tubeboard`, opens the remote shell and pastes. This works while the Pi is
online and Connect is signed in (it was on 17 Sept). The SSH key only works on
the same network as the Pi; when the Mac is on it, a session can do the whole
deploy itself (`ssh locklinestudio@tubeboard.local '...'`), as on 9 Oct.

Every deploy restarts the board, so the settings card shows for a minute after
each one. That is expected, not a fault.

```bash
sudo cp -a /opt/tubeboard "/opt/tubeboard.bak-$(date +%F)"   # the way back; a dated name never nests
cd ~/piccadilly-board && git status --short && git pull      # status should print nothing first
cd pi && sudo SKIP_COMITUP=1 bash install.sh
```

The installer copies `board.py portal.py screen.py rail.py netdiag.py updater.py`,
writes `VERSION`, installs `python3-qrcode`, keeps `settings.json`, restarts both
services and turns on the nightly timer. It no longer asks for a reboot on every
re-run (that was a bug). Then:

```bash
cd /opt/tubeboard
sudo python3 portal.py --add-station "Highbury & Islington" --line victoria
sudo python3 portal.py --rail-key THEKEY
sudo python3 portal.py --add-rail DYP --line great-northern
python3 portal.py --list-stations
python3 board.py --explain
```

`--explain` prints every board in turn: what the feed answered and the columns
made of it. Both columns on each is the check. **Run it in `/opt/tubeboard`**,
not in the clone: the live settings are there. That trap cost an hour once.

What the screen does on every boot from now on: "Starting up" for the first
minute; if comitup is still on its hotspot after that, the setup screen; the
board as soon as a fetch lands. A power cut in the recipient's house therefore
shows the board, not setup instructions, unless the WiFi really is gone.

The `sudo` on the writing commands is because the installer and the service
write `settings.json` as root. Without it the command ends in one line saying
so. Optional extra boards: `--add-station "Highbury & Islington" --line mildmay`
(the Overground there, both ways) and `--line windrush`.

**Rollback by hand:**
```bash
sudo cp /opt/tubeboard.bak-<date>/*.py /opt/tubeboard/ && sudo systemctl restart tubeboard tubeboard-portal
```
`/opt/tubeboard.bak-update` is the updater's backup from its last install; the
same command works with it in place of a dated one. The updater installs `main`
again the next time `main` moves, so turn it off first if the old code must stay.
The old `board.py` ignores the new keys in `settings.json` and shows the first
TfL board on the rotation, so the settings can stay. The backup has no `rail.py`
or `netdiag.py`; leave the new ones in place, the old programs never import them.

**Shut down before pulling power:** `sudo shutdown -h now`.

## The rotation

`settings.json` has a `stations` list of `{source, line, station_id,
station_name}` and a `rotate_seconds` (default 30). An empty list means one
board, built from the top-level `line` and `station_id`, which is every install
made before this. The portal keeps those top-level keys pointing at the first
TfL board on the rotation, so an older `board.py` still shows a real station.

`station_views()` turns the settings into one flat settings dict per board, so
`fetch()` and `render()` never learned there is more than one. Each board keeps
its own data, last-updated time and failure count, keyed by source, line and
stop. The loop draws before it fetches, so a switch never waits on the network.
Only the board on screen is fetched, on its own 30 s clock. A row of dots at the
bottom right, just above the footer rule, says which board is up and how many
there are: each filled in its board's line colour, the current one larger and
ringed in white. Eight is the most.

"No live data" is three minutes since the board's last good fetch, whatever the
rotation; a failed fetch is redrawn at once.

Editing the list: the settings page (**Boards on the screen**) on the Pi's own
network, or the shell commands above from anywhere. Remove works by the board's
identity, not its position.

## National Rail boards

TfL's unified API carries arrivals for the tube, DLR, Elizabeth line and
Overground only. Drayton Park is Great Northern, a National Rail station on the
Northern City Line. Its departures come from National Rail's Live Departure
Board service through the Rail Data Marketplace:

- `rail.py`: `fetch()` calls `GetDepartureBoard/{CRS}` with the key in an
  `x-apikey` header; `predictions()` turns the board into TfL-shaped arrivals.
  Inbound is a train to a London terminus (`LONDON_TERMINI`); the platform name
  carries the line's compass word (`LINES[...]["inbound"]`, Southbound for Great
  Northern) so the two columns read as TfL's do. Cancelled trains are left out.
  A train the feed calls "Delayed" with no estimate stays, and once its timetable
  time has gone its row says `delayed` instead of minutes. A train that left more
  than two minutes ago is dropped. Minutes are measured against the feed's own
  clock (`generatedAt`), because the Pi's is wrong for a moment after every power
  cut until NTP steps it. Two services expected the same minute are two rows.
- No key, a refused key, or a stop the feed does not know is said on the screen
  in those words ("Rail key needed", "...does not know this stop"), never as
  "not answering": those are this board's problems, and the WiFi is not asked.
- Thameslink is not in the operator table: it runs through London, so "a train
  to a London terminus is inbound" cannot name its directions.
- A station entry is `{"source": "national-rail", "line": "great-northern",
  "station_id": "DYP", "station_name": "Drayton Park"}`. The status line stays
  TfL's: it publishes one for the operators under the same line ids.
- The key is `rail_api_key` in `settings.json`; `rail_api_url` overrides the
  base URL if the product path moves (it has moved once before). The default is
  `https://api1.raildata.org.uk/1010-live-departure-board-dep1_2/LDBWS/api/20220120`,
  the one the product's Specification tab publishes. The old `-dep` path answers
  401 "Invalid ApiKey for given resource" to a good key.
- The roundel bar says NATIONAL RAIL. It is TfL's mark with the wrong words on
  it, chosen so the header stays one shape; Raoul may want the double arrow.

**Seen live on 7 Oct 2026**, from the Mac, with Raoul's key. The field names
were as written (`trainServices`, `std`, `etd`, `platform`, `isCancelled`,
`serviceID`, `destination[].locationName`, `via` with the word in it, `generatedAt`).
Three things were not, and are fixed:

1. The product path is `-dep1_2`, not `-dep` (above).
2. The gateway answers the `python-requests` user agent with a 403 HTML page and
   nothing else; any other name gets the board. `fetch()` sends `tubeboard/1.0`.
   A 403 from this feed is therefore a client problem, never the key.
3. `generatedAt` carries an offset (`+01:00`) and seven decimals. `feed_time()`
   drops the offset instead of converting through the Pi's timezone, so the
   feed's clock and its `std`/`etd` stay on the same clock on a Pi left on UTC.

Also: `nrccMessages` are `[{"Value": "<p>…</p>"}]` with `&nbsp;` entities, now
unescaped. `test_rotation.py`'s `RAIL_BOARD` is the live shape with made-up
trains. That morning DYP showed 15 services, Moorgate trains on platform 1
(Southbound), Welwyn / Hertford / Stevenage on platform 2 (Northbound), and the
columns came out that way.

## The WiFi screen and the diagnosis

The ticker's lesson: the device knew exactly which failure it had and wrote it
to a port nobody could see, while the panel said "...". This screen can say it.

- **HOTSPOT** (comitup has no network to join, for more than a minute after the
  board started): `render_setup()` draws JOIN WIFI, the hotspot's name large, a
  QR code (`WIFI:T:nopass;S:<name>;;`, or the WPA form if `ap_password` is set;
  dark on light because phone cameras read inverted codes badly) and three steps
  naming `http://10.41.0.1`. Name and password come from `/etc/comitup.conf`.
- **CONNECTING**: WIFI OK and the network's name.
- **CONNECTED but no trains**: a normal board with one footer line. If the feed
  answered with a refusal (no key, key refused, 404 for the stop, 429) the line
  is that, in the fetch's words. Otherwise `netdiag.verdict()`: No internet on X /
  WiFi needs sign-in / Transport for London (or National Rail) not answering. The
  network checks (`comitup-cli i`, which prints `HOTSPOT state`; `ip`; `nmcli`;
  DNS; a plain-http `generate_204` probe) run only after such a failure and at
  most every 30 s. A good fetch clears everything. The journal gets a `network:`
  line when the verdict changes, and one `fetch failed` line per distinct error,
  not one per pass.
- For one minute (`ADDRESS_SECONDS`) from when the board first has an IP, not
  from boot, the footer's right corner shows a QR code for the settings page on
  that IP, with "Scan for settings" and "Hides in 42s" counting down beside it.
  A slow WiFi join, or the hand-over at the recipient's, still gets its minute.
- **Forget the WiFi**: on the page (type FORGET) or `sudo python3 portal.py
  --forget-wifi`. Deletes every saved network that is not an access point:
  comitup's own hotspot connection is named `<ap_name>-0000`, not the ssid, and
  comitup only remakes it at service start, so it is told apart by asking
  NetworkManager its mode, never by its name. comitup raises the hotspot within a
  minute; the board keeps drawing. This is hand-over step 1 below, which used to
  be done by hand with nmcli. Over Pi Connect it cuts your own connection, by design.

**Settled on the Pi, 9 Oct:** `python3-qrcode` installs under that name on
trixie; `generate_204` answers 204 from the house.
**Still unverified on hardware:** that iOS joins an open network from a `WIFI:`
QR (needs a phone and the hotspot up, so hand-over step 4); that nmcli's terse
output escapes a colon in a name as `\:` (handled, from the man page; no saved
network has a colon to test it).
comitup's one-shot output and its connection naming were checked against its
source (`davesteele/comitup`, 1.30 to 1.47.1).

## The screen, as changed on 9 Oct

Raoul watched the real screen and asked for each of these. Each is a commit with
its reason in the message.

- **Column headings say the direction only.** "towards X" beside them showed
  only when every train went to X, which the rows already said, and vanished
  otherwise. Removed. `--explain` still prints it.
- **Four trains a side**, not five, the text the same size: under their heading,
  a little lower than five's first row and 8% further apart (`ROW_DROP`,
  `ROW_OPEN` in `board.py`), the spare space at the bottom. Centred, they floated
  away from the headings; at five's spacing they sat too tight under them.
- **A status too long for its line scrolls**: everything after "Status:", the
  mark and "Minor Delays" included. It rests 3 s when a board or a status first
  shows, then loops with no stop, a few words' gap between passes. A still frame
  (`--png`) cuts it with an ellipsis, as before. How: `render(..., ticker={})`
  hands back the box and one pass as a strip; a daemon thread (`Ticker`) writes
  only that box, 30 times a second, with `pwrite` of RGB565 rows packed once per
  status. The full frame keeps its 10 s redraw; the frame and the strip are
  packed before the shared lock, and only set, patch and write happen under it.
  Measured on the Pi: 8% of one core including the redraws, no stall at redraws.
- **The settings card** is one minute: a QR code in the bottom right corner for
  `http://<the board's IP>:8080`, "Scan for settings" and "Hides in 42s" beside it,
  the countdown drawn by the ticker once a second. The IP is the one address that
  always opens from a phone on the same WiFi, and the code is made fresh each
  boot, so nobody types anything. Without `python3-qrcode` (the Mac) the
  addresses show as text in a slim white box instead. The rotation's dots wait
  while the code has the corner; with more rows it shrinks rather than cover a train.
- **The dots** moved to the bottom right, coloured (see "The rotation").
- A long "towards" ran off the screen before it was removed: the Pi draws in
  DejaVu, which is wider than the Helvetica Neue the Mac's `--png` uses. See
  hardware fact 10.

## Where things live

- `pi/` - the Pi: `board.py`, `portal.py`, `rail.py`, `netdiag.py`, `screen.py`,
  `updater.py`, `install.sh`, `bench.sh`, `test_rotation.py`, the systemd units
  (the board, the portal, and the update service and its timer). `pi/README.md`
  is the user-facing guide and matches the code as of tonight.
- `index.html` - the web version, live at raoulasaurous.github.io/piccadilly-board.
  One station, TfL only. As of tonight it carries the Pi board's column logic
  (headings from the trains, an empty direction kept, the worst status with its
  cause, "no live data" with an age) and `test_index.py` at the root drives it
  in headless Chrome.
- `case/sled.py` - the Pi mounting sled (manifold3d). `cd case && python3 sled.py`
  rebuilds the three STLs.
- Artifacts, private to Raoul's claude.ai account (update in place with `url=`):
  the parts page https://claude.ai/code/artifact/b780921a-e621-4f93-a29b-1e38fc4c2e7c
  (**out of date**: its power section shows the one-lead plan that failed) and the
  sled viewer https://claude.ai/artifact/RmcsgJq7pK1BVMFQAx7zWr (source
  `case/viewer.html` on Raoul's Mac, not in git).
- On Raoul's Mac only (gitignored): `backup/pi-config-backup.tgz`, the Pi's
  config from 14 Sept (the pinned EDID and the sudo rule).

## The Pi

- Raspberry Pi OS Lite 64-bit (Debian 13 trixie, Python 3.13). Hostname
  `tubeboard`, user `locklinestudio`, passwordless sudo in
  `/etc/sudoers.d/020-tubeboard` (added by hand; Imager does not do it).
- SSH is key-only, from Raoul's Mac (`~/.ssh/id_ed25519`, comment
  `tubeboard@Raouls-Laptop`): `ssh locklinestudio@tubeboard.local`. The Deco mesh
  sometimes drops `.local` names; then `arp -a` or the router's app.
- A console password exists and was once typed into a chat, so treat it as
  exposed. Never ask for it, never write it down. Resetting it is a required step
  before the hand-over.
- Code: a clone at `~/piccadilly-board` on `main`. Services: `tubeboard.service`,
  `tubeboard-portal.service` (port 8080), comitup (hotspot and its page on 80).
- See the real screen from the same network: `ssh locklinestudio@tubeboard.local
  'sudo cat /dev/fb0 | gzip' > fb.gz`, decode as RGB565 little-endian 1920x1080,
  stride 3840. From anywhere: ask for a phone photo.
- Health: `vcgencmd get_throttled` must be `0x0`; count `draw failed` and `fetch
  failed` in `journalctl -u tubeboard.service -b`; `cat
  /sys/class/graphics/fb0/virtual_size` must be `1920,1080`; `sudo ddcutil --brief
  getvcp 10` is the brightness. New tonight: `network:` lines in the journal say
  what the WiFi was doing each time a fetch failed. Once the updater is
  installed: `cat /run/tubeboard/health.json` (the board) and
  `cat /var/lib/tubeboard/update-history` (the nightly updates).

## Hardware facts that cost time to learn

Do not re-learn these.

1. **A Pi 3 gives a 16-bit framebuffer and nothing else.** `board.py` packs RGB565
   with numpy. That is the normal path, not a fallback.
2. **`video=HDMI-A-1:1920x1080MR@60D`: the `MR` matters.** Without it the mode
   falls back to 1024x768.
3. **The screen's EDID is pinned** (`drm.edid_firmware=HDMI-A-1:edid/tubeboard.bin`),
   or a power cut to the monitor alone zooms the board into its top-left corner
   until a reboot. **Run `install.sh` with the screen plugged in and on.**
4. **Power is settled.** UGREEN Nexode 35 W brick: USB-C to the monitor on its own
   lead, USB-A to the Pi on a MaGeek 3 m micro-USB lead. 20-minute soak at 100%:
   0 under-voltage, 0 throttled, 40 C peak. The one-lead splitter failed (1 V
   lost over 2 m). **Never feed the Pi from the monitor's second USB-C port**: it
   browned out and corrupted the card.
5. A faint hiss from the Pi is coil whine. Harmless; a dab of hot glue if audible.
6. The board logs one `fetch failed (...)` about name resolution per boot, because
   the service starts before DNS is up. It recovers in 30 s. Expected.
7. comitup owns port 80, so the settings page is on 8080. A full `install.sh` run
   restarts comitup so the hotspot carries the name we set; `SKIP_COMITUP=1` does not.
8. Pi Connect is a **user** service: `loginctl enable-linger locklinestudio`, and
   `rpi-connect signin` under `setsid nohup`, never `timeout`.
9. Pi OS Lite has no git. `apt install git` before cloning.
10. **A Mac PNG is not the Pi's screen.** `board.py` takes DejaVu where it exists
    and Helvetica Neue on a Mac, and DejaVu is wider: "towards Walthamstow
    Central" fitted on the Mac and ran off the Pi's screen. Check widths on the
    Pi, or read its framebuffer (see "The Pi"); a test that matters must hold in
    either font.

## The westbound report (19 Sept), in short

*"Only eastbound, no westbound."* The board was right: a weekend part closure.
What was wrong was that a correct board looked broken. PR #2 keeps an empty
direction's column and says `No trains reported`; the status line starts at the
clause naming the closure. PR #1 fixed two real bugs found on the way: a
direction-blind `dedupe` (the Circle line ate itself) and an all-or-nothing
regroup fallback. Verified against live TfL on a Mac during the real closure.
Still never seen on the real screen. `board.py --explain` is the diagnostic.

## Still to do

In rough order:

1. **A photo of the real screen** from a phone. The framebuffer has been read
   (the pixels are right); nobody has seen the colours on the panel itself.
2. **The self-updater is built (9 Oct), not yet on the Pi.** See "Deploying".
   The next deploy by hand installs it. Then check on the Pi: `systemctl
   list-timers` shows `tubeboard-update.timer`; `cat /run/tubeboard/health.json`
   counts draws; `sudo python3 /opt/tubeboard/updater.py --dry-run` ends "a real
   run would now..." or "nothing new". Unseen on hardware so far: a real run,
   `runuser` with git in the clone, the restart under `RuntimeDirectory=`, and a
   rollback. A deliberately broken commit on a branch, merged and then reverted,
   is the way to see a rollback without risk to the wall.
3. **`index.html` cannot rotate**, and its line name, colour and roundel are
   fixed to the Piccadilly in the markup. The direction bug it carried since
   19 Sept is fixed as of tonight. A one-destination board (the DLR shape) leaves
   its second column blank where the Pi goes full width.
4. **The rail board's finer points** (the feed has now been seen live): a
   cancelled train is dropped rather than shown struck through; the roundel says
   NATIONAL RAIL; London termini are a hand-kept list in `rail.py`. Its status
   line is TfL's for Great Northern, which said Good Service on 7 Oct while the
   feed's own notice said trains were delayed up to 10 minutes: the feed's
   notices could be the reason after the status, and now they would scroll.
5. **`index.html` has drifted** from the Pi board on 9 Oct: it still has
   "towards", five rows and a cut status. Raoul's call whether it follows.
6. The physical build (below). The frame order is parked by Raoul.

## Physical build

**How it is held** (agreed with Raoul): no glass. The mount presses on the
frame's lip. The monitor lies face down on the mount on foam tape. The EasyFrame
6 mm spacer ring and 5 mm foam bring the perimeter level with the monitor's
thicker spine. The backing board sits behind the screen, held by six turn
buttons. The Pi sits on the sled, screwed to the backing board. Cork bumpers
hold the frame 5 mm off the wall.

**The sled** (`case/sled.py`) is a tray, not a box. It screws to the **backing
board, never to the monitor**. Footprint 73 x 64 mm, height 14.9 mm with the Pi
and its GPIO header. Not printed yet.

**Depth**, measured by Raoul 7 Oct: the monitor is **12 mm** at its thick socket
edge and about 7 mm elsewhere; the Pi about 10 mm with its pins. Stack behind
the lip: mount 1.5 + monitor 12 + backing 3 = 16.5 mm, then the Pi:

| Where the Pi goes | Stack | Rebate needed |
|---|---|---|
| On the monitor's thin back, on foam tape, inside the frame | 22.5 mm | 27 mm or more |
| Behind the backing board, on foam tape | 27.5 mm | 32 mm or more |
| Behind the backing board, on the sled | 31.5 mm | 36 mm or more |

Behind the backing board keeps the SD card and cables reachable from the back;
on the monitor's back means lifting the backing board to reach the Pi.

**Frame options found 7 Oct** (dark wood, made to 457 x 305 or 456 x 305, a
search across UK framers with each page verified). **Order a plain frame, not the
BOX version**: EasyFrame's box version glues a 5 mm spacer inside each side,
which takes 10 mm off the width and height and adds no depth.

- EasyFrame 20 mm Brown Stain (walnut tone), code 364453492, **40 mm rebate**,
  about GBP 40 at 457 x 305 with no glazing. Fits every Pi position. Raoul built
  this order on the site: 456 x 305, mount opening 346 x 195 (55 mm borders),
  Off White mount, No Glazing, MDF backing and D-rings, 5 mm white foam board
  and 9 mm foam tape, GBP 48.71.
- EasyFrame 40 mm Walnut Stain, code 311493492, 27 mm rebate, GBP 79.73 as
  configured: fits **only** with the Pi on the monitor's back; 80 mm larger
  outside.
- eFrame Extra Deep Dark Box (wenge, 36 mm rebate, GBP 61) and Wenge Extra Deep
  Flat (35 mm, GBP 52): the darkest found.
- Picture Frames Express R308 walnut (48 mm rebate) and R662 dark walnut veneer
  (42 mm): read from the category page only.

EasyFrame supplies D-rings and screws with mouldings 18 mm or wider; nothing
extra is needed to hang it. The "coloured backing board" option is a solid card
with no opening: leave it off.

### Still to measure

1. ~~The screen's thickness at its thick socket edge.~~ 12 mm, 7 Oct (above).
   The notes from before: add 19.4 for the sled, or 15.4 for the Pi on tape. Stack 28 or under: EasyFrame 20 mm Walnut Stain,
   code 307453492 (a *Picture* Frame on the site, 28 mm rebate),
   https://www.easyframe.co.uk/Item/307453492. 28 to 32: still walnut, the
   bumpers hide it. Over 32: 20 mm Brown Stain Box Frame, code 364453492BOX,
   40 mm rebate, https://www.easyframe.co.uk/Item/364453492BOX. Checked 6 Oct:
   the 40 mm Walnut Box (27 mm rebate) and the 22 mm Walnut Foil Box (17 mm) are
   both shallower, so brown is the deep option, not a deeper walnut. Prices
   quoted 19 Sept for 457 x 305 with the 6 mm spacer: GBP 55.61 / 60.64.
2. **Connector reach** past the monitor's edge with the leads in, cables lying
   as they want. Under 40 mm: nothing. 40 to 50: mini-HDMI pigtail,
   amazon.co.uk/dp/B09X1L94S8. Over 50: a right-angle mini-HDMI adapter. USB-C
   long too: right-angle USB-C 2-pack, amazon.co.uk/dp/B0CBTWJ1YS. The monitor
   end is **mini-HDMI (type C), not micro**.
3. **Palm test.** After an hour at 100%, a palm on the screen front for ten
   seconds. Comfortable or not.

Then the mount from Southbank Art (custom window mount, 1.5 mm board,
https://southbankart.co.uk/products/custom-window-mount): 457 x 305, opening
**346 x 195**, **untick the 3 mm overlap**.

## Parts

- **In use:** Yodoit 15.6" 1080p matte (B0BYJ79PHH), Pi 3 A+ (B07KKBCXLY),
  KEXIN 32 GB microSD, UGREEN Nexode 35 W (B0CFFNTCRY), MaGeek 3 m USB-A to
  micro-USB (B00WEVG57K), the HDMI lead from the monitor's box.
- **Retired, do not reuse for power:** the kenable 12 W charger, the INIU 2 m
  lead, the RIIEYOCA splitter.
- **Still to buy:** the frame, the mount, maybe the pigtail. Optional white
  braided sleeve, amazon.co.uk/dp/B09PG6JYPW.
- Zero / Zero 2 W boards are sold out until 2027. A Pico or ESP32 cannot drive
  this monitor. Do not revisit the brain choice.

## Settings on the Pi

Station Arsenal (`940GZZLUASL`), line `piccadilly`, 4 rows (clamped 1-8), fetch
every 30 s, redraw every 10 s, rotation 30 s once there is more than one board.
Brightness 100% from 07:00, 30% from 21:00. Off-overnight exists but is off.
`screen.py` fails safe, and the board survives it not. New keys tonight:
`source`, `stations`, `rotate_seconds`, `rail_api_key`, `rail_api_url`; an old
file without them behaves as before. `auto_update` (9 Oct) is read by the updater
only: missing or true means the nightly update runs, false stops it. A hand edit that leaves a number where a
list goes, a quoted number, or a trailing comma is survived by the board (it
keeps the old settings and says so in the journal) and refused by the portal
with a sentence, never silently overwritten.

## Before it goes to the recipient

1. **Forget the WiFi** from the settings page or `sudo python3 portal.py
   --forget-wifi`. Otherwise the board carries this house's password, and never
   raises the setup hotspot at theirs.
2. Reset the console password (required, see "The Pi").
3. Tell the recipient plainly that Raoul can log in remotely (Pi Connect), and
   that the board updates itself overnight from GitHub. Decide whether Raoul's SSH
   key stays. Claude recommended keeping both, and saying so.
4. **See the setup screen and the hotspot on a phone once.** On 14 Sept the
   join flow worked end to end but under the stock name; nobody has yet seen
   **TubeBoard-setup**, the QR, or the WIFI OK screen.
5. Their first power-on is then: the setup screen, join from a phone, choose
   their WiFi, WIFI OK, trains. Settings at http://tubeboard.local:8080, which
   the footer says, boxed, for the first minute after the board has their IP.

## Decisions already made (do not re-open)

- Font: DejaVu. Hammersmith One was tried and rejected: one weight only.
- The roundel bar says the network: UNDERGROUND, DLR, OVERGROUND, ELIZABETH LINE,
  and now NATIONAL RAIL. Drawn to TfL proportions, not TfL's artwork.
- 4 rows per direction (Raoul, 9 Oct; it was 5). The footer shows "Updated 12s
  ago", not a clock time. Column headings name the direction only (9 Oct).
- The whole frame is drawn at 2x and reduced (305 ms per frame on a Pi 3).
- Rotation 30 s (Raoul, 6 Oct). Dots, not a label: bottom right above the footer
  rule, in the line colours, the current one ringed in white (9 Oct).
- A long status scrolls, the whole line after "Status:", looping without a stop
  (9 Oct). The settings card shows for one minute (9 Oct).
- The setup QR is dark on light. The portal is unauthenticated on the home
  network ("local is fine", the same call as on the ticker); the one destructive
  action, Forget the WiFi, asks for a typed word.
- Power: see above. Settled.

## Working with Raoul

- Give short, plain answers: the result, not the reasoning.
- For anything Raoul will send to the recipient: plain words, no em-dashes, and
  always the complete text, never a list of edits.
- He reads this on his phone first thing. The state table and "Start here" are
  for that.
