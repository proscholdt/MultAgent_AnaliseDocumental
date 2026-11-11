"""Teste de ponta a ponta contra a API em execução.

    uv run python scripts/smoke.py                      # envia os PDFs de data/pdfs
    uv run python scripts/smoke.py --files data/pdfs/Brasal.pdf --query "receita líquida"

Cria (ou reutiliza) um usuário, faz login, envia os PDFs, acompanha os jobs e imprime,
por documento, tempo por etapa, tokens e custo estimado. Sai com código 1 se algo falhar.
"""
import argparse
import sys
import time
from pathlib import Path

import httpx


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--username", default="smoke")
    parser.add_argument("--password", default="smoke-senha-123")
    parser.add_argument("--files", nargs="*", type=Path)
    parser.add_argument("--query", default="alavancagem dívida líquida EBITDA covenants")
    parser.add_argument("--timeout", type=int, default=3600, help="segundos de espera por documento")
    args = parser.parse_args()
    files = args.files or sorted(Path("data/pdfs").glob("*.pdf"))
    if not files:
        print("Nenhum PDF encontrado.")
        return 1

    with httpx.Client(base_url=args.base_url, timeout=120) as client:
        health = client.get("/health").json()
        print("health:", health)

        credentials = {"username": args.username, "password": args.password}
        registered = client.post("/auth/register", json=credentials)
        if registered.status_code not in (201, 409):
            print("Falha no cadastro:", registered.text)
            return 1
        token = client.post("/auth/login", data=credentials).raise_for_status().json()
        client.headers["Authorization"] = f"Bearer {token['access_token']}"
        print(f"usuário {args.username} ({token['user_id']})")

        jobs = {}
        for path in files:
            with path.open("rb") as handle:
                response = client.post("/api/documents", files={"file": (path.name, handle, "application/pdf")})
            if response.status_code != 202:
                print(f"{path.name}: upload recusado ({response.status_code}) {response.text}")
                return 1
            jobs[path.name] = response.json()
            print(f"{path.name}: doc_id={jobs[path.name]['doc_id']} job_id={jobs[path.name]['job_id']}")

        failed = False
        for name, ids in jobs.items():
            started = time.time()
            last = None
            while True:
                try:
                    job = client.get(f"/api/jobs/{ids['job_id']}").json()
                except httpx.TransportError as exc:  # API ocupada ou reiniciando: o job segue no worker
                    print(f"  [{name}] consulta falhou ({type(exc).__name__}), tentando de novo")
                    time.sleep(5)
                    continue
                line =f"{job['status']} {job['stage'] or ''} {job['progress_current']}/{job['progress_total']}"
                if line != last:
                    print(f"  [{name}] {line}")
                    last = line
                if job["status"] in ("done", "failed"):
                    break
                if time.time() - started > args.timeout:
                    print(f"  [{name}] tempo esgotado")
                    failed = True
                    break
                time.sleep(2)

            if job["status"] != "done":
                print(f"  [{name}] ERRO: {job['error']}")
                failed = True
                continue
            document = client.get(f"/api/documents/{ids['doc_id']}").json()
            print(f"  [{name}] {document['company']} | {document['period']} | {document['doc_type']} | "
                  f"{document['default_unit']} | {document['page_count']} págs | {document['chunk_count']} chunks")
            print(f"  [{name}] tempos (s): {job['timings']}")
            print(f"  [{name}] uso: {job['usage']}")

        search = client.post("/api/search", json={"query": args.query, "limit": 5})
        if search.status_code == 200:
            print(f"\nbusca híbrida: {args.query!r}")
            for hit in search.json()["hits"]:
                snippet = " ".join(hit["text"].split())[:110]
                print(f"  {hit['score']:.3f}  {hit['file_name']} p.{hit['page_start']} [{hit['chunk_type']}] {snippet}")
        else:
            print("busca indisponível:", search.status_code, search.text)
            failed = True

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
