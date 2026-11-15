"""Leitura de uma célula numa tabela em Markdown, como o indexador as grava.

É o centro do "o agente aponta, o código lê": o agente diz o rótulo da linha e o cabeçalho da
coluna; o servidor acha a célula e devolve o que está escrito nela. O modelo nunca digita o
número. Rótulo que não existe ou que aparece mais de uma vez é erro, com as opções, e nunca
"a primeira que casar".
"""
import difflib
import re
import unicodedata
from dataclasses import dataclass

from app.governanca.erros import ToolRejected

_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{2,}")
_CELL_BREAK = re.compile(r"(?<!\\)\|")
_SCOPES = {"consolidado": "consolidado", "controladora": "controladora"}
_MAX_OPTIONS = 12


@dataclass(frozen=True)
class Table:
    header: list[str]
    rows: list[list[str]]


@dataclass(frozen=True)
class Cell:
    text: str
    row_label: str
    column_label: str
    row_text: str  # a linha inteira, em Markdown: é o trecho citado
    header_text: str
    row_index: int = -1
    column_index: int = -1


def _cells(line: str) -> list[str]:
    inner = line.strip()
    inner = inner.removeprefix("|")
    inner = inner[:-1] if inner.endswith("|") and not inner.endswith("\\|") else inner
    return [" ".join(cell.replace("\\|", "|").split()) for cell in _CELL_BREAK.split(inner)]


def parse_table(text: str) -> Table | None:
    """A tabela de um chunk. As linhas antes dela (título, período, unidade) e depois dela
    (notas de rodapé) não fazem parte. None se o chunk não tem tabela."""
    lines = text.splitlines()
    for index, line in enumerate(lines[:-1]):
        if line.lstrip().startswith("|") and _SEPARATOR.match(lines[index + 1]):
            header = _cells(line)
            rows = []
            for row in lines[index + 2:]:
                if not row.lstrip().startswith("|"):
                    break
                cells = _cells(row)
                rows.append(cells + [""] * (len(header) - len(cells)))
            return Table(header=header, rows=rows)
    return None


def normalize(label: str) -> str:
    """Rótulo para comparação: sem diferença de caixa, de acento, de espaços ou de pontuação nas
    pontas. O PDF escreve "Caixa liquido" e o modelo copia "Caixa líquido": é a mesma linha."""
    folded = " ".join(label.split()).strip(" .:;").casefold()
    return "".join(ch for ch in unicodedata.normalize("NFD", folded) if not unicodedata.combining(ch))


def closest(wanted: str, labels: list[str]) -> list[str]:
    """Os rótulos mais parecidos com o pedido, para o erro ajudar em vez de despejar a tabela."""
    unique = [label for label in dict.fromkeys(labels) if label]
    by_key = {normalize(label): label for label in unique}
    near = difflib.get_close_matches(normalize(wanted), list(by_key), n=_MAX_OPTIONS, cutoff=0.35)
    words = set(normalize(wanted).split())
    sharing = [label for label in unique if words & set(normalize(label).split()) and normalize(label) not in near]
    return ([by_key[key] for key in near] + sharing)[:_MAX_OPTIONS] or unique[:_MAX_OPTIONS]


def scope_of(column_label: str) -> str | None:
    """Controladora ou consolidado, pelo cabeçalho da coluna."""
    folded = column_label.casefold()
    return next((scope for word, scope in _SCOPES.items() if word in folded), None)


def _pick(kind: str, wanted: str, labels: list[str], occurrence: int | None) -> int:
    """Índice do rótulo pedido. Sem casar, ou casando mais de uma vez sem dizer qual: erro."""
    target = normalize(wanted)
    if not target:
        raise ToolRejected(f"O rótulo da {kind} está vazio. Uma {kind} sem rótulo não identifica um valor: aponte a "
                           f"{kind} que traz o nome do que você quer.")
    matches = [index for index, label in enumerate(labels) if normalize(label) == target]
    if not matches:
        raise ToolRejected(f"A tabela não tem {kind} com o rótulo '{wanted}'. Copie o rótulo como está "
                           "escrito no chunk; em 'opcoes' estão os mais parecidos.", opcoes=closest(wanted, labels))
    if len(matches) > 1 and occurrence is None:
        raise ToolRejected(
            f"O rótulo '{wanted}' aparece {len(matches)} vezes como {kind}. Informe qual delas "
            f"(1 = a primeira) em ocorrencia_{'linha' if kind == 'linha' else 'coluna'}.")
    if occurrence is not None:
        if not 1 <= occurrence <= len(matches):
            raise ToolRejected(f"O rótulo '{wanted}' aparece {len(matches)} vez(es) como {kind}; não existe a "
                               f"ocorrência {occurrence}.")
        return matches[occurrence - 1]
    return matches[0]


def read_cell(text: str, row_label: str, column_label: str, *, row_occurrence: int | None = None,
              column_occurrence: int | None = None) -> Cell:
    """A célula da linha e da coluna pedidas, lida do Markdown do chunk."""
    table = parse_table(text)
    if table is None:
        raise ToolRejected("Este chunk não tem tabela. Para valor escrito em texto corrido, use origem='texto'.")
    row = _pick("linha", row_label, [cells[0] for cells in table.rows], row_occurrence)
    column = _pick("coluna", column_label, table.header, column_occurrence)
    if column == 0:
        raise ToolRejected("A coluna pedida é a dos rótulos das linhas, não uma coluna de valores.")
    cells = table.rows[row]
    return Cell(text=cells[column], row_label=cells[0], column_label=table.header[column],
                row_text="| " + " | ".join(cells) + " |", header_text="| " + " | ".join(table.header) + " |",
                row_index=row, column_index=column)


def sibling_column(header: list[str], column: int, year: str, other_year: str) -> int | None:
    """A coluna irmã de outro ano: mesmo cabeçalho, com o ano trocado ("Consolidado - 2022" ->
    "Consolidado - 2021", "Consolidado - 31/12/2022" -> "Consolidado - 31/12/2021"). O cabeçalho
    do outro ano pode trazer uma marca a mais no fim ("- (não auditado)"). None se não houver
    exatamente uma candidata."""
    label = normalize(header[column])
    if year not in label:
        return None
    wanted = label.replace(year, other_year)
    matches = [index for index, other in enumerate(header)
               if index not in (0, column) and (normalize(other) == wanted or normalize(other).startswith(wanted + " "))]
    return matches[0] if len(matches) == 1 else None
