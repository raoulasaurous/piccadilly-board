# Handover — the westbound investigation, 19 September

Written for whoever picks this up next, human or model.

**Sister project:** `raoulasaurous/esp32-matrix-ticker` has its own `HANDOVER.md`
and is in a very different state — nothing there is merged, and the hardware
runs an unmerged branch. The two were worked on in the same session; that is the
only thing they share.

## Status

| | |
|---|---|
| `main` | `0331bee` — **both PRs merged** |
| PR #1 | merged — the two direction bugs |
| PR #2 | merged — the empty column and the status line |
| **The Pi at the relative's house** | **still running the old code** |

Merging is not deploying here. There is no self-update: the board runs from
`/opt/tubeboard/`, which `install.sh` copies into — *not* from the git clone.
Someone on that WiFi has to do this:

```bash
ssh <user>@tubeboard.local
cd ~/piccadilly-board && git pull
sudo cp pi/board.py /opt/tubeboard/ && sudo systemctl restart tubeboard
```

`settings.json` is not touched, so the station and brightness survive. The board
redraws within about 30 seconds.

## What was reported, and what it actually was

*"It's showing on the screen but only eastbound, no westbound."* Arsenal,
Piccadilly line.

**The board was right.** There was a weekend part closure and TfL was reporting
no westbound trains at all. Nothing was broken — but the board had no way to say
so, and that is its own fault:

- One direction reported meant one group, and one group is drawn full width. The
  board silently reshaped itself into something that no longer resembles a
  departure board. Nothing on screen said "there are no westbound trains" — the
  column simply ceased to exist.
- The status line held the explanation and spent its width on dates:
  `Part Closure - Saturday 19 September, from 0130 and all day Sunday…`, clipped
  before the part naming what was shut.

Both fixed in PR #2. An empty direction now keeps its column and says
`No trains reported`, whenever the missing side can be named from the running
one (the compass pairs, and the Circle line's two rails). The status line starts
at the clause that names the closure: `no service between Acton Town and
Uxbridge / Heathrow`.

## Two real bugs found on the way (PR #1)

Neither was the cause at Arsenal, but both drop a direction on the floor:

- **`dedupe` was direction-blind.** It matched on destination plus time alone, so
  two trains going opposite ways collapsed into one whenever they shared a
  destination and fell within five seconds. The Circle line does that all day —
  both rails run to Hammersmith and to Edgware Road — so a rail could lose enough
  trains to empty its column. It now refuses to merge two predictions that state
  *different* directions or *different* compass headings. A blank on either side
  is "not stated" and contradicts nothing, which is what keeps the duplicate DLR
  predictions this function was written for collapsing as before.
- **The regroup fallback was all-or-nothing.** `if (not all(keys)): continue`
  threw away the entire Eastbound/Westbound split if a *single* train lacked a
  compass platform name. One train at `Platform Unknown` and the reader got one
  mixed `DEPARTURES` column. Such a train is now placed with the trains already
  running to its destination, or sharing its direction; if it can be placed by
  neither the split is still refused, because a train under the wrong heading
  sends someone to the wrong platform.

## The diagnostic

```bash
cd /opt/tubeboard && python3 board.py --explain
```

Prints what TfL answered for the configured station — how many predictions, on
which platforms, with which direction — then the columns the board made of them.
Safe to run while the service is going; it only fetches and prints.

Run it **in `/opt/tubeboard`**, not in the git clone: `settings.json` lives
beside the running code, and the clone still holds the repo default (Arsenal).
That trap cost an hour.

## Still to do

1. **Deploy to the Pi.** See above. Needs a person on that WiFi.
2. **A self-updater.** Offered three times, never built. A systemd timer that
   pulls `main` and restarts would make merging equal deploying, and the only
   reason it hasn't happened is that every fix so far has needed someone in the
   house. One manual install bootstraps it. The sister project already works
   this way.
3. **`index.html` has the bug the Pi board just had.** The web version still
   filters strictly:

   ```js
   .filter((a) => a.direction === c.api)      // index.html:357
   ```

   TfL omits `direction` at termini and sends it empty across much of the DLR,
   the Elizabeth line and the Overground. On any such station this page shows
   two empty columns while trains are due. `pi/board.py` has the whole fallback
   ladder — heading, then towards, then destination — and the page has none of
   it. Not fixed because the reported fault was the Pi's.
4. **A long destination overruns the minutes column.** "Hainault via Newbury
   Park" collides with "7 min". `clip()` already exists and is used on the status
   line; the destination text is drawn without it. Noted on PR #1, not fixed.

## Environment notes

- `api.tfl.gov.uk` was **blocked** from the cloud sandbox this work was done in,
  so nothing here was tested against live TfL. Everything was verified by driving
  the real `dedupe`/`group`/`column_label` functions over stubbed payloads shaped
  like TfL's, plus a full 1920x1080 frame rendered through `render()`. On a Mac
  the API is reachable and `--explain` is the faster check.
- Getting a repro right mattered more than it sounds. The first attempt at the
  Arsenal case used only the five trains visible on screen, which split into two
  destination columns and did **not** match the photo. The real feed carries a
  third destination further out than the five drawn rows, and that is what stops
  the destination split. Match the whole feed, not the visible rows.
