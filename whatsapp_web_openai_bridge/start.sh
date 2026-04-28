#!/usr/bin/env bash
set -euo pipefail

USER_ID="$(id -u)"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
BRIDGE_LABEL="com.zhangwei.whatsapp-openai-bridge"
WATCHDOG_LABEL="com.zhangwei.whatsapp-openai-bridge-watchdog"
BRIDGE_PLIST="$LAUNCH_AGENTS_DIR/$BRIDGE_LABEL.plist"
WATCHDOG_PLIST="$LAUNCH_AGENTS_DIR/$WATCHDOG_LABEL.plist"
STATUS_URL="http://127.0.0.1:8787/status"
DASHBOARD_URL="http://127.0.0.1:8787/"

if [[ ! -f "$BRIDGE_PLIST" ]]; then
  echo "Missing plist: $BRIDGE_PLIST" >&2
  exit 1
fi

if [[ ! -f "$WATCHDOG_PLIST" ]]; then
  echo "Missing plist: $WATCHDOG_PLIST" >&2
  exit 1
fi

bootstrap_if_needed() {
  local label="$1"
  local plist="$2"

  if launchctl print "gui/$USER_ID/$label" >/dev/null 2>&1; then
    return
  fi

  launchctl bootstrap "gui/$USER_ID" "$plist"
}

bootstrap_if_needed "$BRIDGE_LABEL" "$BRIDGE_PLIST"
bootstrap_if_needed "$WATCHDOG_LABEL" "$WATCHDOG_PLIST"

launchctl kickstart -k "gui/$USER_ID/$BRIDGE_LABEL"
launchctl kickstart -k "gui/$USER_ID/$WATCHDOG_LABEL"

echo "Started $BRIDGE_LABEL and $WATCHDOG_LABEL."
echo "Dashboard: $DASHBOARD_URL"
echo "Tasks API: http://127.0.0.1:8787/tasks"

for _ in {1..12}; do
  if curl -fsS --max-time 2 "$STATUS_URL" >/tmp/whatsapp-bridge-status.json 2>/dev/null; then
    echo "Status:"
    cat /tmp/whatsapp-bridge-status.json
    echo
    echo "View current interaction tasks here: $DASHBOARD_URL"
    exit 0
  fi
  sleep 2
done

echo "Started, but status endpoint is not ready yet: $STATUS_URL"
echo "When ready, view current interaction tasks here: $DASHBOARD_URL"
