"""As migrações do Alembic criam exatamente as tabelas de app/dados/modelos.py. Mudou um modelo
sem gerar a migração: este teste falha (rode `uv run alembic revision --autogenerate -m "..."`)."""
from alembic import command
from alembic.config import Config

from app.dados.banco import _RAIZ, upgrade


def test_migracoes_batem_com_os_modelos(tmp_path):
    url = f"sqlite+aiosqlite:///{(tmp_path / 'migrado.db').as_posix()}"
    upgrade(url)
    config = Config(str(_RAIZ / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    command.check(config)  # levanta se o autogenerate acharia diferença
