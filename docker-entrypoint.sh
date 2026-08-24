#!/bin/sh
set -eu

# Own the USB adb server. Do not fail if no phone is plugged in yet.
adb start-server || true

exec uvicorn app.main:app --host "${HOST:-0.0.0.0}" --port "${PORT:-8787}"
