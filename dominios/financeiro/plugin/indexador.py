"""O plugin das demonstrações financeiras no indexador.

O nome fixo das demonstrações primárias (abre a seção mesmo quando o layout o marca como texto
comum, e separa o título do bloco em que o Azure o devolve), o rodapé "as notas explicativas são
parte integrante", a linha de período ou de unidade que é subtítulo de tabela, e a reserva por
regex dos metadados (razão social S.A./Ltda, data de encerramento). O núcleo do indexador não sabe
nada disso: chama os ganchos de `PluginIndexador` (app/plugins/interface.py).
"""
import re
from collections import Counter

from app.plugins.api import Pacote, ParsedDocument, PluginIndexador

# As demonstrações primárias têm nome fixo. Quando o layout rotula o título como texto comum,
# é por esse nome que ele é reconhecido. O parser da Azure usa a mesma lista para separar o
# título do bloco em que o serviço o devolve (empresa, título, período e unidade juntos).
STATEMENT_TITLE = re.compile(
    r"^(?:balanços?\s+patrimonia(?:l|is)"
    r"|demonstraç(?:ão|ões)\s+d[aeo]s?\s+(?:resultados?(?:\s+abrangentes?)?|fluxos?\s+de\s+caixa"
    r"|mutaç(?:ão|ões)\s+d[eo]\s+patrimônio\s+líquido|valor(?:es)?\s+adicionados?))\b",
    re.IGNORECASE,
)
# Rodapé fixo das demonstrações primárias. O critério de repetição não o pega quando ele
# aparece em menos de cinco páginas (a Millet tem quatro demonstrações de uma página).
_STATEMENT_FOOTER = re.compile(
    r"^as\s+notas\s+explicativas\s+(?:da\s+administração\s+)?são\s+parte\s+integrante\s+d[ae]s(?:tas)?\s+"
    r"demonstrações\s+(?:financeiras|contábeis)(?:\s+(?:individuais|consolidadas|e))*\s*\.?$",
    re.IGNORECASE,
)
# Linha que diz o período ou a unidade de uma tabela, e não o assunto de uma seção.
_PERIOD_LINE = re.compile(
    r"(?:para\s+os?\s+)?(?:exerc[íi]cios?|per[íi]odos?|trimestres?|semestres?)\s+(?:findos?|encerrados?)\s+em\b"
    r"|em\s+\d{1,2}\s+de\s+[a-zç]+\s+de\s+\d{4}\b"
    r"|\((?:em|valores\s+expressos\s+em)\s+(?:milhares|milhões|reais|R\$)",
    re.IGNORECASE,
)
# Reserva por regex dos metadados: razão social S.A./Ltda e data de encerramento.
_MONTH_TO_QUARTER = {"março": "1T", "marco": "1T", "junho": "2T", "setembro": "3T"}
_COMPANY_LINE = re.compile(r"^[A-ZÀ-Ú][\wÀ-ú .,&'’/-]{1,80}?\s(?:S\.\s?A\.?|S/A|Ltda\.?)$")
_DATE = re.compile(r"(\d{1,2})\s+de\s+(janeiro|fevereiro|mar[cç]o|abril|maio|junho|julho|agosto"
                   r"|setembro|outubro|novembro|dezembro)\s+de\s+(\d{4})", re.IGNORECASE)


def metadados_reserva(parsed: ParsedDocument, pacote: Pacote) -> dict[str, str | None]:
    """Reserva determinística sobre as três primeiras páginas."""
    first_pages = [p.native_text for p in sorted(parsed.pages, key=lambda p: p.page)[:3]]
    text = "\n".join(first_pages)

    candidates: Counter[str] = Counter()
    for line in text.splitlines():
        line = line.strip()
        if _COMPANY_LINE.match(line):
            candidates[line] += 1
    company = candidates.most_common(1)[0][0] if candidates else None

    period = None
    match = _DATE.search(text)
    if match:
        day, month, year = int(match.group(1)), match.group(2).lower(), match.group(3)
        if month == "dezembro" and day == 31:
            period = year
        elif month in _MONTH_TO_QUARTER:
            period = f"{_MONTH_TO_QUARTER[month]}{year[2:]}"

    lowered = text.lower()
    doc_type = None
    if "informações trimestrais" in lowered or "informacoes trimestrais" in lowered:
        doc_type = "Informações trimestrais"
    elif "release de resultados" in lowered:
        doc_type = "Release de resultados"
    elif "demonstrações financeiras" in lowered or "demonstracoes financeiras" in lowered:
        doc_type = "Demonstrações financeiras"
    by_role = {"entidade": company, "recorte": period, "tipo_documento": doc_type}
    return {m.nome: by_role[m.papel] for m in pacote.metadados if m.papel in by_role}


class Plugin(PluginIndexador):
    nome = "financeiro"

    def titulo_de_secao(self, linha: str) -> bool:
        return bool(STATEMENT_TITLE.match(linha))

    def rodape_conhecido(self, texto: str) -> bool:
        return bool(_STATEMENT_FOOTER.match(texto))

    def linha_de_subtitulo(self, texto: str) -> bool:
        return bool(_PERIOD_LINE.match(texto))

    def metadados_reserva(self, parsed: ParsedDocument, pacote: Pacote) -> dict[str, str | None]:
        return metadados_reserva(parsed, pacote)

    def instrucao_figuras(self) -> str:
        return "Você transcreve figuras de relatórios financeiros."
