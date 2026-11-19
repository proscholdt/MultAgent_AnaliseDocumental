"""Postgres dos agentes: o que sai dos arquivos quando há várias réplicas.

Com `DATABASE_URL` de Postgres, ficam no banco o checkpoint das conversas (LangGraph), as
execuções e o trace delas, as conversas, a memória dos especialistas e as tarefas A2A. Sem ele,
tudo continua em arquivos e SQLite na pasta de dados, como antes.

As tabelas são poucas e de chave simples: criadas aqui com `CREATE TABLE IF NOT EXISTS`, sem
migrações. O checkpoint e as tarefas A2A são das bibliotecas, que criam as próprias tabelas.
"""
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

log = logging.getLogger(__name__)

TABELAS = (
    ("CREATE TABLE IF NOT EXISTS memoria (usuario TEXT, conversa TEXT, agente TEXT, documento TEXT, "
    "mensagens JSONB, chamadas INTEGER, atualizada TEXT, PRIMARY KEY (usuario, conversa, agente, documento))"),
    ("CREATE TABLE IF NOT EXISTS execucoes (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, conversa_id TEXT, "
    "criada_em TEXT NOT NULL, estado TEXT NOT NULL, dados JSONB NOT NULL)"),
    "CREATE INDEX IF NOT EXISTS execucoes_usuario ON execucoes (user_id, criada_em)",
    "CREATE INDEX IF NOT EXISTS execucoes_conversa ON execucoes (conversa_id, criada_em)",
    "CREATE TABLE IF NOT EXISTS passos (id BIGSERIAL PRIMARY KEY, execucao_id TEXT NOT NULL, dados JSONB NOT NULL)",
    "CREATE INDEX IF NOT EXISTS passos_execucao ON passos (execucao_id, id)",
    ("CREATE TABLE IF NOT EXISTS mensagens_agentes (id BIGSERIAL PRIMARY KEY, execucao_id TEXT NOT NULL, "
    "agente TEXT NOT NULL, doc_id TEXT, dados JSONB NOT NULL)"),
    "CREATE INDEX IF NOT EXISTS mensagens_execucao ON mensagens_agentes (execucao_id, id)",
    "CREATE TABLE IF NOT EXISTS conversas (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, criada_em TEXT NOT NULL)",
)


def is_postgres(url: str) -> bool:
    return url.startswith(("postgresql", "postgres:"))


def _resto(url: str) -> str:
    """O endereço sem o esquema: 'postgresql+asyncpg://u@h/db' → '//u@h/db'."""
    return url.partition(":")[2]


def psycopg_dsn(url: str) -> str:
    """O endereço no formato do psycopg (e do checkpoint do LangGraph): sem o driver do SQLAlchemy."""
    return f"postgresql:{_resto(url)}"


def asyncpg_url(url: str) -> str:
    """O endereço para o SQLAlchemy assíncrono (as tarefas A2A)."""
    return f"postgresql+asyncpg:{_resto(url)}"


@contextmanager
def conexao(url: str) -> Iterator[Any]:
    """Uma conexão curta, em transação: confirma no fim, desfaz se der erro. As operações daqui são
    poucas e pequenas por execução; um pool não compensaria a dependência a mais."""
    import psycopg

    with psycopg.connect(psycopg_dsn(url)) as connection:
        yield connection


def preparar(url: str) -> None:
    """Cria o banco (se não existe) e as tabelas. Chamado na subida de cada serviço."""
    import psycopg
    from psycopg import sql

    dsn = psycopg_dsn(url)
    nome = psycopg.conninfo.conninfo_to_dict(dsn).get("dbname")
    if nome and nome != "postgres":
        admin = psycopg.conninfo.make_conninfo(dsn, dbname="postgres")
        with psycopg.connect(admin, autocommit=True) as connection:
            if connection.execute("SELECT 1 FROM pg_database WHERE datname = %s", (nome,)).fetchone() is None:
                connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(nome)))
                log.info("banco %s criado", nome)
    with conexao(url) as connection:
        for comando in TABELAS:
            connection.execute(comando)
