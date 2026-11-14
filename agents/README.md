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
  gravada com fonte; `relatorio` exige o relatório consolidado montado; `relatorio_empresa` traz
  o `doc_id` da empresa e exige o relatório dela (só as pendências daquela empresa);
  `fora_do_escopo` não executa nada e devolve um texto fixo com as opções.
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
permissão é uma entrada em `mcp-server/config/policy.yaml`: quem escreve o agente não concede a própria
permissão.

## Estado e memória

| O quê | Onde |
|---|---|
| Resultados (valores, análise, respostas, relatório) | base do servidor MCP, fonte da verdade |
| Turnos da conversa | checkpoint LangGraph em SQLite, `thread_id` = conversa |
| Memória de cada especialista | SQLite por (usuário, conversa, agente, documento), compactada na leitura |
| Trace com raciocínio e mensagens completas | `data/execucoes/` |

## Freios

`max_turnos` por agente, teto de tokens e tempo por execução, ondas por rodada, rodadas da
conferência e memória reenviada (`execucao` e `memoria_max_tokens` no `config/ajustes.yaml`),
limite de chamadas na política do MCP, e nenhuma execução começa com o token do usuário a menos
de 15 min de vencer.

## Configuração

| Onde | O quê | Quando vale |
|---|---|---|
| `agentes/<nome>.md` | `modelo`, `raciocinio`, `max_turnos` e o prompt de cada agente | quando o arquivo muda |
| `config/ajustes.yaml` | preços, freios, raciocínio padrão, tempos | no próximo pedido |
| `.env` | só chaves e segredos | ao recriar o contêiner |

As duas pastas são montadas no contêiner: mudar não pede build nem restart. Arquivo inválido
não derruba nada: continua valendo o anterior, e o motivo aparece em `GET /health`
(`arquivos_invalidos`, `erro_ajustes`).

## Rodar

```bash
cp .env.example .env          # só chaves: OPENAI_API_KEY, MCP_AGENT_SECRET (o mesmo do MCP), A2A_SECRET
docker compose up -d --build  # orquestrador :8200, especialistas :8201
uv run pytest                 # sem rede: modelo com roteiro, MCP em memória, A2A por ASGI

uv run python scripts/gerar_entrega.py --usuario <u> --senha <s> --rodar   # relatório + JSON em entrega/
uv run python scripts/avaliar.py --usuario <u> --senha <s>                 # contra avaliacao/gabarito.json
```

O `modelo` do agente é o nome no provedor. Com o prefixo `groq:` (por exemplo
`groq:openai/gpt-oss-120b`), ele vai à Groq pela API compatível com a OpenAI, com `GROQ_API_KEY`. Tools sem argumento seguem sem `strict`, porque a Groq recusa esse caso.

| Rota | Função |
|---|---|
| `POST /api/conversas`, `POST /api/conversas/{id}/mensagens` | Conversa e turno em texto livre |
| `GET /api/conversas[/{id}]` | Conversas e turnos |
| `POST /api/relatorios`, `/api/perguntas` | Atalhos com tipo definido |
| `GET /api/execucoes/{id}[/mensagens]` | Estado, passos, custo; mensagens de cada agente |

Relatório completo dos três documentos, com raciocínio ligado:

| Modelos | Tempo | Custo | Indicadores certos | Inconsistências da fonte |
|---|---|---|---|---|
| `gpt-5.4-nano` em todos (padrão) | 195 s | US$ 0,14 | 24 de 24 | 2 de 6 |
| `gpt-5.4-mini` + `gpt-5.4` no validador | 332 s | US$ 1,02 | 24 de 24 | 4 de 6 |
| `gpt-5-nano` em todos | parou em 12 min | US$ 0,08 | nenhuma extração fechada | — |

Nos dois primeiros, nenhum valor ausente foi gravado como informado, os valores calculados e
as cinco perguntas saíram certos, e todas as citações existem no trecho citado.

## Estrutura

```
app/grafo.py          execução em grafo: orquestrador, delegar, conferencia, fim
app/runner.py         um agente PydanticAI: modelo, toolset MCP, passos, freios
app/especialistas.py  serviço A2A dos especialistas, memória
app/memoria.py        histórico por conversa e compactação
app/main.py           API do orquestrador
agentes/              um .md por agente
```
