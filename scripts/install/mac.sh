#!/bin/bash
# Mac installer behind INSTALAR.command: checks the hardware before touching anything, installs what is missing
# (Homebrew packages, Python/Node dependencies), then starts API + worker + UI for THIS folder and opens /instalar.
# Safe to run again: once the install is done it only starts the services and opens the app.
#
# Env (tests / a second copy on the same Mac): CVM_DATA_DIR, CVM_API_PORT (8790), CVM_UI_PORT (3010),
# CVM_NO_TERMINAL=1 (services in the background, logs in <data>/logs, pids in <data>/logs/installer.pids),
# CVM_NO_OPEN=1 (no browser), CVM_DRY=1 (only say what would happen), CVM_BREW (path to brew),
# CVM_FAKE_ARCH / CVM_FAKE_RAM_GB (hardware check tests).

# Minimum Mac for the base install. Mirrors cvm/setup/requirements.py FEATURES["base"] (Mac row): Python may not
# exist yet, so the numbers are repeated here. Keep both in sync.
MIN_RAM_GB=32
MIN_DISK_GB=15

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"
BREW_INSTALL='/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'

env_get() { # KEY default: the environment first, then the .env at the root, then the default
  local key=$1 def=$2 val
  val="$(printenv "$key")"
  if [ -z "$val" ] && [ -f "$ROOT/.env" ]; then
    val="$(grep -E "^${key}=" "$ROOT/.env" | tail -1 | cut -d= -f2- | sed -e 's/^["'\'']//' -e 's/["'\'']$//')"
  fi
  echo "${val:-$def}"
}

DATA="$(env_get CVM_DATA_DIR "$ROOT/data")"
API_PORT="$(env_get CVM_API_PORT 8790)"
UI_PORT="${CVM_UI_PORT:-3010}"
DRY="${CVM_DRY:-}"

say() { echo "$*"; }
step() { echo; echo "== $* =="; }
fail() { echo; echo "✗ $*"; exit "${2:-1}"; }
doing() { # run a command, or only show it in a dry run
  if [ -n "$DRY" ]; then echo "  (simulação) $*"; return 0; fi
  echo "  → $*"
  "$@"
}

check_hw() {
  step "Conferindo o computador"
  local arch ram
  if [ -n "${CVM_FAKE_ARCH:-}" ]; then
    arch=$CVM_FAKE_ARCH
  elif [ "$(uname -s)" != "Darwin" ]; then
    fail "Este instalador é para Mac. No Windows, use INSTALAR.bat." 2
  elif [ "$(sysctl -n hw.optional.arm64 2>/dev/null)" = "1" ]; then
    arch=arm64  # also right under Rosetta, where uname -m says x86_64
  else
    arch="$(uname -m)"
  fi
  if [ "$arch" != "arm64" ]; then
    fail "O CVM precisa de um Mac com chip Apple (M1 ou mais novo). Este Mac tem processador Intel ($arch), então nada foi instalado." 2
  fi
  ram="${CVM_FAKE_RAM_GB:-$(( $(sysctl -n hw.memsize) / 1073741824 ))}"
  if [ "$ram" -lt "$MIN_RAM_GB" ]; then
    fail "O CVM precisa de pelo menos $MIN_RAM_GB GB de memória. Este Mac tem $ram GB, então nada foi instalado." 2
  fi
  say "✓ Mac com chip Apple, $ram GB de memória"
  local free_gb
  free_gb="$(df -g "$ROOT" 2>/dev/null | awk 'NR==2 {print $4}')"
  if [ -n "$free_gb" ] && [ "$free_gb" -lt "$MIN_DISK_GB" ]; then
    say "! Só $free_gb GB livres no disco; o básico usa uns $MIN_DISK_GB GB. Dá para seguir, mas libere espaço antes de baixar os modelos."
  fi
}

installed() { # 0 when the wizard already finished (data/setup.json), or for an install older than the wizard
  local f="$DATA/setup.json" db="$DATA/cvm.sqlite"
  if [ -f "$f" ]; then
    grep -Eq '"done"[[:space:]]*:[[:space:]]*true' "$f"
    return
  fi
  [ -f "$db" ] && command -v sqlite3 >/dev/null &&
    [ "$(sqlite3 -readonly "$db" 'select 1 from channel limit 1' 2>/dev/null)" = "1" ]
}

BREW=""
ensure_brew() {
  step "Homebrew"
  if [ -n "${CVM_BREW:-}" ]; then
    [ -x "$CVM_BREW" ] && BREW=$CVM_BREW
  else
    for b in /opt/homebrew/bin/brew /usr/local/bin/brew "$(command -v brew)"; do
      [ -n "$b" ] && [ -x "$b" ] && { BREW=$b; break; }
    done
  fi
  if [ -z "$BREW" ]; then
    echo
    echo "✗ Falta o Homebrew, o instalador de programas do Mac. Ele pede a sua senha, por isso você instala ele."
    echo "  Cole esta linha no Terminal, aperte Enter e siga o que aparecer:"
    echo
    echo "    $BREW_INSTALL"
    echo
    echo "  Depois, dê dois cliques em INSTALAR.command de novo."
    exit 1
  fi
  say "✓ Homebrew ($BREW)"
}

ensure_deps() {
  step "Programas"
  if command -v uv >/dev/null; then say "✓ uv"; else doing "$BREW" install uv || fail "Não deu para instalar o uv."; fi
  local node_major=0
  command -v node >/dev/null && node_major="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)"
  if [ "$node_major" -ge 18 ]; then say "✓ node $(node -v)"
  else doing "$BREW" install node || fail "Não deu para instalar o Node."; fi
  local prefix
  prefix="$("$BREW" --prefix 2>/dev/null || echo /opt/homebrew)"
  if [ -x "$prefix/opt/ffmpeg-full/bin/ffmpeg" ]; then say "✓ ffmpeg-full"
  else doing "$BREW" install ffmpeg-full || fail "Não deu para instalar o ffmpeg-full."; fi
  if command -v pdftoppm >/dev/null; then say "✓ poppler"
  else doing "$BREW" install poppler || fail "Não deu para instalar o poppler."; fi
}

ensure_env() { # a first .env from .env.example (it sets the video languages offered), else an empty one; never overwritten
  [ -f "$ROOT/.env" ] && return
  if [ -f "$ROOT/.env.example" ]; then
    if [ -n "$DRY" ]; then echo "  (simulação) criar .env a partir do .env.example"; else cp "$ROOT/.env.example" "$ROOT/.env"; fi
  elif [ -n "$DRY" ]; then echo "  (simulação) criar .env vazio"
  else printf '# Configuração do CVM. Tudo é opcional; as chaves são pedidas no assistente.\n' >"$ROOT/.env"; fi
}

sync() {
  step "Bibliotecas do CVM (pode levar alguns minutos na primeira vez)"
  (cd "$ROOT/backend" && doing uv sync) || fail "O uv sync falhou. Veja a mensagem acima."
  (cd "$ROOT/backend" && doing uv run playwright install chromium) || fail "Não deu para instalar o navegador do Playwright."
  (cd "$ROOT/frontend" && doing npm install --no-audit --no-fund) || fail "O npm install falhou. Veja a mensagem acima."
  ensure_env
  if [ ! -f "$DATA/setup.json" ]; then
    if [ -n "$DRY" ]; then echo "  (simulação) marcar $DATA/setup.json para o assistente"
    else mkdir -p "$DATA" && printf '{"stage": "wizard", "done": false}\n' >"$DATA/setup.json"; fi
  fi
}

# --- services of THIS folder only (another copy of the CVM on the same Mac is never touched) ---
API_PAT="$ROOT/backend/.venv/bin/cvm-api"
WORKER_PAT="$ROOT/backend/.venv/bin/cvm-worker"
UI_PAT="$ROOT/frontend/node_modules/.bin/next"

pids_of() { # pids of the processes running this program: the command line ends with the path (api, worker; also
  # "caffeinate -i <worker>") or runs "<path> dev" (next). Whole paths, so another copy of the CVM on this Mac never
  # counts; folder names with spaces are fine. (the path goes in through the environment: as an argument, awk's own
  # command line would match)
  ps -axo pid=,command= | CVM_PAT="$1" awk '
    BEGIN { p = ENVIRON["CVM_PAT"] }
    { pid = $1; c = $0; sub(/^[ \t]*[0-9]+[ \t]+/, "", c); n = length(c) - length(p) + 1
      if ((n >= 1 && substr(c, n) == p) || index(c, p " dev ")) print pid }'
}
ours() { [ -n "$(pids_of "$1")" ]; }
port_busy() { lsof -nP -tiTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }

service_env() { # the env each service needs, as shell words
  local e="CVM_API_PORT=$(printf %q "$API_PORT") NEXT_PUBLIC_CVM_API_PORT=$(printf %q "$API_PORT")"
  [ -n "${CVM_DATA_DIR:-}" ] && e="$e CVM_DATA_DIR=$(printf %q "$CVM_DATA_DIR")"
  echo "$e"
}

launch() { # name dir command...
  local name=$1 dir=$2; shift 2
  if [ -n "${CVM_NO_TERMINAL:-}" ]; then
    mkdir -p "$DATA/logs"
    local log="$DATA/logs/$name.log"
    [ "$name" = worker ] && log="$DATA/logs/worker-console.log"  # the worker writes worker.log itself
    (cd "$dir" && eval "export $(service_env)" && exec nohup "$@" >>"$log" 2>&1 </dev/null) &
    local pid=$!
    echo "$name $pid" >>"$DATA/logs/installer.pids"
    if [ "$name" = worker ]; then caffeinate -i -w "$pid" >/dev/null 2>&1 & fi
    say "  ▶ $name ligado em segundo plano (pid $pid, log em $log)"
    return
  fi
  local cmd="" w
  for w in "$@"; do cmd="$cmd $(printf %q "$w")"; done
  [ "$name" = worker ] && cmd="caffeinate -i$cmd"
  osascript - "$name" "$dir" "export $(service_env); exec$cmd" <<'OSA' >/dev/null
on run argv
  set {nm, d, c} to argv
  tell application "Terminal"
    set w to do script "printf '\\e]0;CVM " & nm & "\\a'; cd " & quoted form of d & " && " & c
    set custom title of w to "CVM " & nm
  end tell
end run
OSA
  say "  ▶ $name ligado (janela \"CVM $name\" no Terminal)"
}

start_one() { # name pattern port dir command...
  local name=$1 pat=$2 port=$3 dir=$4; shift 4
  if [ -n "$port" ]; then say "▶ subindo $name (porta $port)"; else say "▶ subindo $name"; fi
  if ours "$pat"; then say "  ✓ $name já estava ligado"; return; fi
  if [ -n "$port" ] && port_busy "$port"; then
    local msg="A porta $port já está em uso por outro programa. Feche esse programa ou escolha outra porta (CVM_API_PORT / CVM_UI_PORT)."
    if [ -n "$DRY" ]; then say "  (simulação) $msg"; return; fi
    fail "$msg"
  fi
  [ -n "$DRY" ] && return
  launch "$name" "$dir" "$@"
}

start_and_open() {
  local page=$1
  step "Ligando o CVM"
  start_one api "$API_PAT" "$API_PORT" "$ROOT/backend" "$API_PAT"
  start_one worker "$WORKER_PAT" "" "$ROOT/backend" "$WORKER_PAT"
  start_one interface "$UI_PAT" "$UI_PORT" "$ROOT/frontend" "$UI_PAT" dev --turbopack -H 127.0.0.1 -p "$UI_PORT"
  local url="http://localhost:$UI_PORT$page"
  if [ -n "$DRY" ]; then say "(simulação) abrir $url"; return; fi
  say "Esperando a interface responder (na primeira vez demora um pouco)..."
  local ok=""
  for _ in $(seq 1 180); do
    if curl -s -o /dev/null --max-time 2 "http://127.0.0.1:$API_PORT/health" &&
       curl -s -o /dev/null --max-time 60 "$url"; then ok=1; break; fi
    sleep 1
  done
  [ -n "$ok" ] || fail "A interface não respondeu em 3 minutos. Veja as janelas \"CVM api\" e \"CVM interface\" (ou $DATA/logs)."
  [ -z "${CVM_NO_OPEN:-}" ] && open "$url"
  say "✓ CVM: $url"
}

stop_ours() { # stops THIS folder's API, worker and UI (the "Atualizar" button: cvm/setup/update.py waits for the
  # queue to reach a step boundary before calling `mac.sh restart`)
  step "Desligando o CVM desta pasta"
  local pat pid port cwd
  for pat in "$API_PAT" "$WORKER_PAT" "$UI_PAT"; do
    for pid in $(pids_of "$pat"); do doing kill "$pid"; done
  done
  [ -n "$DRY" ] && return
  for _ in $(seq 1 20); do
    ours "$API_PAT" || ours "$WORKER_PAT" || ours "$UI_PAT" || break
    sleep 1
  done
  for pat in "$API_PAT" "$WORKER_PAT" "$UI_PAT"; do
    for pid in $(pids_of "$pat"); do kill -9 "$pid" 2>/dev/null; done
  done
  # the UI's next-server child keeps the port: kill a listener only when it runs from this folder
  for port in "$API_PORT" "$UI_PORT"; do
    for pid in $(lsof -nP -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null); do
      cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')"
      case "$cwd" in "$ROOT"|"$ROOT"/*) kill "$pid" 2>/dev/null; sleep 1; kill -9 "$pid" 2>/dev/null ;; esac
    done
  done
  say "✓ desligado"
}

main() {
  if [ "${1:-}" = restart ]; then  # used by "Atualizar": no hardware check, no browser
    CVM_NO_OPEN=1
    stop_ours
    start_and_open "/"
    return
  fi
  check_hw
  if installed; then
    say "✓ CVM já instalado: só falta ligar."
    start_and_open "/"
    return
  fi
  ensure_brew
  ensure_deps
  sync
  start_and_open "/instalar"
  say "Siga o assistente no navegador para terminar a instalação."
}

main "$@"
