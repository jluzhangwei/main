#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${INSTALL_DIR:-$HOME/.local/bin}"
AUDIT_DIR="${SSH_PROXY_AUDIT_DIR:-$HOME/.ssh_proxy/audit}"
SHELL_RC=""
RUN_SMOKE=1
UPDATE_PATH=1

usage() {
  cat <<'EOF'
Usage: ./install.sh [options]

Install ssh-proxy commands for the current user.

Options:
  --install-dir DIR   Install command wrappers into DIR. Default: ~/.local/bin
  --audit-dir DIR     Default audit directory. Default: ~/.ssh_proxy/audit
  --no-smoke          Skip smoke test.
  --no-path           Do not update shell PATH file.
  -h, --help          Show this help.

Environment:
  INSTALL_DIR         Same as --install-dir
  SSH_PROXY_AUDIT_DIR Same as --audit-dir

Examples:
  ./install.sh
  ./install.sh --install-dir ~/bin --audit-dir ~/ssh-proxy-audit
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-dir)
      INSTALL_DIR="$2"
      shift 2
      ;;
    --audit-dir)
      AUDIT_DIR="$2"
      shift 2
      ;;
    --no-smoke)
      RUN_SMOKE=0
      shift
      ;;
    --no-path)
      UPDATE_PATH=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

INSTALL_DIR="${INSTALL_DIR/#\~/$HOME}"
AUDIT_DIR="${AUDIT_DIR/#\~/$HOME}"

need_file() {
  local path="$1"
  if [[ ! -e "$PROJECT_DIR/$path" ]]; then
    echo "Missing required file: $PROJECT_DIR/$path" >&2
    exit 1
  fi
}

need_file "ssh_proxy/cli.py"
need_file "ssh_proxy/audit_cli.py"
need_file "ssh_proxy/policy_cli.py"
need_file "ssh_proxy/bin/ssh-proxy"
need_file "ssh_proxy/bin/ssh-proxy-audit"
need_file "ssh_proxy/bin/ssh-proxy-policy"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required but not found in PATH." >&2
  exit 1
fi

mkdir -p "$INSTALL_DIR" "$AUDIT_DIR"

install_wrapper() {
  local name="$1"
  local module="$2"
  local target="$INSTALL_DIR/$name"
  # The checked-in wrappers are intentionally generic. Installed wrappers pin
  # PYTHONPATH to this checkout and set the default audit directory.
  cat > "$target" <<EOF
#!/usr/bin/env bash
export PYTHONPATH="$PROJECT_DIR:\${PYTHONPATH:-}"
export SSH_PROXY_AUDIT_DIR="\${SSH_PROXY_AUDIT_DIR:-$AUDIT_DIR}"
exec python3 -m "$module" "\$@"
EOF
  chmod +x "$target"
}

install_wrapper "ssh-proxy" "ssh_proxy.cli"
install_wrapper "ssh-proxy-audit" "ssh_proxy.audit_cli"
install_wrapper "ssh-proxy-policy" "ssh_proxy.policy_cli"

detect_shell_rc() {
  local shell_name
  shell_name="$(basename "${SHELL:-}")"
  case "$shell_name" in
    zsh) echo "$HOME/.zshrc" ;;
    bash) echo "$HOME/.bashrc" ;;
    *) echo "$HOME/.profile" ;;
  esac
}

SHELL_RC="$(detect_shell_rc)"
if [[ "$UPDATE_PATH" == "1" && ":$PATH:" != *":$INSTALL_DIR:"* ]]; then
  touch "$SHELL_RC"
  if ! grep -Fq "export PATH=\"$INSTALL_DIR:\$PATH\"" "$SHELL_RC"; then
    {
      echo ""
      echo "# ssh-proxy"
      echo "export PATH=\"$INSTALL_DIR:\$PATH\""
    } >> "$SHELL_RC"
  fi
fi

if [[ "$RUN_SMOKE" == "1" ]]; then
  "$INSTALL_DIR/ssh-proxy" policy check "show version" >/dev/null
  if "$INSTALL_DIR/ssh-proxy" policy check "reload" >/dev/null 2>&1; then
    echo "Smoke test failed: reload should be blocked." >&2
    exit 1
  fi
  SSH_PROXY_AUDIT_DIR="$AUDIT_DIR" "$INSTALL_DIR/ssh-proxy" -- /bin/echo ssh-proxy-smoke >/dev/null
  "$INSTALL_DIR/ssh-proxy" audit --audit-dir "$AUDIT_DIR" list >/dev/null
fi

cat <<EOF
ssh-proxy installed.

Commands:
  $INSTALL_DIR/ssh-proxy
  $INSTALL_DIR/ssh-proxy-audit   compatibility wrapper
  $INSTALL_DIR/ssh-proxy-policy  compatibility wrapper

Audit directory:
  $AUDIT_DIR

Shell PATH file:
  $([[ "$UPDATE_PATH" == "1" ]] && echo "$SHELL_RC" || echo "not modified")

Try:
  ssh-proxy -- <your-login-command>
  ssh-proxy policy list
  ssh-proxy audit list
EOF

if [[ "$UPDATE_PATH" == "1" ]]; then
  cat <<EOF
If 'ssh-proxy' is not found in this terminal, run:
  source "$SHELL_RC"
EOF
else
  cat <<EOF
PATH was not modified. Run commands by full path or add this directory to PATH:
  $INSTALL_DIR
EOF
fi
