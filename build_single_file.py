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
import shutil
import stat
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(HERE, "SEND THIS")

# Runtime modules only. The build scripts and the icon generator are developer
# tools and would just make the download bigger.
MODULES = ["config", "store", "sheets", "sources", "classify", "authors",
           "enrich", "gaps", "labels", "scheduler", "gui", "moa_engine",
           "backtest", "selftest"]

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

    MOA_APPDIR_BASE overrides where "beside" means. The .app launcher sets it to
    the folder holding the .app, because the script itself lives inside
    Contents/Resources and output buried in there would be invisible.

    Beside the file is the obvious place to look for your own results. A bundle
    run from a read-only mount, or from inside a .zip a mail client unpacked to
    a temp folder, falls back to Documents rather than failing.
    """
    base = os.environ.get("MOA_APPDIR_BASE") or os.path.dirname(
        os.path.abspath(bundle))
    beside = os.path.join(base, "CTS MOA Engine")
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
        # Compile under THIS module's filename. Using the loop variable from
        # the block above reported every error against the last module loaded,
        # so a SyntaxError in sources.py appeared as selftest.py.
        exec(compile(code, sys.modules[name].__file__, "exec"),
             sys.modules[name].__dict__)


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
    '@echo off & if "%~1"=="" (pyw -3 -c "pass" >nul 2>&1'
    ' && (start "" pyw -3 -x "%~f0" --gui & exit /b)'
    ' || pythonw -c "pass" >nul 2>&1'
    ' && (start "" pythonw -x "%~f0" --gui & exit /b))'
    ' & py -3 -c "pass" >nul 2>&1 && (py -3 -x "%~f0" %* & exit /b)'
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


MAC_UNQUARANTINE = """#!/bin/sh
# Double-click this if "CTS MOA Engine" will not open.
#
# macOS flags anything that arrives by email or Drive as quarantined, and
# refuses to launch an app that has not been notarized by Apple. Notarizing
# needs a paid Apple Developer account. Removing the flag is the standard fix
# and only affects this one app.
cd "$(dirname "$0")/.." || exit 1
APP="CTS MOA Engine.app"
if [ ! -d "$APP" ]; then
  echo "Could not find $APP next to this file."
  echo "Keep this folder alongside the app and try again."
  printf "Press Return to close. "; read -r _; exit 1
fi
xattr -dr com.apple.quarantine "$APP" 2>/dev/null
echo "Unblocked. Starting the engine..."
open "$APP"

# Wait and confirm, rather than exiting into a race with the detached launch.
# Reporting success without checking is how a silent failure reaches the user.
n=0
while [ $n -lt 20 ]; do
  if pgrep -f "engine.command" >/dev/null 2>&1; then
    echo ""
    echo "Running. Your browser should be showing the engine now."
    echo "If it is not, the address is in /tmp/cts-moa-engine.log"
    echo ""
    echo "You only needed this once. From now on just double-click"
    echo "\"CTS MOA Engine\" itself."
    printf "Press Return to close this window. "; read -r _
    exit 0
  fi
  n=$((n + 1)); sleep 1
done
echo ""
echo "It did not start. The last lines of the log were:"
echo ""
tail -n 12 /tmp/cts-moa-engine.log 2>/dev/null || echo "(no log was written)"
printf "\nPress Return to close this window. "; read -r _
exit 1
"""


APPLESCRIPT = """on run
	try
		set selfPath to POSIX path of (path to me)
		set engine to selfPath & "Contents/Resources/engine.command"
		-- The .app sits in the folder the user double-clicked in; output belongs
		-- there, not inside the bundle. "path to me" ends with a slash, so two
		-- levels up is the containing folder.
		set base to do shell script "dirname " & quoted form of (selfPath)
		do shell script "chmod +x " & quoted form of engine
		-- Log rather than discard. If the browser fails to open, gui.log is the
		-- only way the user can find the URL, and the old .app wrote one.
		set logFile to "/tmp/cts-moa-engine.log"
		-- PYTHONUNBUFFERED is the equivalent of python3 -u here: the shell header
		-- execs python itself, so the flag cannot be passed on the command line.
		-- Without it the log stays empty and a user whose browser did not open has
		-- no way to find the URL.
		do shell script "PYTHONUNBUFFERED=1 MOA_APPDIR_BASE=" & quoted form of base & ¬
			" nohup " & quoted form of engine & " > " & ¬
			quoted form of logFile & " 2>&1 &"
		-- A silent failure is the worst outcome: the user double-clicks, nothing
		-- happens, and there is no terminal to show them why. So check whether the
		-- process is still alive and surface the log if it is not.
		delay 5
		set alive to (do shell script ¬
			"pgrep -f engine.command >/dev/null 2>&1 && echo yes || echo no")
		if alive is "no" then
			set tailOut to (do shell script "tail -n 12 " & quoted form of logFile & ¬
				" 2>/dev/null || echo '(no log)'")
			display dialog "The CTS MOA engine stopped before it could start." & ¬
				return & return & tailOut buttons {"OK"} default button 1 with icon stop
		end if
	on error errText
		display dialog "Could not start the CTS MOA engine." & return & return & ¬
			errText buttons {"OK"} default button 1 with icon stop
	end try
end run
"""


def _build_mac_app(command_path):
    """Wrap the single file in a .app so double-clicking opens no terminal.

    A .command is opened BY Terminal.app, so a window always appears; the
    extension is what causes it, not anything Python does. An .app launches the
    same script detached with nohup and shows nothing but the browser.

    The payload lives in Contents/Resources, so the .app is self-contained and
    Finder still shows it as a single item.
    """
    app = os.path.join(DIST, "CTS MOA Engine.app")
    if os.path.isdir(app):
        shutil.rmtree(app)
    scpt = os.path.join(DIST, "_launcher.applescript")
    with open(scpt, "w", encoding="utf-8") as fh:
        fh.write(APPLESCRIPT)
    rc = subprocess.call(["osacompile", "-o", app, scpt])
    os.remove(scpt)
    if rc != 0:
        print("  osacompile failed; the .command is still usable")
        return None
    res = os.path.join(app, "Contents", "Resources")
    dest = os.path.join(res, "engine.command")
    shutil.copy2(command_path, dest)
    os.chmod(dest, os.stat(dest).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    # RE-SIGN. osacompile signs the bundle, and adding the payload afterwards
    # breaks that seal: codesign then reports "a sealed resource is missing or
    # invalid" and macOS refuses to open the app at all, which is not the same
    # as the ordinary unsigned-developer warning and cannot be clicked past.
    # Ad-hoc (-s -) is all that is available without a Developer ID, and it is
    # enough to make the bundle internally consistent.
    rc = subprocess.call(["codesign", "--force", "--deep", "--sign", "-", app],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if rc != 0:
        print("  WARNING: could not re-sign the app; macOS may refuse to open it")
    else:
        chk = subprocess.run(["codesign", "--verify", "--deep", "--strict", app],
                             capture_output=True, text=True)
        if chk.returncode != 0:
            print(f"  WARNING: signature still invalid: {chk.stderr.strip()[:70]}")
        else:
            print("  signature verifies after adding the payload")
    print(f"  {_kb(app)}  {app}  (no terminal window)")
    return app


def _package(app, command_path, bat_path):
    """One sendable file per platform.

    A .app is a folder, so it cannot be emailed as-is; zipping it is the only
    way to hand over one item while keeping the no-terminal launch. The loose
    .command is folded in as a fallback for a Mac where Gatekeeper blocks the
    unsigned app, and then removed from the folder so there is nothing to
    choose between.
    """
    mac_zip = os.path.join(DIST, "CTS MOA Engine (Mac).zip")
    if os.path.exists(mac_zip):
        os.remove(mac_zip)
    if app:
        # ditto, NOT zipfile. An .app is signed, and Python's zip writer does
        # not carry the extended attributes and resource forks the signature
        # depends on, so the unzipped copy fails validation and macOS reports
        # it as damaged and refusing to open. ditto is Apple's own tool for
        # exactly this and is what `Compress` in Finder uses underneath.
        staging = os.path.join(DIST, "_mac")
        if os.path.isdir(staging):
            shutil.rmtree(staging)
        helper_dir = os.path.join(staging, "If the app will not open")
        os.makedirs(helper_dir)
        # macOS attaches a quarantine flag to anything that arrives by mail or
        # Drive, and refuses to launch an app that is not notarized -- silently,
        # with no dialog to click past. Notarizing needs a paid Developer ID.
        # Stripping the flag is the reliable fix, so it ships as something to
        # double-click rather than a command to retype.
        fixer = os.path.join(helper_dir, "Open the engine.command")
        with open(fixer, "w", encoding="utf-8") as fh:
            fh.write(MAC_UNQUARANTINE)
        os.chmod(fixer, os.stat(fixer).st_mode | stat.S_IXUSR | stat.S_IXGRP
                 | stat.S_IXOTH)
        subprocess.check_call(["ditto", app, os.path.join(
            staging, os.path.basename(app))])
        shutil.copy2(command_path, os.path.join(
            helper_dir, "Run in a terminal instead.command"))
        # No --keepParent: it would wrap everything in the staging folder's
        # name, so unzipping produced "_mac/CTS MOA Engine.app".
        rc = subprocess.call(["ditto", "-c", "-k", "--sequesterRsrc",
                              staging, mac_zip])
        shutil.rmtree(staging)
        if rc != 0:
            print("  ditto failed; the .app is still in SEND THIS")
            return
        shutil.rmtree(app)
        os.remove(command_path)
        print(f"  {_kb_file(mac_zip)}  {mac_zip}")
    win = os.path.join(DIST, "CTS MOA Engine (Windows).bat")
    if os.path.exists(win):
        os.remove(win)
    os.rename(bat_path, win)
    print(f"  {_kb_file(win)}  {win}")


def _kb_file(path):
    return f"{os.path.getsize(path) // 1024:>6} KB"


def _kb(path):
    total = 0
    for root, _d, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return f"{total // 1024:>6} KB"


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
    app = _build_mac_app(mac)
    win = os.path.join(DIST, "CTS MOA Engine.bat")
    with open(win, "wb") as fh:
        fh.write(WIN_HEADER.encode("utf-8"))          # CRLF, written literally
        fh.write((WIN_NOTE + body).encode("utf-8"))
    out.append(win)

    print(f"\nbundled {len(payload['modules'])} modules, "
          f"{len(payload['data'])} data file(s)")
    print("\nSEND THIS - one file per platform:")
    _package(app, mac, win)
    return out


if __name__ == "__main__":
    build()
