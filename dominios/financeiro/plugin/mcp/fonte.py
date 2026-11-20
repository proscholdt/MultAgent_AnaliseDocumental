"""A fonte de um valor das demonstrações: o escopo (consolidado ou controladora), o sinal (prejuízo)
e a conferência de que a linha ou a frase apontada é mesmo do indicador, e da companhia inteira."""
import re
from typing import Any

from app.plugins.api import IndexedDocument, ToolRejected, mentions, normalize, plain, statement_tables

_SCOPES = {"consolidado": "consolidado", "controladora": "controladora"}
# Palavras que indicam o valor de uma parte do grupo, e não o da companhia (sem acento).
_PARTS = ("distribuidora", "geradora", "comercializadora", "controlada", "subsidiaria", "segmento",
          "unidade de negocio")
# Linhas que repartem o lucro entre os controladores e os demais acionistas.
_MINORITY = re.compile(r"minoritari|nao controlador|atribui")
# Linha de total ou de classificação: num quadro de composição (nota de caixa, nota de
# empréstimos), o rótulo dela não repete o assunto, que está no título da seção.
_SUBTOTAL = re.compile(r"^(total|circulante|nao circulante)\b")
# A linha única de capex e os dois campos separados se excluem: gravar um apaga o outro.
CAPEX_SINGLE, CAPEX_SPLIT = "capex_imobilizado_e_intangivel", ("capex_imobilizado", "capex_intangivel")
# O capex informado em tabela é uma linha de investimentos, não uma linha qualquer com a palavra.
CAPEX_ROW = re.compile(r"(capex|investimentos?\b|total de investimento)")


def escopo_da_coluna(rotulo: str) -> str | None:
    """Controladora ou consolidado, pelo cabeçalho da coluna."""
    folded = rotulo.casefold()
    return next((scope for word, scope in _SCOPES.items() if word in folded), None)


def escopo_alternativo(escopo: str | None, cabecalho: list[str]) -> bool:
    """Foi usada a coluna da controladora, e a tabela tem a do consolidado."""
    return escopo == "controladora" and any(escopo_da_coluna(label) == "consolidado" for label in cabecalho)


def ler_trecho(campo: str, trecho: str) -> dict[str, Any]:
    """O escopo dito na frase e o prejuízo: "prejuízo de R$ 5.672 milhões" traz o número positivo,
    e o valor é negativo."""
    folded = trecho.casefold()
    return {"escopo": "consolidado" if "consolidad" in folded else "controladora" if "controladora" in folded
            else None,
            "negativo": campo == "lucro_liquido" and "prejuízo" in folded and "lucro" not in folded}


def campos_que_se_excluem(campo: str) -> tuple[str, ...]:
    return CAPEX_SPLIT if campo == CAPEX_SINGLE else (CAPEX_SINGLE,) if campo in CAPEX_SPLIT else ()


def dica_celula_repetida(campo: str, outro: str) -> str | None:
    if {campo, outro} <= set(CAPEX_SPLIT):
        return (f"Se a linha junta imobilizado e intangível, grave-a no campo '{CAPEX_SINGLE}' (ele substitui os dois "
                "campos separados).")
    return None


def avisos_do_valor(achado: dict[str, Any]) -> list[str]:
    if achado.get("alternativa_consolidada"):
        return ["Foi usada a coluna da controladora, e a tabela tem a do consolidado. Prefira o consolidado."]
    return []


def conferir_fonte(args: Any, info: Any, found: dict[str, Any], chunk: dict[str, Any],
                   document: IndexedDocument) -> None:
    """A fonte apontada tem de ser do indicador pedido, e da companhia inteira."""
    table = args.origem == "tabela"
    source = plain(found["linha"] if table else found["trecho"])
    section = plain(" > ".join(chunk.get("section_path") or []))
    if args.campo in ("emprestimos_circulante", "emprestimos_nao_circulante") and table and "balanco" not in section \
            and "circulante" not in plain(f"{found['linha']} {found['coluna']} {section}"):
        # A separação entre curto e longo prazo está no balanço. Uma nota com uma linha só de
        # empréstimos traz o total: gravado como circulante, somava de novo o não circulante (na
        # Brasal, 3.417 = 1.849 + 1.568 entrou como circulante, e a dívida líquida saiu errada).
        raise ToolRejected(
            f"{info.label}: a tabela apontada ('{' > '.join(chunk.get('section_path') or [])}') não separa circulante "
            "de não circulante: o valor dela é o total dos empréstimos. Aponte a linha no balanço patrimonial "
            "(passivo circulante e passivo não circulante), ou numa tabela que diga qual dos dois é.")
    if info.statement and table and info.section and plain(info.section) not in section:
        # A mesma linha aparece em outras tabelas (mutações do patrimônio, valor adicionado), sem as
        # colunas por ano: o valor entra, mas sem o ano anterior. Vale a demonstração do próprio campo.
        pages = sorted({other["page_start"] for other in statement_tables(document, info.section)})
        if pages:
            raise ToolRejected(
                f"{info.label} vem da demonstração de {info.section} (p. {', '.join(map(str, pages[:6]))}), e a "
                f"tabela apontada é de '{' > '.join(chunk.get('section_path') or [])}'. Aponte a linha na "
                "demonstração: é nela que estão as colunas do ano e do ano anterior.")
    # Num quadro de composição, a linha do total não repete o assunto: vale o título da seção.
    in_its_note = table and bool(_SUBTOTAL.match(source)) and mentions(info, section)
    if info.mention and not info.main and not mentions(info, source) and not in_its_note:
        raise ToolRejected(
            f"{info.label}: a linha apontada ('{found['linha'] if table else args.trecho}') não é a deste valor. O "
            f"rótulo tem de mencionar {' ou '.join(repr(term.replace('+', ' e ')) for term in info.mention)}. Num "
            "quadro de composição, aponte a linha do total, não a de um componente; nas demonstrações, a linha "
            "com o nome do próprio valor.")
    if info.statement and info.mention and not mentions(info, source):
        raise ToolRejected(
            f"{info.label}: a {'linha' if table else 'frase'} apontada ('{found['linha'] if table else args.trecho}') "
            f"não é a deste valor. O rótulo tem de mencionar "
            f"{' ou '.join(repr(term.replace('+', ' e ')) for term in info.mention)}: aponte a linha do total da "
            "demonstração, com o nome do próprio valor.")
    if args.campo == "lucro_liquido" and table:
        # A demonstração do resultado consolidada repete o rótulo: o lucro do exercício e, depois da
        # participação dos minoritários, a parte dos controladores. O indicador é o total. Numa
        # repetição da execução final, o lucro da Millet veio da segunda linha (14.743 de 18.891).
        rows, index = found["table"].rows, found["row_index"]
        earlier = [i for i in range(index) if normalize(rows[i][0]) == normalize(rows[index][0])]
        if earlier and any(_MINORITY.search(plain(rows[i][0])) for i in range(earlier[-1] + 1, index)):
            raise ToolRejected(
                f"A linha apontada é a {len(earlier) + 1}ª com o rótulo '{found['linha']}' e vem depois da participação "
                "dos minoritários (não controladores): é a parte do lucro atribuída aos controladores. O lucro "
                "líquido do exercício é o total, que vem antes: aponte a 1ª ocorrência (ocorrencia_linha=1).")
    if args.campo == "capex" and table and not CAPEX_ROW.match(source):
        raise ToolRejected(
            f"Capex: a linha apontada ('{found['linha']}') não é um total de investimentos da empresa. O capex "
            "informado vem de uma linha ou frase de investimentos; as linhas de aquisição do fluxo de caixa vão nos "
            "campos de apoio (capex_imobilizado, capex_intangivel ou capex_imobilizado_e_intangivel), e o campo "
            "capex fica como não encontrado.")
    if info.mention and info.main and not mentions(info, source):
        raise ToolRejected(
            f"{info.label}: a {'linha' if table else 'frase'} apontada não menciona "
            f"{' nem '.join(repr(term) for term in info.mention)}. Índices de nome parecido (por exemplo "
            "'alavancagem' como dívida sobre patrimônio ou sobre capital) são outra conta. Se a empresa não informa "
            "este indicador, use salvar_nao_encontrado, descartando esta página e dizendo no motivo qual índice ela "
            "informa.")
    if info.statement and not table and args.medida == "valor" and not args.nota:
        raise ToolRejected(
            f"{info.label} vem da demonstração financeira, que é uma tabela: aponte a linha e a coluna dela "
            "(origem='tabela'). O texto do relatório arredonda e pode omitir o sinal. Se o documento não traz a "
            "demonstração em tabela, salve de novo dizendo isso em 'nota'.")
    if info.main and not table and not args.nota:
        part = next((word for word in _PARTS if word in source), None)
        if part:
            raise ToolRejected(
                f"O trecho fala em '{part}': parece o valor de uma parte do grupo, e o campo é o da companhia "
                "(consolidado). Leia a página inteira: o valor consolidado costuma vir depois dos segmentos. Se a "
                "empresa só informa este, salve de novo dizendo em 'nota' o que o valor representa.")
