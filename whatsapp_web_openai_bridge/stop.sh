#!/usr/bin/env bash
set -euo pipefail

USER_ID="$(id -u)"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
BRIDGE_LABEL="com.zhangwei.whatsapp-openai-bridge"
WATCHDOG_LABEL="com.zhangwei.whatsapp-openai-bridge-watchdog"
BRIDGE_PLIST="$LAUNCH_AGENTS_DIR/$BRIDGE_LABEL.plist"
WATCHDOG_PLIST="$LAUNCH_AGENTS_DIR/$WATCHDOG_LABEL.plist"
STATUS_URL="http://127.0.0.1:8787/status"

bootout_if_loaded() {
  local label="$1"
  local plist="$2"

  if launchctl print "gui/$USER_ID/$label" >/dev/null 2>&1; then
    launchctl bootout "gui/$USER_ID" "$plist"
  fi
}

# Stop watchdog first so it does not immediately restart the bridge.
bootout_if_loaded "$WATCHDOG_LABEL" "$WATCHDOG_PLIST"
bootout_if_loaded "$BRIDGE_LABEL" "$BRIDGE_PLIST"

echo "Stopped $WATCHDOG_LABEL and $BRIDGE_LABEL."

if curl -fsS --max-time 2 "$STATUS_URL" >/dev/null 2>&1; then
  echo "Warning: status endpoint is still reachable: $STATUS_URL" >&2
  exit 1
fi

echo "Status endpoint is down."
