@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "URL=http://127.0.0.1:8765/kanban-ti-visivel-demo/"
set "LOG=%~dp0kanban-web.log"

set "JA_LIGADO=0"
netstat -ano | findstr LISTENING | findstr ":8765" >nul
if %errorlevel%==0 (
  set "JA_LIGADO=1"
  goto abrir
)

echo.
echo Preparando o Kanban neste PC...
echo (Se faltar Python, instala sozinho no seu usuario — sem administrador.)
echo.

where powershell >nul 2>&1
if errorlevel 1 (
  echo Preciso do PowerShell do Windows para preparar o ambiente.
  ping -n 8 127.0.0.1 >nul
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalar-python.ps1" -Auto
if errorlevel 1 (
  echo.
  echo Nao consegui preparar o Python automaticamente.
  echo Sem rede ou politica do PC pode bloquear. Tente de novo ou use instalar-python.cmd.
  echo O quadro Individual ainda abre so com o arquivo HTML, sem Python.
  ping -n 12 127.0.0.1 >nul
  exit /b 1
)

rem Atualiza PATH desta janela apos eventual instalacao
set "PYEXE="
for /f "delims=" %%I in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do set "PYEXE=%%I"
if not defined PYEXE (
  for /f "delims=" %%I in ('powershell -NoProfile -Command "$p=Join-Path $env:LOCALAPPDATA 'Programs\Python'; if (Test-Path $p) { Get-ChildItem $p -Recurse -Filter python.exe | Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName }"') do set "PYEXE=%%I"
)
if not defined PYEXE (
  echo Python foi instalado, mas esta janela ainda nao o enxerga.
  echo Feche e abra de novo o atalho Subir Kanban.
  ping -n 10 127.0.0.1 >nul
  exit /b 1
)

echo [%date% %time%] Iniciando "%PYEXE%" (sem janela de console) > "%LOG%"
rem So ESTE processo: WindowStyle Hidden. Nao mexe em outros py.exe / python.
rem Parar: so o PID da porta 8765 (parar-kanban.cmd).
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$p='%PYEXE%'; $s=Join-Path '%~dp0' 'servidor-local.py'; $d='%~dp0'; $l='%~dp0kanban-web.log'; $e='%~dp0kanban-web.err.log'; Start-Process -FilePath $p -ArgumentList '-u',$s -WorkingDirectory $d -WindowStyle Hidden -RedirectStandardOutput $l -RedirectStandardError $e"

set /a TENTATIVAS=0
:espera
set /a TENTATIVAS+=1
netstat -ano | findstr LISTENING | findstr ":8765" >nul
if %errorlevel%==0 goto abrir
if %TENTATIVAS% GEQ 25 (
  echo.
  echo O Kanban nao subiu na porta 8765.
  echo Veja: %LOG%
  if exist "%LOG%" type "%LOG%"
  ping -n 12 127.0.0.1 >nul
  exit /b 1
)
ping -n 2 127.0.0.1 >nul
goto espera

:abrir
start "" "%URL%"
echo.
if "%JA_LIGADO%"=="1" (
  echo Kanban ja estava no ar — sem nova instancia. Aberto no navegador.
) else (
  echo Pronto. Kanban aberto no navegador.
)
echo Para o compartilhado neste PC: Parametros → Gerar chave e ativar → Copiar chave.
echo Para encerrar: botao Desligar no quadro, ou Parar Kanban.
ping -n 6 127.0.0.1 >nul
exit /b 0
