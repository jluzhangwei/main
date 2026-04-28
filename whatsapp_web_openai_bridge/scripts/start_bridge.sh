#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="/Users/zhangwei/python/whatsapp_web_openai_bridge"
cd "$ROOT_DIR"

export PATH="/Users/zhangwei/.local/bin:/Users/zhangwei/.vscode/extensions/openai.chatgpt-26.422.30944-darwin-x64/bin/macos-x86_64:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

exec /Users/zhangwei/.local/bin/node src/index.mjs
