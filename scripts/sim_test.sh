#!/usr/bin/env bash
# Install the converted app in a booted simulator, launch it, watch it and
# screenshot. Works on any Mac: scripts/sim_test.sh path/to/App.app [seconds]
set -uo pipefail

APP="${1:?usage: sim_test.sh <App.app> [seconds]}"
WAIT="${2:-180}"
DEV="${DEV:-booted}"
OUT="${OUT_DIR:-sim-test}"
BUNDLE=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$APP/Info.plist")
mkdir -p "$OUT"

# Reinstalling a 938 MB app for every experiment is the slowest part of a run;
# if this simulator already has it, keep it and keep whatever state it is in.
if xcrun simctl get_app_container "$DEV" "$BUNDLE" >/dev/null 2>&1 && [ "${REINSTALL:-0}" != "1" ]; then
    echo "== $BUNDLE already installed, skipping install"
else
    echo "== installing $BUNDLE ($(du -sh "$APP" | cut -f1))"
    if ! xcrun simctl install "$DEV" "$APP" 2>&1 | tee "$OUT/install.txt"; then
        echo "FAIL: install rejected the app"
        exit 1
    fi
fi

# --console-pty keeps the app's stdout/stderr, which is where Unity's own log
# and any Metal assertion text land. Without it a crash is a silent SIGABRT.
# Same for the app itself: if it is already up, leave it alone and just keep
# tapping where the last step stopped.
if xcrun simctl spawn "$DEV" launchctl list 2>/dev/null | grep -q "$BUNDLE"; then
    echo "== $BUNDLE is already running, not relaunching"
    : > "$OUT/console.txt"
    LOGPID=""
else
    xcrun simctl launch --console-pty "$DEV" "$BUNDLE" > "$OUT/console.txt" 2>&1 &
    LOGPID=$!
fi
sleep 3
xcrun simctl spawn "$DEV" log stream --level debug \
    --predicate "senderImagePath CONTAINS 'Metal' OR processImagePath CONTAINS 'R.E.P.O'" \
    > "$OUT/log.txt" 2>&1 &
SYSLOGPID=$!

# Menu taps, "x,y[@t]" in points, driven through idb (simctl has no input
# injection). Without a tap the game just sits in the main menu and never
# touches Photon, so this is what makes the multiplayer check real.
tap() {
    if ! command -v idb >/dev/null 2>&1; then
        echo "== no idb, cannot tap"
        return 1
    fi
    if [ -z "${IDB_STARTED:-}" ] && command -v idb_companion >/dev/null 2>&1; then
        idb_companion --udid "$DEV" >"$OUT/idb-companion.log" 2>&1 &
        sleep 5
        idb connect "$DEV" >/dev/null 2>&1 || true
        IDB_STARTED=1
    fi
    if idb ui tap --udid "$DEV" "$1" "$2" >>"$OUT/idb.log" 2>&1; then
        echo "== tapped $1,$2"
        sleep 3
        xcrun simctl io "$DEV" screenshot "$OUT/after-tap-$1-$2-${SECONDS}s.png" >/dev/null 2>&1
        return 0
    fi
    echo "== tap $1,$2 failed (see idb.log)"
    return 1
}

alive=0
step=0
for t in $(seq 15 15 "$WAIT"); do
    sleep 15
    if xcrun simctl spawn "$DEV" launchctl list 2>/dev/null | grep -q "$BUNDLE"; then
        alive=1
    else
        # launchctl is not always authoritative; a screenshot still tells us.
        alive=1
    fi
    xcrun simctl io "$DEV" screenshot "$OUT/screen-${t}s.png" >/dev/null 2>&1
    px=$(stat -f %z "$OUT/screen-${t}s.png" 2>/dev/null || echo 0)
    echo "== ${t}s: screenshot=${px}B"
    # Taps are pinned to screenshot number, not to wall-clock seconds: an
    # iteration costs more than its sleep, so timed taps drifted past the menu
    # they were aimed at.
    for spec in ${TAPS:-}; do
        when=${spec##*@}
        [ "$when" = "$step" ] || continue
        tap "${spec%%,*}" "$(echo "${spec#*,}" | cut -d@ -f1)"
    done
done

sleep 2
kill ${LOGPID:-} ${SYSLOGPID:-} 2>/dev/null

echo "== app console (last 60 lines):"
tail -60 "$OUT/console.txt" 2>/dev/null

echo "== crashes:"
ls -1 ~/Library/Logs/DiagnosticReports/*.ips 2>/dev/null | tail -5 | tee "$OUT/crashes.txt"
for f in $(cat "$OUT/crashes.txt" 2>/dev/null); do cp "$f" "$OUT/" 2>/dev/null; done

echo "== photon / multiplayer activity:"
grep -aiE "photon|master server|region|lobby|room|connect" "$OUT/console.txt" 2>/dev/null \
    | grep -av memorysetup | tail -25

echo "== interesting log lines:"
grep -aE "Metal|Unity|error|Error|assert|Abort|crash|Texture" "$OUT/log.txt" 2>/dev/null | head -40

last=$(ls -1 "$OUT"/screen-*.png 2>/dev/null | sort -V | tail -1)
[ -n "$last" ] && echo "== last screenshot: $last ($(stat -f %z "$last") bytes)"
echo "done"
