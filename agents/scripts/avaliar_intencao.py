"""Mede a classificação de intenção do chat contra mensagens rotuladas (dominios/<id>/avaliacao/intencoes.yaml).

    uv run python scripts/avaliar_intencao.py              # o roteador semântico (centésimos de centavo)
    uv run python scripts/avaliar_intencao.py --com-llm    # e o LLM com as regras do orquestrador (~US$ 0,01)
    uv run python scripts/avaliar_intencao.py --varrer     # acerto por limiar e margem, no conjunto de ajuste

Três números, no conjunto de teste:
- roteador: quantas ele decide (cobertura) e quantas das que decide estão certas (precisão);
- LLM sozinho: o orquestrador classificando tudo, como era antes do roteador;
- os dois juntos, como roda hoje: o roteador decide quando tem certeza, o LLM decide o resto.

O LLM aqui recebe só a seção "Intenção" do prompt do orquestrador, a lista dos documentos e o pedido
anterior: é a regra de classificação dele, não a primeira decisão inteira (que também delega).
"""
import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.orquestrador.intencao import Roteador, embedder_openai, empresas_citadas

CONJUNTO = ROOT.parent / "dominios" / "financeiro" / "avaliacao" / "intencoes.yaml"
ROTAS = ROOT.parent / "dominios" / "financeiro" / "intencoes.yaml"
ORQUESTRADOR = ROOT / "agentes" / "orquestrador" / "orquestrador.md"
INTENCOES = ["pergunta", "relatorio", "relatorio_documento", "fora_do_escopo"]


def decisao(escolha, mensagem: dict, documentos: list[dict], limiar: float, margem: float):
    """A intenção que o roteador daria com outro limiar e outra margem (sem chamar a API de novo)."""
    if escolha.rota is None or escolha.semelhanca < limiar or escolha.margem < margem:
        return None, None
    if escolha.rota != "relatorio":
        return escolha.rota, None
    citadas = empresas_citadas(mensagem["texto"], documentos) or (
        empresas_citadas(mensagem.get("anterior") or "", documentos) if escolha.seguimento else [])
    return ("relatorio_documento", citadas[0]["doc_id"]) if len(citadas) == 1 else ("relatorio", None)


def certo(intencao, empresa, mensagem: dict) -> bool:
    return intencao == mensagem["esperado"] and (intencao != "relatorio_documento" or empresa == mensagem.get("empresa"))


async def classificar_llm(client, modelo: str, esforco: str | None, regras: str, documentos: list[dict],
                          mensagem: dict) -> tuple[str, str | None, int, int, float]:
    lista = "\n".join(f"- {d['doc_id']}: {d.get('entidade') or d.get('empresa')}" for d in documentos)
    sistema = (f"{regras}\n\nDocumentos do usuário (doc_id: empresa):\n{lista}\n\n"
               "Devolva a intenção da mensagem atual e, só em relatorio_documento, o doc_id da empresa em `empresa`.")
    pedido = mensagem["texto"]
    if mensagem.get("anterior"):
        pedido = f"## Conversa até aqui\n- Pedido: {mensagem['anterior']}\n\n## Pedido atual\n{pedido}"
    esquema = {"type": "object", "additionalProperties": False, "required": ["intencao", "empresa"],
               "properties": {"intencao": {"type": "string", "enum": INTENCOES},
                              "empresa": {"type": ["string", "null"]}}}
    inicio = time.monotonic()
    extra = {"reasoning_effort": esforco} if esforco else {}
    resposta = await client.chat.completions.create(
        model=modelo, messages=[{"role": "system", "content": sistema}, {"role": "user", "content": pedido}],
        response_format={"type": "json_schema", "json_schema": {"name": "intencao", "strict": True, "schema": esquema}},
        **extra)
    dados = json.loads(resposta.choices[0].message.content)
    return (dados["intencao"], dados.get("empresa"), resposta.usage.prompt_tokens, resposta.usage.completion_tokens,
            time.monotonic() - inicio)


def regras_do_orquestrador() -> tuple[str, str, str | None]:
    texto = ORQUESTRADOR.read_text(encoding="utf-8")
    secao = texto[texto.index("## Intenção"):]
    secao = secao[:secao.index("\n## ", 5)]
    modelo = re.search(r"^modelo:\s*(\S+)", texto, re.MULTILINE).group(1)
    esforco = re.search(r"^raciocinio:\s*(\S+)", texto, re.MULTILINE)
    return secao, modelo, (esforco.group(1) if esforco and esforco.group(1) != "nenhum" else None)


def pct(a: int, b: int) -> str:
    return f"{a} de {b} ({100 * a / b:.0f}%)" if b else "—"


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--com-llm", action="store_true", help="classifica também com o LLM, para comparar")
    parser.add_argument("--varrer", action="store_true", help="acerto por limiar e margem no conjunto de ajuste")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    settings = get_settings()
    dados = yaml.safe_load(CONJUNTO.read_text(encoding="utf-8"))
    documentos, mensagens = dados["documentos"], dados["mensagens"]
    roteador = Roteador(settings.intencoes_path or ROTAS, embedder_openai(settings.openai_api_key))
    rotas, _ = await roteador.rotas()

    tokens_roteador, tempos = 0, []
    escolhas = []
    for mensagem in mensagens:
        inicio = time.monotonic()
        escolha = await roteador.escolher(mensagem["texto"], mensagem.get("anterior"), documentos)
        tempos.append(time.monotonic() - inicio)
        tokens_roteador += escolha.tokens
        escolhas.append(escolha)

    if args.varrer:
        ajuste = [(e, m) for e, m in zip(escolhas, mensagens, strict=True) if m["conjunto"] == "ajuste"]
        print(f"Conjunto de ajuste ({len(ajuste)} mensagens): cobertura e precisão por limiar e margem\n")
        print("| limiar | margem | decide | certas entre as decididas | erradas |")
        print("|---|---|---|---|---|")
        for limiar in (0.40, 0.45, 0.50, 0.55, 0.60, 0.65):
            for margem in (0.0, 0.03, 0.06, 0.10):
                ditas = [(decisao(e, m, documentos, limiar, margem), m) for e, m in ajuste]
                feitas = [(d, m) for d, m in ditas if d[0] is not None]
                boas = sum(certo(d[0], d[1], m) for d, m in feitas)
                print(f"| {limiar:.2f} | {margem:.2f} | {len(feitas)} | {pct(boas, len(feitas))} | {len(feitas) - boas} |")
        print()

    llm = [None] * len(mensagens)
    tokens_llm = [0, 0]
    tempos_llm = []
    if args.com_llm:
        from openai import AsyncOpenAI

        regras, modelo, esforco = regras_do_orquestrador()
        client = AsyncOpenAI(api_key=settings.openai_api_key)
        for i, mensagem in enumerate(mensagens):
            intencao, empresa, entrada, saida, gasto = await classificar_llm(client, modelo, esforco, regras,
                                                                             documentos, mensagem)
            llm[i] = (intencao, empresa)
            tokens_llm[0] += entrada
            tokens_llm[1] += saida
            tempos_llm.append(gasto)

    print(f"Roteador: limiar {rotas.limiar}, margem {rotas.margem}, {rotas.modelo}\n")
    print("| conjunto | mensagem | esperado | roteador | semelhança | margem | " + ("LLM | " if args.com_llm else "")
          + "certo |")
    print("|---|---|---|---|---|---|" + ("---|" if args.com_llm else "") + "---|")
    for i, (escolha, mensagem) in enumerate(zip(escolhas, mensagens, strict=True)):
        texto = (f"[{mensagem['anterior']}] " if mensagem.get("anterior") else "") + mensagem["texto"]
        esperado = mensagem["esperado"] + (f" ({mensagem['empresa']})" if mensagem.get("empresa") else "")
        dito = (escolha.intencao + (f" ({escolha.doc_id})" if escolha.doc_id else "")) if escolha.intencao else \
            f"— ({escolha.rota}?)"
        final = (escolha.intencao, escolha.doc_id) if escolha.intencao else (llm[i] or (None, None))
        coluna_llm = (f"{llm[i][0]}{f' ({llm[i][1]})' if llm[i][1] else ''} | " if args.com_llm else "")
        marca = "sim" if certo(final[0], final[1], mensagem) else ("—" if final[0] is None else "**não**")
        print(f"| {mensagem['conjunto']} | {texto} | {esperado} | {dito} | {escolha.semelhanca:.2f} | "
              f"{escolha.margem:.2f} | {coluna_llm}{marca} |")

    teste = [i for i, m in enumerate(mensagens) if m["conjunto"] == "teste"]
    decididas = [i for i in teste if escolhas[i].intencao]
    certas_roteador = sum(certo(escolhas[i].intencao, escolhas[i].doc_id, mensagens[i]) for i in decididas)
    print(f"\n## Conjunto de teste: {len(teste)} mensagens\n")
    print(f"- Roteador decide: {pct(len(decididas), len(teste))}; certas entre as que decide: "
          f"{pct(certas_roteador, len(decididas))}")
    print(f"- Roteador, tempo médio por mensagem: {1000 * sum(tempos) / len(tempos):.0f} ms; "
          f"embeddings: {tokens_roteador} tokens em todo o conjunto")
    if args.com_llm:
        so_llm = sum(certo(llm[i][0], llm[i][1], mensagens[i]) for i in teste)
        juntos = sum(certo(*((escolhas[i].intencao, escolhas[i].doc_id) if escolhas[i].intencao else llm[i]),
                           mensagens[i]) for i in teste)
        llm_nas_do_roteador = sum(certo(llm[i][0], llm[i][1], mensagens[i]) for i in decididas)
        print(f"- LLM sozinho: {pct(so_llm, len(teste))}; nas que o roteador decide, o LLM acerta "
              f"{pct(llm_nas_do_roteador, len(decididas))}")
        print(f"- Roteador + LLM (como roda hoje): {pct(juntos, len(teste))}")
        print(f"- LLM, tempo médio por mensagem: {1000 * sum(tempos_llm) / len(tempos_llm):.0f} ms; "
              f"tokens: {tokens_llm[0]} de entrada e {tokens_llm[1]} de saída em todo o conjunto")


if __name__ == "__main__":
    asyncio.run(main())
