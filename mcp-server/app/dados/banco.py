"""Banco do servidor MCP, via SQLAlchemy assíncrono.

- SQLite (desenvolvimento e testes): as tabelas são criadas com `create_all`.
- Postgres (produção, várias réplicas): o banco é criado se não existir e as migrações do
  Alembic (`migracoes/`) são aplicadas na subida. Mudou `app/dados/modelos.py`: gere uma
  migração (`uv run alembic revision --autogenerate -m "..."`).
"""
import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

log = logging.getLogger(__name__)
_RAIZ = Path(__file__).resolve().parents[2]


class Base(DeclarativeBase):
    pass


def is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


async def ensure_database(url: str) -> None:
    """Cria o banco do Postgres se ainda não existe (o volume do Postgres pode ser de antes)."""
    import asyncpg

    parsed = make_url(url)
    name = parsed.database
    dsn = parsed.set(drivername="postgresql", database="postgres").render_as_string(hide_password=False)
    connection = await asyncpg.connect(dsn)
    try:
        if not await connection.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", name):
            await connection.execute(f'CREATE DATABASE "{name}"')
            log.info("banco %s criado", name)
    finally:
        await connection.close()


def upgrade(url: str) -> None:
    """Aplica as migrações até a última. Roda numa thread: o env.py do Alembic tem o próprio laço."""
    from alembic import command
    from alembic.config import Config

    config = Config(str(_RAIZ / "alembic.ini"))
    config.set_main_option("script_location", str(_RAIZ / "migracoes"))
    config.set_main_option("version_locations", str(_RAIZ / "migracoes" / "versoes"))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(config, "head")


class Database:
    def __init__(self, url: str) -> None:
        if is_sqlite(url) and ":memory:" not in url:
            Path(url.split("///", 1)[1]).parent.mkdir(parents=True, exist_ok=True)
        self.url = url
        self.engine: AsyncEngine = create_async_engine(url, **({} if is_sqlite(url) else {"pool_pre_ping": True}))
        self._sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def init(self) -> None:
        from app.dados import modelos  # noqa: F401 - registra as tabelas no metadata

        if is_sqlite(self.url):
            async with self.engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            return
        await ensure_database(self.url)
        await asyncio.to_thread(upgrade, self.url)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Uma transação: confirma no fim, desfaz se houver erro."""
        async with self._sessions() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def close(self) -> None:
        await self.engine.dispose()
