"""Engine e sessões assíncronas. A API e os dois workers usam o mesmo módulo."""
import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.infra.modelos import Base

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def configure(database_url: str | None = None) -> AsyncEngine:
    """Cria (ou recria) o engine. Os testes chamam com SQLite em memória."""
    global _engine, _sessionmaker
    url = database_url or get_settings().database_url
    kwargs: dict = {}
    if url.startswith("sqlite") and ":memory:" in url:
        # Uma única conexão compartilhada, senão cada sessão veria um banco vazio.
        kwargs = {"poolclass": StaticPool, "connect_args": {"check_same_thread": False}}
    else:
        kwargs = {"pool_pre_ping": True}
    _engine = create_async_engine(url, **kwargs)
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def _maker() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        configure()
    assert _sessionmaker is not None
    return _sessionmaker


async def init_db() -> None:
    """Cria as tabelas se não existirem. API e workers sobem juntos e podem tentar criar ao
    mesmo tempo; quem perder a corrida tenta de novo e encontra as tabelas prontas."""
    if _engine is None:
        configure()
    assert _engine is not None
    for attempt in range(1, 11):
        try:
            async with _engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
                await conn.run_sync(_add_missing_columns)
            return
        except Exception:
            if attempt == 10:
                raise
            await asyncio.sleep(2)


def _add_missing_columns(conn) -> None:
    """`create_all` cria tabela que falta, mas não coluna nova em tabela que já existe. Uma base de
    antes dos domínios ganha as colunas novas aqui (nulas ou com o padrão), em Postgres e SQLite."""
    inspector = inspect(conn)
    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue
        existing = {column["name"] for column in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing:
                continue
            kind = column.type.compile(dialect=conn.dialect)
            default = column.default.arg if column.default is not None and not callable(column.default.arg) else None
            clause = f" DEFAULT '{default}'" if isinstance(default, str) else ""
            conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {column.name} {kind}{clause}'))


async def dispose() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


async def get_session() -> AsyncIterator[AsyncSession]:
    """Dependência do FastAPI."""
    async with _maker()() as session:
        yield session


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Sessão com commit/rollback automáticos, usada pelos workers."""
    async with _maker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
