---
descricao: Responde a UMA pergunta sobre os documentos, comparando empresas quando preciso, e grava a resposta com as fontes. Use para as cinco perguntas do relatório (informe o pergunta_id) e para perguntas livres do usuário.
---
## Neste domínio: demonstrações financeiras de empresas

- Os campos são os indicadores de cada empresa, informados e calculados. Exemplo de marcador: `{{abc123.receita_liquida}}`; a variação é contra o ano anterior; `{{doc_id.analise.guidance}}` quando o guidance foi registrado como ausente.
- Diga sempre se o valor é informado pela empresa ou calculado. Pergunta sobre várias empresas: responda empresa por empresa. No texto, use o nome da empresa.
- Pergunta sobre a variação da RECEITA pede o trecho em que a administração explica a receita (busque "receita líquida" no relatório da administração e leia a página), não um trecho sobre custos, sobre o lucro ou sobre uma controlada pequena. Se o trecho é de uma parte do grupo, diga de qual.

O que responde, nas duas perguntas em que isso mais erra:

- **Motivo da variação da receita** (`motivo_resultado`): o servidor já entrega os itens de cada empresa em ordem, com o que está sob o título de receita primeiro. O PRIMEIRO item de cada empresa é a resposta: comece por ele. Os outros só entram como complemento, dizendo de que parte do grupo são. Só serve o item que explica a RECEITA da empresa como um todo (o consolidado, ou a própria companhia). Não serve item sobre custo ("reduzindo os custos com..."), sobre o lucro, ou sobre uma controlada ou segmento só: esse pode entrar depois, dizendo de qual parte do grupo é.
- **Guidance** (`guidance`): só o que a administração diz que vai fazer ou espera: planos, estratégia, investimentos, renovação de contratos ou concessões, metas. A declaração de continuidade operacional ("possui condições de dar continuidade a suas operações pelos próximos 12 meses") não é guidance: é a conclusão sobre um risco. Não a use como guidance.
