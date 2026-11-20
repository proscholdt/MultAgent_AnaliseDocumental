"""Carrega o plugin do servidor MCP de um pacote: `dominios/<id>/plugin/mcp/__init__.py` (ou
`plugin/mcp.py`), com uma classe `Plugin` que herda de `PluginMCP`.

O plugin não é instalado nem copiado para a imagem: é lido da pasta dos pacotes, a mesma montada
em `/app/dominios` nos contêineres. Pacote sem plugin, ou com plugin só para outro sistema (o
indexador), usa o neutro.
"""
import importlib.util
import sys
from pathlib import Path

from app import pacotes
from app.plugins.interface import PluginGenerico, PluginMCP


def arquivo_do_plugin(pacote: pacotes.Pacote) -> Path | None:
    """Onde está o plugin deste servidor no pacote, se ele declara um."""
    if not pacote.plugin:
        return None
    pasta = pacotes.pasta_dos_dominios() / pacote.id / "plugin"
    for candidato in (pasta / "mcp" / "__init__.py", pasta / "mcp.py"):
        if candidato.is_file():
            return candidato
    return None


def carregar(pacote: pacotes.Pacote) -> PluginMCP:
    path = arquivo_do_plugin(pacote)
    if path is None:
        return PluginGenerico()
    nome = f"plugin_{pacote.id}_mcp"
    pacote_python = path.name == "__init__.py"
    spec = importlib.util.spec_from_file_location(
        nome, path, submodule_search_locations=[str(path.parent)] if pacote_python else None)
    if spec is None or spec.loader is None:
        raise pacotes.PacoteInvalido(f"{pacote.id}: não consegui ler o plugin em {path}")
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[nome] = modulo  # os submódulos do plugin (from .formulas import ...) se acham por aqui
    try:
        spec.loader.exec_module(modulo)
    except Exception as erro:
        sys.modules.pop(nome, None)
        raise pacotes.PacoteInvalido(f"{pacote.id}: o plugin em {path} falhou ao carregar: {erro}") from erro
    classe = getattr(modulo, "Plugin", None)
    if not (isinstance(classe, type) and issubclass(classe, PluginMCP)):
        raise pacotes.PacoteInvalido(f"{pacote.id}: {path} não define `Plugin`, subclasse de PluginMCP")
    return classe()
