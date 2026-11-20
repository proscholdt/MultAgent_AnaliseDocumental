"""Coleções e domínios: cada coleção é de um domínio, e o documento enviado nela é lido com as
regras dele. Quem envia sem escolher cai na coleção do domínio padrão."""
import sqlalchemy as sa

from app.infra import banco
from tests.conftest import register_and_login
from tests.test_documents import upload


async def test_the_domains_come_from_the_packages(ctx):
    _, headers = await register_and_login(ctx.client, "ana")
    dominios = (await ctx.client.get("/api/dominios", headers=headers)).json()
    financeiro = next(d for d in dominios if d["id"] == "financeiro")
    assert financeiro["padrao"] and financeiro["entidade"]["nome"] == "empresa"
    assert [m["nome"] for m in financeiro["metadados"]] == ["empresa", "periodo", "tipo_documento"]
    assert financeiro["textos"]["envio"].startswith("Envie as demonstrações")
    assert (await ctx.client.get("/api/dominios")).status_code == 401  # só com login


async def test_a_collection_is_of_one_known_domain_and_belongs_to_its_user(ctx):
    _, ana = await register_and_login(ctx.client, "ana")
    _, bia = await register_and_login(ctx.client, "bia")
    desconhecido = await ctx.client.post("/api/colecoes", headers=ana, json={"nome": "X", "dominio": "astrologia"})
    assert desconhecido.status_code == 422 and "financeiro" in desconhecido.json()["detail"]

    criada = await ctx.client.post("/api/colecoes", headers=ana, json={"nome": "Balanços 2022", "dominio": "financeiro"})
    assert criada.status_code == 201
    colecao = criada.json()
    assert (colecao["nome"], colecao["dominio"], colecao["documentos"]) == ("Balanços 2022", "financeiro", 0)
    assert [c["id"] for c in (await ctx.client.get("/api/colecoes", headers=ana)).json()] == [colecao["id"]]
    assert (await ctx.client.get("/api/colecoes", headers=bia)).json() == []


async def test_a_document_sent_to_a_collection_takes_its_domain(ctx, pdf_bytes):
    _, headers = await register_and_login(ctx.client, "ana")
    colecao = (await ctx.client.post("/api/colecoes", headers=headers,
                                     json={"nome": "Balanços", "dominio": "financeiro"})).json()
    doc_id = (await upload(ctx, headers, pdf_bytes, colecao_id=colecao["id"])).json()["doc_id"]
    document = (await ctx.client.get(f"/api/documents/{doc_id}", headers=headers)).json()
    assert (document["collection_id"], document["dominio"], document["metadados"]) == (colecao["id"], "financeiro", {})
    listed = (await ctx.client.get("/api/colecoes", headers=headers)).json()
    assert listed[0]["documentos"] == 1
    only = (await ctx.client.get(f"/api/documents?colecao_id={colecao['id']}", headers=headers)).json()
    assert [d["doc_id"] for d in only] == [doc_id]


async def test_without_a_collection_the_document_goes_to_the_default_domain(ctx, pdf_bytes):
    _, headers = await register_and_login(ctx.client, "ana")
    doc_id = (await upload(ctx, headers, pdf_bytes)).json()["doc_id"]
    document = (await ctx.client.get(f"/api/documents/{doc_id}", headers=headers)).json()
    colecoes = (await ctx.client.get("/api/colecoes", headers=headers)).json()
    assert [(c["nome"], c["dominio"], c["documentos"]) for c in colecoes] == [("Demonstrações financeiras", "financeiro", 1)]
    assert document["collection_id"] == colecoes[0]["id"] and document["dominio"] == "financeiro"


async def test_another_users_collection_is_not_found(ctx, pdf_bytes):
    _, ana = await register_and_login(ctx.client, "ana")
    _, bia = await register_and_login(ctx.client, "bia")
    colecao = (await ctx.client.post("/api/colecoes", headers=ana, json={"nome": "A", "dominio": "financeiro"})).json()
    response = await upload(ctx, bia, pdf_bytes, colecao_id=colecao["id"])
    assert response.status_code == 404 and ctx.publisher.messages == []


async def test_metadata_correction_by_name_is_checked_against_the_domain(ctx, pdf_bytes):
    _, headers = await register_and_login(ctx.client, "ana")
    doc_id = (await upload(ctx, headers, pdf_bytes)).json()["doc_id"]
    wrong = await ctx.client.patch(f"/api/documents/{doc_id}", headers=headers, json={"metadados": {"paciente": "X"}})
    assert wrong.status_code == 422 and "empresa" in wrong.json()["detail"]
    ok = await ctx.client.patch(f"/api/documents/{doc_id}", headers=headers,
                                json={"metadados": {"empresa": "Nome Certo S.A."}})
    assert ok.status_code == 202


async def test_a_database_from_before_the_domains_gets_the_new_columns(settings, tmp_path):
    """Base antiga: a tabela documents sem coleção nem domínio. A subida acrescenta as colunas."""
    url = f"sqlite+aiosqlite:///{(tmp_path / 'antiga.db').as_posix()}"
    engine = banco.configure(url)
    async with engine.begin() as conn:
        await conn.execute(sa.text("CREATE TABLE documents (user_id VARCHAR(36) NOT NULL, doc_id VARCHAR(16) NOT NULL, "
                                   "file_name VARCHAR(255), PRIMARY KEY (user_id, doc_id))"))
        await conn.execute(sa.text("INSERT INTO documents (user_id, doc_id, file_name) VALUES ('u', 'd', 'a.pdf')"))
    await banco.init_db()
    async with engine.connect() as conn:
        columns = {row[1] for row in (await conn.execute(sa.text("PRAGMA table_info(documents)"))).all()}
        dominio = (await conn.execute(sa.text("SELECT dominio FROM documents"))).scalar_one()
    assert {"collection_id", "dominio", "metadados", "dominio_sugerido", "aviso_dominio"} <= columns
    assert dominio == "financeiro"  # o documento de antes é do domínio padrão
    await banco.dispose()
