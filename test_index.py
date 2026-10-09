#!/usr/bin/env python3
"""Offline test of index.html, the web version of the board.

    python3 test_index.py

Loads the page in headless Chrome with TfL's two requests stubbed, and reads
back what it drew: the column headings, the rows, the status line. The cases
are the ones that broke the Pi board before 19 Sept: a part closure with one
direction running, a station where TfL omits "direction", a single list. The
page also follows the Pi board's 9 Oct screen: a heading with no "towards",
four trains a side, and a status too long for its line that scrolls.
Needs Chrome or Chromium; set CHROME=/path/to/chrome if it is not found.
"""
import glob
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(HERE, "index.html")


def find_chrome():
    cands = [os.environ.get("CHROME", "")]
    cands += [shutil.which(n) or "" for n in ("chromium", "chromium-browser", "google-chrome", "chrome")]
    cands += ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
              "/Applications/Chromium.app/Contents/MacOS/Chromium"]
    cands += glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome")
    cands += glob.glob("/opt/pw-browsers/chromium_headless_shell-*/chrome-linux/headless_shell")
    for c in cands:
        if c and os.path.exists(c):
            return c
    sys.exit("no Chrome or Chromium found; set CHROME=/path/to/chrome")


chrome = find_chrome()
A = lambda plat, dirn, tow, dest, t, i: {"platformName": plat, "direction": dirn, "towards": tow, "destinationName": dest, "timeToStation": t, "id": i, "vehicleId": i}
GOOD = [{"lineStatuses": [{"statusSeverity": 10, "statusSeverityDescription": "Good Service"}]}]
# A reason in one long sentence: the page keeps a sentence whole, so this is far
# wider than the footer's room in any font, on a phone or a wall.
LONG_WHY = ("Severe delays because of a faulty train at Hyde Park Corner and a signal failure at Acton Town, "
            "so trains between Heathrow and Cockfosters are running with long gaps and are very crowded, "
            "and the first and last trains of the day may be cancelled")
LONG = [{"lineStatuses": [{"statusSeverity": 6, "statusSeverityDescription": "Severe Delays", "reason": LONG_WHY}]}]
# Six trains each way, so the page has to choose four.
MANY = ([A("Eastbound - Platform 1", "inbound", "Cockfosters", f"{n} Underground Station", 60 * (i + 1), f"e{i}")
         for i, n in enumerate(["Cockfosters", "Arnos Grove", "Oakwood", "Southgate", "Bounds Green", "Turnpike Lane"])]
        + [A("Westbound - Platform 2", "outbound", "Heathrow", f"{n} Underground Station", 90 * (i + 1), f"w{i}")
           for i, n in enumerate(["Heathrow Terminal 5", "Heathrow Terminal 4", "Uxbridge", "Rayners Lane", "Acton Town", "Barons Court"])])
CASES = {
  "both ways": ([A("Eastbound - Platform 1", "inbound", "Cockfosters", "Cockfosters Underground Station", 120, "1"),
                 A("Westbound - Platform 2", "outbound", "Heathrow via T4 Loop", "Heathrow Terminal 4 Underground Station", 300, "2")], GOOD),
  "closure": ([A("Eastbound - Platform 1", "inbound", "Cockfosters", "Cockfosters Underground Station", 120, "1"),
               A("Eastbound - Platform 1", "inbound", "Arnos Grove", "Arnos Grove Underground Station", 400, "3")],
              [{"lineStatuses": [{"statusSeverity": 10, "statusSeverityDescription": "Good Service"},
                                 {"statusSeverity": 14, "statusSeverityDescription": "Part Closure",
                                  "reason": "Piccadilly Line: Saturday 19 September, no service between Hyde Park Corner and Acton Town. London Buses are accepting tickets."}]}]),
  "no direction": ([A("Platform 7", "", "Stratford", "Stratford Rail Station", 150, "5"), A("Platform 8", "", "Richmond", "Richmond Rail Station", 420, "6")], GOOD),
  "one list": ([A("Platform 1", "", "", "Lewisham DLR Station", 100, "x"), A("Platform 2", "", "", "Lewisham DLR Station", 400, "y")], GOOD),
}
# Written into the title every half second after the load, so the dump can read
# what only a browser knows: colours, sizes, the animation, and how many times
# the status line's markup changed since the first half second (a refresh with the
# same status must change none).
PROBE = """
let muts = null;
setInterval(() => {
  const q = (s) => document.querySelector(s), box = (e) => e.getBoundingClientRect();
  if (muts === null) {
    muts = 0;
    new MutationObserver((r) => { muts += r.length; })
      .observe(q('.status'), { subtree: true, childList: true, characterData: true, attributes: true });
  }
  const track = q('#track'), line = q('#line'), cs = getComputedStyle(track), val = q('#status');
  const rows = [...document.querySelectorAll('#colA .row')], copy = track.children[1];
  document.title = JSON.stringify({
    stroke: getComputedStyle(q('#tick circle')).stroke,
    muts: muts,
    anim: [cs.animationName, cs.animationDelay, cs.animationIterationCount, cs.animationTimingFunction],
    cqw: box(q('.cols')).width / 100,
    track: box(track).width, line: box(line).width, gap: parseFloat(getComputedStyle(line).marginRight),
    copyW: copy ? box(copy).width : null,
    copyIds: copy ? copy.querySelectorAll('[id]').length + (copy.id ? 1 : 0) : 0,
    ellipsis: getComputedStyle(val).textOverflow, cut: val.scrollWidth > val.clientWidth,
    pitch: rows.length > 1 ? box(rows[1]).top - box(rows[0]).top : null,
    drop: rows.length ? box(rows[0]).top - box(q('#colA')).top : null,
    spare: rows.length ? box(q('#colA')).bottom - box(rows[rows.length - 1]).bottom : null,
  });
}, 500);
"""
def run(arrivals, status, stub=None, budget=4000, flags=()):
    """Render the page with fetch stubbed. `stub` is JavaScript for a custom fetch;
    the default answers the arrivals and the status given. `flags` go to Chrome."""
    page = open(PAGE, encoding="utf-8").read()
    stub = stub or ("window.fetch = async (url) => ({ json: async () => url.includes('/Arrivals/') ? %s : %s });"
                    % (json.dumps(arrivals), json.dumps(status)))
    page = page.replace("<script>", "<script>" + stub + PROBE + "</script>\n<script>", 1)
    d = tempfile.mkdtemp(); path = os.path.join(d, "t.html"); open(path, "w", encoding="utf-8").write(page)
    out = subprocess.run([chrome, "--headless=new", "--no-sandbox", "--disable-gpu", f"--virtual-time-budget={budget}", *flags, "--dump-dom", "file://" + path],
                         capture_output=True, text=True, timeout=90).stdout
    def col(id_):
        m = re.search(r'id="dir%sName">([^<]*)<.*?id="col%s">(.*?)</div>\s*</section>' % (id_, id_), out, re.S)
        name, body = m.group(1), m.group(2)
        rows = re.findall(r'class="dest">([^<]*)<.*?class="mins">([^<]*)<', body, re.S)
        return name, rows, "No trains reported" in body
    status_txt = re.search(r'id="status">([^<]*)<', out).group(1)
    mode = re.search(r'id="mode">([^<]*)<', out).group(1)
    title = re.search(r"<title>(\{[^<]*\})</title>", out)
    run.last = dict(json.loads(html.unescape(title.group(1))) if title else {},
                    updated=re.search(r'id="updated">([^<]*)<', out).group(1),
                    doctype=out.lstrip().lower().startswith("<!doctype html>"),
                    # whatever sits in each column heading, tags and spacing stripped
                    heads=[" ".join(re.sub(r"<[^>]*>", " ", h).split()) for h in re.findall(r'class="colhead">(.*?)</div>', out, re.S)],
                    scrolling=bool(re.search(r'class="ticker scrolling"', out)),
                    # the word anywhere in the two columns, above or beside the rows
                    towards="towards" in out.split('class="cols"', 1)[-1].split('class="foot"', 1)[0].lower())
    return col("A"), col("B"), status_txt, mode
fails = 0
def check(name, cond, detail=""):
    global fails
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else "  " + str(detail)))
    fails += 0 if cond else 1
a, b, st, mode = run(*CASES["both ways"])
check("live data replaced the sample", mode == "live", mode)
check("the page has a doctype, so browsers are not in quirks mode", run.last["doctype"])
check("last updated is an age, not a clock time", run.last["updated"] == "just now", run.last["updated"])
good_stroke = run.last["stroke"]
check("both ways: headings from the platforms", (a[0], b[0]) == ("EASTBOUND", "WESTBOUND"), (a, b))
check("no towards text beside or under a heading, in the page or in the headings", run.last["heads"] == ["EASTBOUND", "WESTBOUND"] and not run.last["towards"], (run.last["heads"], run.last["towards"]))
check("rows drawn, branch kept where it matters", a[1] == [("Cockfosters", "2 min")] and b[1] == [("Heathrow Terminal 4", "5 min")], (a[1], b[1]))
a, b, st, mode = run(*CASES["closure"])
check("closure: eastbound trains stay one column", a[0] == "EASTBOUND" and len(a[1]) == 2, a)
check("the westbound column stays and says it is empty", b[0] == "WESTBOUND" and b[2] and not b[1], b)
check("the status is the worst one, with the cause", st.startswith("Part Closure - no service between Hyde Park Corner"), st)
check("and the tick beside it is no longer green", run.last["stroke"] and run.last["stroke"] != good_stroke, (run.last["stroke"], good_stroke))
a, b, st, mode = run(*CASES["no direction"])
check("no direction: split by where they go", (a[0], b[0]) == ("TOWARDS RICHMOND", "TOWARDS STRATFORD"), (a, b))
check("and that heading is the whole heading", run.last["heads"] == ["TOWARDS RICHMOND", "TOWARDS STRATFORD"], run.last["heads"])
a, b, st, mode = run(*CASES["one list"])
check("one destination, no heading: one list, the other column blank", a[0] == "LEWISHAM" and len(a[1]) == 2 and b[0] == "" and not b[2], (a, b))
# a station whose own name ends in "Station" keeps it: one tail only
a, b, st, mode = run([A("Southbound - Platform 2", "inbound", "Battersea Power Station", "Battersea Power Station Underground Station", 180, "p"),
                      A("Northbound - Platform 1", "outbound", "Edgware via Bank", "Edgware Underground Station", 240, "q")], GOOD)
check("one tail only: Battersea Power Station keeps its Station", a[1] and a[1][0][0] == "Battersea Power Station", a[1])
check("and a branch that matters stays on the row", b[1] and b[1][0][0] == "Edgware via Bank", b[1])

# four trains a side, the nearest four, spaced a little more open than five were
a, b, st, mode = run(MANY, GOOD, flags=["--window-size=1280,800"])
check("at most four rows per column, six trains each way", len(a[1]) == 4 and len(b[1]) == 4, (len(a[1]), len(b[1])))
check("and they are the four nearest", [r[1] for r in a[1]] == ["1 min", "2 min", "3 min", "4 min"] and a[1][0][0] == "Cockfosters", a[1])
w = run.last["cqw"]    # 1% of the board's width, the unit the page's sizes are in
check("rows sit at five's spacing opened by 8% (6.4 units x 1.08)", run.last["pitch"] and abs(run.last["pitch"] / w - 6.4 * 1.08) < 0.05, (run.last["pitch"], w))
check("the block drops about 1% of the width below where five's first row sat", run.last["drop"] and abs(run.last["drop"] / w - 1.0) < 0.05, (run.last["drop"], w))
check("the spare space is at the bottom of the column", run.last["spare"] is not None and run.last["spare"] > run.last["pitch"] * 0.25, (run.last["spare"], run.last["pitch"]))
a, b, st, mode = run(CASES["both ways"][0], GOOD)
check("fewer trains than four draw only those", len(a[1]) == 1 and len(b[1]) == 1, (a[1], b[1]))

# a status too long for its line scrolls; one that fits does not move
a, b, st, mode = run(CASES["both ways"][0], GOOD)
check("a short status stands still", not run.last["scrolling"] and run.last["anim"][0] == "none" and st == "Good Service", (run.last["scrolling"], run.last["anim"], st))
a, b, st, mode = run(CASES["both ways"][0], LONG)
check("a long status keeps its whole text and gets the scrolling treatment", run.last["scrolling"] and st.endswith("may be cancelled"), (run.last["scrolling"], st[-30:]))
check("it rests 3 s once, runs at an even pace and never stops between passes", run.last["anim"] == ["ticker", "3s", "infinite", "linear"], run.last["anim"])
check("the loop is the line and a copy of it, so each pass ends where the next starts", run.last["copyW"] and abs(run.last["copyW"] - run.last["line"]) < 1 and abs(run.last["track"] - 2 * (run.last["line"] + run.last["gap"])) < 2, run.last)
check("the copy carries no ids and the page reads the status once", run.last["copyIds"] == 0 and st.count("Severe Delays") == 1, run.last["copyIds"])
check("the gap between passes is a few words wide (about 3 text heights)", 2 < run.last["gap"] / (run.last["cqw"] * 1.45) < 4, (run.last["gap"], run.last["cqw"]))
check("the mark scrolls with the text, so the warning is not left standing", run.last["line"] > run.last["gap"], run.last)
check("the tick is no longer green on the long one", run.last["stroke"] and run.last["stroke"] != good_stroke, (run.last["stroke"], good_stroke))
# the same status on every refresh must not restart the pass: 30 s refresh, 45 s of page time
steady = ("window.fetch = async (url) => ({ json: async () => url.includes('/Arrivals/') ? %s : %s });"
          % (json.dumps(CASES["both ways"][0]), json.dumps(LONG)))
a, b, st, mode = run(None, None, stub=steady, budget=45000)
check("a refresh with the same status leaves the line alone, mid-pass", run.last["scrolling"] and run.last["muts"] == 0 and mode == "live", (run.last["scrolling"], run.last["muts"], mode))
# reduced motion: cut with an ellipsis instead
a, b, st, mode = run(CASES["both ways"][0], LONG, flags=["--force-prefers-reduced-motion"])
check("reduced motion: the long status does not scroll", not run.last["scrolling"] and run.last["anim"][0] == "none", (run.last["scrolling"], run.last["anim"]))
check("reduced motion: it is cut with an ellipsis, not lost", run.last["ellipsis"] == "ellipsis" and run.last["cut"], (run.last["ellipsis"], run.last["cut"]))
# a phone: the page stacks, and the line still scrolls when it is too long
a, b, st, mode = run(CASES["both ways"][0], LONG, flags=["--window-size=390,800"])
check("on a phone the long status scrolls too", run.last["scrolling"], run.last["scrolling"])
a, b, st, mode = run(MANY, GOOD, flags=["--window-size=390,800"])
check("on a phone, four rows at most", len(a[1]) == 4 and len(b[1]) == 4, (len(a[1]), len(b[1])))

# the status call failing must not invent a green tick, nor lose the trains
a, b, st, mode = run(CASES["both ways"][0], {"httpStatusCode": 429, "message": "rate limit"})
check("a rate-limited status is unknown, not Good Service", st == "Service status unknown" and mode == "live" and a[1], (st, mode, a[1]))
throwing = ("window.fetch = async (url) => ({ json: async () => { if (url.includes('/Status')) throw new SyntaxError('html'); return %s; } });"
            % json.dumps(CASES["both ways"][0]))
a, b, st, mode = run(None, None, stub=throwing)
check("a status body that is not JSON keeps the trains and says unknown", st == "Service status unknown" and a[1] and mode == "live", (st, mode, a[1]))

# a refresh that fails after a live load is "no live data", not "sample data"
flaky = ("let n = 0; window.fetch = async (url) => { n++; if (n > 2) throw new TypeError('offline');"
         " return { json: async () => url.includes('/Arrivals/') ? %s : %s }; };" % (json.dumps(CASES["both ways"][0]), json.dumps(GOOD)))
a, b, st, mode = run(None, None, stub=flaky, budget=45000)
check("after a live load, a dead feed is 'no live data' with the live rows kept", mode == "no live data" and a[0] == "EASTBOUND" and a[1], (mode, a))
check("and last updated counts up", run.last["updated"].endswith("ago") and run.last["updated"] != "just now", run.last["updated"])

print("all passed" if not fails else f"{fails} FAILED"); sys.exit(1 if fails else 0)
