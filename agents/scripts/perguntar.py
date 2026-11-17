"""Uma mensagem no chat, pelo terminal: o mesmo caminho do frontend (roteador, orquestrador, agentes).

    uv run python scripts/perguntar.py --usuario <u> --senha <s> --texto "Qual empresa teve a maior margem EBITDA?"
    uv run python scripts/perguntar.py --usuario <u> --senha <s> --texto "e a menor?" --conversa conv-...

Sem --conversa, abre uma conversa nova; com ela, a mensagem é um turno a mais daquela conversa (e o
"e a menor?" sabe qual foi a pergunta anterior). Imprime quem decidiu a intenção, as ondas, a
resposta com as fontes, o tempo e o custo. Custa o que a mensagem custar: centavos numa pergunta
com a base pronta; um relatório pedido assim roda o relatório inteiro (cerca de US$ 0,15).
"""
import argparse
import asyncio
import os
import sys
from datetime import datetime

import httpx
from base import login, settings


def seconds(start: str, end: str) -> float:
    return (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()


def show(run: dict) -> None:
    steps = run.get("passos") or []
    for step in steps:
        if step["tipo"] == "intencao":
            print(f"Intenção: {step['intencao']} (decidiu: {step.get('fonte') or 'llm'})"
                  + (f", empresa {step['doc_id']}" if step.get("doc_id") else ""))
        elif step["tipo"] == "orquestrador" and (step.get("decisao") or {}).get("delegacoes"):
            agents = [item["agente"] for item in step["decisao"]["delegacoes"]]
            print(f"  onda {step['onda']}: " + ", ".join(f"{name} ×{agents.count(name)}" if agents.count(name) > 1
                                                       else name for name in dict.fromkeys(agents)))
        elif step["tipo"] == "barrada":
            print(f"  barrada: {step['agente']}")
    result = run.get("resultado") or {}
    print()
    if result.get("fora_do_escopo"):
        print(run.get("mensagem"))
        for option in result.get("opcoes") or []:
            print(f"  - {option['titulo']}: {option['exemplo']}")
    for answer in result.get("respostas") or []:
        print(answer["resposta"])
        print("\nFontes: " + "; ".join(dict.fromkeys(answer.get("fontes") or [])))
    if result.get("relatorio"):
        print(result["relatorio"][:1500] + ("\n…" if len(result["relatorio"]) > 1500 else ""))
        print("\n(o relatório inteiro: make relatorio)")
    if run["estado"] != "completa":
        print(f"Estado: {run['estado']}. {run.get('erro') or run.get('mensagem') or ''}")
        for item in run.get("pendencias") or []:
            print(f"  falta: {item['o_que_falta']}")
    total = (run.get("uso") or {}).get("total") or {}
    took = seconds(steps[0]["em"], steps[-1]["em"]) if len(steps) > 1 else 0
    print(f"\n{run['tipo']} · {run['estado']} · {took:.0f} s · US$ {total.get('custo_usd') or 0:.4f}"
          f" · conversa {run.get('conversa_id')}")


async def main() -> int:
    parser = argparse.ArgumentParser(description="Manda uma mensagem ao chat e mostra a resposta.")
    parser.add_argument("--usuario", default=os.environ.get("AVALIACAO_USUARIO"))
    parser.add_argument("--senha", default=os.environ.get("AVALIACAO_SENHA"))
    parser.add_argument("--texto", required=True, help="a mensagem, como seria escrita no chat")
    parser.add_argument("--conversa", help="continua esta conversa (o id que a resposta anterior mostrou)")
    parser.add_argument("--api", default="http://localhost:8200", help="endereço da API do orquestrador")
    args = parser.parse_args()

    identity = await login(settings(), args.usuario, args.senha, "perguntar")
    async with httpx.AsyncClient(base_url=args.api, timeout=None,
                                 headers={"Authorization": f"Bearer {identity.user_token}"}) as http:
        conversation = args.conversa or (await http.post("/api/conversas")).json()["conversa_id"]
        response = await http.post(f"/api/conversas/{conversation}/mensagens", json={"texto": args.texto})
        if response.status_code >= 400:
            sys.exit(f"O orquestrador recusou ({response.status_code}): {response.text[:300]}")
        run = response.json()
        if run["estado"] in ("na_fila", "em_andamento"):  # resposta antes do fim: acompanha até terminar
            print("Executando...")
            while run["estado"] in ("na_fila", "em_andamento"):
                await asyncio.sleep(2)
                run = (await http.get(f"/api/execucoes/{run['execucao_id']}")).json()
    show(run)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(main()))
