#!/usr/bin/env python3
"""Offline test of index.html, the web version of the board.

    python3 test_index.py

Loads the page in headless Chrome with TfL's two requests stubbed, and reads
back what it drew: the column headings, the rows, the status line. The cases
are the ones that broke the Pi board before 19 Sept: a part closure with one
direction running, a station where TfL omits "direction", a single list.
Needs Chrome or Chromium; set CHROME=/path/to/chrome if it is not found.
"""
import glob
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
def run(arrivals, status):
    html = open(PAGE, encoding="utf-8").read()
    stub = ("<script>window.fetch = async (url) => ({ json: async () => url.includes('/Arrivals/') ? %s : %s });</script>\n"
            % (json.dumps(arrivals), json.dumps(status)))
    html = html.replace("<script>", stub + "<script>", 1)
    d = tempfile.mkdtemp(); path = os.path.join(d, "t.html"); open(path, "w", encoding="utf-8").write(html)
    out = subprocess.run([chrome, "--headless=new", "--no-sandbox", "--disable-gpu", "--virtual-time-budget=4000", "--dump-dom", "file://" + path],
                         capture_output=True, text=True, timeout=60).stdout
    def col(id_):
        m = re.search(r'id="dir%sName">([^<]*)<.*?id="dir%sTowards">([^<]*)<.*?id="col%s">(.*?)</div>\s*</section>' % (id_, id_, id_), out, re.S)
        name, tow, body = m.group(1), m.group(2), m.group(3)
        rows = re.findall(r'class="dest">([^<]*)<.*?class="mins">([^<]*)<', body, re.S)
        return name, tow, rows, "No trains reported" in body
    status_txt = re.search(r'id="status">([^<]*)<', out).group(1)
    mode = re.search(r'id="mode">([^<]*)<', out).group(1)
    return col("A"), col("B"), status_txt, mode
fails = 0
def check(name, cond, detail=""):
    global fails
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else "  " + str(detail)))
    fails += 0 if cond else 1
a, b, st, mode = run(*CASES["both ways"])
check("live data replaced the sample", mode == "live", mode)
check("both ways: headings from the platforms", (a[0], b[0]) == ("EASTBOUND", "WESTBOUND"), (a, b))
check("towards shown under each", (a[1], b[1]) == ("Cockfosters", "Heathrow"), (a, b))
check("rows drawn, branch kept where it matters", a[2] == [("Cockfosters", "2 min")] and b[2] == [("Heathrow Terminal 4", "5 min")], (a[2], b[2]))
a, b, st, mode = run(*CASES["closure"])
check("closure: eastbound trains stay one column", a[0] == "EASTBOUND" and len(a[2]) == 2, a)
check("the westbound column stays and says it is empty", b[0] == "WESTBOUND" and b[3] and not b[2], b)
check("the status is the worst one, with the cause", st.startswith("Part Closure - no service between Hyde Park Corner"), st)
a, b, st, mode = run(*CASES["no direction"])
check("no direction: split by where they go", (a[0], b[0]) == ("TOWARDS RICHMOND", "TOWARDS STRATFORD") and a[1] == "" and b[1] == "", (a, b))
a, b, st, mode = run(*CASES["one list"])
check("one destination, no heading: one list, the other column blank", a[0] == "LEWISHAM" and len(a[2]) == 2 and b[0] == "" and not b[3], (a, b))
print("all passed" if not fails else f"{fails} FAILED"); sys.exit(1 if fails else 0)
