# WhatsApp AI Bridge Console

A local WhatsApp Web bridge that turns phone messages into a controllable AI workflow, with a browser console for observability, model setup, group roles, and runtime management.

## Features

- WhatsApp Web login with QR code via Baileys.
- Local auth/session persistence.
- Local dashboard at `http://127.0.0.1:8787/`.
- Setup page at `http://127.0.0.1:8787/setup`.
- Backend modes:
  - `codex`: local Codex CLI.
  - `openai`: OpenAI SDK, including OpenAI-compatible `OPENAI_BASE_URL`.
- Recent interaction monitor, keeping the latest 10 interactions.
- Group whitelist with trigger-based replies.
- Group `answer-only` mode so group chats cannot trigger local operations.
- Per-chat local history storage.
- Start/stop helper scripts.
- macOS `launchctl` startup/watchdog helper scripts.

## Install

```bash
cd whatsapp_web_openai_bridge
cp .env.example .env
npm install
```

## Run

Foreground:

```bash
npm start
```

LaunchAgent-managed:

```bash
./start.sh
./stop.sh
```

On first login, scan the QR code from WhatsApp:

```text
WhatsApp -> Settings -> Linked Devices -> Link a device
```

## Local UI

- Dashboard: `http://127.0.0.1:8787/`
- Model setup: `http://127.0.0.1:8787/setup`
- Status JSON: `http://127.0.0.1:8787/status`
- Recent tasks JSON: `http://127.0.0.1:8787/tasks`
- Group role/config JSON: `http://127.0.0.1:8787/config`
- Group list JSON: `http://127.0.0.1:8787/groups`

## Environment

```env
LLM_BACKEND=codex
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5.2
OPENAI_BASE_URL=
CODEX_CLI_PATH=codex
CODEX_MODEL=
CODEX_WORKDIR=/Users/yourname/project
CODEX_EXEC_ENV=host
CODEX_REASONING_EFFORT=medium
CODEX_TIMEOUT_MS=120000
PORT=8787
AUTH_DIR=./data/auth
STATE_DIR=./data/state
SYSTEM_PROMPT=You are a concise and helpful assistant replying over WhatsApp.
MESSAGE_HISTORY_LIMIT=20
LOG_LEVEL=info
ALLOWED_GROUP_JIDS=
GROUP_TRIGGER_PATTERN=^/ai\s+
GROUP_SYSTEM_PROMPT=You are a concise group assistant. Only reply when explicitly triggered. In group chats, answer only; do not perform local operations.
GROUP_AI_NAME=Bridge Assistant
```

## Safety Notes

- `.env`, `data/`, `logs/`, and `node_modules/` are intentionally ignored.
- WhatsApp auth data stays local under `data/auth`.
- Group chats only respond when the group JID is allowlisted and the trigger pattern matches.
- Group messages run in answer-only mode and do not trigger local file, command, ticket, deployment, or account operations.
