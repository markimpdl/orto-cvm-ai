@echo off
rem Cria na Area de Trabalho os atalhos "Ligar CVM" (INICIAR.bat) e "Desligar CVM" (DESLIGAR.bat). Rodar uma vez.
setlocal
cd /d "%~dp0"
powershell -NoProfile -Command "$d = [Environment]::GetFolderPath('Desktop'); $s = New-Object -ComObject WScript.Shell; foreach ($p in @(@('Ligar CVM','INICIAR.bat'), @('Desligar CVM','DESLIGAR.bat'))) { $l = $s.CreateShortcut((Join-Path $d ($p[0] + '.lnk'))); $l.TargetPath = (Join-Path '%~dp0' $p[1]); $l.WorkingDirectory = '%~dp0'; $l.Save() }; Write-Host ('Atalhos criados em ' + $d)"
pause
