"""Memória de um especialista na conversa do usuário.

Quando o orquestrador chama de novo o mesmo especialista, para o mesmo documento e na mesma
conversa, ele recebe de volta a própria conversa das vezes anteriores: o que pediu, o que leu,
o que tentou gravar e o que o servidor recusou. Não recomeça do zero.

A chave é (usuário, conversa, agente, documento). Outra conversa, outro usuário ou outro
documento começam limpos.

A memória dura a conversa inteira, então o tamanho é controlado:

- o resultado das tools das chamadas anteriores vira uma linha (o agente sabe o que leu e pode
  ler de novo; o conteúdo continua no documento e na base);
- o raciocínio das chamadas anteriores sai (ele está no trace e nas mensagens guardadas);
- acima do teto, saem as chamadas mais antigas, inteiras. A mais recente fica sempre.

O que vale de verdade continua na base do servidor MCP. A memória poupa releituras e dá
continuidade; não é fonte de valor.
"""
import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_KEEP_RESULT_CHARS = 300  # resultado de tool menor que isto fica inteiro


def _estimate_tokens(data: Any) -> int:
    return len(json.dumps(data, ensure_ascii=False)) // 4


def _shorten_result(part: dict[str, Any]) -> dict[str, Any]:
    content = part.get("content")
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    if len(text) <= _KEEP_RESULT_CHARS:
        return part
    outcome = "recusada" if part.get("outcome") == "failed" else "ok"
    note = (f"[resultado de {part.get('tool_name')} ({outcome}) encurtado na memória: {len(text)} caracteres. "
            f"Começava com: {text[:160]}… Se precisar do conteúdo, chame a tool de novo.]")
    return {**part, "content": note}


def _without_item_id(part: dict[str, Any]) -> dict[str, Any]:
    """Tira o id do item no provedor. Na API de Responses da OpenAI, a chamada de tool e o texto de
    um modelo que raciocinou trazem o id do item, e um item com id só é aceito de volta junto com o
    raciocínio que o precedeu: sem ele, a OpenAI recusa o pedido inteiro (erro 400, "provided
    without its required 'reasoning' item"). Como o raciocínio sai da memória, o id sai junto; a
    chamada continua ligada ao resultado pelo `tool_call_id`."""
    return {**part, "id": None} if part.get("id") else part


def shorten(run_messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Uma chamada anterior, encurtada: sem raciocínio (e sem os ids de item que dependem dele) e
    com os resultados longos de tools em uma linha."""
    out = []
    for message in run_messages:
        parts = []
        for part in message.get("parts", []):
            kind = part.get("part_kind")
            if kind == "thinking":
                continue
            if kind == "tool-return":
                parts.append(_shorten_result(part))
            elif kind in ("tool-call", "text"):
                parts.append(_without_item_id(part))
            else:
                parts.append(part)
        if parts:
            out.append({**message, "parts": parts})
    return out


def _is_run_start(message: dict[str, Any]) -> bool:
    return message.get("kind") == "request" and any(p.get("part_kind") == "user-prompt" for p in message.get("parts", []))


def split_runs(messages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """O histórico dividido em chamadas: cada uma começa no pedido (prompt do usuário)."""
    runs: list[list[dict[str, Any]]] = []
    for message in messages:
        if _is_run_start(message) or not runs:
            runs.append([])
        runs[-1].append(message)
    return runs


def without_unanswered_calls(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """O histórico sem as chamadas de tool que ficaram sem resposta. Uma chamada que caiu no meio
    (rede, tempo) deixa o pedido do modelo sem o resultado; com ele no histórico, o PydanticAI
    recusa a chamada seguinte ("unprocessed tool calls"), e o documento ficava travado (visto numa
    execução real, depois de um ReadError na busca)."""
    answered = {part.get("tool_call_id") for message in messages for part in message.get("parts", [])
                if part.get("part_kind") in ("tool-return", "retry-prompt")}
    out = []
    for message in messages:
        parts = [part for part in message.get("parts", [])
                 if part.get("part_kind") != "tool-call" or part.get("tool_call_id") in answered]
        if parts:
            out.append({**message, "parts": parts})
    return out


def compact(runs: list[list[dict[str, Any]]], max_tokens: int) -> list[dict[str, Any]]:
    """O histórico para a próxima chamada: todas as anteriores encurtadas e, acima do teto, sem as
    mais antigas. A última chamada guardada fica sempre. As chamadas de tool sem resposta saem."""
    shortened = [shorten(run) for run in runs if run]
    while len(shortened) > 1 and _estimate_tokens(shortened) > max_tokens:
        shortened.pop(0)
    return without_unanswered_calls([message for run in shortened for message in run])


class Memory:
    """Histórico por (usuário, conversa, agente, documento), em SQLite."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS memoria (usuario TEXT, conversa TEXT, agente TEXT, documento TEXT, "
            "mensagens TEXT, chamadas INTEGER, atualizada TEXT, PRIMARY KEY (usuario, conversa, agente, documento))")
        self._db.commit()

    def load(self, user_id: str, conversa: str, agente: str, doc_id: str | None) -> list[dict[str, Any]]:
        """O histórico bruto guardado: todas as chamadas anteriores, inteiras."""
        with self._lock:
            row = self._db.execute(
                "SELECT mensagens FROM memoria WHERE usuario=? AND conversa=? AND agente=? AND documento=?",
                (user_id, conversa, agente, doc_id or "")).fetchone()
        return json.loads(row[0]) if row else []

    def append(self, user_id: str, conversa: str, agente: str, doc_id: str | None,
               previous: list[dict[str, Any]], new: list[dict[str, Any]]) -> None:
        """Guarda o histórico bruto: o anterior inteiro mais as mensagens desta chamada. O
        encurtamento é feito na leitura (`compact`), para não se acumular a cada chamada e para a
        regra poder mudar sem perder o que foi guardado."""
        messages = previous + new
        with self._lock:
            self._db.execute(
                "INSERT INTO memoria VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (usuario, conversa, agente, documento) "
                "DO UPDATE SET mensagens=excluded.mensagens, chamadas=excluded.chamadas, atualizada=excluded.atualizada",
                (user_id, conversa, agente, doc_id or "", json.dumps(messages, ensure_ascii=False),
                 len(split_runs(messages)), datetime.now(UTC).isoformat(timespec="seconds")))
            self._db.commit()


class PostgresMemory:
    """A mesma memória no Postgres (`DATABASE_URL`): várias réplicas de um especialista leem e
    gravam o mesmo histórico."""

    def __init__(self, url: str) -> None:
        self._url = url

    def load(self, user_id: str, conversa: str, agente: str, doc_id: str | None) -> list[dict[str, Any]]:
        from app.nucleo.banco import conexao

        with conexao(self._url) as connection:
            row = connection.execute(
                "SELECT mensagens FROM memoria WHERE usuario=%s AND conversa=%s AND agente=%s AND documento=%s",
                (user_id, conversa, agente, doc_id or "")).fetchone()
        return row[0] if row else []

    def append(self, user_id: str, conversa: str, agente: str, doc_id: str | None,
               previous: list[dict[str, Any]], new: list[dict[str, Any]]) -> None:
        from psycopg.types.json import Jsonb

        from app.nucleo.banco import conexao

        messages = previous + new
        with conexao(self._url) as connection:
            connection.execute(
                "INSERT INTO memoria VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (usuario, conversa, agente, documento) DO UPDATE SET mensagens=excluded.mensagens, "
                "chamadas=excluded.chamadas, atualizada=excluded.atualizada",
                (user_id, conversa, agente, doc_id or "", Jsonb(messages), len(split_runs(messages)),
                 datetime.now(UTC).isoformat(timespec="seconds")))


def open_memory(settings: Any) -> "Memory | PostgresMemory":
    """A memória dos especialistas: Postgres com `DATABASE_URL`, senão SQLite na pasta de dados."""
    from app.nucleo.banco import is_postgres

    if is_postgres(settings.database_url):
        return PostgresMemory(settings.database_url)
    return Memory(settings.data_dir / "memoria.db")

