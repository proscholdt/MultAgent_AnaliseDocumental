from app.infra import arquivos as storage
from app.schemas import IndexRequested, ParseRequested
from tests.conftest import register_and_login


async def upload(ctx, headers, data: bytes, name: str = "relatorio.pdf", **form):
    return await ctx.client.post("/api/documents", headers=headers, data=form,
                                 files={"file": (name, data, "application/pdf")})


async def test_upload_saves_file_and_enqueues_parse(ctx, pdf_bytes):
    user_id, headers = await register_and_login(ctx.client, "ana")
    response = await upload(ctx, headers, pdf_bytes, company="Empresa X")
    assert response.status_code == 202, response.text
    body = response.json()

    assert body["doc_id"] == storage.compute_doc_id(pdf_bytes)
    assert storage.upload_path(user_id, body["doc_id"]).read_bytes() == pdf_bytes

    topic, key, message = ctx.publisher.messages[0]
    assert topic == ctx.settings.kafka_topic_parse
    assert key == user_id  # chave de particionamento = usuário
    assert message == ParseRequested(job_id=body["job_id"], user_id=user_id, doc_id=body["doc_id"])

    job = (await ctx.client.get(f"/api/jobs/{body['job_id']}", headers=headers)).json()
    assert job["status"] == "queued" and job["progress_total"] == 1
    document = (await ctx.client.get(f"/api/documents/{body['doc_id']}", headers=headers)).json()
    assert document["status"] == "queued" and document["page_count"] == 1


async def test_upload_ignores_client_path_in_filename(ctx, pdf_bytes):
    user_id, headers = await register_and_login(ctx.client, "ana")
    response = await upload(ctx, headers, pdf_bytes, name="../../etc/passwd.pdf")
    doc_id = response.json()["doc_id"]
    document = (await ctx.client.get(f"/api/documents/{doc_id}", headers=headers)).json()
    assert document["file_name"] == "passwd.pdf"
    assert storage.upload_path(user_id, doc_id).parent == ctx.settings.storage_dir / user_id / doc_id


async def test_upload_rejects_non_pdf_and_oversized(ctx, pdf_bytes, monkeypatch):
    _, headers = await register_and_login(ctx.client, "ana")
    not_pdf = await upload(ctx, headers, b"MZ\x90\x00 isto nao e um pdf", name="virus.pdf")
    assert not_pdf.status_code == 400
    broken = await upload(ctx, headers, b"%PDF-1.7\nlixo sem estrutura", name="quebrado.pdf")
    assert broken.status_code == 400

    monkeypatch.setattr(ctx.settings, "max_upload_mb", 0)
    too_big = await upload(ctx, headers, pdf_bytes)
    assert too_big.status_code == 413
    assert ctx.publisher.messages == []


def encrypted_pdf(user_password: str) -> bytes:
    import io

    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.encrypt(user_password=user_password, owner_password="dono", algorithm="AES-256")
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


async def test_upload_accepts_permission_only_encryption_but_not_open_password(ctx):
    """O PDF da Brasal é AES com senha de usuário vazia (só restringe permissões): tem de entrar."""
    _, headers = await register_and_login(ctx.client, "ana")
    permission_only = await upload(ctx, headers, encrypted_pdf(user_password=""))
    assert permission_only.status_code == 202, permission_only.text
    needs_password = await upload(ctx, headers, encrypted_pdf(user_password="segredo"))
    assert needs_password.status_code == 400
    assert "senha" in needs_password.json()["detail"]


async def test_upload_marks_job_failed_when_queue_is_down(ctx, pdf_bytes):
    _, headers = await register_and_login(ctx.client, "ana")
    ctx.publisher.fail = True
    response = await upload(ctx, headers, pdf_bytes)
    assert response.status_code == 503

    doc_id = storage.compute_doc_id(pdf_bytes)
    jobs = (await ctx.client.get(f"/api/documents/{doc_id}/jobs", headers=headers)).json()
    assert [job["status"] for job in jobs] == ["failed"]


async def test_reupload_reuses_doc_id(ctx, pdf_bytes):
    _, headers = await register_and_login(ctx.client, "ana")
    first = (await upload(ctx, headers, pdf_bytes)).json()
    second = (await upload(ctx, headers, pdf_bytes, name="outro-nome.pdf")).json()
    assert first["doc_id"] == second["doc_id"] and first["job_id"] != second["job_id"]
    documents = (await ctx.client.get("/api/documents", headers=headers)).json()
    assert len(documents) == 1 and documents[0]["file_name"] == "outro-nome.pdf"


async def test_users_cannot_see_each_others_documents_or_jobs(ctx, pdf_bytes):
    _, ana = await register_and_login(ctx.client, "ana")
    _, bruno = await register_and_login(ctx.client, "bruno")
    body = (await upload(ctx, ana, pdf_bytes)).json()

    assert (await ctx.client.get("/api/documents", headers=bruno)).json() == []
    for method, path in [
        ("GET", f"/api/documents/{body['doc_id']}"),
        ("GET", f"/api/documents/{body['doc_id']}/chunks"),
        ("GET", f"/api/documents/{body['doc_id']}/jobs"),
        ("DELETE", f"/api/documents/{body['doc_id']}"),
        ("GET", f"/api/jobs/{body['job_id']}"),
        ("POST", f"/api/jobs/{body['job_id']}/retry"),
    ]:
        response = await ctx.client.request(method, path, headers=bruno)
        assert response.status_code == 404, (method, path)
    patched = await ctx.client.patch(f"/api/documents/{body['doc_id']}", headers=bruno,
                                     json={"company": "Invasor S.A."})
    assert patched.status_code == 404
    # O documento da Ana continua lá.
    assert (await ctx.client.get(f"/api/documents/{body['doc_id']}", headers=ana)).status_code == 200


async def test_same_file_from_two_users_is_two_documents(ctx, pdf_bytes):
    ana_id, ana = await register_and_login(ctx.client, "ana")
    bruno_id, bruno = await register_and_login(ctx.client, "bruno")
    a = (await upload(ctx, ana, pdf_bytes)).json()
    b = (await upload(ctx, bruno, pdf_bytes)).json()
    assert a["doc_id"] == b["doc_id"]
    assert storage.upload_path(ana_id, a["doc_id"]) != storage.upload_path(bruno_id, b["doc_id"])
    assert len((await ctx.client.get("/api/documents", headers=ana)).json()) == 1


async def test_patch_stores_override_and_enqueues_reindex(ctx, pdf_bytes):
    user_id, headers = await register_and_login(ctx.client, "ana")
    doc_id = (await upload(ctx, headers, pdf_bytes)).json()["doc_id"]

    empty = await ctx.client.patch(f"/api/documents/{doc_id}", headers=headers, json={})
    assert empty.status_code == 400

    response = await ctx.client.patch(f"/api/documents/{doc_id}", headers=headers,
                                      json={"company": "Empresa Corrigida S.A.", "period": "2022"})
    assert response.status_code == 202
    message = ctx.publisher.on(ctx.settings.kafka_topic_index)[-1]
    assert message == IndexRequested(job_id=response.json()["job_id"], user_id=user_id, doc_id=doc_id,
                                     reason="metadata_changed")


async def test_retry_only_for_failed_jobs_and_routes_by_stage(ctx, pdf_bytes):
    user_id, headers = await register_and_login(ctx.client, "ana")
    body = (await upload(ctx, headers, pdf_bytes)).json()

    not_failed = await ctx.client.post(f"/api/jobs/{body['job_id']}/retry", headers=headers)
    assert not_failed.status_code == 409

    # Falha na indexação com o parse já em cache: volta direto para a fila de indexação.
    from app.infra import banco as db
    from app.infra import repo

    parsed_file = storage.parsed_path(user_id, body["doc_id"], ctx.settings.parser)
    parsed_file.parent.mkdir(parents=True)
    parsed_file.write_text("{}", encoding="utf-8")
    async with db.session_scope() as session:
        await repo.mark_job_failed(session, body["job_id"], "index", "OpenAI fora do ar")

    retried = await ctx.client.post(f"/api/jobs/{body['job_id']}/retry", headers=headers)
    assert retried.status_code == 202 and retried.json()["status"] == "queued"
    assert retried.json()["error"] is None
    message = ctx.publisher.on(ctx.settings.kafka_topic_index)[-1]
    assert message.job_id == body["job_id"] and message.reason == "retry"


async def test_delete_removes_rows_and_files(ctx, pdf_bytes):
    user_id, headers = await register_and_login(ctx.client, "ana")
    body = (await upload(ctx, headers, pdf_bytes)).json()
    deleted = await ctx.client.delete(f"/api/documents/{body['doc_id']}", headers=headers)
    assert deleted.status_code == 204
    assert not storage.doc_dir(user_id, body["doc_id"]).exists()
    assert (await ctx.client.get(f"/api/documents/{body['doc_id']}", headers=headers)).status_code == 404
    assert (await ctx.client.get(f"/api/jobs/{body['job_id']}", headers=headers)).status_code == 404


async def test_health_reports_components(ctx):
    response = await ctx.client.get("/health")
    assert response.status_code == 200
    components = response.json()["components"]
    assert components["api"] == "ok" and components["postgres"] == "ok" and components["qdrant"] == "ok"
    assert response.json()["parser"] == "docling"  # o padrão


async def test_azure_parser_without_credentials_is_reported_and_blocks_upload(ctx, pdf_bytes, monkeypatch):
    """PARSER=azure sem endpoint e chave: o parse-worker não sobe, então a API recusa o upload
    em vez de deixar um job parado na fila."""
    _, headers = await register_and_login(ctx.client, "ana")
    monkeypatch.setattr(ctx.settings, "parser", "azure")

    health = (await ctx.client.get("/health")).json()
    assert health["parser"] == "azure" and health["status"] == "degraded"
    assert "AZURE_DI_ENDPOINT" in health["components"]["parser"]
    refused = await upload(ctx, headers, pdf_bytes)
    assert refused.status_code == 503 and "AZURE_DI_KEY" in refused.json()["detail"]
    assert ctx.publisher.messages == []

    monkeypatch.setattr(ctx.settings, "azure_di_endpoint", "https://exemplo.cognitiveservices.azure.com/")
    monkeypatch.setattr(ctx.settings, "azure_di_key", "chave-de-teste")
    assert (await ctx.client.get("/health")).json()["status"] == "ok"
    assert (await upload(ctx, headers, pdf_bytes)).status_code == 202
