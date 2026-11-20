---
descricao: Faz a análise qualitativa de UM documento (motivos do resultado, riscos, guidance, eventos relevantes e tom da administração), cada item com a citação literal que o sustenta. Precisa do doc_id.
---
## Neste domínio: demonstrações financeiras

O que procurar em cada categoria:

| categoria | o que procurar |
|---|---|
| `motivo_resultado` | o que a administração aponta como causa do resultado e da variação da receita |
| `risco` | riscos e pontos de atenção: de mercado, de crédito, de liquidez, regulatórios, contingências, continuidade |
| `guidance` | perspectivas, projeções e expectativas para os próximos períodos |
| `evento_relevante` | aquisições, reestruturações, eventos subsequentes, mudanças contábeis, recuperação judicial |
| `tom` | o tom geral da administração: `positivo`, `neutro` ou `negativo`, com a justificativa |

Onde estão: busque "relatório da administração", "mensagem da administração", "comentário de desempenho". Alguns documentos trazem só as demonstrações e as notas explicativas, sem comentário da administração. Riscos costumam estar na nota de instrumentos financeiros e gestão de riscos; eventos, na nota de eventos subsequentes; contingências, na nota de provisões. Prefira os itens mais específicos da empresa aos genéricos.

`tom`: um item só, com `tom` = `positivo`, `neutro` ou `negativo`. A afirmação é a justificativa, e a citação é a frase que melhor mostra esse tom. Sem comentário da administração, o tom das notas explicativas é, em regra, `neutro`: justifique assim, citando uma frase factual.

Se não há guidance porque não há relatório da administração, registre a ausência. "A administração não explica a variação" é ausência, não achado.

Política contábil genérica (como a empresa reconhece receita, como mensura provisões) não é motivo de resultado, nem guidance, nem evento.

Para `motivo_resultado`, o que interessa é a explicação da administração para o desempenho do ano: por que a receita subiu ou caiu, o que pesou no lucro. Ela está no relatório da administração ("Desempenho financeiro", "Receita líquida"), quando ele existe. Um dos itens tem de ser o motivo da variação da RECEITA, se a administração o explica (busque "receita líquida" no relatório e leia a página).

Em relatório de grupo, cada controlada tem o seu parágrafo (distribuidora, geradora, comercializadora). Diga na afirmação de quem é a explicação ("a queda da receita da distribuidora..."), e prefira a do negócio principal ou a do consolidado: o motivo do resultado de uma controlada pequena não é o motivo do resultado da companhia.

Para `guidance`, o que interessa é o que a administração diz que vai fazer ou espera: planos, metas, prioridades, prazos. Quando há mensagem ou carta da administração (costuma abrir o documento, nas primeiras páginas), leia-a inteira com `ler_pagina`: é ali que ficam os planos. Estimativa contábil (prazo de realização de um crédito, premissa de um teste de impairment) não é guidance. A declaração de continuidade operacional ("a Companhia possui condições de dar continuidade a suas operações pelos próximos 12 meses") também não: é a conclusão sobre um risco, e vai em `risco`.
