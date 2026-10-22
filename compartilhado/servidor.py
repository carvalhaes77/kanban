# -*- coding: utf-8 -*-
"""Serviço opcional do Kanban compartilhado. Python padrão + SQLite. Off por padrão."""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PORTA = 8777
RAIZ = Path(__file__).resolve().parent
DADOS = RAIZ / "dados"
BACKUP = RAIZ / "backup"
DB = DADOS / "quadro.sqlite"
STATUS = DADOS / "status.json"
HOST_KEY = DADOS / "chave-host.txt"
ONLINE_TTL = 45
BACKUP_A_CADA = 300
BACKUP_RETENCAO_DIAS = 7  # cópias com mais de 7 dias saem; as dos últimos 7 dias ficam
MAX_USUARIOS = 15
COLUNAS = ("backlog", "priorizado", "execucao", "aguardando", "concluido")
# Ping do quadro: limites e padrão (ms). Host ajusta; todos os clientes usam o valor da sala.
SYNC_PING_MIN_ABS = 800
SYNC_PING_MAX_ABS = 30000
SYNC_PING_MIN_PADRAO = 1000
SYNC_PING_MAX_PADRAO = 5000
SYNC_PING_PADRAO = 2500

travas = threading.RLock()
_ultimo_backup = 0.0


def agora_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def garantir_pastas() -> None:
    DADOS.mkdir(parents=True, exist_ok=True)
    BACKUP.mkdir(parents=True, exist_ok=True)


def conectar() -> sqlite3.Connection:
    garantir_pastas()
    con = sqlite3.connect(DB, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def init_db() -> None:
    with travas:
        con = conectar()
        try:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta (
                  chave TEXT PRIMARY KEY,
                  valor TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS usuarios (
                  id INTEGER PRIMARY KEY,
                  nome TEXT NOT NULL,
                  papel TEXT NOT NULL,
                  ultimo_visto TEXT NOT NULL,
                  cliente_id TEXT UNIQUE
                );
                CREATE TABLE IF NOT EXISTS cards (
                  id TEXT PRIMARY KEY,
                  column_id TEXT NOT NULL,
                  titulo TEXT NOT NULL,
                  solicitante TEXT NOT NULL,
                  prioridade TEXT NOT NULL,
                  criado_em TEXT NOT NULL,
                  criado_por TEXT NOT NULL,
                  aguardando_desde TEXT,
                  ordem INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS historico (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  card_id TEXT NOT NULL,
                  em TEXT NOT NULL,
                  quem TEXT NOT NULL,
                  de_coluna TEXT,
                  para_coluna TEXT,
                  detalhe TEXT
                );
                CREATE TABLE IF NOT EXISTS arquivados (
                  id TEXT PRIMARY KEY,
                  titulo TEXT NOT NULL,
                  solicitante TEXT NOT NULL,
                  prioridade TEXT NOT NULL,
                  criado_em TEXT NOT NULL,
                  criado_por TEXT NOT NULL,
                  de_coluna TEXT NOT NULL,
                  descartado_em TEXT NOT NULL,
                  descartado_por TEXT NOT NULL
                );
                """
            )
            if con.execute("SELECT COUNT(*) AS c FROM meta WHERE chave='ativo'").fetchone()["c"] == 0:
                con.execute("INSERT INTO meta(chave, valor) VALUES('ativo', '1')")
            for chave_meta, valor_meta in (
                ("sync_ping_min_ms", str(SYNC_PING_MIN_PADRAO)),
                ("sync_ping_max_ms", str(SYNC_PING_MAX_PADRAO)),
                ("sync_ping_ms", str(SYNC_PING_PADRAO)),
            ):
                if con.execute("SELECT COUNT(*) AS c FROM meta WHERE chave=?", (chave_meta,)).fetchone()["c"] == 0:
                    con.execute("INSERT INTO meta(chave, valor) VALUES(?, ?)", (chave_meta, valor_meta))
            # Schema 2: cadeira = cliente_id; nome livre (pode repetir / corrigir)
            cols = {r[1] for r in con.execute("PRAGMA table_info(usuarios)").fetchall()}
            if "cliente_id" not in cols:
                con.execute("ALTER TABLE usuarios ADD COLUMN cliente_id TEXT")
            ver = con.execute("SELECT valor FROM meta WHERE chave='usuarios_schema'").fetchone()
            if not ver or ver["valor"] != "2":
                con.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS usuarios_v2 (
                      id INTEGER PRIMARY KEY,
                      nome TEXT NOT NULL,
                      papel TEXT NOT NULL,
                      ultimo_visto TEXT NOT NULL,
                      cliente_id TEXT NOT NULL UNIQUE
                    );
                    INSERT OR IGNORE INTO usuarios_v2(id, nome, papel, ultimo_visto, cliente_id)
                    SELECT id, nome, papel, ultimo_visto,
                           CASE WHEN cliente_id IS NOT NULL AND TRIM(cliente_id) != ''
                                THEN cliente_id ELSE ('legado-' || id) END
                    FROM usuarios;
                    DROP TABLE IF EXISTS usuarios;
                    ALTER TABLE usuarios_v2 RENAME TO usuarios;
                    INSERT OR REPLACE INTO meta(chave, valor) VALUES('usuarios_schema', '2');
                    """
                )
            con.commit()
        finally:
            con.close()
        STATUS.write_text(
            json.dumps(
                {
                    "ativo": True,
                    "porta": PORTA,
                    "maxUsuarios": MAX_USUARIOS,
                    "chaveDefinida": bool(chave_host()),
                    "aviso": "Compartilhado opcional. A chave é gerada no Kanban (Parâmetros), sem abrir pasta no Windows.",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )


def chave_host() -> str:
    garantir_pastas()
    if not HOST_KEY.exists():
        return ""
    return HOST_KEY.read_text(encoding="utf-8").strip()


def ips_desta_maquina() -> set[str]:
    import socket

    ips = {"127.0.0.1", "::1", "localhost"}
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            ip = info[4][0]
            if ip:
                ips.add(ip)
                ips.add(str(ip).replace("::ffff:", ""))
    except OSError:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ips.add(s.getsockname()[0])
    except OSError:
        pass
    return ips


def pedido_deste_pc(handler: BaseHTTPRequestHandler) -> bool:
    """True só se o pedido veio deste computador (não de outro na rede)."""
    ip = (handler.client_address[0] or "").replace("::ffff:", "")
    return ip in ips_desta_maquina()


def erro_host_local(handler: BaseHTTPRequestHandler) -> str | None:
    """Host sensível: tem que ser ESTE PC + chave correta. None = autorizado."""
    if not pedido_deste_pc(handler):
        return "Ações de host só neste computador (onde a sala foi ativada). Outro PC entra só como convidado."
    esperada = chave_host()
    if not esperada:
        return "Host ainda não definiu a chave neste PC."
    if (handler.headers.get("X-Host-Key") or "").strip() != esperada:
        return "Chave de host inválida."
    return None


def gravar_chave_host(chave: str) -> None:
    garantir_pastas()
    HOST_KEY.write_text(chave.strip(), encoding="utf-8")
    STATUS.write_text(
        json.dumps(
            {
                "ativo": True,
                "porta": PORTA,
                "maxUsuarios": MAX_USUARIOS,
                "chaveDefinida": True,
                "aviso": "Chave definida pelo host no Kanban.",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def limpar_offline(con: sqlite3.Connection) -> None:
    """Remove quem parou de dar ping — inclusive o host (ID 0), para poder reentrar."""
    limite = time.time() - ONLINE_TTL
    rows = con.execute("SELECT id, ultimo_visto FROM usuarios").fetchall()
    for row in rows:
        try:
            ts = datetime.fromisoformat(row["ultimo_visto"]).timestamp()
        except ValueError:
            ts = 0
        if ts < limite:
            con.execute("DELETE FROM usuarios WHERE id=?", (row["id"],))


def proximo_id(con: sqlite3.Connection) -> int:
    row = con.execute("SELECT COALESCE(MAX(id), -1) AS m FROM usuarios").fetchone()
    return int(row["m"]) + 1


def usuarios_online(con: sqlite3.Connection) -> list[dict]:
    limpar_offline(con)
    rows = con.execute(
        "SELECT id, nome, papel, ultimo_visto AS ultimoVisto, "
        "COALESCE(cliente_id, '') AS clienteId FROM usuarios ORDER BY id"
    ).fetchall()
    saida = []
    for r in rows:
        item = dict(r)
        cid = (item.get("clienteId") or "").strip()
        item["clienteIdCurto"] = cid[:8] if cid else ""
        saida.append(item)
    return saida


def cards_lista(con: sqlite3.Connection) -> list[dict]:
    rows = con.execute(
        "SELECT id, column_id AS column, titulo, solicitante, prioridade, "
        "criado_em AS criadoEm, criado_por AS criadoPor, aguardando_desde AS aguardandoDesde, ordem "
        "FROM cards ORDER BY ordem, criado_em"
    ).fetchall()
    return [dict(r) for r in rows]


def arquivados_lista(con: sqlite3.Connection) -> list[dict]:
    rows = con.execute(
        "SELECT id, titulo, solicitante, prioridade, "
        "criado_em AS criadoEm, criado_por AS criadoPor, "
        "de_coluna AS deColuna, descartado_em AS descartadoEm, descartado_por AS descartadoPor "
        "FROM arquivados ORDER BY descartado_em DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def historico_card(con: sqlite3.Connection, card_id: str) -> list[dict]:
    rows = con.execute(
        "SELECT em, quem, de_coluna AS deColuna, para_coluna AS paraColuna, detalhe "
        "FROM historico WHERE card_id=? ORDER BY id DESC LIMIT 30",
        (card_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def meta_int(con: sqlite3.Connection, chave: str, padrao: int, lo: int, hi: int) -> int:
    row = con.execute("SELECT valor FROM meta WHERE chave=?", (chave,)).fetchone()
    try:
        n = int(str(row["valor"]).strip()) if row else padrao
    except (TypeError, ValueError):
        n = padrao
    return max(lo, min(hi, n))


SALA_PARAMS_PADRAO = {
    "tituloPagina": "KANBAN COMPARTILHADO",
    "subtitulo": "Sala da equipe — personalizada pelo host",
    "regras": "Arraste o card. WIP: Priorizado 3 · Em execução 2.",
    "rodape": "Quadro compartilhado neste PC host.",
    "tituloModal": "Novo card (compartilhado)",
    "notaModal": "Entra no quadro da sala para todos.",
    "rotuloTitulo": "Título",
    "placeholderTitulo": "Ex.: Impressora Farmácia",
    "rotuloSolicitante": "Solicitante",
    "placeholderSolicitante": "Ex.: Ana",
    "botaoNovo": "+ Novo card",
    "botaoAdicionar": "Adicionar",
    "prefixoSolicitante": "Solicitante:",
    "fonte": '"Segoe UI", system-ui, sans-serif',
    "corCabecalho": "#1F497D",
    "corTextoCabecalho": "#FFFFFF",
    "corFundo": "#E8EDF2",
    "corTexto": "#1C2833",
    "corTextoSuave": "#3D4C5C",
    "corAlerta": "#C0392B",
    "corCard": "#FFFFFF",
    "corBacklog": "#FFE5E5",
    "corPriorizado": "#FFF2CC",
    "corExecucao": "#D5E8D4",
    "corAguardando": "#DAE8FC",
    "corConcluido": "#E1D5E7",
    "slaHoras": 24,
    "syncPingMinMs": SYNC_PING_MIN_PADRAO,
    "syncPingMaxMs": SYNC_PING_MAX_PADRAO,
    "syncPingMs": SYNC_PING_PADRAO,
}


def _texto_sala(valor, limite: int, padrao: str) -> str:
    if not isinstance(valor, str):
        return padrao
    t = valor.strip()[:limite]
    return t if t else padrao


def _cor_sala(valor, padrao: str) -> str:
    if isinstance(valor, str) and len(valor.strip()) == 7 and valor.strip().startswith("#"):
        return valor.strip().upper()
    return padrao


def normalizar_sala_params(origem: dict | None) -> dict:
    o = origem if isinstance(origem, dict) else {}
    base = dict(SALA_PARAMS_PADRAO)
    saida = {
        "tituloPagina": _texto_sala(o.get("tituloPagina"), 120, base["tituloPagina"]),
        "subtitulo": _texto_sala(o.get("subtitulo"), 180, base["subtitulo"]),
        "regras": _texto_sala(o.get("regras"), 220, base["regras"]),
        "rodape": _texto_sala(o.get("rodape"), 200, base["rodape"]),
        "tituloModal": _texto_sala(o.get("tituloModal"), 80, base["tituloModal"]),
        "notaModal": _texto_sala(o.get("notaModal"), 160, base["notaModal"]),
        "rotuloTitulo": _texto_sala(o.get("rotuloTitulo"), 40, base["rotuloTitulo"]),
        "placeholderTitulo": _texto_sala(o.get("placeholderTitulo"), 120, base["placeholderTitulo"]),
        "rotuloSolicitante": _texto_sala(o.get("rotuloSolicitante"), 40, base["rotuloSolicitante"]),
        "placeholderSolicitante": _texto_sala(o.get("placeholderSolicitante"), 80, base["placeholderSolicitante"]),
        "botaoNovo": _texto_sala(o.get("botaoNovo"), 40, base["botaoNovo"]),
        "botaoAdicionar": _texto_sala(o.get("botaoAdicionar"), 40, base["botaoAdicionar"]),
        "prefixoSolicitante": _texto_sala(o.get("prefixoSolicitante"), 40, base["prefixoSolicitante"]),
        "fonte": _texto_sala(o.get("fonte"), 80, base["fonte"]),
        "corCabecalho": _cor_sala(o.get("corCabecalho"), base["corCabecalho"]),
        "corTextoCabecalho": _cor_sala(o.get("corTextoCabecalho"), base["corTextoCabecalho"]),
        "corFundo": _cor_sala(o.get("corFundo"), base["corFundo"]),
        "corTexto": _cor_sala(o.get("corTexto"), base["corTexto"]),
        "corTextoSuave": _cor_sala(o.get("corTextoSuave"), base["corTextoSuave"]),
        "corAlerta": _cor_sala(o.get("corAlerta"), base["corAlerta"]),
        "corCard": _cor_sala(o.get("corCard"), base["corCard"]),
        "corBacklog": _cor_sala(o.get("corBacklog"), base["corBacklog"]),
        "corPriorizado": _cor_sala(o.get("corPriorizado"), base["corPriorizado"]),
        "corExecucao": _cor_sala(o.get("corExecucao"), base["corExecucao"]),
        "corAguardando": _cor_sala(o.get("corAguardando"), base["corAguardando"]),
        "corConcluido": _cor_sala(o.get("corConcluido"), base["corConcluido"]),
    }
    try:
        sla = int(o.get("slaHoras", base["slaHoras"]))
    except (TypeError, ValueError):
        sla = base["slaHoras"]
    saida["slaHoras"] = max(1, min(240, sla))

    def _ms(chave: str, padrao: int) -> int:
        try:
            return int(o.get(chave, padrao))
        except (TypeError, ValueError):
            return padrao

    mn = max(SYNC_PING_MIN_ABS, min(SYNC_PING_MAX_ABS, _ms("syncPingMinMs", base["syncPingMinMs"])))
    mx = max(mn, min(SYNC_PING_MAX_ABS, _ms("syncPingMaxMs", base["syncPingMaxMs"])))
    ms = max(mn, min(mx, _ms("syncPingMs", base["syncPingMs"])))
    saida["syncPingMinMs"] = mn
    saida["syncPingMaxMs"] = mx
    saida["syncPingMs"] = ms
    return saida


def ler_sala_params(con: sqlite3.Connection) -> dict:
    row = con.execute("SELECT valor FROM meta WHERE chave='sala_params'").fetchone()
    bruto = None
    if row and row["valor"]:
        try:
            bruto = json.loads(row["valor"])
        except json.JSONDecodeError:
            bruto = None
    if not isinstance(bruto, dict):
        bruto = {}
        # Migra ping antigo (meta solta) para dentro da sala
        bruto["syncPingMinMs"] = meta_int(
            con, "sync_ping_min_ms", SYNC_PING_MIN_PADRAO, SYNC_PING_MIN_ABS, SYNC_PING_MAX_ABS
        )
        bruto["syncPingMaxMs"] = meta_int(
            con, "sync_ping_max_ms", SYNC_PING_MAX_PADRAO, SYNC_PING_MIN_ABS, SYNC_PING_MAX_ABS
        )
        bruto["syncPingMs"] = meta_int(
            con, "sync_ping_ms", SYNC_PING_PADRAO, SYNC_PING_MIN_ABS, SYNC_PING_MAX_ABS
        )
    return normalizar_sala_params(bruto)


def gravar_sala_params(con: sqlite3.Connection, origem: dict | None) -> dict:
    sala = normalizar_sala_params(origem)
    con.execute(
        "INSERT OR REPLACE INTO meta(chave, valor) VALUES('sala_params', ?)",
        (json.dumps(sala, ensure_ascii=False),),
    )
    # Mantém chaves legadas de ping alinhadas (status antigo / ferramentas)
    con.execute(
        "INSERT OR REPLACE INTO meta(chave, valor) VALUES('sync_ping_min_ms', ?)",
        (str(sala["syncPingMinMs"]),),
    )
    con.execute(
        "INSERT OR REPLACE INTO meta(chave, valor) VALUES('sync_ping_max_ms', ?)",
        (str(sala["syncPingMaxMs"]),),
    )
    con.execute(
        "INSERT OR REPLACE INTO meta(chave, valor) VALUES('sync_ping_ms', ?)",
        (str(sala["syncPingMs"]),),
    )
    return sala


def sync_ping_cfg(con: sqlite3.Connection) -> dict:
    sala = ler_sala_params(con)
    return {
        "syncPingMinMs": sala["syncPingMinMs"],
        "syncPingMaxMs": sala["syncPingMaxMs"],
        "syncPingMs": sala["syncPingMs"],
    }


def snapshot(con: sqlite3.Connection) -> dict:
    sala = ler_sala_params(con)
    return {
        "ativo": True,
        "porta": PORTA,
        "maxUsuarios": MAX_USUARIOS,
        "usuarios": usuarios_online(con),
        "cards": cards_lista(con),
        "arquivados": arquivados_lista(con),
        "servidorEm": agora_iso(),
        "sala": sala,
        "syncPingMinMs": sala["syncPingMinMs"],
        "syncPingMaxMs": sala["syncPingMaxMs"],
        "syncPingMs": sala["syncPingMs"],
    }


def limpar_backups_antigos() -> int:
    """Remove cópias com mais de BACKUP_RETENCAO_DIAS (o de 8 dias sai; os de 7 ficam)."""
    BACKUP.mkdir(parents=True, exist_ok=True)
    limite = time.time() - (BACKUP_RETENCAO_DIAS * 86400)
    removidos = 0
    for arq in BACKUP.glob("quadro-*.sqlite"):
        try:
            if arq.stat().st_mtime < limite:
                arq.unlink()
                removidos += 1
        except OSError:
            pass
    return removidos


def talvez_backup(forcar: bool = False) -> str | None:
    """Copia o SQLite da sala para backup/. Retenção: últimos 7 dias."""
    global _ultimo_backup
    agora = time.time()
    if not forcar and agora - _ultimo_backup < BACKUP_A_CADA:
        return None
    if not DB.exists():
        return None
    BACKUP.mkdir(parents=True, exist_ok=True)
    nome = datetime.now().strftime("quadro-%Y%m%d-%H%M%S.sqlite")
    shutil.copy2(DB, BACKUP / nome)
    _ultimo_backup = agora
    limpar_backups_antigos()
    return nome


def listar_backups() -> list[dict]:
    limpar_backups_antigos()
    BACKUP.mkdir(parents=True, exist_ok=True)
    itens = []
    agora = time.time()
    for arq in sorted(BACKUP.glob("quadro-*.sqlite"), key=lambda p: p.stat().st_mtime, reverse=True):
        st = arq.stat()
        idade_dias = max(0, int((agora - st.st_mtime) // 86400))
        itens.append(
            {
                "arquivo": arq.name,
                "bytes": st.st_size,
                "em": datetime.fromtimestamp(st.st_mtime).astimezone().isoformat(timespec="seconds"),
                "idadeDias": idade_dias,
            }
        )
    return itens


def nome_backup_seguro(nome: str) -> Path | None:
    base = Path(str(nome or "")).name
    if not base.startswith("quadro-") or not base.endswith(".sqlite"):
        return None
    if "/" in str(nome) or "\\" in str(nome) or ".." in base:
        return None
    caminho = (BACKUP / base).resolve()
    if not str(caminho).startswith(str(BACKUP.resolve())):
        return None
    if not caminho.is_file():
        return None
    return caminho


def restaurar_arquivo_backup(caminho: Path) -> None:
    """Substitui o SQLite da sala pela cópia escolhida (cards + arquivados + meta)."""
    BACKUP.mkdir(parents=True, exist_ok=True)
    if DB.exists():
        # Guarda estado atual antes de restaurar (também entra na janela de 7 dias)
        nome = datetime.now().strftime("quadro-%Y%m%d-%H%M%S-antes-restore.sqlite")
        try:
            shutil.copy2(DB, BACKUP / nome)
        except OSError:
            pass
    shutil.copy2(caminho, DB)
    limpar_backups_antigos()


def json_body(handler: BaseHTTPRequestHandler) -> dict:
    tamanho = int(handler.headers.get("Content-Length") or 0)
    if tamanho <= 0:
        return {}
    bruto = handler.rfile.read(tamanho)
    if not bruto:
        return {}
    return json.loads(bruto.decode("utf-8"))


def responder(handler: BaseHTTPRequestHandler, codigo: int, payload: dict) -> None:
    corpo = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(codigo)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(corpo)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type, X-Host-Key, X-User-Id, X-User-Name")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
    handler.end_headers()
    handler.wfile.write(corpo)


def usuario_por_header(handler: BaseHTTPRequestHandler, con: sqlite3.Connection) -> dict | None:
    try:
        uid = int(handler.headers.get("X-User-Id") or "-1")
    except ValueError:
        return None
    nome = (handler.headers.get("X-User-Name") or "").strip()
    row = con.execute("SELECT id, nome, papel FROM usuarios WHERE id=?", (uid,)).fetchone()
    if not row:
        return None
    if nome and row["nome"].casefold() != nome.casefold():
        return None
    con.execute("UPDATE usuarios SET ultimo_visto=? WHERE id=?", (agora_iso(), uid))
    return dict(row)


class Handler(BaseHTTPRequestHandler):
    server_version = "KanbanCompartilhado/1.0"

    def log_message(self, fmt: str, *args) -> None:
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Host-Key, X-User-Id, X-User-Name")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:
        rota = urlparse(self.path).path
        with travas:
            con = conectar()
            try:
                if rota in ("/api/status", "/api/compartilhado/status"):
                    limpar_offline(con)
                    con.commit()
                    sala = ler_sala_params(con)
                    responder(
                        self,
                        200,
                        {
                            "ativo": True,
                            "porta": PORTA,
                            "maxUsuarios": MAX_USUARIOS,
                            "chaveDefinida": bool(chave_host()),
                            "online": len(usuarios_online(con)),
                            "mensagem": "Compartilhado ligado no host.",
                            "sala": sala,
                            "syncPingMinMs": sala["syncPingMinMs"],
                            "syncPingMaxMs": sala["syncPingMaxMs"],
                            "syncPingMs": sala["syncPingMs"],
                        },
                    )
                    return
                if rota in ("/api/quadro", "/api/compartilhado/quadro"):
                    limpar_offline(con)
                    con.commit()
                    talvez_backup(False)
                    responder(self, 200, snapshot(con))
                    return
                if rota.startswith("/api/historico/") or rota.startswith("/api/compartilhado/historico/"):
                    card_id = rota.rsplit("/", 1)[-1]
                    responder(self, 200, {"cardId": card_id, "historico": historico_card(con, card_id)})
                    return
                responder(self, 404, {"erro": "Rota não encontrada."})
            finally:
                con.close()

    def do_POST(self) -> None:
        rota = urlparse(self.path).path
        try:
            dados = json_body(self)
        except json.JSONDecodeError:
            responder(self, 400, {"erro": "JSON inválido."})
            return

        with travas:
            con = conectar()
            try:
                if rota in ("/api/entrar", "/api/compartilhado/entrar"):
                    nome = str(dados.get("nome") or "").strip()[:40]
                    papel = str(dados.get("papel") or "convidado").strip().lower()
                    chave = str(dados.get("chaveHost") or "").strip()
                    cliente_id = str(dados.get("clienteId") or "").strip()[:64]
                    esperada = chave_host()
                    if not nome:
                        responder(self, 400, {"erro": "Informe um nome."})
                        return
                    if not cliente_id or len(cliente_id) < 8:
                        responder(self, 400, {"erro": "Identificação do navegador ausente. Atualize a página."})
                        return
                    if not esperada:
                        responder(self, 503, {"erro": "Host ainda não gerou a chave nos Parâmetros."})
                        return
                    if chave != esperada:
                        responder(self, 403, {"erro": "Chave inválida. Peça a chave ao host."})
                        return

                    limpar_offline(con)
                    agora = agora_iso()
                    # Host = só neste PC. Outro computador NUNCA assume a cadeira 0, mesmo com a chave.
                    quer_host = papel == "host" and pedido_deste_pc(self)
                    mesmo = con.execute(
                        "SELECT id, nome, papel, cliente_id FROM usuarios WHERE cliente_id=?",
                        (cliente_id,),
                    ).fetchone()

                    # Mesmo navegador (cliente_id): mantém a cadeira; nome pode mudar à vontade
                    if mesmo:
                        if quer_host:
                            if mesmo["id"] != 0:
                                con.execute("DELETE FROM usuarios WHERE id=0")
                                con.execute("DELETE FROM usuarios WHERE cliente_id=?", (cliente_id,))
                                con.execute(
                                    "INSERT INTO usuarios(id, nome, papel, ultimo_visto, cliente_id) VALUES(?,?,?,?,?)",
                                    (0, nome, "host", agora, cliente_id),
                                )
                                uid, papel = 0, "host"
                            else:
                                con.execute(
                                    "UPDATE usuarios SET nome=?, papel=?, ultimo_visto=? WHERE id=0",
                                    (nome, "host", agora),
                                )
                                uid, papel = 0, "host"
                        else:
                            uid = int(mesmo["id"])
                            # Cadeira 0 só neste PC; se outro PC estava como host (legado), rebaixa
                            if uid == 0 and not pedido_deste_pc(self):
                                con.execute("DELETE FROM usuarios WHERE cliente_id=?", (cliente_id,))
                                uid = proximo_id(con)
                                if uid == 0:
                                    uid = 1
                                con.execute(
                                    "INSERT INTO usuarios(id, nome, papel, ultimo_visto, cliente_id) VALUES(?,?,?,?,?)",
                                    (uid, nome, "convidado", agora, cliente_id),
                                )
                                papel = "convidado"
                            else:
                                con.execute(
                                    "UPDATE usuarios SET nome=?, ultimo_visto=? WHERE cliente_id=?",
                                    (nome, agora, cliente_id),
                                )
                                papel = "host" if uid == 0 else "convidado"
                        con.commit()
                        responder(
                            self,
                            200,
                            {
                                "id": uid,
                                "nome": nome,
                                "papel": papel,
                                "clienteId": cliente_id,
                                "clienteIdCurto": cliente_id[:8],
                                "quadro": snapshot(con),
                            },
                        )
                        return

                    online = usuarios_online(con)
                    if len(online) >= MAX_USUARIOS:
                        responder(self, 429, {"erro": "Limite de %s pessoas no compartilhado." % MAX_USUARIOS})
                        return

                    # Novo navegador: organizador sempre toma a cadeira 0; convidado ganha próxima
                    if quer_host:
                        con.execute("DELETE FROM usuarios WHERE id=0")
                        uid, papel = 0, "host"
                    else:
                        uid = proximo_id(con)
                        if uid == 0:
                            uid = 1
                        papel = "convidado"
                    con.execute(
                        "INSERT INTO usuarios(id, nome, papel, ultimo_visto, cliente_id) VALUES(?,?,?,?,?)",
                        (uid, nome, papel, agora, cliente_id),
                    )
                    con.commit()
                    responder(
                        self,
                        200,
                        {
                            "id": uid,
                            "nome": nome,
                            "papel": papel,
                            "clienteId": cliente_id,
                            "clienteIdCurto": cliente_id[:8],
                            "quadro": snapshot(con),
                        },
                    )
                    return

                if rota in ("/api/sair", "/api/compartilhado/sair"):
                    user = usuario_por_header(self, con)
                    if user:
                        con.execute("DELETE FROM usuarios WHERE id=?", (user["id"],))
                        con.commit()
                    responder(self, 200, {"ok": True})
                    return

                if rota in ("/api/ping", "/api/compartilhado/ping"):
                    user = usuario_por_header(self, con)
                    if not user:
                        responder(self, 401, {"erro": "Sessão inválida. Entre de novo."})
                        return
                    con.commit()
                    responder(self, 200, snapshot(con))
                    return

                if rota in ("/api/cards", "/api/compartilhado/cards"):
                    user = usuario_por_header(self, con)
                    if not user:
                        responder(self, 401, {"erro": "Entre com um nome antes de criar card."})
                        return
                    titulo = str(dados.get("titulo") or "").strip()[:180]
                    # Solicitante = sempre o nome da sessão (host ou convidado); cliente não escolhe outro
                    solicitante = str(user["nome"] or "").strip()[:80]
                    prioridade = str(dados.get("prioridade") or "normal")
                    if prioridade not in ("normal", "alta", "baixa"):
                        prioridade = "normal"
                    coluna = str(dados.get("column") or "backlog")
                    if coluna not in COLUNAS:
                        coluna = "backlog"
                    if not titulo:
                        responder(self, 400, {"erro": "Título obrigatório."})
                        return
                    card_id = "c-" + uuid.uuid4().hex[:12]
                    agora = agora_iso()
                    ordem = con.execute("SELECT COALESCE(MAX(ordem),0)+1 AS o FROM cards").fetchone()["o"]
                    aguardando = agora if coluna == "aguardando" else None
                    con.execute(
                        "INSERT INTO cards(id, column_id, titulo, solicitante, prioridade, criado_em, criado_por, aguardando_desde, ordem) "
                        "VALUES(?,?,?,?,?,?,?,?,?)",
                        (card_id, coluna, titulo, solicitante, prioridade, agora, "%s-%s" % (user["id"], user["nome"]), aguardando, ordem),
                    )
                    con.execute(
                        "INSERT INTO historico(card_id, em, quem, de_coluna, para_coluna, detalhe) VALUES(?,?,?,?,?,?)",
                        (card_id, agora, "%s-%s" % (user["id"], user["nome"]), None, coluna, "criou"),
                    )
                    con.commit()
                    talvez_backup(False)
                    responder(self, 200, {"ok": True, "quadro": snapshot(con)})
                    return

                if rota in ("/api/mover", "/api/compartilhado/mover"):
                    user = usuario_por_header(self, con)
                    if not user:
                        responder(self, 401, {"erro": "Sessão inválida."})
                        return
                    card_id = str(dados.get("id") or "")
                    coluna = str(dados.get("column") or "")
                    if coluna not in COLUNAS:
                        responder(self, 400, {"erro": "Coluna inválida."})
                        return
                    card = con.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
                    if not card:
                        responder(self, 404, {"erro": "Card não encontrado."})
                        return
                    de = card["column_id"]
                    agora = agora_iso()
                    aguardando = card["aguardando_desde"]
                    if coluna == "aguardando" and de != "aguardando":
                        aguardando = agora
                    if coluna != "aguardando":
                        aguardando = None
                    con.execute(
                        "UPDATE cards SET column_id=?, aguardando_desde=? WHERE id=?",
                        (coluna, aguardando, card_id),
                    )
                    con.execute(
                        "INSERT INTO historico(card_id, em, quem, de_coluna, para_coluna, detalhe) VALUES(?,?,?,?,?,?)",
                        (card_id, agora, "%s-%s" % (user["id"], user["nome"]), de, coluna, "moveu"),
                    )
                    con.commit()
                    talvez_backup(False)
                    responder(self, 200, {"ok": True, "quadro": snapshot(con)})
                    return

                if rota in ("/api/arquivar", "/api/compartilhado/arquivar"):
                    user = usuario_por_header(self, con)
                    if not user:
                        responder(self, 401, {"erro": "Sessão inválida."})
                        return
                    card_id = str(dados.get("id") or "")
                    card = con.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
                    if not card:
                        responder(self, 404, {"erro": "Card não encontrado."})
                        return
                    agora = agora_iso()
                    quem = "%s-%s" % (user["id"], user["nome"])
                    con.execute(
                        "INSERT OR REPLACE INTO arquivados"
                        "(id, titulo, solicitante, prioridade, criado_em, criado_por, de_coluna, descartado_em, descartado_por) "
                        "VALUES(?,?,?,?,?,?,?,?,?)",
                        (
                            card["id"],
                            card["titulo"],
                            card["solicitante"],
                            card["prioridade"],
                            card["criado_em"],
                            card["criado_por"],
                            card["column_id"],
                            agora,
                            quem,
                        ),
                    )
                    con.execute("DELETE FROM cards WHERE id=?", (card_id,))
                    con.execute(
                        "INSERT INTO historico(card_id, em, quem, de_coluna, para_coluna, detalhe) VALUES(?,?,?,?,?,?)",
                        (card_id, agora, quem, card["column_id"], None, "descartou"),
                    )
                    con.commit()
                    talvez_backup(False)
                    responder(self, 200, {"ok": True, "quadro": snapshot(con)})
                    return

                if rota in ("/api/arquivados/excluir", "/api/compartilhado/arquivados/excluir"):
                    user = usuario_por_header(self, con)
                    if not user:
                        responder(self, 401, {"erro": "Sessão inválida."})
                        return
                    if not pedido_deste_pc(self) or (user["papel"] != "host" and user["id"] != 0):
                        responder(
                            self,
                            403,
                            {"erro": "Excluir arquivado só no PC host (onde a sala foi ativada)."},
                        )
                        return
                    card_id = str(dados.get("id") or "")
                    row = con.execute("SELECT id FROM arquivados WHERE id=?", (card_id,)).fetchone()
                    if not row:
                        responder(self, 404, {"erro": "Arquivado não encontrado."})
                        return
                    con.execute("DELETE FROM arquivados WHERE id=?", (card_id,))
                    con.execute("DELETE FROM historico WHERE card_id=?", (card_id,))
                    con.commit()
                    talvez_backup(False)
                    responder(self, 200, {"ok": True, "quadro": snapshot(con)})
                    return

                if rota in ("/api/arquivados/limpar", "/api/compartilhado/arquivados/limpar"):
                    user = usuario_por_header(self, con)
                    if not user:
                        responder(self, 401, {"erro": "Sessão inválida."})
                        return
                    if not pedido_deste_pc(self) or (user["papel"] != "host" and user["id"] != 0):
                        responder(
                            self,
                            403,
                            {"erro": "Limpar arquivados só no PC host (onde a sala foi ativada)."},
                        )
                        return
                    ids = [r["id"] for r in con.execute("SELECT id FROM arquivados").fetchall()]
                    con.execute("DELETE FROM arquivados")
                    for cid in ids:
                        con.execute("DELETE FROM historico WHERE card_id=?", (cid,))
                    con.commit()
                    talvez_backup(True)
                    responder(self, 200, {"ok": True, "quadro": snapshot(con)})
                    return

                if rota in ("/api/arquivados/copiar", "/api/compartilhado/arquivados/copiar"):
                    user = usuario_por_header(self, con)
                    if not user:
                        responder(self, 401, {"erro": "Sessão inválida."})
                        return
                    card_id = str(dados.get("id") or "")
                    orig = con.execute("SELECT * FROM arquivados WHERE id=?", (card_id,)).fetchone()
                    if not orig:
                        responder(self, 404, {"erro": "Arquivado não encontrado."})
                        return
                    novo_id = "c-" + uuid.uuid4().hex[:12]
                    agora = agora_iso()
                    quem = "%s-%s" % (user["id"], user["nome"])
                    ordem = con.execute("SELECT COALESCE(MAX(ordem),0)+1 AS o FROM cards").fetchone()["o"]
                    con.execute(
                        "INSERT INTO cards(id, column_id, titulo, solicitante, prioridade, criado_em, criado_por, aguardando_desde, ordem) "
                        "VALUES(?,?,?,?,?,?,?,?,?)",
                        (
                            novo_id,
                            "backlog",
                            orig["titulo"],
                            orig["solicitante"],
                            orig["prioridade"],
                            agora,
                            quem,
                            None,
                            ordem,
                        ),
                    )
                    con.execute(
                        "INSERT INTO historico(card_id, em, quem, de_coluna, para_coluna, detalhe) VALUES(?,?,?,?,?,?)",
                        (novo_id, agora, quem, None, "backlog", "cópia do arquivado"),
                    )
                    con.commit()
                    talvez_backup(False)
                    responder(self, 200, {"ok": True, "quadro": snapshot(con), "novoId": novo_id})
                    return

                if rota in ("/api/host/sala-params", "/api/compartilhado/host/sala-params"):
                    err_host = erro_host_local(self)
                    if err_host:
                        responder(self, 403, {"erro": err_host})
                        return
                    bruto = dados.get("sala") if isinstance(dados.get("sala"), dict) else dados
                    sala = gravar_sala_params(con, bruto if isinstance(bruto, dict) else {})
                    con.commit()
                    responder(self, 200, {"ok": True, "sala": sala, "quadro": snapshot(con)})
                    return

                if rota in ("/api/host/sync-ping", "/api/compartilhado/host/sync-ping"):
                    # Compat: ainda aceita, grava dentro de sala_params
                    err_host = erro_host_local(self)
                    if err_host:
                        responder(self, 403, {"erro": err_host})
                        return
                    atual = ler_sala_params(con)
                    try:
                        atual["syncPingMinMs"] = int(dados.get("syncPingMinMs") or atual["syncPingMinMs"])
                        atual["syncPingMaxMs"] = int(dados.get("syncPingMaxMs") or atual["syncPingMaxMs"])
                        atual["syncPingMs"] = int(dados.get("syncPingMs") or atual["syncPingMs"])
                    except (TypeError, ValueError):
                        responder(self, 400, {"erro": "Valores de sincronização inválidos."})
                        return
                    sala = gravar_sala_params(con, atual)
                    con.commit()
                    responder(
                        self,
                        200,
                        {
                            "ok": True,
                            "quadro": snapshot(con),
                            "syncPingMinMs": sala["syncPingMinMs"],
                            "syncPingMaxMs": sala["syncPingMaxMs"],
                            "syncPingMs": sala["syncPingMs"],
                        },
                    )
                    return

                if rota in ("/api/host/exportar", "/api/compartilhado/host/exportar"):
                    err_host = erro_host_local(self)
                    if err_host:
                        responder(self, 403, {"erro": err_host})
                        return
                    limpar_offline(con)
                    con.commit()
                    responder(
                        self,
                        200,
                        {
                            "projeto": "kanban-compartilhado",
                            "exportadoEm": agora_iso(),
                            "quadro": snapshot(con),
                        },
                    )
                    return

                if rota in ("/api/host/importar", "/api/compartilhado/host/importar"):
                    err_host = erro_host_local(self)
                    if err_host:
                        responder(self, 403, {"erro": err_host})
                        return
                    quadro = dados.get("quadro") if isinstance(dados.get("quadro"), dict) else dados
                    cards = quadro.get("cards") if isinstance(quadro, dict) else None
                    if not isinstance(cards, list):
                        responder(self, 400, {"erro": "Arquivo sem lista de cards."})
                        return
                    arquivados = quadro.get("arquivados") if isinstance(quadro, dict) else None
                    if arquivados is None and isinstance(dados.get("arquivados"), list):
                        arquivados = dados.get("arquivados")
                    if not isinstance(arquivados, list):
                        arquivados = []
                    talvez_backup(True)
                    con.execute("DELETE FROM historico")
                    con.execute("DELETE FROM cards")
                    con.execute("DELETE FROM arquivados")
                    ordem = 0
                    for item in cards:
                        if not isinstance(item, dict):
                            continue
                        titulo = str(item.get("titulo") or "").strip()[:180]
                        if not titulo:
                            continue
                        coluna = str(item.get("column") or item.get("columnId") or "backlog")
                        if coluna not in COLUNAS:
                            coluna = "backlog"
                        prioridade = str(item.get("prioridade") or "normal")
                        if prioridade not in ("normal", "alta", "baixa"):
                            prioridade = "normal"
                        card_id = str(item.get("id") or ("c-" + uuid.uuid4().hex[:12]))
                        criado_em = str(item.get("criadoEm") or agora_iso())
                        criado_por = str(item.get("criadoPor") or "0-host")
                        solicitante = str(item.get("solicitante") or "Não informado")[:80]
                        aguardando = item.get("aguardandoDesde") if coluna == "aguardando" else None
                        con.execute(
                            "INSERT OR REPLACE INTO cards(id, column_id, titulo, solicitante, prioridade, criado_em, criado_por, aguardando_desde, ordem) "
                            "VALUES(?,?,?,?,?,?,?,?,?)",
                            (card_id, coluna, titulo, solicitante, prioridade, criado_em, criado_por, aguardando, ordem),
                        )
                        ordem += 1
                    for item in arquivados:
                        if not isinstance(item, dict):
                            continue
                        titulo = str(item.get("titulo") or "").strip()[:180]
                        if not titulo:
                            continue
                        card_id = str(item.get("id") or ("a-" + uuid.uuid4().hex[:12]))
                        prioridade = str(item.get("prioridade") or "normal")
                        if prioridade not in ("normal", "alta", "baixa"):
                            prioridade = "normal"
                        de_coluna = str(item.get("deColuna") or item.get("de_coluna") or "")
                        con.execute(
                            "INSERT OR REPLACE INTO arquivados"
                            "(id, titulo, solicitante, prioridade, criado_em, criado_por, de_coluna, descartado_em, descartado_por) "
                            "VALUES(?,?,?,?,?,?,?,?,?)",
                            (
                                card_id,
                                titulo,
                                str(item.get("solicitante") or "Não informado")[:80],
                                prioridade,
                                str(item.get("criadoEm") or item.get("criado_em") or agora_iso()),
                                str(item.get("criadoPor") or item.get("criado_por") or "0-host")[:80],
                                de_coluna[:40],
                                str(item.get("descartadoEm") or item.get("descartado_em") or agora_iso()),
                                str(item.get("descartadoPor") or item.get("descartado_por") or "")[:80],
                            ),
                        )
                    sala_imp = quadro.get("sala") if isinstance(quadro, dict) else None
                    if isinstance(sala_imp, dict):
                        gravar_sala_params(con, sala_imp)
                    con.commit()
                    responder(self, 200, {"ok": True, "quadro": snapshot(con)})
                    return

                if rota in ("/api/host/apagar", "/api/compartilhado/host/apagar"):
                    err_host = erro_host_local(self)
                    if err_host:
                        responder(self, 403, {"erro": err_host})
                        return
                    con.execute("DELETE FROM historico")
                    con.execute("DELETE FROM cards")
                    con.execute("DELETE FROM arquivados")
                    con.commit()
                    talvez_backup(True)
                    responder(self, 200, {"ok": True, "quadro": snapshot(con)})
                    return

                if rota in ("/api/host/limpar-dados", "/api/compartilhado/host/limpar-dados"):
                    # Arquiva todos os cards do quadro (mesmo fluxo do Descartar); arquivados antigos ficam.
                    err_host = erro_host_local(self)
                    if err_host:
                        responder(self, 403, {"erro": err_host})
                        return
                    user = usuario_por_header(self, con)
                    quem = (
                        "%s-%s · Limpar dados" % (user["id"], user["nome"])
                        if user
                        else "0-host · Limpar dados"
                    )
                    talvez_backup(True)
                    agora = agora_iso()
                    cards = con.execute("SELECT * FROM cards ORDER BY ordem, id").fetchall()
                    n = 0
                    for card in cards:
                        con.execute(
                            "INSERT OR REPLACE INTO arquivados"
                            "(id, titulo, solicitante, prioridade, criado_em, criado_por, de_coluna, descartado_em, descartado_por) "
                            "VALUES(?,?,?,?,?,?,?,?,?)",
                            (
                                card["id"],
                                card["titulo"],
                                card["solicitante"],
                                card["prioridade"],
                                card["criado_em"],
                                card["criado_por"],
                                card["column_id"],
                                agora,
                                quem,
                            ),
                        )
                        con.execute(
                            "INSERT INTO historico(card_id, em, quem, de_coluna, para_coluna, detalhe) VALUES(?,?,?,?,?,?)",
                            (card["id"], agora, quem, card["column_id"], None, "limpar dados"),
                        )
                        con.execute("DELETE FROM cards WHERE id=?", (card["id"],))
                        n += 1
                    con.commit()
                    snap = snapshot(con)
                    snap["limpos"] = n
                    responder(self, 200, {"ok": True, "limpos": n, "quadro": snap})
                    return

                if rota in ("/api/host/padrao", "/api/compartilhado/host/padrao"):
                    err_host = erro_host_local(self)
                    if err_host:
                        responder(self, 403, {"erro": err_host})
                        return
                    # Zera a sala: cards + arquivados + visual padrão; pasta backup/ ilesa.
                    talvez_backup(True)
                    exemplos = dados.get("cards") if isinstance(dados.get("cards"), list) else []
                    con.execute("DELETE FROM historico")
                    con.execute("DELETE FROM cards")
                    con.execute("DELETE FROM arquivados")
                    gravar_sala_params(con, dict(SALA_PARAMS_PADRAO))
                    agora = agora_iso()
                    ordem = 0
                    for item in exemplos:
                        if not isinstance(item, dict):
                            continue
                        titulo = str(item.get("titulo") or "").strip()[:180]
                        if not titulo:
                            continue
                        coluna = str(item.get("column") or "backlog")
                        if coluna not in COLUNAS:
                            coluna = "backlog"
                        prioridade = str(item.get("prioridade") or "normal")
                        if prioridade not in ("normal", "alta", "baixa"):
                            prioridade = "normal"
                        card_id = str(item.get("id") or ("c-" + uuid.uuid4().hex[:12]))
                        solicitante = str(item.get("solicitante") or "Não informado")[:80]
                        aguardando = agora if coluna == "aguardando" else None
                        con.execute(
                            "INSERT INTO cards(id, column_id, titulo, solicitante, prioridade, criado_em, criado_por, aguardando_desde, ordem) "
                            "VALUES(?,?,?,?,?,?,?,?,?)",
                            (card_id, coluna, titulo, solicitante, prioridade, agora, "0-host", aguardando, ordem),
                        )
                        con.execute(
                            "INSERT INTO historico(card_id, em, quem, de_coluna, para_coluna, detalhe) VALUES(?,?,?,?,?,?)",
                            (card_id, agora, "0-host", None, coluna, "exemplo padrão"),
                        )
                        ordem += 1
                    con.commit()
                    responder(self, 200, {"ok": True, "quadro": snapshot(con)})
                    return

                if rota in ("/api/host/backups", "/api/compartilhado/host/backups"):
                    err_host = erro_host_local(self)
                    if err_host:
                        responder(self, 403, {"erro": err_host})
                        return
                    responder(
                        self,
                        200,
                        {
                            "ok": True,
                            "retencaoDias": BACKUP_RETENCAO_DIAS,
                            "backups": listar_backups(),
                            "aviso": "Cópias automáticas da sala neste PC. Após %s dias o arquivo sai da lista."
                            % BACKUP_RETENCAO_DIAS,
                        },
                    )
                    return

                if rota in ("/api/host/backup", "/api/compartilhado/host/backup"):
                    err_host = erro_host_local(self)
                    if err_host:
                        responder(self, 403, {"erro": err_host})
                        return
                    nome = talvez_backup(True)
                    responder(
                        self,
                        200,
                        {
                            "ok": True,
                            "arquivo": nome,
                            "retencaoDias": BACKUP_RETENCAO_DIAS,
                            "backups": listar_backups(),
                        },
                    )
                    return

                if rota in ("/api/host/backup/restaurar", "/api/compartilhado/host/backup/restaurar"):
                    err_host = erro_host_local(self)
                    if err_host:
                        responder(self, 403, {"erro": err_host})
                        return
                    caminho = nome_backup_seguro(str(dados.get("arquivo") or ""))
                    if not caminho:
                        responder(self, 404, {"erro": "Backup não encontrado."})
                        return
                    # Fecha a conexão atual, troca o arquivo, reabre
                    try:
                        con.close()
                    except sqlite3.Error:
                        pass
                    try:
                        restaurar_arquivo_backup(caminho)
                    except OSError as exc:
                        responder(self, 500, {"erro": "Não restaurrou o arquivo: %s" % exc})
                        return
                    con = conectar()
                    limpar_offline(con)
                    con.commit()
                    responder(
                        self,
                        200,
                        {
                            "ok": True,
                            "arquivo": caminho.name,
                            "quadro": snapshot(con),
                            "aviso": "Sala restaurada por completo (cards + arquivados + parâmetros da época do backup).",
                        },
                    )
                    return

                responder(self, 404, {"erro": "Rota não encontrada."})
            finally:
                con.close()

    def do_DELETE(self) -> None:
        self.do_POST()


def main() -> None:
    # Se abriu com console (atalho /MIN), o titulo ajuda a achar no Alt+Tab — nao mexe em outros Python.
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleTitleW("Kanban Compartilhado (porta 8777)")
        except Exception:
            pass
    garantir_pastas()
    init_db()
    chave = chave_host()
    (DADOS / "LEIA-HOST.txt").write_text(
        "A chave é gerada no Kanban (Parâmetros → Gerar chave e ativar).\n"
        "Não abra pastas no Explorer para copiar chave.\n\n"
        "Chave atual neste host:\n%s\n\n"
        "Passe essa mesma chave aos convidados. Host entra como ID 0.\n"
        "Limite recomendado: %s pessoas.\n" % (chave or "(ainda não definida)", MAX_USUARIOS),
        encoding="utf-8",
    )
    server = ThreadingHTTPServer(("0.0.0.0", PORTA), Handler)
    print("Compartilhado ON em http://127.0.0.1:%s" % PORTA)
    print("Chave definida:" , "sim" if chave else "não (gere nos Parâmetros do Kanban)")
    print("SQLite em: %s" % DB)
    print("Backup em: %s" % BACKUP)
    print("Ctrl+C para encerrar.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nEncerrando compartilhado...")
    finally:
        talvez_backup(True)
        STATUS.write_text(json.dumps({"ativo": False, "porta": PORTA}, ensure_ascii=False), encoding="utf-8")
        server.server_close()


if __name__ == "__main__":
    main()
