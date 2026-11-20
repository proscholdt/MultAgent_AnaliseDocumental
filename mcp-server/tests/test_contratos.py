"""O contrato das tools com os agentes, por domínio: nome, grupo, descrição e esquema dos argumentos,
como o modelo os vê no `tools/list` (as listas fechadas e as palavras de cada domínio já no lugar).

Uma mudança aqui muda o que o modelo vê. Se for de propósito, regrave os arquivos:

    ATUALIZAR_CONTRATOS=1 uv run pytest tests/test_contratos.py
"""
import json
import os
from pathlib import Path

import pytest

from app import pacotes
from app.governanca.catalogo import load_tools

PASTA = Path(__file__).parent / "contratos"


def _contrato(dominio: str) -> dict:
    tools = load_tools()
    return {name: tools[name].definition(dominio).model_dump(mode="json", exclude_none=True) for name in sorted(tools)}


@pytest.mark.parametrize("dominio", sorted(pacotes.todos()))
def test_contrato_das_tools_nao_mudou_sem_querer(dominio):
    arquivo = PASTA / f"tools-{dominio}.json"
    atual = _contrato(dominio)
    if os.environ.get("ATUALIZAR_CONTRATOS") == "1":
        PASTA.mkdir(exist_ok=True)
        arquivo.write_text(json.dumps(atual, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    gravado = json.loads(arquivo.read_text(encoding="utf-8"))
    removidas, novas = sorted(set(gravado) - set(atual)), sorted(set(atual) - set(gravado))
    mudadas = sorted(name for name in set(atual) & set(gravado) if atual[name] != gravado[name])
    assert not (removidas or novas or mudadas), (
        f"Contrato das tools de {dominio} mudou. Removidas: {removidas}; novas: {novas}; mudadas: {mudadas}. "
        "Se foi de propósito: ATUALIZAR_CONTRATOS=1 uv run pytest tests/test_contratos.py")


def test_nenhum_marcador_fica_sem_troca():
    """Toda palavra marcada ($entidade, $n_perguntas...) vira a palavra do domínio."""
    for dominio in pacotes.todos():
        texto = json.dumps(_contrato(dominio), ensure_ascii=False)
        assert "$" not in texto.replace("R$", ""), dominio
