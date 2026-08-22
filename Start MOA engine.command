#!/usr/bin/env bash
# Double-clickable launcher for the CTS MOA sourcing engine.
#
# macOS opens a .command file in Terminal on double-click, which is the only
# reliable way to hand a shell tool to someone who does not use a shell. All
# this does is start the local web app; the actual interface is the browser
# window that opens.
#
# There is nothing to sign in to and nothing to install beyond Python 3.

cd "$(dirname "$0")" || exit 1

BOLD=$'\033[1m'; DIM=$'\033[2m'; GREEN=$'\033[32m'; RED=$'\033[31m'; OFF=$'\033[0m'

printf '\n%sCTS MOA sourcing engine%s\n' "$BOLD" "$OFF"
printf '%s\n' "────────────────────────────────────────────────────────────"

# ---------------------------------------------------------------- preflight
PY=""
for candidate in python3 /usr/local/bin/python3 /opt/homebrew/bin/python3 /usr/bin/python3; do
  if command -v "$candidate" >/dev/null 2>&1; then PY="$candidate"; break; fi
done

if [ -z "$PY" ]; then
  printf '%s✗ Python 3 is not installed.%s\n\n' "$RED" "$OFF"
  printf '  Install it from https://www.python.org/downloads/\n'
  printf '  then double-click this file again.\n\n'
  printf 'Press Return to close. '
  read -r _
  exit 1
fi

printf '%s✓%s Python %s\n' "$GREEN" "$OFF" \
  "$("$PY" -c 'import sys;print(".".join(map(str,sys.version_info[:3])))')"

# ---------------------------------------------------------------- launch
printf '\n%sOpening the app in your web browser…%s\n\n' "$BOLD" "$OFF"
printf '%sKeep this window open while you work.%s\n' "$DIM" "$OFF"
printf '%sTo stop, close the browser tab and press Ctrl-C here.%s\n' "$DIM" "$OFF"

"$PY" gui.py

printf '\nPress Return to close this window. '
read -r _
