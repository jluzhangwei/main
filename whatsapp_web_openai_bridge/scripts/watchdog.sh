#!/usr/bin/env bash
set -euo pipefail

LABEL="com.zhangwei.whatsapp-openai-bridge"
ROOT_DIR="/Users/zhangwei/python/whatsapp_web_openai_bridge"
LOG_DIR="$ROOT_DIR/logs"
STATE_DIR="$ROOT_DIR/data/state"
STATUS_URL="http://127.0.0.1:8787/status"
SEND_URL="http://127.0.0.1:8787/send-test"
HEARTBEAT_INTERVAL_SECONDS="${HEARTBEAT_INTERVAL_SECONDS:-3600}"

mkdir -p "$LOG_DIR" "$STATE_DIR"

log() {
  printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG_DIR/watchdog.log"
}

restart_bridge() {
  log "restarting $LABEL: $*"
  /bin/launchctl kickstart -k "gui/$(id -u)/$LABEL" >> "$LOG_DIR/watchdog.log" 2>&1 || {
    /bin/launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/$LABEL.plist" >> "$LOG_DIR/watchdog.log" 2>&1 || true
  }
}

status_json=""
connection_state=""
for attempt in 1 2 3 4 5 6; do
  status_json="$(/usr/bin/curl -fsS --max-time 5 "$STATUS_URL" 2>> "$LOG_DIR/watchdog.log" || true)"
  if [[ -n "$status_json" ]]; then
    connection_state="$(printf '%s' "$status_json" | /Users/zhangwei/.local/bin/node -e 'let input = ""; process.stdin.on("data", (chunk) => input += chunk); process.stdin.on("end", () => { try { process.stdout.write(JSON.parse(input).connectionState || ""); } catch { process.stdout.write(""); } });' 2>/dev/null || true)"
    if [[ "$connection_state" == "open" ]]; then
      break
    fi
  fi
  sleep 5
done

if [[ -z "$status_json" ]]; then
  restart_bridge "status endpoint unavailable after retries"
  exit 0
fi

if [[ "$connection_state" != "open" ]]; then
  restart_bridge "connectionState=$connection_state"
  exit 0
fi

last_heartbeat_file="$STATE_DIR/last-heartbeat-at"
now="$(date +%s)"
last="0"
if [[ -f "$last_heartbeat_file" ]]; then
  last="$(tr -cd '0-9' < "$last_heartbeat_file" || true)"
  last="${last:-0}"
fi

if (( now - last >= HEARTBEAT_INTERVAL_SECONDS )); then
  message="[whatsapp bridge] alive $(date '+%Y-%m-%d %H:%M:%S %Z')"
  escaped_message="$(printf '%s' "$message" | /usr/bin/sed 's/\\/\\\\/g; s/"/\\"/g')"
  if /usr/bin/curl -fsS --max-time 15 \
    -H 'Content-Type: application/json' \
    -d "{\"text\":\"$escaped_message\"}" \
    "$SEND_URL" >> "$LOG_DIR/watchdog.log" 2>&1; then
    printf '%s' "$now" > "$last_heartbeat_file"
    log "heartbeat sent"
  else
    log "heartbeat send failed"
  fi
fi
