#!/bin/sh
set -eu

# Own the USB adb server. Do not fail if no phone is plugged in yet.
adb kill-server >/dev/null 2>&1 || true
adb start-server || true

# Dummy X display: --no-window disables scrcpy control, which drops -S/-w.
export DISPLAY="${DISPLAY:-:99}"
export SDL_VIDEODRIVER="${SDL_VIDEODRIVER:-x11}"
export SDL_AUDIODRIVER="${SDL_AUDIODRIVER:-dummy}"
export SCRCPY_PID_FILE="${SCRCPY_PID_FILE:-/tmp/scrcpy.pid}"
export ADB="${ADB:-$(command -v adb)}"

Xvfb "$DISPLAY" -ac -screen 0 640x480x24 -nolisten tcp >/dev/null 2>&1 &
sleep 1

# Keep the panel off while USB-awake. Restart on unplug or SIGTERM from dim_display.
(
  while true; do
    echo "scrcpy: starting (--turn-screen-off --stay-awake)"
    scrcpy --no-video --no-audio --turn-screen-off --stay-awake &
    echo $! > "$SCRCPY_PID_FILE"
    wait "$(cat "$SCRCPY_PID_FILE")" || true
    rm -f "$SCRCPY_PID_FILE"
    echo "scrcpy: exited; retry in 2s"
    sleep 2
  done
) &

exec uvicorn app.main:app --host "${HOST:-0.0.0.0}" --port "${PORT:-8787}"
