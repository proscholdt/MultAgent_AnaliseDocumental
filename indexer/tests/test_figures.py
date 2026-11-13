"""Conferência do que o modelo de visão transcreve de um gráfico: número, texto e posição."""
import json

import pytest

from app.pipeline.figure_ocr import read_figures
from app.pipeline.figures import (
    FigureTranscription,
    FigureValue,
    number_key,
    transcribe_figures,
    verify_transcription,
)
from app.schemas import OcrWord, ParsedDocument, ParsedItem, ParsedPage


def word(text: str, x0: float, y0: float, x1: float | None = None, y1: float | None = None) -> OcrWord:
    return OcrWord(text=text, x0=x0, y0=y0, x1=x0 + 50 if x1 is None else x1, y1=y0 + 15 if y1 is None else y1)


def loose(*texts: str) -> list[OcrWord]:
    """Textos lidos pelo OCR sem nenhuma relação de posição entre si (em diagonal)."""
    return [word(text, 300 * i, 100 * i) for i, text in enumerate(texts)]


def value(valor: str, *rotulos: str) -> FigureValue:
    return FigureValue(valor=valor, rotulos=list(rotulos))


def transcription(valores: list[FigureValue], **overrides) -> FigureTranscription:
    base = {"tipo": "grafico", "titulo": None, "legivel": True, "valores": valores, "notas": []}
    return FigureTranscription(**{**base, **overrides})


def lines(result) -> list[str]:
    return result.text.split("\n")


# --- 1. número --------------------------------------------------------------

PAGE_TEXT = "Evolução das Perdas Totais [GWh]. Perdas Totais/Carga Fio 26,51% 21,53% 9.101 dez/22."


def test_number_key_keeps_the_separator_position():
    assert number_key("26,51%") == number_key("26.51") == "26|51"
    assert number_key("2.651") == "2|651" != number_key("26,51")
    assert number_key("9.101.") == "9|101"  # ponto final de frase
    assert number_key("N/D") == ""


def test_values_the_model_skipped_come_from_the_text_layer_of_the_figure():
    """Gráfico de perdas da Light, numa das execuções: o modelo transcreveu 30 dos 43 valores, e
    o 26,51% de dez/22 ficou de fora. A camada de texto do PDF dentro da figura tem todos."""
    layer = ("8.992 9.101 \r\n9.303 \r\n26,81% 26,51%\r\n21,53% 21,53% 21,53%\r\n2021 2022\r\n"
             "12 meses 3,0%\r\ndez/20 dez/22\r\nPerdas Totais REN\r\n5")
    page = "Perdas Totais REN dez/20 dez/22 8.992 9.101 9.303 26,81% 26,51% 21,53% 2021 2022 12 meses 3,0%"
    result = verify_transcription(transcription([
        value("8.992", "dez/20"), value("26,81%", "dez/20"), value("9.303", "2021"),
    ]), page, [], layer)
    # Entram os valores das linhas feitas só de valores, uma vez cada, na forma em que estão
    # escritos. Ficam de fora: o que o modelo já transcreveu, o número que ele usou como rótulo
    # (2021), o "12" de "12 meses" (e o 3,0% que divide a linha com ele) e o dígito solto.
    assert "Valores escritos na figura que o modelo não transcreveu: 9.101; 26,51%; 21,53%; 2022" in lines(result)
    assert "Outros 4 valores vieram direto da camada de texto da figura, sem rótulo.)" in result.text
    assert result.verified_ratio == 1.0  # a conta continua sendo só do que o modelo transcreveu

    one = verify_transcription(transcription([value("8.992", "dez/20")]), page, [], "8.992\r\n9.101\r\ndez/20")
    assert "Outro valor veio direto da camada de texto da figura, sem rótulo.)" in one.text

    # Sem a camada de texto da figura (gráfico em imagem, ou parser que não a extrai), nada muda.
    plain = verify_transcription(transcription([value("8.992", "dez/20")]), page, [])
    assert "não transcreveu" not in plain.text
    # Figura que o modelo deu como ilegível não é completada: fica só o título.
    unreadable = verify_transcription(transcription([], legivel=False, titulo="Perdas Totais"), page, [], layer)
    assert "não transcreveu" not in unreadable.text


def test_only_values_present_in_page_text_are_indexed():
    result = verify_transcription(transcription([
        value("26,51%", "Dez/22"),
        value("21.53%", "Patamar regulatório"),  # o modelo trocou a vírgula por ponto
        value("25,98%", "Set/22"),  # não existe no texto da página
    ]), PAGE_TEXT)
    # Sem OCR não há posição: os valores conferem, mas nenhum rótulo pode ser ligado a eles.
    assert "Valores sem rótulo conferido: 26,51%; 21,53%" in lines(result)
    assert "25,98" not in result.text
    assert "Set/22: 25,98%" in result.unverified_values
    assert result.verified_ratio == pytest.approx(0.667, abs=0.001)
    assert "Valores: 2 de 3 conferidos, 2 no texto da página e 0 por OCR do recorte" in result.text


def test_same_digits_with_the_separator_elsewhere_is_another_number():
    result = verify_transcription(transcription([value("26,51%"), value("9.101")]),
                                  "foram 2.651 clientes, 265,1 GWh e 9.101 perdas")
    assert lines(result)[1] == "Valores sem rótulo conferido: 9.101"
    assert result.verified_ratio == 0.5 and result.unverified_values == ["26,51%"]


def test_number_is_indexed_as_the_source_writes_it():
    """O modelo normaliza "94,2%" para "94.2%"; o índice guarda a forma da página."""
    result = verify_transcription(transcription([value("94.2%"), value("R$ 1.817 mi")]),
                                  "arrecadação de 94,2% e EBITDA de 1.817")
    assert "Valores sem rótulo conferido: 94,2%; R$ 1.817 mi" in lines(result)


def test_value_read_by_ocr_confirms_a_chart_that_is_an_image():
    """Gráfico colado como imagem: o número não está no texto da página, mas o OCR do recorte
    leu o mesmo valor que o modelo de visão."""
    result = verify_transcription(
        transcription([value("26,51%"), value("21,53%"), value("25.98%")]),
        PAGE_TEXT, loose("Set/22", "25,98%", "9.101"))
    assert "Valores sem rótulo conferido: 26,51%; 21,53%; 25,98%" in lines(result)
    assert result.verified_ratio == 1.0
    assert result.verified_by_ocr == 1  # os outros dois já estavam no texto da página
    assert "Valores: 3 de 3 conferidos, 2 no texto da página e 1 por OCR do recorte" in result.text


def test_ocr_does_not_confirm_what_it_did_not_read():
    result = verify_transcription(transcription([
        value("3,0%"),  # OCR leu "3" e "0" soltos: não é prova
        value("6%"),  # um dígito só nunca é confirmado por OCR
        value("14,68%"),  # OCR leu outro número
        value("1,234"),  # OCR leu "1.234": milhar ou decimal? Sem a página, não confere
    ], titulo="PECLD"), "sem números", loose("PECLD", "%0°", "6", "3", "14,86%", "1.234"))
    assert result.verified_ratio == 0.0 and result.verified_by_ocr == 0
    assert result.unverified_values == ["3,0%", "6%", "14,68%", "1,234"]
    for text in ("3,0%", "6%", "14,68%", "1,234", "1.234"):
        assert text not in result.text


def test_hallucinated_numbers_never_reach_the_index():
    result = verify_transcription(
        transcription([value("26,51%"), value("21.53%"), value("25,98%")], titulo="Perdas"),
        "página sobre perdas, sem nenhum número relevante")
    assert result.verified_ratio == 0.0
    assert len(result.unverified_values) == 3
    for text in ("26,51", "21.53", "25,98"):
        assert text not in result.text


# --- 2. texto ---------------------------------------------------------------


def test_title_must_exist_in_the_figure_or_in_the_page():
    kept = verify_transcription(transcription([], titulo="Evolução das Perdas Totais [GWh]"), PAGE_TEXT)
    assert lines(kept)[0] == "[Figura: grafico] Evolução das Perdas Totais [GWh]"

    by_ocr = verify_transcription(transcription([value("9.101")], titulo="Efeito médio"),
                                  PAGE_TEXT, loose("Efeito médio"))
    assert lines(by_ocr)[0] == "[Figura: grafico] Efeito médio"

    invented = verify_transcription(transcription([value("9.101")], titulo="Resultado Operacional"), PAGE_TEXT)
    assert lines(invented)[0] == "[Figura: grafico]"
    assert "Título não encontrado: Resultado Operacional" in invented.unverified_values


def test_notes_enter_only_without_numbers_and_with_known_words():
    """Nota é texto livre: não dá para conferir a que cada número dela se refere."""
    result = verify_transcription(
        transcription([value("9.101")],
                      notas=["Perdas Totais em GWh", "Industrial 2022: Cativo 4.887", "Fonte: relatório interno"]),
        PAGE_TEXT)
    assert "Nota: Perdas Totais em GWh" in lines(result)
    assert "4.887" not in result.text and "relatório interno" not in result.text
    assert result.unverified_values == ["Nota: Industrial 2022: Cativo 4.887", "Nota: Fonte: relatório interno"]


def test_decorative_and_empty_figures_are_skipped():
    assert verify_transcription(transcription([], tipo="decorativo", titulo="Perdas"), PAGE_TEXT) is None
    assert verify_transcription(transcription([]), PAGE_TEXT) is None


def test_illegible_figure_keeps_only_the_title():
    result = verify_transcription(
        transcription([value("26,51%")], titulo="Evolução das Perdas Totais", legivel=False), PAGE_TEXT)
    assert "Evolução das Perdas Totais" in result.text and "26,51%" not in result.text
    assert result.verified_ratio is None and result.verified_labels_ratio is None


# --- 3. posição: qual rótulo pertence a qual número ---------------------------

PERIODS = ("dez/20", "mar/21", "jun/21")
COLUMNS = (40, 160, 280)
ROWS = [  # cada linha de valores do gráfico, de cima para baixo
    ("9.303", "9.902", "9.833"),
    ("8.992", "9.547", "9.512"),
    ("26,81%", "28,19%", "27,75%"),
    ("19,20%", "19,31%", "19,42%"),
]


def chart_words() -> list[OcrWord]:
    """Gráfico de colunas: quatro linhas de valores, períodos no eixo e legenda de cores embaixo."""
    words = [word(text, x, 20 + 30 * r) for r, row in enumerate(ROWS) for x, text in zip(COLUMNS, row, strict=True)]
    words += [word(period, x + 5, 150, x + 45) for x, period in zip(COLUMNS, PERIODS, strict=True)]
    words += [word("Perdas Totais", 60, 190, 150), word("REN", 230, 190, 260)]
    return words


def chart_values(swap: bool = False) -> list[FigureValue]:
    series = ("Perdas Totais", "Perdas Totais", "REN", "REN")
    values = [value(text, serie, period)
              for serie, row in zip(series, ROWS, strict=True) for period, text in zip(PERIODS, row, strict=True)]
    if swap:  # o modelo liga 26,81% a mar/21 e 28,19% a dez/20
        values[6], values[7] = value("26,81%", "REN", "mar/21"), value("28,19%", "REN", "dez/20")
    return values


def test_label_is_kept_when_it_is_under_its_values():
    result = verify_transcription(transcription(chart_values()), "", chart_words())
    assert lines(result)[1:4] == [
        "dez/20: 9.303; 8.992; 26,81%; 19,20%",
        "mar/21: 9.902; 9.547; 28,19%; 19,31%",
        "jun/21: 9.833; 9.512; 27,75%; 19,42%",
    ]
    assert result.verified_ratio == 1.0 and result.verified_by_ocr == 12


def test_colour_legend_is_not_linked_to_values():
    """A legenda diz que série existe, mas a ligação é por cor: a posição não a confirma."""
    result = verify_transcription(transcription(chart_values()), "", chart_words())
    assert "Outros textos da figura, sem ligação conferida com os valores: Perdas Totais; REN" in lines(result)
    assert not any(line.startswith(("Perdas Totais", "REN")) for line in lines(result))
    assert result.verified_labels_ratio == 0.5  # 12 de período conferidas, 12 de série não
    assert "Rótulos: 12 de 24 ligações conferidas pela posição na figura" in result.text
    assert "Rótulo sem ligação conferida com os valores: REN" in result.unverified_values


def test_value_linked_to_the_wrong_column_loses_the_label():
    result = verify_transcription(transcription(chart_values(swap=True)), "", chart_words())
    assert "dez/20: 9.303; 8.992; 19,20%" in lines(result)
    assert "mar/21: 9.902; 9.547; 19,31%" in lines(result)
    assert "jun/21: 9.833; 9.512; 27,75%; 19,42%" in lines(result)
    # Os dois números existem na figura, mas não onde o modelo disse.
    assert "Valores sem rótulo conferido: 26,81%; 28,19%" in lines(result)


def test_value_without_label_does_not_weaken_the_labels_of_its_column():
    """O modelo não rotulou a linha de totais: isso não é prova contra o período da coluna."""
    values = [value(text) for text in ROWS[0]] + chart_values()[3:]
    result = verify_transcription(transcription(values), "", chart_words())
    assert "dez/20: 8.992; 26,81%; 19,20%" in lines(result)
    assert "Valores sem rótulo conferido: 9.303; 9.902; 9.833" in lines(result)


def test_title_and_caption_are_not_labels():
    """Um título em cima de uma coluna e um subtítulo que cobre a figura toda não são rótulos."""
    words = [word("PECLD/ROB", 20, 0, 140), word("12 meses", 20, 30, 300),
             word("3,0%", 70, 100), word("6,0%", 240, 100),
             word("Dez/21", 55, 150, 115), word("Dez/22", 225, 150, 285)]
    result = verify_transcription(transcription([
        value("3,0%", "PECLD/ROB", "12 meses", "Dez/21"),
        value("6,0%", "PECLD não-recorrente", "12 meses", "Dez/22"),
    ]), "", words)
    assert lines(result)[1:3] == ["Dez/21: 3,0%", "Dez/22: 6,0%"]
    assert "Outros textos da figura, sem ligação conferida com os valores: PECLD/ROB; 12 meses" in lines(result)
    assert "Rótulo não encontrado na figura: PECLD não-recorrente" in result.unverified_values


def test_label_that_is_not_written_in_the_figure_is_dropped():
    """O modelo leu "Efetivo médio" onde está escrito "Efeito médio"."""
    words = [word("14,68%", 40, 20), word("Efeito médio", 30, 150, 100), word("5,13%", 160, 20),
             word("Compra de", 160, 150, 220), word("Energia", 165, 168, 215)]
    result = verify_transcription(
        transcription([value("14,68%", "Efetivo médio"), value("5,13%", "Compra de Energia")]), "", words)
    assert "Compra de Energia: 5,13%" in lines(result)  # rótulo em duas linhas
    assert "Valores sem rótulo conferido: 14,68%" in lines(result)
    assert "Efetivo" not in result.text
    assert "Rótulo não encontrado na figura: Efetivo médio" in result.unverified_values


def test_label_beside_the_value_in_horizontal_bars():
    words = [word("Varejo", 10, 20, 60), word("94,2%", 300, 20),
             word("Grandes Clientes", 10, 60, 110), word("103,3%", 320, 60)]
    result = verify_transcription(
        transcription([value("94,2%", "Varejo"), value("103,3%", "Grandes Clientes")]), "", words)
    assert lines(result)[1:3] == ["Varejo: 94,2%", "Grandes Clientes: 103,3%"]

    swapped = verify_transcription(
        transcription([value("103,3%", "Varejo"), value("94,2%", "Grandes Clientes")]), "", words)
    assert "Valores sem rótulo conferido: 103,3%; 94,2%" in lines(swapped)


def test_group_label_spanning_two_columns():
    """Dois níveis de rótulo no eixo: o ano embaixo de cada coluna e a classe embaixo do par."""
    words = [word("5.409", 300, 20, 340), word("5.269", 370, 20, 410), word("3.534", 440, 20, 480),
             word("3.388", 510, 20, 550),
             word("2021", 300, 150, 340), word("2022", 370, 150, 410), word("2021", 440, 150, 480),
             word("2022", 510, 150, 550),
             word("Industrial", 320, 180, 390), word("Outros", 470, 180, 520),
             word("Cativo", 325, 210, 385), word("Uso Rede", 395, 210, 475)]
    result = verify_transcription(transcription([
        value("5.409", "Industrial", "2021", "Cativo"), value("5.269", "Industrial", "2022", "Uso Rede"),
        value("3.534", "Outros", "2021", "Cativo"), value("3.388", "Outros", "2022", "Uso Rede"),
    ]), "", words)
    assert lines(result)[1:5] == [
        "Industrial | 2021: 5.409", "Industrial | 2022: 5.269", "Outros | 2021: 3.534", "Outros | 2022: 3.388"]
    assert "Outros textos da figura, sem ligação conferida com os valores: Cativo; Uso Rede" in lines(result)


# --- OCR dos recortes e cache das transcrições --------------------------------


def test_figure_ocr_reads_only_candidates_and_only_once(tmp_path):
    class FakeOcr:
        def __init__(self):
            self.calls = []

        def read(self, path):
            self.calls.append(path.name)
            if path.name == "quebrado.png":
                raise OSError("imagem corrompida")
            return [word("14,68%", 10, 10)]

    done = [word("já lido", 0, 0)]
    doc = ParsedDocument(doc_id="d", file_name="f.pdf", page_count=1, parser="t", pipeline_version="x", items=[
        ParsedItem(type="figure", page=1, image_path="figures/grafico.png", area_ratio=0.15),
        ParsedItem(type="figure", page=1, image_path="figures/logo.png", area_ratio=0.01),
        ParsedItem(type="figure", page=1, image_path="figures/quebrado.png", area_ratio=0.10),
        ParsedItem(type="figure", page=1, image_path="figures/lido.png", area_ratio=0.10, ocr_words=done),
        ParsedItem(type="text", page=1, text="parágrafo"),
    ])
    ocr = FakeOcr()
    assert read_figures(doc, tmp_path, ocr, min_area_ratio=0.04) == 2
    assert ocr.calls == ["grafico.png", "quebrado.png"]
    assert [item.ocr_words for item in doc.items] == [[word("14,68%", 10, 10)], None, [], done, None]
    assert read_figures(doc, tmp_path, ocr, min_area_ratio=0.04) == 0  # nada a refazer


def test_parsed_json_from_before_ocr_positions_is_read_again():
    """Cache antigo guardava só `ocr_text`; sem as posições, a figura volta para a fila do OCR."""
    old = {"type": "figure", "page": 1, "image_path": "figures/g.png", "area_ratio": 0.1, "ocr_text": "14,68%"}
    assert ParsedItem.model_validate(old).ocr_words is None


async def test_transcription_cache_in_the_old_format_is_redone(tmp_path, settings):
    class FakeLLM:
        calls = 0

        async def parse(self, **kwargs):
            self.calls += 1
            return transcription([value("26,51%", "Dez/22")])

    doc = ParsedDocument(
        doc_id="d", file_name="f.pdf", page_count=1, parser="t", pipeline_version="x",
        items=[ParsedItem(type="figure", page=1, image_path="figures/g.png", area_ratio=0.15, ocr_words=[])],
        pages=[ParsedPage(page=1, native_text=PAGE_TEXT)])
    cache = tmp_path / "figures.json"
    cache.write_text(json.dumps({"figures/g.png": {  # formato anterior: séries e pontos
        "tipo": "grafico", "titulo": None, "legivel": True, "notas": [],
        "series": [{"nome": "Perdas", "pontos": [{"rotulo": "Dez/22", "valor": "26,51%"}]}]}}), encoding="utf-8")
    llm = FakeLLM()

    results = await transcribe_figures(doc, tmp_path, cache, llm, settings)
    assert llm.calls == 1 and "26,51%" in results["figures/g.png"].text
    assert "valores" in json.loads(cache.read_text(encoding="utf-8"))["figures/g.png"]

    await transcribe_figures(doc, tmp_path, cache, llm, settings)
    assert llm.calls == 1  # formato atual em cache: não chama de novo
    # Sem LLM (chave ausente), cache antigo não serve e a figura fica de fora.
    cache.write_text(json.dumps({"figures/g.png": {"tipo": "grafico", "series": []}}), encoding="utf-8")
    assert await transcribe_figures(doc, tmp_path, cache, None, settings) == {}
