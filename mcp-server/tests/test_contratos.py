"""O contrato das tools com os agentes: nome, grupo, descrição e esquema dos argumentos.

Uma mudança aqui muda o que o modelo vê. Se for de propósito, regrave o arquivo:

    ATUALIZAR_CONTRATOS=1 uv run pytest tests/test_contratos.py
"""
import json
import os
from pathlib import Path

from app.governanca.catalogo import load_tools

ARQUIVO = Path(__file__).parent / "contratos" / "tools.json"


def _contrato() -> dict:
    tools = load_tools()
    return {name: tools[name].definition().model_dump(mode="json", exclude_none=True) for name in sorted(tools)}


def test_contrato_das_tools_nao_mudou_sem_querer():
    atual = _contrato()
    if os.environ.get("ATUALIZAR_CONTRATOS") == "1":
        ARQUIVO.write_text(json.dumps(atual, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    gravado = json.loads(ARQUIVO.read_text(encoding="utf-8"))
    removidas, novas = sorted(set(gravado) - set(atual)), sorted(set(atual) - set(gravado))
    mudadas = sorted(name for name in set(atual) & set(gravado) if atual[name] != gravado[name])
    assert not (removidas or novas or mudadas), (
        f"Contrato das tools mudou. Removidas: {removidas}; novas: {novas}; mudadas: {mudadas}. "
        "Se foi de propósito: ATUALIZAR_CONTRATOS=1 uv run pytest tests/test_contratos.py")
