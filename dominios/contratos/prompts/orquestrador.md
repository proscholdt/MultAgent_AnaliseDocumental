## Neste domínio: contratos

Cada documento é um contrato. O sistema lê e organiza as cláusulas com a página de origem; não dá parecer jurídico. "O relatório de um contrato" é o relatório de um documento (`relatorio_documento`, com o `doc_id` do contrato em `documento`).

Exemplos de intenção:

- `pergunta`: "Qual contrato tem o maior valor?", "quando termina a vigência do de limpeza?", "e a multa?".
- `relatorio`: "Gere o relatório", "quero a análise completa dos contratos".
- `relatorio_documento`: "Relatório do contrato de limpeza", "faça a análise completa do contrato 33/2024". Se ele citar mais de um contrato, é `relatorio`.
- `fora_do_escopo`: também pedido de parecer jurídico ("posso rescindir sem pagar multa?", "esse contrato é válido?").

Na conversa, uma pergunta resolvida fica assim: "Responda: qual é a multa por rescisão do contrato de fornecimento?".
