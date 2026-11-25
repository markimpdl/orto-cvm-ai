@echo off
rem Sobe a API, o worker e a interface, cada um na sua janela, e reinicia o que cair.
rem Um servico que ja esta rodando nao e ligado de novo (dois workers fariam os mesmos videos em dobro).
rem   INICIAR.bat              sobe os tres e abre http://localhost:3010
rem   INICIAR.bat api|worker|ui  sobe so um (usado pelas janelas)
rem Para desligar: DESLIGAR.bat
setlocal
cd /d "%~dp0"
if not "%~1"=="" goto run

call :up api "*cvm-api*" "CVM API"
call :up worker "*cvm-worker*" "CVM Worker"
call :up ui "*next*dev*3010*" "CVM Interface"
echo Abrindo http://localhost:3010 ...
powershell -NoProfile -Command "for($i=0;$i -lt 60;$i++){try{Invoke-WebRequest http://localhost:3010 -UseBasicParsing -TimeoutSec 2 | Out-Null; break}catch{Start-Sleep 1}}"
start "" http://localhost:3010
exit /b

:up
rem %1 = servico, %2 = padrao da linha de comando, %3 = titulo da janela
powershell -NoProfile -Command "if (Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '%~2' -and $_.Name -notin 'powershell.exe','pwsh.exe','cmd.exe' }) { exit 0 } else { exit 1 }"
if %errorlevel%==0 (
  echo [ok] %~1 ja estava ligado
) else (
  start "%~3" cmd /c ""%~f0" %~1"
  echo [+] %~1 ligado
)
exit /b

:run
title CVM %~1
:loop
echo [%date% %time%] iniciando %~1...
if /i "%~1"=="api" (cd /d "%~dp0backend" & uv run cvm-api)
if /i "%~1"=="worker" (cd /d "%~dp0backend" & uv run cvm-worker)
rem sem --turbopack: no Windows o Turbopack quebra as fontes do Google (next/font) e toda pagina da erro 500
if /i "%~1"=="ui" (cd /d "%~dp0frontend" & npx next dev -H 127.0.0.1 -p 3010)
echo [%date% %time%] %~1 parou (codigo %errorlevel%). Reiniciando em 10 s... (feche esta janela ou use DESLIGAR.bat)
timeout /t 10 /nobreak >nul
goto loop
