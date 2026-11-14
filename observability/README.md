# Observabilidade

Traces, métricas e logs dos quatro serviços num lugar só: **Tempo**, **Prometheus**, **Loki** e
**Grafana**, com o **Grafana Alloy** como coletor único. Os fluxos estão desenhados em
[docs/fluxos.html](docs/fluxos.html).

```mermaid
flowchart LR
    subgraph S[serviços]
        FE[frontend] & IDX[indexer] & MCP[mcp-server] & AG[agents]
    end
    S -->|OTLP: traces e métricas| AL[Alloy]
    D[(Docker: saída dos contêineres)] -->|logs| AL
    AL -->|traces| T[Tempo]
    AL -->|métricas| P[Prometheus]
    AL -->|logs| L[Loki]
    T -->|RED e mapa de serviços| P
    T & P & L --> G[Grafana :3001]
```

## O que é coletado

| Sinal | De onde | Como |
|---|---|---|
| **Traces** | os quatro serviços | OpenTelemetry (`app/telemetria.py` em cada serviço Python, `src/instrumentation.server.ts` no frontend), exportado por OTLP/HTTP ao Alloy |
| **Métricas** | os serviços, o Qdrant e a própria pilha | OTLP (contadores e histogramas próprios) e scrape; o Tempo gera as de requisição (taxa, erro, latência) a partir dos spans |
| **Logs** | os contêineres dos quatro serviços | o Alloy lê a saída pelo Docker; rótulos `projeto`, `servico`, `nivel` |

**Um trace atravessa tudo.** O contexto (`traceparent`) vai junto em cada chamada HTTP (frontend →
orquestrador → especialistas por A2A → servidor MCP → indexador) e, no indexador, dentro da
mensagem do Kafka (upload → parse → indexação). Uma pergunta no chat aparece como uma árvore só:
cada onda do grafo, cada especialista, cada chamada ao modelo (com os tokens, pela instrumentação
do PydanticAI), cada tool no MCP e cada busca no índice.

**Log ↔ trace.** Toda linha de log dos serviços Python traz `trace_id=` e `span_id=`. No Grafana,
a linha abre o trace, e o trace mostra as linhas dele.

## Métricas próprias

| Métrica | Rótulos |
|---|---|
| `agentes_execucoes_total`, `agentes_execucao_duracao_seconds` | `tipo`, `estado` |
| `agentes_tokens_total`, `agentes_custo_usd_total` | `agente`, `modelo`, `tipo` (entrada, cache, saída) |
| `agentes_delegacoes_total`, `agentes_especialista_duracao_seconds` | `agente`, `estado` |
| `mcp_tool_chamadas_total`, `mcp_tool_duracao_seconds` | `agente`, `tool`, `resultado` (ok, rejeitada, recusada, invalida, erro) |
| `indexador_mensagens_total`, `indexador_etapa_duracao_seconds` | `etapa` (parse, index), `resultado` (ok, retry, dlq) |
| `indexador_paginas_total`, `indexador_chunks_total`, `indexador_custo_usd_total` | `parser` |
| `traces_spanmetrics_*`, `traces_service_graph_*` | geradas pelo Tempo: por serviço e operação |

## Painéis

| Painel | Mostra |
|---|---|
| **Visão geral** (página inicial) | requisições, erros e latência por serviço, mapa dos serviços, execuções e custo dos agentes, erros nos logs |
| **Agentes** | custo e tokens por agente, chamadas de tool e o que os portões barraram, delegações, duração, execuções recentes (traces), os passos e o raciocínio de cada execução |
| **Indexador** | mensagens por etapa, retries e DLQ, duração do parse e da indexação, páginas, chunks, custo, documentos processados (traces) |

Para investigar, a aba **Explore** do Grafana consulta as três fontes direto (TraceQL no Tempo,
LogQL no Loki, PromQL no Prometheus).

## Rodar

```bash
docker compose up -d     # Grafana em http://localhost:3001 (abre sem login; admin/admin para editar)
```

Suba antes o indexador, que cria a rede `indexer_default`. Os quatro serviços já apontam para o
coletor (`OTEL_EXPORTER_OTLP_ENDPOINT=http://alloy:4318` nos composes). Para desligar a exportação
de um serviço, deixe a variável vazia no `.env` dele. Com a pilha fora do ar, os serviços seguem
normalmente; os erros de exportação são silenciados.

| Porta | Serviço |
|---|---|
| 3001 | Grafana |
| 4317 / 4318 | OTLP (gRPC / HTTP), para serviços rodando fora do Docker |
| 9090 | Prometheus |
| 3100 | Loki |
| 3200 | Tempo |
| 12345 | interface do Alloy |

O conteúdo das mensagens dos modelos (texto dos documentos) não vai para os traces. Para incluir,
use `OTEL_INCLUIR_CONTEUDO=true` no `.env` dos agentes.

Retenção: 3 dias de traces, 7 de logs, 15 de métricas.

Para acompanhar uma execução enquanto ela acontece, há a [sala dos agentes](../sala/README.md):
os agentes desenhados trabalhando, a partir dos passos do orquestrador e da auditoria do servidor
MCP, e uma aba com os logs do Loki.

## Estrutura

```
alloy/config.alloy         coletor: OTLP, scrape e logs do Docker
tempo/tempo.yaml           traces e o gerador de métricas
loki/loki.yaml             logs
prometheus/prometheus.yml  métricas (recebe por remote write)
grafana/provisioning/      fontes (já ligadas entre si) e painéis
grafana/dashboards/        visao-geral, agentes, indexador
```
