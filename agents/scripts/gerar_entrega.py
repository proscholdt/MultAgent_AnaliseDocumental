"""Gera os resultados a partir da base: extração, análise, perguntas, validação e relatório.

    uv run python scripts/gerar_entrega.py --usuario <usuário do indexador> --senha <senha>
        [--saida ../resultados] [--partes extracao,analise,perguntas,validacao,relatorio] [--rodar]

Lê a base de uma coleção (`--colecao`, ou a coleção padrão do usuário: a primeira do domínio
financeiro), com o gabarito do domínio dela. Por padrão só lê o que já está na base, sem custo. Com --rodar, antes pede o relatório completo
à API do orquestrador (POST /api/relatorios) e espera terminar; o resumo dessa execução (tempo,
tokens, custo por agente e por documento) vai junto. Pela raiz do projeto: `make resultados`,
`make extracao`, `make analise`, `make perguntas`, `make validacao`, `make relatorio`.

O que sai na pasta (uma subpasta por item do enunciado):

    2-extracao/<documento>.json          só os campos pedidos e só o que está escrito no documento
                                         (valor, unidade, ano anterior, variação quando a empresa a
                                         escreve, página e trecho), ou "não encontrado". Nada calculado
    2-extracao/completo/<documento>.json o JSON completo: valores de apoio, cálculos e validações
    3-analise/<documento>.json           a análise qualitativa, cada item com a citação e a página
    4-perguntas/respostas.md e .json     as 5 perguntas, com as fontes
    5-validacao/<documento>.json         inconsistências e informações não encontradas
    6-relatorio/relatorio.md             o relatório consolidado, como o servidor o montou
    6-relatorio/avaliacao.md             as métricas da base contra o gabarito
    execucao.json                        só com --rodar: estado, tempo, tokens e custo da execução
    LEIAME.md                            o que foi gerado, de quando é a base e o que faltou
"""
import asyncio
import json
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from base import ROOT, arguments, login, settings, snapshot

from app.ferramentas.avaliacao import evaluate, render
from app.ferramentas.avaliacao_automatica import gabarito_path
from app.orquestrador.grafo import latin_only

PARTS = {
    "extracao": ("2-extracao", "Extração estruturada (item 2): um JSON por documento"),
    "analise": ("3-analise", "Análise qualitativa (item 3): um JSON por documento"),
    "perguntas": ("4-perguntas", "As 5 perguntas (item 4), com as fontes"),
    "validacao": ("5-validacao", "Validação (item 5): inconsistências e não encontrados, por documento"),
    "relatorio": ("6-relatorio", "Relatório consolidado (item 6) e a nota contra o gabarito"),
}


def write(path: Path, content: str | dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, indent=2)
    path.write_text(text.rstrip("\n") + "\n", encoding="utf-8")


async def run_report(api: str, token: str, colecao_id: str | None = None) -> dict:
    """Pede o relatório ao orquestrador e acompanha até terminar."""
    async with httpx.AsyncClient(base_url=api, headers={"Authorization": f"Bearer {token}"}, timeout=60) as http:
        created = await http.post("/api/relatorios", json={"aguardar": False, "colecao_id": colecao_id})
        created.raise_for_status()
        run_id, started = created.json()["execucao_id"], time.monotonic()
        print(f"Execução {run_id} pedida aos agentes; acompanhando (leva de 2 a 5 min)...")
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


def export(current: dict, out: Path, parts: list[str], gabarito_file: Path) -> list[str]:
    """Grava as partes pedidas e devolve o que faltou na base para elas."""
    missing: list[str] = []
    for part in parts:
        shutil.rmtree(out / PARTS[part][0], ignore_errors=True)  # nada de arquivo de uma base anterior
    for doc in current["documentos"]:
        name, header = Path(doc["arquivo"]).stem, {"arquivo": doc["arquivo"], "empresa": doc["empresa"]}
        if "extracao" in parts:
            if doc.get("extracao_enxuta"):
                write(out / "2-extracao" / f"{name}.json", doc["extracao_enxuta"])
                write(out / "2-extracao" / "completo" / f"{name}.json", doc["extracao"])
            else:
                missing.append(f"extração de {doc['empresa'] or doc['arquivo']}")
        if "analise" in parts:
            # A análise vem por categoria; categoria sem item nenhum (nem "não encontrado") não foi feita.
            empty = [category for category, items in (doc["analise"] or {}).items() if not items]
            if empty:
                missing.append(f"análise de {doc['empresa'] or doc['arquivo']} ({', '.join(empty)})")
            write(out / "3-analise" / f"{name}.json", {**header, "analise": doc["analise"]})
        if "validacao" in parts:
            write(out / "5-validacao" / f"{name}.json", {**header, "alertas": doc["alertas"]})
    if "perguntas" in parts:
        answers = sorted((answer for answer in current["respostas"] if not answer["pergunta_id"].startswith("livre")),
                         key=lambda answer: answer["pergunta_id"])
        missing += [f"resposta à pergunta {n}" for n in range(1, 6)
                    if f"p{n}" not in {answer["pergunta_id"] for answer in answers}]
        write(out / "4-perguntas" / "respostas.json", answers)
        lines = ["# Respostas às cinco perguntas", ""]
        for answer in answers:
            lines += [f"## {answer['pergunta_id'][1:]}. {answer['pergunta']}", "", answer["resposta"], "",
                      "Fontes: " + "; ".join(dict.fromkeys(answer["fontes"])) + ".", ""]
        write(out / "4-perguntas" / "respostas.md", "\n".join(lines))
    if "relatorio" in parts:
        if current["relatorio"]:
            write(out / "6-relatorio" / "relatorio.md", current["relatorio"])
        else:
            missing.append("relatório consolidado montado")
        gabarito = json.loads(gabarito_file.read_text(encoding="utf-8"))
        shown = gabarito_file.resolve().relative_to(ROOT.parent).as_posix()
        write(out / "6-relatorio" / "avaliacao.md", "# Avaliação contra o gabarito\n\n"
              f"Base lida em {current['gerado_em']}. Gabarito: `{shown}`, conferido à mão "
              "nos PDFs.\n\n" + render(evaluate(gabarito, current)))
    return missing


def readme(current: dict, out: Path, missing: list[str]) -> None:
    """O LEIAME da pasta: o que existe nela agora (de qualquer comando), de quando é a base e o que faltou."""
    lines = ["# Resultados", "", f"Gerado a partir da base do servidor MCP em {current['gerado_em']}, "
             f"com {len(current['documentos'])} documento(s): "
             + ", ".join(doc["arquivo"] for doc in current["documentos"]) + ".", ""]
    for folder, description in PARTS.values():
        if (out / folder).exists():
            files = sorted(path.relative_to(out).as_posix() for path in (out / folder).rglob("*") if path.is_file())
            lines += [f"## {description}", "", *[f"- [{file}]({file})" for file in files], ""]
    if (out / "execucao.json").exists():
        lines += ["## Execução dos agentes", "", "- [execucao.json](execucao.json): tempo, tokens e custo", ""]
    if missing:
        lines += ["## O que faltou na base", "", *[f"- {item}" for item in missing], "",
                  "Para gerar com os agentes antes: o mesmo comando com `RODAR=sim` (cerca de US$ 0,15).", ""]
    write(out / "LEIAME.md", "\n".join(lines))


async def main() -> int:
    parser = arguments("Gera os resultados a partir da base.")
    parser.add_argument("--rodar", action="store_true", help="pede o relatório completo ao orquestrador antes")
    parser.add_argument("--api", default="http://localhost:8200", help="endereço da API do orquestrador")
    parser.add_argument("--saida", default=str(ROOT.parent / "resultados"), help="pasta dos resultados")
    parser.add_argument("--partes", default=",".join(PARTS),
                        help=f"quais itens gerar, separados por vírgula: {', '.join(PARTS)}")
    parser.add_argument("--retrato", help="gera a partir de um retrato guardado por avaliar.py "
                        "(avaliacao/resultados/retrato-*.json), sem ler a base nem pedir login")
    parser.add_argument("--execucao", help="com --retrato: o arquivo da execução que gerou aquele retrato "
                        "(data/execucoes/<id>.json), para o resumo de tempo, tokens e custo")
    args = parser.parse_args()
    out = Path(args.saida).resolve()
    parts = [part.strip() for part in args.partes.split(",") if part.strip()]
    if unknown := [part for part in parts if part not in PARTS]:
        parser.error(f"parte desconhecida: {', '.join(unknown)}. Use: {', '.join(PARTS)}")

    gabarito_file = ROOT / "avaliacao" / "gabarito.json"
    if args.retrato:
        current = json.loads(Path(args.retrato).read_text(encoding="utf-8"))
        if args.execucao:
            run = json.loads(Path(args.execucao).read_text(encoding="utf-8"))
            trace = Path(args.execucao).with_suffix(".jsonl")
            steps = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()] if trace.exists() else []
            run.pop("resultado", None)
            run.pop("user_id", None)
            run["mensagem"] = latin_only(run.get("mensagem", ""))  # como o serviço passou a gravar
            write(out / "execucao.json", {"execucao_id": run.pop("id"), **run, "passos": [
                {key: value for key, value in step.items() if key != "argumentos"} for step in steps]})
    else:
        config = settings()
        identity = await login(config, args.usuario, args.senha, f"entrega-{datetime.now(UTC):%Y%m%d-%H%M%S}",
                               colecao=args.colecao)
        if args.rodar:
            write(out / "execucao.json", await run_report(args.api, identity.user_token, identity.colecao_id))
        current = await snapshot(config, identity)
        gabarito_file = ROOT / gabarito_path(config, identity.dominio)
    if not current["documentos"]:
        print("A base não tem documento indexado para este usuário: envie os PDFs antes.")
        return 1

    missing = export(current, out, parts, gabarito_file)
    readme(current, out, missing)
    for part in parts:
        print(f"  {PARTS[part][0]:<13} {PARTS[part][1]}")
    if missing:
        print("Faltou na base: " + "; ".join(missing) + ".")
        print("Para gerar com os agentes antes, repita com RODAR=sim (cerca de US$ 0,15).")
    print(f"Resultados em {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
