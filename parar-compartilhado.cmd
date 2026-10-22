@echo off
chcp 65001 >nul
set "ACHOU="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr LISTENING ^| findstr ":8777"') do (
  echo Encerrando processo %%P na porta 8777.
  taskkill /PID %%P /F >nul 2>&1
  set "ACHOU=1"
)
if not defined ACHOU echo Nao havia compartilhado na porta 8777.
echo Servico compartilhado parado. O Kanban individual continua normal.
ping -n 4 127.0.0.1 >nul
exit /b 0
