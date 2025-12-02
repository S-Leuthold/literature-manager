#!/bin/bash
# Literature Manager Watch Daemon
# Wrapper script for launchd that handles startup delays and retries

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="/Users/samleuthold/.pyenv/versions/3.9.19/bin/python3"
LOG_DIR="$SCRIPT_DIR/logs"
MAX_RETRIES=5
RETRY_DELAY=10

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

# Ensure log directory exists
mkdir -p "$LOG_DIR"

# Wait for system to stabilize after boot
# Check if we're within 2 minutes of boot time
UPTIME_SECONDS=$(sysctl -n kern.boottime | awk '{print $4}' | tr -d ',')
BOOT_TIME=$UPTIME_SECONDS
CURRENT_TIME=$(date +%s)
SECONDS_SINCE_BOOT=$((CURRENT_TIME - BOOT_TIME))

if [ "$SECONDS_SINCE_BOOT" -lt 120 ]; then
    WAIT_TIME=$((120 - SECONDS_SINCE_BOOT))
    log "System recently booted. Waiting ${WAIT_TIME}s for stability..."
    sleep "$WAIT_TIME"
fi

# Wait for network (iCloud needs this)
log "Checking network connectivity..."
for i in $(seq 1 30); do
    if ping -c 1 -W 1 8.8.8.8 >/dev/null 2>&1; then
        log "Network is available"
        break
    fi
    if [ "$i" -eq 30 ]; then
        log "WARNING: Network not available after 30s, proceeding anyway"
    fi
    sleep 1
done

# Ensure files are downloaded from iCloud
log "Ensuring files are downloaded from iCloud..."
brctl download "$SCRIPT_DIR" 2>/dev/null || true
sleep 2

# Verify Python files are accessible
log "Verifying Python files..."
if ! "$PYTHON" -c "import sys; sys.path.insert(0, '$SCRIPT_DIR/src'); import literature_manager.cli" 2>/dev/null; then
    log "ERROR: Cannot import literature_manager module. Waiting and retrying..."
    sleep 10
    brctl download "$SCRIPT_DIR" 2>/dev/null || true
    sleep 5
fi

# Clear stale PID file if exists
PID_FILE="$LOG_DIR/watch.pid"
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE" 2>/dev/null || echo "")
    if [ -n "$OLD_PID" ] && ! kill -0 "$OLD_PID" 2>/dev/null; then
        log "Removing stale PID file"
        rm -f "$PID_FILE"
    fi
fi

# Launch the actual watcher with retries
log "Starting literature-manager watch..."
cd "$SCRIPT_DIR"

export PYTHONPATH="$SCRIPT_DIR/src"
export PYENV_VERSION="3.9.19"

RETRY_COUNT=0
while [ "$RETRY_COUNT" -lt "$MAX_RETRIES" ]; do
    "$PYTHON" -m literature_manager.cli watch --verbose
    EXIT_CODE=$?

    if [ "$EXIT_CODE" -eq 0 ]; then
        log "Watch exited cleanly"
        exit 0
    fi

    RETRY_COUNT=$((RETRY_COUNT + 1))
    log "Watch exited with code $EXIT_CODE. Retry $RETRY_COUNT/$MAX_RETRIES in ${RETRY_DELAY}s..."
    sleep "$RETRY_DELAY"
done

log "ERROR: Max retries ($MAX_RETRIES) exceeded. Giving up."
exit 1
