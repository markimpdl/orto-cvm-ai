@echo off
rem Dois cliques aqui instalam o CVM neste PC (Windows 11 + placa NVIDIA) e abrem o assistente no navegador.
rem Rodar de novo e seguro: depois de instalado, so liga o CVM e abre o app.
rem   INSTALAR.bat restart   desliga e liga a API, o worker e a interface desta pasta
rem   INSTALAR.bat stop      desliga os tres
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install\windows.ps1" %*
set "code=%errorlevel%"
if "%code%"=="0" exit /b 0
echo.
echo A instalacao parou ^(codigo %code%^). Leia a mensagem acima.
pause
exit /b %code%
