"""As regras de inconsistência das demonstrações: a conta em reais de uma frase, a variação contra
a extração e a projeção para um ano anterior ao do documento. O núcleo acha os candidatos; o
validador julga (ver app/regras/inconsistencias.py no servidor MCP)."""
import re
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.plugins.api import COMPARISON as _COMPARISON
from app.plugins.api import FIELDS, MAIN_FIELDS, Regra, format_br, in_reais, plain, to_decimal

# Diferença aceita, em fração do maior valor, para a conta da frase (o texto arredonda).
ROUNDING = Decimal("0.02")
# Diferença aceita, em pontos percentuais, para a variação percentual da frase.
PERCENT_ROUNDING = Decimal("1.5")

_SCALE = r"(?P<scale>mil(?:h(?:[õo]es|[ãa]o)|hares)?|bilh(?:[õo]es|[ãa]o))"
_MONEY = re.compile(rf"(?P<neg>[-−–]\s*)?R\$\s*(?P<number>\d[\d.,]*\d|\d)(?:\s+{_SCALE}\b)?", re.IGNORECASE)
_PERCENT = re.compile(r"(?P<number>\d[\d.,]*\d|\d)\s*%")
_DOWN = r"redu[çc][ãa]o|queda|retra[çc][ãa]o|diminui[çc][ãa]o|recuo|decr[ée]scimo"
_UP = r"aumento|crescimento|alta|eleva[çc][ãa]o|incremento|acr[ée]scimo|avan[çc]o"
_CHANGE = re.compile(rf"\b(?:(?P<down>{_DOWN})|(?P<up>{_UP}))\b(?:\s+de)?\s*$", re.IGNORECASE)
_FORWARD = re.compile(r"(?:cen[áa]rio|proje[çc][ãa]o|proje[çc][õo]es|projetad[oa]s?|previs[ãa]o|estimativa|expectativa"
                      r"|guidance|perspectiva)[^.;]{0,80}?\b(?:para|em)\s+(?:os\s+|o\s+)?(?:pr[óo]ximos\s+)?"
                      r"(?:\d+\s+meses\s+de\s+|ano\s+de\s+|exerc[íi]cio\s+de\s+)?(20\d{2})\b", re.IGNORECASE)


@dataclass(frozen=True)
class Indicator:
    """Um indicador em dinheiro com os dois períodos, como a extração o leu (em reais)."""

    rotulo: str
    termos: tuple[str, ...]  # sem acento, minúsculas: o que a frase tem de mencionar
    atual: Decimal
    anterior: Decimal


def indicators_of(extraction: Any) -> list[Indicator]:
    """Os indicadores em dinheiro com os dois períodos, para a regra que compara o texto com a
    extração. Sem extração (ou sem o ano anterior), a lista fica vazia e a regra não roda."""
    if extraction is None:
        return []
    out = []
    for campo in MAIN_FIELDS:
        spec = FIELDS[campo]
        now, before = extraction.amount(campo, extraction.reference), extraction.amount(campo, extraction.comparable)
        if spec.kind == "valor" and now is not None and before is not None:
            out.append(Indicator(spec.label, spec.mention, now, before))
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


def _projection_in_the_past(sentence: str, year: int | None) -> str | None:
    if year is None:
        return None
    for match in _FORWARD.finditer(sentence):
        target = int(match.group(1))
        if target < year:
            return f"Projeção ou cenário para {target}, num documento de {year}."
    return None


def regras(nucleo: list[Regra], year: int | None, indicators: Iterable[Indicator]) -> list[Regra]:
    """As regras na ordem de sempre: as de dinheiro primeiro, a do ano do núcleo, a da projeção."""
    indicators = list(indicators)
    return [("conta_na_frase", _sentence_arithmetic),
            ("variacao_contra_extracao", lambda s: _against_extraction(s, indicators)),
            *nucleo,
            ("projecao_para_o_passado", lambda s: _projection_in_the_past(s, year))]
