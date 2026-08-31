#!/usr/bin/env python3
"""Build the two self-contained distributables.

    SEND THIS/CTS MOA Engine.command   macOS  -- double-click
    SEND THIS/CTS MOA Engine.bat       Windows -- double-click

Each is one file containing the whole engine. The modules are gzipped, base64'd
and registered in sys.modules **from memory**, so nothing is unpacked to disk
and there is no folder of loose scripts to keep together. Only data is written:
on first run the bundle creates a "CTS MOA Engine" folder beside itself with
input/, output/ and a blank contacts template.

Both files are polyglots -- valid shell/batch *and* valid Python -- which is
what lets a single file be both double-clickable and runnable by Python.

Run:  python3 build_single_file.py
"""
import base64
import gzip
import json
import os
import stat
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(HERE, "SEND THIS")

# Runtime modules only. The build scripts and the icon generator are developer
# tools and would just make the download bigger.
MODULES = ["config", "store", "sheets", "sources", "classify", "authors",
           "enrich", "scheduler", "gui", "moa_engine", "backtest", "selftest"]

# Small starter files. Deliberately NOT the ASCPT programme export: that is a
# colleague's data and a point-in-time snapshot, not ours to redistribute.
DATA = {"input/contacts.xlsx": "input/contacts.xlsx"}

BOOTSTRAP = r'''
import base64, gzip, io, json, os, sys, types

_PAYLOAD = "@@PAYLOAD@@"


def _unpack():
    return json.loads(gzip.decompress(base64.b64decode(_PAYLOAD)).decode("utf-8"))


def _appdir(bundle):
    """Working folder: "CTS MOA Engine" beside the bundle, else in Documents.

    Beside the file is the obvious place to look for your own results. A bundle
    run from a read-only mount, or from inside a .zip a mail client unpacked to
    a temp folder, falls back to Documents rather than failing.
    """
    beside = os.path.join(os.path.dirname(os.path.abspath(bundle)),
                          "CTS MOA Engine")
    for cand in (beside, os.path.join(os.path.expanduser("~"), "Documents",
                                      "CTS MOA Engine")):
        try:
            os.makedirs(os.path.join(cand, "input"), exist_ok=True)
            os.makedirs(os.path.join(cand, "output"), exist_ok=True)
            probe = os.path.join(cand, ".writable")
            with open(probe, "w") as fh:
                fh.write("ok")
            os.remove(probe)
            return cand
        except OSError:
            continue
    sys.exit("Could not create a working folder beside this file or in Documents.")


def _install(payload, appdir):
    """Register every bundled module in sys.modules, from memory.

    __file__ is pointed at the working folder so config.HERE resolves there and
    input/ and output/ land next to the user's results, not in a temp dir.
    """
    src = payload["modules"]
    for name in src:
        mod = types.ModuleType(name)
        mod.__file__ = os.path.join(appdir, name + ".py")
        mod.__package__ = ""
        sys.modules[name] = mod
    for name, code in src.items():
        exec(compile(code, mod.__file__, "exec"), sys.modules[name].__dict__)


def _seed(payload, appdir):
    for rel, b64 in payload.get("data", {}).items():
        dest = os.path.join(appdir, rel)
        if os.path.exists(dest):
            continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as fh:
            fh.write(base64.b64decode(b64))
    settings = os.path.join(appdir, "settings.json")
    if not os.path.exists(settings):
        with open(settings, "w") as fh:
            json.dump({"output_dir": "./output", "input_dir": "./input",
                       "owner_initials": "", "ncbi_email": ""}, fh, indent=2)


def main():
    bundle = os.path.abspath(sys.argv[0])
    appdir = _appdir(bundle)
    payload = _unpack()
    os.environ.setdefault("MOA_SETTINGS", os.path.join(appdir, "settings.json"))
    _install(payload, appdir)
    _seed(payload, appdir)

    args = sys.argv[1:]
    if args:
        import moa_engine
        sys.exit(moa_engine.main(args))
    # No arguments: this was double-clicked, so open the browser app. The GUI
    # shells out per action, and there is no moa_engine.py on disk to shell to,
    # so point it back at this bundle.
    import gui
    gui.ENGINE_ARGV = [sys.executable, "-u", bundle]
    gui.main()


main()
'''

MAC_HEADER = """#!/bin/sh
"exec" "/bin/sh" "-c" 'for c in python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 /Library/Frameworks/Python.framework/Versions/Current/bin/python3 python; do command -v "$c" >/dev/null 2>&1 && "$c" -c pass >/dev/null 2>&1 && exec "$c" "$0" "$@"; done; printf "\\n  This tool needs Python, which is not on this Mac yet.\\n\\n  A download page is opening in your browser now.\\n  Download the big yellow button, open the installer, click through it,\\n  then double-click this file again. It takes about two minutes and you\\n  only ever do it once.\\n\\n  If the page did not open:  https://www.python.org/downloads/\\n\\n  Press Return to close this window. "; open "https://www.python.org/downloads/" >/dev/null 2>&1; read -r _; exit 1' "$0" "$@"
# sh runs the line above and replaces itself with Python running this same file.
# Python reads it as a string expression and does nothing with it -- which is
# what lets one file be both double-clickable and importable.
#
# Each candidate is TESTED, not just located. Every Mac ships /usr/bin/python3,
# but on a machine without working developer tools it is a stub that prints a
# wall of Xcode errors instead of running. `-c pass` weeds it out.
#
# When nothing works we open the download page rather than only naming it: the
# recipient is a journal editor, not a developer, and a URL printed in a
# terminal is something they have to retype.
"""

# The batch header MUST be exactly one line: `python -x` skips the first line
# only, so a multi-line header makes Python choke on line 2. That bug shipped
# once and made the Windows build unusable -- it is why build() asserts on it.
#
# `exit /b` inside the parenthesised block ends the batch outright, so once a
# working Python is found the rest of the line never runs. py -3 is tried first
# because a bare `python` on Windows may be the Microsoft Store stub, which
# opens the Store instead of running anything.
WIN_HEADER = (
    '@echo off & py -3 -c "pass" >nul 2>&1 && (py -3 -x "%~f0" %* & exit /b)'
    ' & python -c "pass" >nul 2>&1 && (python -x "%~f0" %* & exit /b)'
    ' & echo. & echo   This tool needs Python, which is not on this PC yet.'
    ' & echo. & echo   A download page is opening in your browser now.'
    ' & echo   Run the installer and TICK "Add Python to PATH" on the first screen,'
    ' & echo   then double-click this file again. About two minutes, once only.'
    ' & echo. & echo   If the page did not open: https://www.python.org/downloads/'
    ' & echo. & start "" "https://www.python.org/downloads/"'
    ' & pause\r\n')

WIN_NOTE = """# (Line 1 above is Windows batch; Python skipped it with -x. On macOS the
#  .command variant is used instead, which needs no such trick.)
"""


def build():
    payload = {"modules": {}, "data": {}}
    for name in MODULES:
        path = os.path.join(HERE, name + ".py")
        if not os.path.exists(path):
            print(f"  skip (missing): {name}.py")
            continue
        with open(path, encoding="utf-8") as fh:
            payload["modules"][name] = fh.read()
    for rel, src in DATA.items():
        path = os.path.join(HERE, src)
        if os.path.exists(path):
            with open(path, "rb") as fh:
                payload["data"][rel] = base64.b64encode(fh.read()).decode("ascii")
        else:
            print(f"  skip (missing): {src}")

    blob = base64.b64encode(
        gzip.compress(json.dumps(payload).encode("utf-8"), 9)).decode("ascii")
    body = BOOTSTRAP.replace("@@PAYLOAD@@", blob)

    os.makedirs(DIST, exist_ok=True)
    out = []

    mac = os.path.join(DIST, "CTS MOA Engine.command")
    with open(mac, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(MAC_HEADER + body)
    os.chmod(mac, os.stat(mac).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    out.append(mac)

    assert WIN_HEADER.count("\n") == 1, (
        "the batch header must be exactly one line -- python -x skips only the first")
    win = os.path.join(DIST, "CTS MOA Engine.bat")
    with open(win, "wb") as fh:
        fh.write(WIN_HEADER.encode("utf-8"))          # CRLF, written literally
        fh.write((WIN_NOTE + body).encode("utf-8"))
    out.append(win)

    print(f"\nbundled {len(payload['modules'])} modules, "
          f"{len(payload['data'])} data file(s)")
    for p in out:
        print(f"  {os.path.getsize(p) / 1024:6.0f} KB  {p}")
    return out


if __name__ == "__main__":
    build()
