"""Números no formato brasileiro e leitura de célula em tabela Markdown."""
from decimal import Decimal

import pytest

from app.governanca.erros import ToolRejected
from app.regras.numeros import (
    contains_number,
    format_br,
    in_reais,
    parse_number,
    to_decimal,
    unit_in_text,
    unit_of_number,
)
from app.regras.tabelas import parse_table, read_cell
from tests.conftest import DRE, financeiro

# --- números ------------------------------------------------------------------------------


@pytest.mark.parametrize("written, value", [
    ("1.325.059", "1325059"), ("26,51", "26.51"), ("1.234,56", "1234.56"), ("2022", "2022"),
    ("0,4", "0.4"), ("3.32", "3.32"), ("12.5", "12.5"), ("7", "7"), ("9.032", "9032"),
])
def test_brazilian_numbers_are_read_as_written(written, value):
    assert to_decimal(written) == Decimal(value)


def test_a_cell_is_a_number_only_when_it_is_a_single_number():
    assert parse_number("(839.903)").value == Decimal(-839903)  # parênteses: negativo
    assert parse_number("- 2,45%") == parse_number("-2,45%").__class__(Decimal("-2.45"), "- 2,45%", "percentual")
    assert (parse_number("13,5%").kind, parse_number("3,32x").kind, parse_number("R$ 9.032").kind) == (
        "percentual", "multiplo", "valor")
    for not_a_number in ("-", "", "n/a", "2023 / 2024", "na empresa 162.913", "24.883 523", "Total"):
        assert parse_number(not_a_number) is None


def test_number_is_matched_whole_never_as_a_piece_of_another():
    text = "A dívida líquida totalizou R$9.032 milhões, aumento de 23% em comparação a 2021, de R$7.353 milhões."
    assert contains_number(text, "9.032") and contains_number(text, "23") and contains_number(text, "2021")
    assert not contains_number("foram 21.234 clientes", "1.234")
    assert not contains_number("foram 21.234 clientes", "234")
    assert contains_number("índice de 3,32x.", "3,32")  # o ponto final da frase não faz parte do número


def test_unit_comes_from_what_is_written_next_to_the_number():
    text = "A dívida líquida totalizou R$9.032 milhões, e o caixa, R$ 1,2 bilhão. Investiu 450 mil em obras."
    assert unit_of_number(text, "9.032") == "R$ milhões"
    assert unit_of_number(text, "1,2") == "R$ bilhões"
    assert unit_of_number(text, "450") == "R$ mil"
    assert unit_of_number("o índice encerrou em 3,32x", "3,32") is None
    assert unit_in_text("(Valores expressos em milhares de reais)") == "R$ mil"
    assert unit_in_text("valores em R$ milhões") == "R$ milhões"
    assert unit_in_text("perdas de 9.101 GWh") is None


def test_values_in_different_units_are_compared_in_reais():
    assert in_reais(Decimal(9032), "R$ milhões") == in_reais(Decimal(9032000), "R$ mil")
    assert in_reais(Decimal(1), "GWh") is None and in_reais(Decimal(1), None) is None
    assert (format_br(Decimal(1325059)), format_br(Decimal("24.8391"), 2), format_br(Decimal(-5672203))) == (
        "1.325.059", "24,84", "-5.672.203")


# --- tabelas ------------------------------------------------------------------------------


def test_cell_is_read_by_row_and_column_labels_ignoring_title_lines_and_case():
    cell = read_cell(DRE, "receita operacional líquida", "Consolidado - 2022")
    assert (cell.text, cell.row_label, cell.column_label) == ("1.325.059", "Receita operacional líquida",
                                                              "Consolidado - 2022")
    assert cell.row_text == "| Receita operacional líquida | 21 | - | - | 1.325.059 | 1.061.410 |"
    assert cell.header_text.startswith("|  | Notas | Controladora - 2022 | Controladora - 2021 - (não auditado)")
    # A coluna de 2021 é outra célula: pedir 2022 nunca devolve o valor de 2021.
    assert read_cell(DRE, "Receita operacional líquida", "Consolidado - 2021 - (não auditado)").text == "1.061.410"
    # O escopo (consolidado ou controladora) é do plugin financeiro, não da leitura da tabela.
    scope_of = financeiro().fonte.escopo_da_coluna
    assert scope_of(cell.column_label) == "consolidado" and scope_of("Controladora - 2022") == "controladora"
    assert scope_of("31/12/2022") is None


def test_unknown_label_is_refused_with_the_labels_that_exist():
    with pytest.raises(ToolRejected, match="não tem linha") as error:
        read_cell(DRE, "Receita líquida", "Consolidado - 2022")
    # As opções são os rótulos mais parecidos com o pedido, não a tabela inteira.
    assert error.value.opcoes[0] == "Receita operacional líquida" and "Lucro bruto" not in error.value.opcoes
    with pytest.raises(ToolRejected, match="não tem coluna") as error:
        read_cell(DRE, "Lucro bruto", "2022")
    assert set(error.value.opcoes) == {"Controladora - 2022", "Consolidado - 2022"}  # as que falam em 2022
    # O acento não separa dois rótulos: o PDF escreve "Caixa liquido", o modelo copia "Caixa líquido".
    assert read_cell(DRE, "receita operacional liquida", "consolidado - 2022").text == "1.325.059"
    with pytest.raises(ToolRejected, match="rótulos das linhas"):
        read_cell(DRE.replace("|  | Notas", "| Conta | Notas"), "Lucro bruto", "Conta")
    with pytest.raises(ToolRejected, match="não tem tabela"):
        read_cell("Só um parágrafo, sem tabela.", "Lucro bruto", "2022")


def test_repeated_label_needs_the_occurrence_never_the_first_match():
    table = ("| Conta | 2022 | 2022 |\n|---|---|---|\n| Lucro líquido do exercício | 18.891 | 14.743 |\n"
             "| Atribuível aos controladores | 14.743 | 14.743 |\n| Lucro líquido do exercício | 5.371 | 6.470 |")
    with pytest.raises(ToolRejected, match="aparece 2 vezes como linha"):
        read_cell(table, "Lucro líquido do exercício", "2022", column_occurrence=1)
    with pytest.raises(ToolRejected, match="aparece 2 vezes como coluna"):
        read_cell(table, "Atribuível aos controladores", "2022")
    assert read_cell(table, "Lucro líquido do exercício", "2022", row_occurrence=2, column_occurrence=2).text == "6.470"
    with pytest.raises(ToolRejected, match="não existe a ocorrência 3"):
        read_cell(table, "Lucro líquido do exercício", "2022", row_occurrence=3, column_occurrence=1)


def test_table_parsing_handles_escaped_bars_short_rows_and_footnotes():
    text = ("Título da tabela\n\n| Conta | 2022 |\n|---|---|\n| Receitas \\| despesas | 10 |\n| Curta |\n\n"
            "(a) Nota de rodapé com 99.999 que não é linha da tabela")
    table = parse_table(text)
    assert table.header == ["Conta", "2022"]
    assert table.rows == [["Receitas | despesas", "10"], ["Curta", ""]]
    assert parse_table("sem tabela") is None
