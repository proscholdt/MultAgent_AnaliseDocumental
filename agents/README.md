# Agentes

Um orquestrador em grafo (LangGraph) e cinco especialistas (PydanticAI) em serviços separados,
ligados por A2A. Todos usam as ferramentas pelo [servidor MCP](../mcp-server/README.md). Os
fluxos estão desenhados em [docs/fluxos.html](docs/fluxos.html).

```mermaid
flowchart LR
    S([START]) --> O[orquestrador<br/>devolve a Decisao]
    O -->|Send, em paralelo| D[delegar × N<br/>A2A]
    D -->|relatos| O
    O -->|terminado| C[conferencia<br/>código]
    C -->|falta algo| O
    C -->|completo| F[fim] --> E([END])
```

## Como funciona

- **Orquestrador**: agente PydanticAI com saída estruturada
  `Decisao{delegacoes, terminado, mensagem, intencao}`. Ele não chama ninguém; o grafo executa
  as delegações da onda em paralelo e volta com os relatos montados pelo código.
- **Conferência**: o código pergunta ao MCP o que falta (`pendencias`). "Terminei" dito pelo
  modelo não encerra; até 2 rodadas extras, depois a execução fica `incompleta`.
- **Intenção**: no chat, a primeira decisão classifica a mensagem. `pergunta` exige resposta
  gravada com fonte; `relatorio` exige o relatório montado; `fora_do_escopo` não executa nada e
  devolve um texto fixo com as opções.
- **A2A**: o orquestrador assina um token por chamada (destino, execução, usuário); só ele entra
  no serviço dos especialistas. Cada especialista chega ao MCP com um token de agente próprio.

| Agente | Faz | Modelo |
|---|---|---|
| `orquestrador` | Classifica, decide as ondas, acompanha | padrão |
| `extrator` | Os 8 indicadores de um documento, com origem | padrão |
| `validador` | Inconsistências no texto da fonte | forte |
| `analista` | Motivos, riscos, guidance, eventos, tom | padrão |
| `pesquisador` | Uma pergunta, com fontes | padrão |
| `redator` | Resumo por empresa e montagem do relatório | padrão |

Um agente é um arquivo `agentes/<nome>.md` (cabeçalho + prompt), relido sem reiniciar. A
permissão é uma entrada em `mcp-server/policy.yaml`: quem escreve o agente não concede a própria
permissão.

## Estado e memória

| O quê | Onde |
|---|---|
| Resultados (valores, análise, respostas, relatório) | base do servidor MCP, fonte da verdade |
| Turnos da conversa | checkpoint LangGraph em SQLite, `thread_id` = conversa |
| Memória de cada especialista | SQLite por (usuário, conversa, agente, documento), compactada na leitura |
| Trace com raciocínio e mensagens completas | `data/execucoes/` |

## Freios

`max_turnos` por agente, teto de tokens e tempo por execução (`EXECUCAO_MAX_TOKENS`,
`EXECUCAO_MAX_SEGUNDOS`), `MAX_ONDAS`, `RODADAS_PENDENCIAS`, `MEMORIA_MAX_TOKENS`, limite de
chamadas na política do MCP, e nenhuma execução começa com o token do usuário a menos de 15 min
de vencer.

## Rodar

```bash
cp .env.example .env          # OPENAI_API_KEY, MCP_AGENT_SECRET (o mesmo do MCP), A2A_SECRET
docker compose up -d --build  # orquestrador :8200, especialistas :8201
uv run pytest                 # sem rede: modelo com roteiro, MCP em memória, A2A por ASGI

uv run python scripts/gerar_entrega.py --usuario <u> --senha <s> --rodar   # relatório + JSON em entrega/
uv run python scripts/avaliar.py --usuario <u> --senha <s>                 # contra avaliacao/gabarito.json
```

| Rota | Função |
|---|---|
| `POST /api/conversas`, `POST /api/conversas/{id}/mensagens` | Conversa e turno em texto livre |
| `GET /api/conversas[/{id}]` | Conversas e turnos |
| `POST /api/relatorios`, `/api/perguntas` | Atalhos com tipo definido |
| `GET /api/execucoes/{id}[/mensagens]` | Estado, passos, custo; mensagens de cada agente |

Relatório completo dos três documentos: 332 s e US$ 1,02 com raciocínio ligado (146 s e
US$ 0,58 sem); 24 de 24 indicadores com status certo e nenhum valor ausente gravado como
informado.

## Estrutura

```
app/grafo.py          execução em grafo: orquestrador, delegar, conferencia, fim
app/runner.py         um agente PydanticAI: modelo, toolset MCP, passos, freios
app/especialistas.py  serviço A2A dos especialistas, memória
app/memoria.py        histórico por conversa e compactação
app/main.py           API do orquestrador
agentes/              um .md por agente
```
