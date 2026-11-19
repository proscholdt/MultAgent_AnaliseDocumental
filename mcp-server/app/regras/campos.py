"""O domínio do relatório lido de `config/dominio.yaml`: os campos da extração (os indicadores do
relatório e os valores de apoio das fórmulas), as categorias da análise e as perguntas.

O arquivo é lido uma vez, na importação: o esquema das tools (os `Literal` abaixo) sai dele, e
o modelo vê esse esquema. Outro arquivo: variável de ambiente `DOMINIO_PATH`.
"""
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

DOMINIO_PATH = Path(os.environ.get("DOMINIO_PATH") or Path(__file__).resolve().parents[2] / "config" / "dominio.yaml")

# Os operandos e resultados de app/regras/formulas.py: sem eles, as fórmulas não fecham.
CAMPOS_DAS_FORMULAS = frozenset({
    "receita_liquida", "ebitda", "margem_ebitda", "lucro_liquido", "divida_liquida", "alavancagem",
    "fluxo_caixa_operacional", "capex", "resultado_antes_financeiro", "depreciacao_amortizacao",
    "emprestimos_circulante", "emprestimos_nao_circulante", "caixa_equivalentes", "capex_imobilizado",
    "capex_intangivel", "capex_imobilizado_e_intangivel",
})
_NOME = r"^[a-z][a-z0-9_]{1,39}$"


@dataclass(frozen=True)
class Field:
    name: str
    label: str
    kind: str  # "valor" (dinheiro) | "percentual" | "multiplo"
    main: bool
    terms: tuple[str, ...] = ()
    section: str | None = None
    mention: tuple[str, ...] = ()
    statement: bool = False


class _Bloco(BaseModel):
    model_config = ConfigDict(extra="forbid")  # chave com erro de digitação é erro, não silêncio


class _Campo(_Bloco):
    nome: str
    rotulo: str
    tipo: Literal["valor", "percentual", "multiplo"]
    principal: bool
    termos: list[str] = []
    secao: str | None = None
    mencao: list[str] = []
    demonstracao: bool = False

    @field_validator("nome")
    @classmethod
    def _nome(cls, value: str) -> str:
        if not re.fullmatch(_NOME, value):
            raise ValueError(f"nome inválido: {value!r} (minúsculas, números e _)")
        return value


class _Categoria(_Bloco):
    nome: str
    rotulo: str
    termos: list[str] = []


class _Pergunta(_Bloco):
    id: str
    enunciado: str

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        if value == "livre" or not value[:1].isalpha():
            raise ValueError(f"id de pergunta inválido: {value!r}")
        return value


class Dominio(_Bloco):
    campos: list[_Campo]
    categorias: list[_Categoria]
    perguntas: list[_Pergunta]

    @model_validator(mode="after")
    def _coerente(self) -> "Dominio":
        for rotulo, nomes in (("campo", [c.nome for c in self.campos]), ("categoria", [c.nome for c in self.categorias]),
                              ("pergunta", [p.id for p in self.perguntas])):
            repetidos = sorted({nome for nome in nomes if nomes.count(nome) > 1})
            if repetidos:
                raise ValueError(f"{rotulo} repetido: {', '.join(repetidos)}")
        faltam = sorted(CAMPOS_DAS_FORMULAS - {c.nome for c in self.campos})
        if faltam:
            raise ValueError(f"faltam os campos usados pelas fórmulas: {', '.join(faltam)}")
        if not any(c.principal for c in self.campos) or not self.categorias:
            raise ValueError("é preciso ao menos um campo principal e uma categoria")
        return self


def carregar(path: Path = DOMINIO_PATH) -> Dominio:
    try:
        bruto: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise RuntimeError(f"{path}: {error}") from error
    return Dominio.model_validate(bruto)


DOMINIO = carregar()

_FIELDS = [Field(name=c.nome, label=c.rotulo, kind=c.tipo, main=c.principal, terms=tuple(c.termos), section=c.secao,
                 mention=tuple(c.mencao), statement=c.demonstracao) for c in DOMINIO.campos]
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
