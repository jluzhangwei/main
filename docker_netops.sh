#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

ACTION="${1:-start}"

require_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    echo "[docker] 未找到 docker 命令，请先安装 Docker Desktop 或 Docker Engine。"
    exit 1
  fi
}

start_all() {
  docker compose up -d --build
  echo ""
  echo "NetOps Docker 已启动："
  echo "- Frontend: http://127.0.0.1:5173"
  echo "- Backend:  http://127.0.0.1:8000"
  echo "- Docs:     http://127.0.0.1:8000/docs"
}

stop_all() {
  docker compose down
}

reset_all() {
  docker compose down -v
}

status_all() {
  docker compose ps
}

logs_all() {
  docker compose logs -f
}

case "$ACTION" in
  start)
    require_docker
    start_all
    ;;
  stop)
    require_docker
    stop_all
    ;;
  restart)
    require_docker
    stop_all
    start_all
    ;;
  reset)
    require_docker
    reset_all
    ;;
  status)
    require_docker
    status_all
    ;;
  logs)
    require_docker
    logs_all
    ;;
  *)
    echo "用法: $0 {start|stop|restart|reset|status|logs}"
    exit 1
    ;;
esac
