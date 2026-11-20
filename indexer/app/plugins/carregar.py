"""Carrega o plugin do indexador de um pacote: `dominios/<id>/plugin/indexador.py` (ou
`plugin/indexador/__init__.py`), com uma classe `Plugin` que herda de `PluginIndexador`.

O plugin não é instalado nem copiado para a imagem: é lido da pasta dos pacotes, a mesma montada
em `/app/dominios` nos contêineres. Pacote sem plugin, ou com plugin só para outro sistema (o
servidor MCP), usa o neutro.
"""
import importlib.util
import sys
from functools import cache
from pathlib import Path

from app import pacotes
from app.plugins.interface import PluginGenerico, PluginIndexador


def arquivo_do_plugin(pacote: pacotes.Pacote) -> Path | None:
    """Onde está o plugin do indexador no pacote, se ele declara um."""
    if not pacote.plugin:
        return None
    pasta = pacotes.pasta_dos_dominios() / pacote.id / "plugin"
    for candidato in (pasta / "indexador.py", pasta / "indexador" / "__init__.py"):
        if candidato.is_file():
            return candidato
    return None


def do_dominio(dominio: str | None) -> PluginIndexador:
    """O plugin do domínio (None: o padrão), carregado uma vez por processo."""
    return _carregado(dominio or pacotes.PADRAO)


@cache
def _carregado(dominio: str) -> PluginIndexador:
    return carregar(pacotes.pacote(dominio))


def carregar(pacote: pacotes.Pacote) -> PluginIndexador:
    path = arquivo_do_plugin(pacote)
    if path is None:
        return PluginGenerico()
    nome = f"plugin_{pacote.id}_indexador"
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
    if not (isinstance(classe, type) and issubclass(classe, PluginIndexador)):
        raise pacotes.PacoteInvalido(f"{pacote.id}: {path} não define `Plugin`, subclasse de PluginIndexador")
    return classe()
