"""Candidatos a inconsistência no texto da fonte, achados por regra.

O validador recebe a lista, lê cada trecho e decide o que é erro do documento: a regra acha, o
modelo julga. Regra que acha demais custa algumas leituras; regra que não acha deixa o erro para
o olho do modelo, como antes. No núcleo, as regras que valem para qualquer documento:

- **ano**: comparação de um ano com ele mesmo ("encerrou 2021 em 3,32x, menor do que no 2021");
- **quadro**: a data do texto que apresenta o quadro não está nas colunas dele; duas colunas com
  o mesmo título e a mesma data.

O plugin do domínio acrescenta as suas (no financeiro: a conta em reais de uma frase, a variação
contra a extração, a projeção para um ano anterior ao do documento), na ordem que quiser.
"""
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from app.regras.campos import atual
from app.regras.tabelas import parse_table
from app.regras.texto import squash

MAX_CANDIDATES = 40
_SENTENCE_MAX = 600
# Até onde, depois da palavra de comparação, procurar o ano comparado.
_NEAR = 25

_YEAR = re.compile(r"(?<![\d/.,])(20\d{2})(?![\d.,]\d)")
_ANY_YEAR = re.compile(r"(?<!\d)(20\d{2})(?!\d)")  # também dentro de data: "31/12/2021"
_DATE_YEAR = re.compile(r"\b\d{1,2}\s+de\s+[a-zç]+\s+de\s+(20\d{2})\b|\b\d{1,2}/\d{1,2}/(20\d{2})\b", re.IGNORECASE)
# Palavras de comparação: "em relação a", "frente a", "menor do que".
COMPARISON = re.compile(r"em\s+rela[çc][ãa]o|comparad|compara[çc][ãa]o|frente\s+a|contra\b|\bante\b|versus|\bvs\b"
                         r"|menor\s+(?:do\s+)?que|maior\s+(?:do\s+)?que|inferior\s+a|superior\s+a", re.IGNORECASE)
_SENTENCE_END = re.compile(r"(?<=[.;!?])\s+(?=[A-ZÁÉÍÓÚÂÊÔÃÕÇ(\"“])")


@dataclass(frozen=True)
class Candidate:
    chunk_index: int
    pagina: int
    regra: str
    motivo: str
    trecho: str  # copiado do chunk (espaços normalizados): serve de citação
    # Nos quadros: os mesmos números em outro quadro do documento, com a coluna (e o ano) de lá.
    prova: str | None = None
    # A prova contradiz o ano que o texto dá ao quadro, com dois valores ou mais: não é suspeita, é
    # fato do documento. Na Millet (p. 41), com a prova na mão, o modelo descartou dizendo que "2022"
    # era o ano da projeção; o descarte de um candidato assim é recusado (descartar_candidato).
    prova_forte: bool = False

    def view(self) -> dict:
        out = {"chunk_index": self.chunk_index, "pagina": self.pagina, "regra": self.regra,
               "motivo": self.motivo, "trecho": self.trecho}
        if self.prova:
            out["prova"] = self.prova
        if self.prova_forte:
            out["prova_forte"] = ("Pelo menos dois valores deste quadro aparecem em outro quadro na coluna de outro "
                                  "ano que não o do texto: registre com registrar_alerta. Não pode ser descartado.")
        return out


def year_of(period: str | None) -> int | None:
    """O ano do período do documento: "2022" → 2022; "3T25" → 2025."""
    if not period:
        return None
    match = re.search(r"(20\d{2})", period) or re.search(r"[1-4]\s*T\s*(\d{2})\b", period, re.IGNORECASE)
    if not match:
        return None
    year = int(match.group(1))
    return year if year > 100 else 2000 + year


def sentences(text: str) -> list[str]:
    """As frases do texto (sem as linhas de tabela), com os espaços normalizados."""
    prose = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("|"))
    out = []
    for paragraph in re.split(r"\n\s*\n", prose):
        out += [squash(part) for part in _SENTENCE_END.split(squash(paragraph)) if part.strip()]
    return out


def _same_year_comparison(sentence: str) -> str | None:
    """Comparação entre dois momentos com o mesmo ano dos dois lados ("encerrou 2021 em 3,32x,
    menor do que no 2021"). O ano de depois tem de vir logo depois da comparação: em "em relação
    a RTE e em 13 de dezembro de 2022", a comparação não é entre anos."""
    for comparison in COMPARISON.finditer(sentence):
        before = [m.group(1) for m in _YEAR.finditer(sentence[:comparison.start()])]
        after = _YEAR.search(sentence[comparison.end():comparison.end() + _NEAR])
        if before and after and set(before) == {after.group(1)}:
            return f"A frase compara {before[-1]} com {after.group(1)}: o mesmo ano dos dois lados da comparação."
    return None


def _table_rows(text: str) -> tuple[list[str], str, list[str]]:
    """As linhas de texto antes do quadro, a linha do cabeçalho e as células dele."""
    lines = text.splitlines()
    first = next((i for i, line in enumerate(lines) if line.lstrip().startswith("|")), None)
    if first is None:
        return [], "", []
    header = [squash(cell) for cell in lines[first].strip().strip("|").split("|")]
    return [line for line in lines[:first] if line.strip()], lines[first], header


def _table_dates(text: str) -> list[tuple[str, str | None]]:
    """Os problemas de data no cabeçalho de um quadro: (motivo, o ano que o texto dá ao quadro, se
    o problema é esse)."""
    preamble, _, header = _table_rows(text)
    if not header:
        return []
    problems = []
    header_years = {m.group(1) for cell in header for m in _ANY_YEAR.finditer(cell)}
    # Só a linha logo acima do quadro, a que o apresenta ("... em 31 de dezembro de 2021:"), e só
    # quando ela fala de uma data só. "Nos exercícios findos em 31 de dezembro de 2022 e de 2021"
    # costuma vir antes de uma movimentação que começa no saldo de um ano antes.
    lead = preamble[-1] if preamble else ""
    stated = {m.group(1) for m in _ANY_YEAR.finditer(lead)} if _DATE_YEAR.search(lead) else set()
    if len(stated) == 1 and header_years and not stated & header_years:
        reason = (f"O texto apresenta o quadro em {', '.join(sorted(stated))}, e as colunas são de "
                  f"{', '.join(sorted(header_years))}.")
        problems.append((reason, next(iter(stated))))
    seen: dict[str, int] = {}
    for cell in header:
        if cell and _ANY_YEAR.search(cell):
            seen[cell] = seen.get(cell, 0) + 1
    # Exatamente duas: célula mesclada sobre três ou mais colunas o parser repete em todas.
    repeated = [cell for cell, count in seen.items() if count == 2]
    if repeated:
        problems.append((f"Duas colunas do quadro com o mesmo título: \"{repeated[0]}\".", None))
    return problems


def _quote_for_table(text: str) -> str:
    """A citação de um problema de quadro: a frase que o apresenta e o cabeçalho."""
    preamble, header_line, _ = _table_rows(text)
    return squash(" ".join([*preamble[-1:], header_line]))[:_SENTENCE_MAX]


# Um número de quadro que identifica um valor: com separador de milhar ("24.176", "(5.672.203)").
# Os pequenos ("355", "2") se repetem por acaso entre quadros e não provam nada.
_BIG_NUMBER = re.compile(r"\(?-?(\d{1,3}(?:\.\d{3})+)\)?")
_PROOF_MAX = 2


def _numbers_by_column(chunks: list[dict]) -> dict[str, list[tuple[int, int, str]]]:
    """Em todos os quadros do documento: número → [(chunk_index, página, título da coluna com ano)]."""
    index: dict[str, list[tuple[int, int, str]]] = {}
    for chunk in chunks:
        table = parse_table(chunk.get("text") or "")
        if table is None:
            continue
        for row in table.rows:
            for column, cell in enumerate(row[:len(table.header)]):
                title = table.header[column]
                match = _BIG_NUMBER.fullmatch(cell.strip())
                if match and _ANY_YEAR.search(title):
                    index.setdefault(match.group(1), []).append((chunk["chunk_index"], chunk.get("page_start") or 0,
                                                                 title))
    return index


def _proof(chunk: dict, index: dict[str, list[tuple[int, int, str]]]) -> tuple[str | None, list[str]]:
    """Os números deste quadro que aparecem em outro quadro, com a coluna (e o ano) de lá; e os
    títulos dessas colunas, para saber se a prova contradiz o ano do texto."""
    table = parse_table(chunk.get("text") or "")
    if table is None:
        return None, []
    found: list[str] = []
    titles: list[str] = []
    for row in table.rows:
        for cell in row:
            match = _BIG_NUMBER.fullmatch(cell.strip())
            if not match:
                continue
            for other, page, title in index.get(match.group(1), []):
                if other != chunk["chunk_index"]:
                    line = f"{match.group(1)} aparece na coluna \"{title}\" do quadro da p. {page} (chunk {other})"
                    if line not in found:
                        found.append(line)
                        titles.append(title)
                    break
            if len(found) >= _PROOF_MAX:
                return "Os mesmos valores em outro quadro: " + "; ".join(found) + ".", titles
    return ("Os mesmos valores em outro quadro: " + "; ".join(found) + "." if found else None), titles


def _contradicts(stated: str | None, titles: list[str]) -> bool:
    """Prova forte: dois valores ou mais em colunas com ano, nenhuma delas do ano que o texto afirma."""
    dated = [title for title in titles if _ANY_YEAR.search(title)]
    return stated is not None and len(dated) >= _PROOF_MAX and not any(stated in title for title in dated)


def find(chunks: Iterable[dict], period: str | None, extraction: Any = None) -> list[Candidate]:
    """Os candidatos do documento, na ordem das páginas. No máximo MAX_CANDIDATES. `extraction` é a
    extração publicada, para as regras do domínio que comparam o texto com ela."""
    year = year_of(period)
    chunks = list(chunks)
    by_column = _numbers_by_column(chunks)
    rules = atual().plugin.regras_inconsistencia(
        [("ano_comparado_com_ele_mesmo", _same_year_comparison)], year, extraction)
    out: list[Candidate] = []
    for chunk in chunks:
        text = chunk.get("text") or ""
        page, index = chunk.get("page_start") or 0, chunk["chunk_index"]
        for sentence in sentences(text):
            if len(sentence) > _SENTENCE_MAX:
                continue
            for rule, check in rules:
                reason = check(sentence)
                if reason:
                    out.append(Candidate(index, page, rule, reason, sentence))
                    break
        if chunk.get("chunk_type") == "table" or "\n|" in text:
            problems = _table_dates(text)
            proof, titles = _proof(chunk, by_column) if problems else (None, [])
            for reason, stated in problems:
                out.append(Candidate(index, page, "data_do_quadro", reason, _quote_for_table(text), proof,
                                     prova_forte=_contradicts(stated, titles)))
    return out[:MAX_CANDIDATES]
