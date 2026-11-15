"""Mede a qualidade do que está na base contra o gabarito conferido à mão.

    uv run python scripts/avaliar.py --usuario <usuário do indexador> --senha <senha>

Lê a base pelo servidor MCP (identidade "sistema", só leitura), compara com
avaliacao/gabarito.json e imprime as métricas. Guarda o retrato em avaliacao/resultados/ e,
se houver um retrato anterior, diz o quanto as duas execuções coincidem.

    --salvar-em <arquivo.md>   grava também a tabela de resultados em Markdown
    --sem-guardar              não grava o retrato (para olhar sem deixar rastro)
"""
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from base import ROOT, arguments, login, settings, snapshot

from app.ferramentas.avaliacao import evaluate, render, stability

RESULTS = ROOT / "avaliacao" / "resultados"


async def main() -> int:
    parser = arguments("Avalia a extração, os alertas e as respostas contra o gabarito.")
    parser.add_argument("--salvar-em", help="arquivo Markdown para a tabela de resultados")
    parser.add_argument("--sem-guardar", action="store_true", help="não grava o retrato desta avaliação")
    args = parser.parse_args()

    config = settings()
    identity = await login(config, args.usuario, args.senha, f"avaliacao-{datetime.now(UTC):%Y%m%d-%H%M%S}")
    current = await snapshot(config, identity)
    gabarito = json.loads((ROOT / "avaliacao" / "gabarito.json").read_text(encoding="utf-8"))
    report = evaluate(gabarito, current)
    table = render(report)
    print(table)

    previous = sorted(RESULTS.glob("retrato-*.json")) if RESULTS.is_dir() else []
    if previous:
        compared = stability(json.loads(previous[-1].read_text(encoding="utf-8")), current)
        print(f"\nEstabilidade contra {previous[-1].name}: {compared['iguais']} de {compared['campos']} indicadores "
              "com o mesmo status e o mesmo valor.")
        for item in compared["diferentes"]:
            print(f"  - {item['arquivo']} / {item['campo']}: {item['antes']} -> {item['depois']}")
    if not args.sem_guardar:
        RESULTS.mkdir(parents=True, exist_ok=True)
        stamp = f"{datetime.now(UTC):%Y%m%d-%H%M%S}"
        (RESULTS / f"retrato-{stamp}.json").write_text(json.dumps(current, ensure_ascii=False, indent=1), encoding="utf-8")
        (RESULTS / f"avaliacao-{stamp}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.salvar_em:
        Path(args.salvar_em).write_text(table + "\n", encoding="utf-8")
    summary = report["resumo"]
    # Sai com erro se houve invenção ou status errado: serve de verificação automática.
    return 0 if summary["invencao"]["inventados"] == 0 and summary["status_certo"]["acertos"] == summary["status_certo"]["de"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
