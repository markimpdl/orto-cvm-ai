"""INSTALAR.bat / scripts/install/windows.ps1. The numbers and file format are checked everywhere; when PowerShell 7
(`pwsh`) is on this machine the script is also parsed and run with CVM_DRY=1, fake hardware, free ports and throwaway
data folders: nothing is installed, started or opened."""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from cvm.setup.requirements import FEATURES

ROOT = Path(__file__).resolve().parents[2]
PS1 = ROOT / "scripts" / "install" / "windows.ps1"
BAT = ROOT / "INSTALAR.bat"
PWSH = shutil.which("pwsh")
needs_pwsh = pytest.mark.skipif(not PWSH, reason="pwsh (PowerShell 7) not installed")


def _ps1_number(name: str) -> int:
    m = re.search(rf"^\${name}\s*=\s*(\d+)\s*$", PS1.read_text(encoding="utf-8-sig"), re.M)
    assert m, f"${name} not found in windows.ps1"
    return int(m.group(1))


def test_ps1_minimums_match_requirements():
    win = FEATURES["base"].windows
    assert _ps1_number("MIN_RAM_GB") == win.ram_gb == 32
    assert _ps1_number("MIN_VRAM_GB") == win.vram_gb == 8
    assert _ps1_number("MIN_DISK_GB") == win.disk_gb


def test_ps1_is_utf8_with_bom():
    # Windows PowerShell 5.1 reads a BOM-less file as ANSI and garbles the pt-BR accents
    raw = PS1.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    raw.decode("utf-8")


def test_bat_is_plain_ascii_and_calls_the_ps1():
    raw = BAT.read_bytes()
    raw.decode("ascii")  # cmd.exe reads .bat in the OEM code page
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")  # CRLF like INICIAR.bat
    text = raw.decode("ascii")
    assert 'powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\\install\\windows.ps1" %*' in text
    assert "pause" in text


def test_ui_binds_localhost_and_skips_turbopack():
    text = PS1.read_text(encoding="utf-8-sig")
    line = next(x for x in text.splitlines() if "$NextJs dev" in x and "&" in x)
    assert "-H 127.0.0.1" in line and "--turbopack" not in line


def test_winget_ids():
    text = PS1.read_text(encoding="utf-8-sig")
    for pkg in ("astral-sh.uv", "OpenJS.NodeJS.LTS", "Gyan.FFmpeg", "oschwartz10612.Poppler", "Git.Git"):
        assert f'"{pkg}"' in text
    assert "--accept-package-agreements --accept-source-agreements" in text


def test_update_calls_a_script_that_exists(monkeypatch):
    from cvm.setup import update

    monkeypatch.setattr(update, "ROOT", ROOT)
    monkeypatch.setattr(update.sys, "platform", "win32")
    cmd = update._installer("restart")
    assert Path(cmd[cmd.index("-File") + 1]) == PS1 and PS1.exists() and cmd[-1] == "restart"


def test_shipped_in_the_public_copy():
    import importlib.util

    spec = importlib.util.spec_from_file_location("export_public", ROOT / "scripts" / "export_public.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for rel in ("INSTALAR.bat", "INICIAR.bat", "DESLIGAR.bat", "CRIAR-ATALHOS.bat", "scripts/install/windows.ps1"):
        assert not mod._excluded(rel), rel


# ---------- PowerShell runs (pwsh on this Mac) ----------

def run(tmp_path, *args, **env):
    e = {**os.environ, "CVM_API_PORT": "18791", "CVM_UI_PORT": "13011", "CVM_NO_OPEN": "1", "CVM_DRY": "1",
         "CVM_DATA_DIR": str(tmp_path), **env}
    for k in [k for k, v in e.items() if v is None]:
        del e[k]
    r = subprocess.run([PWSH, "-NoProfile", "-File", str(PS1), *args], capture_output=True, text=True, env=e,
                       timeout=120)
    return r.returncode, r.stdout + r.stderr


@needs_pwsh
def test_ps1_parses():
    out = subprocess.run([PWSH, "-NoProfile", "-Command",
                          "$t=$null;$e=$null;[void][System.Management.Automation.Language.Parser]::ParseFile("
                          f"'{PS1}',[ref]$t,[ref]$e); $e.Count"], capture_output=True, text=True, timeout=60)
    assert out.stdout.strip() == "0", out.stdout + out.stderr


@needs_pwsh
def test_fresh_install_dry_lists_steps(tmp_path):
    code, out = run(tmp_path, CVM_FAKE_GPU_GB="8", CVM_FAKE_RAM_GB="32")
    assert code == 0, out
    for s in ("8 GB de vídeo", "uv sync", "playwright install chromium", "npm install", "subindo api", "subindo worker",
              "subindo ui", "/instalar", "setup.json"):
        assert s in out, (s, out)
    assert "Exception" not in out and "InvalidOperation" not in out
    assert not any(tmp_path.iterdir())  # a dry run writes nothing


@needs_pwsh
@pytest.mark.parametrize("gpu,ram,says", [("6", "32", ["8 GB de memória de vídeo", "tem 6 GB"]),
                                          ("8", "16", ["32 GB de memória", "tem 16 GB"])])
def test_small_pc_refused_before_installing(tmp_path, gpu, ram, says):
    code, out = run(tmp_path, CVM_FAKE_GPU_GB=gpu, CVM_FAKE_RAM_GB=ram)
    assert code == 2, out
    for s in says:
        assert s in out
    assert not re.search(r"winget|uv sync|npm", out)


@needs_pwsh
def test_not_windows_refused_without_fakes(tmp_path):
    code, out = run(tmp_path, CVM_FAKE_GPU_GB=None, CVM_FAKE_RAM_GB=None)
    assert code == 2 and "Este instalador é para Windows" in out


@needs_pwsh
def test_rerun_after_install_only_starts(tmp_path):
    (tmp_path / "setup.json").write_text('{"done": true}')
    code, out = run(tmp_path, CVM_FAKE_GPU_GB="8", CVM_FAKE_RAM_GB="32")
    assert code == 0 and "já instalado" in out and "subindo" in out, out
    assert not re.search(r"winget|uv sync|npm|playwright", out)
    assert "http://localhost:13011/" in out and "/instalar" not in out


@needs_pwsh
def test_missing_winget_explains_and_stops(tmp_path):
    code, out = run(tmp_path, CVM_FAKE_GPU_GB="8", CVM_FAKE_RAM_GB="32", CVM_WINGET=str(tmp_path / "no" / "winget.exe"))
    assert code == 1, out
    assert "Falta o winget" in out and "App Installer" in out and "INSTALAR.bat" in out
    assert not re.search(r"uv sync|npm install", out)


@needs_pwsh
def test_restart_and_stop_dry(tmp_path):
    code, out = run(tmp_path, "restart")
    assert code == 0, out
    assert "Desligando o CVM desta pasta" in out and "subindo api" in out
    assert not re.search(r"winget|uv sync|npm|Conferindo o computador", out)
    code, out = run(tmp_path, "stop")
    assert code == 0 and "Desligando o CVM desta pasta" in out and "subindo" not in out


@needs_pwsh
def test_unknown_action(tmp_path):
    code, out = run(tmp_path, "nope")
    assert code == 2 and "uso:" in out


def _functions(*names: str) -> str:
    """PowerShell that defines only these functions of windows.ps1 (its main part is not run)."""
    return (f"$ast = [System.Management.Automation.Language.Parser]::ParseFile('{PS1}', [ref]$null, [ref]$null); "
            "foreach ($f in $ast.FindAll({ $args[0] -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true)) "
            f"{{ if (@({', '.join(repr(n) for n in names)}) -contains $f.Name) "
            "{ Invoke-Expression ($f.Extent.Text -replace '^function\\s+', 'function global:') } }; ")


@needs_pwsh
def test_env_from_example_and_tool_paths_never_overwrite(tmp_path):
    # a fresh copy gets .env from .env.example; the ffmpeg path is added once, quoted; values already set are kept
    (tmp_path / ".env.example").write_text("CVM_VIDEO_LANGUAGES=pt-BR,en-US\nCVM_FFMPEG_BIN=\n")
    script = (f"$Root = '{tmp_path}'; " "$Sep = [IO.Path]::DirectorySeparatorChar; $Dry = $false; "
              "$Utf8 = [Text.UTF8Encoding]::new($false); "
              + _functions("P", "Say", "Initialize-Env", "Set-EnvDefault") +
              "Initialize-Env; Set-EnvDefault 'CVM_FFMPEG_BIN' 'C:\\Program Files\\ffmpeg\\bin\\ffmpeg.exe'; "
              "Set-EnvDefault 'CVM_FFMPEG_BIN' 'C:\\other\\ffmpeg.exe'; Set-EnvDefault 'CVM_FFPROBE_BIN' 'C:\\x\\ffprobe.exe'")
    env = {k: v for k, v in os.environ.items() if not k.startswith("CVM_FF")}
    r = subprocess.run([PWSH, "-NoProfile", "-Command", script], capture_output=True, text=True,
                       env=env, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    lines = (tmp_path / ".env").read_text(encoding="utf-8").splitlines()
    assert lines[0] == "CVM_VIDEO_LANGUAGES=pt-BR,en-US"
    assert lines.count("CVM_FFMPEG_BIN='C:\\Program Files\\ffmpeg\\bin\\ffmpeg.exe'") == 1
    assert not any(x.startswith("CVM_FFMPEG_BIN=") and "other" in x for x in lines)
    assert "CVM_FFMPEG_BIN=" not in lines  # the empty line from the example was replaced, not duplicated
    assert "CVM_FFPROBE_BIN='C:\\x\\ffprobe.exe'" in lines

    from dotenv import dotenv_values
    assert dotenv_values(tmp_path / ".env")["CVM_FFMPEG_BIN"] == "C:\\Program Files\\ffmpeg\\bin\\ffmpeg.exe"


PROCS = [  # (pid, name, command line) as Win32_Process shows them
    (1, "cvm-api.exe", r'"c:\cvm a\backend\.venv\Scripts\cvm-api.exe"'),  # Windows paths ignore case
    (2, "python.exe", r'python.exe "C:\CVM A\backend\.venv\Scripts\cvm-worker.exe"'),
    (3, "cvm-api.exe", r'"C:\CVM AB\backend\.venv\Scripts\cvm-api.exe"'),  # another copy of the CVM
    (4, "node.exe", r'node "C:\CVM A\frontend\node_modules\next\dist\bin\next" dev -H 127.0.0.1 -p 3010'),
    (5, "node.exe", r'node "C:\CVM A\frontend\node_modules\next\dist\server\lib\start-server.js"'),
    (6, "cmd.exe", r'cmd /c echo C:\CVM A\backend\.venv\Scripts\cvm-api.exe'),  # a shell that only mentions it
    (7, "powershell.exe", r'powershell -NoProfile -ExecutionPolicy Bypass -File "C:\CVM A\scripts\install\windows.ps1" run worker'),
    (8, "powershell.exe", r'powershell -File "C:\CVM A\scripts\install\windows.ps1" restart'),
    (9, "cmd.exe", r'cmd /c ""C:\CVM A\INICIAR.bat" api"'),
    (10, "python.exe", r'"C:\CVM A\backend\.venv\Scripts\python.exe" -m cvm.setup.update run'),  # the update itself
]


@needs_pwsh
def test_service_match_only_this_copy():
    # ours = the whole path of THIS folder; shells that only mention it never count; the update run is never stopped;
    # the service windows (our loop and INICIAR.bat's) are found by their command line
    procs = ",".join("[pscustomobject]@{ ProcessId = %d; Name = '%s'; CommandLine = '%s' }" % (i, n, c.replace("'", "''"))
                     for i, n, c in PROCS)
    script = ("$Root = 'C:\\CVM A'; $Sep = '\\'; " + _functions("P", "Get-Ours", "Get-ServiceWindows") +
              "$PSCommandPath = 'C:\\CVM A\\scripts\\install\\windows.ps1'; "
              "$Patterns = @{ api = (P 'backend\\.venv\\Scripts\\cvm-api'); "
              "worker = (P 'backend\\.venv\\Scripts\\cvm-worker'); ui = (P 'frontend\\node_modules\\next') + '\\' }; "
              "$Shells = 'powershell.exe', 'pwsh.exe', 'cmd.exe'; "
              f"$procs = @({procs}); "
              "foreach ($n in 'api', 'worker', 'ui') { $n + '=' + ((Get-Ours $n $procs).ProcessId -join ',') }; "
              "'win=' + ((Get-ServiceWindows $procs).ProcessId -join ',')")
    r = subprocess.run([PWSH, "-NoProfile", "-Command", script], capture_output=True, text=True, timeout=60)
    assert r.stdout.split() == ["api=1", "worker=2", "ui=4,5", "win=7,9"], r.stdout + r.stderr
