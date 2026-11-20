"""A linha de base: o relatório completo rodado várias vezes, com a nota, o custo, o tempo e a
variação entre as rodadas. É o número que uma mudança de prompt, modelo ou regra não pode piorar.

    uv run python scripts/baseline.py --usuario u --senha s --rodadas 3
    uv run python scripts/baseline.py --usuario u --senha s --rodadas 3 --gabarito avaliacao/cegos/gabarito.json
    uv run python scripts/baseline.py ... --zerar-entre      # cada rodada do zero (make zerar-base)

Sem --zerar-entre, a 2ª rodada em diante reaproveita a base e mede só o que falta refazer. Com ele,
antes de cada rodada roda `make zerar-base CONFIRMA=sim` na raiz: apaga a base do MCP e a memória
dos agentes (os PDFs indexados ficam). Custa cerca de US$ 0,30 por rodada.

Grava avaliacao/baseline/<data>.json com cada rodada e o resumo (média, mínimo e máximo).
"""
import asyncio
import json
import statistics
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from base import ROOT, arguments, login, settings, snapshot
from gerar_entrega import run_report

from app.ferramentas.avaliacao import evaluate

OUT = ROOT / "avaliacao" / "baseline"
# As taxas do resumo da avaliação que entram na linha de base (acertos / de).
METRICAS = ("status_certo", "valor_informado_certo", "pagina_certa", "valor_calculado_certo",
            "inconsistencias_na_fonte", "perguntas_certas")


def taxa(entrada: dict | None) -> float | None:
    if not entrada or not entrada.get("de"):
        return None
    return round(entrada["acertos"] / entrada["de"], 3)


def rodada(avaliacao: dict, execucao: dict) -> dict:
    resumo = avaliacao["resumo"]
    total = (execucao.get("uso") or {}).get("total") or {}
    inicio, fim = execucao.get("iniciada_em"), execucao.get("terminada_em")
    segundos = (datetime.fromisoformat(fim) - datetime.fromisoformat(inicio)).total_seconds() if inicio and fim else None
    return {"execucao_id": execucao.get("execucao_id") or execucao.get("id"), "estado": execucao.get("estado"),
            "segundos": segundos, "custo_usd": total.get("custo_usd"),
            "tokens": (total.get("uso") or {}).get("total"),
            "invencoes": resumo["invencao"]["inventados"],
            **{nome: taxa(resumo.get(nome)) for nome in METRICAS}}


def resumir(rodadas: list[dict]) -> dict:
    out = {}
    for chave in ("segundos", "custo_usd", "tokens", "invencoes", *METRICAS):
        valores = [r[chave] for r in rodadas if r.get(chave) is not None]
        if valores:
            out[chave] = {"media": round(statistics.fmean(valores), 3), "min": min(valores), "max": max(valores)}
    return out


async def main() -> int:
    parser = arguments("Roda o relatório completo várias vezes e grava a linha de base.")
    parser.add_argument("--rodadas", type=int, default=3)
    parser.add_argument("--api", default="http://localhost:8200", help="endereço da API do orquestrador")
    parser.add_argument("--gabarito", default=str(ROOT / "avaliacao" / "gabarito.json"))
    parser.add_argument("--zerar-entre", action="store_true",
                        help="antes de cada rodada, `make zerar-base CONFIRMA=sim` (apaga a base do MCP)")
    args = parser.parse_args()
    gabarito = json.loads(Path(args.gabarito).read_text(encoding="utf-8"))
    config = settings()
    rodadas = []
    for numero in range(1, args.rodadas + 1):
        if args.zerar_entre:
            await asyncio.to_thread(subprocess.run, ["make", "zerar-base", "CONFIRMA=sim"], cwd=ROOT.parent, check=True)
            await asyncio.sleep(15)  # os serviços voltando
        identity = await login(config, args.usuario, args.senha, f"baseline-{numero}", colecao=args.colecao)
        execucao = await run_report(args.api, identity.user_token, identity.colecao_id)
        avaliacao = evaluate(gabarito, await snapshot(config, identity))
        rodadas.append(rodada(avaliacao, execucao))
        print(f"rodada {numero}: " + ", ".join(f"{k}={v}" for k, v in rodadas[-1].items() if k != "execucao_id"))
    resultado = {"em": datetime.now(UTC).isoformat(timespec="seconds"), "gabarito": args.gabarito,
                 "do_zero": args.zerar_entre, "rodadas": rodadas, "resumo": resumir(rodadas)}
    OUT.mkdir(parents=True, exist_ok=True)
    arquivo = OUT / f"{datetime.now(UTC):%Y%m%d-%H%M%S}.json"
    arquivo.write_text(json.dumps(resultado, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(resultado["resumo"], ensure_ascii=False, indent=1))
    print(f"Linha de base em {arquivo}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
