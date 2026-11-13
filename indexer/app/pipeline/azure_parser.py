"""Parser na nuvem: PDF → ParsedDocument com o Azure AI Document Intelligence (PARSER=azure).

O modelo de layout devolve parágrafos com papel (título, cabeçalho de página...), tabelas com
as células e figuras com a região que ocupam na página. Aqui isso é convertido para o mesmo
ParsedDocument que o Docling produz, de modo que chunking, conferência de gráficos e
indexação não mudam.

Três coisas continuam sendo feitas na máquina:

- o texto nativo de cada página (pypdf), referência exata das conferências, como no Docling;
- o recorte de cada gráfico, renderizando a página com o pypdfium2. O serviço informa só a
  região; a imagem é o que vai para o modelo de visão;
- os textos de dentro de cada gráfico, com posição. Vêm das palavras que o serviço leu na
  página, reagrupadas em trechos. O serviço lê por OCR também os gráficos colados como imagem
  num PDF digital (conferido nos da Light). Se mesmo assim ele não devolver nenhum número
  dentro de uma figura e o OCR local estiver instalado, o OCR local lê o recorte.

A resposta crua do serviço fica guardada ao lado do parsed.json (response.json.gz). Refazer a
conversão, por mudança de regra ou de versão do pipeline, não chama nem paga o serviço de novo.

Este módulo não importa Docling nem torch: roda na imagem leve.
"""
import gzip
import io
import json
import logging
import math
import re
import zlib
from bisect import bisect_left
from collections.abc import Callable
from pathlib import Path
from statistics import median
from typing import Any

from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import ClientAuthenticationError, HttpResponseError
from pypdf import PdfReader, PdfWriter

from app.config import PIPELINE_VERSION, Settings
from app.pipeline.chunker import STATEMENT_TITLE
from app.pipeline.parsing import (
    MIN_NATIVE_CHARS,
    ParserError,
    ProgressCallback,
    check_system_fonts,
    native_pages,
)
from app.schemas import OcrWord, ParsedDocument, ParsedItem

log = logging.getLogger(__name__)

Box = tuple[float, float, float, float]  # x0, y0, x1, y1 na unidade da página (polegadas em PDF)
# Recorta a região de uma página e devolve (caminho relativo, pixels por unidade em x, em y,
# origem do recorte em pixels). None se não foi possível renderizar.
Crop = Callable[[int, Box, tuple[float, float]], tuple[str, float, float, tuple[int, int]] | None]
# Camada de texto do PDF dentro de uma região da página (mesmos argumentos do recorte).
TextLayer = Callable[[int, Box, tuple[float, float]], str | None]

_SKIP_ROLES = {"pageHeader", "pageFooter", "pageNumber"}
_RUNNING_ROLES = {"pageHeader", "pageFooter"}
_HEADING_ROLES = {"title", "sectionHeading"}
_HEADER_KINDS = {"columnHeader", "stubHead"}
_SELECTION_MARKS = (":selected:", ":unselected:")
# Anotação de cabeçalho que o serviço devolve numa linha própria, abaixo dos anos: só texto
# entre parênteses, sem dígitos. "(não auditado)", "(reapresentado)". "(1.551)" é um valor.
_HEADER_NOTE = re.compile(r"(?:\([^()\d]{3,40}\)\s*)+")
_NOTE = re.compile(r"\([^()]+\)")
# Linha que diz o período ou a unidade de uma tabela, e não o assunto de uma seção.
_PERIOD_LINE = re.compile(
    r"(?:para\s+os?\s+)?(?:exerc[íi]cios?|per[íi]odos?|trimestres?|semestres?)\s+(?:findos?|encerrados?)\s+em\b"
    r"|em\s+\d{1,2}\s+de\s+[a-zç]+\s+de\s+\d{4}\b"
    r"|\((?:em|valores\s+expressos\s+em)\s+(?:milhares|milhões|reais|R\$)",
    re.IGNORECASE,
)
_TITLE_BLOCK_MAX_LINES = 6
_TITLE_MAX_CHARS = 120
_NOT_A_TITLE = re.compile(r"\d{4}|www\.|@", re.IGNORECASE)  # CEP, telefone, site: é timbre
_BAND_TOLERANCE = 0.02  # fração da altura da página
# Palavras da mesma linha separadas por mais que isto (em alturas de linha) são textos
# distintos: os rótulos "dez/20" e "mar/21" de um eixo, e não as palavras de "Perdas Totais".
_SEGMENT_GAP = 1.2
_ROW_OVERLAP = 0.5
_HAS_NUMBER = re.compile(r"\d\D*\d")  # dois dígitos: um solto não prova nada (ver figures.py)
_NUMERIC = re.compile(r"[+\-−(]?\d[\d.,]*[%x)]?$")  # palavra que é só um número: "3.573", "85%", "-2,45%"
# Respostas que não mudam se a chamada for repetida: credencial, endpoint ou documento recusado.
_PERMANENT_STATUS = {400, 401, 403, 404, 413}
RESPONSE_FILE = "response.json.gz"  # resposta crua do serviço, na pasta de cache do parser


# ---------------------------------------------------------------------------
# Geometria e texto
# ---------------------------------------------------------------------------


def _box(polygon: list[float]) -> Box:
    xs, ys = polygon[0::2], polygon[1::2]
    return min(xs), min(ys), max(xs), max(ys)


def _regions(element: dict[str, Any]) -> list[tuple[int, Box]]:
    """(página, caixa) de cada região do elemento. Parágrafo que atravessa a quebra tem duas."""
    return [(region["pageNumber"], _box(region["polygon"]))
            for region in element.get("boundingRegions") or [] if region.get("polygon")]


def _first_offset(element: dict[str, Any]) -> int | None:
    """Posição do elemento no texto corrido do documento: é o que dá a ordem de leitura."""
    spans = element.get("spans") or []
    return min(span["offset"] for span in spans) if spans else None


def _center_in(inner: Box, outer: Box) -> bool:
    x, y = (inner[0] + inner[2]) / 2, (inner[1] + inner[3]) / 2
    return outer[0] <= x <= outer[2] and outer[1] <= y <= outer[3]


def _clean(text: str) -> str:
    """Texto do serviço, sem as marcas de caixa de seleção e com os espaços normalizados.

    O serviço enxerga caixas de seleção onde há um pontilhado ou um traço: nas tabelas da
    Brasal foram 62, coladas em rótulos e valores ("Estoques ☐", "10 ☐"). Onde a caixa existe
    de verdade (o balanço social da Light), o PDF já a escreve como texto: "( X )"."""
    for mark in _SELECTION_MARKS:
        text = text.replace(mark, " ")
    return " ".join(text.split())


def _cell_text(text: str) -> str:
    return _clean(text).replace("|", "\\|")


def _key(text: str) -> str:
    """Texto normalizado, para reconhecer a mesma linha em páginas diferentes."""
    return " ".join(text.split()).casefold()


# ---------------------------------------------------------------------------
# Tabela → Markdown
# ---------------------------------------------------------------------------


def _column_ranges(cells: list[dict[str, Any]]) -> dict[int, tuple[float, float]]:
    """Faixa horizontal de cada coluna, pelas células que ocupam uma coluna só."""
    ranges: dict[int, tuple[float, float]] = {}
    for cell in cells:
        regions = _regions(cell)
        if (cell.get("columnSpan") or 1) != 1 or not regions:
            continue
        x0, _, x1, _ = regions[0][1]
        column = cell.get("columnIndex", 0)
        low, high = ranges.get(column, (x0, x1))
        ranges[column] = (min(low, x0), max(high, x1))
    return ranges


def _header_notes(cell: dict[str, Any], words: list[tuple[str, list[float]]] | None,
                  ranges: dict[int, tuple[float, float]]) -> dict[int, str] | None:
    """Coluna de cada anotação de uma célula como "(não auditado) (não auditado)".

    O serviço costuma devolver essa célula cobrindo várias colunas, ou na coluna errada. A
    coluna certa é aquela sobre a qual a anotação está escrita, e isso vem da posição das
    palavras na página. None = não deu para localizar todas as anotações."""
    notes = _NOTE.findall(_cell_text(cell.get("content") or ""))
    regions = _regions(cell)
    centers: list[float] = []
    if words and regions:
        start: float | None = None
        for content, polygon in words:  # na ordem de leitura em que o serviço as devolve
            box = _box(polygon)
            if not _center_in(box, regions[0][1]):
                continue
            if start is None and content.lstrip().startswith("("):
                start = box[0]
            if start is not None and content.rstrip().endswith(")"):
                centers.append((start + box[2]) / 2)
                start = None
    if len(centers) == len(notes):
        found: dict[int, str] = {}
        for center, note in zip(centers, notes, strict=True):
            column = next((c for c, (x0, x1) in ranges.items() if x0 <= center <= x1), None)
            if column is None:
                return None
            found[column] = f"{found[column]} {note}" if column in found else note
        return found
    if len(notes) == 1 and (cell.get("columnSpan") or 1) == 1:
        return {cell.get("columnIndex", 0): notes[0]}
    return None


def table_markdown(table: dict[str, Any], words: list[tuple[str, list[float]]] | None = None) -> str:
    """Tabela do serviço em Markdown, no mesmo formato que o Docling exporta: uma linha de
    cabeçalho por coluna, com os níveis juntados ("Controladora - 2022").

    Célula de cabeçalho que cobre várias colunas é repetida em cada uma, para o nível de cima
    entrar no nome de todas. Célula de dados que cobre várias fica só na primeira posição: um
    número repetido nas outras pareceria vários valores.

    `words` são as palavras da página, com posição. Servem para um caso: a anotação que fica
    logo abaixo do cabeçalho ("(não auditado)" sob as colunas de 2021) vem como linha de dados,
    muitas vezes sob a coluna errada. Ela volta para o cabeçalho da coluna sobre a qual está
    escrita: "Controladora - 2021 - (não auditado)"."""
    rows, columns = table.get("rowCount") or 0, table.get("columnCount") or 0
    cells = table.get("cells") or []
    if not rows or not columns or not cells:
        return ""
    grid = [[""] * columns for _ in range(rows)]
    is_header = [[False] * columns for _ in range(rows)]
    for cell in cells:
        row, column = cell.get("rowIndex", 0), cell.get("columnIndex", 0)
        header = cell.get("kind") in _HEADER_KINDS
        text = _cell_text(cell.get("content") or "")
        row_span = (cell.get("rowSpan") or 1) if header else 1
        column_span = (cell.get("columnSpan") or 1) if header else 1
        for r in range(row, min(rows, row + row_span)):
            for c in range(column, min(columns, column + column_span)):
                grid[r][c], is_header[r][c] = text, header

    # Linhas de cabeçalho: as do topo em que tudo o que está preenchido é cabeçalho.
    header_rows = 0
    while (header_rows < rows and any(is_header[header_rows])
           and all(is_header[header_rows][c] or not grid[header_rows][c] for c in range(columns))):
        header_rows += 1
    if header_rows:
        levels = [[grid[r][c] for r in range(header_rows) if grid[r][c]] for c in range(columns)]
        body = grid[header_rows:]
        # Anotações de cabeçalho na primeira linha de dados: vão para a coluna certa. Se alguma
        # não puder ser localizada, a linha fica como o serviço a devolveu.
        moves: list[tuple[int, dict[int, str]]] = []
        for cell in cells:
            if cell.get("rowIndex") != header_rows or cell.get("kind") in _HEADER_KINDS:
                continue
            text = _cell_text(cell.get("content") or "")
            if text and _HEADER_NOTE.fullmatch(text):
                notes = _header_notes(cell, words, _column_ranges(cells))
                if notes is None:
                    moves = []
                    break
                moves.append((cell.get("columnIndex", 0), notes))
        for source, notes in moves:
            body[0][source] = ""
            for column, note in notes.items():
                levels[column].append(note)
        if moves and not any(body[0]):
            body = body[1:]
        header = [" - ".join(dict.fromkeys(level)) for level in levels]
    else:  # sem cabeçalho reconhecido, a primeira linha faz o papel dele
        header, body = grid[0], grid[1:]

    # Rótulo de linha quebrado em duas: "Lucro operacional antes das receitas e despesas" com
    # os valores, e na linha seguinte só "financeiras". A continuação volta para o rótulo.
    merged: list[list[str]] = []
    for row in body:
        if (merged and merged[-1][0] and row[0] and row[0][0].islower() and not any(row[1:])):
            merged[-1][0] = f"{merged[-1][0]} {row[0]}"
        else:
            merged.append(row)
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(row) + " |" for row in merged]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Texto de dentro das figuras
# ---------------------------------------------------------------------------


def _reading(polygon: list[float]) -> str:
    """Sentido de leitura de uma palavra: "h" (horizontal), "up" (vertical, de baixo para cima)
    ou "down". Vem da primeira aresta do polígono, que acompanha a linha de base do texto."""
    dx, dy = polygon[2] - polygon[0], polygon[3] - polygon[1]
    if abs(dy) <= abs(dx):
        return "h"
    return "up" if dy < 0 else "down"


def _segment(group: list[tuple]) -> OcrWord:
    boxes = [word[1] for word in group]
    return OcrWord(text=" ".join(word[0] for word in group),
                   x0=min(b[0] for b in boxes), y0=min(b[1] for b in boxes),
                   x1=max(b[2] for b in boxes), y1=max(b[3] for b in boxes))


def figure_segments(words: list[tuple[str, list[float]]], box: Box) -> list[OcrWord] | None:
    """Trechos de texto dentro da região da figura, na unidade da página.

    O serviço devolve palavra por palavra, cada uma com o seu polígono. A conferência de
    posição dos gráficos compara rótulos inteiros, então palavras vizinhas na mesma linha são
    juntadas num trecho só ("Perdas Totais"), e um espaço largo separa trechos ("dez/20" e
    "mar/21" num eixo). Palavra escrita na vertical só se junta a outra vertical da mesma
    coluna: o "29.082" de dentro de uma barra não vira "29.082 85%" com o rótulo ao lado.
    None = o serviço não devolveu nenhuma palavra dentro da figura."""
    inside = [(text.strip(), _box(polygon), _reading(polygon)) for text, polygon in words if text.strip()]
    inside = [word for word in inside if _center_in(word[1], box)]
    if not inside:
        return None
    segments: list[OcrWord] = []
    for reading in ("h", "up", "down"):
        # (texto, caixa, início e fim no sentido da leitura, início e fim na perpendicular)
        placed: list[tuple] = []
        for text, (x0, y0, x1, y1), word_reading in inside:
            if word_reading != reading:
                continue
            if reading == "h":
                placed.append((text, (x0, y0, x1, y1), x0, x1, y0, y1))
            elif reading == "up":
                placed.append((text, (x0, y0, x1, y1), -y1, -y0, x0, x1))
            else:
                placed.append((text, (x0, y0, x1, y1), y0, y1, x0, x1))
        rows: list[list[tuple]] = []  # linhas (ou colunas, no texto vertical)
        for word in sorted(placed, key=lambda w: (w[4] + w[5]) / 2):
            for row in rows:
                first = row[0]
                shorter = min(first[5] - first[4], word[5] - word[4])
                overlap = min(first[5], word[5]) - max(first[4], word[4])
                if shorter > 0 and overlap >= _ROW_OVERLAP * shorter:
                    row.append(word)
                    break
            else:
                rows.append([word])
        for row in rows:
            row.sort(key=lambda w: w[2])
            # Altura da letra: o menor lado da caixa. Vale também quando a fonte não informa o
            # sentido e um rótulo vertical chega como uma caixa alta e estreita.
            height = median(min(b[2] - b[0], b[3] - b[1]) for _, b, *_ in row)
            group = [row[0]]
            for word in row[1:]:
                # Dois números vizinhos são dois valores, por mais perto que estejam: os de
                # uma barra empilhada ficam quase encostados.
                both_numbers = _NUMERIC.match(word[0]) and _NUMERIC.match(group[-1][0])
                if both_numbers or word[2] - group[-1][3] > _SEGMENT_GAP * height:
                    segments.append(_segment(group))
                    group = [word]
                else:
                    group.append(word)
            segments.append(_segment(group))
    return segments


def _in_crop(segment: OcrWord, scale_x: float, scale_y: float, origin: tuple[int, int]) -> OcrWord:
    """Da unidade da página para pixels do recorte, que é como a conferência dos gráficos lê."""
    return OcrWord(text=segment.text,
                   x0=segment.x0 * scale_x - origin[0], y0=segment.y0 * scale_y - origin[1],
                   x1=segment.x1 * scale_x - origin[0], y1=segment.y1 * scale_y - origin[1])


# ---------------------------------------------------------------------------
# Títulos, cabeçalhos e rodapés
# ---------------------------------------------------------------------------


class _Lines:
    """Linhas de cada página, para saber em que linhas um parágrafo foi escrito. O serviço
    devolve o parágrafo como um texto só; as quebras de linha estão na lista de linhas."""

    def __init__(self, pages: dict[int, dict[str, Any]]) -> None:
        self._pages: dict[int, tuple[list[int], list[str]]] = {}
        for number, page in pages.items():
            found = sorted(
                ((offset, _clean(line.get("content") or "")) for line in page.get("lines") or []
                 if (offset := _first_offset(line)) is not None),
                key=lambda line: line[0])
            self._pages[number] = ([offset for offset, _ in found], [text for _, text in found])

    def of(self, paragraph: dict[str, Any], page: int) -> list[str]:
        """Linhas do parágrafo. Se o serviço não devolveu as linhas, o parágrafo é uma linha só."""
        offsets, texts = self._pages.get(page, ([], []))
        spans = paragraph.get("spans") or []
        found: list[str] = []
        if spans and offsets:
            start = spans[0]["offset"]
            first, last = bisect_left(offsets, start), bisect_left(offsets, start + spans[0]["length"])
            found = [text for text in texts[first:last] if text]
        return found or [text for text in [_clean(paragraph.get("content") or "")] if text]


class _Running:
    """Cabeçalhos e rodapés corridos, a partir do que o serviço rotulou.

    O rótulo do serviço falha nos dois sentidos. Às vezes o mesmo cabeçalho vem sem rótulo
    numa página ou outra (na Light, 5 vezes em 175). E às vezes um título que só existe no alto
    de uma página vem rotulado como cabeçalho. O que separa um do outro é a repetição:
    cabeçalho corrido aparece em mais de uma página."""

    def __init__(self, placed: list[tuple[dict[str, Any], int, Box | None, int, int]], lines: _Lines,
                 pages: dict[int, dict[str, Any]]) -> None:
        self._heights = {number: float(page.get("height") or 0) for number, page in pages.items()}
        self._line_pages: dict[str, set[int]] = {}
        # texto do parágrafo → (páginas, topo e pé da faixa que ele ocupa, em fração da altura)
        self._bands: dict[str, tuple[set[int], float, float]] = {}
        for paragraph, page, box, _, _ in placed:
            if paragraph.get("role") not in _RUNNING_ROLES:
                continue
            for line in lines.of(paragraph, page):
                self._line_pages.setdefault(_key(line), set()).add(page)
            height = self._heights.get(page)
            if box is not None and height:
                key = _key(_clean(paragraph.get("content") or ""))
                found, top, bottom = self._bands.get(key, (set(), 1.0, 0.0))
                found.add(page)
                self._bands[key] = (found, min(top, box[1] / height), max(bottom, box[3] / height))

    def repeats(self, line: str) -> bool:
        """A linha é de um cabeçalho ou rodapé que aparece em mais de uma página?"""
        return len(self._line_pages.get(_key(line), ())) >= 2

    def untagged(self, text: str, page: int, box: Box | None) -> bool:
        """Parágrafo sem rótulo que é cabeçalho ou rodapé corrido: tem o mesmo texto de um que
        o serviço rotulou em outras páginas e está na mesma faixa da página."""
        found = self._bands.get(_key(text))
        height = self._heights.get(page)
        if found is None or len(found[0]) < 2 or box is None or not height:
            return False
        center = (box[1] + box[3]) / 2 / height
        return found[1] - _BAND_TOLERANCE <= center <= found[2] + _BAND_TOLERANCE


def _statement_title_parts(lines: list[str], role: str | None, running: _Running) -> list[tuple[str, str]] | None:
    """Bloco de título de uma demonstração que o serviço devolveu como um parágrafo só:
    empresa, título, período e unidade ("LIGHT S.A." / "BALANÇOS PATRIMONIAIS" / "EM 31 DE
    DEZEMBRO DE 2022 E DE 2021" / "(Em milhares de reais)"). Devolve as partes em (tipo, texto),
    como o resto do pipeline espera: o título abre a seção, e o que vem depois dele é texto,
    que o chunker põe no alto da tabela. None se o parágrafo não é um bloco desses.

    Linha que começa em minúscula é continuação da anterior ("Demonstração do resultado da
    controlada em conjunto ... em 31 de dezembro" / "de 2022"): aí é um título só, quebrado
    pela largura da página, e nada é separado."""
    if not 2 <= len(lines) <= _TITLE_BLOCK_MAX_LINES:
        return None
    index = next((i for i, line in enumerate(lines) if STATEMENT_TITLE.match(line)), None)
    if index is None or any(not (line[0].isupper() or line[0] in "(\"“") for line in lines[index + 1:]):
        return None
    # Parágrafo comum: só é bloco de título se o título vem logo no começo e nada, do título em
    # diante, termina como frase. (A linha da empresa, antes dele, termina em "S.A.")
    if role not in _HEADING_ROLES and (index > 1 or any(line[-1] in ".;:," for line in lines[index:])):
        return None
    parts = [("heading", line) for line in lines[:index] if not running.repeats(line)]
    parts.append(("heading", lines[index]))
    rest = " ".join(lines[index + 1:])
    return [*parts, ("text", rest)] if rest else parts


# ---------------------------------------------------------------------------
# Resposta do serviço → itens na ordem de leitura
# ---------------------------------------------------------------------------


def convert_layout(result: dict[str, Any], *, crop: Crop, min_area_ratio: float,
                   small_min_area_ratio: float, text_layer: TextLayer | None = None) -> list[ParsedItem]:
    """Parágrafos, tabelas e figuras do serviço, na ordem de leitura, no esquema do projeto.

    `result` é a resposta no formato da API REST (chaves em camelCase). `crop` grava o recorte
    de uma figura e é chamado só para as que vão ao modelo de visão: as que passam de
    `min_area_ratio` e as pequenas (a partir de `small_min_area_ratio`) em que o serviço leu
    números, que são gráficos pequenos. O resto são logotipos e ícones. `text_layer` lê a
    camada de texto do PDF na região de cada figura recortada."""
    pages = {page["pageNumber"]: page for page in result.get("pages") or []}
    tables, figures = result.get("tables") or [], result.get("figures") or []
    paragraphs = result.get("paragraphs") or []

    # O que está dentro de uma tabela ou de uma figura não é texto corrido: é célula ou rótulo.
    containers = [(element.get("spans") or [], _regions(element)) for element in (*tables, *figures)]
    # Legenda de figura vai para o item da figura. Legenda de tabela e notas de rodapé (de
    # tabela ou de figura) ficam no texto, em ordem de leitura: o chunker as junta à tabela.
    figure_captions = {offset for figure in figures
                       if (offset := _first_offset(figure.get("caption") or {})) is not None}
    notes = [table.get("caption") for table in tables]
    notes += [note for element in (*tables, *figures) for note in element.get("footnotes") or []]
    kept_notes = {offset for note in notes if note and (offset := _first_offset(note)) is not None}

    def contained(offset: int, page: int, box: Box | None) -> bool:
        return any(
            any(span["offset"] <= offset < span["offset"] + span["length"] for span in spans)
            or (box is not None
                and any(number == page and _center_in(box, region) for number, region in regions))
            for spans, regions in containers
        )

    def page_of(offset: int) -> int | None:
        """Página de um trecho do texto corrido, para o parágrafo que vier sem região."""
        for number, page in pages.items():
            if any(span["offset"] <= offset < span["offset"] + span["length"]
                   for span in page.get("spans") or []):
                return number
        return None

    blocks: list[tuple[float, int, ParsedItem]] = []  # (posição na leitura, desempate, item)
    tops: dict[int, list[tuple[float, int]]] = {}  # página → (topo, posição) de cada parágrafo

    # Parágrafos que têm onde ficar: (parágrafo, página, caixa, última página, posição no texto).
    placed: list[tuple[dict[str, Any], int, Box | None, int, int]] = []
    for paragraph in paragraphs:
        regions, offset = _regions(paragraph), _first_offset(paragraph)
        if offset is None:
            continue
        page, box = regions[0] if regions else (page_of(offset), None)
        if page is not None:
            placed.append((paragraph, page, box, max((number for number, _ in regions), default=page), offset))
    lines = _Lines(pages)
    running = _Running(placed, lines, pages)

    def is_body(paragraph: dict[str, Any], page: int, box: Box | None, offset: int) -> bool:
        """O parágrafo vira item? Fora ficam cabeçalho, rodapé, número de página, legenda de
        figura e o que está dentro de tabela ou figura."""
        if paragraph.get("role") in _SKIP_ROLES or offset in figure_captions:
            return False
        return offset in kept_notes or not contained(offset, page, box)

    def header_title(index: int) -> list[tuple[str, str]]:
        """Título que o serviço rotulou como cabeçalho de página, em (tipo, texto).

        Cabeçalho corrido se repete; o que aparece no alto de uma página só pode ser o título
        dela. Vale quando é o nome de uma demonstração ("Demonstrações dos fluxos de caixa", na
        Millet) ou quando tem cara de título e a página não abre com um título de verdade
        (o "Anexo I" da Light). Timbre com endereço e telefone não passa."""
        paragraph, page, _, _, _ = placed[index]
        rare = [line for line in lines.of(paragraph, page) if not running.repeats(line)]
        if not rare:
            return []
        statement = next((i for i, line in enumerate(rare) if STATEMENT_TITLE.match(line)), None)
        if statement is not None:
            rest = " ".join(rare[statement + 1:])
            return [("heading", rare[statement]), *([("text", rest)] if rest else [])]
        if any(len(line) > _TITLE_MAX_CHARS or _NOT_A_TITLE.search(line) for line in rare):
            return []
        for other, other_page, other_box, _, other_offset in placed[index + 1:]:
            if other_page != page:
                break
            if is_body(other, other_page, other_box, other_offset):
                if other.get("role") in _HEADING_ROLES:
                    return []
                break
        return [("heading", " ".join(rare))]

    for index, (paragraph, page, box, last, offset) in enumerate(placed):
        if box is not None:
            tops.setdefault(page, []).append((box[1], offset))
        role = paragraph.get("role")
        text = _clean(paragraph.get("content") or "")
        if not text:
            continue
        if role == "pageHeader":
            parts = header_title(index)
        elif not is_body(paragraph, page, box, offset) or (role is None and running.untagged(text, page, box)):
            continue  # fora do texto corrido, ou cabeçalho corrido que veio sem rótulo
        else:
            # Linha de período ou de unidade rotulada como título ("Exercício findo em 31 de
            # dezembro de 2021") é o subtítulo de uma tabela: vai como texto, para o alto dela.
            heading = role in _HEADING_ROLES and not _PERIOD_LINE.match(text)
            parts = (_statement_title_parts(lines.of(paragraph, page), role, running)
                     or [("heading" if heading else "text", text)])
        for kind, piece in parts:
            if kind == "heading":
                item = ParsedItem(type="heading", text=piece, page=page, level=1)
            else:
                item = ParsedItem(type="text", text=piece, page=page, page_end=last if last > page else None)
            blocks.append((offset, len(blocks), item))

    first_on_page = {page: min(offset for _, offset in found) for page, found in tops.items()}
    end = len(result.get("content") or "")

    def position(element: dict[str, Any], page: int, top: float) -> float:
        """Posição na leitura. Figura sem texto não tem posição no texto corrido: fica logo
        antes do primeiro parágrafo que começa abaixo dela na página."""
        offset = _first_offset(element)
        if offset is not None:
            return offset
        below = [found for found_top, found in tops.get(page, []) if found_top >= top]
        if below:
            return min(below) - 0.5
        later = [first_on_page[number] for number in first_on_page if number > page]
        return (min(later) if later else end + page) - 0.5

    words_by_page = {number: [(word.get("content") or "", word["polygon"]) for word in page.get("words") or []
                              if len(word.get("polygon") or []) >= 8]
                     for number, page in pages.items()}

    for table in tables:
        regions = _regions(table)
        markdown = table_markdown(table, words_by_page.get(regions[0][0]) if regions else None)
        if regions and markdown:
            page, box = regions[0]
            blocks.append((position(table, page, box[1]), len(blocks),
                           ParsedItem(type="table", text=markdown, page=page)))

    for figure in figures:
        regions = _regions(figure)
        if not regions or regions[0][0] not in pages:
            continue
        number, box = regions[0]
        page = pages[number]
        size = (float(page.get("width") or 0), float(page.get("height") or 0))
        area = (box[2] - box[0]) * (box[3] - box[1]) / (size[0] * size[1]) if size[0] and size[1] else 0.0
        item = ParsedItem(type="figure", text=_clean((figure.get("caption") or {}).get("content") or ""),
                          page=number, area_ratio=round(area, 4))
        segments = figure_segments(words_by_page.get(number, []), box) or []
        has_numbers = any(_HAS_NUMBER.search(segment.text) for segment in segments)
        wanted = area >= min_area_ratio or (area >= small_min_area_ratio and has_numbers)
        if wanted and (cropped := crop(number, box, size)):
            item.image_path, scale_x, scale_y, origin = cropped
            # Se o serviço não leu nenhum número dentro da figura, ou ela não tem números ou eles
            # estão numa imagem que ele não leu. Nos dois casos o que veio (um título, por
            # exemplo) não confere valor nenhum: a figura fica para o OCR local, se houver.
            if has_numbers:
                item.ocr_words = [_in_crop(segment, scale_x, scale_y, origin) for segment in segments]
            if text_layer is not None:
                item.text_layer = text_layer(number, box, size)
        blocks.append((position(figure, number, box[1]), len(blocks), item))

    blocks.sort(key=lambda block: block[:2])
    return [item for _, _, item in blocks]


# ---------------------------------------------------------------------------
# Recorte das figuras e chamada ao serviço
# ---------------------------------------------------------------------------


class _FigureCropper:
    """Renderiza a página com o pypdfium2 e recorta a região de cada figura."""

    def __init__(self, pdf_path: Path, out_dir: Path, scale: float) -> None:
        import pypdfium2

        self._pdf = pypdfium2.PdfDocument(str(pdf_path))
        self._out_dir, self._scale = out_dir, scale
        self._rendered: tuple[int, Any] | None = None  # última página renderizada
        self._count = 0

    def __call__(self, page: int, box: Box, size: tuple[float, float]):
        try:
            if self._rendered is None or self._rendered[0] != page:
                self._rendered = (page, self._pdf[page - 1].render(scale=self._scale).to_pil())
            image = self._rendered[1]
        except Exception as exc:  # noqa: BLE001 - sem o recorte a figura só não é transcrita
            log.warning("Não foi possível renderizar a página %d: %s", page, exc)
            return None
        scale_x, scale_y = image.width / size[0], image.height / size[1]
        left, top = max(0, math.floor(box[0] * scale_x)), max(0, math.floor(box[1] * scale_y))
        right = min(image.width, math.ceil(box[2] * scale_x))
        bottom = min(image.height, math.ceil(box[3] * scale_y))
        if right - left < 2 or bottom - top < 2:
            return None
        self._count += 1
        relative = f"figures/p{page:04d}_{self._count:03d}.png"
        (self._out_dir / "figures").mkdir(parents=True, exist_ok=True)
        image.crop((left, top, right, bottom)).save(self._out_dir / relative, format="PNG")
        return relative, scale_x, scale_y, (left, top)

    def text_layer(self, page: int, box: Box, size: tuple[float, float]) -> str | None:
        """Camada de texto do PDF dentro da região: os rótulos de um gráfico vetorial, exatos.
        Vazio num gráfico colado como imagem. None se não der para ler (página girada, erro)."""
        try:
            pdf_page = self._pdf[page - 1]
            if pdf_page.get_rotation():  # as coordenadas do serviço são as da página já girada
                return None
            width, height = pdf_page.get_size()  # em pontos, com a origem embaixo
            scale_x, scale_y = width / size[0], height / size[1]
            text_page = pdf_page.get_textpage()
            try:
                return text_page.get_text_bounded(left=box[0] * scale_x, bottom=height - box[3] * scale_y,
                                                  right=box[2] * scale_x, top=height - box[1] * scale_y)
            finally:
                text_page.close()
        except Exception as exc:  # noqa: BLE001 - sem ela a figura só não é completada
            log.warning("Não foi possível ler a camada de texto da página %d: %s", page, exc)
            return None

    def close(self) -> None:
        self._pdf.close()


def pdf_for_upload(pdf_path: Path) -> bytes:
    """Bytes do PDF a enviar. PDF só com restrição de permissões (criptografado com senha de
    abertura vazia, como o da Brasal) é regravado sem a criptografia: o serviço pede que a
    proteção seja removida antes do envio."""
    data = pdf_path.read_bytes()
    reader = PdfReader(io.BytesIO(data))
    if not reader.is_encrypted:
        return data
    reader.decrypt("")
    buffer = io.BytesIO()
    PdfWriter(clone_from=reader).write(buffer)
    return buffer.getvalue()


class AzureParser:
    name = "azure"

    def __init__(self, settings: Settings, client: DocumentIntelligenceClient | None = None) -> None:
        if client is None and not settings.azure_di_configured:
            raise ParserError("O parser da Azure exige AZURE_DI_ENDPOINT e AZURE_DI_KEY no .env.")
        check_system_fonts()  # os recortes de gráfico são renderizados aqui
        self._settings = settings
        self._client = client
        self.page_price_usd = settings.azure_di_price_per_1k_pages / 1000
        self._local_ocr: Any = None  # OCR local, só como reserva; carregado se for preciso

    def warmup(self) -> None:
        """Nada a carregar: os modelos estão no serviço."""

    def _analyze(self, data: bytes) -> dict[str, Any]:
        client = self._client or DocumentIntelligenceClient(
            self._settings.azure_di_endpoint.strip(), AzureKeyCredential(self._settings.azure_di_key.strip()))
        try:
            poller = client.begin_analyze_document(
                self._settings.azure_di_model, body=io.BytesIO(data),
                content_type="application/octet-stream", string_index_type="unicodeCodePoint",
            )
            poller.wait(self._settings.azure_di_timeout_seconds)
            if not poller.done():
                raise TimeoutError(
                    f"A análise no Azure não terminou em {self._settings.azure_di_timeout_seconds} s.")
            return poller.result().as_dict()
        except ClientAuthenticationError as exc:
            raise ParserError(f"O Azure recusou a credencial (AZURE_DI_KEY): {exc.message}") from exc
        except HttpResponseError as exc:
            if exc.status_code in _PERMANENT_STATUS:
                raise ParserError(
                    f"O Azure recusou a requisição ({exc.status_code}). Confira AZURE_DI_ENDPOINT, "
                    f"AZURE_DI_KEY e AZURE_DI_MODEL: {exc.message}") from exc
            raise  # 429, 5xx, rede: vale tentar de novo

    def _stored_response(self, path: Path, total: int) -> dict[str, Any] | None:
        """Resposta do serviço guardada por um parse anterior deste documento, se for do mesmo
        modelo e cobrir todas as páginas."""
        if not path.exists():
            return None
        try:
            result = json.loads(gzip.decompress(path.read_bytes()))
        except (OSError, EOFError, ValueError, zlib.error):
            return None  # arquivo pela metade ou corrompido: chama o serviço de novo
        if (not isinstance(result, dict) or result.get("modelId") != self._settings.azure_di_model
                or len(result.get("pages") or []) < total):
            return None
        return result

    @staticmethod
    def _store_response(result: dict[str, Any], path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(gzip.compress(json.dumps(result, ensure_ascii=False).encode("utf-8")))
        temporary.replace(path)  # troca atômica, como no parsed.json

    def parse(self, pdf_path: Path, doc_id: str, file_name: str, out_dir: Path,
              on_progress: ProgressCallback | None = None) -> ParsedDocument:
        pages = native_pages(pdf_path)
        total = len(pages)
        if on_progress:
            on_progress(0, total)
        response_file = out_dir / RESPONSE_FILE
        result = self._stored_response(response_file, total)
        billed = 0
        if result is None:
            result = self._analyze(pdf_for_upload(pdf_path))
            billed = len(result.get("pages") or [])
            if billed < total:
                raise ParserError(
                    f"O Azure analisou {billed} de {total} páginas. O nível gratuito (F0) lê só as "
                    "duas primeiras páginas de cada documento; use um recurso no nível S0.")
            self._store_response(result, response_file)

        cropper = _FigureCropper(pdf_path, out_dir, self._settings.figure_render_scale)
        try:
            items = convert_layout(result, crop=cropper, text_layer=cropper.text_layer,
                                   min_area_ratio=self._settings.figure_min_area_ratio,
                                   small_min_area_ratio=self._settings.figure_small_min_area_ratio)
        finally:
            cropper.close()
        if on_progress:
            on_progress(total, total)
        return ParsedDocument(
            doc_id=doc_id,
            file_name=file_name,
            page_count=total,
            parser=f"azure-document-intelligence {result.get('modelId') or self._settings.azure_di_model} "
                   f"{result.get('apiVersion') or ''}".strip(),
            pipeline_version=PIPELINE_VERSION,
            items=items,
            pages=pages,
            ocr_pages=[p.page for p in pages if len(p.native_text.strip()) < MIN_NATIVE_CHARS],
            billed_pages=billed,
        )

    def read_figures(self, parsed: ParsedDocument, out_dir: Path) -> int:
        """Reserva para figura em que o serviço não devolveu nenhuma palavra: o OCR local lê o
        recorte, se estiver instalado (imagem do alvo "parse"). Na imagem leve a figura fica
        sem texto de conferência, e os valores dela que não estiverem no texto da página são
        omitidos, como manda a regra dos gráficos."""
        from app.pipeline.figure_ocr import FigureOcr, read_figures  # só numpy e Pillow no import

        pending = [item for item in parsed.items
                   if item.type == "figure" and item.image_path and item.ocr_words is None]
        if not pending:
            return 0
        if self._local_ocr is None:
            ocr = FigureOcr()
            try:
                ocr.warmup()  # é aqui que o RapidOCR é importado
                self._local_ocr = ocr
            except ImportError:
                self._local_ocr = False
        if not self._local_ocr:
            for item in pending:
                item.ocr_words = []
            log.info("%d figura(s) sem texto devolvido pelo Azure e sem OCR local nesta imagem.", len(pending))
            return 0
        return read_figures(parsed, out_dir, self._local_ocr, self._settings.figure_min_area_ratio)
