@echo off
rem Desliga a API, o worker e a interface abertos pelo INICIAR.bat.
rem Antes avisa se algum video esta no meio de uma etapa (ela recomeca do zero quando o worker voltar).
setlocal
powershell -NoProfile -Command "try { $j = Invoke-RestMethod http://127.0.0.1:8790/jobs -TimeoutSec 5 } catch { exit 0 }; $busy = 'scripting','voicing','sourcing_visuals','lipsyncing','composing','rendering','seo'; $w = @($j | Where-Object { $_.progress.pid -and -not $_.progress.waiting -and $busy -contains $_.state }); foreach ($x in $w) { Write-Host ('  #' + $x.project_id + ' ' + $x.state + ': ' + $x.progress.label) }; if ($w.Count) { exit 3 } else { exit 0 }"
if %errorlevel%==3 (
  echo Atencao: os videos acima estao no meio de uma etapa e ela vai recomecar do zero.
  choice /c SN /n /m "Desligar mesmo assim? (S/N) "
  if errorlevel 2 (echo Nada foi desligado. & pause & exit /b)
)
rem fecha as janelas do INICIAR.bat (e tudo que rodava dentro delas), senao o loop religaria o servico
taskkill /FI "WINDOWTITLE eq CVM*" /T /F >nul 2>&1
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { ($_.CommandLine -like '*cvm-worker*' -or $_.CommandLine -like '*cvm-api*' -or $_.CommandLine -like '*next*dev*3010*') -and $_.Name -notin 'powershell.exe','pwsh.exe' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
echo CVM desligado.
pause
