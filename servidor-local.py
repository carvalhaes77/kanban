# -*- coding: utf-8 -*-
"""Sobe o Kanban na porta 8765 e permite ligar/desligar o compartilhado sob demanda."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import uuid
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

RAIZ = Path(__file__).resolve().parent
PORTA_WEB = 8765
PORTA_COMP = 8777
DADOS = RAIZ / "compartilhado" / "dados"
HOST_KEY = DADOS / "chave-host.txt"
PROC_COMP = None
TRAVA = threading.RLock()
SERVER_WEB = None


def encerrar_geral() -> dict:
    """Para compartilhado (8777) e agenda o fim do servidor web (8765)."""
    global SERVER_WEB
    parar_compartilhado()

    def _parar_web():
        import time

        time.sleep(0.35)
        srv = SERVER_WEB
        if srv is not None:
            try:
                srv.shutdown()
            except Exception:
                pass

    threading.Thread(target=_parar_web, daemon=True).start()
    return {
        "ok": True,
        "mensagem": "Encerrando sessões e serviços do Kanban neste PC (não desliga o sistema).",
        "sistema": nome_sistema(),
    }


def nome_sistema() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "mac"
    return "linux"


def json_resp(handler, codigo, payload):
    corpo = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(codigo)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(corpo)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.end_headers()
    handler.wfile.write(corpo)


def porta_aberta(porta: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", porta)) == 0


def ips_desta_maquina() -> set[str]:
    import socket

    ips = {"127.0.0.1", "::1", "localhost"}
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            ip = info[4][0]
            if ip:
                ips.add(ip)
                ips.add(ip.replace("::ffff:", ""))
    except OSError:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ips.add(s.getsockname()[0])
    except OSError:
        pass
    return ips


def ler_chave() -> str:
    if not HOST_KEY.exists():
        return ""
    return HOST_KEY.read_text(encoding="utf-8").strip()


def gravar_chave(chave: str) -> None:
    DADOS.mkdir(parents=True, exist_ok=True)
    HOST_KEY.write_text(chave.strip(), encoding="utf-8")


def iniciar_compartilhado(chave: str) -> dict:
    global PROC_COMP
    with TRAVA:
        gravar_chave(chave)
        if porta_aberta(PORTA_COMP):
            return {
                "ok": True,
                "jaEstavaLigado": True,
                "porta": PORTA_COMP,
                "chave": chave,
                "sistema": nome_sistema(),
            }
        script = RAIZ / "compartilhado" / "servidor.py"
        kwargs = {"cwd": str(RAIZ)}
        if sys.platform == "win32":
            # Só ESTE filho: sem janela de console. Não mexe em outros python/py.exe.
            # CREATE_NO_WINDOW = 0x08000000
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | 0x08000000
            kwargs["stdin"] = subprocess.DEVNULL
            kwargs["stdout"] = subprocess.DEVNULL
            kwargs["stderr"] = subprocess.DEVNULL
        else:
            kwargs["start_new_session"] = True
        PROC_COMP = subprocess.Popen([sys.executable, str(script)], **kwargs)
        for _ in range(40):
            if porta_aberta(PORTA_COMP):
                return {
                    "ok": True,
                    "jaEstavaLigado": False,
                    "porta": PORTA_COMP,
                    "chave": chave,
                    "pid": PROC_COMP.pid,
                    "sistema": nome_sistema(),
                }
            threading.Event().wait(0.25)
        return {"ok": False, "erro": "O serviço compartilhado não respondeu a tempo.", "sistema": nome_sistema()}


def _pids_na_porta(porta: int) -> set[str]:
    pids: set[str] = set()
    try:
        if sys.platform == "win32":
            out = subprocess.check_output(
                f'netstat -ano | findstr LISTENING | findstr ":{porta}"',
                shell=True,
                text=True,
                errors="ignore",
            )
            for linha in out.splitlines():
                partes = linha.split()
                if partes:
                    pids.add(partes[-1])
        else:
            out = subprocess.check_output(
                ["lsof", "-nP", f"-iTCP:{porta}", "-sTCP:LISTEN", "-t"],
                text=True,
                errors="ignore",
            )
            for pid in out.split():
                if pid.strip().isdigit():
                    pids.add(pid.strip())
    except Exception:
        pass
    return pids


def _matar_pid(pid: str) -> None:
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True)
        else:
            os.kill(int(pid), signal.SIGTERM)
    except Exception:
        try:
            if sys.platform != "win32":
                os.kill(int(pid), signal.SIGKILL)
        except Exception:
            pass


def parar_compartilhado() -> dict:
    global PROC_COMP
    with TRAVA:
        if PROC_COMP and PROC_COMP.poll() is None:
            try:
                if sys.platform == "win32" and hasattr(signal, "CTRL_BREAK_EVENT"):
                    PROC_COMP.send_signal(signal.CTRL_BREAK_EVENT)
                PROC_COMP.terminate()
                PROC_COMP.wait(timeout=5)
            except Exception:
                try:
                    PROC_COMP.kill()
                except Exception:
                    pass
            PROC_COMP = None
        if porta_aberta(PORTA_COMP):
            for pid in _pids_na_porta(PORTA_COMP):
                _matar_pid(pid)
        return {"ok": True, "ativo": porta_aberta(PORTA_COMP), "sistema": nome_sistema()}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(RAIZ), **kwargs)

    def log_message(self, fmt, *args):
        sys.stderr.write("[web] " + (fmt % args) + "\n")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def _ler_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        return json.loads(self.rfile.read(n).decode("utf-8"))

    def do_GET(self):
        rota = urlparse(self.path).path
        if rota == "/api/local/status":
            json_resp(
                self,
                200,
                {
                    "web": True,
                    "sistema": nome_sistema(),
                    "compartilhadoAtivo": porta_aberta(PORTA_COMP),
                    "portaCompartilhado": PORTA_COMP,
                    "chaveDefinida": bool(ler_chave()),
                },
            )
            return
        return super().do_GET()

    def _so_neste_pc(self) -> bool:
        ip = (self.client_address[0] or "").replace("::ffff:", "")
        return ip in ips_desta_maquina()

    def do_POST(self):
        rota = urlparse(self.path).path
        try:
            dados = self._ler_json()
        except json.JSONDecodeError:
            json_resp(self, 400, {"erro": "JSON inválido."})
            return

        if rota == "/api/local/compartilhado/ligar":
            if not self._so_neste_pc():
                json_resp(self, 403, {"erro": "Ligar o compartilhado só neste computador (host)."})
                return
            chave = str(dados.get("chave") or "").strip()
            if not chave:
                chave = uuid.uuid4().hex
            if len(chave) < 8:
                json_resp(self, 400, {"erro": "Chave muito curta."})
                return
            resultado = iniciar_compartilhado(chave)
            json_resp(self, 200 if resultado.get("ok") else 500, resultado)
            return

        if rota == "/api/local/compartilhado/desligar":
            if not self._so_neste_pc():
                json_resp(self, 403, {"erro": "Desligar o compartilhado só neste computador (host)."})
                return
            json_resp(self, 200, parar_compartilhado())
            return

        if rota == "/api/local/encerrar":
            if not self._so_neste_pc():
                json_resp(
                    self,
                    403,
                    {"erro": "Desligar geral só neste computador (onde o Subir Kanban está rodando)."},
                )
                return
            json_resp(self, 200, encerrar_geral())
            return

        if rota == "/api/local/compartilhado/confirmar-host":
            # Só neste PC: a chave do arquivo local bate com a informada?
            if not self._so_neste_pc():
                json_resp(self, 403, {"erro": "Confirmação de host só neste computador."})
                return
            chave = str(dados.get("chave") or "").strip()
            salva = ler_chave()
            sou = bool(chave) and bool(salva) and chave == salva
            json_resp(
                self,
                200,
                {
                    "souHost": sou,
                    "compartilhadoAtivo": porta_aberta(PORTA_COMP),
                    "chaveDefinida": bool(salva),
                },
            )
            return

        json_resp(self, 404, {"erro": "Rota não encontrada."})


def main():
    global SERVER_WEB
    os.chdir(RAIZ)
    DADOS.mkdir(parents=True, exist_ok=True)
    # Instância única: se a 8765 já responde, não sobe outro processo (atalho/cmd/navegador).
    if porta_aberta(PORTA_WEB):
        print(
            "Kanban ja esta no ar em http://127.0.0.1:%s/kanban-ti-visivel-demo/ (instancia unica)."
            % PORTA_WEB,
            flush=True,
        )
        print("Nao foi criada outra instancia. Use Desligar no quadro ou Parar Kanban para encerrar.", flush=True)
        raise SystemExit(0)
    # Se abriu com console (teste manual), o titulo ajuda no Alt+Tab — nao mexe em outros Python.
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleTitleW("Kanban Demonstracao (porta 8765)")
        except Exception:
            pass
    try:
        server = ThreadingHTTPServer(("0.0.0.0", PORTA_WEB), Handler)
    except OSError as exc:
        if porta_aberta(PORTA_WEB):
            print(
                "Kanban ja esta no ar (porta %s). Instancia unica — nada novo foi iniciado." % PORTA_WEB,
                flush=True,
            )
            raise SystemExit(0) from exc
        print("ERRO ao abrir porta %s: %s" % (PORTA_WEB, exc), flush=True)
        raise SystemExit(1) from exc
    SERVER_WEB = server
    print("Kanban web em http://127.0.0.1:%s/kanban-ti-visivel-demo/" % PORTA_WEB, flush=True)
    print("Compartilhado: Parametros -> Gerar chave e ativar.", flush=True)
    print("Instancia unica nesta porta. Novo Subir Kanban so reabre o navegador.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Encerrando...", flush=True)
    finally:
        parar_compartilhado()
        server.server_close()
        SERVER_WEB = None


if __name__ == "__main__":
    main()
