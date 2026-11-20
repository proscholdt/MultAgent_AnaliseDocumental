"""Catálogo de tools: cada tool é uma função registrada com nome, grupo, descrição e o modelo
dos argumentos. O servidor monta `tools/list` e valida `tools/call` a partir daqui.

Os argumentos são sempre planos (sem objeto dentro de objeto) e com enumerações. Dois motivos:
o modelo erra menos, e o erro de validação volta apontando um campo, não um JSON inteiro.
"""
import copy
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, get_type_hints

import mcp.types as t
from pydantic import BaseModel, ConfigDict

from app.config import Settings
from app.dados.banco import Database
from app.dados.base import Store
from app.dados.documentos import Documents
from app.dados.indexador import IndexerClient
from app.governanca.auditoria import Audit
from app.governanca.identidade import Caller, UserResolver
from app.governanca.politica import PolicyStore
from app.regras.campos import com_opcoes, no_vocabulario

# Restrições que a validação daqui confere, mas que o modo estrito de saída do modelo não aceita
# no esquema. Ficam só no Pydantic.
_SCHEMA_ONLY_VALIDATION = {"title", "default", "minLength", "maxLength", "pattern", "minimum", "maximum",
                           "exclusiveMinimum", "exclusiveMaximum", "format"}


class Args(BaseModel):
    """Base dos argumentos de toda tool: campo desconhecido é erro."""

    model_config = ConfigDict(extra="forbid")


@dataclass
class Deps:
    """O que as tools usam. Montado uma vez na subida do servidor."""

    settings: Settings
    indexer: IndexerClient
    users: UserResolver
    policies: PolicyStore
    audit: Audit
    database: Database
    documents: Documents
    store: Store


@dataclass(frozen=True)
class Call:
    """Uma chamada de tool: quem chama e o que está disponível para atendê-la."""

    caller: Caller
    deps: Deps


Handler = Callable[[Call, Any], Awaitable[Any]]


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Esquema JSON dos argumentos no formato que o modo estrito exige: sem referências, todo
    campo obrigatório (o opcional aceita null) e nenhum campo extra."""
    schema = model.model_json_schema()
    definitions = schema.pop("$defs", {})

    def clean(node: Any) -> Any:
        if isinstance(node, list):
            return [clean(item) for item in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            return clean(copy.deepcopy(definitions[node["$ref"].rsplit("/", 1)[1]]))
        cleaned = {key: clean(value) for key, value in node.items() if key not in _SCHEMA_ONLY_VALIDATION}
        if cleaned.get("type") == "object":
            cleaned["properties"] = cleaned.get("properties", {})
            cleaned["required"] = list(cleaned["properties"])
            cleaned["additionalProperties"] = False
        return cleaned

    return clean(schema)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    group: str
    description: str
    args: type[BaseModel]
    handler: Handler

    def definition(self, dominio: str | None = None) -> t.Tool:
        """A tool como o agente a vê, com as listas fechadas (campos, categorias, perguntas) do domínio."""
        return t.Tool(name=self.name, description=no_vocabulario(self.description, dominio),
                      input_schema=com_opcoes(strict_schema(self.args), dominio))


_TOOLS: dict[str, ToolSpec] = {}


def tool(name: str, group: str, description: str) -> Callable[[Handler], Handler]:
    """Registra uma tool. O modelo dos argumentos vem da anotação do parâmetro `args`."""

    def register(handler: Handler) -> Handler:
        if name in _TOOLS:
            raise ValueError(f"Tool registrada duas vezes: {name}")
        args = get_type_hints(handler)["args"]
        _TOOLS[name] = ToolSpec(name=name, group=group, description=" ".join(description.split()), args=args,
                                handler=handler)
        return handler

    return register


def load_tools() -> dict[str, ToolSpec]:
    """Todas as tools, na ordem em que foram registradas. Importar os módulos é o que registra."""
    from app.tools import analise, documentos, extracao, relatorio, respostas, validacao  # noqa: F401

    return dict(_TOOLS)
