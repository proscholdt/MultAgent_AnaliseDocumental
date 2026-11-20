---
descricao: Extrai os campos de UM contrato (partes, objeto, valor, assinatura, vigência, multa, reajuste e foro), com a origem de cada valor, e fecha a extração. Precisa do doc_id.
---
## Neste domínio: contratos

Cada documento é UM contrato. Não há período: deixe `periodo` em `null`.

- **Texto** (`contratante`, `contratada`, `objeto`, `indice_reajuste`, `foro`): o `valor` é o que o contrato escreve, copiado do trecho: o nome da parte ("Brilho Serviços de Limpeza Ltda."), o objeto em poucas palavras do próprio texto, o índice ("IPCA"), o foro ("Comarca de Belo Horizonte/MG"). Num quadro-resumo em tabela, aponte a linha e a coluna, sem `valor`.
- **Data** (`data_assinatura`, `inicio_vigencia`, `fim_vigencia`): o `valor` é a data como está escrita ("15/01/2024", "10 de março de 2024"); o servidor grava em dd/mm/aaaa e confere no trecho. A assinatura costuma estar no fim ("Belo Horizonte, 15/01/2024") ou no preâmbulo ("firmam este contrato em...").
- **Valor total** (`valor_total`): o número como está escrito ("120.000,00"), da frase com "R$" ou da célula.
- **Multa por rescisão** (`multa_rescisao`): o percentual ("10%") da multa por rescindir o contrato. Multa de mora (por atraso de pagamento) não é a multa por rescisão: se só houver essa, o campo é "não encontrado", e o motivo diz que a multa do contrato é de mora.

Se a vigência é dada em meses ("doze meses a partir de 1º de abril de 2024"), grave o início e registre `fim_vigencia` como "não encontrado", dizendo no motivo que o contrato só dá a duração: nunca calcule a data.

Um contrato-modelo, com campos em branco ("R$ ____", "[nome da contratada]"), não informa os valores: é "não encontrado", e o motivo diz que é um modelo sem preenchimento.
