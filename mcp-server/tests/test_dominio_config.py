"""O domínio vem de config/dominio.yaml: um indicador novo é só YAML, e um arquivo incoerente
não sobe."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from app.regras.campos import CAMPOS_DAS_FORMULAS, DOMINIO_PATH, carregar

RAIZ = Path(__file__).resolve().parents[1]


def _gravar(tmp_path: Path, dados: dict) -> Path:
    path = tmp_path / "dominio.yaml"
    path.write_text(yaml.safe_dump(dados, allow_unicode=True), encoding="utf-8")
    return path


def _dados() -> dict:
    return yaml.safe_load(DOMINIO_PATH.read_text(encoding="utf-8"))


def test_o_arquivo_do_repositorio_e_valido():
    dominio = carregar()
    assert CAMPOS_DAS_FORMULAS <= {campo.nome for campo in dominio.campos}
    assert [p.id for p in dominio.perguntas] == ["p1", "p2", "p3", "p4", "p5"]


def test_indicador_novo_so_com_yaml_chega_ao_esquema_das_tools(tmp_path):
    dados = _dados()
    dados["campos"].append({"nome": "patrimonio_liquido", "rotulo": "Patrimônio líquido", "tipo": "valor",
                            "principal": True, "termos": ["patrimônio líquido"], "secao": "balanço",
                            "mencao": ["patrimonio liquido"], "demonstracao": True})
    dados["perguntas"].append({"id": "p6", "enunciado": "Qual empresa tem o maior patrimônio líquido?"})
    codigo = ("import json; from app.governanca.catalogo import load_tools; t = load_tools(); "
              "print(json.dumps({'campo': t['salvar_indicador'].definition().input_schema['properties']['campo'], "
              "'pergunta': t['salvar_resposta'].definition().input_schema['properties']['pergunta_id']}))")
    saida = subprocess.run([sys.executable, "-c", codigo], cwd=RAIZ, capture_output=True, text=True, check=True,
                           env={**os.environ, "DOMINIO_PATH": str(_gravar(tmp_path, dados))})
    esquema = json.loads(saida.stdout.strip().splitlines()[-1])
    assert "patrimonio_liquido" in esquema["campo"]["enum"]
    assert "p6" in esquema["pergunta"]["enum"] and "livre" in esquema["pergunta"]["enum"]


@pytest.mark.parametrize("estrago, motivo", [
    (lambda d: d["campos"].pop(1), "fórmulas"),
    (lambda d: d["campos"].append(dict(d["campos"][0])), "repetido"),
    (lambda d: d["campos"][0].update(tipo="moeda"), "tipo"),
    (lambda d: d["campos"][0].update(rotullo="x"), "rotullo"),
    (lambda d: d["perguntas"].append({"id": "livre", "enunciado": "x"}), "livre"),
])
def test_arquivo_incoerente_nao_sobe(tmp_path, estrago, motivo):
    dados = _dados()
    estrago(dados)
    with pytest.raises(Exception, match=motivo):
        carregar(_gravar(tmp_path, dados))
