"""Os candidatos a inconsistência da fonte de um documento, no terminal.

São as mesmas regras que o validador recebe pela tool `candidatos_inconsistencia`, sem modelo e
sem custo: serve para ver o que elas acham num PDF antes de rodar o relatório. A regra que
compara o texto com a extração só roda se o documento já tiver extração gravada na base.

    python -m app.ferramentas.candidatos --usuario <u> --senha <s> [--doc Millet]

No contêiner (o jeito do Makefile da raiz): `docker compose exec mcp-server python -m ...`.
"""
import argparse
import asyncio
import sys
import textwrap

import httpx

from app.config import get_settings
from app.dados.banco import Database
from app.dados.base import Store
from app.dados.documentos import Documents
from app.dados.indexador import IndexerClient
from app.governanca.identidade import Caller
from app.regras.formulas import Extraction
from app.regras.inconsistencias import find
from app.tools.extracao import periods, to_value

_WIDTH = 100


def _login(url: str, user: str, password: str) -> str:
    response = httpx.post(f"{url}/auth/login", data={"username": user, "password": password}, timeout=30)
    if response.status_code != 200:
        sys.exit(f"Login recusado pelo indexador ({response.status_code}): confira usuário e senha.")
    return response.json()["access_token"]


def _matches(meta: dict, wanted: str | None) -> bool:
    if not wanted:
        return True
    wanted = wanted.casefold()
    return any(wanted in (meta.get(key) or "").casefold() for key in ("doc_id", "file_name", "company"))


async def run(user: str, password: str, wanted: str | None) -> int:
    settings = get_settings()
    token = _login(settings.indexer_url, user, password)
    client = IndexerClient(settings)
    database = Database(settings.database_url)
    await database.init()
    try:
        user_id = (await client.me(token))["user_id"]
        caller = Caller(agent="terminal", run_id="terminal", user_id=user_id, user_token=token)
        listed = [meta for meta in await client.documents(token)
                  if meta.get("status") == "indexed" and _matches(meta, wanted)]
        if not listed:
            print(f"Nenhum documento indexado{f' com {wanted!r}' if wanted else ''} para {user}.")
            return 1
        documents, store = Documents(client), Store(database)
        for meta in sorted(listed, key=lambda m: m["file_name"]):
            document = await documents.get(caller, meta["doc_id"])
            reference, comparable = periods(document)
            rows = await store.values(user_id, document.doc_id, document.fingerprint)
            extraction = Extraction([to_value(row) for row in rows], reference, comparable)
            found = find(document.chunks, document.period, extraction)
            print(f"\n{document.company or document.file_name} ({document.file_name}, {document.doc_id}): "
                  f"{len(found)} candidato(s); extração na base: {'sim' if rows else 'não'}")
            for candidate in found:
                strong = " · PROVA FORTE: não pode ser descartado" if candidate.prova_forte else ""
                print(f"\n  p. {candidate.pagina} · chunk {candidate.chunk_index} · {candidate.regra}{strong}")
                for label, text in (("motivo", candidate.motivo), ("prova", candidate.prova),
                                    ("trecho", candidate.trecho[:300])):
                    if text:
                        print(textwrap.fill(text, _WIDTH, initial_indent=f"    {label}: ",
                                            subsequent_indent=" " * (len(label) + 6)))
        print("\nAs páginas são as do arquivo PDF. Candidato é suspeita: quem decide é o validador, no relatório.")
        return 0
    finally:
        await client.close()
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Candidatos a inconsistência da fonte, por regra (sem modelo).")
    parser.add_argument("--usuario", required=True, help="usuário do indexador")
    parser.add_argument("--senha", required=True, help="senha do indexador")
    parser.add_argument("--doc", help="parte do nome do arquivo, da empresa ou do doc_id (padrão: todos)")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(asyncio.run(run(args.usuario, args.senha, args.doc)))


if __name__ == "__main__":
    main()
