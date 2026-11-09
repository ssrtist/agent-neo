#!/usr/bin/env bash
# Agent Neo (agent-neo) desktop GUI launcher for macOS.
# Mirrors run-gui.cmd: runs neo_gui.py from the repo .venv.
# Options are passed through:
#   --workspace DIR
#   --backend copilot|gemini|whatsapp|desktop|auto|custom
#   --browser-mode offscreen|visible|headless
#   --foreground   (keep attached to current terminal instead of backgrounding)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
PY="$VENV_DIR/bin/python3"

# Auto-bootstrap .venv if missing
if [[ ! -x "$PY" ]]; then
  echo "[run-gui] Creating Python virtual environment in $VENV_DIR..."
  python3 -m venv "$VENV_DIR"
  "$PY" -m pip install --upgrade pip >/dev/null
  "$PY" -m pip install pytauri-wheel==0.8.0 playwright==1.64.1 PyYAML==6.0.3 requests==2.34.2
fi

# Auto-detect Microsoft Edge or Google Chrome on macOS if BROWSER_AUTOMATION_EDGE_PATH is unset
if [[ -z "${BROWSER_AUTOMATION_EDGE_PATH:-}" ]]; then
  if [[ -x "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge" ]]; then
    export BROWSER_AUTOMATION_EDGE_PATH="/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"
  elif [[ -x "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" ]]; then
    export BROWSER_AUTOMATION_EDGE_PATH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
  fi
fi

# Run directly in foreground for --help / -h / --version / --foreground
FOREGROUND=0
ARGS=()
for arg in "$@"; do
  case "$arg" in
    -h|--help|--version)
      exec "$PY" "$SCRIPT_DIR/neo_gui.py" "$@"
      ;;
    --foreground)
      FOREGROUND=1
      ;;
    *)
      ARGS+=("$arg")
      ;;
  esac
done

if [[ "$FOREGROUND" -eq 1 ]]; then
  exec "$PY" "$SCRIPT_DIR/neo_gui.py" ${ARGS[@]+"${ARGS[@]}"}
else
  nohup "$PY" "$SCRIPT_DIR/neo_gui.py" ${ARGS[@]+"${ARGS[@]}"} >/dev/null 2>&1 &
  disown
fi
