#!/usr/bin/env python3
"""Keeps the board on main: when main has moved, install it, check that the board
still works, and put the old code back if it does not.

    sudo python3 /opt/tubeboard/updater.py --now      # update now, by hand
    python3 /opt/tubeboard/updater.py --dry-run       # say what a run would do, change nothing
    sudo python3 /opt/tubeboard/updater.py --timer    # what tubeboard-update.timer runs, about 04:00

The board hangs on a wall nobody can reach, so every step that could leave it
broken has a way back:

 1. settings.json "auto_update": false stops the nightly run. --now still runs.
 2. As the clone's owner, fetch main. Nothing new, local changes in the clone, a
    branch other than main, commits of its own, or a commit that already failed
    here: stop, and touch nothing.
 3. Note whether the board is live (a good fetch in the last five minutes), and
    whether the settings page answers.
 4. Copy /opt/tubeboard, and the systemd units, to /opt/tubeboard.bak-update.
 5. git merge --ff-only, as the owner.
 6. install.sh as root with SKIP_COMITUP=1 (a full run restarts comitup, which can
    drop the WiFi), with a time limit.
 7. The new updater must start, and the board must come up on the new code within
    two minutes. Then it is watched until the settings card's minute has passed
    and the rotation has gone round once (2 to 6 minutes): no restart, no failed
    draw, drawing steadily, live again if it was live before with every board it
    showed fetched, and the settings page answering if it did before.
 8. If not: put the backup back (keeping the settings.json there now), restart
    both services, reset the clone to the commit before, and record the commit as
    bad so it is never tried again. A failure that may be the night rather than
    the code (install.sh, which needs apt and the network; a board that draws but
    cannot fetch) is rolled back the same way but tried again the next night, and
    marked bad only on the third. A newer commit on main is tried as usual.

Every step is logged to stdout (journalctl -u tubeboard-update), and one line per
run goes to /var/lib/tubeboard/update-history, which survives a reboot where the
journal does not. Exit 0: updated, or nothing to do. 1: stopped, or an error.
3: rolled back.
"""
import argparse
import fcntl
import json
import os
import pwd
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

OPT = "/opt/tubeboard"
BACKUP = OPT + ".bak-update"          # one, replaced each time; the dated backups are a person's
STATE = "/var/lib/tubeboard"          # on the card: the bad commits and the history outlive a reboot
HEALTH = "/run/tubeboard/health.json"  # board.py writes it every frame
UNITS = "/etc/systemd/system"
PORTAL = "http://127.0.0.1:8080/"
REMOTE, BRANCH = "origin", "main"
SERVICES = ("tubeboard.service", "tubeboard-portal.service")
# What install.sh puts in /etc/systemd/system. They go into the backup with the code:
# a commit that renamed the board's program would otherwise leave the old code
# restored under a unit that cannot start it.
UNIT_FILES = SERVICES + ("tubeboard-update.service", "tubeboard-update.timer")
UNITS_SAVED = ".systemd"              # inside the backup, never copied into /opt

INSTALL_SECONDS = 15 * 60   # apt-get update and install on a Pi 3 A+, with room to spare
LIVE_SECONDS = 300          # live: a good fetch this recently
# How the new board is watched. First it has START_SECONDS to come up on the new
# commit. Then it is watched for long enough that the settings card (board.py's
# ADDRESS_SECONDS, the first minute) has gone, the footer has drawn as it does all
# day, and the rotation has come round once, so every board has been drawn with the
# new code and has fetched: at least OBSERVE_MIN, at most OBSERVE_MAX. Watching only
# the first 20 s, as the first version did, saw board 1 with the card on it and
# passed code that broke every frame after (review, 9 Oct 2026).
ADDRESS_SECONDS = 60        # board.py's settings card; keep the two the same
START_SECONDS = 120
OBSERVE_MIN, OBSERVE_MAX = 120, 360
HEALTH_SECONDS = START_SECONDS + OBSERVE_MAX
POLL_SECONDS = 5
# A failure that may be the night and not the code (install.sh, which needs apt and
# the network; a board that draws but cannot fetch) is rolled back and tried again
# the next night. Only after this many such nights is the commit marked bad.
TRIES = 3
HISTORY_LINES = 200

DONE, STOPPED, ROLLED_BACK = 0, 1, 3


def short(commit):
    return (commit or "unknown")[:7]


def switched_off(value):
    """auto_update as a person might write it by hand: false, "false", "no", "off", 0."""
    return value is False or value == 0 or str(value).strip().lower() in ("false", "no", "off", "0")


def as_user(cmd, user, euid=None, home=None):
    """cmd, run as `user` when this is root and the user is someone else. git runs as
    the clone's owner, so nothing in the clone ends up owned by root and breaks the
    next git pull by hand. HOME is the owner's: git reads its config from there and
    stops dead on a config file it may not read."""
    euid = os.geteuid() if euid is None else euid
    if not user or euid != 0 or user == "root":
        return list(cmd)
    if home is None:
        home = pwd.getpwnam(user).pw_dir
    return ["runuser", "-u", user, "--", "env", f"HOME={home}", "GIT_TERMINAL_PROMPT=0"] + list(cmd)


def run_command(cmd, user=None, timeout=None):
    """Run cmd and hand back (exit code, output). One that outlives its time limit is
    stopped with its whole process group: install.sh runs apt, and stopping only bash
    would leave apt running and holding its lock."""
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")    # a fetch must never wait for a password
    try:
        p = subprocess.Popen(as_user(cmd, user), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, text=True, env=env, start_new_session=True)
    except OSError as e:
        return 127, str(e)
    try:
        out = p.communicate(timeout=timeout)[0]
        return p.returncode, out or ""
    except subprocess.TimeoutExpired:
        pass
    # TERM first and a while to go: dpkg stopped dead can need a person to finish it
    out = ""
    for sig, wait in ((signal.SIGTERM, 30), (signal.SIGKILL, None)):
        try:
            os.killpg(p.pid, sig)
        except OSError:
            pass
        try:
            out = p.communicate(timeout=wait)[0] or ""
            break
        except subprocess.TimeoutExpired:
            pass
    return 124, out + f"\n(stopped after {timeout} s)"


def http_status(url, timeout=15):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:                               # noqa: BLE001
        return None


class Updater:
    """One run. Everything it does to the Pi goes through `run` (git, install.sh,
    systemctl), `now`, `sleep` and `http_get`, and every path is an argument, so the
    tests drive it against a model of the Pi in a temp dir."""

    def __init__(self, clone=None, run=run_command, now=time.time, sleep=time.sleep,
                 http_get=http_status, log=None, opt=OPT, backup=BACKUP, state=STATE,
                 health=HEALTH, units=UNITS, python=sys.executable):
        self.clone, self.run, self.now, self.sleep, self.http_get = clone, run, now, sleep, http_get
        self.log = log or (lambda msg: print(msg, flush=True))
        self.opt, self.backup, self.state, self.health, self.units = opt, backup, state, health, units
        self.python = python
        self.owner = None
        self.dry = False
        self.bad_path = os.path.join(state, "bad-commits")
        self.marker = os.path.join(state, "updating.json")
        self.history_path = os.path.join(state, "update-history")
        self.clone_file = os.path.join(state, "clone")

    # ------------------------------------------------------------ small parts

    def stamp(self):
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.now()))

    def git(self, *args, timeout=60):
        return self.run(["git", "-C", self.clone, *args], user=self.owner, timeout=timeout)

    def git_out(self, *args):
        rc, out = self.git(*args)
        return out.strip() if rc == 0 else None

    def installed(self):
        """The commit /opt/tubeboard was installed from, as install.sh wrote it."""
        try:
            with open(os.path.join(self.opt, "VERSION")) as f:
                return f.read().strip() or None
        except OSError:
            return None

    def settings(self):
        try:
            with open(os.path.join(self.opt, "settings.json")) as f:
                d = json.load(f)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            return None
        return d if isinstance(d, dict) else None

    def read_health(self):
        try:
            with open(self.health) as f:
                h = json.load(f)
        except (OSError, ValueError):
            return None
        return h if isinstance(h, dict) else None

    def live(self, h, t):
        ok = h.get("last_fetch_ok")
        return isinstance(ok, (int, float)) and t - ok <= LIVE_SECONDS

    def history(self, text):
        """One line per run, on the card. journald on the Pi is volatile, so this is
        the only account of last week's updates after a power cut."""
        if self.dry:
            return
        try:
            try:
                with open(self.history_path) as f:
                    lines = f.read().splitlines()
            except FileNotFoundError:
                lines = []
            lines = (lines + [f"{self.stamp()} {text}"])[-HISTORY_LINES:]
            self.write_file(self.history_path, "\n".join(lines) + "\n")
        except OSError as e:
            self.log(f"could not write {self.history_path}: {e}")

    def write_file(self, path, text):
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    def finish(self, code, text):
        self.log(text)
        self.history(text)
        return code

    def stop(self, text):
        return self.finish(STOPPED, "stopped: " + text)

    def bad_reason(self, commit):
        try:
            with open(self.bad_path) as f:
                for line in f:
                    parts = line.strip().split(" ", 1)
                    if parts and parts[0] == commit:
                        return parts[1] if len(parts) > 1 else "no reason recorded"
        except OSError:
            pass
        return None

    def record_bad(self, commit, why):
        try:
            with open(self.bad_path, "a") as f:
                f.write(f"{commit} {self.stamp()} {why}\n")
                f.flush()
                os.fsync(f.fileno())
        except OSError as e:
            self.log(f"COULD NOT RECORD {short(commit)} AS BAD in {self.bad_path}: {e}")

    def clear_marker(self):
        try:
            os.unlink(self.marker)
        except OSError as e:
            self.log(f"could not delete {self.marker}: {e}. The next run will think this one died")

    def take_lock(self):
        """A run by hand while the timer's run is going would merge and restore on top
        of it. None: there is no lock to take. A real run then cannot write its state;
        a dry run makes nothing, not even this file, and with no file no run is going."""
        path = os.path.join(self.state, "lock")
        try:
            if not self.dry:
                os.makedirs(self.state, exist_ok=True)
            f = open(path) if self.dry else open(path, "a")
        except OSError:
            return None
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.close()
            return False
        return f

    def find_clone(self):
        clone = self.clone
        if not clone:
            # install.sh writes where it was run from: that clone is what the Pi follows
            try:
                with open(self.clone_file) as f:
                    clone = f.read().strip()
            except OSError:
                clone = None
        if not clone or not os.path.exists(os.path.join(clone, ".git")):
            return f"no git clone at {clone or '(none)'}: run install.sh once from the clone, " \
                   f"or give --clone"
        try:
            self.owner = pwd.getpwuid(os.stat(clone).st_uid).pw_name
        except (KeyError, OSError) as e:
            return f"cannot tell who owns {clone}: {e}"
        self.clone = clone
        return None

    # ------------------------------------------------------------ backup and restore

    def make_backup(self):
        """/opt/tubeboard and the units, to BACKUP. Built beside it and renamed into
        place, so a power cut part-way never leaves half a backup under its name.
        Nothing in /opt has changed yet then, and the next run makes a new one."""
        tmp = self.backup + ".tmp"
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.copytree(self.opt, tmp, symlinks=True, ignore=shutil.ignore_patterns("__pycache__"))
        os.makedirs(os.path.join(tmp, UNITS_SAVED))
        for name in UNIT_FILES:
            src = os.path.join(self.units, name)
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(tmp, UNITS_SAVED, name))
        shutil.rmtree(self.backup, ignore_errors=True)
        os.rename(tmp, self.backup)
        os.sync()

    def restore(self):
        """The backup back as /opt/tubeboard, with the settings.json there now: the
        owner may have changed a setting while the update ran. Built beside it and
        swapped in by rename, so the board's directory is never half old, half new."""
        if not os.path.isdir(self.backup):
            raise RuntimeError(f"there is no backup at {self.backup}")
        keep = None
        try:
            with open(os.path.join(self.opt, "settings.json"), "rb") as f:
                keep = f.read()
        except OSError:
            pass
        tmp, old = self.opt + ".restore", self.opt + ".failed"
        for d in (tmp, old):
            shutil.rmtree(d, ignore_errors=True)
        shutil.copytree(self.backup, tmp, symlinks=True,
                        ignore=shutil.ignore_patterns(UNITS_SAVED, "__pycache__"))
        if keep is not None:
            with open(os.path.join(tmp, "settings.json"), "wb") as f:
                f.write(keep)
        if os.path.isdir(self.opt):
            os.rename(self.opt, old)
        os.rename(tmp, self.opt)
        shutil.rmtree(old, ignore_errors=True)
        saved = os.path.join(self.backup, UNITS_SAVED)
        if os.path.isdir(saved):
            for name in sorted(os.listdir(saved)):
                shutil.copy2(os.path.join(saved, name), os.path.join(self.units, name))
        os.sync()

    def restart(self):
        for cmd in (["systemctl", "daemon-reload"], ["systemctl", "restart", *SERVICES]):
            rc, out = self.run(cmd, timeout=120)
            self.log(f"{' '.join(cmd)}: {'ok' if rc == 0 else f'FAILED (exit {rc}) {out.strip()}'}")

    def reset_clone(self, commit):
        if self.git_out("rev-parse", "--verify", "HEAD^{commit}") == commit:
            return
        rc, out = self.git("reset", "--hard", commit)
        self.log(f"clone reset to {short(commit)}" if rc == 0 else
                 f"COULD NOT RESET THE CLONE to {short(commit)} (exit {rc}): {out.strip()}")

    # ------------------------------------------------------------ the board's health

    def describe(self, h, t):
        if h is None:
            return "no health file"
        last = h.get("last_draw")
        ok = h.get("last_fetch_ok")
        return (f"pid {h.get('pid')}, runs {short(h.get('version'))}, {h.get('draws', 0)} draws, "
                f"{h.get('failed_draws', 0)} failed, last draw "
                + (f"{t - last:.0f} s ago" if isinstance(last, (int, float)) else "never")
                + ", last good fetch "
                + (f"{t - ok:.0f} s ago" if isinstance(ok, (int, float)) else "never"))

    def watch_for(self, h, settings):
        """How long to watch a new board, and how many of its boards must have fetched
        by then: the settings card's minute, one turn of the rotation, and a margin."""
        try:
            rotate = max(5, min(300, int((settings or {}).get("rotate_seconds", 30))))
        except (TypeError, ValueError, OverflowError):
            rotate = 30
        boards = h.get("boards") if isinstance(h.get("boards"), int) and h.get("boards") > 0 else 1
        turn = rotate * boards if boards > 1 else 0
        window = max(OBSERVE_MIN, min(OBSERVE_MAX, ADDRESS_SECONDS + turn + 30))
        # a rotation longer than the window: only the boards shown in it can have fetched
        shown = boards if boards == 1 else min(boards, 1 + int(window // rotate))
        return window, shown

    def check(self, version, before_pid, need_live, need_portal, settings=None, quick=False):
        """Watch the restarted board until it has shown it works. Hands back (None,
        False) if it did, otherwise (why, may_be_the_night): why in words for the log,
        and whether the failure could be the network rather than the code."""
        start_by = self.now() + START_SECONDS
        first = None            # (pid, draws, when) when the new process was first seen
        why = "the board wrote no health file"
        while True:
            h = self.read_health()
            t = self.now()
            if first is None:
                if h is None:
                    why = "the board wrote no health file"
                elif version and h.get("version") != version:
                    why = f"the board runs {short(h.get('version'))}, not {short(version)}"
                elif before_pid and h.get("pid") == before_pid:
                    why = "the board was not restarted"
                else:
                    first = (h.get("pid"), h.get("draws") or 0, t)
                    window, shown = self.watch_for(h, settings)
                    if quick:
                        # after a rollback: only to say in the log whether the old code
                        # came back, which it has run for months
                        window, shown = 30, 1
                    self.log(f"health: the new board is up (pid {h.get('pid')}); watching it for "
                             f"{window} s, through the settings card and "
                             + (f"{shown} board(s) of the rotation" if shown > 1 else "its board"))
                if first is None:
                    if t >= start_by:
                        return f"{why}, after {START_SECONDS} s ({self.describe(h, t)})", False
                    self.sleep(POLL_SECONDS)
                    continue
            # watching the new process
            if h is None:
                return "the health file went away while the board was watched", False
            if h.get("pid") != first[0]:
                return (f"the board restarted while it was watched (pid {first[0]} became "
                        f"{h.get('pid')}): it crashed or gave up", False)
            if h.get("failed_draws"):
                return f"the board failed {h['failed_draws']} draw(s) ({self.describe(h, t)})", False
            if t - first[2] < window:
                self.sleep(POLL_SECONDS)
                continue
            # the whole window has passed: judge it
            draws, last = (h.get("draws") or 0) - first[1], h.get("last_draw")
            # it redraws at least every 10 s; half that rate is the floor
            if draws < window / 20 or not isinstance(last, (int, float)) or t - last > 30:
                return f"the board is not drawing ({self.describe(h, t)})", False
            if need_live:
                ok = h.get("boards_ok")
                if not self.live(h, t):
                    return ("the board draws, but has had no good fetch, and it had one before "
                            f"({self.describe(h, t)})"), True
                if isinstance(ok, int) and ok < shown:
                    return (f"only {ok} of the {shown} board(s) shown fetched, and it was live "
                            f"before ({self.describe(h, t)})"), True
            if need_portal and self.http_get(PORTAL) != 200:
                return "the board draws, but the settings page does not answer, and it did before", False
            self.log(f"health: good after {window} s. {self.describe(h, t)}"
                     + (f", {h.get('boards_ok')} of {h.get('boards')} boards fetched" if h.get("boards") else "")
                     + (", settings page answers" if need_portal else ""))
            return None, False

    # ------------------------------------------------------------ the run

    def main(self, mode):
        """mode: "timer", "now" or "dry-run". Hands back the exit code."""
        self.dry = mode == "dry-run"
        self.log(f"tube board update, --{mode}, {self.stamp()}")
        lock = self.take_lock()
        if lock is False:
            return self.stop("another update is running")
        if lock is None and not self.dry:
            return self.stop(f"cannot write {self.state}: run this with sudo")
        try:
            return self.update(mode)
        finally:
            if lock:
                lock.close()

    def update(self, mode):
        problem = self.find_clone()
        if problem:
            return self.stop(problem)
        self.log(f"clone: {self.clone}, owned by {self.owner}")
        if os.path.exists(self.marker) and not self.recover():
            # carrying on would back up the half-installed code over the good backup
            return self.stop(f"the last update did not finish and could not be put right. A person "
                             f"is needed: see above, then delete {self.marker}")

        # 1. may it?
        settings = self.settings()
        if settings is None:
            return self.stop(f"{self.opt}/settings.json cannot be read, so whether updates are "
                             "wanted is not known")
        if switched_off(settings.get("auto_update", True)):
            if mode == "timer":
                return self.finish(DONE, "auto_update is off in settings.json: nothing done")
            self.log(f"auto_update is off in settings.json, but --{mode} was asked for by hand: going on")
        else:
            self.log("auto_update is on")

        # 2. is there something, and is it safe to take?
        rc, out = self.git("fetch", "--quiet", REMOTE, BRANCH, timeout=180)
        if rc != 0:
            return self.stop(f"git fetch failed (exit {rc}): {out.strip()[-300:]}")
        target = self.git_out("rev-parse", "--verify", f"{REMOTE}/{BRANCH}^{{commit}}")
        head = self.git_out("rev-parse", "--verify", "HEAD^{commit}")
        installed = self.installed()
        if not target or not head:
            return self.stop("git could not say which commit main or the clone is at")
        self.log(f"{BRANCH} is {short(target)}, the clone is at {short(head)}, "
                 f"{self.opt} runs {short(installed)}")
        if target == head == installed:
            return self.finish(DONE, f"nothing new: {short(target)} is installed")
        branch = self.git_out("symbolic-ref", "--quiet", "--short", "HEAD")
        if branch != BRANCH:
            return self.stop(f"the clone is on {branch or 'no branch'}, not {BRANCH}: left alone")
        rc, changes = self.git("status", "--porcelain")
        if rc != 0:
            return self.stop("git status failed")
        if changes.strip():
            for line in changes.rstrip().splitlines()[:10]:
                self.log("  " + line)
            return self.stop("the clone has local changes (git status in the clone lists them). "
                             "Left alone, never overwritten: commit or discard them by hand")
        bad = self.bad_reason(target)
        if bad:
            return self.stop(f"{short(target)} failed here before ({bad}), so it is not tried again. "
                             f"Merge a fix to {BRANCH}, or delete its line in {self.bad_path} to retry")
        rc, _ = self.git("merge-base", "--is-ancestor", "HEAD", target)
        if rc != 0:
            return self.stop(f"the clone has commits that are not on {BRANCH}: left alone")
        new = self.git_out("log", "--oneline", "--no-decorate", f"HEAD..{target}") or ""
        if new:
            self.log(f"new on {BRANCH}:")
            for line in new.splitlines()[:20]:
                self.log("  " + line)
        else:
            self.log(f"the clone is at {short(target)} already, but it is not what is installed")

        # 3. how the board is doing now, to hold the new code to the same
        t = self.now()
        before = self.read_health()
        was_live = before is not None and self.live(before, t)
        portal_was_up = self.http_get(PORTAL) == 200
        self.log(f"board before: {self.describe(before, t)}; "
                 f"{'live' if was_live else 'not live'}; settings page "
                 f"{'answers' if portal_was_up else 'does not answer'}")

        if self.dry:
            self.log(f"dry run: a real run would now back up {self.opt}, merge {short(target)}, "
                     "run install.sh and check the board. Nothing was changed")
            return DONE

        # 4. the way back
        try:
            self.make_backup()
        except Exception as e:                  # noqa: BLE001
            return self.stop(f"could not back up {self.opt} ({e!r}): not updating without a way back")
        self.log(f"backup: {self.opt} -> {self.backup}")
        # From here until the end, a run that dies (a power cut) is put right by the
        # next one, which finds this file.
        try:
            self.write_file(self.marker, json.dumps({"head": head, "installed": installed,
                                                     "new": target, "started": self.stamp()}))
        except OSError as e:
            return self.stop(f"cannot write {self.marker} ({e}): not updating without it")
        try:
            # 5.
            rc, out = self.git("merge", "--ff-only", target)
            if rc != 0:
                self.clear_marker()             # nothing was changed: the merge refused as a whole
                return self.stop(f"git merge --ff-only {short(target)} refused (exit {rc}): {out.strip()[-300:]}")
            self.log(f"merged: the clone is at {short(target)}")
            # 6.
            script = os.path.join(self.clone, "pi", "install.sh")
            self.log(f"install: SKIP_COMITUP=1 bash {script}")
            started = self.now()
            rc, out = self.run(["env", "SKIP_COMITUP=1", "bash", script], timeout=INSTALL_SECONDS)
            tail = out.strip().splitlines()[-(5 if rc == 0 else 30):]
            for line in tail:
                self.log("  | " + line)
            if rc != 0:
                return self.rollback(head, installed, target, f"install.sh failed (exit {rc})",
                                     maybe_the_night=True)
            self.log(f"install: done in {self.now() - started:.0f} s")
            # 7. the updater that will run tomorrow has to start, or there is no tomorrow
            rc, out = self.run([self.python, os.path.join(self.opt, "updater.py"), "--help"], timeout=60)
            if rc != 0:
                return self.rollback(head, installed, target,
                                     f"the new updater.py does not start (exit {rc}): {out.strip()[-200:]}")
            self.log(f"health: waiting up to {HEALTH_SECONDS} s for the board to run {short(target)}")
            why, maybe = self.check(target, before.get("pid") if before else None, was_live, portal_was_up,
                                    settings=settings)
            if why:
                return self.rollback(head, installed, target, why, maybe_the_night=maybe)
        except Exception as e:                  # noqa: BLE001
            return self.rollback(head, installed, target, f"the updater itself failed: {e!r}")
        self.clear_marker()
        return self.finish(DONE, f"UPDATED: installed {short(target)} (was {short(installed)})")

    def tries(self, commit):
        """How many nights this commit has been rolled back for a reason that may
        have been the night, counting tonight."""
        path = os.path.join(self.state, "tries")
        n = 0
        try:
            with open(path) as f:
                n = sum(1 for line in f if line.split()[:1] == [commit])
        except OSError:
            pass
        try:
            with open(path, "a") as f:
                f.write(f"{commit} {self.stamp()}\n")
        except OSError as e:
            self.log(f"could not count this try in {path}: {e}")
        return n + 1

    def rollback(self, head, installed, bad, why, maybe_the_night=False):
        self.log(f"ROLLING BACK {short(bad)}: {why}")
        # first, so that whatever happens next this commit is not tried again tonight
        if maybe_the_night and (n := self.tries(bad)) < TRIES:
            self.log(f"this may be the network or apt, not the code: {short(bad)} is tried again "
                     f"tomorrow night (try {n} of {TRIES})")
        else:
            self.record_bad(bad, why)
        restored = True
        try:
            self.restore()
            self.log(f"restored {self.opt} from {self.backup}, keeping today's settings.json")
        except Exception as e:                  # noqa: BLE001
            restored = False
            self.log(f"ROLLBACK COULD NOT RESTORE {self.opt}: {e!r}. The next run tries again")
        before = self.read_health()
        self.restart()
        self.reset_clone(head)
        if restored:
            self.clear_marker()
            # nothing more can be done from here, but the log should say how it ended
            again, _ = self.check(installed, before.get("pid") if before else None, False, False,
                                  settings=self.settings(), quick=True)
            self.log("the old code is drawing again" if again is None else
                     f"THE OLD CODE IS NOT DRAWING EITHER: {again}")
        return self.finish(ROLLED_BACK, f"ROLLED BACK {short(bad)}, back on {short(installed)}: {why}")

    def recover(self):
        """The last run stopped part-way (a power cut, or it was killed): /opt/tubeboard
        can be half installed. Put the backup back, unless a person has installed
        something else since. True when the run can carry on as usual."""
        try:
            with open(self.marker) as f:
                m = json.load(f)
            head, installed, new = m["head"], m.get("installed"), m["new"]
        except (OSError, ValueError, KeyError, TypeError) as e:
            self.log(f"THE LAST UPDATE DID NOT FINISH, and its notes cannot be read ({e!r})")
            return False
        self.log(f"THE LAST UPDATE DID NOT FINISH: to {short(new)}, started {m.get('started')}")
        now_installed = self.installed()
        now_head = self.git_out("rev-parse", "--verify", "HEAD^{commit}")
        if now_installed not in (installed, new, None) or now_head not in (head, new):
            self.log(f"since then {self.opt} runs {short(now_installed)} and the clone is at "
                     f"{short(now_head)}: someone else installed, so that is left as it is")
        elif self.dry:
            self.log("dry run: a real run would put the backup back first")
            return True
        else:
            try:
                self.restore()
            except Exception as e:              # noqa: BLE001
                self.log(f"COULD NOT PUT THE BACKUP BACK: {e!r}")
                return False
            self.restart()
            self.reset_clone(head)
            self.log(f"put back: {self.opt} is {short(installed)} again")
            self.history(f"put back {short(installed)} after an update to {short(new)} did not finish")
        if not self.dry:
            self.clear_marker()
        return True


def main(argv=None):
    ap = argparse.ArgumentParser(description="Install main on the tube board, check the board "
                                             "still works, and put the old code back if not.")
    how = ap.add_mutually_exclusive_group(required=True)
    how.add_argument("--now", action="store_true",
                     help="update now, by hand. Runs even when auto_update is off")
    how.add_argument("--dry-run", action="store_true",
                     help="say what an update would do and change nothing (it does fetch main)")
    how.add_argument("--timer", action="store_true",
                     help="what tubeboard-update.timer runs: does nothing when auto_update is off")
    ap.add_argument("--clone", help="the git clone to follow (default: where install.sh was last run from)")
    a = ap.parse_args(argv)
    mode = "now" if a.now else "dry-run" if a.dry_run else "timer"
    if mode != "dry-run" and os.geteuid() != 0:
        print("run this with sudo: it installs into /opt and restarts the board", file=sys.stderr)
        return STOPPED
    return Updater(clone=a.clone).main(mode)


if __name__ == "__main__":
    sys.exit(main())
