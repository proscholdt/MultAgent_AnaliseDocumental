"""O que os scripts têm em comum: entrar como um usuário do indexador e tirar um retrato da
base do servidor MCP, com a identidade "sistema" (que na política só tem tools de leitura)."""
import argparse
import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # rodar como `python scripts/x.py`, de agents/

from app.config import Settings, get_settings
from app.ferramentas.retrato import snapshot  # noqa: F401 - os scripts importam daqui
from app.nucleo.tokens import Identity

ROOT = Path(__file__).resolve().parents[1]


def arguments(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--usuario", default=os.environ.get("AVALIACAO_USUARIO"),
                        help="usuário do indexador, dono dos documentos (ou a variável AVALIACAO_USUARIO)")
    parser.add_argument("--senha", default=os.environ.get("AVALIACAO_SENHA"),
                        help="senha dele (ou a variável AVALIACAO_SENHA)")
    return parser


async def login(settings: Settings, username: str | None, password: str | None, run_id: str) -> Identity:
    if not username or not password:
        sys.exit("Informe --usuario e --senha (o usuário do indexador que enviou os documentos).")
    async with httpx.AsyncClient(base_url=settings.indexer_url, timeout=30) as http:
        response = await http.post("/auth/login", data={"username": username, "password": password})
        if response.status_code != 200:
            sys.exit(f"Login recusado pelo indexador ({response.status_code}).")
        body = response.json()
    return Identity(run_id=run_id, user_id=body["user_id"], user_token=body["access_token"])


def settings() -> Settings:
    return get_settings()
