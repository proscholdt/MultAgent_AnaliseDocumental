"""Candidatos a inconsistência no texto da fonte, achados por regra.

O validador recebe a lista, lê cada trecho e decide o que é erro do documento: a regra acha, o
modelo julga. Regra que acha demais custa algumas leituras; regra que não acha deixa o erro para
o olho do modelo, como antes. Cinco regras, de três famílias:

- **conta**: a frase traz o valor, a variação e o valor comparável, e a conta não fecha
  ("R$1.817 milhões, uma redução de R$2.996 milhões em relação aos R$1.179 milhões"); ou a
  frase traz o valor de um indicador e a variação, e ela não bate com os dois períodos que a
  extração leu das tabelas;
- **ano**: comparação de um ano com ele mesmo ("encerrou 2021 em 3,32x, menor do que no 2021");
  projeção para um ano anterior ao do documento ("cenário provável para os 12 meses de 2020",
  num documento de 2022);
- **quadro**: a data do texto que apresenta o quadro não está nas colunas dele; duas colunas com
  o mesmo título e a mesma data.
"""
import re
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from app.regras.campos import FIELDS, MAIN_FIELDS
from app.regras.formulas import Extraction
from app.regras.numeros import format_br, in_reais, to_decimal
from app.regras.tabelas import parse_table
from app.regras.texto import plain, squash

MAX_CANDIDATES = 40
_SENTENCE_MAX = 600
# Diferença aceita, em fração do maior valor, para a conta da frase (o texto arredonda).
ROUNDING = Decimal("0.02")
# Diferença aceita, em pontos percentuais, para a variação percentual da frase.
PERCENT_ROUNDING = Decimal("1.5")
# Até onde, depois da palavra de comparação, procurar o ano comparado.
_NEAR = 25

_YEAR = re.compile(r"(?<![\d/.,])(20\d{2})(?![\d.,]\d)")
_ANY_YEAR = re.compile(r"(?<!\d)(20\d{2})(?!\d)")  # também dentro de data: "31/12/2021"
_DATE_YEAR = re.compile(r"\b\d{1,2}\s+de\s+[a-zç]+\s+de\s+(20\d{2})\b|\b\d{1,2}/\d{1,2}/(20\d{2})\b", re.IGNORECASE)
_SCALE = r"(?P<scale>mil(?:h(?:[õo]es|[ãa]o)|hares)?|bilh(?:[õo]es|[ãa]o))"
_MONEY = re.compile(rf"(?P<neg>[-−–]\s*)?R\$\s*(?P<number>\d[\d.,]*\d|\d)(?:\s+{_SCALE}\b)?", re.IGNORECASE)
_PERCENT = re.compile(r"(?P<number>\d[\d.,]*\d|\d)\s*%")
_DOWN = r"redu[çc][ãa]o|queda|retra[çc][ãa]o|diminui[çc][ãa]o|recuo|decr[ée]scimo"
_UP = r"aumento|crescimento|alta|eleva[çc][ãa]o|incremento|acr[ée]scimo|avan[çc]o"
_CHANGE = re.compile(rf"\b(?:(?P<down>{_DOWN})|(?P<up>{_UP}))\b(?:\s+de)?\s*$", re.IGNORECASE)
_COMPARISON = re.compile(r"em\s+rela[çc][ãa]o|comparad|compara[çc][ãa]o|frente\s+a|contra\b|\bante\b|versus|\bvs\b"
                         r"|menor\s+(?:do\s+)?que|maior\s+(?:do\s+)?que|inferior\s+a|superior\s+a", re.IGNORECASE)
_FORWARD = re.compile(r"(?:cen[áa]rio|proje[çc][ãa]o|proje[çc][õo]es|projetad[oa]s?|previs[ãa]o|estimativa|expectativa"
                      r"|guidance|perspectiva)[^.;]{0,80}?\b(?:para|em)\s+(?:os\s+|o\s+)?(?:pr[óo]ximos\s+)?"
                      r"(?:\d+\s+meses\s+de\s+|ano\s+de\s+|exerc[íi]cio\s+de\s+)?(20\d{2})\b", re.IGNORECASE)
_SENTENCE_END = re.compile(r"(?<=[.;!?])\s+(?=[A-ZÁÉÍÓÚÂÊÔÃÕÇ(\"“])")


RULES = ("conta_na_frase", "variacao_contra_extracao", "ano_comparado_com_ele_mesmo", "projecao_para_o_passado",
         "data_do_quadro")


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


@dataclass(frozen=True)
class Indicator:
    """Um indicador em dinheiro com os dois períodos, como a extração o leu (em reais)."""

    rotulo: str
    termos: tuple[str, ...]  # sem acento, minúsculas: o que a frase tem de mencionar
    atual: Decimal
    anterior: Decimal


def indicators_of(extraction: Extraction) -> list[Indicator]:
    """Os indicadores em dinheiro com os dois períodos, para a regra que compara o texto com a
    extração. Sem extração (ou sem o ano anterior), a lista fica vazia e a regra não roda."""
    out = []
    for campo in MAIN_FIELDS:
        spec = FIELDS[campo]
        now, before = extraction.amount(campo, extraction.reference), extraction.amount(campo, extraction.comparable)
        if spec.kind == "valor" and now is not None and before is not None:
            out.append(Indicator(spec.label, spec.mention, now, before))
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


def _money_in(sentence: str) -> list[tuple[re.Match, Decimal | None]]:
    """Valores em reais da frase, com a escala dita depois do número ("R$1.817 milhões")."""
    found = []
    for match in _MONEY.finditer(sentence):
        number = to_decimal(match["number"].rstrip(".,"))
        if number is None:
            continue
        if match["neg"]:
            number = -number
        scale = (match["scale"] or "").lower()
        unit = ("R$ bilhões" if scale.startswith("bilh") else "R$ milhões" if scale.startswith("milh")
                else "R$ mil" if scale.startswith("mil") else "R$" if not scale else None)
        found.append((match, in_reais(number, unit) if scale else number))
    return found


def _close(a: Decimal, b: Decimal) -> bool:
    return abs(a - b) <= ROUNDING * max(abs(a), abs(b), Decimal(1))


def _sentence_arithmetic(sentence: str) -> str | None:
    """Valor, variação e comparável na mesma frase, e a conta não fecha. Devolve o motivo."""
    money = _money_in(sentence)
    for position in range(1, len(money) - 1):
        change = _CHANGE.search(sentence[:money[position][0].start()][-60:])
        if not change:
            continue
        (first, now), (_, delta), (last, before) = money[position - 1], money[position], money[position + 1]
        between = sentence[money[position][0].end():last.start()]
        if not _COMPARISON.search(between) or None in (now, delta, before):
            continue
        if any(m["scale"] for m, _ in money[position - 1:position + 2]) and \
                not all(m["scale"] for m, _ in money[position - 1:position + 2]):
            continue  # escala em uns e não em outros: não dá para comparar
        expected = before - now if change["down"] else now - before
        if not _close(expected, abs(delta)):
            word = change.group(0).split()[0].lower()
            return (f"{first.group(0).strip()} contra {last.group(0).strip()} dá uma diferença de "
                    f"{_plain_amount(now - before, first)}, e a frase diz {word} de {money[position][0].group(0).strip()}.")
    percents = list(_PERCENT.finditer(sentence))
    for percent in percents:
        change = _CHANGE.search(sentence[:percent.start()][-60:])
        if not change:
            continue
        before_money = [(m, v) for m, v in money if m.end() <= percent.start()]
        after_money = [(m, v) for m, v in money if m.start() >= percent.end()]
        if not before_money or not after_money:
            continue
        (first, now), (last, before) = before_money[-1], after_money[0]
        if not _COMPARISON.search(sentence[percent.end():last.start()]) or None in (now, before) or before == 0:
            continue
        rate = to_decimal(percent["number"])
        computed = (now - before) / abs(before) * 100
        stated = -rate if change["down"] else rate
        if rate is not None and abs(computed - stated) > PERCENT_ROUNDING:
            return (f"{first.group(0).strip()} contra {last.group(0).strip()} é uma variação de "
                    f"{format_br(computed, 1)}%, e a frase diz {change.group(0).split()[0].lower()} de {percent.group(0)}.")
    return None


def _plain_amount(amount: Decimal, like: re.Match) -> str:
    """Uma diferença em reais escrita na escala do valor da frase."""
    scale = (like["scale"] or "").lower()
    unit = ("R$ bilhões" if scale.startswith("bilh") else "R$ milhões" if scale.startswith("milh")
            else "R$ mil" if scale.startswith("mil") else None)
    one = in_reais(Decimal(1), unit) if unit else Decimal(1)
    return f"R$ {format_br(amount / one)}{' ' + unit.split()[1] if unit else ''}"


def _against_extraction(sentence: str, indicators: Iterable[Indicator]) -> str | None:
    """A frase traz o valor atual de um indicador e uma variação em reais que não bate com os dois
    períodos que a extração leu das tabelas."""
    money = _money_in(sentence)
    folded = plain(sentence)
    for indicator in indicators:
        # Um termo com "+" exige todas as partes ("caixa+operacionais"), como nos campos.
        if not any(all(part in folded for part in term.split("+")) for term in indicator.termos):
            continue
        for position in range(len(money) - 1):
            match, value = money[position]
            if value is None or not _close(value, indicator.atual):
                continue
            delta_match, delta = money[position + 1]
            change = _CHANGE.search(sentence[:delta_match.start()][-60:])
            if not change or delta is None:
                continue
            expected = indicator.anterior - indicator.atual if change["down"] else indicator.atual - indicator.anterior
            if not _close(expected, abs(delta)):
                return (f"{indicator.rotulo}: a extração leu {_plain_amount(indicator.atual, match)} no período e "
                        f"{_plain_amount(indicator.anterior, match)} no comparável, uma diferença de "
                        f"{_plain_amount(indicator.atual - indicator.anterior, match)}; a frase diz "
                        f"{change.group(0).split()[0].lower()} de {delta_match.group(0).strip()}.")
    return None


def _same_year_comparison(sentence: str) -> str | None:
    """Comparação entre dois momentos com o mesmo ano dos dois lados ("encerrou 2021 em 3,32x,
    menor do que no 2021"). O ano de depois tem de vir logo depois da comparação: em "em relação
    a RTE e em 13 de dezembro de 2022", a comparação não é entre anos."""
    for comparison in _COMPARISON.finditer(sentence):
        before = [m.group(1) for m in _YEAR.finditer(sentence[:comparison.start()])]
        after = _YEAR.search(sentence[comparison.end():comparison.end() + _NEAR])
        if before and after and set(before) == {after.group(1)}:
            return f"A frase compara {before[-1]} com {after.group(1)}: o mesmo ano dos dois lados da comparação."
    return None


def _projection_in_the_past(sentence: str, year: int | None) -> str | None:
    if year is None:
        return None
    for match in _FORWARD.finditer(sentence):
        target = int(match.group(1))
        if target < year:
            return f"Projeção ou cenário para {target}, num documento de {year}."
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


def find(chunks: Iterable[dict], period: str | None, indicators: Iterable[Indicator] = ()) -> list[Candidate]:
    """Os candidatos do documento, na ordem das páginas. No máximo MAX_CANDIDATES."""
    year = year_of(period)
    indicators = list(indicators)
    chunks = list(chunks)
    by_column = _numbers_by_column(chunks)
    out: list[Candidate] = []
    for chunk in chunks:
        text = chunk.get("text") or ""
        page, index = chunk.get("page_start") or 0, chunk["chunk_index"]
        for sentence in sentences(text):
            if len(sentence) > _SENTENCE_MAX:
                continue
            for rule, check in (("conta_na_frase", _sentence_arithmetic),
                                ("variacao_contra_extracao", lambda s: _against_extraction(s, indicators)),
                                ("ano_comparado_com_ele_mesmo", _same_year_comparison),
                                ("projecao_para_o_passado", lambda s: _projection_in_the_past(s, year))):
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
