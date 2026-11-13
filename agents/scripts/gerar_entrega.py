"""Gera a pasta de entrega a partir da base: relatório, JSON por documento, respostas e avaliação.

    uv run python scripts/gerar_entrega.py --usuario <usuário do indexador> --senha <senha>

Por padrão só lê o que já está na base. Com --rodar, antes pede o relatório completo à API do
orquestrador (POST /api/relatorios) e espera terminar; o resumo dessa execução (tempo, tokens,
custo por agente e por documento) vai junto.

O que sai em entrega/:

    relatorio.md                  o relatório consolidado, como o servidor o montou
    extracao_enxuta/<documento>.json  só os campos do item 2 do teste e só o que está escrito no
                                  documento (valor, unidade, ano anterior, variação quando a empresa
                                  a escreve, página e trecho), ou "não encontrado". Nada calculado
    extracao/<documento>.json     o JSON completo: também os valores de apoio, os cálculos e as validações
    analise/<documento>.json      a análise qualitativa, cada item com a citação e a página
    alertas/<documento>.json      inconsistências e informações não encontradas
    respostas.md / respostas.json as 5 perguntas, com as fontes
    avaliacao.md                  as métricas contra o gabarito
    execucao.json                 só com --rodar: estado, tempo, tokens e custo da execução
"""
import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from base import ROOT, arguments, login, settings, snapshot

from app.evaluation import evaluate, render
from app.grafo import latin_only

OUT = ROOT / "entrega"


def write(path: Path, content: str | dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, indent=2)
    path.write_text(text.rstrip("\n") + "\n", encoding="utf-8")


async def run_report(api: str, token: str) -> dict:
    """Pede o relatório ao orquestrador e acompanha até terminar."""
    async with httpx.AsyncClient(base_url=api, headers={"Authorization": f"Bearer {token}"}, timeout=60) as http:
        created = await http.post("/api/relatorios", json={"aguardar": False})
        created.raise_for_status()
        run_id, started = created.json()["execucao_id"], time.monotonic()
        while True:
            run = (await http.get(f"/api/execucoes/{run_id}")).json()
            if run["estado"] not in ("na_fila", "em_andamento"):
                break
            await asyncio.sleep(5)
    print(f"Execução {run_id}: {run['estado']} em {time.monotonic() - started:.0f} s")
    # No resumo da execução ficam os números; os argumentos das tools (texto dos documentos) ficam no trace local.
    steps = [{key: value for key, value in step.items() if key != "argumentos"} for step in run.pop("passos", [])]
    run.pop("resultado", None)
    return {**run, "passos": steps}


async def main() -> int:
    parser = arguments("Gera a pasta de entrega a partir da base.")
    parser.add_argument("--rodar", action="store_true", help="pede o relatório completo ao orquestrador antes")
    parser.add_argument("--api", default="http://localhost:8200", help="endereço da API do orquestrador")
    parser.add_argument("--retrato", help="gera a entrega de um retrato guardado por avaliar.py "
                        "(avaliacao/resultados/retrato-*.json), sem ler a base nem pedir login")
    parser.add_argument("--execucao", help="com --retrato: o arquivo da execução que gerou aquele retrato "
                        "(data/execucoes/<id>.json), para o resumo de tempo, tokens e custo")
    args = parser.parse_args()

    if args.retrato:
        current = json.loads(Path(args.retrato).read_text(encoding="utf-8"))
        if args.execucao:
            run = json.loads(Path(args.execucao).read_text(encoding="utf-8"))
            trace = Path(args.execucao).with_suffix(".jsonl")
            steps = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()] if trace.exists() else []
            run.pop("resultado", None)
            run.pop("user_id", None)
            run["mensagem"] = latin_only(run.get("mensagem", ""))  # como o serviço passou a gravar
            write(OUT / "execucao.json", {"execucao_id": run.pop("id"), **run, "passos": [
                {key: value for key, value in step.items() if key != "argumentos"} for step in steps]})
    else:
        config = settings()
        identity = await login(config, args.usuario, args.senha, f"entrega-{datetime.now(UTC):%Y%m%d-%H%M%S}")
        if args.rodar:
            write(OUT / "execucao.json", await run_report(args.api, identity.user_token))
        current = await snapshot(config, identity)
    if current["pendencias"]:
        print("Atenção: a base tem pendências; a entrega sai incompleta.")
        for item in current["pendencias"]:
            print(f"  - {item['o_que_falta']} ({item.get('empresa') or item.get('pergunta_id') or ''})")

    for doc in current["documentos"]:
        name = Path(doc["arquivo"]).stem
        if doc["extracao"]:
            write(OUT / "extracao" / f"{name}.json", doc["extracao"])
        if doc.get("extracao_enxuta"):
            write(OUT / "extracao_enxuta" / f"{name}.json", doc["extracao_enxuta"])
        write(OUT / "analise" / f"{name}.json", {"arquivo": doc["arquivo"], "empresa": doc["empresa"],
                                                 "analise": doc["analise"]})
        write(OUT / "alertas" / f"{name}.json", {"arquivo": doc["arquivo"], "empresa": doc["empresa"],
                                                 "alertas": doc["alertas"]})
    answers = [answer for answer in current["respostas"] if not answer["pergunta_id"].startswith("livre")]
    write(OUT / "respostas.json", answers)
    lines = ["# Respostas às cinco perguntas", ""]
    for answer in sorted(answers, key=lambda answer: answer["pergunta_id"]):
        lines += [f"## {answer['pergunta_id'][1:]}. {answer['pergunta']}", "", answer["resposta"], "",
                  "Fontes: " + "; ".join(dict.fromkeys(answer["fontes"])) + ".", ""]
    write(OUT / "respostas.md", "\n".join(lines))
    if current["relatorio"]:
        write(OUT / "relatorio.md", current["relatorio"])

    gabarito = json.loads((ROOT / "avaliacao" / "gabarito.json").read_text(encoding="utf-8"))
    report = evaluate(gabarito, current)
    write(OUT / "avaliacao.md", "# Avaliação contra o gabarito\n\n"
          f"Base lida em {current['gerado_em']}. Gabarito: `avaliacao/gabarito.json`, conferido à mão nos PDFs.\n\n"
          + render(report))
    print(f"Entrega gerada em {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
