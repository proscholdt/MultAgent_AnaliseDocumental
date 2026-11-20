"""O fechamento da extração das demonstrações: os valores de apoio que as fórmulas precisam, o
EBITDA que reconcilia com a demonstração, e a evidência de que um indicador que a empresa informa
com o próprio nome não pode ser dado como não encontrado."""
import re
from decimal import Decimal

from app.plugins.api import (
    FIELDS,
    SENTENCE,
    Extraction,
    IndexedDocument,
    ToolRejected,
    in_reais,
    mentions,
    number_tokens,
    parse_number,
    parse_table,
    plain,
    squash,
    statement_tables,
    to_decimal,
    unit_of_number,
)

# O que cada indicador calculável precisa ter gravado quando a empresa não o informa.
_SUPPORT = {
    "ebitda": "resultado_antes_financeiro e depreciacao_amortizacao",
    "divida_liquida": "emprestimos_circulante, emprestimos_nao_circulante e caixa_equivalentes",
    "capex": "capex_imobilizado e capex_intangivel (ou capex_imobilizado_e_intangivel, se for uma linha só)",
}
_SUPPORT_FIELDS = {
    "ebitda": ("resultado_antes_financeiro", "depreciacao_amortizacao"),
    "divida_liquida": ("emprestimos_circulante", "emprestimos_nao_circulante", "caixa_equivalentes"),
    "capex": ("capex_imobilizado", "capex_intangivel"),
}

# Os indicadores que a empresa informa com o próprio nome. Só para eles vale a regra de que uma
# linha ou frase com valor não pode ser descartada. O capex ficou de fora depois da 7ª execução
# real: "BNDES - Capex" é o nome de um financiamento da Light, a regra o tomou por capex informado,
# e o campo ficou sem saída (não podia ser dado como ausente nem gravado).
_NAMED = ("ebitda", "margem_ebitda", "divida_liquida", "alavancagem")
_HAS = {"valor": re.compile(r"R\$\s?\d"), "percentual": re.compile(r"\d\s?%"), "multiplo": re.compile(r"\d\s?x\b")}
# Rótulos de razão: "Dívida líquida/EBITDA" não é linha de EBITDA nem de dívida líquida.
_RATIO = re.compile(r"/|\bsobre\b|\brelacao\b|\bindice\b|\bmargem\b")
_RECONCILES = Decimal("0.02")  # diferença relativa aceita entre o EBITDA do texto e o da demonstração


def support_rows(document: IndexedDocument, campo: str) -> dict[int, str]:
    """Páginas em que há uma linha de tabela que serviria de valor de apoio para o campo, com o
    rótulo dela. É a evidência contra "o documento não traz as linhas de apoio"."""
    info = FIELDS[campo]
    found: dict[int, str] = {}
    for chunk in statement_tables(document, info.section):
        table = parse_table(chunk["text"])
        for row in (table.rows if table else []):
            if row[0] and mentions(info, plain(row[0])) and any(parse_number(cell) for cell in row[1:]):
                found.setdefault(chunk["page_start"], row[0])
    return found


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
            for sentence in SENTENCE.split(squash(chunk["text"])):
                folded = plain(sentence)
                if any(term in folded for term in terms) and mentions(info, folded) and _HAS[info.kind].search(sentence):
                    sentences.append((page, f"frase '{sentence[:170]}' (chunk {chunk['chunk_index']})"))
    return rows + sentences  # as tabelas primeiro: é delas que o valor deve vir, quando há


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
        for sentence in SENTENCE.split(squash(chunk["text"])):
            folded = plain(sentence)
            if not (any(term in folded for term in terms) and mentions(info, folded)):
                continue
            for token in number_tokens(sentence):
                value, unit = to_decimal(token), unit_of_number(sentence, token)
                candidate = in_reais(value, unit) if value is not None and unit else None
                if candidate and abs(abs(candidate) - abs(target)) / abs(target) <= _RECONCILES:
                    return f"p. {chunk['page_start']} (chunk {chunk['chunk_index']}): '{sentence[:220]}'"
    return None


def conferir_fechamento(document: IndexedDocument, extraction: Extraction, apoio_ausente: str | None) -> list[str]:
    """O EBITDA que reconcilia e os valores de apoio. Devolve os indicadores sem cálculo possível,
    quando o agente explicou em `apoio_ausente` que o documento não traz os operandos."""
    better = reconciling_ebitda(document, extraction)
    if better:
        raise ToolRejected(
            "O EBITDA gravado não bate com a demonstração (resultado antes do resultado financeiro mais depreciação "
            "e amortização), e o documento tem outra frase de EBITDA cujo valor bate, em módulo. A gravada é "
            "provavelmente de uma parte do grupo ou de outro critério. Grave a frase abaixo no campo 'ebitda' e "
            "feche de novo.", problemas=[better])
    # Indicador não informado e sem os valores de apoio: o relatório sairia sem o número, e a
    # falta passaria por "a empresa não informa". O agente grava o apoio, ou diz por que não há.
    # O EBITDA entra na lista mesmo quando a empresa o informa: é com os operandos que o servidor
    # confere o informado. Numa execução real o extrator gravou o EBITDA da Light e pulou os
    # operandos, e o alerta do sinal omitido no texto não disparou.
    uncomputable = [campo for campo in _SUPPORT
                    if extraction.calculated(campo, extraction.reference) is None
                    and (campo == "ebitda" or extraction.found(campo, extraction.reference) is None)]
    if uncomputable and not apoio_ausente:
        raise ToolRejected(
            "Faltam valores de apoio. Sem eles o servidor não calcula o que a empresa não informa nem confere o "
            "EBITDA que ela informa. Grave-os com salvar_campo e feche de novo. Se o documento não os traz, "
            "feche de novo explicando em 'apoio_ausente'.",
            problemas=[f"{campo}: faltam {_SUPPORT[campo]}" for campo in uncomputable])
    if uncomputable:
        # "O documento não traz" também precisa de prova: se há linha de apoio nas demonstrações, ela
        # tem de ser gravada. Na 3ª execução real o agente usou esta saída com as linhas na p. 10.
        there = []
        for campo in uncomputable:
            for support in _SUPPORT_FIELDS[campo]:
                if extraction.found(support, extraction.reference) is None:
                    rows = support_rows(document, support)
                    if rows:
                        page, label = next(iter(sorted(rows.items())))
                        there.append(f"{support}: há linha que serve na p. {page} ('{label}')"
                                     + (f" e em mais {len(rows) - 1} página(s)" if len(rows) > 1 else ""))
        if there:
            raise ToolRejected(
                "O documento traz as linhas de apoio: grave-as com salvar_campo (origem='tabela') e feche de "
                "novo. 'apoio_ausente' só vale quando o documento realmente não as tem.", problemas=there)
    return uncomputable
