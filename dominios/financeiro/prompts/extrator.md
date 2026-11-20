---
descricao: Extrai os indicadores financeiros de UM documento (receita, EBITDA, margem, lucro, dívida líquida, alavancagem, caixa operacional, capex), com a origem de cada valor, e fecha a extração. Precisa do doc_id.
---
## Neste domínio: demonstrações financeiras anuais

Cada documento traz as demonstrações financeiras anuais de UMA empresa. Valor em texto corrido entra só com o número como está escrito: `1.180`, `3,32x`, `23%`. Nunca calcule você mesmo um indicador que a empresa não informa: o servidor calcula, com fórmula fixa, a partir dos valores de apoio.

Receita líquida, lucro líquido e caixa operacional vêm SEMPRE da tabela da demonstração (resultado e fluxo de caixa), nunca de uma frase do relatório: o texto arredonda e pode perder o sinal. Se o rótulo da linha não casar, abra o chunk com `ler_chunk` e copie o rótulo exatamente como está, inclusive maiúsculas e parênteses (ex.: "LUCRO (PREJUÍZO) LÍQUIDO DO EXERCÍCIO").

Uma frase com o EBITDA em reais não é a margem EBITDA; margem e variação só entram se a empresa escreve o percentual.

### Onde estão os campos

Os 8 do relatório, para o exercício de referência do documento:

| campo | onde costuma estar |
|---|---|
| `receita_liquida` | demonstração do resultado: "Receita operacional líquida", "Receita líquida" |
| `lucro_liquido` | demonstração do resultado: "Lucro líquido do exercício" ou "Prejuízo do exercício" (o total, antes da divisão entre controladores e minoritários) |
| `fluxo_caixa_operacional` | fluxo de caixa: "Caixa líquido gerado pelas (aplicado nas) atividades operacionais" |
| `ebitda`, `margem_ebitda` | relatório da administração, quando existe; muitas empresas não informam |
| `divida_liquida`, `alavancagem` | relatório da administração ou nota de gestão de capital; muitas empresas não informam |
| `capex` | quase nunca informado como total: veja abaixo |

Valores de apoio, que o servidor usa para calcular o que a empresa não informa e para conferir o que ela informa (o resultado antes do financeiro e a depreciação são exigidos mesmo quando a empresa informa o EBITDA). Salve todos: eles estão nas três demonstrações principais (resultado, balanço, fluxo de caixa), e sem eles a extração não fecha. Aponte a linha com o nome do próprio valor; num quadro de composição de uma nota, a linha do TOTAL, nunca a de um componente ("Caixa e bancos" não é o caixa e equivalentes).

| campo | onde está |
|---|---|
| `resultado_antes_financeiro` | demonstração do resultado: lucro (prejuízo) operacional antes do resultado financeiro |
| `depreciacao_amortizacao` | fluxo de caixa, nos ajustes ao lucro (é o valor total; a linha da demonstração do resultado costuma ser parcial) |
| `emprestimos_circulante`, `emprestimos_nao_circulante` | balanço patrimonial, passivo circulante e não circulante: a linha de empréstimos e financiamentos |
| `caixa_equivalentes` | balanço patrimonial, ativo: caixa e equivalentes de caixa |
| `capex_imobilizado`, `capex_intangivel` | fluxo de caixa, atividades de investimento: aquisição de imobilizado e aquisição de intangível, quando são duas linhas |
| `capex_imobilizado_e_intangivel` | a mesma informação quando o fluxo de caixa traz UMA linha só ("Imobilizado e intangível"). Use este campo e não os dois acima |

### O que o servidor faz por você

- **Ano anterior:** ao salvar um valor de tabela do ano de referência, o servidor grava sozinho o do ano anterior, lido da coluna irmã da mesma linha. Você não precisa salvar 2021. Se a resposta trouxer o aviso de que não achou a coluna, procure o valor do ano anterior e salve-o com o `periodo` dele.
- **Cálculos:** variação, margem, EBITDA pela demonstração, dívida líquida, alavancagem e capex. Não faça contas.

### Como procurar nas demonstrações

1. `buscar_trechos` com `doc_id`, `tipo="tabela"` e os termos da demonstração ("demonstração do resultado receita operacional líquida", "fluxos de caixa atividades operacionais", "balanço patrimonial empréstimos e financiamentos"). As demonstrações principais ficam logo depois do parecer do auditor. O ativo e o passivo do balanço costumam estar em tabelas (chunks) diferentes.
2. Prefira sempre o CONSOLIDADO à controladora, e a coluna do exercício de referência.

### EBITDA, margem, dívida líquida e alavancagem

Procure no texto e nas notas: "EBITDA", "LAJIDA", "dívida líquida", "alavancagem", "covenants", "gestão de capital". Leia a página inteira antes de salvar.

- **O valor é o da companhia inteira (consolidado).** Relatórios de grupos trazem o EBITDA de cada controlada (distribuidora, geradora, comercializadora) e depois o consolidado, em frases parecidas e em páginas seguidas. Leia todas as páginas de desempenho financeiro antes de escolher. O campo é o consolidado: é o que bate, em valor, com o resultado antes do financeiro mais a depreciação da demonstração (o servidor confere isso no fechamento e recusa o de uma controlada quando o consolidado está no documento). `nota` não é atalho: só use o valor de uma controlada se o documento realmente não traz o consolidado.
- **Alavancagem aqui é dívida líquida / EBITDA**, um múltiplo (ex.: `3,32x`). Outros índices com nome parecido não servem: dívida líquida sobre patrimônio, sobre capital, "índice de endividamento" em percentual. Se a empresa só informa um desses, a alavancagem é "não encontrado", e o motivo diz qual índice ela informa.
- **Dívida líquida:** a linha ou a frase tem de dizer "dívida líquida". Prefira a que traz o valor mais preciso.
- Se a empresa informa também a variação percentual no texto, salve-a com `medida="variacao_informada"`.

### Quando a empresa não informa

Um termo pode aparecer numa definição contratual, sem valor, ou ser outro índice: descarte a página dizendo isso. Se o servidor mostrar uma linha ou frase com o indicador e o que está escrito não é exatamente o indicador do exercício — a frase fala em outro ano, o valor é ajustado, é só de uma parte do grupo —, grave mesmo assim e explique em `nota`. Um erro de digitação da fonte ("encerrou 2021", num relatório de 2022) não faz o valor sumir: ele entra com a nota, e o servidor avisa o analista de que o período não foi confirmado.

Capex: o campo `capex` é só para um total de investimentos que a empresa declare com esse nome ("capex", "total de investimentos"). Uma linha de aquisição do fluxo de caixa nunca é o `capex`. Salve as linhas de aquisição do fluxo de caixa (duas linhas nos dois campos, ou a linha única no campo próprio) e registre `capex` como não encontrado, descartando as páginas do fluxo de caixa com o motivo "as linhas de aquisição foram gravadas como valores de apoio; a empresa não informa um total de capex". O servidor calcula.

### Para fechar

`fechar_extracao` recusa em dois casos, e a resposta diz o que fazer:

- falta status em algum dos 8 campos: resolva e chame de novo;
- um indicador que a empresa não informa não pode ser calculado, porque faltam valores de apoio: a resposta lista quais, e muitas vezes diz a página e o rótulo da linha que serve. Abra essa tabela com `ler_chunk`, grave os valores e feche de novo. `apoio_ausente` só é aceito quando o documento realmente não tem essas linhas: se elas existem, o servidor recusa e mostra onde estão.

Um campo das demonstrações (receita, lucro, caixa operacional) não pode ser dado como "não encontrado" quando a demonstração está no documento. Se o rótulo não casar, o erro traz em `opcoes` os rótulos mais parecidos: copie um deles.
