"""O que vai para o Postgres quando há várias réplicas. Roda só com um Postgres de teste:

    AGENTS_TEST_DATABASE_URL=postgresql://usuario:senha@localhost:5432/postgres uv run pytest tests/test_postgres.py

Cada teste usa um banco novo, apagado no fim. Com a mesma variável, a suíte inteira roda no
Postgres (execuções, conversas, trace e memória no banco).
"""
import os

import pytest

URL = os.environ.get("AGENTS_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="sem AGENTS_TEST_DATABASE_URL")


@pytest.fixture
def banco(database_url):
    return database_url


def test_memoria_e_compartilhada_entre_replicas(banco):
    from app.especialistas.memoria import PostgresMemory

    uma, outra = PostgresMemory(banco), PostgresMemory(banco)
    pedido = {"kind": "request", "parts": [{"part_kind": "user-prompt", "content": "extraia"}]}
    uma.append("ana", "conv-1", "extrator", "doc", [], [pedido])
    assert outra.load("ana", "conv-1", "extrator", "doc") == [pedido]
    assert outra.load("bruno", "conv-1", "extrator", "doc") == []


def test_execucoes_conversas_e_trace_no_banco(banco):
    from app.orquestrador.execucoes import PostgresConversations, PostgresRuns

    runs, outra_replica = PostgresRuns(banco), PostgresRuns(banco)
    conversa = PostgresConversations(banco).create("ana")
    run = runs.create("ana", "pergunta", "Qual a maior margem?", conversa.id)
    run.estado = "completa"
    runs.save(run)
    assert outra_replica.get(run.id, "ana").estado == "completa"
    assert outra_replica.get(run.id, "bruno") is None
    assert [r.id for r in outra_replica.of_conversation(conversa.id)] == [run.id]
    assert PostgresConversations(banco).get(conversa.id, "ana") == conversa
    assert PostgresConversations(banco).get(conversa.id, "bruno") is None

    runs.trace(run.id).add("delegando", agente="extrator")
    runs.save_messages(run.id, "extrator", "doc", [{"kind": "request"}])
    assert outra_replica.trace(run.id).read()[0]["agente"] == "extrator"
    assert outra_replica.messages(run.id, "extrator")[0]["mensagens"] == [{"kind": "request"}]
    assert outra_replica.messages(run.id, "redator") == []


async def test_checkpoint_e_tarefas_a2a_criam_as_tabelas(banco):
    from a2a.server.tasks import DatabaseTaskStore
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.nucleo.banco import asyncpg_url, psycopg_dsn

    async with AsyncPostgresSaver.from_conn_string(psycopg_dsn(banco)) as saver:
        await saver.setup()
    engine = create_async_engine(asyncpg_url(banco))
    store = DatabaseTaskStore(engine)
    await store.initialize()
    await engine.dispose()

    import psycopg
    with psycopg.connect(psycopg_dsn(banco)) as connection:
        tabelas = {row[0] for row in connection.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname='public'").fetchall()}
    assert {"checkpoints", "tasks", "memoria", "execucoes", "passos", "conversas"} <= tabelas
