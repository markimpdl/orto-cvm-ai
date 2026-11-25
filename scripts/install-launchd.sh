#!/usr/bin/env bash
# Keeps the CVM API, worker and UI running in the background (start at login, restart on crash).
# Uninstall: scripts/install-launchd.sh uninstall
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AGENTS="$HOME/Library/LaunchAgents"
LOGS="$ROOT/data/logs"
mkdir -p "$AGENTS" "$LOGS"
NAMES=(com.cvm.api com.cvm.worker com.cvm.ui)

if [ "${1:-}" = "uninstall" ]; then
  for n in "${NAMES[@]}"; do launchctl bootout "gui/$(id -u)/$n" 2>/dev/null || true; rm -f "$AGENTS/$n.plist"; done
  echo "removido"; exit 0
fi

write() { # name workdir command...
  local name=$1 dir=$2; shift 2
  local args=""; for a in "$@"; do args+="<string>$a</string>"; done
  cat > "$AGENTS/$name.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$name</string>
  <key>WorkingDirectory</key><string>$dir</string>
  <key>ProgramArguments</key><array>$args</array>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$HOME/.local/bin</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$LOGS/$name.log</string>
  <key>StandardErrorPath</key><string>$LOGS/$name.log</string>
</dict></plist>
PLIST
  launchctl bootout "gui/$(id -u)/$name" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$AGENTS/$name.plist"
}

( cd "$ROOT/frontend" && npm run build >/dev/null )
write com.cvm.api "$ROOT/backend" "$ROOT/backend/.venv/bin/cvm-api"
# caffeinate -i keeps the Mac awake while the worker runs, so scheduled publishing isn't missed
write com.cvm.worker "$ROOT/backend" /usr/bin/caffeinate -i "$ROOT/backend/.venv/bin/cvm-worker"
write com.cvm.ui "$ROOT/frontend" "$(command -v npx)" next start -H 127.0.0.1 -p 3010
echo "CVM rodando: http://127.0.0.1:3010 (logs em data/logs/)"
