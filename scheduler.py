"""Optional monthly schedule, entirely under the user's control.

Nothing is installed until `moa-engine start` is run, and `moa-engine stop`
removes it completely. The scheduled job only ever runs `update --go`; the
annual dossier stays manual, because it needs a fresh ASCPT program export
that only a human can drop in.

macOS uses launchd and Windows uses Task Scheduler, both wired up here. On
Linux we print the cron line to add rather than editing the user's crontab
behind their back.

NOTE: the Windows path has not been run on a Windows machine. It is written
from the documented schtasks interface; treat it as untested.
"""
import os
import plistlib
import subprocess
import sys

import config

LABEL = "com.ascpt.cts.moa-engine"
TASK_NAME = "CTS MOA engine monthly update"
PLIST_DIR = os.path.expanduser("~/Library/LaunchAgents")
PLIST_PATH = os.path.join(PLIST_DIR, f"{LABEL}.plist")
LOG_PATH = os.path.join(config.HERE, "schedule.log")

WINDOWS = sys.platform.startswith("win")


def _is_macos():
    return sys.platform == "darwin"


def _loaded():
    if not _is_macos():
        return False
    p = subprocess.run(["launchctl", "list"], capture_output=True, text=True)
    return LABEL in (p.stdout or "")


# ------------------------------------------------------------------ Windows
def _schtasks(*args):
    return subprocess.run(["schtasks"] + list(args),
                          capture_output=True, text=True)


def _win_command():
    """The command line Task Scheduler will run.

    pythonw.exe rather than python.exe so the monthly run does not flash a
    console window at whoever happens to be logged in.
    """
    exe = sys.executable
    quiet = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(quiet):
        exe = quiet
    script = os.path.join(config.HERE, "moa_engine.py")
    return f'"{exe}" "{script}" update --go'


def _win_status():
    p = _schtasks("/query", "/tn", TASK_NAME)
    if p.returncode != 0:
        return "Schedule: OFF — nothing runs unless you run it yourself"
    return (f"Schedule: ON — Task Scheduler task {TASK_NAME!r} runs "
            f"'update --go' monthly.\n"
            f"          Inspect it in Task Scheduler, or turn it off with "
            f"'moa-engine stop'.")


def _win_start(day, hour):
    p = _schtasks("/create", "/tn", TASK_NAME, "/tr", _win_command(),
                  "/sc", "monthly", "/d", str(int(day)),
                  "/st", f"{int(hour):02d}:17", "/f")
    if p.returncode != 0:
        print("Could not create the scheduled task.")
        print((p.stderr or p.stdout or "").strip()[:400])
        print("\nYou can add it by hand in Task Scheduler instead:")
        print(f"  Name    : {TASK_NAME}")
        print(f"  Trigger : monthly, day {day}, {hour:02d}:17")
        print(f"  Action  : {_win_command()}")
        return 1
    print(f"Schedule ON — 'update --go' will run on day {day} of each month "
          f"at {hour:02d}:17.")
    print("Turn it off any time with:  moa-engine stop")
    print("\nNote: this Windows path is newly added and has not been tested on "
          "a Windows machine.\nCheck Task Scheduler once to confirm the task "
          "looks right.")
    return 0


def _win_stop():
    p = _schtasks("/query", "/tn", TASK_NAME)
    if p.returncode != 0:
        print("Schedule is already OFF — nothing to remove.")
        return 0
    p = _schtasks("/delete", "/tn", TASK_NAME, "/f")
    if p.returncode != 0:
        print("Could not remove the scheduled task.")
        print((p.stderr or p.stdout or "").strip()[:400])
        return 1
    print("Schedule OFF — the scheduled task has been removed.")
    print("Your queue and dossier files are untouched.")
    return 0


# ------------------------------------------------------------------ status
def status_line():
    if WINDOWS:
        return _win_status()
    if not _is_macos():
        return ("Schedule: not managed on this platform. "
                "See README for the cron line to add manually.")
    if os.path.exists(PLIST_PATH) and _loaded():
        try:
            with open(PLIST_PATH, "rb") as fh:
                d = plistlib.load(fh)
            cal = d.get("StartCalendarInterval", {})
            return (f"Schedule: ON — runs 'update --go' on day "
                    f"{cal.get('Day', '?')} at {cal.get('Hour', '?'):02d}:00. "
                    f"Log: {LOG_PATH}")
        except Exception:
            return "Schedule: ON (could not read the plist)"
    if os.path.exists(PLIST_PATH):
        return "Schedule: installed but NOT loaded — run './moa-engine start' again"
    return "Schedule: OFF — nothing runs unless you run it yourself"


def start(day=7, hour=9):
    if WINDOWS:
        return _win_start(day, hour)
    if not _is_macos():
        print("Automatic scheduling is wired up for macOS and Windows.")
        print("On Linux, add this to `crontab -e` instead:\n")
        print(f"  0 {hour} {day} * *  cd {config.HERE!r} && "
              f"./moa-engine update --go >> schedule.log 2>&1")
        return 1
    # No settings check: the defaults work, so there is nothing to configure
    # before a scheduled run can succeed.
    os.makedirs(PLIST_DIR, exist_ok=True)
    runner = os.path.join(config.HERE, "moa-engine")
    plist = {
        "Label": LABEL,
        "ProgramArguments": [runner, "update", "--go"],
        "WorkingDirectory": config.HERE,
        "StartCalendarInterval": {"Day": int(day), "Hour": int(hour), "Minute": 17},
        "StandardOutPath": LOG_PATH,
        "StandardErrorPath": LOG_PATH,
        "RunAtLoad": False,
        # If the Mac is asleep at the scheduled moment, launchd runs the job at
        # the next wake. The scan re-reads a trailing window and dedups by key,
        # so a late or repeated run costs nothing.
    }
    with open(PLIST_PATH, "wb") as fh:
        plistlib.dump(plist, fh)

    subprocess.run(["launchctl", "unload", PLIST_PATH],
                   capture_output=True, text=True)
    p = subprocess.run(["launchctl", "load", PLIST_PATH],
                       capture_output=True, text=True)
    if p.returncode != 0:
        print(f"launchctl load failed: {(p.stderr or '').strip()}")
        return 1
    print(f"Schedule ON — 'update --go' will run on day {day} of each month "
          f"at {hour:02d}:17.")
    print(f"Log: {LOG_PATH}")
    print("Turn it off any time with:  ./moa-engine stop")
    return 0


def stop():
    if WINDOWS:
        return _win_stop()
    if not _is_macos():
        print("No launchd schedule to remove. If you added a cron line, "
              "remove it with `crontab -e`.")
        return 0
    if not os.path.exists(PLIST_PATH):
        print("Schedule is already OFF — nothing to remove.")
        return 0
    subprocess.run(["launchctl", "unload", PLIST_PATH],
                   capture_output=True, text=True)
    os.remove(PLIST_PATH)
    print("Schedule OFF — the job is unloaded and the plist removed.")
    print("Your queue and dossier files are untouched.")
    return 0
