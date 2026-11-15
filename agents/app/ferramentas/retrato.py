"""O retrato da base de um usuário, lido pelo servidor MCP com a identidade "sistema" (que na
política só tem tools de leitura), e a conferência das citações direto no indexador. É o que a
avaliação compara com o gabarito: usado pelos scripts e pelo orquestrador ao fim de um relatório."""
from datetime import UTC, datetime
from typing import Any

import httpx

from app.config import Settings
from app.nucleo import mcp
from app.nucleo.tokens import Identity


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
