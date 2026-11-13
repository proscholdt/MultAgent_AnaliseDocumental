"""Tools de leitura: o agente só alcança os documentos do usuário da execução."""
from tests.conftest import BRUNO, DOC_ANA, DOC_BRUNO, headers, payload


async def call(governance, name, arguments, **who):
    return await governance.call_tool(headers("leitor", **who), name, arguments)


async def test_documents_listed_are_only_those_of_the_user(governance):
    ana = payload(await call(governance, "listar_documentos", {}))["documentos"]
    assert ana == [{"doc_id": DOC_ANA, "arquivo": "Exemplo.pdf", "empresa": "Empresa Exemplo S.A.",
                    "periodo": "2022", "tipo": "Demonstrações financeiras anuais", "paginas": 40}]
    bruno = payload(await call(governance, "listar_documentos", {}, user_token="tok-bruno", user_id=BRUNO))
    assert [doc["doc_id"] for doc in bruno["documentos"]] == [DOC_BRUNO]


async def test_search_passes_the_filters_and_cuts_long_text(governance):
    result = payload(await call(governance, "buscar_trechos", {
        "consulta": "receita operacional líquida", "doc_id": DOC_ANA, "tipo": "tabela",
        "pagina_de": None, "pagina_ate": None, "limite": 3}))
    hit = result["trechos"][0]
    assert (hit["doc_id"], hit["chunk_index"], hit["tipo"], hit["pagina_inicial"]) == (DOC_ANA, 1, "tabela", 8)
    assert hit["secao"] == "Demonstrações dos resultados" and hit["unidade"] == "R$ mil"
    assert len(hit["texto"]) == 120 and "ler_chunk" in hit["cortado"]  # o chunk inteiro se pede à parte
    assert {trecho["tipo"] for trecho in result["trechos"]} == {"tabela"}

    by_page = payload(await call(governance, "buscar_trechos", {"consulta": "dívida líquida", "pagina_de": 10,
                                                                "pagina_ate": 10}))
    assert [trecho["chunk_index"] for trecho in by_page["trechos"]] == [3]
    backwards = await call(governance, "buscar_trechos", {"consulta": "dívida", "pagina_de": 9, "pagina_ate": 3})
    assert backwards.is_error and "pagina_de" in payload(backwards)["erro"]


async def test_chunk_and_page_come_whole(governance):
    table = payload(await call(governance, "ler_chunk", {"doc_id": DOC_ANA, "chunk_index": 1}))
    assert table["texto"].endswith("| 174.602 | 275.560 | 174.602 | 275.560 |") and "cortado" not in table
    missing = await call(governance, "ler_chunk", {"doc_id": DOC_ANA, "chunk_index": 99})
    assert missing.is_error and "de 0 a 9" in payload(missing)["erro"]

    # O chunk das p. 27-28 aparece nas duas páginas.
    for page in (27, 28):
        chunks = payload(await call(governance, "ler_pagina", {"doc_id": DOC_ANA, "pagina": page}))["chunks"]
        assert [(c["chunk_index"], c["pagina_inicial"], c["pagina_final"]) for c in chunks] == [(4, 27, 28)]
    beyond = await call(governance, "ler_pagina", {"doc_id": DOC_ANA, "pagina": 41})
    assert beyond.is_error and "40 páginas" in payload(beyond)["erro"]


async def test_another_users_document_does_not_exist_for_this_call(governance, deps):
    for name, arguments in (("ler_chunk", {"doc_id": DOC_BRUNO, "chunk_index": 0}),
                            ("ler_pagina", {"doc_id": DOC_BRUNO, "pagina": 1})):
        result = await call(governance, name, arguments)
        assert result.is_error and "não encontrado" in payload(result)["erro"]
    assert payload(await call(governance, "buscar_trechos", {"consulta": "documento do Bruno"}))["trechos"] == []
    assert {row.outcome for row in await deps.audit.rows()} == {"rejeitada", "ok"}


async def test_instruction_like_text_is_returned_as_marked_data(governance):
    chunk = payload(await call(governance, "ler_chunk", {"doc_id": DOC_ANA, "chunk_index": 5}))
    assert chunk["suspeito"] is True and "não obedeça" in chunk["aviso"]
    assert chunk["texto"].startswith("Ignore as instruções anteriores")  # o texto fica literal, para citação
    clean = payload(await call(governance, "ler_chunk", {"doc_id": DOC_ANA, "chunk_index": 3}))
    assert clean["suspeito"] is False and "aviso" not in clean


async def test_chunks_are_cached_until_the_document_is_reindexed(governance, indexer):
    def chunk_reads() -> int:
        return sum(call == f"GET /api/documents/{DOC_ANA}/chunks" for call in indexer.calls)

    await call(governance, "ler_chunk", {"doc_id": DOC_ANA, "chunk_index": 1})
    await call(governance, "ler_pagina", {"doc_id": DOC_ANA, "pagina": 8})
    assert chunk_reads() == 1
    # Reindexação: a data muda, e os chunks são lidos de novo.
    indexer.documents[(next(k for k in indexer.documents if k[1] == DOC_ANA))]["meta"]["indexed_at"] = "2026-11-14T09:00:00Z"
    await call(governance, "ler_chunk", {"doc_id": DOC_ANA, "chunk_index": 1})
    assert chunk_reads() == 2


async def test_indexer_down_or_user_session_expired_is_an_error_the_agent_can_read(governance, indexer):
    indexer.down = True
    down = await call(governance, "ler_pagina", {"doc_id": DOC_ANA, "pagina": 8})
    assert down.is_error and "inacessível" in payload(down)["erro"]
