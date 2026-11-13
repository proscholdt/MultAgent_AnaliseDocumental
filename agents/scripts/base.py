"""O que os scripts têm em comum: entrar como um usuário do indexador e tirar um retrato da
base do servidor MCP, com a identidade "sistema" (que na política só tem tools de leitura)."""
import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # rodar como `python scripts/x.py`, de agents/

from app import mcp
from app.config import Settings, get_settings
from app.tokens import Identity

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


async def _citations(settings: Settings, identity: Identity, documents: list[dict[str, Any]]) -> dict[str, Any]:
    """Confere, direto no indexador e sem passar pelo servidor MCP, que cada citação guardada
    existe literalmente no chunk citado. É a prova independente de que o portão funciona."""
    total, invalid = 0, []
    async with httpx.AsyncClient(base_url=settings.indexer_url, timeout=60,
                                 headers={"Authorization": f"Bearer {identity.user_token}"}) as http:
        for doc in documents:
            chunks: list[dict[str, Any]] = []
            while True:  # o indexador devolve os chunks em páginas de até 100
                response = await http.get(f"/api/documents/{doc['doc_id']}/chunks",
                                          params={"offset": len(chunks), "limit": 100})
                response.raise_for_status()
                page = response.json()
                chunks += page["items"]
                if len(chunks) >= page["total"] or not page["items"]:
                    break
            texts = {chunk["chunk_index"]: " ".join(chunk["text"].split()) for chunk in chunks}
            quoted = [(item["chunk_index"], item["citacao"]) for items in doc["analise"].values() for item in items
                      if item.get("citacao")]
            quoted += [(alert["detalhes"]["chunk_index"], alert["detalhes"]["citacao"]) for alert in doc["alertas"]
                       if alert["detalhes"].get("citacao")]
            for chunk_index, quote in quoted:
                total += 1
                if " ".join(quote.split()) not in texts.get(chunk_index, ""):
                    invalid.append({"arquivo": doc["arquivo"], "chunk_index": chunk_index, "citacao": quote[:120]})
    return {"total": total, "validas": total - len(invalid), "invalidas": invalid}


async def snapshot(settings: Settings, identity: Identity) -> dict[str, Any]:
    """Tudo o que a base tem para o usuário: extração completa, análise, alertas, respostas,
    relatório e pendências."""
    async with mcp.connect(settings, mcp.SYSTEM, identity) as server:
        listed = (await server.call("ler_extracao", {"doc_id": None, "detalhe": "completo"}))["documentos"]
        compact = {doc["doc_id"]: doc["extracao"] for doc in
                   (await server.call("ler_extracao", {"doc_id": None, "detalhe": "enxuto"}))["documentos"]}
        documents = []
        for doc in listed:
            documents.append({
                **doc,
                "extracao_enxuta": compact.get(doc["doc_id"]),
                "analise": (await server.call("ler_analise", {"doc_id": doc["doc_id"]}))["analise"],
                "alertas": (await server.call("ler_alertas", {"doc_id": doc["doc_id"]}))["alertas"]})
        result = {
            "gerado_em": datetime.now(UTC).isoformat(timespec="seconds"),
            "documentos": sorted(documents, key=lambda doc: doc["arquivo"]),
            "respostas": (await server.call("ler_respostas"))["respostas"],
            "relatorio": (await server.call("ler_relatorio")).get("relatorio"),
            "pendencias": (await server.call("pendencias"))["pendencias"],
        }
    result["citacoes"] = await _citations(settings, identity, result["documentos"])
    return result


def settings() -> Settings:
    return get_settings()
