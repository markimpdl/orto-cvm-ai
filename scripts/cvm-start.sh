#!/usr/bin/env bash
# Starts the CVM API, worker and UI, each in its own Terminal window (they belong to Terminal, so they keep running
# when Claude Code or this window closes; close a window to stop that service, or use scripts/cvm-stop.sh).
# A service that is already running is left alone: two workers would run the same jobs twice.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"

start() { # name pattern dir command
  local name=$1 pattern=$2 dir=$3 cmd=$4
  if pgrep -f "$pattern" >/dev/null; then
    echo "✓ $name já estava ligado"
    return
  fi
  osascript - "$name" "$dir" "$cmd" <<'OSA' >/dev/null
on run argv
  set {nm, d, c} to argv
  tell application "Terminal"
    set w to do script "printf '\\e]0;CVM " & nm & "\\a'; cd " & quoted form of d & " && " & c
    set custom title of w to "CVM " & nm
  end tell
end run
OSA
  echo "▶ $name ligado (janela \"CVM $name\" no Terminal)"
}

start api "bin/cvm-api" "$ROOT/backend" "uv run cvm-api"
# caffeinate -i keeps the Mac awake while the worker runs (scheduled publishing, long renders)
start worker "bin/cvm-worker" "$ROOT/backend" "caffeinate -i uv run cvm-worker"
start interface "next dev|next start" "$ROOT/frontend" "npm run dev"

for _ in $(seq 1 60); do  # wait for the UI before opening it
  curl -s -o /dev/null http://localhost:3010 && break
  sleep 1
done
open http://localhost:3010
echo "CVM: http://localhost:3010"
