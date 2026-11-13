"""Marcadores e montagem do relatório.

Os números do relatório nunca são digitados pelo modelo. No texto que ele escreve (resumo
executivo, respostas) vão marcadores, e o código põe no lugar o valor guardado, com a unidade
e a página:

    {{receita_liquida}}            valor informado, com a origem
    {{receita_liquida.variacao}}   variação contra o período comparável, calculada pelo código
    {{ebitda.calculado}}           valor calculado, com a fórmula
    {{lucro_liquido.comparavel}}   valor do período comparável

Nas respostas, que falam de várias empresas, o marcador leva o documento na frente:
{{doc_id.receita_liquida}}. As tabelas e as listas do relatório saem direto da base.
"""
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.errors import ToolRejected
from app.fields import CATEGORIES, FIELDS, MAIN_FIELDS, QUESTIONS

MARKER = re.compile(r"\{\{\s*([A-Za-z0-9_.]+)\s*\}\}")
_PARTS = ("informado", "comparavel", "variacao", "calculado")
_SEVERITY = {"atencao": "atenção", "informacao": "informação"}


def render_amount(text: str, unit: str | None) -> str:
    """O valor com a unidade, do jeito que se lê: "R$ 1.325.059 mil", "3,32x", "16,74%"."""
    text = text.strip()
    if unit in ("%", "x"):
        return text if text.endswith(unit) else f"{text}{unit}"
    if unit and unit.startswith("R$"):
        scale = unit[2:].strip()
        return f"R$ {text.replace('R$', '').strip()}" + (f" {scale}" if scale else "")
    return f"{text} {unit}" if unit else text


def render_part(block: dict[str, Any], part: str, file_name: str) -> str | None:
    """O texto de uma parte de um indicador, ou None se essa parte não existe."""
    if part == "informado":
        informed = block["informado"]
        if not informed or informed["status"] != "encontrado":
            return "não informado no documento"
        return f"{render_amount(informed['valor'], informed['unidade'])} ({file_name}, p. {informed['origem']['pagina']})"
    if part == "comparavel":
        previous = block["comparavel"]
        if not previous:
            return None
        return f"{render_amount(previous['valor'], previous['unidade'])} ({file_name}, p. {previous['origem']['pagina']})"
    if part == "variacao":
        computed = (block["variacao"] or {}).get("calculada")
        if not computed:
            return None
        sign = "+" if computed["valor_numerico"] > 0 else ""
        note = " (mudou de sinal)" if computed.get("mudanca_de_sinal") else ""
        return f"{sign}{computed['valor']}{note}"
    computed = block["calculado"]
    if not computed:
        return None
    return f"{render_amount(computed['valor'], computed['unidade'])} (calculado: {computed['formula']})"


@dataclass
class Rendered:
    text: str
    sources: list[dict[str, Any]] = field(default_factory=list)  # documento e página de cada valor usado
    markers: int = 0


class Markers:
    """Troca os marcadores de um texto pelos valores das extrações publicadas."""

    def __init__(self, extractions: dict[str, dict[str, Any]], absences: dict[str, dict[str, Any]] | None = None):
        self._extractions = extractions  # doc_id → JSON da extração
        self._absences = absences or {}  # doc_id → {categoria: registro de ausência da análise}

    def render(self, text: str, default_doc: str | None = None) -> Rendered:
        result = Rendered(text="")

        def replace(match: re.Match[str]) -> str:
            result.markers += 1
            return self._resolve(match.group(1), default_doc, result)

        result.text = MARKER.sub(replace, text)
        return result

    def _resolve(self, path: str, default_doc: str | None, result: Rendered) -> str:
        parts = path.split(".")
        doc_id = default_doc
        if parts[0] in self._extractions or (parts[0] not in FIELDS and parts[0] != "analise"):
            doc_id, parts = parts[0], parts[1:]
        if doc_id is None or doc_id not in self._extractions:
            known = ", ".join(self._extractions) or "nenhum"
            raise ToolRejected(f"Marcador {{{{{path}}}}}: documento sem extração publicada. Documentos com "
                               f"extração: {known}.")
        payload = self._extractions[doc_id]
        file_name = payload["documento"]["arquivo"]
        if parts and parts[0] == "analise":
            return self._absence(path, doc_id, parts[1:])
        if not parts or parts[0] not in MAIN_FIELDS:
            raise ToolRejected(f"Marcador {{{{{path}}}}}: campo desconhecido.", opcoes=list(MAIN_FIELDS))
        campo, part = parts[0], parts[1] if len(parts) > 1 else "informado"
        if part not in _PARTS or len(parts) > 2:
            raise ToolRejected(f"Marcador {{{{{path}}}}}: parte desconhecida.", opcoes=list(_PARTS))
        block = payload["indicadores"][campo]
        text = render_part(block, part, file_name)
        if text is None:
            available = [name for name in _PARTS if render_part(block, name, file_name) is not None]
            raise ToolRejected(f"Marcador {{{{{path}}}}}: {FIELDS[campo].label} não tem '{part}' neste documento.",
                               opcoes=[f"{campo}.{name}" for name in available])
        source = block["informado"] if part == "informado" else block["comparavel"] if part == "comparavel" else None
        if source and source.get("status") == "encontrado":
            result.sources.append({"doc_id": doc_id, "arquivo": file_name, "pagina": source["origem"]["pagina"],
                                   "campo": campo})
        return text

    def _absence(self, path: str, doc_id: str, parts: list[str]) -> str:
        record = self._absences.get(doc_id, {}).get(parts[0] if parts else "")
        if record is None:
            raise ToolRejected(f"Marcador {{{{{path}}}}}: só existe para categoria da análise registrada como "
                               "ausente neste documento. Se há itens, cite o trecho com [[doc_id:chunk_index]].")
        terms = ", ".join(record["evidencia"]["termos_procurados"]) or "sem termos fixos"
        return f"não há no documento (procurado: {terms})"


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


def build_report(documents: list[dict[str, Any]], answers: list[dict[str, Any]], generated_at: datetime) -> str:
    """O relatório consolidado, em Markdown. `documents` traz, por documento: o JSON da
    extração, a análise por categoria, os alertas e o resumo já renderizado."""
    names = [doc["extracao"]["documento"]["empresa"] or doc["extracao"]["documento"]["arquivo"] for doc in documents]
    how_to_read = ("Todo número deste relatório foi lido do documento pelo código (célula de tabela ou trecho "
                   "conferido) ou calculado por fórmula fixa a partir desses valores. O que a empresa não informa "
                   "aparece como \"não informado\", e o que foi calculado vem marcado como calculado.")
    out = ["# Relatório de análise das demonstrações financeiras", "",
           f"Gerado em {generated_at:%d/%m/%Y %H:%M} UTC. Empresas: {'; '.join(names)}.".replace("..", "."), "", how_to_read, ""]

    out += ["## 1. Resumo executivo", ""]
    for name, doc in zip(names, documents, strict=True):
        meta = doc["extracao"]["documento"]
        out += [f"### {name} ({meta['periodo_referencia']})", "", doc["resumo"] or "_Resumo não gerado._", ""]

    out += ["## 2. Indicadores comparados", "",
            "| Indicador | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    for campo in MAIN_FIELDS:
        cells = [_cell(doc["extracao"]["indicadores"][campo]) for doc in documents]
        out.append(f"| {FIELDS[campo].label} | " + " | ".join(cells) + " |")
    periods = {(doc["extracao"]["documento"]["periodo_referencia"], doc["extracao"]["documento"]["periodo_comparavel"])
               for doc in documents}
    compared = "; ".join(f"{ref} contra {comp}" for ref, comp in sorted(periods))
    legend = (f"Valores do período de referência; \"var.\" é a variação contra o período comparável ({compared}), "
              "calculada pelo código. A página é a do PDF.")
    if any(" ¹" in line for line in out):
        legend += " ¹ O sinal mudou de um período para o outro: o percentual não tem leitura direta."
    out += ["", legend, ""]
    formulas = {computed["formula"] for doc in documents for block in doc["extracao"]["indicadores"].values()
                if (computed := block["calculado"])}
    if formulas:
        out += ["Fórmulas dos valores calculados:", ""] + [f"- {formula}" for formula in sorted(formulas)] + [""]

    out += ["## 3. Análise qualitativa", ""]
    for name, doc in zip(names, documents, strict=True):
        out += [f"### {name}", ""]
        for categoria, (label, _) in CATEGORIES.items():
            out.append(f"**{label}**")
            out.append("")
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
        out += [f"### {name}", ""]
        missing = [line for block in doc["extracao"]["indicadores"].values() if (line := _not_found(block))]
        out += [f"- {line}" for line in missing] or ["- Todos os indicadores foram informados pela empresa."]
        out.append("")
        alerts = doc["alertas"]
        if not alerts:
            out += ["- Nenhum alerta.", ""]
            continue
        for alert in sorted(alerts, key=lambda a: a["severidade"] != "atencao"):
            details = alert.get("detalhes") or {}
            page = f" (p. {details['pagina']})" if details.get("pagina") else ""
            quote = f"  \n  > \"{details['citacao']}\"" if details.get("citacao") else ""
            out.append(f"- [{_SEVERITY.get(alert['severidade'], alert['severidade'])}] {alert['mensagem']}{page}{quote}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"
