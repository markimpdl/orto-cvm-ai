#!/usr/bin/env bash
# Tests for INSTALAR.command / scripts/install/mac.sh. Everything runs with CVM_DRY=1 and throwaway data folders:
# nothing is installed, started or opened.   bash scripts/install/test_mac.sh
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
INSTALL="$ROOT/INSTALAR.command"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
FAILS=0
# free ports, so a dry run never sees the owner's services on 8790/3010
export CVM_API_PORT=18791 CVM_UI_PORT=13011 CVM_NO_OPEN=1 CVM_DRY=1

run() { # name, then env assignments; runs the installer, sets OUT and CODE
  local name=$1; shift
  OUT="$(env "$@" bash "$INSTALL" 2>&1)"; CODE=$?
  CUR=$name
}
ok() { echo "ok   $CUR: $1"; }
bad() { echo "FAIL $CUR: $1"; echo "$OUT" | sed 's/^/     | /'; FAILS=$((FAILS + 1)); }
expect_code() { [ "$CODE" = "$1" ] && ok "exit $1" || bad "exit $CODE, expected $1"; }
expect_has() { grep -qF -- "$1" <<<"$OUT" && ok "says \"$1\"" || bad "missing \"$1\""; }
expect_not() { grep -qE -- "$1" <<<"$OUT" && bad "should not match /$1/" || ok "no /$1/"; }

fresh() { local d="$TMP/$1"; mkdir -p "$d"; echo "$d"; }

test_intel_mac_refused() {
  run intel_mac CVM_DATA_DIR="$(fresh intel)" CVM_FAKE_ARCH=x86_64 CVM_FAKE_RAM_GB=64
  expect_code 2
  expect_has "precisa de um Mac com chip Apple"
  expect_not "brew|uv sync|npm"
}

test_low_ram_refused() {
  run low_ram CVM_DATA_DIR="$(fresh lowram)" CVM_FAKE_ARCH=arm64 CVM_FAKE_RAM_GB=16
  expect_code 2
  expect_has "32 GB"
  expect_has "16 GB"
  expect_not "brew|uv sync|npm"
}

test_installer_rerun_is_noop() {
  local d; d="$(fresh done)"; echo '{"done": true}' >"$d/setup.json"
  run rerun_noop CVM_DATA_DIR="$d" CVM_FAKE_ARCH=arm64 CVM_FAKE_RAM_GB=48
  expect_code 0
  expect_has "já instalado"
  expect_has "subindo"
  expect_not "brew|uv |npm|playwright|instalando"
}

test_legacy_db_counts_as_installed() {
  local d; d="$(fresh legacy)"
  sqlite3 "$d/cvm.sqlite" "create table channel (id integer primary key, name text); insert into channel (name) values ('x');"
  run legacy_db CVM_DATA_DIR="$d" CVM_FAKE_ARCH=arm64 CVM_FAKE_RAM_GB=48
  expect_code 0
  expect_has "já instalado"
  expect_not "brew|uv |npm|playwright"
  [ ! -e "$d/setup.json" ] && ok "setup.json not written" || bad "setup.json written"
}

test_fresh_install_dry_lists_steps() {
  local d; d="$(fresh new)"
  run fresh_dry CVM_DATA_DIR="$d" CVM_FAKE_ARCH=arm64 CVM_FAKE_RAM_GB=48
  expect_code 0
  expect_has "uv sync"
  expect_has "playwright install chromium"
  expect_has "npm install"
  expect_has "subindo"
  expect_has "/instalar"
  [ -z "$(ls -A "$d")" ] && ok "dry run wrote nothing" || bad "dry run wrote $(ls -A "$d")"
}

test_missing_homebrew_explains_and_stops() {
  run no_brew CVM_DATA_DIR="$(fresh nobrew)" CVM_FAKE_ARCH=arm64 CVM_FAKE_RAM_GB=48 CVM_BREW=/nonexistent/brew
  expect_code 1
  expect_has "Homebrew"
  expect_has 'https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh'
  expect_not "uv sync|npm install"
}

test_service_check_ignores_other_copies() {
  # ours() must not match a path that only shows up in someone's command line (its own awk, this shell)
  eval "$(sed -n '/^pids_of() {/,/^}/p' "$ROOT/scripts/install/mac.sh")"
  eval "$(grep '^ours() {' "$ROOT/scripts/install/mac.sh")"
  CUR=service_check
  OUT="ours $TMP/elsewhere/backend/.venv/bin/cvm-api"
  ours "$TMP/elsewhere/backend/.venv/bin/cvm-api" && bad "matched a program that is not running" || ok "not running"
}

test_pids_of_only_this_copy() {
  eval "$(sed -n '/^pids_of() {/,/^}/p' "$ROOT/scripts/install/mac.sh")"
  CUR=pids_of
  local a="$TMP/copy a/backend/.venv/bin/cvm-api" b="$TMP/copy b/backend/.venv/bin/cvm-api"
  mkdir -p "$(dirname "$a")" "$(dirname "$b")"
  printf '#!/bin/bash\nsleep 30\n' >"$a"; cp "$a" "$b"; chmod +x "$a" "$b"
  "$a" & local pa=$!
  "$b" & local pb=$!
  sleep 0.3
  OUT="$(pids_of "$a")"
  [ "$OUT" = "$pa" ] && ok "only copy a ($pa)" || bad "expected $pa (b is $pb)"
  kill "$pa" "$pb" 2>/dev/null; wait 2>/dev/null
}

test_restart_dry() {
  OUT="$(CVM_DATA_DIR="$(fresh restart)" bash "$ROOT/scripts/install/mac.sh" restart 2>&1)"; CODE=$?
  CUR=restart_dry
  expect_code 0
  expect_has "Desligando o CVM desta pasta"
  expect_has "subindo api"
  expect_not "brew|uv sync|npm|Conferindo o computador"
}

test_env_from_example() {
  # a fresh copy gets the public .env.example (video languages pt-BR,en-US) as its .env; an existing .env is kept
  eval "$(sed -n '/^ensure_env() {/,/^}/p' "$ROOT/scripts/install/mac.sh")"
  CUR=env_from_example
  local ROOT="$TMP/envcopy" DRY=""
  mkdir -p "$ROOT"; printf 'CVM_VIDEO_LANGUAGES=pt-BR,en-US\n' >"$ROOT/.env.example"
  ensure_env
  OUT="$(cat "$ROOT/.env" 2>&1)"
  grep -qx 'CVM_VIDEO_LANGUAGES=pt-BR,en-US' "$ROOT/.env" && ok ".env copied from .env.example" || bad "not copied"
  printf 'CVM_X=1\n' >"$ROOT/.env"; ensure_env
  OUT="$(cat "$ROOT/.env")"
  [ "$OUT" = "CVM_X=1" ] && ok "existing .env kept" || bad "existing .env overwritten"
  rm "$ROOT/.env" "$ROOT/.env.example"; ensure_env
  OUT="$(cat "$ROOT/.env" 2>&1)"
  grep -q '^# Configuração do CVM' "$ROOT/.env" && ok "empty .env without an example" || bad "no .env"
}

test_ui_fonts_ship_with_app() {
  # next/font/google downloads at the first compile: offline installs fail and Turbopack 500s on "l/font?kit=" URLs
  CUR=ui_fonts
  OUT="$(grep -rln 'next/font/google' "$ROOT/frontend/app" "$ROOT/frontend/components" 2>/dev/null)"
  [ -z "$OUT" ] && ok "no next/font/google" || bad "next/font/google still imported"
  local f; for f in Figtree-latin.woff2 Saira-latin.woff2 OFL-Figtree.txt OFL-Saira.txt; do
    [ -s "$ROOT/frontend/app/fonts/$f" ] && ok "ships $f" || bad "missing frontend/app/fonts/$f"
  done
}

test_ui_local_only() {
  # the UI is served on this Mac only (next dev on 0.0.0.0 would expose it to the whole network)
  CUR=ui_local_only
  local f
  for f in frontend/package.json scripts/install/mac.sh INICIAR.bat; do
    OUT="$(grep -n 'next dev\|dev --turbopack' "$ROOT/$f")"
    if [ -n "$OUT" ] && ! grep -v -- '-H 127.0.0.1' <<<"$OUT" | grep -q .; then ok "$f binds 127.0.0.1"; else bad "$f starts next dev without -H 127.0.0.1"; fi
  done
  OUT="$(grep -n 'next start' "$ROOT/scripts/install-launchd.sh")"
  if [ -n "$OUT" ] && ! grep -v -- '-H 127.0.0.1' <<<"$OUT" | grep -q .; then ok "install-launchd.sh binds 127.0.0.1"; else bad "install-launchd.sh starts next without -H 127.0.0.1"; fi
  OUT="$(grep -o '"next": "[^"]*"' "$ROOT/frontend/package.json")"
  [ "$OUT" != '"next": "15.5.4"' ] && ok "next is not the CVE-2025-66478 release" || bad "next 15.5.4"
}

test_fonts_have_their_licence() {
  # every bundled font ships with its SIL OFL 1.1 text (the licence asks for it): OFL-<Family>.txt in the same folder
  CUR=font_licences
  local f dir fam n=0
  while IFS= read -r f; do
    dir="$(dirname "$f")"; fam="$(basename "$f")"; fam="${fam%%[-.]*}"; n=$((n + 1))
    if grep -q "SIL Open Font License, Version 1.1" "$dir/OFL-$fam.txt" 2>/dev/null; then ok "${f#"$ROOT"/} → OFL-$fam.txt"
    else bad "${f#"$ROOT"/} has no OFL-$fam.txt beside it"; fi
  done < <(find "$ROOT/backend/cvm" "$ROOT/frontend/app" "$ROOT/render-templates" \( -name '*.ttf' -o -name '*.otf' -o -name '*.woff2' -o -name '*.woff' \) 2>/dev/null)
  [ "$n" -ge 8 ] && ok "$n fonts checked" || bad "only $n fonts found"
}

for t in test_fonts_have_their_licence test_ui_local_only test_ui_fonts_ship_with_app test_env_from_example test_pids_of_only_this_copy test_restart_dry test_service_check_ignores_other_copies test_intel_mac_refused test_low_ram_refused test_installer_rerun_is_noop test_legacy_db_counts_as_installed \
         test_fresh_install_dry_lists_steps test_missing_homebrew_explains_and_stops; do
  $t
done
if [ "$FAILS" -eq 0 ]; then echo "PASS"; else echo "$FAILS FAILED"; exit 1; fi
