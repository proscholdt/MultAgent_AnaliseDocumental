# 0003 — Indicadores, categorias e perguntas em YAML; fórmulas em Python

**Contexto.** Os 8 indicadores, as categorias e as 5 perguntas eram `Literal` no código: um
indicador novo exigia Python, regras e testes.

**Decisão.** `mcp-server/config/dominio.yaml` define os campos (termos, seção, menção, origem
exigida), as categorias e as perguntas, validado na subida; o esquema das tools sai dele. O
período vira entidade (`app/dominio/periodo.py`: anual, trimestre, acumulado), e dois documentos
da mesma empresa ficam juntos. As fórmulas (EBITDA, dívida líquida, alavancagem, capex) ficam em
`app/regras/formulas.py`: declarar fórmulas com condições e exceções em YAML seria uma linguagem
nova, mais difícil de testar que o Python.

**Consequência.** Indicador ou pergunta nova: YAML, prompt do extrator e gabarito. Fórmula nova
continua exigindo código e teste. O suporte trimestral está no modelo e na conferência da coluna;
falta um gabarito trimestral para medir.
