"""Evidência no documento: onde um indicador aparece, a prova de que ele não existe e o EBITDA que reconcilia."""
import re
from decimal import Decimal
from typing import Any

from app.dominio.documento import IndexedDocument
from app.regras.campos import FIELDS
from app.regras.formulas import Extraction
from app.regras.numeros import (
    in_reais,
    number_tokens,
    parse_number,
    to_decimal,
    unit_of_number,
)
from app.regras.tabelas import parse_table
from app.regras.texto import plain, squash


def _statement_tables(document: IndexedDocument, section: str | None) -> list[dict[str, Any]]:
    """As tabelas da demonstração em que o campo mora (resultado, fluxo, balanço), pelo título da seção."""
    return [chunk for chunk in document.chunks if chunk["chunk_type"] == "table"
            and (section is None or section in " > ".join(chunk.get("section_path") or []).casefold())]


def support_rows(document: IndexedDocument, campo: str) -> dict[int, str]:
    """Páginas em que há uma linha de tabela que serviria de valor de apoio para o campo, com o
    rótulo dela. É a evidência contra "o documento não traz as linhas de apoio"."""
    info = FIELDS[campo]
    found: dict[int, str] = {}
    for chunk in _statement_tables(document, info.section):
        table = parse_table(chunk["text"])
        for row in (table.rows if table else []):
            if row[0] and mentions(info, plain(row[0])) and any(parse_number(cell) for cell in row[1:]):
                found.setdefault(chunk["page_start"], row[0])
    return found


# Os indicadores que a empresa informa com o próprio nome. Só para eles vale a regra de que uma
# linha ou frase com valor não pode ser descartada. O capex ficou de fora depois da 7ª execução
# real: "BNDES - Capex" é o nome de um financiamento da Light, a regra o tomou por capex informado,
# e o campo ficou sem saída (não podia ser dado como ausente nem gravado).
_NAMED = ("ebitda", "margem_ebitda", "divida_liquida", "alavancagem")


# O capex informado em tabela é uma linha de investimentos, não uma linha qualquer com a palavra.
_CAPEX_ROW = re.compile(r"(capex|investimentos?\b|total de investimento)")


_SENTENCE = re.compile(r"(?<=[.;])\s+")


_HAS = {"valor": re.compile(r"R\$\s?\d"), "percentual": re.compile(r"\d\s?%"), "multiplo": re.compile(r"\d\s?x\b")}


# Rótulos de razão: "Dívida líquida/EBITDA" não é linha de EBITDA nem de dívida líquida.
_RATIO = re.compile(r"/|\bsobre\b|\brelacao\b|\bindice\b|\bmargem\b")


def informed_evidence(document: IndexedDocument, campo: str) -> list[tuple[int, str]]:
    """Onde o documento traz o indicador COM um valor do tipo dele: linha de tabela com o nome do
    indicador e um número, ou frase com o nome e um valor (R$, %, x). Isso não pode ser descartado
    como "não encontrado": na 4ª execução real a dívida líquida da Millet, que está numa tabela,
    e a alavancagem da Light, que está numa frase, foram dadas como ausentes."""
    info = FIELDS[campo]
    if campo not in _NAMED:
        return []
    terms = tuple(plain(term) for term in info.terms)
    rows, sentences = [], []
    for chunk in document.chunks:
        page = chunk["page_start"]
        if chunk["chunk_type"] == "table":
            table = parse_table(chunk["text"])
            for row in (table.rows if table else []):
                label = plain(row[0])
                if not any(term in label for term in terms) or not mentions(info, label) \
                        or (info.kind == "valor" and _RATIO.search(label)):
                    continue
                kinds = {number.kind for cell in row[1:] if (number := parse_number(cell))}
                if info.kind in kinds or (info.kind == "percentual" and kinds and "%" in row[0]):
                    rows.append((page, f"linha de tabela '{row[0]}' (chunk {chunk['chunk_index']})"))
        elif chunk["chunk_type"] == "text":
            for sentence in _SENTENCE.split(squash(chunk["text"])):
                folded = plain(sentence)
                if any(term in folded for term in terms) and mentions(info, folded) and _HAS[info.kind].search(sentence):
                    sentences.append((page, f"frase '{sentence[:170]}' (chunk {chunk['chunk_index']})"))
    return rows + sentences  # as tabelas primeiro: é delas que o valor deve vir, quando há


_RECONCILES = Decimal("0.02")  # diferença relativa aceita entre o EBITDA do texto e o da demonstração


def reconciling_ebitda(document: IndexedDocument, extraction: Extraction) -> str | None:
    """Um relatório de grupo traz o EBITDA de cada controlada e o consolidado, em frases parecidas.
    O consolidado é o que bate, em módulo, com a demonstração (resultado antes do financeiro mais
    depreciação). Se o EBITDA gravado veio de texto e não bate, e outra frase de EBITDA do documento
    bate, devolve essa frase: é ela que deve ser gravada. Na 8ª execução real o EBITDA da Light
    voltou a ser o da distribuidora (R$1.817 milhões), com uma nota; o consolidado (R$1.180 milhões)
    estava na página seguinte."""
    informed = extraction.found("ebitda", extraction.reference)
    computed = extraction.calculated("ebitda", extraction.reference)
    if informed is None or informed.origem != "texto" or informed.valor is None or not computed:
        return None
    amount = in_reais(informed.valor, informed.unidade)
    target = in_reais(Decimal(str(computed["valor_numerico"])), computed["unidade"])
    if amount is None or not target or abs(abs(amount) - abs(target)) / abs(target) <= _RECONCILES:
        return None
    info, terms = FIELDS["ebitda"], tuple(plain(term) for term in FIELDS["ebitda"].terms)
    for chunk in document.chunks:
        if chunk["chunk_type"] != "text":
            continue
        for sentence in _SENTENCE.split(squash(chunk["text"])):
            folded = plain(sentence)
            if not (any(term in folded for term in terms) and mentions(info, folded)):
                continue
            for token in number_tokens(sentence):
                value, unit = to_decimal(token), unit_of_number(sentence, token)
                candidate = in_reais(value, unit) if value is not None and unit else None
                if candidate and abs(abs(candidate) - abs(target)) / abs(target) <= _RECONCILES:
                    return f"p. {chunk['page_start']} (chunk {chunk['chunk_index']}): '{sentence[:220]}'"
    return None


def occurrences(document: IndexedDocument, terms: tuple[str, ...]) -> dict[int, str]:
    """Páginas em que algum termo do campo aparece, com um pedaço do texto em volta."""
    found: dict[int, str] = {}
    for chunk in document.chunks:
        text = _squash(chunk["text"])
        folded = text.casefold()
        position = min((p for term in terms if (p := folded.find(term)) >= 0), default=-1)
        if position < 0:
            continue
        excerpt = text[max(0, position - _EXCERPT):position + _EXCERPT]
        for page in range(chunk["page_start"], chunk["page_end"] + 1):
            found.setdefault(page, excerpt)
    return found


_EXCERPT = 90  # caracteres de contexto em volta de um termo achado


def _squash(text: str) -> str:
    return " ".join(text.split())


def mentions(info: Any, text: str) -> bool:
    """O texto (sem acento) fala do campo? Cada termo de `mention` é uma alternativa; "a+b" exige as duas partes."""
    return any(all(part in text for part in term.split("+")) for term in info.mention)
