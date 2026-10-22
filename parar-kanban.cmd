@echo off
chcp 65001 >nul
set "ACHOU="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr LISTENING ^| findstr ":8765"') do (
  echo Encerrando processo %%P na porta 8765.
  taskkill /PID %%P /F >nul 2>&1
  set "ACHOU=1"
)
if not defined ACHOU echo Nao havia servidor na porta 8765.

set "ACHOU2="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr LISTENING ^| findstr ":8777"') do (
  echo Encerrando compartilhado %%P na porta 8777.
  taskkill /PID %%P /F >nul 2>&1
  set "ACHOU2=1"
)
if not defined ACHOU2 echo Compartilhado ja estava parado.

echo.
echo Kanban (e compartilhado, se estava ligado) parado.
ping -n 4 127.0.0.1 >nul
exit /b 0
