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
from app.orquestrador.usuario import default_of

ROOT = Path(__file__).resolve().parents[1]


def arguments(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--usuario", default=os.environ.get("AVALIACAO_USUARIO"),
                        help="usuário do indexador, dono dos documentos (ou a variável AVALIACAO_USUARIO)")
    parser.add_argument("--senha", default=os.environ.get("AVALIACAO_SENHA"),
                        help="senha dele (ou a variável AVALIACAO_SENHA)")
    parser.add_argument("--colecao", default=os.environ.get("AVALIACAO_COLECAO"),
                        help="id da coleção (ou a variável AVALIACAO_COLECAO); sem ela, a coleção padrão do "
                        "usuário, a primeira do domínio financeiro")
    return parser


async def login(settings: Settings, username: str | None, password: str | None, run_id: str,
                colecao: str | None = None) -> Identity:
    """Entra como o usuário e resolve a coleção: a informada ou a padrão. As respostas e o relatório
    consolidado são de uma coleção, e o retrato só vê os documentos dela."""
    if not username or not password:
        sys.exit("Informe --usuario e --senha (o usuário do indexador que enviou os documentos).")
    async with httpx.AsyncClient(base_url=settings.indexer_url, timeout=30) as http:
        response = await http.post("/auth/login", data={"username": username, "password": password})
        if response.status_code != 200:
            sys.exit(f"Login recusado pelo indexador ({response.status_code}).")
        body = response.json()
        listed = await http.get("/api/colecoes", headers={"Authorization": f"Bearer {body['access_token']}"})
        collections = listed.json() if listed.status_code == 200 else []
    chosen = next((c for c in collections if c["id"] == colecao), None) if colecao else default_of(collections)
    if colecao and chosen is None:
        sys.exit(f"A coleção {colecao} não é deste usuário.")
    return Identity(run_id=run_id, user_id=body["user_id"], user_token=body["access_token"],
                    dominio=chosen["dominio"] if chosen else None, colecao_id=chosen["id"] if chosen else None)


def settings() -> Settings:
    return get_settings()
