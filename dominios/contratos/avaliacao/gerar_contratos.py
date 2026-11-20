"""Gera os contratos FICTÍCIOS do domínio de contratos e o gabarito deles.

    uv run --no-project --with reportlab python dominios/contratos/avaliacao/gerar_contratos.py

As partes, os valores e as datas são inventados. Cada contrato tem um layout diferente (cláusulas
em texto, um quadro-resumo em tabela, datas por extenso) e um caso que testa o sistema:

- limpeza (012/2024): tudo informado, em cláusulas;
- fornecimento (2024-07): o quadro-resumo é uma tabela, e a vigência termina antes de começar
  (inconsistência proposital da fonte);
- software (33/2024): a assinatura por extenso, a vigência só em meses (o fim não está escrito: é
  "não encontrado", sem conta), e a única multa é a de mora, que não é a multa por rescisão;
- minuta: um modelo em branco, com as cláusulas e sem os valores: tudo é "não encontrado".

Como os valores nascem aqui, o gabarito sai daqui também, e os dois nunca divergem. O PDF é
reprodutível (sem data nem identificador aleatório): o doc_id, o hash que o indexador calcula, é
gravado no gabarito.
"""
import hashlib
import json
from pathlib import Path

from reportlab import rl_config
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

AQUI = Path(__file__).parent
PDFS = AQUI / "pdfs"
ESTILOS = getSampleStyleSheet()
RODAPE = "Contrato fictício, gerado para testar o sistema. As partes e os valores não existem."
rl_config.invariant = 1


def _p(texto: str, estilo: str = "Normal") -> Paragraph:
    return Paragraph(texto, ESTILOS[estilo])


def _clausula(titulo: str, texto: str) -> list:
    return [_p(titulo, "Heading3"), _p(texto), Spacer(1, 6)]


def limpeza() -> list:
    return [
        _p("CONTRATO Nº 012/2024 - PRESTAÇÃO DE SERVIÇOS DE LIMPEZA", "Title"),
        _p("CONTRATANTE: Condomínio Edifício Aurora, inscrito no CNPJ sob o nº 00.000.000/0001-00, com sede na "
           "Rua das Acácias, 120, Belo Horizonte/MG."),
        _p("CONTRATADA: Brilho Serviços de Limpeza Ltda., inscrita no CNPJ sob o nº 00.000.000/0002-00."),
        Spacer(1, 8),
        *_clausula("CLÁUSULA PRIMEIRA - DO OBJETO",
                   "O objeto do presente contrato é a prestação de serviços de limpeza e conservação das áreas "
                   "comuns do condomínio, cinco dias por semana."),
        *_clausula("CLÁUSULA SEGUNDA - DO VALOR",
                   "O valor total do contrato é de R$ 120.000,00 (cento e vinte mil reais), pago em doze parcelas "
                   "mensais iguais."),
        *_clausula("CLÁUSULA TERCEIRA - DA VIGÊNCIA",
                   "O contrato vigora de 01/02/2024 a 31/01/2025, podendo ser prorrogado por acordo das partes."),
        *_clausula("CLÁUSULA QUARTA - DO REAJUSTE",
                   "Em caso de prorrogação, o valor será reajustado pela variação do IPCA dos doze meses anteriores."),
        PageBreak(),
        *_clausula("CLÁUSULA QUINTA - DAS OBRIGAÇÕES DA CONTRATADA",
                   "A CONTRATADA obriga-se a fornecer os materiais de limpeza e a manter seguro de responsabilidade "
                   "civil durante toda a vigência."),
        *_clausula("CLÁUSULA SEXTA - DA RESCISÃO",
                   "O contrato pode ser rescindido por qualquer das partes com aviso prévio de trinta dias. A parte "
                   "que rescindir sem justa causa pagará multa de 10% sobre o valor remanescente do contrato."),
        *_clausula("CLÁUSULA SÉTIMA - DO FORO",
                   "Fica eleito o foro da Comarca de Belo Horizonte/MG para dirimir as questões deste contrato."),
        _p("Belo Horizonte, 15/01/2024. Assinado pelas partes."),
        Spacer(1, 10), _p(RODAPE),
    ]


def _quadro(linhas: list[list[str]]) -> Table:
    tabela = Table(linhas, colWidths=[160, 300])
    tabela.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey)]))
    return tabela


def fornecimento() -> list:
    return [
        _p("CONTRATO DE FORNECIMENTO Nº 2024-07", "Title"),
        _p("Quadro-resumo", "Heading2"),
        _quadro([["Item", "Condição"],
                 ["Contratante", "Padaria Pão Dourado Ltda."],
                 ["Contratada", "Moinho Serra Azul S.A."],
                 ["Objeto", "Fornecimento mensal de farinha de trigo especial"],
                 ["Valor total", "R$ 84.500,00"],
                 ["Início da vigência", "01/07/2024"],
                 ["Fim da vigência", "30/06/2024"],
                 ["Reajuste", "IGP-M, a cada doze meses"]]),
        Spacer(1, 8),
        _p("Firmado em 20/06/2024."),
        PageBreak(),
        *_clausula("CLÁUSULA 1 - DAS OBRIGAÇÕES",
                   "A CONTRATADA obriga-se a entregar o produto até o quinto dia útil de cada mês, e a CONTRATANTE "
                   "a pagar cada entrega em até quinze dias."),
        *_clausula("CLÁUSULA 2 - DAS PENALIDADES E DA RESCISÃO",
                   "O descumprimento de qualquer cláusula permite a rescisão do contrato, com multa de 20% sobre o "
                   "valor total, devida pela parte que der causa."),
        *_clausula("CLÁUSULA 3 - DA RENOVAÇÃO",
                   "O contrato será renovado automaticamente por igual período, salvo manifestação contrária com "
                   "sessenta dias de antecedência."),
        *_clausula("CLÁUSULA 4 - DO FORO", "As partes elegem o foro da Comarca de Curitiba/PR."),
        Spacer(1, 10), _p(RODAPE),
    ]


def software() -> list:
    return [
        _p("CONTRATO DE LICENÇA DE USO DE SOFTWARE Nº 33/2024", "Title"),
        _p("Pelo presente instrumento, Clínica Vida Plena Ltda., doravante CONTRATANTE, e Nuvem Sistemas Ltda., "
           "doravante CONTRATADA, firmam este contrato em 10 de março de 2024, nas condições abaixo."),
        Spacer(1, 8),
        *_clausula("1. OBJETO", "O objeto deste contrato é a licença de uso do sistema de agendamento da CONTRATADA, "
                                "com suporte técnico em horário comercial."),
        *_clausula("2. PREÇO", "Pela licença, a CONTRATANTE pagará o valor total de R$ 36.000,00 (trinta e seis mil "
                               "reais), em doze parcelas mensais."),
        *_clausula("3. PRAZO", "O contrato terá duração de doze meses, contados a partir de 1º de abril de 2024."),
        *_clausula("4. ATRASO NO PAGAMENTO", "O atraso no pagamento sujeita a CONTRATANTE a multa de mora de 2% e "
                                            "juros de 1% ao mês sobre a parcela em atraso."),
        *_clausula("5. REAJUSTE", "As parcelas serão reajustadas anualmente pelo INPC."),
        *_clausula("6. LIMITAÇÃO DE RESPONSABILIDADE", "A responsabilidade da CONTRATADA fica limitada ao valor pago "
                                                       "nos últimos doze meses."),
        *_clausula("7. FORO", "Fica eleito o foro da Comarca de São Paulo/SP."),
        Spacer(1, 10), _p(RODAPE),
    ]


def minuta() -> list:
    """Uma minuta-modelo em branco: as cláusulas existem, os valores não. Tudo tem de sair "não
    encontrado", sem tirar valor das lacunas nem dos exemplos."""
    return [
        _p("MINUTA DE CONTRATO DE PRESTAÇÃO DE SERVIÇOS (MODELO)", "Title"),
        _p("CONTRATANTE: [nome ou razão social da contratante], inscrita no CNPJ sob o nº [__________]."),
        _p("CONTRATADA: [nome ou razão social da contratada], inscrita no CNPJ sob o nº [__________]."),
        Spacer(1, 8),
        *_clausula("CLÁUSULA PRIMEIRA - DO OBJETO", "O objeto do presente contrato é [descrever o serviço]."),
        *_clausula("CLÁUSULA SEGUNDA - DO VALOR", "O valor total do contrato é de R$ [______] ([valor por extenso])."),
        *_clausula("CLÁUSULA TERCEIRA - DA VIGÊNCIA", "O contrato vigora de [__/__/____] a [__/__/____]."),
        *_clausula("CLÁUSULA QUARTA - DO REAJUSTE", "O valor será reajustado pelo índice [indicar o índice] a cada doze "
                                                   "meses."),
        *_clausula("CLÁUSULA QUINTA - DA RESCISÃO", "A rescisão sem justa causa sujeita a parte que a der a multa de "
                                                   "[__]% sobre o valor remanescente."),
        *_clausula("CLÁUSULA SEXTA - DO FORO", "Fica eleito o foro da Comarca de [cidade/UF]."),
        _p("[Local], [data]. Assinaturas."),
        Spacer(1, 10), _p("Minuta-modelo fictícia, em branco, gerada para testar o sistema."),
    ]


def _campos(info: dict) -> dict:
    """Os campos do gabarito: (status informado com o valor e as páginas) ou não encontrado."""
    todos = ["contratante", "contratada", "objeto", "valor_total", "data_assinatura", "inicio_vigencia",
             "fim_vigencia", "multa_rescisao", "indice_reajuste", "foro"]
    return {campo: ({"status": "informado", **info[campo]} if campo in info else {"status": "nao_encontrado"})
            for campo in todos}


GABARITO = {
    "_como_foi_feito": "Contratos fictícios gerados por gerar_contratos.py: os valores nascem no gerador, e o "
                       "gabarito também. Texto: o que a resposta tem de conter; data em dd/mm/aaaa; valor em reais; "
                       "multa em percentual; página do arquivo PDF.",
    "documentos": {
        "contrato-limpeza.pdf": {"campos": _campos({
            "contratante": {"texto": "Condomínio Edifício Aurora", "paginas": [1]},
            "contratada": {"texto": "Brilho Serviços de Limpeza", "paginas": [1]},
            "objeto": {"texto": "limpeza", "paginas": [1]},
            "valor_total": {"valor": 120000, "unidade": "R$", "paginas": [1]},
            "data_assinatura": {"data": "15/01/2024", "paginas": [2]},
            "inicio_vigencia": {"data": "01/02/2024", "paginas": [1]},
            "fim_vigencia": {"data": "31/01/2025", "paginas": [1]},
            "multa_rescisao": {"valor": 10, "unidade": "%", "paginas": [2]},
            "indice_reajuste": {"texto": "IPCA", "paginas": [1]},
            "foro": {"texto": "Belo Horizonte", "paginas": [2]}}),
            "inconsistencias_na_fonte_esperadas": []},
        "contrato-fornecimento.pdf": {"campos": _campos({
            "contratante": {"texto": "Padaria Pão Dourado", "paginas": [1]},
            "contratada": {"texto": "Moinho Serra Azul", "paginas": [1]},
            "objeto": {"texto": "farinha", "paginas": [1]},
            "valor_total": {"valor": 84500, "unidade": "R$", "paginas": [1]},
            "data_assinatura": {"data": "20/06/2024", "paginas": [1]},
            "inicio_vigencia": {"data": "01/07/2024", "paginas": [1]},
            "fim_vigencia": {"data": "30/06/2024", "paginas": [1]},
            "multa_rescisao": {"valor": 20, "unidade": "%", "paginas": [2]},
            "indice_reajuste": {"texto": "IGP-M", "paginas": [1]},
            "foro": {"texto": "Curitiba", "paginas": [2]}}),
            "inconsistencias_na_fonte_esperadas": [{"pagina": 1, "contem": "30/06/2024"}]},
        "contrato-software.pdf": {"campos": _campos({
            "contratante": {"texto": "Clínica Vida Plena", "paginas": [1]},
            "contratada": {"texto": "Nuvem Sistemas", "paginas": [1]},
            "objeto": {"texto": "licença de uso", "paginas": [1]},
            "valor_total": {"valor": 36000, "unidade": "R$", "paginas": [1]},
            "data_assinatura": {"data": "10/03/2024", "paginas": [1]},
            "inicio_vigencia": {"data": "01/04/2024", "paginas": [1]},
            "indice_reajuste": {"texto": "INPC", "paginas": [1]},
            "foro": {"texto": "São Paulo", "paginas": [1]}}),
            "inconsistencias_na_fonte_esperadas": []},
        "contrato-minuta.pdf": {"campos": _campos({}), "inconsistencias_na_fonte_esperadas": []},
    },
    "perguntas": {
        "p1": {"deve_conter": ["012/2024", "120.000"], "fontes_de": ["contrato-limpeza.pdf"],
               "por_que": "o maior valor total é o do contrato de limpeza"},
        "p2": {"deve_conter": ["31/01/2025", "30/06/2024"], "fontes_de": ["contrato-limpeza.pdf",
                                                                          "contrato-fornecimento.pdf"],
               "por_que": "o de software não escreve o fim (só doze meses a partir de 1º de abril)"},
        "p3": {"deve_conter": ["10%", "20%"], "fontes_de": ["contrato-limpeza.pdf", "contrato-fornecimento.pdf"],
               "por_que": "o de software só tem multa de mora, que não é a de rescisão"},
        "p4": {"deve_conter": ["Belo Horizonte", "Curitiba", "São Paulo", "IPCA", "IGP-M", "INPC"],
               "fontes_de": ["contrato-limpeza.pdf", "contrato-fornecimento.pdf", "contrato-software.pdf"],
               "por_que": "foro e reajuste dos três"},
    },
}


def main() -> None:
    PDFS.mkdir(exist_ok=True)
    for nome, conteudo in (("contrato-limpeza.pdf", limpeza()), ("contrato-fornecimento.pdf", fornecimento()),
                           ("contrato-software.pdf", software()), ("contrato-minuta.pdf", minuta())):
        SimpleDocTemplate(str(PDFS / nome), pagesize=A4, title=nome).build(conteudo)
        GABARITO["documentos"][nome] = {
            "doc_id": hashlib.sha256((PDFS / nome).read_bytes()).hexdigest()[:16], **GABARITO["documentos"][nome]}
        print("gerado", PDFS / nome)
    (AQUI / "gabarito.json").write_text(json.dumps(GABARITO, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("gerado", AQUI / "gabarito.json")


if __name__ == "__main__":
    main()
