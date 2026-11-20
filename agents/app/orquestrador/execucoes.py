"""As execuções, as conversas e o trace de cada execução, em arquivos na pasta de dados.

    data/execucoes/<id>.json         o estado da execução (sem o token do usuário)
    data/execucoes/<id>.jsonl        o trace: uma linha por passo, inclusive o raciocínio do modelo
    data/execucoes/<id>/<n>-<agente>.json   as mensagens completas de cada agente nesta execução
    data/conversas/<id>.json         a conversa (dono e criação); os turnos são as execuções dela

O trace é local de propósito. Ele traz argumentos de tools, que contêm texto dos documentos;
mandar isso a um painel de fora é uma decisão de quem opera, não o padrão.
"""
import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app import pacotes

log = logging.getLogger("agentes.trace")
_LOG_CHARS = 300

KINDS = ("livre", "pergunta", "relatorio", "relatorio_documento", "auto", "fora_do_escopo")  # auto: mensagem de conversa, até o orquestrador dizer o que é
RUNNING = ("na_fila", "em_andamento")


def _curto(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = text if len(text) <= _LOG_CHARS else text[:_LOG_CHARS] + "…"
    return json.dumps(text, ensure_ascii=False) if (" " in text or not text) else text


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Run:
    id: str
    user_id: str
    tipo: str  # livre | pergunta | relatorio | relatorio_documento | auto (vira um dos outros na primeira decisão)
    pedido: str
    doc_id: str | None = None  # no relatório de um documento: o doc_id dele
    # A coleção do pedido e o domínio dela (dominios/<id>/). Sem coleção: o domínio padrão e todos os
    # documentos do usuário, como antes dos pacotes de domínio.
    colecao_id: str | None = None
    dominio: str = pacotes.PADRAO
    conversa_id: str = ""  # a conversa do usuário a que este pedido pertence
    estado: str = "na_fila"  # na_fila | em_andamento | completa | incompleta | falhou | interrompida
    criada_em: str = field(default_factory=now)
    iniciada_em: str | None = None
    terminada_em: str | None = None
    rodadas: int = 0  # quantas vezes o orquestrador rodou (1 + as vezes em que a conferência pediu mais)
    agentes_digest: str = ""  # o retrato dos arquivos de agente que esta execução usou
    mensagem: str = ""  # a mensagem final do orquestrador
    resultado: dict[str, Any] | None = None
    pendencias: list[dict[str, Any]] = field(default_factory=list)
    uso: dict[str, Any] = field(default_factory=dict)
    erro: str | None = None
    avaliacao: dict[str, Any] | None = None  # relatório completo: a nota contra o gabarito


class Trace:
    def __init__(self, path: Path) -> None:
        self.path = path

    def add(self, tipo: str, **fields: Any) -> None:
        line = json.dumps({"em": now(), "tipo": tipo, **fields}, ensure_ascii=False)
        with self.path.open("a", encoding="utf-8") as file:
            file.write(line + "\n")
        # O mesmo passo, numa linha de log chave=valor: no Grafana, os passos de uma execução
        # aparecem no Loki ao lado do trace dela.
        detalhes = " ".join(f"{key}={_curto(value)}" for key, value in fields.items() if value not in (None, "", [], {}))
        log.info("passo=%s execucao=%s %s", tipo, self.path.stem, detalhes)

    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]


class Runs:
    def __init__(self, data_dir: Path) -> None:
        self.folder = data_dir / "execucoes"
        self.folder.mkdir(parents=True, exist_ok=True)
        self._runs: dict[str, Run] = {}
        for path in self.folder.glob("*.json"):
            try:
                run = Run(**json.loads(path.read_text(encoding="utf-8")))
            except (ValueError, TypeError):
                continue
            if run.estado in RUNNING:  # o serviço caiu com ela no meio
                run.estado, run.erro = "interrompida", "o serviço foi reiniciado durante a execução"
                self._write(run)
            self._runs[run.id] = run

    def _write(self, run: Run) -> None:
        (self.folder / f"{run.id}.json").write_text(json.dumps(asdict(run), ensure_ascii=False, indent=1),
                                                    encoding="utf-8")

    def create(self, user_id: str, tipo: str, pedido: str, conversa_id: str = "", colecao_id: str | None = None,
               dominio: str | None = None) -> Run:
        run = Run(id=f"exec-{uuid.uuid4().hex[:12]}", user_id=user_id, tipo=tipo, pedido=pedido,
                  conversa_id=conversa_id, colecao_id=colecao_id, dominio=dominio or pacotes.PADRAO)
        self._runs[run.id] = run
        self._write(run)
        return run

    def save(self, run: Run) -> None:
        self._write(run)

    def get(self, run_id: str, user_id: str) -> Run | None:
        """Só o dono vê a execução: para outro usuário ela não existe."""
        run = self._runs.get(run_id)
        return run if run is not None and run.user_id == user_id else None

    def of(self, user_id: str) -> list[Run]:
        return sorted((run for run in self._runs.values() if run.user_id == user_id),
                      key=lambda run: run.criada_em, reverse=True)

    def of_conversation(self, conversa_id: str) -> list[Run]:
        """Os turnos de uma conversa, do primeiro ao último."""
        return sorted((run for run in self._runs.values() if run.conversa_id == conversa_id),
                      key=lambda run: run.criada_em)

    def trace(self, run_id: str) -> Trace:
        return Trace(self.folder / f"{run_id}.jsonl")

    def save_messages(self, run_id: str, agente: str, doc_id: str | None, mensagens: list[dict[str, Any]]) -> None:
        """As mensagens completas de um agente: o pedido, o raciocínio, cada tool com os argumentos e
        o resultado, e a resposta. Têm texto dos documentos: ficam só aqui."""
        folder = self.folder / run_id
        folder.mkdir(exist_ok=True)
        number = len(list(folder.glob("*.json"))) + 1
        name = f"{number:03d}-{agente}" + (f"-{doc_id[:8]}" if doc_id else "")
        (folder / f"{name}.json").write_text(json.dumps(
            {"agente": agente, "doc_id": doc_id, "em": now(), "mensagens": mensagens}, ensure_ascii=False, indent=1),
            encoding="utf-8")

    def messages(self, run_id: str, agente: str | None = None) -> list[dict[str, Any]]:
        folder = self.folder / run_id
        if not folder.is_dir():
            return []
        found = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(folder.glob("*.json"))]
        return [entry for entry in found if agente is None or entry["agente"] == agente]


@dataclass
class Conversation:
    id: str
    user_id: str
    criada_em: str = field(default_factory=now)


class Conversations:
    """As conversas de cada usuário. O histórico de uma conversa fica no checkpoint do grafo (o que
    o orquestrador lembra) e nas execuções dela (o que foi pedido e respondido)."""

    def __init__(self, data_dir: Path) -> None:
        self.folder = data_dir / "conversas"
        self.folder.mkdir(parents=True, exist_ok=True)

    def create(self, user_id: str) -> Conversation:
        conversation = Conversation(id=f"conv-{uuid.uuid4().hex[:12]}", user_id=user_id)
        (self.folder / f"{conversation.id}.json").write_text(json.dumps(asdict(conversation)), encoding="utf-8")
        return conversation

    def get(self, conversa_id: str, user_id: str) -> Conversation | None:
        """Só o dono vê e continua a conversa: para outro usuário ela não existe."""
        path = self.folder / f"{conversa_id}.json"
        if not conversa_id.startswith("conv-") or not path.is_file():
            return None
        conversation = Conversation(**json.loads(path.read_text(encoding="utf-8")))
        return conversation if conversation.user_id == user_id else None

    def of(self, user_id: str) -> list[Conversation]:
        found = [Conversation(**json.loads(path.read_text(encoding="utf-8"))) for path in self.folder.glob("conv-*.json")]
        return sorted((c for c in found if c.user_id == user_id), key=lambda c: c.criada_em, reverse=True)


# --- Postgres: o mesmo, para várias réplicas do orquestrador ------------------------------------
# Com `DATABASE_URL` de Postgres, execuções, trace, mensagens e conversas ficam no banco: qualquer
# réplica responde a GET /execucoes/<id> e continua uma conversa começada em outra.


def _json(value: Any) -> Any:
    from psycopg.types.json import Jsonb

    return Jsonb(value)


class PostgresTrace(Trace):
    def __init__(self, url: str, run_id: str) -> None:
        self._url, self._run_id = url, run_id
        self.path = Path(run_id)  # o log usa o nome da execução, como no arquivo

    def add(self, tipo: str, **fields: Any) -> None:
        from app.nucleo.banco import conexao

        entry = {"em": now(), "tipo": tipo, **fields}
        with conexao(self._url) as connection:
            connection.execute("INSERT INTO passos (execucao_id, dados) VALUES (%s, %s)",
                               (self._run_id, _json(json.loads(json.dumps(entry, default=str)))))
        detalhes = " ".join(f"{key}={_curto(value)}" for key, value in fields.items() if value not in (None, "", [], {}))
        log.info("passo=%s execucao=%s %s", tipo, self._run_id, detalhes)

    def read(self) -> list[dict[str, Any]]:
        from app.nucleo.banco import conexao

        with conexao(self._url) as connection:
            rows = connection.execute("SELECT dados FROM passos WHERE execucao_id=%s ORDER BY id",
                                      (self._run_id,)).fetchall()
        return [row[0] for row in rows]


class PostgresRuns(Runs):
    """As execuções no banco. Diferente dos arquivos, a subida não marca como interrompidas as que
    estão rodando: com várias réplicas, podem estar rodando em outra."""

    def __init__(self, url: str) -> None:
        self._url = url
        self._runs = {}

    def _write(self, run: Run) -> None:
        from app.nucleo.banco import conexao

        with conexao(self._url) as connection:
            connection.execute(
                "INSERT INTO execucoes (id, user_id, conversa_id, criada_em, estado, dados) VALUES (%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (id) DO UPDATE SET estado=excluded.estado, dados=excluded.dados",
                (run.id, run.user_id, run.conversa_id, run.criada_em, run.estado, _json(asdict(run))))

    def _select(self, where: str, args: tuple, order: str = "criada_em") -> list[Run]:
        from app.nucleo.banco import conexao

        with conexao(self._url) as connection:
            rows = connection.execute(f"SELECT dados FROM execucoes WHERE {where} ORDER BY {order}", args).fetchall()
        return [Run(**row[0]) for row in rows]

    def create(self, user_id: str, tipo: str, pedido: str, conversa_id: str = "", colecao_id: str | None = None,
               dominio: str | None = None) -> Run:
        run = Run(id=f"exec-{uuid.uuid4().hex[:12]}", user_id=user_id, tipo=tipo, pedido=pedido,
                  conversa_id=conversa_id, colecao_id=colecao_id, dominio=dominio or pacotes.PADRAO)
        self._write(run)
        return run

    def get(self, run_id: str, user_id: str) -> Run | None:
        found = self._select("id=%s AND user_id=%s", (run_id, user_id))
        return found[0] if found else None

    def of(self, user_id: str) -> list[Run]:
        return self._select("user_id=%s", (user_id,), "criada_em DESC")

    def of_conversation(self, conversa_id: str) -> list[Run]:
        return self._select("conversa_id=%s", (conversa_id,))

    def trace(self, run_id: str) -> Trace:
        return PostgresTrace(self._url, run_id)

    def save_messages(self, run_id: str, agente: str, doc_id: str | None, mensagens: list[dict[str, Any]]) -> None:
        from app.nucleo.banco import conexao

        entry = {"agente": agente, "doc_id": doc_id, "em": now(), "mensagens": mensagens}
        with conexao(self._url) as connection:
            connection.execute("INSERT INTO mensagens_agentes (execucao_id, agente, doc_id, dados) VALUES (%s,%s,%s,%s)",
                               (run_id, agente, doc_id, _json(json.loads(json.dumps(entry, default=str)))))

    def messages(self, run_id: str, agente: str | None = None) -> list[dict[str, Any]]:
        from app.nucleo.banco import conexao

        with conexao(self._url) as connection:
            rows = connection.execute(
                "SELECT dados FROM mensagens_agentes WHERE execucao_id=%s AND (%s::text IS NULL OR agente=%s) "
                "ORDER BY id", (run_id, agente, agente)).fetchall()
        return [row[0] for row in rows]


class PostgresConversations(Conversations):
    def __init__(self, url: str) -> None:
        self._url = url

    def create(self, user_id: str) -> Conversation:
        from app.nucleo.banco import conexao

        conversation = Conversation(id=f"conv-{uuid.uuid4().hex[:12]}", user_id=user_id)
        with conexao(self._url) as connection:
            connection.execute("INSERT INTO conversas VALUES (%s, %s, %s)",
                               (conversation.id, conversation.user_id, conversation.criada_em))
        return conversation

    def get(self, conversa_id: str, user_id: str) -> Conversation | None:
        from app.nucleo.banco import conexao

        with conexao(self._url) as connection:
            row = connection.execute("SELECT id, user_id, criada_em FROM conversas WHERE id=%s AND user_id=%s",
                                     (conversa_id, user_id)).fetchone()
        return Conversation(*row) if row else None

    def of(self, user_id: str) -> list[Conversation]:
        from app.nucleo.banco import conexao

        with conexao(self._url) as connection:
            rows = connection.execute("SELECT id, user_id, criada_em FROM conversas WHERE user_id=%s "
                                      "ORDER BY criada_em DESC", (user_id,)).fetchall()
        return [Conversation(*row) for row in rows]


def open_store(settings: Any) -> tuple[Runs, Conversations]:
    """Execuções e conversas: Postgres com `DATABASE_URL`, senão arquivos na pasta de dados."""
    from app.nucleo.banco import is_postgres

    if is_postgres(settings.database_url):
        return PostgresRuns(settings.database_url), PostgresConversations(settings.database_url)
    return Runs(settings.data_dir), Conversations(settings.data_dir)
