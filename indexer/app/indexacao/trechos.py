"""Chunking estrutural: segue a estrutura do documento em vez de cortar por tamanho fixo.

- Texto: parágrafos da mesma seção agrupados até ~450 tokens, corte em fronteira de parágrafo.
- Tabela: um chunk por tabela, com as linhas de título que a antecedem (período, unidade).
  Tabela grande é dividida por linhas, repetindo o cabeçalho em cada parte.
- Figura: um chunk por gráfico transcrito e conferido.
Todo chunk guarda a página do PDF e o caminho de seções, para permitir citação.
"""
import math
import re
from dataclasses import dataclass, field
from typing import Literal

from app.config import Settings
from app.indexacao.metadados import detect_declared_unit, page_units, resolve_unit, scope_units
from app.leitura.figuras import FigureResult
from app.plugins.interface import PluginGenerico, PluginIndexador
from app.schemas import ParsedDocument, ParsedItem

ChunkType = Literal["text", "table", "figure"]

_OVERLAP_MAX_TOKENS = 120  # só repete o último parágrafo no chunk seguinte se ele for curto
_PREAMBLE_MAX_TOKENS = 80  # linha curta antes de uma tabela é tratada como título dela
_PREAMBLE_MAX_LINES = 4
_PREAMBLE_BUDGET = 160  # tokens somados das linhas de título
_MIN_TEXT_TOKENS = 3

# Cabeçalho/rodapé corrido: mesma linha curta, no topo ou no pé, em muitas páginas.
_RUNNING_MAX_TOKENS = 60
_RUNNING_MIN_PAGES = 5
_RUNNING_MIN_PAGE_SHARE = 0.08
_RUNNING_TOP_ITEMS = 4
_RUNNING_BOTTOM_ITEMS = 2
_RUNNING_EDGE_SHARE = 0.8
_ANY_CONTINUATION = re.compile(r"\s*[-–—]*\s*\(?continua[cç][aã]o\)?", re.IGNORECASE)

_CONTINUATION = re.compile(r"\s*(?:[-–—]{1,2}\s*continua[cç][aã]o|\(continua[cç][aã]o\))\s*$", re.IGNORECASE)
# Numeração de nota: "22.", "22.1", "3.14.2", "7.Investimentos". Grupos: número, ponto final,
# espaço e o primeiro caractere do título.
_NUMBERED = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,3})(\.?)(\s*)(\S)")
_TITLE_OPENERS = "\"“'(["
# Linha curta, sem quebra e sem pontuação de frase no fim: só uma linha assim pode ser o nome
# fixo de uma seção que o plugin do domínio reconhece (ex.: "Balanços patrimoniais").
_FIXED_TITLE_MAX_CHARS = 200
_MERGED_TITLE_MAX_CHARS = 240
_TOC_LEADER = re.compile(r"\.{5,}")
_TOC_MIN_LINES = 5
_TOC_ROW_SHARE = 0.8  # fração das linhas de uma tabela que têm de ser "título | página"
_TOC_LEFTOVER_MAX_TOKENS = 80  # texto curto que sobra na página do índice ("Índice", o nome da empresa)
_TOC_SECTION_NUMBER = re.compile(r"\d{1,2}\.?")
_NUMERIC_CELL = re.compile(r"[\d.,()%\s-]+")
_FOOTNOTE = re.compile(r"^(?:\(\w{1,3}\)|\*{1,3}|\d{1,2}\))\s*\S")
_FOOTNOTE_MAX_TOKENS = 80
_LETTERED = re.compile(r"^(?:[a-z]\)|\([a-z]\)|\([ivx]+\))\s+\S", re.IGNORECASE)
_LETTERED_TITLE = re.compile(r"^[a-z]\)\s+[A-ZÀ-Ý]")  # "b) Receitas de contratos com clientes"
_HEADING_MAX_CHARS = 120
_TABLE_SEPARATOR =re.compile(r"^\s*\|?\s*:?-{2,}")
_SENTENCE_END = re.compile(r"(?<=[.!?;])\s+")
_INJECTION = re.compile(
    r"ignore\s+(?:all\s+|any\s+)?(?:previous|prior|above)\s+instructions"
    r"|(?:ignore|desconsidere|esque[cç]a)\s+(?:todas\s+)?as\s+instru[cç][oõ]es"
    r"|system\s+prompt|prompt\s+do\s+sistema|you\s+are\s+now|voc[eê]\s+agora\s+[eé]"
    r"|<\s*/?\s*system\s*>|jailbreak",
    re.IGNORECASE,
)

_encoding = None
_encoding_failed = False


def count_tokens(text: str) -> int:
    """Tokens pelo tiktoken (cl100k_base). Sem acesso ao vocabulário, usa uma estimativa."""
    global _encoding, _encoding_failed
    if _encoding is None and not _encoding_failed:
        try:
            import tiktoken

            _encoding = tiktoken.get_encoding("cl100k_base")
        except Exception:  # noqa: BLE001 - sem rede/cache na primeira execução
            _encoding_failed = True
    if _encoding is not None:
        return len(_encoding.encode(text, disallowed_special=()))
    return math.ceil(len(text) / 3.5)


@dataclass
class Chunk:
    chunk_index: int
    chunk_type: ChunkType
    extraction_method: str
    text: str
    section_path: list[str]
    page_start: int
    page_end: int
    unit: str | None = None
    unit_source: str | None = None  # "chunk" | "page" | "mentions" | "inherited"
    token_count: int = 0
    verified_ratio: float | None = None
    unverified_values: list[str] = field(default_factory=list)
    verified_by_ocr: int | None = None  # só em figuras: valores confirmados apenas por OCR
    verified_labels_ratio: float | None = None  # só em figuras: ligações rótulo-valor conferidas
    suspicious: bool = False


def note_number(title: str) -> str | None:
    """Número de nota no início do título ("22.1 Provisões" → "22.1"), ou None.

    Depois do número tem de vir um título: ponto ou espaço, e uma maiúscula. "31 de dezembro
    de 2022" e "5 anos" começam com número e não são notas."""
    numbered = _NUMBERED.match(title)
    if not numbered:
        return None
    number, dot, space, first = numbered.groups()
    if not (dot or space) or not (first.isupper() or first in _TITLE_OPENERS):
        return None
    return number


def numbering_depth(title: str) -> int | None:
    """Profundidade pela numeração: "22." → 1, "22.1" → 2, "22.1.4" → 3. None se não numerado."""
    number = note_number(title)
    return number.count(".") + 1 if number else None


def _same_title(a: str, b: str) -> bool:
    """Mesmo título, ignorando caixa, espaços e pontuação ("7.Investimentos" = "7. Investimentos")."""
    return re.sub(r"\W+", "", a).casefold() == re.sub(r"\W+", "", b).casefold()


def _looks_like_note_title(text: str) -> str | None:
    """Número da nota se a linha tem cara de título numerado: curta, sem pontuação final."""
    line = text.strip()
    if not line or "\n" in line or len(line) > _HEADING_MAX_CHARS or line[-1] in ".;:,":
        return None
    return note_number(line)


def _toc_row_page(line: str) -> int | None:
    """Página apontada por uma linha de índice em forma de tabela ("| 1. | Contexto operacional
    | 8 |"), ou None se a linha não for isso: tem de ser um título e, no fim, um número de
    página. Uma linha com outro número (um valor) é de tabela comum."""
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    filled = [cell for cell in cells if cell]
    if len(filled) < 2 or not re.fullmatch(r"\d{1,4}", filled[-1]):
        return None
    titles = [cell for cell in filled[:-1] if not _TOC_SECTION_NUMBER.fullmatch(cell)]
    if not titles or any(_NUMERIC_CELL.fullmatch(cell) for cell in titles):
        return None
    return int(filled[-1])


def is_table_of_contents(text: str) -> bool:
    """Índice. Em texto corrido, a maioria das linhas termina em pontilhado e número de página.
    Em forma de tabela (é como o parser da Azure o devolve), quase todas as linhas são um
    título seguido do número da página, e as páginas não diminuem de uma linha para a outra."""
    lines = [line for line in text.splitlines() if line.strip()]
    leaders = sum(1 for line in lines if _TOC_LEADER.search(line))
    if leaders >= _TOC_MIN_LINES and leaders >= len(lines) / 2:
        return True
    rows = [line for line in lines if line.lstrip().startswith("|") and not _TABLE_SEPARATOR.match(line)]
    pages = [page for line in rows if (page := _toc_row_page(line)) is not None]
    return (len(pages) >= _TOC_MIN_LINES and len(pages) >= _TOC_ROW_SHARE * len(rows)
            and pages == sorted(pages))


def _running_key(text: str) -> str:
    return re.sub(r"\s+", " ", _ANY_CONTINUATION.sub("", text)).strip().lower()


def drop_running_headers(items: list[ParsedItem], page_count: int) -> list[ParsedItem]:
    """Remove cabeçalhos e rodapés corridos que o parser não rotulou como tal.

    Ex.: o nome da empresa e "Notas explicativas às demonstrações financeiras" no topo de
    cada página. Se ficassem, virariam "seção" de tudo e gerariam dezenas de chunks iguais.
    Critério: linha curta que se repete em muitas páginas e quase sempre na borda da página.
    Num documento longo, "muitas páginas" é uma fração dele. Em dois casos bastam cinco: linha
    no pé da página (ali só há rodapé, como "As notas explicativas são parte integrante das
    demonstrações financeiras") e linha que o layout rotulou como título (o logotipo "Deloitte."
    no alto de cada página do parecer). Uma linha de texto repetida no topo pode ser o título
    de uma tabela, e por isso continua exigindo a fração. Tabelas e figuras nunca são removidas.
    """
    by_page: dict[int, list[ParsedItem]] = {}
    for item in items:
        by_page.setdefault(item.page, []).append(item)

    pages_seen: dict[str, set[int]] = {}
    at_edge: dict[str, int] = {}
    at_bottom: dict[str, int] = {}
    as_heading: dict[str, int] = {}
    occurrences: dict[str, int] = {}
    for page, page_items in by_page.items():
        for position, item in enumerate(page_items):
            if item.type not in ("heading", "text") or count_tokens(item.text) > _RUNNING_MAX_TOKENS:
                continue
            key = _running_key(item.text)
            # Título numerado que se repete ("3. Sumário...--Continuação" em dez páginas) é
            # continuação de nota, não cabeçalho corrido: é ele que mantém a seção certa.
            if not key or note_number(item.text) or _LETTERED.match(item.text):
                continue
            pages_seen.setdefault(key, set()).add(page)
            occurrences[key] = occurrences.get(key, 0) + 1
            bottom = position >= len(page_items) - _RUNNING_BOTTOM_ITEMS
            at_bottom[key] = at_bottom.get(key, 0) + int(bottom)
            at_edge[key] = at_edge.get(key, 0) + int(bottom or position < _RUNNING_TOP_ITEMS)
            as_heading[key] = as_heading.get(key, 0) + int(item.type == "heading")

    def mostly(counter: dict[str, int], key: str) -> bool:
        return counter[key] / occurrences[key] >= _RUNNING_EDGE_SHARE

    min_pages = max(_RUNNING_MIN_PAGES, math.ceil(page_count * _RUNNING_MIN_PAGE_SHARE))
    running = {
        key for key, pages in pages_seen.items()
        if (len(pages) >= min_pages and mostly(at_edge, key))
        or (len(pages) >= _RUNNING_MIN_PAGES and (mostly(at_bottom, key)
                                                  or (mostly(at_edge, key) and mostly(as_heading, key))))
    }
    return [item for item in items
            if item.type not in ("heading", "text") or _running_key(item.text) not in running]


def split_long_text(text: str, max_tokens: int) -> list[str]:
    """Divide um parágrafo que sozinho excede o teto, respeitando fim de frase."""
    pieces: list[str] = []
    current = ""
    for sentence in _SENTENCE_END.split(text):
        candidate = f"{current} {sentence}".strip()
        if current and count_tokens(candidate) > max_tokens:
            pieces.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        pieces.append(current)
    return pieces


def split_table(markdown: str, preamble: str, max_tokens: int) -> list[str]:
    """Divide uma tabela Markdown por linhas, repetindo título e cabeçalho em cada parte."""
    full = f"{preamble}\n\n{markdown}".strip() if preamble else markdown
    if count_tokens(full) <= max_tokens:
        return [full]

    lines = markdown.splitlines()
    header_size = 2 if len(lines) > 1 and _TABLE_SEPARATOR.match(lines[1]) else 1
    header, rows = lines[:header_size], lines[header_size:]
    head = "\n".join(header)
    budget = max_tokens - count_tokens(f"{preamble}\n\n{head}") - 12

    groups: list[list[str]] = [[]]
    used = 0
    for row in rows:
        tokens = count_tokens(row)
        if groups[-1] and used + tokens > budget:
            groups.append([])
            used = 0
        groups[-1].append(row)
        used += tokens

    parts = []
    for i, group in enumerate(groups, start=1):
        label = f"{preamble}\n(parte {i} de {len(groups)})".strip()
        parts.append(f"{label}\n\n{head}\n" + "\n".join(group))
    return parts


def build_context_header(company: str | None, period: str | None, unit: str | None,
                         section_path: list[str], page_start: int, page_end: int,
                         missing: tuple[str, str] = ("Empresa não identificada", "período não identificado")) -> str:
    """Prefixo determinístico embutido nos dois embeddings; não entra no texto citável. `company`
    e `period` são a entidade e o recorte do domínio; `missing`, o que dizer sem eles."""
    pages = f"p. {page_start}" if page_start == page_end else f"p. {page_start}-{page_end}"
    parts = [company or missing[0], period or missing[1]]
    if unit:
        parts.append(unit)
    if section_path:
        parts.append(" > ".join(section_path))
    parts.append(pages)
    return "[" + " | ".join(parts) + "]"


class _Builder:
    def __init__(self, parsed: ParsedDocument, figures: dict[str, FigureResult], settings: Settings,
                 plugin: PluginIndexador):
        self.parsed = parsed
        self.figures = figures
        self.settings = settings
        self.plugin = plugin
        self.units_by_page = page_units(parsed)
        self.scope_by_page = scope_units(parsed)
        self.chunks: list[Chunk] = []
        self.sections: list[str] = []  # caminho completo da seção atual
        self.numbered: list[str] = []  # só a cadeia numerada ("22." > "22.1")
        self.numbered_page: int | None = None  # última página em que a cadeia numerada apareceu
        self.last_note: int | None = None  # número da última nota de primeiro nível vista
        self.seen: dict[str, str] = {}  # número da nota → título, para achar o pai pelo número
        self.fresh = False  # o último item foi um título sem número e nada veio depois dele
        self.items: list[ParsedItem] = []  # itens na ordem de leitura, para olhar adiante
        self.position = 0  # índice do item em processamento
        self.buffer: list[tuple[str, int, int, int]] = []  # (parágrafo, página, tokens, página final)
        self.toc_pages: set[int] = set()  # páginas em que um índice foi descartado

    def without_toc_leftovers(self) -> list[Chunk]:
        """Os chunks, sem o texto curto que sobra na página do índice. Quando o índice vem como
        tabela, o que está em volta dele ("Índice", o nome da empresa, uma linha que ficou fora
        da grade) vem como texto separado e viraria um chunk sem conteúdo."""
        kept = [chunk for chunk in self.chunks
                if not (chunk.chunk_type == "text" and chunk.page_start == chunk.page_end
                        and chunk.page_start in self.toc_pages
                        and chunk.token_count <= _TOC_LEFTOVER_MAX_TOKENS)]
        for index, chunk in enumerate(kept):
            chunk.chunk_index = index
        return kept

    # -- emissão ------------------------------------------------------------

    def emit(self, chunk_type: ChunkType, method: str, text: str, page_start: int, page_end: int,
             result: FigureResult | None = None) -> None:
        text = text.strip()
        tokens = count_tokens(text) if text else 0
        # Resto de layout (uma letra solta de um sumário, por exemplo) não vira chunk.
        if not text or (chunk_type == "text" and tokens < _MIN_TEXT_TOKENS):
            return
        # O índice repete o nome de todas as seções e casaria com qualquer busca por elas.
        if chunk_type != "figure" and is_table_of_contents(text):
            self.toc_pages.update(range(page_start, page_end + 1))
            return
        if chunk_type == "figure":
            # A unidade de um gráfico é a que está escrita nele (GWh, %, R$ milhões). A unidade
            # monetária da página ou do documento não se aplica a um gráfico de perdas em GWh.
            unit = detect_declared_unit(text)
            unit_source = "chunk" if unit else None
        else:
            unit, unit_source = resolve_unit(text, page_start, self.units_by_page, self.scope_by_page)
        self.chunks.append(Chunk(
            chunk_index=len(self.chunks),
            chunk_type=chunk_type,
            extraction_method=method,
            text=text,
            section_path=list(self.sections),
            page_start=page_start,
            page_end=page_end,
            unit=unit,
            unit_source=unit_source,
            token_count=tokens,
            verified_ratio=result.verified_ratio if result else None,
            unverified_values=list(result.unverified_values) if result else [],
            verified_by_ocr=result.verified_by_ocr if result else None,
            verified_labels_ratio=result.verified_labels_ratio if result else None,
            suspicious=bool(_INJECTION.search(text)),
        ))

    def flush(self, keep_overlap: bool = False) -> None:
        if not self.buffer:
            return
        text = "\n\n".join(paragraph for paragraph, _, _, _ in self.buffer)
        self.emit("text", "native_text", text, min(start for _, start, _, _ in self.buffer),
                  max(end for _, _, _, end in self.buffer))
        last = self.buffer[-1]
        self.buffer = [last] if keep_overlap and last[2] <= _OVERLAP_MAX_TOKENS else []

    # -- itens --------------------------------------------------------------

    def add_heading(self, text: str, page: int) -> None:
        """Atualiza o caminho de seções. A hierarquia vem só da numeração das notas, que é
        explícita; o nível de título inferido pelo parser a partir do estilo não é confiável
        (colocava a DRE "dentro" do relatório do auditor).

        - Numerado ("22.1 ..."): os ancestrais são as notas já vistas cujo número é prefixo
          do dele ("22"). O pai de "3.4" é a nota "3", nunca a "3.3" que veio antes; se a
          nota "3" não foi vista, "3.4" fica sem pai.
        - Letra ("a) ..."): filho da cadeia numerada atual.
        - Sem numeração: filho da cadeia numerada se ainda estamos dentro das notas (a cadeia
          apareceu nesta página, ou mais adiante há um título que continua a nota atual ou
          abre a seguinte); senão abre uma parte nova do documento e zera a cadeia.
        - Sem numeração logo depois de outro sem numeração, sem nada entre os dois: é o mesmo
          título quebrado em linhas ("LIGHT S.A." / "DEMONSTRAÇÕES DE RESULTADOS" / "PARA OS
          EXERCÍCIOS FINDOS..."), e as linhas são juntadas.
        """
        title = _CONTINUATION.sub("", text).strip()
        if len(title) < 2:
            return
        number = note_number(title)

        if number is not None:
            self.fresh = False
            parts = number.split(".")
            top = int(parts[0])
            # "6. Contas a receber--Continuação" no topo da página seguinte não abre seção nova
            # nem desfaz a subseção em que o texto está.
            if any(_same_title(known, title) for known in self.numbered):
                self.numbered_page = page
                self.last_note = top
                return
            self.flush()
            if len(parts) == 1 and self.last_note is not None and top < self.last_note:
                self.seen.clear()  # a numeração recomeçou: é outra sequência de notas
            self.last_note = top
            self.seen[number] = title
            ancestors = [self.seen[prefix] for k in range(1, len(parts))
                         if (prefix := ".".join(parts[:k])) in self.seen]
            self.numbered = [*ancestors, title]
            self.numbered_page = page
            self.sections = list(self.numbered)
            return

        if self.sections and _same_title(self.sections[-1], title):
            if self.numbered:
                self.numbered_page = page
            return
        lettered = bool(_LETTERED.match(title))
        if (self.fresh and not lettered
                and len(self.sections[-1]) + len(title) + 3 <= _MERGED_TITLE_MAX_CHARS):
            self.sections[-1] = f"{self.sections[-1]} · {title}"
            return
        self.flush()
        if lettered and self.numbered:
            self.numbered_page = page
            self.sections = [*self.numbered, title]
            self.fresh = False
        elif self.numbered and (self.numbered_page == page or self._inside_notes()):
            self.sections = [*self.numbered, title]
            self.fresh = True
        else:
            self.numbered = []
            self.numbered_page = None
            self.sections = [title]
            self.fresh = True

    def _inside_notes(self) -> bool:
        """Há, mais adiante, um título numerado que continua a nota atual ou abre a seguinte?

        Se há, o subtítulo sem número que está sendo lido ainda pertence às notas, mesmo numa
        página em que o título da nota não aparece. Se a numeração acaba ou recomeça, ele abre
        outra parte do documento (o parecer do auditor depois das notas, por exemplo)."""
        if self.last_note is None:
            return False
        for item in self.items[self.position + 1:]:
            if item.type == "heading":
                number = note_number(_CONTINUATION.sub("", item.text).strip())
            elif item.type == "text":
                number = _looks_like_note_title(item.text)
            else:
                continue
            if number is None:
                continue
            if int(number.split(".")[0]) in (self.last_note, self.last_note + 1):
                return True
            if item.type == "heading":
                return False
        return False

    def is_note_heading(self, text: str) -> bool:
        """Parágrafo que na verdade é título de nota.

        O rótulo de título vem do modelo de layout, que olha a página renderizada e varia com
        o ambiente: com outra fonte, "3.1 Políticas contábeis" sai como item de lista. A
        numeração está no texto e não varia, então uma linha curta, numerada, iniciada por
        maiúscula e sem pontuação final é tratada como título — desde que continue a sequência
        das notas. Essa última condição evita que uma enumeração qualquer ("1. Identificar o
        contrato", "2. ...") seja confundida com a nota 1 ou 2.
        """
        line = text.strip()
        if not line or "\n" in line or len(line) > _HEADING_MAX_CHARS or line[-1] in ".;:,":
            return False
        numbered = _NUMBERED.match(line)
        if numbered:
            number, dot, _space, first = numbered.groups()
            if note_number(line) is None or not first.isupper():
                return False
            parts = number.split(".")
            note = int(parts[0])
            if len(parts) > 1:  # "3.1 ...": subitem da nota atual
                return note == self.last_note
            if not dot:  # "5 Anos" não é a nota 5
                return False
            if self.last_note is None:
                return note == 1
            if note == self.last_note:  # só como continuação da nota aberta ("3. ...--Continuação")
                return bool(self.numbered) and _same_title(_CONTINUATION.sub("", line), self.numbered[0])
            return note == self.last_note + 1
        lettered = _LETTERED_TITLE.match(line)
        return bool(lettered and self.numbered)

    def add_text(self, text: str, page: int, page_end: int | None = None) -> None:
        """`page_end` é a última página de um parágrafo que continua na página seguinte."""
        end = max(page, page_end or page)
        text = text.strip()
        if len(text) < 2:  # letra solta de um logotipo ou de um sumário
            return
        if self.plugin.rodape_conhecido(text):  # rodapé fixo deste tipo de documento: não é conteúdo
            return
        if self.is_note_heading(text):
            self.add_heading(text, page)
            return
        if self._follows_table(page):
            # "(a) Não auditado pelos auditores independentes" logo abaixo da tabela explica uma
            # marca que está dentro dela. O parser costuma entregar a nota duas vezes: dentro do
            # texto da tabela e de novo como parágrafo. A cópia é descartada; se a nota não
            # veio na tabela, é anexada a ela, em vez de virar um chunk solto de dez tokens.
            table = self.chunks[-1]
            if " ".join(text.split()) in " ".join(table.text.split()):
                return
            if _FOOTNOTE.match(text) and count_tokens(text) <= _FOOTNOTE_MAX_TOKENS:
                table.text = f"{table.text}\n\n{text}"
                table.token_count = count_tokens(table.text)
                table.page_end = max(table.page_end, end)
                return
        if (len(text) <= _FIXED_TITLE_MAX_CHARS and "\n" not in text and text[-1] not in ".;:,"
                and self.plugin.titulo_de_secao(text)):
            # O nome fixo de uma seção rotulado como texto comum ("Balanços patrimoniais Em 31 de
            # dezembro de 2022 e 2021"): abre a seção e continua no texto, como título da tabela.
            self.add_heading(text, page)
        self.fresh = False
        tokens = count_tokens(text)
        if tokens > self.settings.chunk_max_tokens:
            self.flush()
            for piece in split_long_text(text, self.settings.chunk_max_tokens):
                self.emit("text", "native_text", piece, page, end)
            return
        buffered = sum(t for _, _, t, _ in self.buffer)
        if self.buffer and buffered + tokens > self.settings.chunk_target_tokens:
            self.flush(keep_overlap=True)
        self.buffer.append((text, page, tokens, end))

    def _follows_table(self, page: int) -> bool:
        """O texto vem logo depois de uma tabela, na mesma página e seção, sem nada entre os dois?"""
        return bool(
            not self.buffer and self.chunks and self.chunks[-1].chunk_type == "table"
            and self.chunks[-1].page_end == page and self.chunks[-1].section_path == self.sections
        )

    def take_preamble(self, page: int) -> str:
        """Retira do buffer as linhas curtas imediatamente anteriores à tabela, na mesma
        página: são o título dela ("Demonstrações dos resultados", "(em milhares de reais)")."""
        lines: list[str] = []
        used = 0
        while (self.buffer and len(lines) < _PREAMBLE_MAX_LINES
               and self.buffer[-1][1] == page and self.buffer[-1][2] <= _PREAMBLE_MAX_TOKENS
               and used + self.buffer[-1][2] <= _PREAMBLE_BUDGET):
            text, _, tokens, _ = self.buffer.pop()
            lines.insert(0, text)
            used += tokens
        return "\n".join(lines)

    def add_table(self, markdown: str, page: int) -> None:
        if not markdown.strip():
            return
        self.fresh = False
        preamble = self.take_preamble(page)
        self.flush()
        for part in split_table(markdown, preamble, self.settings.table_max_tokens):
            self.emit("table", "table_model", part, page, page)

    def add_figure(self, image_path: str | None, caption: str, page: int) -> None:
        result = self.figures.get(image_path or "")
        if result is None:
            return
        self.fresh = False
        self.flush()
        text = f"{result.text}\nLegenda: {caption.strip()}" if caption.strip() else result.text
        self.emit("figure", "vision_llm", text, page, page, result)


def chunk_document(parsed: ParsedDocument, figures: dict[str, FigureResult], settings: Settings,
                   plugin: PluginIndexador | None = None) -> list[Chunk]:
    """Os trechos do documento. `plugin`: o do domínio dele (sem ele, só as regras do núcleo)."""
    builder = _Builder(parsed, figures, settings, plugin or PluginGenerico())
    builder.items = drop_running_headers(parsed.items, parsed.page_count)
    for position, item in enumerate(builder.items):
        builder.position = position
        if item.type == "heading":
            builder.add_heading(item.text, item.page)
        elif item.type == "text":
            builder.add_text(item.text, item.page, item.page_end)
        elif item.type == "table":
            builder.add_table(item.text, item.page)
        elif item.type == "figure":
            builder.add_figure(item.image_path, item.text, item.page)
    builder.flush()
    return builder.without_toc_leftovers()
