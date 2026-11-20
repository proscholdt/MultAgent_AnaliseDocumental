"""O domínio vem do pacote (dominios/<id>/dominio.yaml): um indicador novo é só YAML, um pacote
incoerente não sobe, e um domínio que não é financeiro carrega sem as fórmulas."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from app import pacotes
from app.regras.campos import carregar, conferir, dominio
from tests.conftest import financeiro

RAIZ = Path(__file__).resolve().parents[1]


def _copia(tmp_path: Path, mudar=None, dominio: str = "financeiro") -> Path:
    """Uma cópia de dominios/ com o pacote alterado; devolve a pasta do pacote."""
    destino = tmp_path / "dominios"
    shutil.copytree(pacotes.pasta_dos_dominios(), destino)
    arquivo = destino / dominio / "dominio.yaml"
    dados = yaml.safe_load(arquivo.read_text(encoding="utf-8"))
    if mudar:
        mudar(dados)
    arquivo.write_text(yaml.safe_dump(dados, allow_unicode=True), encoding="utf-8")
    return destino / dominio


def test_o_pacote_financeiro_do_repositorio_e_valido():
    pacote = carregar()
    assert pacote.id == "financeiro" and pacote.plugin == "financeiro"
    assert financeiro().formulas.CAMPOS_DAS_FORMULAS <= {campo.nome for campo in pacote.campos}
    assert [p.id for p in pacote.perguntas] == ["p1", "p2", "p3", "p4", "p5"]
    assert (pacote.metadado("entidade").nome, pacote.metadado("recorte").nome) == ("empresa", "periodo")
    # O código do domínio é o plugin do pacote (dominios/financeiro/plugin/mcp).
    assert dominio("financeiro").plugin.nome == "financeiro"


def test_indicador_novo_so_com_yaml_chega_ao_esquema_das_tools(tmp_path):
    def mudar(dados):
        dados["campos"].append({"nome": "patrimonio_liquido", "rotulo": "Patrimônio líquido", "tipo": "valor",
                                "principal": True, "termos": ["patrimônio líquido"], "secao": "balanço",
                                "mencao": ["patrimonio liquido"], "demonstracao": True})
        dados["perguntas"].append({"id": "p6", "enunciado": "Qual empresa tem o maior patrimônio líquido?"})

    pasta = _copia(tmp_path, mudar)
    codigo = ("import json; from app.governanca.catalogo import load_tools; t = load_tools(); "
              "print(json.dumps({'campo': t['salvar_indicador'].definition().input_schema['properties']['campo'], "
              "'pergunta': t['salvar_resposta'].definition().input_schema['properties']['pergunta_id']}))")
    saida = subprocess.run([sys.executable, "-c", codigo], cwd=RAIZ, capture_output=True, text=True, check=True,
                           env={**os.environ, "DOMINIOS_PATH": str(pasta.parent)})
    esquema = json.loads(saida.stdout.strip().splitlines()[-1])
    assert "patrimonio_liquido" in esquema["campo"]["enum"]
    assert "p6" in esquema["pergunta"]["enum"] and "livre" in esquema["pergunta"]["enum"]


@pytest.mark.parametrize("estrago, motivo", [
    (lambda d: d["campos"].append(dict(d["campos"][0])), "repetido"),
    (lambda d: d["campos"][0].update(tipo="moeda"), "tipo"),
    (lambda d: d["campos"][0].update(rotullo="x"), "rotullo"),
    (lambda d: d["perguntas"].append({"id": "livre", "enunciado": "x"}), "livre"),
    (lambda d: d["metadados"][0].update(papel=None), "entidade"),
    (lambda d: d.update(id="contabil"), "nome da pasta"),
    (lambda d: d["entidade"].update(nome="Empresa X"), "nome inválido"),
])
def test_pacote_incoerente_nao_sobe(tmp_path, estrago, motivo):
    with pytest.raises(pacotes.PacoteInvalido, match=motivo):
        pacotes.ler(_copia(tmp_path, estrago))


def test_o_plugin_financeiro_exige_os_campos_das_formulas(tmp_path):
    pacote = pacotes.ler(_copia(tmp_path, lambda d: d["campos"].pop(1)))
    with pytest.raises(pacotes.PacoteInvalido, match="fórmulas"):
        conferir(pacote)


def test_o_pacote_laudos_do_repositorio_carrega_sem_plugin():
    """Um domínio novo é só uma pasta: sem as fórmulas financeiras, com a própria entidade."""
    laudos = carregar("laudos")
    assert laudos.plugin is None and laudos.metadado("entidade").nome == "paciente"
    assert laudos.recorte.tipo == "data" and laudos.metadado("recorte").nome == "data_coleta"
    assert {c.unidade for c in laudos.campos} >= {"mg/dL", "g/dL", "%"}
    assert not financeiro().formulas.CAMPOS_DAS_FORMULAS & {c.nome for c in laudos.campos}
    assert dominio("laudos").plugin.nome == "generico"  # sem plugin: só os portões do núcleo


def test_sem_o_pacote_padrao_nada_sobe(tmp_path):
    vazia = tmp_path / "vazia"
    vazia.mkdir()
    with pytest.raises(pacotes.PacoteInvalido, match="padrão"):
        pacotes.todos.__wrapped__(vazia)
