@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo ============================================================
echo  Kanban - instalar Python (SEM elevacao / SEM administrador)
echo ============================================================
echo.
echo  O individual funciona so abrindo o HTML, sem Python.
echo  Subir Kanban e o modo Compartilhado precisam de Python 3.9+.
echo.
echo  Python fica no perfil do usuario (%%LOCALAPPDATA%%), fora da pasta
echo  do Kanban — nao mexe nos dados do projeto. %%TEMP%% so serve para
echo  baixar o instalador e e limpo depois (usuarios apagam TEMP).
echo.
echo  Com winget: tenta por ele. Sem winget: baixa o oficial em python.org.
echo  Sem UAC / sem "Executar como administrador".
echo.
pause

where powershell >nul 2>&1
if errorlevel 1 (
  echo Nao encontrei o PowerShell neste Windows.
  ping -n 8 127.0.0.1 >nul
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalar-python.ps1"
set "ERR=%ERRORLEVEL%"

echo.
if "%ERR%"=="0" (
  echo Concluido. Pode usar o atalho Subir Kanban.
) else (
  echo Nao concluiu. Veja as mensagens acima.
)
echo.
pause
exit /b %ERR%
