# Handover - the Tube Board

Written 19 Sept 2026 for whoever picks this up next, human or model. It merges
two handovers: the cloud session that fixed the westbound report today, and the
Mac session that built and bench-tested the hardware from 9 to 17 Sept. It
covers the whole project. Most of it exists nowhere else in the repo.

**This repo is PUBLIC** (GitHub Pages serves `index.html`). Keep WiFi names,
passwords, addresses and personal details out of it, including out of this file.

**Sister project:** `raoulasaurous/esp32-matrix-ticker` has its own
`HANDOVER.md`. The two were worked on in the same sessions and share nothing
else, not even the branch name they both carry.

## What this is

A live Piccadilly line departure board for Arsenal, built by Raoul as a gift for
a friend (called "the recipient" here). A Raspberry Pi 3 A+ draws the board
straight to a 15.6" 1080p monitor: no desktop, no browser. It will hang on a
wall in a deep box frame with a card mount and no glass. The web version
(`index.html`) came first. The Pi board copies its layout.

## State on 19 Sept 2026

| Area | State |
|---|---|
| `main` | `1a54c1c`. PRs #1, #2 and #3 are all merged. No open PRs. |
| The westbound fix | Merged. **Not deployed to the Pi.** |
| Where the Pi is | At a relative's house (per the 19 Sept cloud session), online and showing live trains |
| Power | **Settled 17 Sept. Do not re-test.** One brick, two cables. |
| Brightness and night dimming | Works, over the HDMI cable (DDC/CI) |
| Remote access | Raspberry Pi Connect, signed in 14 Sept, still signed in on 17 Sept, survives reboots |
| WiFi hand-over flow | Proven 14 Sept, but under the wrong hotspot name (see "Before it goes") |
| Pi mounting sled (CAD) | Designed 17 Sept, not printed |
| Frame and mount | **Blocked on three ruler measurements from Raoul** |

The branch `claude/tube-board-westbound-missing-phq184` is fully merged. Work
from `main`.

## Deploying: merging is not deploying

There is no self-update. The board runs from `/opt/tubeboard/`, which
`install.sh` copies into. It does not run from the git clone. For a change to
`board.py` only:

```bash
cd ~/piccadilly-board && git pull
sudo cp pi/board.py /opt/tubeboard/ && sudo systemctl restart tubeboard
```

`settings.json` is not touched, so the station and brightness survive. The board
redraws within about 30 seconds. For a change to the installer, the portal or
`screen.py`, run `cd ~/piccadilly-board/pi && sudo SKIP_COMITUP=1 bash install.sh`
instead. It copies all three files and restarts both services.

**Nobody needs to be in the house.** Raspberry Pi Connect gives a shell from
anywhere: Raoul opens connect.raspberrypi.com, picks `tubeboard`, opens the
remote shell and pastes the commands. This works while the Pi is online and
Connect is still signed in (it was on 17 Sept). The SSH key only works on the
same network as the Pi.

## Where things live

- `pi/` - the Pi board: `board.py` (TfL fetch + Pillow render to `/dev/fb0`),
  `portal.py` (settings page, port 8080), `screen.py` (brightness over DDC/CI),
  `install.sh`, `bench.sh` (decodes `vcgencmd get_throttled`), systemd units.
  `pi/README.md` is the user-facing guide.
- `index.html` - the web version, live at raoulasaurous.github.io/piccadilly-board.
- `case/sled.py` - the Pi mounting sled (manifold3d). `cd case && python3 sled.py`
  rebuilds `sled.stl`, `clamp.stl` and `pi_dummy.stl` in that folder.
- Artifacts, private to Raoul's claude.ai account. Update in place by passing `url=`:
  - Parts page for the recipient:
    https://claude.ai/code/artifact/b780921a-e621-4f93-a29b-1e38fc4c2e7c.
    **Out of date.** Last published 9 Sept, before the parts arrived. Its power
    section still shows the one-lead splitter plan, which failed.
  - Sled CAD viewer: https://claude.ai/artifact/RmcsgJq7pK1BVMFQAx7zWr. Its
    source is `case/viewer.html` on Raoul's Mac (not in git), built with
    `~/.claude/tools/cad_viewport.py`.
- On Raoul's Mac only (gitignored): `backup/pi-config-backup.tgz`, a tar of the
  Pi's config from 14 Sept (the pinned EDID and the sudo rule).

## The Pi

- Raspberry Pi OS Lite 64-bit (Debian 13 trixie). Hostname `tubeboard`, user
  `locklinestudio`.
- SSH is key-only, from Raoul's Mac (`~/.ssh/id_ed25519`, comment
  `tubeboard@Raouls-Laptop`): `ssh locklinestudio@tubeboard.local`. The Deco
  mesh router at Raoul's sometimes drops `.local` names. Then find the IP with
  `arp -a` or in the router's app.
- Passwordless sudo is in `/etc/sudoers.d/020-tubeboard`. Raoul added it by
  hand, because Imager does not do it for a custom user.
- A console password exists. Raoul typed it into a chat once, so treat it as
  exposed. Never ask for it, and never write it anywhere. Resetting it is a
  required step before the board is handed over.
- Code: a clone at `~/piccadilly-board` on `main`. `install.sh` copies
  `board.py portal.py screen.py` to `/opt/tubeboard/` and never overwrites an
  existing `/opt/tubeboard/settings.json`.
- Services: `tubeboard.service` (the board), `tubeboard-portal.service`
  (settings page, http://tubeboard.local:8080), and comitup (WiFi hotspot and
  its own page on port 80).
- See the real screen without asking Raoul: `ssh locklinestudio@tubeboard.local
  'sudo cat /dev/fb0 | gzip' > fb.gz`, then decode it with numpy as RGB565,
  little endian, 1920x1080, stride 3840.
- Health check: `vcgencmd get_throttled` (must be `0x0`), count "draw failed"
  and "fetch failed" in `journalctl -u tubeboard.service -b`,
  `cat /sys/class/graphics/fb0/virtual_size` (must be `1920,1080`),
  `sudo ddcutil --brief getvcp 10` (brightness).
- **Shut down before pulling power:** `sudo shutdown -h now`. A brownout
  already corrupted the SD card once, and it had to be re-imaged.

## Hardware facts that cost time to learn

Do not re-learn these.

1. **A Pi 3 gives a 16-bit framebuffer and nothing else.** The vc4 driver
   decides. `framebuffer_depth` and a `-32` on the `video=` line are both
   ignored. Pillow has no 16-bit packer, so `board.py` packs RGB565 with numpy.
   That is the normal path on this hardware, not a fallback.
2. **`video=HDMI-A-1:1920x1080MR@60D`: the `MR` matters.** Without it the
   kernel builds GTF timings from the `video=` line at 172.8 MHz. That is above
   the Pi 3's 162 MHz HDMI limit, so the mode falls back to 1024x768. `MR`
   (reduced blanking) fits.
3. **The screen's EDID is pinned:** `drm.edid_firmware=HDMI-A-1:edid/tubeboard.bin`.
   Without it, a power cut to the monitor alone drops the mode to 1024x768
   until a reboot: the board is drawn full size but shown zoomed into its
   top-left corner. **Run `install.sh` with the screen plugged in and on**, or
   there is no EDID to capture. (Fixed installer bug: sysfs reports the EDID
   file as 0 bytes, so the installer copies it first and then checks the copy.)
4. **Power is settled.** What works (17 Sept): one UGREEN Nexode 35 W brick
   with USB-C and USB-A. USB-C goes to the monitor on the monitor's own lead.
   USB-A goes to the Pi on a MaGeek 3 m micro-USB lead. A 20-minute soak at 100%
   brightness gave 397 samples, 0 under-voltage, 0 throttled,
   `throttled=0x0` sticky, 0 draw failures and a 40 C peak.
   What failed, and why:
   - One cable and a splitter that fed both devices: about 2 A over 2 m, and
     about 1 V lost. The Pi reported under-voltage (`0x50005`). The monitor's
     backlight browned out too, and the screen blinked. Lower brightness helped
     but did not fix it. Software cannot fix it.
   - **The Pi fed from the monitor's second USB-C port.** That port is a
     trickle output. The Pi went into a brownout loop and corrupted the SD
     card. Never do this again. Never power the monitor from the Pi's USB port
     either.
5. A faint hiss from the Pi is coil whine from its voltage regulator. The pitch
   follows the CPU clock. It is harmless. If it is audible in the frame, put a
   dab of hot glue on the inductor.
6. The board logs one "fetch failed: name resolution" per boot, because the
   service starts before DNS is up. It recovers in 30 s. This is expected.
7. comitup owns port 80, so the settings page is on 8080. apt starts comitup
   with the stock name ("comitup-NNN") until it is restarted. A full
   `install.sh` run restarts it. A run with `SKIP_COMITUP=1` does not touch it.
8. Pi Connect is a **user** service. It needs `loginctl enable-linger
   locklinestudio`, or it stops when the SSH session ends. `rpi-connect signin`
   must keep running while the browser link completes: start it with
   `setsid nohup`, never under `timeout`.
9. Pi OS Lite has no git. Install it with apt before you clone.

## The westbound report (19 Sept), and what it was

*"It's showing on the screen but only eastbound, no westbound."* Arsenal,
Piccadilly line.

**The board was right.** There was a weekend part closure and TfL reported no
westbound trains at all. But the board had no way to say so:

- One direction reported meant one group, and one group was drawn full width.
  The westbound column simply disappeared.
- The status line held the explanation but spent its width on dates, and the
  clip landed before the part that named what was shut.

PR #2 fixed both. An empty direction now keeps its column and says
`No trains reported`, when the missing side can be named from the running one
(the compass pairs, and the Circle line's two rails). The status line starts at
the clause that names the closure.

PR #1 fixed two real bugs found on the way. Neither caused the Arsenal report,
but both could drop a direction:

- **`dedupe` was direction-blind.** It matched on destination and time only,
  so two trains going opposite ways merged when they shared a destination
  within five seconds (the Circle line does this all day). It now refuses to
  merge predictions that state different directions or headings. A blank on
  either side contradicts nothing, so the DLR duplicates it was written for
  still merge.
- **The regroup fallback was all-or-nothing.** One train without a compass
  platform name threw away the whole east/west split. Such a train is now placed
  with trains to the same destination or in the same direction. If neither
  places it, the split is still refused, because a train under the wrong
  heading sends someone to the wrong platform.

**Verified on a Mac on 19 Sept at 21:39 BST, against live TfL during the real
closure.** `--explain` showed 5 eastbound predictions and 0 westbound. A full
render kept the WESTBOUND column with "No trains reported", and the status read
"Part Closure - no service between Hyde Park Corner and Acton Town....". The
cloud session could not reach TfL (blocked in its sandbox) and tested with
stubbed payloads only. **Nobody has seen the fix on the real screen.**

### The diagnostic

```bash
cd /opt/tubeboard && python3 board.py --explain
```

It prints what TfL answered for the configured station (how many predictions,
on which platforms, with which direction), then the columns the board made of
them. It is safe to run while the service is going. **Run it in
`/opt/tubeboard`**, not in the git clone: the live `settings.json` is there,
and the clone holds only the repo default. That trap cost an hour.

A lesson from the repro: match the whole feed, not the visible rows. The real
Arsenal feed carried a third destination beyond the five rows drawn, and that
is what changed the grouping.

## Still to do

In rough order:

1. **Deploy `main` to the Pi** (see "Deploying"), then screenshot the real
   screen to confirm.
2. **A self-updater.** Offered three times, never built. A systemd timer that
   pulls `main` and restarts the board would make merging equal deploying. One
   manual install bootstraps it. The ticker already works this way.
3. **`index.html` has the bug the Pi board just had.** The web version still
   filters strictly on direction (`index.html:357`,
   `.filter((a) => a.direction === c.api)`). TfL omits `direction` at termini
   and leaves it empty across much of the DLR, the Elizabeth line and the
   Overground, so at such stations the page shows two empty columns while trains
   are due. `pi/board.py` has the full fallback (heading, then towards, then
   destination). The page has none of it.
4. **A long destination overruns the minutes column.** "Hainault via Newbury
   Park" collides with "7 min". The destination is drawn without `clip()`.
5. `clip()` adds "..." after text that already ends in a full stop, which gives
   four dots (visible in the status line quoted above).
6. `pi/__pycache__/*.pyc` are tracked in git by mistake. Remove them and add
   them to `.gitignore`.
7. The physical build (below), which waits on Raoul's measurements.

## Physical build

**How it is held** (agreed with Raoul): no glass. The mount presses on the
frame's lip. The monitor lies face down on the mount on foam tape. The
EasyFrame 6 mm spacer ring and 5 mm foam bring the perimeter level with the
monitor's thicker spine. The backing board sits behind the screen, held by six
turn buttons. The Pi sits on the sled, screwed to the backing board. Cork
bumpers hold the frame 5 mm off the wall, for air and for the cables.

**The sled** (`case/sled.py`) is a tray, not a box, because the depth forces it.
It screws to the **backing board, never to the monitor** (5 mm of thin
aluminium, nothing to fix into). It holds the Pi 5 mm above the backing board
(3 mm standoffs on a 2 mm base), clamps both cables so a pull does not reach
the sockets, has vent slots, and has a slot so the SD card comes out with the
frame assembled. Footprint 73 x 64 mm, height 14.9 mm with the Pi and its GPIO
header. Not printed yet.

**The depth arithmetic** (from `sled.py`; the screen number is an estimate from
a photo, not a measurement):

- Behind the frame's lip: mount 1.5 + screen spine (assumed 11) + backing 3 =
  15.5 mm. The Pi goes behind that.
- With the sled: 15.5 + 14.9 = 30.4 mm.
- With the Pi on 1 mm foam tape instead of the sled: 15.5 + 1 + 9.9 = 26.4 mm.

### Blocked on three measurements from Raoul

1. **The screen's thickness at its thick socket edge** (the "spine"). Add 19.4
   for the sled, or 15.4 for the Pi on tape, to get the stack.
   - EasyFrame 20 mm Walnut Stain box frame, code 307453492, rebate 28 mm,
     457 x 305, 6 mm spacer, GBP 55.61 with free delivery. Anything over 28 mm
     stands proud of the frame's back. The 5 mm cork bumpers hide up to about
     4 mm of that. With the 11 mm estimate, the sled stands 2.4 mm proud (fine)
     and the tape option fits inside.
   - EasyFrame 20 mm Brown Stain, code 364453492, rebate 40 mm, GBP 60.64.
     Everything fits inside with room to spare.
   - Do not use Mahogany 225496000 (18 mm, too shallow) or cheap 15 mm frames.
2. **Connector reach.** With the leads plugged in, measure how far the plug
   and cable bend reach past the monitor's edge. The channel inside the frame
   is about 40 mm at the top and bottom and about 49 mm at the sides.
   - Under 40 mm: buy nothing.
   - 40 to 50 mm: UGREEN braided mini-HDMI to HDMI pigtail,
     amazon.co.uk/dp/B09X1L94S8, GBP 9.30, 1,723 reviews.
   - Over 50 mm: a right-angle mini-HDMI adapter. Only niche brands exist
     (Duttek, about 80 reviews). It is a passive part, so the risk is low.
   - The monitor end is **mini-HDMI (type C), not micro**. The Pi end is full size.
   - The monitor's USB-C lead can have the same problem. The fix is a
     right-angle USB-C 2-pack, amazon.co.uk/dp/B0CBTWJ1YS, GBP 6.59.
3. **Palm test.** After an hour, hold a palm on the screen front for ten
   seconds. Comfortable or not? This tells us whether heat in a sealed frame
   is a problem.

Then order the mount from Southbank Art (custom window mount): 457 x 305,
opening **346 x 195**, and **untick the 3 mm overlap**. GBP 15.00 + 4.95 post.

## Parts

- **In use:** Yodoit 15.6" 1080p matte non-touch (B0BYJ79PHH), Raspberry Pi 3
  A+ (B07KKBCXLY), KEXIN 32 GB microSD, UGREEN Nexode 35 W USB-C + USB-A
  (B0CFFNTCRY), MaGeek 3 m white USB-A to micro-USB (B00WEVG57K), the HDMI lead
  from the monitor's box.
- **Retired, do not reuse for power:** the kenable 12 W charger, the INIU 2 m
  lead and the RIIEYOCA splitter from the failed one-lead setup.
- **Still to buy:** the frame, the mount, and maybe the mini-HDMI pigtail (all
  above). Optional: a white braided sleeve so the two cables read as one cord
  (shinfly 13 mm x 3 m, amazon.co.uk/dp/B09PG6JYPW, GBP 6.45).
- Zero / Zero 2 W boards are sold out until 2027. A Pico or ESP32 cannot drive
  this monitor. Do not revisit the brain choice.

## Settings on the Pi

Station Arsenal (`940GZZLUASL`), line `piccadilly`, 5 rows, TfL fetch every
30 s, redraw every 10 s. Brightness 100% from 07:00, 30% from 21:00.
Off-overnight exists but is off. `screen.py` fails safe: if ddcutil is missing
or the monitor does not answer, the board carries on.

## Before it goes to the recipient

1. Delete every WiFi network the Pi knows that is not the recipient's:
   `nmcli connection show`, then `sudo nmcli connection delete "<name>"` for
   each. Otherwise the board carries those passwords, and it never raises the
   setup hotspot.
2. Reset the console password (required, see "The Pi").
3. Tell the recipient plainly that Raoul can log in remotely (Pi Connect).
   Decide whether Raoul's SSH key stays. Claude recommended keeping both, for
   remote fixes, and saying so.
4. **Test the hotspot once more with a phone.** On 14 Sept the flow worked
   end to end: the Pi raised a hotspot, a phone joined it and entered a WiFi
   network, and the Pi rebooted onto it with the board drawing throughout. But
   the hotspot was called `comitup-680`, because comitup was not restarted
   after its name was set. The installer fix (`b6afc71`) came after that test,
   so nobody has yet seen **TubeBoard-setup** on a phone.
5. The recipient's first power-on should then be: the Pi finds no known WiFi,
   raises **TubeBoard-setup**, the recipient joins it from a phone and enters
   their own WiFi, and the Pi reboots onto it. Settings are then at
   http://tubeboard.local:8080.

## Decisions already made (do not re-open)

- Font: DejaVu. Hammersmith One (closest free face to TfL's Johnston) was
  tried and rejected, because it has one weight only.
- The roundel bar says UNDERGROUND (DLR, OVERGROUND and ELIZABETH LINE for
  those networks). It is drawn to TfL proportions, not TfL's artwork.
- 5 rows per direction. The footer shows "Updated 12s ago", an age that visibly
  grows when the feed dies, not a clock time.
- The whole frame is drawn at 2x and reduced for smooth curves (305 ms per frame
  on a Pi 3).
- Power: see above. Settled.

## Working with Raoul

- Give short, plain answers: the result, not the reasoning.
- For anything Raoul will send to the recipient: plain words, no em-dashes,
  and always the complete text, never a list of edits.
