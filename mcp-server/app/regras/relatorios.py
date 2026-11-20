"""Montagem dos relatórios em Markdown: o consolidado e o de um documento. As tabelas e as listas
saem direto da base, e os números entram pelos marcadores (ver marcadores.py)."""
from datetime import datetime
from typing import Any

from app.regras.campos import CATEGORIES, FIELDS, MAIN_FIELDS, QUESTIONS, atual
from app.regras.marcadores import render_amount

_SEVERITY = {"atencao": "atenção", "informacao": "informação"}


# ---------------------------------------------------------------------------------------------
# Relatório
# ---------------------------------------------------------------------------------------------


def _cell(block: dict[str, Any]) -> str:
    """Uma célula da tabela comparativa: o informado (ou a falta dele), a variação e o calculado."""
    informed, lines = block["informado"], []
    if informed and informed["status"] == "encontrado":
        lines.append(f"{render_amount(informed['valor'], informed['unidade'])} (p. {informed['origem']['pagina']})")
    else:
        lines.append("não informado")
    variation = (block["variacao"] or {}).get("calculada")
    if variation:
        sign = "+" if variation["valor_numerico"] > 0 else ""
        lines.append(f"var.: {sign}{variation['valor']}" + (" ¹" if variation.get("mudanca_de_sinal") else ""))
    if block["calculado"]:
        computed = block["calculado"]
        lines.append(f"calculado: {render_amount(computed['valor'], computed['unidade'])}")
    return "<br>".join(lines)


def _not_found(block: dict[str, Any]) -> str | None:
    informed = block["informado"]
    if not informed or informed["status"] != "nao_encontrado":
        return None
    evidence = informed.get("evidencia") or {}
    pages = evidence.get("paginas_descartadas") or []
    searched = ", ".join(evidence.get("termos_procurados") or [])
    where = f" Os termos aparecem nas p. {', '.join(map(str, pages))}, descartadas." if pages else \
        " Os termos não aparecem em nenhuma página."
    return f"**{block['rotulo']}: não encontrado.** {informed.get('motivo') or ''} (Procurado: {searched}.{where})"


def _textos() -> Any:
    """Como o relatório fala no domínio da chamada (textos.relatorio do pacote)."""
    return atual().pacote.textos.relatorio


def _name(doc: dict[str, Any]) -> str:
    return doc["extracao"]["documento"]["entidade"] or doc["extracao"]["documento"]["arquivo"]


def _formulas(documents: list[dict[str, Any]]) -> list[str]:
    formulas = {computed["formula"] for doc in documents for block in doc["extracao"]["campos"].values()
                if (computed := block["calculado"])}
    return ["Fórmulas dos valores calculados:", ""] + [f"- {formula}" for formula in sorted(formulas)] + [""] \
        if formulas else []


def _analysis_lines(doc: dict[str, Any]) -> list[str]:
    """A análise qualitativa de um documento, categoria por categoria, com as citações."""
    out: list[str] = []
    for categoria, (label, _) in CATEGORIES.items():
        out += [f"**{label}**", ""]
        items = doc["analise"].get(categoria) or []
        if not items:
            out += ["- _Não analisado._", ""]
            continue
        for item in items:
            if item.get("status") == "nao_encontrado":
                out.append(f"- Não encontrado no documento. {item['motivo']}")
            else:
                tone = f" **Tom: {item['tom']}.**" if item.get("tom") else ""
                out.append(f"-{tone} {item['afirmacao']}  \n  > \"{item['citacao']}\" (p. {item['pagina']}, "
                           f"{item['fonte']})")
        out.append("")
    return out


def _alert_lines(doc: dict[str, Any]) -> list[str]:
    """O que o documento não informa e os alertas, os de atenção primeiro."""
    missing = [line for block in doc["extracao"]["campos"].values() if (line := _not_found(block))]
    complete = f"- {_textos().todos_encontrados}"
    out = [f"- {line}" for line in missing] or [complete]
    out.append("")
    alerts = doc["alertas"]
    if not alerts:
        return out + ["- Nenhum alerta.", ""]
    for alert in sorted(alerts, key=lambda a: a["severidade"] != "atencao"):
        details = alert.get("detalhes") or {}
        page = f" (p. {details['pagina']})" if details.get("pagina") else ""
        quote = f"  \n  > \"{details['citacao']}\"" if details.get("citacao") else ""
        out.append(f"- [{_SEVERITY.get(alert['severidade'], alert['severidade'])}] {alert['mensagem']}{page}{quote}")
    return out + [""]


def build_company_report(doc: dict[str, Any], generated_at: datetime) -> str:
    """O relatório de um documento só: resumo, campos, análise e alertas. As perguntas
    comparativas não entram (comparam os documentos: são do relatório consolidado)."""
    name, meta = _name(doc), doc["extracao"]["documento"]
    recorte = atual().pacote.recorte
    when = (f"Período {meta['periodo_referencia']}, comparado com {meta['periodo_comparavel']}."
            if meta.get("periodo_comparavel") else f"{recorte.rotulo}: {meta['periodo_referencia']}.")
    heading = f"Gerado em {generated_at:%d/%m/%Y %H:%M} UTC. Documento: {meta['arquivo']}. {when}"
    out = [f"# Relatório de análise: {name}", "", heading, "", _textos().como_ler, ""]
    out += ["## 1. Resumo executivo", "", doc["resumo"] or "_Resumo não gerado._", ""]
    out += [f"## 2. {_textos().secao_campos}", "", f"| {_textos().campo} | Valor |", "|---|---|"]
    for campo in MAIN_FIELDS:
        out.append(f"| {FIELDS[campo].label} | {_cell(doc['extracao']['campos'][campo])} |")
    legend = (f"Valores de {meta['periodo_referencia']}; \"var.\" é a variação contra {meta['periodo_comparavel']}, "
              "calculada pelo código. A página é a do PDF.") if meta.get("periodo_comparavel") \
        else f"Valores do documento de {meta['periodo_referencia']}. A página é a do PDF."
    if any(" ¹" in line for line in out):
        legend += " ¹ O sinal mudou de um período para o outro: o percentual não tem leitura direta."
    out += ["", legend, ""] + _formulas([doc])
    out += ["## 3. Análise qualitativa", ""] + _analysis_lines(doc)
    out += ["## 4. Alertas e informações não encontradas", ""] + _alert_lines(doc)
    return "\n".join(out).rstrip() + "\n"


def build_report(documents: list[dict[str, Any]], answers: list[dict[str, Any]], generated_at: datetime) -> str:
    """O relatório consolidado, em Markdown. `documents` traz, por documento: o JSON da
    extração, a análise por categoria, os alertas e o resumo já renderizado."""
    names = [_name(doc) for doc in documents]
    pacote = atual().pacote
    title = f"# {_textos().titulo or f'Relatório de análise: {pacote.nome}'}"
    out = [title, "",
           f"Gerado em {generated_at:%d/%m/%Y %H:%M} UTC. {pacote.entidade.plural}: {'; '.join(names)}."
           .replace("..", "."), "", _textos().como_ler, ""]

    out += ["## 1. Resumo executivo", ""]
    for name, doc in zip(names, documents, strict=True):
        meta = doc["extracao"]["documento"]
        out += [f"### {name} ({meta['periodo_referencia']})", "", doc["resumo"] or "_Resumo não gerado._", ""]

    out += [f"## 2. {_textos().secao_comparados}", "",
            f"| {_textos().campo} | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    for campo in MAIN_FIELDS:
        cells = [_cell(doc["extracao"]["campos"][campo]) for doc in documents]
        out.append(f"| {FIELDS[campo].label} | " + " | ".join(cells) + " |")
    periods = {(doc["extracao"]["documento"]["periodo_referencia"], doc["extracao"]["documento"]["periodo_comparavel"])
               for doc in documents if doc["extracao"]["documento"].get("periodo_comparavel")}
    compared = "; ".join(f"{ref} contra {comp}" for ref, comp in sorted(periods))
    # Sem comparável (laudo de um dia, documento sem recorte), não há variação para explicar.
    legend = (f"Valores do período de referência; \"var.\" é a variação contra o período comparável ({compared}), "
              "calculada pelo código. A página é a do PDF.") if periods \
        else "Valores de cada documento, na unidade dele. A página é a do PDF."
    if any(" ¹" in line for line in out):
        legend += " ¹ O sinal mudou de um período para o outro: o percentual não tem leitura direta."
    out += ["", legend, ""] + _formulas(documents)

    out += ["## 3. Análise qualitativa", ""]
    for name, doc in zip(names, documents, strict=True):
        out += [f"### {name}", ""] + _analysis_lines(doc)

    out += ["## 4. Perguntas e respostas", ""]
    by_id = {answer["pergunta_id"]: answer for answer in answers}
    for number, (question_id, question) in enumerate(QUESTIONS.items(), start=1):
        out += [f"### {number}. {question}", ""]
        answer = by_id.get(question_id)
        out += [answer["renderizado"] if answer else "_Sem resposta._", ""]
        if answer and answer["citacoes"]:
            sources = dict.fromkeys(f"{c['arquivo']}, p. {c['pagina']}" for c in answer["citacoes"])
            out += ["Fontes: " + "; ".join(sources) + ".", ""]

    out += ["## 5. Alertas e informações não encontradas", ""]
    for name, doc in zip(names, documents, strict=True):
        out += [f"### {name}", ""] + _alert_lines(doc)
    return "\n".join(out).rstrip() + "\n"
