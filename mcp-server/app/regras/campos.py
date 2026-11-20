"""Os campos da extração, as categorias da análise e as perguntas do relatório, lidos do pacote de
domínio (`dominios/<id>/dominio.yaml`, formato em app/pacotes.py).

O pacote é lido uma vez, na importação: o esquema das tools (os `Literal` abaixo) sai dele, e o
modelo vê esse esquema. Qual pacote: a variável `DOMINIO` (padrão: financeiro); onde ficam os
pacotes: `DOMINIOS_PATH` (padrão: /app/dominios no contêiner, dominios/ na raiz do projeto).
"""
import os
from dataclasses import dataclass
from typing import Literal

from app import pacotes

# Os operandos e resultados das fórmulas do plugin financeiro (app/regras/formulas.py): sem eles,
# as fórmulas não fecham. Só valem para pacote com `plugin: financeiro`.
CAMPOS_DAS_FORMULAS = frozenset({
    "receita_liquida", "ebitda", "margem_ebitda", "lucro_liquido", "divida_liquida", "alavancagem",
    "fluxo_caixa_operacional", "capex", "resultado_antes_financeiro", "depreciacao_amortizacao",
    "emprestimos_circulante", "emprestimos_nao_circulante", "caixa_equivalentes", "capex_imobilizado",
    "capex_intangivel", "capex_imobilizado_e_intangivel",
})


@dataclass(frozen=True)
class Field:
    name: str
    label: str
    kind: str  # "valor" (dinheiro) | "percentual" | "multiplo" | "numero" | "data" | "texto"
    main: bool
    terms: tuple[str, ...] = ()
    section: str | None = None
    mention: tuple[str, ...] = ()
    statement: bool = False
    unit: str | None = None


def carregar(dominio: str | None = None) -> pacotes.Pacote:
    """O pacote do domínio, com as exigências dos plugins deste servidor."""
    return conferir(pacotes.pacote(dominio))


def conferir(pacote: pacotes.Pacote) -> pacotes.Pacote:
    """As exigências que o plugin de um pacote faz aos campos dele."""
    if pacote.plugin == "financeiro":
        faltam = sorted(CAMPOS_DAS_FORMULAS - {campo.nome for campo in pacote.campos})
        if faltam:
            raise pacotes.PacoteInvalido(f"{pacote.id}: faltam os campos usados pelas fórmulas: {', '.join(faltam)}")
    return pacote


DOMINIO = carregar(os.environ.get("DOMINIO") or None)

_FIELDS = [Field(name=c.nome, label=c.rotulo, kind=c.tipo, main=c.principal, terms=tuple(c.termos), section=c.secao,
                 mention=tuple(c.mencao), statement=c.demonstracao, unit=c.unidade) for c in DOMINIO.campos]
FIELDS: dict[str, Field] = {field.name: field for field in _FIELDS}
MAIN_FIELDS: tuple[str, ...] = tuple(field.name for field in _FIELDS if field.main)

# Categorias da análise qualitativa e os termos procurados quando o agente diz que o documento não
# traz aquela informação.
CATEGORIES: dict[str, tuple[str, tuple[str, ...]]] = {c.nome: (c.rotulo, tuple(c.termos)) for c in DOMINIO.categorias}

# As perguntas do relatório. As respostas a elas entram no relatório.
QUESTIONS: dict[str, str] = {p.id: p.enunciado for p in DOMINIO.perguntas}

# Os tipos dos argumentos das tools: o que o modelo pode escolher.
Campo = Literal[tuple(FIELDS)]  # type: ignore[valid-type]
CampoPrincipal = Literal[MAIN_FIELDS]  # type: ignore[valid-type]
Categoria = Literal[tuple(CATEGORIES)]  # type: ignore[valid-type]
PerguntaId = Literal[(*QUESTIONS, "livre")]  # type: ignore[valid-type]
