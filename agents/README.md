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

## Nota contra o gabarito

Ao fim de cada relatório completo (consolidado ou de uma empresa), o orquestrador tira o retrato
da base e compara com `avaliacao/gabarito.json`, conferido à mão: status e valor de cada
indicador, página, ano anterior, calculados, invenções, alertas, inconsistências da fonte, as
cinco perguntas e as citações. Não chama modelo. A nota fica na execução (`avaliacao`), no
trace (passo `avaliacao`) e vira métrica no painel "Agentes" do Grafana.

- O documento é reconhecido pelo **conteúdo** (`doc_id`, o hash do PDF), não pelo nome. Documento
  sem gabarito não entra na conta e aparece em `sem_gabarito`; sem nenhum, a execução diz que não
  houve nota.
- As **perguntas** comparam as empresas: só contam quando a base tem exatamente os documentos do
  gabarito. No relatório de uma empresa, não contam.
- Liga e desliga em `config/ajustes.yaml` (`avaliacao.ao_fim_do_relatorio`). O mesmo cálculo roda
  à mão com `scripts/avaliar.py`.

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

## Mapa do código

```
agentes/                    UM ARQUIVO POR AGENTE: modelo, raciocínio, turnos e prompt
config/ajustes.yaml         preços, freios, tempos, traces com conteúdo

app/
├─ config.py                .env (chaves) + config/ajustes.yaml
├─ telemetria.py
│
├─ orquestrador/            O SERVIÇO :8200
│  ├─ api.py                rotas: conversas, mensagens, execuções, atalhos
│  ├─ grafo.py              o grafo: orquestrador → delegar → conferência → fim
│  ├─ execucoes.py          execuções, conversas e trace em disco
│  ├─ especialistas_a2a.py  descobrir e chamar os especialistas por A2A
│  └─ usuario.py            quem é o usuário (login do indexador)
│
├─ especialistas/           O SERVIÇO :8201
│  ├─ servico.py            cada arquivo de agente vira um agente A2A
│  └─ memoria.py            histórico de cada especialista na conversa, compactado
│
├─ nucleo/                  O QUE OS DOIS USAM
│  ├─ agentes.py            ler e validar agentes/<nome>.md
│  ├─ executar.py           rodar UM agente PydanticAI: modelo, tools do MCP, passos, freios
│  ├─ mcp.py                conexão com o servidor MCP
│  ├─ tokens.py             token A2A e token do agente
│  └─ custos.py             custo pelos tokens e pelos preços do ajustes.yaml
│
└─ ferramentas/             APOIO
   ├─ cli.py                python -m app.ferramentas.cli validar | novo-agente
   ├─ avaliacao.py          a conta: a base contra o gabarito (só os documentos que têm gabarito)
   ├─ retrato.py            o retrato da base, lido pelo servidor MCP como "sistema"
   └─ avaliacao_automatica.py  a nota ao fim de cada relatório e as métricas dela

scripts/                    avaliar.py, gerar_entrega.py
```

Para criar um agente: `python -m app.ferramentas.cli novo-agente <nome>` cria `agentes/<nome>.md`
e mostra a entrada para colar em `mcp-server/config/policy.yaml`.
