## Neste domínio: laudos de exames laboratoriais

Cada documento é o laudo de um paciente. O sistema não faz diagnóstico nem recomenda tratamento: ele organiza os resultados com a página de origem. "O relatório de um paciente" é o relatório de um documento (`relatorio_documento`, com o `doc_id` do laudo em `documento`).

Exemplos de intenção:

- `pergunta`: "A glicose da Maria está fora da referência?", "e o colesterol?", "qual paciente tem o LDL mais alto?".
- `relatorio`: "Gere o relatório", "quero a análise completa dos exames".
- `relatorio_documento`: "Relatório da Maria", "faça a análise completa do exame do João". Se ele citar mais de um paciente, é `relatorio`.
- `fora_do_escopo`: também pedido de diagnóstico, de prognóstico ou de tratamento ("estou doente?", "que remédio tomo?").

Na conversa, uma pergunta resolvida fica assim: "Responda: qual é o colesterol total da Ana?".
