# 0005 — Postgres opcional para várias réplicas

**Contexto.** A base do MCP, a memória dos agentes, o checkpoint das conversas e as execuções
ficavam em SQLite e arquivos num volume: uma réplica de cada serviço.

**Decisão.** Com `MCP_DATABASE_URL` / `AGENTS_DATABASE_URL` de Postgres:
- MCP: migrações do Alembic aplicadas na subida; limite de chamadas por execução com contagem por
  upsert (`contagem_chamadas`), atômico entre réplicas.
- Agentes: checkpoint do LangGraph (`AsyncPostgresSaver`), execuções, trace, conversas e memória em
  tabelas; tarefas A2A no `DatabaseTaskStore` do SDK.
- Sem as variáveis, tudo continua em SQLite e arquivos (desenvolvimento e testes).
- As duas suítes rodam também contra Postgres na CI (`MCP_TEST_DATABASE_URL`,
  `AGENTS_TEST_DATABASE_URL`).

**Consequência.** Conexões curtas do psycopg nas tabelas dos agentes (sem pool): suficiente para o
volume de hoje; com muito tráfego, um pool. Uma delegação A2A em andamento numa réplica que cai
ainda depende do timeout do orquestrador para ser refeita.
