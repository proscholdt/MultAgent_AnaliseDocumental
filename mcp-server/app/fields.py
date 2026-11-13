"""Os campos da extração: os 8 que o teste pede e os valores de apoio que servem de operando
para o que o servidor calcula (EBITDA, dívida líquida, capex)."""
from dataclasses import dataclass
from typing import Literal

Campo = Literal[
    "receita_liquida", "ebitda", "margem_ebitda", "lucro_liquido", "divida_liquida", "alavancagem",
    "fluxo_caixa_operacional", "capex",
    "resultado_antes_financeiro", "depreciacao_amortizacao", "emprestimos_circulante",
    "emprestimos_nao_circulante", "caixa_equivalentes", "capex_imobilizado", "capex_intangivel",
    "capex_imobilizado_e_intangivel",
]
CampoPrincipal = Literal[
    "receita_liquida", "ebitda", "margem_ebitda", "lucro_liquido", "divida_liquida", "alavancagem",
    "fluxo_caixa_operacional", "capex",
]


@dataclass(frozen=True)
class Field:
    name: str
    label: str
    kind: str  # "valor" (dinheiro) | "percentual" | "multiplo"
    main: bool
    # Termos procurados no documento quando o agente diz que o campo não existe. Se algum
    # aparecer, ele tem de dizer por que aquelas páginas não servem.
    terms: tuple[str, ...] = ()
    # Palavra que a seção de origem deve ter quando o valor vem de tabela (resultado, fluxo,
    # balanço). Valor de outra seção entra, mas gera alerta.
    section: str | None = None
    # O que a linha da tabela (ou o trecho) de origem tem de mencionar. "Alavancagem" e "índice de
    # endividamento" nomeiam contas diferentes em cada empresa; o campo deste sistema é dívida
    # líquida / EBITDA, então a fonte tem de falar em EBITDA. Comparação sem acento.
    # Um termo com "+" exige todas as partes ("antes+financeir").
    mention: tuple[str, ...] = ()
    # Valor de demonstração: vem da tabela da demonstração, não de uma frase. O texto do
    # relatório da administração arredonda e, às vezes, perde o sinal.
    statement: bool = False


_FIELDS = [
    Field("receita_liquida", "Receita líquida", "valor", True,
          ("receita líquida", "receita operacional líquida"), "resultado", statement=True, mention=("receita",)),
    Field("ebitda", "EBITDA", "valor", True, ("ebitda", "lajida"), mention=("ebitda", "lajida")),
    Field("margem_ebitda", "Margem EBITDA", "percentual", True,
          ("margem ebitda", "margem lajida", "margem do ebitda"), mention=("ebitda", "lajida")),
    Field("lucro_liquido", "Lucro líquido", "valor", True,
          ("lucro líquido", "prejuízo líquido", "resultado líquido", "prejuízo do exercício"), "resultado",
          statement=True, mention=("lucro", "prejuizo", "resultado liquido", "resultado do exercicio",
                                   "resultado do periodo")),
    Field("divida_liquida", "Dívida líquida", "valor", True, ("dívida líquida", "endividamento líquido"),
          mention=("divida liquida", "endividamento liquido")),
    Field("alavancagem", "Alavancagem (dívida líquida / EBITDA)", "multiplo", True,
          ("alavancagem", "dívida líquida/ebitda", "dívida líquida / ebitda", "dívida líquida sobre ebitda"),
          mention=("ebitda", "lajida")),
    Field("fluxo_caixa_operacional", "Fluxo de caixa operacional", "valor", True,
          ("atividades operacionais",), "fluxo", statement=True,
          mention=("caixa+operacionais", "caixa+operacoes")),
    Field("capex", "Capex", "valor", True,
          ("capex", "aquisição de imobilizado", "aquisições de imobilizado", "aquisição de ativo imobilizado",
           "aquisições de bens do ativo imobilizado", "adições ao imobilizado"), "fluxo"),
    Field("resultado_antes_financeiro", "Resultado antes do resultado financeiro", "valor", False,
          section="resultado", mention=("antes+financeir", "lucro operacional", "resultado operacional")),
    Field("depreciacao_amortizacao", "Depreciação e amortização", "valor", False, mention=("deprecia",)),
    Field("emprestimos_circulante", "Empréstimos e financiamentos (circulante)", "valor", False, section="balanço",
          mention=("emprestimo", "financiamento")),
    Field("emprestimos_nao_circulante", "Empréstimos e financiamentos (não circulante)", "valor", False,
          section="balanço", mention=("emprestimo", "financiamento")),
    # Na nota de caixa, "Caixa e bancos" é um componente; o valor é o da linha do total.
    Field("caixa_equivalentes", "Caixa e equivalentes de caixa", "valor", False, section="balanço",
          mention=("caixa e equivalente",)),
    Field("capex_imobilizado", "Aquisição de imobilizado", "valor", False, section="fluxo", mention=("imobilizado",)),
    Field("capex_intangivel", "Aquisição de intangível", "valor", False, section="fluxo", mention=("intangivel",)),
    # Algumas empresas têm uma linha só no fluxo de caixa ("Imobilizado e intangível"). Ela entra
    # aqui, e não nos dois campos acima: gravada duas vezes, o capex sairia dobrado.
    Field("capex_imobilizado_e_intangivel", "Aquisição de imobilizado e intangível (linha única)", "valor", False,
          section="fluxo", mention=("imobilizado+intangivel",)),
]
FIELDS: dict[str, Field] = {field.name: field for field in _FIELDS}
MAIN_FIELDS: tuple[str, ...] = tuple(field.name for field in _FIELDS if field.main)

# Categorias da análise qualitativa (item 3 do teste) e os termos procurados quando o agente
# diz que o documento não traz aquela informação.
CATEGORIES: dict[str, tuple[str, tuple[str, ...]]] = {
    "motivo_resultado": ("Principais motivos do resultado", ()),
    "risco": ("Riscos e pontos de atenção", ("risco",)),
    "guidance": ("Guidance e perspectivas", ("guidance", "perspectiva", "projeç", "expectativa")),
    # Termos específicos: "aquisição" sozinha casaria com a linha de capex do fluxo de caixa, e
    # "contingência", com a nota de provisões de qualquer demonstração.
    "evento_relevante": ("Eventos relevantes", ("evento subsequente", "eventos subsequentes",
                                                "combinação de negócios", "reestruturaç",
                                                "recuperação judicial")),
    "tom": ("Tom geral da administração", ()),
}
Categoria = Literal["motivo_resultado", "risco", "guidance", "evento_relevante", "tom"]

# As 5 perguntas que o teste manda rodar e entregar. As respostas a elas entram no relatório.
QUESTIONS: dict[str, str] = {
    "p1": "Qual empresa teve a maior margem EBITDA no período?",
    "p2": "O que a administração de cada empresa apontou como principal motivo da variação da receita?",
    "p3": "Alguma das empresas está com alavancagem acima de 3x? Qual?",
    "p4": "Quais riscos são citados por mais de uma empresa?",
    "p5": "Qual é o guidance de cada empresa para o próximo ano?",
}
