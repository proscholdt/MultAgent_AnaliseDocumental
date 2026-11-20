"""Gera os laudos FICTÍCIOS do domínio de laudos e o gabarito deles.

    uv run --with reportlab python dominios/laudos/avaliacao/gerar_laudos.py

Pacientes, laboratórios e resultados são inventados: laudo é dado sensível (LGPD), e nenhum exame
real entra no repositório. Cada laudo tem um layout diferente (tabela com a unidade na célula,
blocos de texto, duas tabelas), alguns resultados fora da faixa de referência, observações e uma
inconsistência proposital (a liberação antes da coleta). Como os valores nascem aqui, o gabarito
sai daqui também, e os dois nunca divergem.
"""
import hashlib
import json
from pathlib import Path

from reportlab import rl_config
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

AQUI = Path(__file__).parent
PDFS = AQUI / "pdfs"
ESTILOS = getSampleStyleSheet()
RODAPE = "Laudo fictício, gerado para testar o sistema. Não corresponde a nenhum paciente real."
# Sem data nem identificador aleatório no PDF: gerar de novo dá os mesmos bytes, e o doc_id (o hash
# que o indexador calcula) continua o do gabarito.
rl_config.invariant = 1


def _p(texto: str, estilo: str = "Normal") -> Paragraph:
    return Paragraph(texto, ESTILOS[estilo])


def _tabela(linhas: list[list[str]]) -> Table:
    tabela = Table(linhas, hAlign="LEFT", colWidths=[170, 110, 190])
    tabela.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold")]))
    return tabela


def _cabecalho(laboratorio: str, paciente: str, coleta: str, liberacao: str, medico: str) -> list:
    return [_p(laboratorio, "Title"),
            _p(f"Paciente: {paciente}"),
            _p(f"Data da coleta: {coleta}"),
            _p(f"Laudo liberado em: {liberacao}"),
            _p(f"Médico solicitante: {medico}"),
            Spacer(1, 12)]


def maria() -> list:
    return [*_cabecalho("Laboratório Vida Clínica", "Maria Souza", "12/03/2024", "13/03/2024", "Dra. Helena Prado"),
            _p("Bioquímica", "Heading2"),
            _tabela([["Exame", "Resultado", "Valores de referência"],
                     ["Glicose", "92 mg/dL", "70 a 99 mg/dL"],
                     ["Hemoglobina glicada (HbA1c)", "5,4 %", "inferior a 5,7 %"]]),
            Spacer(1, 12),
            _p("Lipidograma", "Heading2"),
            _tabela([["Exame", "Resultado", "Valores de referência"],
                     ["Colesterol total", "231 mg/dL", "inferior a 190 mg/dL"],
                     ["Colesterol HDL", "48 mg/dL", "superior a 40 mg/dL"],
                     ["Colesterol LDL", "158 mg/dL", "inferior a 130 mg/dL"],
                     ["Triglicerídeos", "125 mg/dL", "inferior a 150 mg/dL"]]),
            Spacer(1, 12),
            _p("Material: soro. Método: enzimático colorimétrico."),
            _p("Observação: amostra levemente lipêmica."),
            _p("Recomenda-se repetir o lipidograma após jejum de 12 horas."),
            Spacer(1, 12),
            _p("Responsável técnico: Dr. Paulo Mendes, CRF 12345"),
            _p(RODAPE)]


def joao() -> list:
    def bloco(exame: str, material: str, metodo: str, resultado: str, referencia: str) -> list:
        return [_p(exame, "Heading3"), _p(f"Material: {material}. Método: {metodo}."),
                _p(f"Resultado: {resultado}"), _p(f"Valores de referência: {referencia}"), Spacer(1, 8)]

    # A liberação (04/06) antes da coleta (05/06): a inconsistência que o validador deve achar.
    return [*_cabecalho("Laboratório Santa Luzia", "João Pereira", "05/06/2024", "04/06/2024", "Dr. Carlos Nunes"),
            *bloco("GLICOSE", "soro", "enzimático", "118 mg/dL", "70 a 99 mg/dL"),
            *bloco("HEMOGLOBINA GLICADA (HbA1c)", "sangue total", "HPLC", "6,3 %", "inferior a 5,7 %"),
            *bloco("CREATININA", "soro", "cinético", "1,05 mg/dL", "0,70 a 1,30 mg/dL"),
            *bloco("TRIGLICERÍDEOS", "soro", "enzimático", "210 mg/dL", "inferior a 150 mg/dL"),
            _p("Observação: paciente informou jejum de 8 horas."),
            Spacer(1, 12),
            _p("Responsável técnico: Dra. Lúcia Ramos, CRBM 54321"),
            _p(RODAPE)]


def ana() -> list:
    return [*_cabecalho("Laboratório Bem Estar", "Ana Lima", "20/09/2024", "21/09/2024", "Dr. Rafael Costa"),
            _p("Hemograma", "Heading2"),
            _tabela([["Exame", "Resultado", "Valores de referência"],
                     ["Eritrócitos", "4,1 milhões/mm³", "4,0 a 5,2 milhões/mm³"],
                     ["Hemoglobina", "11,2 g/dL", "12,0 a 15,5 g/dL"],
                     ["Hematócrito", "35 %", "36 a 46 %"]]),
            Spacer(1, 12),
            _p("Bioquímica", "Heading2"),
            _tabela([["Exame", "Resultado", "Valores de referência"],
                     ["Glicose", "85 mg/dL", "70 a 99 mg/dL"],
                     ["Colesterol total", "178 mg/dL", "inferior a 190 mg/dL"],
                     ["Colesterol HDL", "62 mg/dL", "superior a 40 mg/dL"],
                     ["Colesterol LDL", "96 mg/dL", "inferior a 130 mg/dL"],
                     ["Triglicerídeos", "98 mg/dL", "inferior a 150 mg/dL"],
                     ["Creatinina", "0,78 mg/dL", "0,50 a 1,10 mg/dL"]]),
            Spacer(1, 12),
            _p("Observação: resultado de hemoglobina confirmado em segunda dosagem."),
            Spacer(1, 12),
            _p("Responsável técnico: Dr. Paulo Mendes, CRF 12345"),
            _p(RODAPE)]


def _campos(encontrados: dict[str, float]) -> dict:
    todos = ["glicose", "hemoglobina_glicada", "colesterol_total", "hdl", "ldl", "triglicerideos", "creatinina",
             "hemoglobina"]
    unidades = {"hemoglobina_glicada": "%", "hemoglobina": "g/dL"}
    return {campo: ({"status": "informado", "valor": encontrados[campo], "unidade": unidades.get(campo, "mg/dL"),
                     "paginas": [1]} if campo in encontrados else {"status": "nao_encontrado"}) for campo in todos}


GABARITO = {
    "_como_foi_feito": "Laudos fictícios gerados por gerar_laudos.py: os valores nascem no gerador, e o gabarito "
                       "também. Valor em número, na unidade do pacote; página do arquivo PDF.",
    "documentos": {
        "laudo-maria.pdf": {"campos": _campos({"glicose": 92, "hemoglobina_glicada": 5.4, "colesterol_total": 231,
                                                    "hdl": 48, "ldl": 158, "triglicerideos": 125}),
                            "inconsistencias_na_fonte_esperadas": []},
        "laudo-joao.pdf": {"campos": _campos({"glicose": 118, "hemoglobina_glicada": 6.3, "creatinina": 1.05,
                                                   "triglicerideos": 210}),
                           "inconsistencias_na_fonte_esperadas": [{"pagina": 1, "contem": "04/06/2024"}]},
        "laudo-ana.pdf": {"campos": _campos({"glicose": 85, "colesterol_total": 178, "hdl": 62, "ldl": 96,
                                                  "triglicerideos": 98, "creatinina": 0.78, "hemoglobina": 11.2}),
                          "inconsistencias_na_fonte_esperadas": []},
        # Um documento público que não é laudo: o consenso da SBAC sobre o laudo do perfil lipídico
        # (https://www.sbac.org.br/wp-content/uploads/2017/09/ConsensoOficial_PerfilLipidico_2016_v13.pdf,
        # não versionado aqui). Tem tabelas de valores de referência e nenhum resultado de paciente:
        # tudo tem de sair "não encontrado", sem tirar resultado das tabelas de referência.
        "consenso-perfil-lipidico-sbac.pdf": {"doc_id": "34f35b0636d713dd", "campos": _campos({}),
                                              "inconsistencias_na_fonte_esperadas": []},
    },
    "perguntas": {
        "p1": {"deve_conter": ["Maria", "João", "Ana"], "fontes_de": ["laudo-maria.pdf", "laudo-joao.pdf", "laudo-ana.pdf"],
               "por_que": "fora da faixa: colesterol total e LDL (Maria); glicose, HbA1c e triglicerídeos (João); "
                          "hemoglobina (Ana)"},
        "p2": {"deve_conter": ["João", "118"], "fontes_de": ["laudo-joao.pdf"], "por_que": "a maior glicose é a do João"},
        "p3": {"deve_conter": ["Maria", "Ana", "231", "178"], "fontes_de": ["laudo-maria.pdf", "laudo-ana.pdf"],
               "por_que": "o perfil lipídico completo está nos laudos da Maria e da Ana; o do João só tem triglicerídeos"},
        "p4": {"deve_conter_um_de": ["lipêmica", "jejum", "segunda dosagem"], "fontes_de": ["laudo-maria.pdf"],
               "por_que": "amostra lipêmica e repetir em jejum (Maria); jejum de 8 horas (João); segunda dosagem (Ana)"},
    },
}


def main() -> None:
    PDFS.mkdir(exist_ok=True)
    for nome, conteudo in (("laudo-maria.pdf", maria()), ("laudo-joao.pdf", joao()), ("laudo-ana.pdf", ana())):
        SimpleDocTemplate(str(PDFS / nome), pagesize=A4, title=nome).build(conteudo)
        # O avaliador reconhece o documento pelo doc_id, não pelo nome do arquivo.
        GABARITO["documentos"][nome] = {
            "doc_id": hashlib.sha256((PDFS / nome).read_bytes()).hexdigest()[:16], **GABARITO["documentos"][nome]}
        print("gerado", PDFS / nome)
    (AQUI / "gabarito.json").write_text(json.dumps(GABARITO, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("gerado", AQUI / "gabarito.json")


if __name__ == "__main__":
    main()
