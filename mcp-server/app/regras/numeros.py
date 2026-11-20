"""Números como os documentos os escrevem (formato brasileiro) e unidades monetárias.

O servidor nunca recebe um número "interpretado" pelo modelo: ele mesmo lê o texto da célula
ou do trecho e converte. `(1.234)` é negativo, `-` e célula vazia não são número, `13,5%` é
percentual e `3,32x` é múltiplo.
"""
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

_NUMBER = re.compile(r"\d[\d.,]*\d|\d")
# Um número inteiro do texto, com o que vem colado nele: sinal, parênteses, % ou x.
_VALUE = re.compile(r"(?P<open>\()?\s*(?P<sign>[-−–])?\s*(?:R\$\s*)?(?P<number>\d[\d.,]*\d|\d)\s*"
                    r"(?P<suffix>%|x\b)?\s*(?P<close>\))?")
_SCALE_WORD = r"(mil|milhares|milh[õo]es|milh[ãa]o|bilh[õo]es|bilh[ãa]o)"
_UNIT = re.compile(rf"R\$\s*{_SCALE_WORD}\b|\b{_SCALE_WORD}\s+de\s+reais", re.IGNORECASE)
_UNIT_AFTER_NUMBER = re.compile(rf"\s*{_SCALE_WORD}\b", re.IGNORECASE)
_SCALES = {"R$": Decimal(1), "R$ mil": Decimal(1_000), "R$ milhões": Decimal(1_000_000),
           "R$ bilhões": Decimal(1_000_000_000)}


@dataclass(frozen=True)
class Number:
    value: Decimal
    text: str  # como está escrito na fonte, com sinal e sufixo
    kind: str  # "valor" | "percentual" | "multiplo"


def to_decimal(token: str) -> Decimal | None:
    """Converte um número escrito no formato brasileiro. Com ponto e vírgula, o último
    separador é o decimal. Só com pontos em grupos de três dígitos, são milhares ("1.325.059").
    Só com vírgula, é decimal ("26,51")."""
    token = token.strip()
    if not token or not re.fullmatch(r"\d[\d.,]*\d|\d", token):
        return None
    if "," in token and "." in token:
        decimal_separator = "," if token.rfind(",") > token.rfind(".") else "."
        thousands = "." if decimal_separator == "," else ","
        token = token.replace(thousands, "").replace(decimal_separator, ".")
    elif "," in token:
        head, _, tail = token.rpartition(",")
        # "1,234,567" (milhares à inglesa) não aparece nestes documentos; vírgula é decimal.
        token = head.replace(",", "") + "." + tail
    elif "." in token:
        groups = token.split(".")
        if all(len(group) == 3 for group in groups[1:]) and 1 <= len(groups[0]) <= 3:
            token = "".join(groups)
    try:
        return Decimal(token)
    except InvalidOperation:
        return None


def parse_number(text: str) -> Number | None:
    """O número de uma célula ou de um valor citado. None se o texto não é um único número
    (célula vazia, traço, rótulo)."""
    text = " ".join(text.split())
    match = _VALUE.fullmatch(text)
    if not match:
        return None
    value = to_decimal(match["number"])
    if value is None:
        return None
    if (match["open"] and match["close"]) or match["sign"]:
        value = -value
    kind = {"%": "percentual", "x": "multiplo"}.get(match["suffix"] or "", "valor")
    return Number(value=value, text=text, kind=kind)


def number_tokens(text: str) -> list[str]:
    """Os números do texto, cada um inteiro: em "21.234" o único número é "21.234"."""
    return [token.rstrip(".,") for token in _NUMBER.findall(text)]


def same_number(a: str, b: str) -> bool:
    left, right = to_decimal(a), to_decimal(b)
    return left is not None and left == right


def contains_number(text: str, number: str) -> bool:
    """O número aparece no texto como número inteiro, e não como pedaço de outro: "1.234" não
    está em "21.234"."""
    return any(same_number(token, number) for token in number_tokens(text))


def _unit(word: str) -> str:
    word = word.lower()
    if word.startswith("bilh"):
        return "R$ bilhões"
    if word.startswith("milh") and not word.startswith("milhar"):
        return "R$ milhões"
    return "R$ mil"


def unit_in_text(text: str) -> str | None:
    """Unidade monetária declarada no trecho ("valores em R$ mil", "em milhares de reais")."""
    match = _UNIT.search(text)
    return _unit(match.group(1) or match.group(2)) if match else None


def unit_of_number(text: str, number: str) -> str | None:
    """Unidade escrita logo depois de um número do trecho: em "totalizou R$ 9.032 milhões, de
    R$ 7.353 milhões", a de "9.032" é R$ milhões. None se o número não traz unidade colada."""
    for match in _NUMBER.finditer(text):
        if same_number(match.group().rstrip(".,"), number):
            # O ponto final da frase pode ter entrado no número ("... R$ 9.032."): volta um.
            end = match.end() - (len(match.group()) - len(match.group().rstrip(".,")))
            unit = _UNIT_AFTER_NUMBER.match(text, end)
            if unit:
                return _unit(unit.group(1))
    return None


def reais_sem_escala(text: str, number: str) -> bool:
    """O número vem logo depois de "R$", sem escala ("R$ 120.000,00"): é um valor em reais. Só
    vale quando nem o trecho nem o chunk declaram uma escala (mil, milhões), que tem precedência."""
    for match in _NUMBER.finditer(text):
        if same_number(match.group().rstrip(".,"), number) and re.search(r"R\$\s*$", text[:match.start()]):
            end = match.end() - (len(match.group()) - len(match.group().rstrip(".,")))
            return not _UNIT_AFTER_NUMBER.match(text, end)
    return False


def in_reais(value: Decimal, unit: str | None) -> Decimal | None:
    """O valor em reais, para comparar dois valores de unidades diferentes. None se a unidade
    não é monetária conhecida."""
    scale = _SCALES.get(unit or "")
    return value * scale if scale is not None else None


def format_br(value: Decimal, places: int = 0) -> str:
    """Número no formato brasileiro: 1325059 → "1.325.059"; 24.84 → "24,84"."""
    quantized = value.quantize(Decimal(1).scaleb(-places)) if places else value.quantize(Decimal(1))
    text = f"{quantized:,.{places}f}"
    return text.replace(",", "§").replace(".", ",").replace("§", ".")
