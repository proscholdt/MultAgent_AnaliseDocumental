"""Avaliação da busca híbrida, com os casos do pacote de um domínio.

    uv run python scripts/eval_search.py                 # usuário "smoke", domínio financeiro
    uv run python scripts/eval_search.py --dominio laudos

Roda contra a API em execução, depois de `scripts/smoke.py`. São perguntas cuja resposta tem
página conhecida, em dominios/<id>/avaliacao/busca.yaml.
Para cada uma, conta em que posição vem o chunk certo, isto é, o que cobre uma das páginas
esperadas e contém o trecho esperado. Imprime acerto nos 8 primeiros, no 1º lugar e o MRR.
"""
import argparse
import sys
from pathlib import Path

import httpx
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # rodar como `python scripts/x.py`, de indexer/

from app import pacotes

K = 8


def cases(dominio: str) -> list[tuple[str, str, set[int], str]]:
    """Os casos do pacote: dominios/<id>/avaliacao/busca.yaml (arquivo, pergunta, páginas aceitas e
    o trecho que o chunk precisa conter)."""
    path = pacotes.arquivo_do_pacote(dominio, "avaliacao/busca.yaml")
    if path is None:
        sys.exit(f"O pacote {dominio} não tem avaliacao/busca.yaml.")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [(c["arquivo"], c["pergunta"], set(c["paginas"]), c.get("trecho") or "") for c in data["casos"]]



def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--username", default="smoke")
    parser.add_argument("--password", default="smoke-senha-123")
    parser.add_argument("--dominio", default=pacotes.PADRAO, help="o pacote com os casos (avaliacao/busca.yaml)")
    args = parser.parse_args()
    casos = cases(args.dominio)

    with httpx.Client(base_url=args.base_url, timeout=120) as client:
        token = client.post("/auth/login", data={"username": args.username, "password": args.password})
        if token.status_code != 200:
            print("Login recusado. Rode scripts/smoke.py antes, com o mesmo usuário.")
            return 1
        client.headers["Authorization"] = f"Bearer {token.json()['access_token']}"
        doc_ids = {d["file_name"]: d["doc_id"] for d in client.get("/api/documents").json()}
        missing = {case[0] for case in casos} - set(doc_ids)
        if missing:
            print("Documentos não indexados para este usuário:", sorted(missing))
            return 1

        ranks = []
        for file_name, query, pages, needle in casos:
            hits = client.post("/api/search", json={"query": query, "limit": K,
                                                    "doc_id": doc_ids[file_name]}).json()["hits"]
            rank = next((position for position, hit in enumerate(hits, start=1)
                         if set(range(hit["page_start"], hit["page_end"] + 1)) & pages
                         and needle.lower() in hit["text"].lower()), None)
            ranks.append(rank)
            top = f"p.{hits[0]['page_start']} [{hits[0]['chunk_type']}]" if hits else "(vazio)"
            print(f"  {f'{rank}º' if rank else '—':>3}  {file_name:<10} {query[:66]:<66} | topo: {top}")

    found = [rank for rank in ranks if rank]
    mrr = sum(1 / rank for rank in found) / len(ranks)
    print(f"\n{len(ranks)} perguntas | entre os {K} primeiros: {len(found)} | em 1º lugar: {found.count(1)} "
          f"| até o 3º: {sum(rank <= 3 for rank in found)} | MRR: {mrr:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
