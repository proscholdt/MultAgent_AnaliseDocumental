"""Datas escritas no documento: a leitura em dd/mm/aaaa e a prova no trecho citado."""
from app.regras.datas import data_no_trecho, normalizar_data


def test_a_date_is_read_in_the_ways_documents_write_it():
    assert normalizar_data("12/03/2024") == "12/03/2024"
    assert normalizar_data("1/3/24") == "01/03/2024"
    assert normalizar_data("12.03.2024") == "12/03/2024"
    assert normalizar_data("12 de março de 2024") == "12/03/2024"
    assert normalizar_data("1º de Janeiro de 2025") == "01/01/2025"
    assert normalizar_data("2024") is None and normalizar_data("31/02") is None and normalizar_data("45/13/2024") is None


def test_the_quote_has_to_write_the_date():
    trecho = "O contrato vigora de 1º de janeiro de 2025 a 31/12/2025, podendo ser prorrogado."
    assert data_no_trecho("01/01/2025", trecho) and data_no_trecho("31/12/2025", trecho)
    # O ano certo com outro dia, ou a data de outro trecho, não provam.
    assert not data_no_trecho("01/02/2025", trecho) and not data_no_trecho("31/12/2024", trecho)
    assert not data_no_trecho("não é data", trecho)
