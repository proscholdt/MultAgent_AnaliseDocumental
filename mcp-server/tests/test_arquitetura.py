"""As fronteiras da arquitetura, conferidas a cada `make testes`.

- `app/dominio` e `app/regras` são o núcleo: só a biblioteca padrão, pydantic e eles mesmos.
  Banco (SQLAlchemy), HTTP, MCP e os adaptadores (`app.dados`) ficam de fora.
- `app/casos_de_uso` usa as regras e as portas, nunca os adaptadores diretamente.
"""
import ast
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app"

# Pacote → prefixos de import proibidos.
_PROIBIDOS = {
    "dominio": ("app.dados", "app.governanca", "app.tools", "app.casos_de_uso", "sqlalchemy", "httpx", "mcp"),
    "regras": ("app.dados", "app.governanca", "app.tools", "app.casos_de_uso", "sqlalchemy", "httpx", "mcp"),
    "casos_de_uso": ("app.dados", "app.tools", "sqlalchemy", "httpx"),
}


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.append(node.module)
    return names


@pytest.mark.parametrize("pacote", sorted(_PROIBIDOS))
def test_nucleo_nao_depende_de_infraestrutura(pacote):
    violacoes = [
        f"{arquivo.relative_to(APP.parent)} importa {nome}"
        for arquivo in sorted((APP / pacote).glob("*.py"))
        for nome in _imports(arquivo)
        if any(nome == proibido or nome.startswith(proibido + ".") for proibido in _PROIBIDOS[pacote])
    ]
    assert not violacoes, "\n".join(violacoes)
