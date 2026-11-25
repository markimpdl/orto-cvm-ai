# Windows installer behind INSTALAR.bat (mirrors scripts/install/mac.sh step by step): checks the hardware before
# touching anything, installs what is missing (winget packages, Python/Node dependencies), then starts API + worker +
# UI for THIS folder and opens /instalar. Safe to run again: once the install is done it only starts the services and
# opens the app.
#
#   windows.ps1            install (or just start, when already installed) and open the browser
#   windows.ps1 restart    stop and start THIS folder's API, worker and UI (the "Atualizar" button: cvm/setup/update.py)
#   windows.ps1 stop       stop them (update.py stops them before `uv sync`: Windows locks files in use)
#   windows.ps1 run <api|worker|ui>   the loop inside each service window (restarts what falls, like INICIAR.bat)
#
# Env (tests / a second copy on the same PC): CVM_DATA_DIR, CVM_API_PORT (8790), CVM_UI_PORT (3010), CVM_NO_OPEN=1
# (no browser), CVM_DRY=1 (only say what would happen), CVM_WINGET (path to winget), CVM_FAKE_GPU_GB /
# CVM_FAKE_RAM_GB (hardware check tests; either one also skips the "is this Windows" check).
#
# Saved as UTF-8 with BOM: Windows PowerShell 5.1 reads a file without BOM as the ANSI code page and would garble the
# accents. Written for 5.1 (what Windows 11 ships); PowerShell 7 runs it too.

param([string]$Action = "", [string]$Service = "")

# Minimum PC for the base install. Mirrors cvm/setup/requirements.py FEATURES["base"] (Windows row): Python may not
# exist yet, so the numbers are repeated here. Keep both in sync (tests/test_install_windows.py checks it).
$MIN_RAM_GB = 32
$MIN_VRAM_GB = 8
$MIN_DISK_GB = 25

$ErrorActionPreference = "Continue"
try { [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false) } catch { }

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path.TrimEnd("\", "/")
$Sep = [IO.Path]::DirectorySeparatorChar
function P([string]$rel) { $Root + $Sep + ($rel -replace "[\\/]", $Sep) }  # a path inside this folder

function Get-Setting([string]$key, [string]$default) { # the environment first, then the .env at the root, then the default
  $val = [Environment]::GetEnvironmentVariable($key)
  $envFile = P ".env"
  if (-not $val -and (Test-Path -LiteralPath $envFile)) {
    $line = Get-Content -LiteralPath $envFile -Encoding UTF8 | Where-Object { $_ -match "^$key=" } | Select-Object -Last 1
    if ($line) { $val = ($line -split "=", 2)[1].Trim().Trim('"').Trim("'") }
  }
  if ($val) { $val } else { $default }
}

$Data = Get-Setting "CVM_DATA_DIR" (P "data")
$ApiPort = Get-Setting "CVM_API_PORT" "8790"
$UiPort = Get-Setting "CVM_UI_PORT" "3010"
$Dry = [bool]$env:CVM_DRY
$Utf8 = [Text.UTF8Encoding]::new($false)  # files we write: UTF-8 without BOM (python-dotenv, json)

function Say([string]$msg) { Write-Host $msg }
function Step([string]$msg) { Write-Host ""; Write-Host "== $msg ==" }
function Fail([string]$msg, [int]$code = 1) { Write-Host ""; Write-Host "[x] $msg"; exit $code }
function Doing([string]$shown, [scriptblock]$run) { # run a command, or only show it in a dry run; $true when it worked
  if ($Dry) { Say "  (simulação) $shown"; return $true }
  Say "  -> $shown"
  $global:LASTEXITCODE = 0
  & $run | Out-Host  # shown, not returned: the caller gets only $true / $false
  return ($LASTEXITCODE -eq 0)
}
function Test-IsWindows { [Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT }
function Find-Command([string]$name) { (Get-Command $name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1).Source }

# ---------------- hardware ----------------

function Test-Hardware {
  Step "Conferindo o computador"
  $fake = $env:CVM_FAKE_GPU_GB -or $env:CVM_FAKE_RAM_GB
  if (-not $fake -and -not (Test-IsWindows)) { Fail "Este instalador é para Windows. No Mac, use INSTALAR.command." 2 }

  $gpu = ""; $vram = 0
  if ($env:CVM_FAKE_GPU_GB) {
    $gpu = "placa de teste"; $vram = [int]$env:CVM_FAKE_GPU_GB
  } else {
    $smi = Find-Command "nvidia-smi"
    if (-not $smi -and (Test-Path "$env:SystemRoot\System32\nvidia-smi.exe")) { $smi = "$env:SystemRoot\System32\nvidia-smi.exe" }
    if (-not $smi) {
      Fail "O CVM precisa de uma placa de vídeo NVIDIA com o driver instalado, e não achei nenhuma neste computador. Nada foi instalado." 2
    }
    # one line per card ("NVIDIA GeForce RTX 5060, 8151", MiB): the biggest one counts
    foreach ($line in (& $smi --query-gpu=name,memory.total --format=csv,noheader,nounits 2>$null)) {
      $name, $mib = $line -split ",\s*(?=[^,]*$)"
      $gb = [math]::Round([double]$mib / 1024)
      if ($gb -gt $vram) { $vram = $gb; $gpu = $name.Trim() }
    }
    if (-not $gpu) { Fail "Não consegui ler a placa de vídeo pelo nvidia-smi. Atualize o driver da NVIDIA e rode de novo. Nada foi instalado." 2 }
  }
  if ($vram -lt $MIN_VRAM_GB) {
    Fail "O CVM precisa de uma placa NVIDIA com pelo menos $MIN_VRAM_GB GB de memória de vídeo. A sua ($gpu) tem $vram GB, então nada foi instalado." 2
  }

  if ($env:CVM_FAKE_RAM_GB) { $ram = [int]$env:CVM_FAKE_RAM_GB }
  else {
    # whole GB, as cvm/setup/requirements.detect(): a 32 GB PC shows ~31.8 GB to Windows
    $ram = [int][math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)
  }
  if ($ram -lt $MIN_RAM_GB) {
    Fail "O CVM precisa de pelo menos $MIN_RAM_GB GB de memória. Este computador tem $ram GB, então nada foi instalado." 2
  }
  Say "[ok] $gpu ($vram GB de vídeo), $ram GB de memória"

  try {
    $free = [math]::Floor((Get-Item -LiteralPath $Root).PSDrive.Free / 1GB)
    if ($free -lt $MIN_DISK_GB) {
      Say "[!] Só $free GB livres no disco; o básico usa uns $MIN_DISK_GB GB. Dá para seguir, mas libere espaço antes de baixar os modelos."
    }
  } catch { }
}

function Test-Installed { # $true when the wizard already finished (data\setup.json), or for an install older than the wizard
  $f = Join-Path $Data "setup.json"
  if (Test-Path -LiteralPath $f) { return [bool]((Get-Content -LiteralPath $f -Raw) -match '"done"\s*:\s*true') }
  $db = Join-Path $Data "cvm.sqlite"
  $py = P "backend\.venv\Scripts\python.exe"
  if (-not (Test-Path -LiteralPath $db) -or -not (Test-Path -LiteralPath $py)) { return $false }
  $env:CVM_DB_CHECK = $db
  $out = & $py -c "import os, sqlite3; c = sqlite3.connect('file:' + os.environ['CVM_DB_CHECK'] + '?mode=ro', uri=True); print(1 if c.execute('select 1 from channel limit 1').fetchone() else 0)" 2>$null
  return ("$out".Trim() -eq "1")
}

# ---------------- programs (winget) ----------------

function Update-Path { # new installs land in the registry PATH; this window still has the old one
  if (-not (Test-IsWindows)) { return }
  $parts = @()
  foreach ($scope in "Machine", "User") { $parts += ([Environment]::GetEnvironmentVariable("Path", $scope) -split ";") }
  $parts += "$env:LOCALAPPDATA\Microsoft\WinGet\Links", "$env:USERPROFILE\.local\bin"
  $parts += ($env:Path -split ";")  # what this run added itself (poppler) stays
  $seen = @{}; $out = @()
  foreach ($p in $parts) {
    if (-not $p) { continue }
    $p = [Environment]::ExpandEnvironmentVariables($p)
    if (-not $seen.ContainsKey($p.ToLower())) { $seen[$p.ToLower()] = 1; $out += $p }
  }
  $env:Path = $out -join ";"
}

$Winget = ""
function Test-Winget {
  Step "winget"
  if ($env:CVM_WINGET) { if (Test-Path -LiteralPath $env:CVM_WINGET) { $script:Winget = $env:CVM_WINGET } }
  else { $script:Winget = Find-Command "winget" }
  if (-not $Winget -and $Dry -and -not $env:CVM_WINGET) { $script:Winget = "winget"; Say "  (simulação) winget não encontrado; seguindo só para mostrar os passos"; return }
  if (-not $Winget) {
    Write-Host ""
    Write-Host "[x] Falta o winget, o instalador de programas do Windows."
    Write-Host "  Abra a Microsoft Store, procure por ""Instalador de Aplicativo"" (App Installer) e instale ou atualize."
    Write-Host "  Depois, dê dois cliques em INSTALAR.bat de novo."
    exit 1
  }
  Say "[ok] winget"
}

function Install-Package([string]$id, [string]$label, [scriptblock]$present) {
  # winget's exit code is not trusted ("already installed" is an error to it): the program being there afterwards is
  Doing "winget install --id $id" { & $Winget install --id $id -e --accept-package-agreements --accept-source-agreements } | Out-Null
  if ($Dry) { return }
  Update-Path
  if (-not (& $present)) { Fail "Não deu para instalar o $label pelo winget. Veja a mensagem acima e rode INSTALAR.bat de novo." }
}

function Test-Libass([string]$ffmpeg) { # the captions need libass (the "ass" / "subtitles" filters)
  if (-not $ffmpeg -or -not (Test-Path -LiteralPath $ffmpeg)) { return $false }
  $filters = & $ffmpeg -hide_banner -filters 2>$null
  return [bool]($filters | Select-String -Pattern "\sass\s" -Quiet)
}

function Find-Ffmpeg { # an ffmpeg with libass: on PATH, in winget's links, or in the Gyan package folder
  $c = @()
  $c += @(Get-Command ffmpeg -CommandType Application -All -ErrorAction SilentlyContinue | ForEach-Object { $_.Source })
  $c += "$env:LOCALAPPDATA\Microsoft\WinGet\Links\ffmpeg.exe", "$env:ProgramFiles\WinGet\Links\ffmpeg.exe"
  foreach ($base in "$env:LOCALAPPDATA\Microsoft\WinGet\Packages", "$env:ProgramFiles\WinGet\Packages") {
    if ($base -and (Test-Path -LiteralPath $base)) {
      $c += @(Get-ChildItem -Path $base -Filter "Gyan.FFmpeg*" -Directory -ErrorAction SilentlyContinue |
              Get-ChildItem -Recurse -Filter ffmpeg.exe -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName })
    }
  }
  foreach ($f in $c) { if (Test-Libass $f) { return $f } }
  return $null
}

function Find-Pdftoppm { # poppler's bin folder: on PATH, else inside the winget package (it does not always link it)
  $cmd = Find-Command "pdftoppm"
  if ($cmd) { return $cmd }
  foreach ($base in "$env:LOCALAPPDATA\Microsoft\WinGet\Packages", "$env:ProgramFiles\WinGet\Packages") {
    if ($base -and (Test-Path -LiteralPath $base)) {
      $f = Get-ChildItem -Path $base -Filter "oschwartz10612.Poppler*" -Directory -ErrorAction SilentlyContinue |
           Get-ChildItem -Recurse -Filter pdftoppm.exe -ErrorAction SilentlyContinue | Select-Object -First 1
      if ($f) { return $f.FullName }
    }
  }
  return $null
}

function Add-UserPath([string]$dir) { # for the app (it runs pdftoppm by name) in this run and in the next windows
  $user = [Environment]::GetEnvironmentVariable("Path", "User")
  if (-not (($user -split ";") -contains $dir)) {
    [Environment]::SetEnvironmentVariable("Path", (($user.TrimEnd(";"), $dir) -join ";").TrimStart(";"), "User")
  }
  if (-not (($env:Path -split ";") -contains $dir)) { $env:Path = "$dir;$env:Path" }
}

$Ffmpeg = $null
function Install-Programs {
  Step "Programas"
  Update-Path
  if (Find-Command "uv") { Say "[ok] uv" } else { Install-Package "astral-sh.uv" "uv" { Find-Command "uv" } }

  $nodeMajor = 0
  if (Find-Command "node") { try { $nodeMajor = [int](& node -p "process.versions.node.split('.')[0]") } catch { } }
  if ($nodeMajor -ge 18) { Say "[ok] node $(& node -v)" }
  else { Install-Package "OpenJS.NodeJS.LTS" "Node.js" { Find-Command "node" } }

  $script:Ffmpeg = Find-Ffmpeg
  if ($Ffmpeg) { Say "[ok] ffmpeg ($Ffmpeg)" }
  else {
    Install-Package "Gyan.FFmpeg" "ffmpeg" { Find-Ffmpeg }
    if (-not $Dry) { $script:Ffmpeg = Find-Ffmpeg }
  }

  $pdf = Find-Pdftoppm
  if (-not $pdf) {
    Install-Package "oschwartz10612.Poppler" "poppler" { Find-Pdftoppm }
    if (-not $Dry) { $pdf = Find-Pdftoppm }
  }
  if ($pdf) {
    if (-not (Find-Command "pdftoppm")) { Add-UserPath (Split-Path -Parent $pdf) }
    Say "[ok] poppler"
  }

  if (Find-Command "git") { Say "[ok] git" } else { Install-Package "Git.Git" "Git" { Find-Command "git" } }
}

# ---------------- .env and libraries ----------------

function Initialize-Env { # a first .env from .env.example (it sets the video languages offered), else an empty one; never overwritten
  $envFile = P ".env"; $example = P ".env.example"
  if (Test-Path -LiteralPath $envFile) { return }
  if (Test-Path -LiteralPath $example) {
    if ($Dry) { Say "  (simulação) criar .env a partir do .env.example" } else { Copy-Item -LiteralPath $example $envFile }
  } elseif ($Dry) { Say "  (simulação) criar .env vazio" }
  else { [IO.File]::WriteAllText($envFile, "# Configuração do CVM. Tudo é opcional; as chaves são pedidas no assistente.`r`n", $Utf8) }
}

function Set-EnvDefault([string]$key, [string]$value) { # writes KEY='value' to .env only when it is not set yet
  $envFile = P ".env"
  if ([Environment]::GetEnvironmentVariable($key)) { return }
  if ((Test-Path -LiteralPath $envFile) -and (Get-Content -LiteralPath $envFile -Encoding UTF8 | Where-Object { $_ -match "^$key=." })) { return }
  if ($Dry) { Say "  (simulação) gravar $key no .env"; return }
  $text = if (Test-Path -LiteralPath $envFile) { [IO.File]::ReadAllText($envFile, $Utf8) } else { "" }
  $lines = @($text -split "`r?`n" | Where-Object { $_ -notmatch "^$key=\s*$" })  # an empty KEY= line is replaced
  $body = (($lines -join "`r`n").TrimEnd() + "`r`n$key='$value'`r`n").TrimStart()
  [IO.File]::WriteAllText($envFile, $body, $Utf8)
  Say "  $key gravado no .env"
}

function Set-Tools { # the app's default ffmpeg path is the Mac one (config.py): point it at the one found here
  if (-not $Ffmpeg) {
    if ($Dry) { Say "  (simulação) gravar CVM_FFMPEG_BIN e CVM_FFPROBE_BIN no .env" }
    return
  }
  $probe = Join-Path (Split-Path -Parent $Ffmpeg) "ffprobe.exe"
  Set-EnvDefault "CVM_FFMPEG_BIN" $Ffmpeg
  if (Test-Path -LiteralPath $probe) { Set-EnvDefault "CVM_FFPROBE_BIN" $probe }
}

function Sync-Libraries {
  Step "Bibliotecas do CVM (pode levar alguns minutos na primeira vez)"
  Push-Location (P "backend")
  $ok = (Doing "uv sync" { & uv sync }) -and (Doing "uv run playwright install chromium" { & uv run playwright install chromium })
  Pop-Location
  if (-not $ok) { Fail "O uv sync falhou. Veja a mensagem acima." }
  Push-Location (P "frontend")
  $ok = Doing "npm install --no-audit --no-fund" { & npm.cmd install --no-audit --no-fund }
  Pop-Location
  if (-not $ok) { Fail "O npm install falhou. Veja a mensagem acima." }
  Initialize-Env
  Set-Tools
  $setup = Join-Path $Data "setup.json"
  if (-not (Test-Path -LiteralPath $setup)) {
    if ($Dry) { Say "  (simulação) marcar $setup para o assistente" }
    else {
      New-Item -ItemType Directory -Force -Path $Data | Out-Null
      [IO.File]::WriteAllText($setup, '{"stage": "wizard", "done": false}' + "`n", $Utf8)
    }
  }
}

# ---------------- services of THIS folder only (another copy of the CVM on the same PC is never touched) ----------------

$ApiExe = P "backend\.venv\Scripts\cvm-api.exe"
$WorkerExe = P "backend\.venv\Scripts\cvm-worker.exe"
$NextJs = P "frontend\node_modules\next\dist\bin\next"
# a process is ours when its command line holds one of these whole paths (uv's launcher and the python it starts both
# carry the script path; next dev and its next-server children run from node_modules\next)
$Patterns = @{
  api    = P "backend\.venv\Scripts\cvm-api"
  worker = P "backend\.venv\Scripts\cvm-worker"
  ui     = (P "frontend\node_modules\next") + $Sep
}
$Shells = "powershell.exe", "pwsh.exe", "cmd.exe", "conhost.exe", "WindowsTerminal.exe"

function Get-Procs {
  try { @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object { $_.CommandLine }) } catch { @() }
}
function Get-Ours([string]$name, $procs) {
  $pat = $Patterns[$name]
  @($procs | Where-Object { $_ -and $_.CommandLine -and $_.ProcessId -ne $PID -and $Shells -notcontains $_.Name -and
                            ($_.CommandLine.IndexOf($pat, [StringComparison]::OrdinalIgnoreCase) -ge 0 -or
                             # a worker started some other way (uv run, INICIAR.bat) may not carry this folder's path:
                             # any cvm-worker counts, as in INICIAR.bat, so two workers never make the same videos
                             ($name -eq "worker" -and $_.CommandLine -match '(?i)\bcvm-worker(\.exe)?\b')) })
}
function Get-ServiceWindows($procs) {
  # the loops of the service windows: ours ("windows.ps1 run <name>") and INICIAR.bat's ("INICIAR.bat" api), same folder
  $me = [regex]::Escape($PSCommandPath)
  $bat = [regex]::Escape((P "INICIAR.bat"))
  @($procs | Where-Object { $_ -and $_.CommandLine -and $_.ProcessId -ne $PID -and
                            ($_.CommandLine -match "(?i)$me""?\s+run\s+(api|worker|ui)\b" -or
                             $_.CommandLine -match "(?i)$bat""?\s+(api|worker|ui)\b") })
}

function Test-PortBusy([int]$port) {
  $c = New-Object Net.Sockets.TcpClient
  try { return ($c.ConnectAsync("127.0.0.1", $port).Wait(400) -and $c.Connected) } catch { return $false } finally { $c.Close() }
}

function Set-ServiceEnv {
  $env:CVM_API_PORT = $ApiPort
  $env:NEXT_PUBLIC_CVM_API_PORT = $ApiPort
  $env:CVM_UI_PORT = $UiPort
}

function Start-ServiceWindow([string]$name) {
  Set-ServiceEnv
  $shell = (Get-Process -Id $PID).Path  # the same PowerShell that runs this script (5.1 or 7)
  $argv = "-NoProfile -ExecutionPolicy Bypass -File ""$PSCommandPath"" run $name"
  Start-Process -FilePath $shell -ArgumentList $argv -WorkingDirectory $Root | Out-Null
  Say "  > $name ligado (janela ""CVM $name"")"
}

function Start-One([string]$name, [string]$port, $procs) {
  if ($port) { Say "> subindo $name (porta $port)" } else { Say "> subindo $name" }
  if ((Get-Ours $name $procs).Count) { Say "  [ok] $name já estava ligado"; return }
  if ($port -and (Test-PortBusy ([int]$port))) {
    $msg = "A porta $port já está em uso por outro programa. Feche esse programa ou escolha outra porta (CVM_API_PORT / CVM_UI_PORT)."
    if ($Dry) { Say "  (simulação) $msg"; return }
    Fail $msg
  }
  if ($Dry) { return }
  Start-ServiceWindow $name
}

function Start-AndOpen([string]$page) {
  Step "Ligando o CVM"
  $procs = Get-Procs
  Start-One "api" $ApiPort $procs
  Start-One "worker" "" $procs
  Start-One "ui" $UiPort $procs
  $url = "http://localhost:$UiPort$page"
  if ($Dry) { Say "(simulação) abrir $url"; return }
  Say "Esperando a interface responder (na primeira vez demora um pouco)..."
  $ok = $false
  for ($i = 0; $i -lt 180; $i++) {
    try {
      Invoke-WebRequest "http://127.0.0.1:$ApiPort/health" -UseBasicParsing -TimeoutSec 2 | Out-Null
      Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 60 | Out-Null
      $ok = $true; break
    } catch { Start-Sleep -Seconds 1 }
  }
  if (-not $ok) { Fail "A interface não respondeu em 3 minutos. Veja as janelas ""CVM api"" e ""CVM ui""." }
  if (-not $env:CVM_NO_OPEN) { Start-Process $url }
  Say "[ok] CVM: $url"
}

function Stop-Ours { # this folder's API, worker and UI; the service windows first, or their loop would start them again
  Step "Desligando o CVM desta pasta"
  $procs = Get-Procs
  $targets = @(Get-ServiceWindows $procs) + @(Get-Ours "api" $procs) + @(Get-Ours "worker" $procs) + @(Get-Ours "ui" $procs)
  foreach ($p in $targets) {
    if ($Dry) { Say "  (simulação) parar $($p.Name) $($p.ProcessId)"; continue }
    Say "  -> parar $($p.Name) $($p.ProcessId)"
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue  # one by one, never the tree: the update runs below the API
  }
  if ($Dry) { return }
  for ($i = 0; $i -lt 20; $i++) {
    $procs = Get-Procs
    $left = @(Get-Ours "api" $procs) + @(Get-Ours "worker" $procs) + @(Get-Ours "ui" $procs)
    if (-not $left.Count) { break }
    foreach ($p in $left) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 1
  }
  Say "[ok] desligado"
}

function Invoke-ServiceLoop([string]$name) { # inside a service window: run it and start it again when it falls
  try { $Host.UI.RawUI.WindowTitle = "CVM $name" } catch { }
  Set-ServiceEnv
  if ($name -eq "worker") { # keep the PC awake while the worker runs (the Mac's caffeinate -i)
    try {
      Add-Type -Namespace Cvm -Name Power -MemberDefinition '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint f);'
      [Cvm.Power]::SetThreadExecutionState([uint32]2147483649) | Out-Null  # ES_CONTINUOUS | ES_SYSTEM_REQUIRED
    } catch { }
  }
  while ($true) {
    Say "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] iniciando $name..."
    $global:LASTEXITCODE = 0
    switch ($name) {
      "api"    { Set-Location (P "backend"); & $ApiExe }
      "worker" { Set-Location (P "backend"); & $WorkerExe }
      # no --turbopack: on Windows Turbopack breaks fonts and every page gives a 500 (see INICIAR.bat)
      "ui"     { Set-Location (P "frontend"); & node $NextJs dev -H 127.0.0.1 -p $UiPort }
      default  { Fail "serviço desconhecido: $name (use api, worker ou ui)" 2 }
    }
    Say "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $name parou (código $LASTEXITCODE). Reiniciando em 10 s... (feche esta janela para desligar)"
    Start-Sleep -Seconds 10
  }
}

# ---------------- main ----------------

Update-Path  # uv/node/ffmpeg installed by an earlier run are found even from a window opened before that run

switch ($Action) {
  "restart" { # used by "Atualizar": no hardware check, no browser
    $env:CVM_NO_OPEN = "1"
    Stop-Ours
    Start-AndOpen "/"
    exit 0
  }
  "stop" { Stop-Ours; exit 0 }
  "run" { Invoke-ServiceLoop $Service; exit 0 }
  "" { }
  default { Say "uso: windows.ps1 [restart | stop | run api|worker|ui]"; exit 2 }
}

Test-Hardware
if (Test-Installed) {
  Say "[ok] CVM já instalado: só falta ligar."
  Start-AndOpen "/"
  exit 0
}
Test-Winget
Install-Programs
Sync-Libraries
Start-AndOpen "/instalar"
Say "Siga o assistente no navegador para terminar a instalação."
exit 0
