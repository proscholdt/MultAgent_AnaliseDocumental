"""Os campos da extração, as categorias da análise e as perguntas do relatório, lidos do pacote de
domínio (`dominios/<id>/dominio.yaml`, formato em app/pacotes.py).

Um servidor atende vários domínios. O domínio de cada chamada vem do token do agente (a coleção da
execução) e fica numa variável de contexto durante a chamada (`usando`). `FIELDS`, `MAIN_FIELDS`,
`CATEGORIES` e `QUESTIONS` são visões do domínio da chamada atual: quem as importa continua lendo
como dicionário e tupla, sem saber de domínio. Fora de uma chamada (testes, ferramentas), vale o
domínio da variável `DOMINIO` (padrão: financeiro).

Nos argumentos das tools, `Campo`, `CampoPrincipal`, `Categoria` e `PerguntaId` são texto marcado:
o esquema que o agente vê no `tools/list` ganha a lista fechada do domínio dele (`com_opcoes`), e
a chamada confere o valor contra o domínio da chamada.
"""
import os
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import cache
from typing import Annotated, Any

from pydantic import AfterValidator
from pydantic import Field as PydanticField

from app import pacotes
from app.plugins.carregar import carregar as carregar_plugin
from app.plugins.interface import PluginMCP


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


def conferir(pacote: pacotes.Pacote, plugin: PluginMCP | None = None) -> pacotes.Pacote:
    """As exigências que o plugin de um pacote faz aos campos dele."""
    (plugin or carregar_plugin(pacote)).validar_pacote(pacote)
    return pacote


def carregar(dominio: str | None = None) -> pacotes.Pacote:
    """O pacote do domínio, com as exigências do plugin deste servidor."""
    return conferir(pacotes.pacote(dominio))


@dataclass(frozen=True)
class Dominio:
    """O que as regras e as tools precisam de um domínio, já no formato delas."""
    pacote: pacotes.Pacote
    fields: dict[str, Field]
    main_fields: tuple[str, ...]
    categories: dict[str, tuple[str, tuple[str, ...]]]
    questions: dict[str, str]
    # O código do domínio (dominios/<id>/plugin/mcp): sem plugin, o neutro, que só tem os portões do núcleo.
    plugin: PluginMCP

    @property
    def id(self) -> str:
        return self.pacote.id

    def opcoes(self, tipo: str) -> list[str]:
        return {"campos": list(self.fields), "campos_principais": list(self.main_fields),
                "categorias": list(self.categories), "perguntas": [*self.questions, "livre"]}[tipo]


@cache
def dominio(dominio_id: str | None = None) -> Dominio:
    pacote = pacotes.pacote(dominio_id)
    plugin = carregar_plugin(pacote)
    conferir(pacote, plugin)
    fields = [Field(name=c.nome, label=c.rotulo, kind=c.tipo, main=c.principal, terms=tuple(c.termos),
                    section=c.secao, mention=tuple(c.mencao), statement=c.demonstracao, unit=c.unidade)
              for c in pacote.campos]
    return Dominio(
        pacote=pacote, fields={field.name: field for field in fields},
        main_fields=tuple(field.name for field in fields if field.main),
        # Categorias da análise e os termos procurados quando o agente diz que o documento não as traz.
        categories={c.nome: (c.rotulo, tuple(c.termos)) for c in pacote.categorias},
        # As perguntas do relatório. As respostas a elas entram no relatório.
        questions={p.id: p.enunciado for p in pacote.perguntas},
        plugin=plugin)


# O domínio do processo (fora de chamada) e o da chamada atual.
PADRAO_DO_PROCESSO = os.environ.get("DOMINIO") or None
_ATUAL: ContextVar[str | None] = ContextVar("dominio", default=None)


def atual() -> Dominio:
    return dominio(_ATUAL.get() or PADRAO_DO_PROCESSO)


@contextmanager
def usando(dominio_id: str | None) -> Iterator[Dominio]:
    """Durante o bloco, as regras e as tools enxergam este domínio."""
    escolhido = dominio(dominio_id or PADRAO_DO_PROCESSO)  # domínio desconhecido: PacoteInvalido aqui
    token = _ATUAL.set(escolhido.id)
    try:
        yield escolhido
    finally:
        _ATUAL.reset(token)


class _MapaAtual(Mapping):
    """Um dicionário do domínio da chamada atual."""

    def __init__(self, pegar: Callable[[Dominio], dict]) -> None:
        self._pegar = pegar

    def __getitem__(self, key: str) -> Any:
        return self._pegar(atual())[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._pegar(atual()))

    def __len__(self) -> int:
        return len(self._pegar(atual()))

    def __repr__(self) -> str:
        return repr(self._pegar(atual()))


class _TuplaAtual(Sequence):
    """Uma tupla do domínio da chamada atual."""

    def __init__(self, pegar: Callable[[Dominio], tuple]) -> None:
        self._pegar = pegar

    def __getitem__(self, index):  # type: ignore[override]
        return self._pegar(atual())[index]

    def __len__(self) -> int:
        return len(self._pegar(atual()))

    def __iter__(self) -> Iterator[str]:
        return iter(self._pegar(atual()))

    def __contains__(self, value: object) -> bool:
        return value in self._pegar(atual())

    def __add__(self, other: tuple) -> tuple:
        return tuple(self) + tuple(other)

    def __repr__(self) -> str:
        return repr(self._pegar(atual()))


FIELDS: Mapping[str, Field] = _MapaAtual(lambda d: d.fields)
MAIN_FIELDS: Sequence[str] = _TuplaAtual(lambda d: d.main_fields)
CATEGORIES: Mapping[str, tuple[str, tuple[str, ...]]] = _MapaAtual(lambda d: d.categories)
QUESTIONS: Mapping[str, str] = _MapaAtual(lambda d: d.questions)


class _DominioDoProcesso:
    """`DOMINIO` como antes: o pacote do domínio da chamada atual."""

    def __getattr__(self, name: str) -> Any:
        return getattr(atual().pacote, name)


DOMINIO: Any = _DominioDoProcesso()


def _escolha(tipo: str) -> Any:
    """Texto que só aceita uma das opções do domínio da chamada; no esquema, marcado para receber
    a lista fechada do domínio de quem pede o `tools/list`."""
    def conferir_valor(value: str) -> str:
        opcoes = atual().opcoes(tipo)
        if value not in opcoes:
            raise ValueError(f"{value!r} não existe neste domínio ({atual().id}). Use um de: {', '.join(opcoes)}")
        return value

    return Annotated[str, AfterValidator(conferir_valor), PydanticField(json_schema_extra={"x-dominio": tipo})]


Campo = _escolha("campos")
CampoPrincipal = _escolha("campos_principais")
Categoria = _escolha("categorias")
PerguntaId = _escolha("perguntas")


def com_opcoes(schema: Any, dominio_id: str | None = None) -> Any:
    """O esquema de uma tool com a lista fechada do domínio no lugar de cada marca `x-dominio`."""
    alvo = dominio(dominio_id or PADRAO_DO_PROCESSO)
    if isinstance(schema, list):
        return [com_opcoes(item, dominio_id) for item in schema]
    if not isinstance(schema, dict):
        return schema
    out = {key: com_opcoes(value, dominio_id) for key, value in schema.items() if key != "x-dominio"}
    if "x-dominio" in schema:
        out["enum"] = alvo.opcoes(schema["x-dominio"])
    return out
