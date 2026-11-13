"""Auditoria do índice: compara os chunks de cada documento com a camada de texto do PDF.

    uv run python scripts/audit_index.py                 # usuário "smoke", PDFs de data/pdfs

Roda contra a API em execução, depois de `scripts/smoke.py`. Para cada documento do usuário que
tenha o PDF correspondente em data/pdfs, confere:

- se os números das tabelas existem no texto da página (o modelo de tabelas não inventou nada);
- se os números do PDF estão em algum chunk (nada se perdeu no caminho);
- se há página com texto e sem chunk, chunk acima do limite, parágrafo repetido;
- se a contagem do Postgres bate com a do Qdrant.

Serve para comparar os dois parsers: indexe com PARSER=docling, rode; troque para PARSER=azure,
reenvie os PDFs e rode de novo. O primeiro item é o que mostra se um parser que lê a página por
OCR errou algum número.

Sai com código 1 se algum número de tabela não existir na página ou se as contagens divergirem.
"""
import argparse
import logging
import re
import sys
from pathlib import Path

import httpx
from pypdf import PdfReader

logging.getLogger("pypdf").setLevel(logging.ERROR)  # avisos de fonte que não afetam o texto
NUMBER = re.compile(r"\d[\d.,]*\d")
TABLE_MAX_TOKENS, TEXT_MAX_TOKENS, SMALL_TOKENS = 1500, 800, 15


def squeeze(text: str) -> str:
    return re.sub(r"\s+", "", text.lower())


def digits(number: str) -> int:
    return len(re.sub(r"\D", "", number))


def fetch_chunks(client: httpx.Client, doc_id: str) -> list[dict]:
    chunks, offset = [], 0
    while True:
        page = client.get(f"/api/documents/{doc_id}/chunks", params={"offset": offset, "limit": 100}).json()
        chunks += page["items"]
        offset += 100
        if offset >= page["total"]:
            return chunks


def audit(name: str, chunks: list[dict], pages: dict[int, str]) -> dict:
    def on_page(page: int) -> list[dict]:
        return [c for c in chunks if c["page_start"] <= page <= c["page_end"]]

    table_numbers = table_missing = 0
    for chunk in chunks:
        if chunk["chunk_type"] != "table":
            continue
        native = squeeze(" ".join(pages.get(p, "") for p in range(chunk["page_start"], chunk["page_end"] + 1)))
        for number in NUMBER.findall(chunk["text"]):
            if digits(number) >= 3:
                table_numbers += 1
                table_missing += squeeze(number) not in native

    pdf_numbers = lost = lost_in_sections = 0
    empty_pages = []
    for page, text in pages.items():
        here = on_page(page)
        if text.strip() and not here:
            empty_pages.append(page)
        body = squeeze(" ".join(c["text"] for c in here))
        sections = squeeze(" ".join(" ".join(c["section_path"]) for c in here))
        for number in NUMBER.findall(text):
            if digits(number) < 4:
                continue
            pdf_numbers += 1
            if squeeze(number) not in body:
                lost += 1
                lost_in_sections += squeeze(number) in sections

    repeated = 0
    for chunk in chunks:
        paragraphs = [" ".join(p.split()) for p in re.split(r"\n\s*\n", chunk["text"]) if len(p) > 30]
        repeated += len(paragraphs) - len(set(paragraphs))

    oversized = sum(c["token_count"] > (TEXT_MAX_TOKENS if c["chunk_type"] == "text" else TABLE_MAX_TOKENS)
                    for c in chunks if c["chunk_type"] != "figure")
    kinds = {kind: sum(c["chunk_type"] == kind for c in chunks) for kind in ("text", "table", "figure")}
    parsers = ", ".join(sorted({c.get("parser") or "?" for c in chunks}))
    print(f"\n{name} (parser: {parsers}): {len(chunks)} chunks {kinds}, {len(pages)} páginas")
    print(f"  números de tabela presentes no texto da página: {table_numbers - table_missing} de {table_numbers}")
    print(f"  números do PDF (4+ dígitos) presentes nos chunks: {pdf_numbers - lost} de {pdf_numbers}"
          f" (dos {lost} ausentes, {lost_in_sections} são número de seção)")
    print(f"  páginas com texto e sem chunk: {empty_pages}")
    print(f"  chunks acima do limite: {oversized} | abaixo de {SMALL_TOKENS} tokens: "
          f"{sum(c['token_count'] < SMALL_TOKENS for c in chunks)} | sem seção: "
          f"{sum(not c['section_path'] for c in chunks)} | parágrafos repetidos: {repeated}")
    print(f"  chunks que cobrem mais de uma página: {sum(c['page_end'] > c['page_start'] for c in chunks)}")
    return {"table_missing": table_missing}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--username", default="smoke")
    parser.add_argument("--password", default="smoke-senha-123")
    parser.add_argument("--pdf-dir", type=Path, default=Path("data/pdfs"))
    args = parser.parse_args()

    failed = False
    with httpx.Client(base_url=args.base_url, timeout=120) as client:
        token = client.post("/auth/login", data={"username": args.username, "password": args.password})
        if token.status_code != 200:
            print("Login recusado. Rode scripts/smoke.py antes, com o mesmo usuário.")
            return 1
        client.headers["Authorization"] = f"Bearer {token.json()['access_token']}"
        for document in client.get("/api/documents").json():
            pdf = args.pdf_dir / document["file_name"]
            if document["status"] != "indexed" or not pdf.exists():
                print(f"\n{document['file_name']}: ignorado (status {document['status']}, PDF local: {pdf.exists()})")
                continue
            chunks = fetch_chunks(client, document["doc_id"])
            pages = {n: page.extract_text() or "" for n, page in enumerate(PdfReader(str(pdf)).pages, start=1)}
            result = audit(document["file_name"], chunks, pages)
            if document["chunk_count"] != len(chunks):
                print(f"  CONTAGEM DIVERGENTE: Postgres {document['chunk_count']}, Qdrant {len(chunks)}")
                failed = True
            failed = failed or result["table_missing"] > 0
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
