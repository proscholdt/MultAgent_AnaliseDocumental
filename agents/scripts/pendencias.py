"""O que falta na base de um usuário: a mesma lista que o orquestrador e a conferência usam.

    uv run python scripts/pendencias.py --usuario <usuário do indexador> --senha <senha> [--doc Millet] [--json]

Chama a tool `pendencias` do servidor MCP com a identidade "sistema" (só leitura). A lista não
fica gravada em lugar nenhum: o servidor a calcula na hora, cruzando os documentos indexados com
o que a base tem (extração, revisão, análise, as 5 respostas, resumos, relatório). Com --doc, é o
que falta para o relatório só daquela empresa (aceita o nome do arquivo, da empresa ou o doc_id).
"""
import asyncio
import json
import sys
from datetime import UTC, datetime

from base import arguments, login, settings

from app.nucleo import mcp


def pick(documents: list[dict], wanted: str) -> str:
    """O doc_id a partir do nome do arquivo, da empresa ou do próprio doc_id."""
    key = wanted.casefold()
    found = [doc for doc in documents
             if key == doc["doc_id"] or key in doc["arquivo"].casefold() or key in (doc["empresa"] or "").casefold()]
    if len(found) != 1:
        names = ", ".join(f"{doc['arquivo']} ({doc['doc_id']})" for doc in documents)
        sys.exit(f"'{wanted}' não aponta para um documento só. Documentos: {names}.")
    return found[0]["doc_id"]


def show(items: list[dict]) -> None:
    if not items:
        print("Nada pendente: a base está completa.")
        return
    ready = [item for item in items if item.get("liberada", True)]
    waiting = [item for item in items if not item.get("liberada", True)]
    for title, group in (("Pode ser feito agora", ready), ("Aguardando outra etapa", waiting)):
        if not group:
            continue
        print(f"\n{title} ({len(group)}):")
        for item in group:
            where = item.get("empresa") or (f"pergunta {item['pergunta_id'][1:]}" if item.get("pergunta_id") else "")
            line = f"  - [{item['agente']}] {item['o_que_falta']}" + (f" — {where}" if where else "")
            if item.get("categorias"):
                line += f" ({', '.join(item['categorias'])})"
            print(line)
            if item.get("aguarda"):
                print(f"      aguarda: {item['aguarda']}")
    print(f"\nTotal: {len(items)} pendência(s), {len(ready)} liberada(s).")


async def main() -> int:
    parser = arguments("Mostra o que falta na base, pela tool pendencias do servidor MCP.")
    parser.add_argument("--doc", help="só o relatório desta empresa: nome do arquivo, da empresa ou doc_id")
    parser.add_argument("--json", action="store_true", help="a lista crua, como o servidor devolve")
    args = parser.parse_args()

    config = settings()
    identity = await login(config, args.usuario, args.senha, f"pendencias-{datetime.now(UTC):%Y%m%d-%H%M%S}",
                           colecao=args.colecao)
    async with mcp.connect(config, mcp.SYSTEM, identity) as server:
        arguments_ = {}
        if args.doc:
            documents = (await server.call("listar_documentos"))["documentos"]
            arguments_ = {"doc_id": pick(documents, args.doc)}
        items = (await server.call("pendencias", arguments_))["pendencias"]
    if args.json:
        print(json.dumps(items, ensure_ascii=False, indent=2))
    else:
        show(items)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(main()))
