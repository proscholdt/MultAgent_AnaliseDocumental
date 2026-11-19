"""Marcadores: o texto do agente leva {{campo}}, e o código põe o valor gravado.

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
from typing import Any

from app.dominio.erros import ToolRejected
from app.regras.campos import FIELDS, MAIN_FIELDS

MARKER = re.compile(r"\{\{\s*([A-Za-z0-9_.]+)\s*\}\}")


_PARTS = ("informado", "comparavel", "variacao", "calculado")


def render_amount(text: str, unit: str | None) -> str:
    """O valor com a unidade, do jeito que se lê: "R$ 1.325.059 mil", "3,32x", "16,74%"."""
    text = text.strip()
    if unit in ("%", "x"):
        return text if text.endswith(unit) else f"{text}{unit}"
    if unit and unit.startswith("R$"):
        scale = unit[2:].strip()
        return f"R$ {text.replace('R$', '').strip()}" + (f" {scale}" if scale else "")
    return f"{text} {unit}" if unit else text


def source_pages(indicators: dict[str, Any], campo: str, part: str) -> list[int]:
    """As páginas de onde um valor sai. O calculado e a variação não têm página própria: saem
    dos valores de origem, e é a página deles que a resposta tem de citar."""
    block = indicators[campo]
    if part == "informado":
        informed = block["informado"]
        return [informed["origem"]["pagina"]] if informed and informed["status"] == "encontrado" else []
    if part == "comparavel":
        return [block["comparavel"]["origem"]["pagina"]] if block["comparavel"] else []
    if part == "variacao":
        return sorted(set(source_pages(indicators, campo, "informado") + source_pages(indicators, campo, "comparavel")))
    pages: set[int] = set()
    for operand in (block["calculado"] or {}).get("operandos", []):
        if operand.get("pagina"):
            pages.add(operand["pagina"])
        elif operand.get("formula") and operand["campo"] != campo:  # operando que também é calculado
            pages.update(source_pages(indicators, operand["campo"], "calculado"))
    return sorted(pages)


def _pages(pages: list[int]) -> str:
    return "p. " + (", ".join(map(str, pages[:-1])) + " e " + str(pages[-1]) if len(pages) > 1 else str(pages[0]))


def render_part(block: dict[str, Any], part: str, file_name: str, pages: list[int] | None = None,
                brief: bool = False) -> str | None:
    """O texto de uma parte de um indicador, ou None se essa parte não existe. `brief` é para o
    resumo executivo, que tem 5 linhas: o calculado sai sem a fórmula (ela está na tabela)."""
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
    if brief:
        return f"{render_amount(computed['valor'], computed['unidade'])} (calculado" + (
            f"; {file_name}, {_pages(pages)})" if pages else ")")
    origin = f"; valores de origem em {file_name}, {_pages(pages)}" if pages else ""
    return f"{render_amount(computed['valor'], computed['unidade'])} (calculado: {computed['formula']}{origin})"


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

    def render(self, text: str, default_doc: str | None = None, brief: bool = False) -> Rendered:
        result = Rendered(text="")

        def replace(match: re.Match[str]) -> str:
            result.markers += 1
            return self._resolve(match.group(1), default_doc, result, brief)

        result.text = MARKER.sub(replace, text)
        return result

    def _resolve(self, path: str, default_doc: str | None, result: Rendered, brief: bool = False) -> str:
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
        pages = source_pages(payload["indicadores"], campo, part)
        text = render_part(block, part, file_name, pages, brief)
        if text is None:
            available = [name for name in _PARTS if render_part(block, name, file_name) is not None]
            raise ToolRejected(f"Marcador {{{{{path}}}}}: {FIELDS[campo].label} não tem '{part}' neste documento.",
                               opcoes=[f"{campo}.{name}" for name in available])
        for page in pages:
            result.sources.append({"doc_id": doc_id, "arquivo": file_name, "pagina": page, "campo": campo})
        return text

    def _absence(self, path: str, doc_id: str, parts: list[str]) -> str:
        record = self._absences.get(doc_id, {}).get(parts[0] if parts else "")
        if record is None:
            raise ToolRejected(f"Marcador {{{{{path}}}}}: só existe para categoria da análise registrada como "
                               "ausente neste documento. Se há itens, cite o trecho com [[doc_id:chunk_index]].")
        terms = ", ".join(record["evidencia"]["termos_procurados"]) or "sem termos fixos"
        return f"não há no documento (procurado: {terms})"
