#!/usr/bin/env bash
# deploy/run_forever.sh — Portable shell while-loop supervisor fallback (D-05)
#
# Use this script when launchd is not available (Linux, CI, quick-run) or as
# a simple test harness before setting up the launchd LaunchAgent.
#
# Prerequisites:
#   - A .env file in the project root with all required env vars (see .env.example)
#   - Python virtual environment activated, OR python resolves to the venv Python
#     (e.g. activate venv before running this script, or set full path in line below)
#
# Usage:
#   cd /path/to/ai-snp-trading-claude
#   chmod +x deploy/run_forever.sh
#   ./deploy/run_forever.sh
#
# The ThrottleInterval (30s below) should match service.launchd_throttle_interval_s
# in rules.json and the ThrottleInterval in com.bot.trading.plist (D-06).
#
# SECURITY: This script sources .env which may contain Telegram secrets.
# Run only from a trusted environment. Ensure .env is chmod 600.

set -euo pipefail

# Source environment variables from .env (D-13: secrets via env vars only)
# -a exports every variable defined; +a turns off auto-export after sourcing.
if [ -f .env ]; then
    set -a
    # shellcheck source=/dev/null
    source .env
    set +a
else
    echo "[WARN] .env not found; using existing environment variables" >&2
fi

# ThrottleInterval: seconds to wait between restarts (D-06 crash-loop guard)
# Change this to match service.launchd_throttle_interval_s in rules.json.
THROTTLE_INTERVAL=30

echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] Starting bot supervisor loop (ThrottleInterval=${THROTTLE_INTERVAL}s)..." >&2

while true; do
    python3 -m bot
    EXIT_CODE=$?
    echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] bot exited with code ${EXIT_CODE} — restarting in ${THROTTLE_INTERVAL}s..." >&2
    sleep "${THROTTLE_INTERVAL}"
done
