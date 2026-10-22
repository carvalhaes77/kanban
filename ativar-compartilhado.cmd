@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo Preferivel: Subir Kanban e, nos Parametros, clicar em "Gerar chave e ativar".
echo Esse botao e o gatilho oficial (gera a chave e sobe o servico neste PC).
echo.
echo Este atalho e so reserva, se a API local nao estiver disponivel.
echo.

netstat -ano | findstr LISTENING | findstr ":8777" >nul
if %errorlevel%==0 (
  echo Compartilhado ja esta ligado na porta 8777.
  goto fim
)

where py >nul 2>&1
if errorlevel 1 (
  echo Nao encontrei o Python (comando py).
  goto fim
)

if not exist "compartilhado\dados" mkdir "compartilhado\dados"
if not exist "compartilhado\dados\chave-host.txt" (
  py -3 -c "import uuid,pathlib; p=pathlib.Path('compartilhado/dados/chave-host.txt'); p.write_text(uuid.uuid4().hex, encoding='utf-8'); print('Chave gerada:', p.read_text(encoding='utf-8'))"
)

REM /MIN so afeta ESTA janela (titulo "Kanban Compartilhado"). Outros py.exe ficam iguais.
REM Parar: so o PID que escuta a porta 8777 (parar-compartilhado.cmd).
start "Kanban Compartilhado" /MIN /D "%~dp0" py -3 compartilhado\servidor.py

set /a TENTATIVAS=0
:espera
set /a TENTATIVAS+=1
netstat -ano | findstr LISTENING | findstr ":8777" >nul
if %errorlevel%==0 goto ok
if %TENTATIVAS% GEQ 20 (
  echo Nao subiu na porta 8777.
  goto fim
)
ping -n 2 127.0.0.1 >nul
goto espera

:ok
echo Compartilhado ligado na porta 8777.
echo Copie a chave que aparece nos Parametros do Kanban (ou em compartilhado\dados\chave-host.txt) e passe aos convidados.

:fim
ping -n 6 127.0.0.1 >nul
exit /b 0
