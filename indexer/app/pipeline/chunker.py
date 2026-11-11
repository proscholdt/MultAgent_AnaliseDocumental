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
from app.pipeline.figures import FigureResult
from app.pipeline.metadata import page_units, resolve_unit
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
_NUMBERED = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,3})\.?\s+\S")
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
    token_count: int = 0
    verified_ratio: float | None = None
    unverified_values: list[str] = field(default_factory=list)
    suspicious: bool = False


def numbering_depth(title: str) -> int | None:
    """Profundidade pela numeração: "22." → 1, "22.1" → 2, "22.1.4" → 3. None se não numerado."""
    numbered = _NUMBERED.match(title)
    return numbered.group(1).count(".") + 1 if numbered else None


def _running_key(text: str) -> str:
    return re.sub(r"\s+", " ", _ANY_CONTINUATION.sub("", text)).strip().lower()


def drop_running_headers(items: list[ParsedItem], page_count: int) -> list[ParsedItem]:
    """Remove cabeçalhos e rodapés corridos que o parser não rotulou como tal.

    Ex.: o nome da empresa e "Notas explicativas às demonstrações financeiras" no topo de
    cada página. Se ficassem, virariam "seção" de tudo e gerariam dezenas de chunks iguais.
    Critério: linha curta que se repete em muitas páginas e quase sempre na borda da página.
    Tabelas e figuras nunca são removidas.
    """
    by_page: dict[int, list[ParsedItem]] = {}
    for item in items:
        by_page.setdefault(item.page, []).append(item)

    pages_seen: dict[str, set[int]] = {}
    at_edge: dict[str, int] = {}
    occurrences: dict[str, int] = {}
    for page, page_items in by_page.items():
        for position, item in enumerate(page_items):
            if item.type not in ("heading", "text") or count_tokens(item.text) > _RUNNING_MAX_TOKENS:
                continue
            key = _running_key(item.text)
            # Título numerado que se repete ("3. Sumário...--Continuação" em dez páginas) é
            # continuação de nota, não cabeçalho corrido: é ele que mantém a seção certa.
            if not key or _NUMBERED.match(item.text) or _LETTERED.match(item.text):
                continue
            pages_seen.setdefault(key, set()).add(page)
            occurrences[key] = occurrences.get(key, 0) + 1
            edge = position < _RUNNING_TOP_ITEMS or position >= len(page_items) - _RUNNING_BOTTOM_ITEMS
            at_edge[key] = at_edge.get(key, 0) + int(edge)

    min_pages = max(_RUNNING_MIN_PAGES, math.ceil(page_count * _RUNNING_MIN_PAGE_SHARE))
    running = {
        key for key, pages in pages_seen.items()
        if len(pages) >= min_pages and at_edge[key] / occurrences[key] >= _RUNNING_EDGE_SHARE
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
                         section_path: list[str], page_start: int, page_end: int) -> str:
    """Prefixo determinístico embutido nos dois embeddings; não entra no texto citável."""
    pages = f"p. {page_start}" if page_start == page_end else f"p. {page_start}-{page_end}"
    parts = [company or "Empresa não identificada", period or "período não identificado"]
    if unit:
        parts.append(unit)
    if section_path:
        parts.append(" > ".join(section_path))
    parts.append(pages)
    return "[" + " | ".join(parts) + "]"


class _Builder:
    def __init__(self, parsed: ParsedDocument, figures: dict[str, FigureResult], settings: Settings):
        self.parsed = parsed
        self.figures = figures
        self.settings = settings
        self.units_by_page = page_units(parsed)
        self.chunks: list[Chunk] = []
        self.sections: list[str] = []  # caminho completo da seção atual
        self.numbered: list[str] = []  # só a cadeia numerada ("22." > "22.1")
        self.numbered_page: int | None = None  # última página em que a cadeia numerada apareceu
        self.last_note: int | None = None  # número da última nota de primeiro nível vista
        self.buffer: list[tuple[str, int, int]] = []  # (parágrafo, página, tokens)

    # -- emissão ------------------------------------------------------------

    def emit(self, chunk_type: ChunkType, method: str, text: str, page_start: int, page_end: int,
             result: FigureResult | None = None) -> None:
        text = text.strip()
        tokens = count_tokens(text) if text else 0
        # Resto de layout (uma letra solta de um sumário, por exemplo) não vira chunk.
        if not text or (chunk_type == "text" and tokens < _MIN_TEXT_TOKENS):
            return
        self.chunks.append(Chunk(
            chunk_index=len(self.chunks),
            chunk_type=chunk_type,
            extraction_method=method,
            text=text,
            section_path=list(self.sections),
            page_start=page_start,
            page_end=page_end,
            unit=resolve_unit(text, page_start, self.units_by_page),
            token_count=tokens,
            verified_ratio=result.verified_ratio if result else None,
            unverified_values=list(result.unverified_values) if result else [],
            suspicious=bool(_INJECTION.search(text)),
        ))

    def flush(self, keep_overlap: bool = False) -> None:
        if not self.buffer:
            return
        text = "\n\n".join(paragraph for paragraph, _, _ in self.buffer)
        pages = [page for _, page, _ in self.buffer]
        self.emit("text", "native_text", text, min(pages), max(pages))
        last = self.buffer[-1]
        self.buffer = [last] if keep_overlap and last[2] <= _OVERLAP_MAX_TOKENS else []

    # -- itens --------------------------------------------------------------

    def add_heading(self, text: str, page: int) -> None:
        """Atualiza o caminho de seções. A hierarquia vem só da numeração das notas, que é
        explícita; o nível de título inferido pelo parser a partir do estilo não é confiável
        (colocava a DRE "dentro" do relatório do auditor).

        - Numerado ("22.1 ..."): entra na cadeia numerada pela profundidade.
        - Letra ("a) ..."): filho da cadeia numerada atual.
        - Sem numeração: filho da cadeia numerada se ela apareceu nesta mesma página
          (subtítulo de nota); senão abre uma parte nova do documento e zera a cadeia.
          Na dúvida prefere-se perder o ancestral a exibir um ancestral errado.
        """
        title = _CONTINUATION.sub("", text).strip()
        if not title:
            return
        depth = numbering_depth(title)

        if depth is not None:
            self.last_note = int(_NUMBERED.match(title).group(1).split(".")[0])
            # "6. Contas a receber--Continuação" no topo da página seguinte não abre seção nova.
            if len(self.numbered) >= depth and self.numbered[depth - 1] == title:
                self.numbered_page = page
                return
            self.flush()
            self.numbered = self.numbered[: depth - 1] + [title]
            self.numbered_page = page
            self.sections = list(self.numbered)
            return

        if self.sections and self.sections[-1] == title:
            if self.numbered:
                self.numbered_page = page
            return
        self.flush()
        if _LETTERED.match(title) and self.numbered:
            self.numbered_page = page
            self.sections = [*self.numbered, title]
        elif self.numbered and self.numbered_page == page:
            self.sections = [*self.numbered, title]
        else:
            self.numbered = []
            self.numbered_page = None
            self.sections = [title]

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
            if not numbered.group(0)[-1].isupper():
                return False
            parts = numbered.group(1).split(".")
            note = int(parts[0])
            if len(parts) > 1:  # "3.1 ...": subitem da nota atual
                return note == self.last_note
            if line[len(numbered.group(1))] != ".":  # "5 anos" não é a nota 5
                return False
            if self.last_note is None:
                return note == 1
            if note == self.last_note:  # só como continuação da nota aberta ("3. ...--Continuação")
                return bool(self.numbered) and _CONTINUATION.sub("", line).strip() == self.numbered[0]
            return note == self.last_note + 1
        lettered = _LETTERED_TITLE.match(line)
        return bool(lettered and self.numbered)

    def add_text(self, text: str, page: int) -> None:
        text = text.strip()
        if not text:
            return
        if self.is_note_heading(text):
            self.add_heading(text, page)
            return
        tokens = count_tokens(text)
        if tokens > self.settings.chunk_max_tokens:
            self.flush()
            for piece in split_long_text(text, self.settings.chunk_max_tokens):
                self.emit("text", "native_text", piece, page, page)
            return
        buffered = sum(t for _, _, t in self.buffer)
        if self.buffer and buffered + tokens > self.settings.chunk_target_tokens:
            self.flush(keep_overlap=True)
        self.buffer.append((text, page, tokens))

    def take_preamble(self, page: int) -> str:
        """Retira do buffer as linhas curtas imediatamente anteriores à tabela, na mesma
        página: são o título dela ("Demonstrações dos resultados", "(em milhares de reais)")."""
        lines: list[str] = []
        used = 0
        while (self.buffer and len(lines) < _PREAMBLE_MAX_LINES
               and self.buffer[-1][1] == page and self.buffer[-1][2] <= _PREAMBLE_MAX_TOKENS
               and used + self.buffer[-1][2] <= _PREAMBLE_BUDGET):
            text, _, tokens = self.buffer.pop()
            lines.insert(0, text)
            used += tokens
        return "\n".join(lines)

    def add_table(self, markdown: str, page: int) -> None:
        if not markdown.strip():
            return
        preamble = self.take_preamble(page)
        self.flush()
        for part in split_table(markdown, preamble, self.settings.table_max_tokens):
            self.emit("table", "table_model", part, page, page)

    def add_figure(self, image_path: str | None, caption: str, page: int) -> None:
        result = self.figures.get(image_path or "")
        if result is None:
            return
        self.flush()
        text = f"{result.text}\nLegenda: {caption.strip()}" if caption.strip() else result.text
        self.emit("figure", "vision_llm", text, page, page, result)


def chunk_document(parsed: ParsedDocument, figures: dict[str, FigureResult], settings: Settings) -> list[Chunk]:
    builder = _Builder(parsed, figures, settings)
    for item in drop_running_headers(parsed.items, parsed.page_count):
        if item.type == "heading":
            builder.add_heading(item.text, item.page)
        elif item.type == "text":
            builder.add_text(item.text, item.page)
        elif item.type == "table":
            builder.add_table(item.text, item.page)
        elif item.type == "figure":
            builder.add_figure(item.image_path, item.text, item.page)
    builder.flush()
    return builder.chunks
