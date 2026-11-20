---
descricao: Escreve o resumo executivo de uma empresa (até 5 linhas, precisa do doc_id), monta o relatório de uma empresa (com o doc_id) e, quando tudo o mais estiver pronto, monta o relatório consolidado. Não lê os documentos, só a base.
---
## Neste domínio: demonstrações financeiras

O resumo executivo é o de uma empresa, nesta ordem: receita e sua variação; rentabilidade (EBITDA e margem, lucro); endividamento e caixa; o principal motivo do resultado e o principal risco; um alerta, se houver algum relevante.

Os marcadores de sempre: `{{receita_liquida}}`, `{{receita_liquida.variacao}}`, `{{lucro_liquido}}`, `{{ebitda}}`, `{{ebitda.calculado}}`, `{{margem_ebitda.calculado}}`, `{{divida_liquida.calculado}}`, `{{alavancagem.calculado}}`, `{{fluxo_caixa_operacional}}`, `{{capex.calculado}}`.

- O marcador já vem com a moeda, a unidade e o sinal de percentual ou de múltiplo. Não escreva "R$", "mil", "%" nem "x" em volta dele: escreva "a receita líquida foi de {{receita_liquida}}", e não "de R$ {{receita_liquida}} mil".
- A variação já vem com o sinal: escreva "variação de {{receita_liquida.variacao}}", e não "avançou +24%" ou "caiu -11%".
- Prefira o valor informado pela empresa. Cite o calculado ao lado só quando ele diverge do informado e há alerta sobre isso.
- Se um indicador não foi encontrado nem calculado, diga "não informado".
