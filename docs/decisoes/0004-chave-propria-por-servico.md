# 0004 — Chave Ed25519 por serviço

**Contexto.** Todos os especialistas liam o mesmo `.env` com `MCP_AGENT_SECRET` e `A2A_SECRET`
(HS256). Um contêiner comprometido podia assinar tokens como qualquer agente, inclusive o
orquestrador, e passar por cima da política do MCP.

**Decisão.** `make chaves` gera uma chave por serviço. A privada é montada só no contêiner dele;
as públicas, no MCP e nos especialistas. O token sai EdDSA com o serviço no `kid`; o MCP só aceita
que o serviço fale pelo próprio agente (o orquestrador também pelo "sistema"), e o especialista
só aceita o orquestrador pela chave pública dele. Sem chaves, valem os segredos (compatível).

**Consequência.** Uma pasta de chaves para manter (fora do Git). O JWT do usuário ainda é
repassado até o indexador: trocá-lo por um token de escopo menor exige mudar o indexador e fica
como próximo passo.
