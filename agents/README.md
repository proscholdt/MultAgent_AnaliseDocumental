# Agentes: orquestrador e especialistas

Sistema 3 de 3 da solução. Aqui ficam os agentes: um **orquestrador**, que entende o pedido e
decide o que fazer, e **especialistas**, que fazem cada parte do trabalho (extrair, revisar,
analisar, responder, redigir).

- Os especialistas são **agentes remotos**: o orquestrador os chama pelo protocolo **A2A**
  (Agent2Agent), e eles só respondem a ele.
- Todos usam as ferramentas e leem os documentos por um lugar só, o
  [servidor MCP](../mcp-server/README.md) (sistema 2), que decide o que cada um pode chamar e
  confere tudo o que eles gravam.
- Os documentos vêm do [indexador](../indexer/README.md) (sistema 1).

Os agentes são feitos com **PydanticAI**, e a orquestração é um grafo **LangGraph**. O usuário
conversa: um pedido pode continuar o anterior, e os agentes lembram o que foi feito na conversa.

A tela fica no [frontend](../frontend/README.md) (sistema 4). Tudo também é testável pelo Swagger em
`http://localhost:8200/docs`.

```mermaid
flowchart TD
    U[Swagger / script] -->|"JWT do indexador"| API

    subgraph ORQ[orquestrador :8200 · grafo LangGraph]
        API[API] --> O[nó orquestrador<br/>PydanticAI: decide a onda]
        O --> D[nó delegar<br/>uma chamada por delegação,<br/>em paralelo]
        D --> O
        O --> CF[nó conferencia<br/>pelo código]
        CF -->|falta algo| O
        CK[(checkpoint<br/>por conversa)] -.- O
    end

    D -->|"A2A (JSON-RPC)<br/>token do orquestrador,<br/>emitido para aquele especialista"| ESP

    subgraph ESP[especialistas :8201 · agentes PydanticAI]
        MEM[(memória por<br/>conversa)]
        E[extrator]
        V[validador]
        A[analista]
        P[pesquisador]
        R[redator]
    end

    O -->|MCP| MCP
    ESP -->|"MCP, cada um com o SEU token"| MCP
    CF -->|"MCP, como 'sistema' (só leitura)"| MCP

    MCP[mcp-server :8100<br/>política, portões, base, auditoria] --> IDX[indexer :8000<br/>busca e chunks]
```

É o desenho *orchestrator-workers* em hub-and-spoke, com um supervisor no grafo: o orquestrador é
o centro, os especialistas são os raios, e o A2A é o cabo entre eles. Os raios não se falam.

## Como rodar

Os três sistemas sobem na ordem: indexador, servidor MCP, agentes.

```bash
# 1. Indexador: sobe, cria o usuário e envia os PDFs (ver indexer/README.md)
cd indexer && cp .env.example .env && docker compose up --build -d

# 2. Servidor MCP
cd ../mcp-server && cp .env.example .env && docker compose up --build -d

# 3. Agentes
cd ../agents && cp .env.example .env && docker compose up --build -d
```

No `agents/.env`: a chave da OpenAI, o `MCP_AGENT_SECRET` **igual ao do `mcp-server/.env`** e um
`A2A_SECRET` novo (o comando para gerar está no arquivo).

Depois, no Swagger (`http://localhost:8200/docs`):

1. Faça login no indexador (`POST http://localhost:8000/auth/login`) e copie o `access_token`.
2. Clique em **Authorize** e cole o token. O login é o mesmo do indexador: os agentes só
   enxergam os documentos desse usuário.
3. `POST /api/relatorios` pede o relatório completo. `GET /api/execucoes/{id}` mostra o
   andamento, os passos, os tokens e o custo.

Ou, de uma vez, pelo script que gera a pasta de entrega:

```bash
uv run python scripts/gerar_entrega.py --usuario <usuário> --senha <senha> --rodar
```

Fora do Docker (dois processos):

```bash
uv sync
uv run uvicorn app.especialistas:app_factory --factory --port 8201
uv run uvicorn app.main:app_factory --factory --port 8200
uv run pytest        # 67 testes, sem rede
```

## O que dá para pedir

| Rota | Função |
|---|---|
| `POST /api/relatorios` | O relatório consolidado completo dos documentos do usuário |
| `POST /api/perguntas` | Uma pergunta em linguagem natural; devolve a resposta com as fontes |
| `POST /api/execucoes` | Pedido em texto livre: o orquestrador decide o que fazer |
| `POST /api/conversas` | Abre uma conversa |
| `POST /api/conversas/{id}/mensagens` | Um turno da conversa, em texto livre: pergunta ou relatório (o orquestrador classifica), lembrando os turnos anteriores. O resto recebe "fora do escopo" e as opções |
| `GET /api/conversas` | As conversas do usuário, com o primeiro pedido como título |
| `GET /api/conversas/{id}` | Os turnos de uma conversa, com as respostas (e o relatório, no turno de relatório) |
| `GET /api/execucoes/{id}` | Estado, resultado, passos (quem chamou o quê, e o raciocínio do modelo), tokens e custo |
| `GET /api/execucoes/{id}/mensagens` | As mensagens completas de cada agente: pedido, raciocínio, tools com argumentos e resultados |
| `GET /api/execucoes` | Execuções do usuário |
| `GET /api/agentes` | Agentes carregados e as tools que a política do servidor MCP dá a cada um |
| `GET /health` | Estado do serviço, agentes e modelos |

As rotas de execução, pergunta e relatório aceitam `conversa_id`: sem ele, cada pedido abre uma
conversa nova.

Uma execução termina como `completa`, `incompleta` (com o que faltou), `falhou` ou
`interrompida`. O resultado vem **da base**, lido pelo código: o relatório montado pelo servidor,
ou a resposta gravada com as fontes. O texto final do orquestrador é só uma mensagem de estado.

## Um agente é um arquivo

`agentes/extrator.md`:

```markdown
---
nome: extrator
descricao: Extrai os indicadores financeiros de UM documento, com a origem de cada valor.
modelo: padrao        # apelido; o modelo real fica no .env (MODELOS)
max_turnos: 60
---
Você extrai indicadores de demonstrações financeiras anuais de UMA empresa por vez...
```

O corpo é o prompt. A `descricao` é o que o orquestrador lê para decidir quando chamar: ela vira
a descrição da tool e do Agent Card.

### Criar um agente: dois passos

1. **O arquivo.** `agentes/<nome>.md`. O comando cria a partir de um modelo:

   ```bash
   uv run python -m app.cli novo-agente consultor \
       --descricao "Compara os riscos citados pelas empresas." --tools ler_analise,buscar_trechos
   ```

2. **A permissão.** O comando imprime a entrada para colar em `mcp-server/policy.yaml`:

   ```yaml
     consultor:
       tools: [ler_analise, buscar_trechos]
   ```

Os dois passos valem sem reiniciar nenhum serviço: a pasta de agentes e a política são relidas
quando mudam. Na execução seguinte, o agente novo já é uma tool do orquestrador, tem Agent Card e
endpoint A2A. `GET /api/agentes` mostra o resultado.

Por que dois passos e não um: **quem escreve o agente não concede a própria permissão.** O
arquivo diz o que o agente é; a política do servidor MCP diz o que ele pode. Uma chave `tools`
no arquivo é erro. Um agente com arquivo e sem entrada na política não tem tool nenhuma, e a
execução não começa: o erro diz qual agente e o que falta.

Arquivo inválido não derruba os outros: fica de fora, e o motivo aparece em `GET /api/agentes` e
em `uv run python -m app.cli validar`.

### Os especialistas que vêm no repositório

| Agente | Faz | Modelo | Tools que a política dá |
|---|---|---|---|
| `orquestrador` | Entende o pedido, delega e acompanha | padrão | `listar_documentos`, `pendencias`, `ler_extracao`; devolve a decisão da onda |
| `extrator` | Os 8 indicadores de um documento, com a origem | padrão | RAG, `salvar_indicador`, `salvar_nao_encontrado`, `fechar_extracao` |
| `validador` | Lê o texto da fonte e sinaliza o que não fecha | **forte** | RAG, leitura da base, `registrar_alerta`, `concluir_revisao` |
| `analista` | Motivos, riscos, guidance, eventos e tom, com citação | padrão | RAG, `salvar_item_analise`, `registrar_ausencia_analise` |
| `pesquisador` | Uma pergunta, com fontes | padrão | RAG, leitura da base, `comparar_indicadores`, `salvar_resposta` |
| `redator` | Resumo executivo e montagem do relatório | padrão | leitura da base, `salvar_resumo`, `montar_relatorio` |

O orquestrador **não lê trecho de documento**. Texto malicioso dentro de um PDF não chega a quem
decide o que fazer.

## A2A: o cabo entre o orquestrador e os especialistas

Para cada arquivo de agente (menos o orquestrador), o serviço dos especialistas publica:

```
GET  /agentes/<nome>/.well-known/agent-card.json    o Agent Card, gerado do arquivo
POST /agentes/<nome>/                                o endpoint A2A (JSON-RPC, SendMessage)
```

```mermaid
sequenceDiagram
    participant O as orquestrador
    participant E as especialista (A2A)
    participant M as mcp-server
    O->>E: GET /agentes (catálogo: quem está no ar e com que tools)
    O->>E: GET agent-card.json
    O->>E: SendMessage: tarefa + {doc_id, conversa_id, orçamento restante}<br/>Authorization: token A2A (aud = este especialista)<br/>X-User-Token: JWT do usuário
    E->>E: memória deste especialista nesta conversa e documento
    E->>M: tools, com o token do PRÓPRIO especialista
    M-->>E: resultado ou recusa do portão
    E->>M: pendencias (como "sistema")
    E-->>O: tarefa concluída + relato montado pelo código
```

- **Só o orquestrador entra.** O endpoint exige um token assinado com `A2A_SECRET`, com o
  chamador `orquestrador` e emitido para aquele especialista (o `aud` leva o nome). O token do
  extrator não serve no analista. O card é público e declara essa exigência.
- **A identidade viaja junto.** O token leva a execução e o usuário; o JWT do usuário segue no
  cabeçalho `X-User-Token`, fora da mensagem, e chega ao servidor MCP e ao indexador.
- **Cada especialista abre a própria conexão MCP, com o próprio token.** O orquestrador não
  empresta permissão: o que o extrator pode é o que a política diz do extrator.
- **O que volta é um relato montado pelo código**, não o texto do especialista: o estado, o que
  ele gravou (contado nas chamadas que deram certo) e o que ainda falta para ele (lido da base).
  O texto livre só volta se o arquivo do agente pedir `resposta: texto`. Os passos e os tokens
  voltam junto, para o trace, e o código os tira antes de mostrar o relato ao modelo.

### Por que A2A, e quanto custa

O ganho é a fronteira: cada especialista é um serviço com endereço, identidade e contrato
(o card), que pode ser implantado, escalado e trocado sozinho, e que outro orquestrador poderia
chamar. O custo é uma chamada de rede a mais por delegação e mais peças para operar. Dentro de um
processo só, o nó `delegar` chamaria o agente direto, com menos código; o desenho com A2A foi uma
escolha por separar as responsabilidades do jeito que elas se separariam em produção.

## Como uma execução acontece: o grafo

```
START → orquestrador ──(delegações)──► delegar (uma por delegação, em paralelo) ──► orquestrador
                     └─(terminado, ou nada a delegar)──► conferencia ──(completo)──► fim → END
                                                                     └─(falta algo)──► orquestrador
```

| Nó | O que faz |
|---|---|
| `orquestrador` | Um agente PydanticAI. Lê `pendencias` e os documentos pelo servidor MCP e devolve uma **decisão estruturada**: as delegações desta onda, se terminou e a mensagem ao usuário. Ele não chama os especialistas: o grafo chama |
| `delegar` | Uma chamada A2A a um especialista. As delegações de uma onda rodam em paralelo (`Send` do LangGraph), e o grafo só volta ao orquestrador quando todas terminam, com os relatos |
| `conferencia` | O código pergunta ao servidor MCP o que falta. Se faltar, o orquestrador recomeça com a lista, até 2 vezes; depois disso a execução termina como `incompleta`. "Terminei" dito pelo modelo não encerra nada |
| `fim` | Registra o turno na conversa (pedido, resposta, estado) |

O relatório anda em ondas: extração e análise dos três documentos juntas; revisão das fontes;
as cinco perguntas e os resumos; a montagem. A ordem não depende do orquestrador: é o servidor
MCP que diz o que está liberado e recusa o que chega antes da hora.

## Estado, memória e conversa

| O quê | Onde fica | Quem usa |
|---|---|---|
| **O que foi produzido** (valores, análise, alertas, respostas, relatório) | base do servidor MCP | todos os agentes, pelas tools; é a fonte da verdade |
| **A conversa do usuário** (os turnos: pedido, resposta, estado) | checkpoint do grafo, em SQLite, por conversa (`thread_id`) | o orquestrador: os turnos anteriores entram no pedido dele |
| **A memória de cada especialista** | SQLite no serviço dos especialistas, por (usuário, conversa, agente, documento) | o especialista: as chamadas anteriores dele voltam como histórico |
| **As mensagens completas de cada agente** (pedido, raciocínio, tools, resposta) | `data/execucoes/<id>/` | quem depura: `GET /api/execucoes/{id}/mensagens` |
| **O trace** (cada passo, com o raciocínio) | `data/execucoes/<id>.jsonl` | `GET /api/execucoes/{id}` |

**O que a mensagem pede.** No chat o usuário só escreve. Na primeira decisão, o orquestrador
diz o que a mensagem é (`intencao`: pergunta, relatório ou fora do escopo), e o tipo da execução
passa a ser esse. O tipo diz o que a conferência final cobra: uma pergunta só fecha com uma
resposta gravada pelo pesquisador, com fontes; um relatório, com o relatório montado.

**Fora do escopo.** O sistema não é de conversa. Saudação, piada, previsão do tempo, conselho de
investimento, qualquer coisa que não seja uma pergunta sobre os documentos ou o relatório: o
código não executa nada (mesmo que o orquestrador tenha delegado) e responde com um texto fixo e
as opções do que o sistema faz, cada uma com um exemplo. Assim nenhum texto livre do orquestrador
chega ao usuário como resposta. Na primeira versão o turno era um pedido livre sem cobrança, e o
orquestrador respondia perguntas sozinho, na mensagem, sem fonte. Na dúvida entre pergunta e fora
do escopo, o prompt manda tratar como pergunta: se a resposta não estiver nos documentos, o
pesquisador diz que não encontrou. A classificação fica no trace (passo `intencao`).

**Pergunta de seguimento.** O orquestrador recebe os turnos anteriores ("A conversa até aqui") e
delega ao pesquisador a pergunta já resolvida: "E a menor?" vira "qual empresa teve a MENOR
margem EBITDA?".

**Memória do especialista.** Chamado de novo na mesma conversa, para o mesmo documento, o
especialista recebe a própria conversa das vezes anteriores e não recomeça do zero. Como ela dura
a conversa inteira, o código controla o tamanho na leitura: o resultado das tools antigas vira uma
linha ("leu o chunk 11, 1.200 caracteres; chame de novo se precisar"), o raciocínio antigo sai, e
acima do teto (`MEMORIA_MAX_TOKENS`) saem as chamadas mais antigas. O guardado é o histórico
inteiro; o encurtamento é só no que volta ao modelo.

O que vale continua na base. A memória poupa releituras e dá continuidade; não é fonte de valor.

### Freios

| Freio | Onde |
|---|---|
| Turnos por agente (`max_turnos`) | arquivo do agente |
| Teto de tokens e de tempo da execução inteira | `.env`; o orquestrador passa o que resta a cada especialista e desconta o que ele gastou |
| Chamadas de tool por execução, por agente | `policy.yaml` do servidor MCP |
| Rodadas de conferência | `.env` (`RODADAS_PENDENCIAS`) |
| Validade do token do usuário | uma execução não começa se o token vence em menos de 15 min |
| Ondas por rodada | `.env` (`MAX_ONDAS`): o orquestrador não decide para sempre |
| Tamanho da memória reenviada | `.env` (`MEMORIA_MAX_TOKENS`) |

## Trace, tokens e custo

Cada execução grava um trace local, uma linha por passo (`data/execucoes/<id>.jsonl`), e o
resumo aparece em `GET /api/execucoes/{id}`. O raciocínio do modelo entra como passo próprio,
antes da tool que ele motivou:

```json
{"em": "...", "tipo": "raciocinio", "agente": "extrator", "doc_id": "5905cd34...",
 "texto": "**Considering investment lines**\n\nI'm thinking I might need to look at the capital expenditures related to intangible assets. [...] I should definitely check out pages 11 and 12 for those investment lines."}
{"em": "...", "tipo": "tool", "agente": "extrator", "doc_id": "5905cd34...", "tool": "salvar_indicador",
 "ok": false, "ms": 271, "argumentos": "{\"campo\": \"alavancagem\", ...}"}
{"em": "...", "tipo": "especialista", "agente": "extrator", "doc_id": "5905cd34...", "estado": "concluido",
 "gravacoes": {"salvar_indicador": 9, "fechar_extracao": 1}, "segundos": 52.3, "uso": {"total": 273303}}
{"em": "...", "tipo": "conferencia", "rodada": 1, "completo": true, "pendencias": 0}
```

- **Tokens e custo** são contados por agente e por documento. O preço vem da tabela `PRECOS` do
  `.env`; modelo sem preço aparece com os tokens e com custo nulo, não com um número inventado.
- **O raciocínio** é o resumo que a OpenAI devolve (`ESFORCO_RACIOCINIO` e `RESUMO_RACIOCINIO`
  no `.env`), em inglês, mesmo com o prompt em português. Sem um esforço de raciocínio ligado o modelo não raciocina, e não há o que
  registrar: foi o que aconteceu nas primeiras execuções com a base nova.
- **As mensagens completas** de cada agente ficam em `data/execucoes/<id>/` e saem em
  `GET /api/execucoes/{id}/mensagens?agente=`: o pedido, o raciocínio, cada tool com os
  argumentos e o resultado, e a resposta. É o que permite entender uma escolha errada.
- **Tudo é local de propósito.** O trace e as mensagens têm texto dos documentos.
- A **auditoria do servidor MCP** é o registro completo: toda chamada de toda tool, com o
  resultado, por execução.

## Combate à alucinação: como um número chega ao relatório

Nenhum número do relatório é escrito pelo modelo. O caminho de cada um:

1. **O agente aponta, o código lê.** Valor de tabela: o agente diz o chunk, a linha e a coluna, e
   o servidor MCP lê a célula. Valor de texto: o trecho tem de existir literalmente no chunk e
   conter o número inteiro. A unidade, a página, o ano e o escopo saem do documento, não do agente.
2. **A fonte tem de ser a do indicador.** A linha ou a frase tem de mencionar o indicador, ser do
   tipo certo (dinheiro, percentual, múltiplo), vir da demonstração certa e ser da companhia
   inteira. Uma célula não entra em dois campos.
3. **"Não encontrado" tem prova.** O servidor procura os termos no documento inteiro. O que
   aparece com valor tem de ser gravado; o que só é mencionado tem de ser descartado página por
   página, com o motivo. A evidência vai para o relatório.
4. **O que é calculado é do código**, com fórmula fixa, e fica separado do que a empresa informa.
5. **Texto do modelo leva marcadores, não números.** No resumo e nas respostas, os valores entram
   por marcador, que o código preenche com a unidade e a página. Citação tem de existir no trecho
   citado. Número solto é recusado.
6. **Validações automáticas** comparam o informado com o calculado e geram alerta. Nada é
   corrigido em silêncio.
7. **A ordem e a completude são do código**: o servidor diz o que está liberado e recusa o que
   chega fora de ordem; a execução só termina como completa quando a lista de pendências está vazia.

O detalhe de cada conferência, com o erro que ela barra, está no
[README do servidor MCP](../mcp-server/README.md#portões-o-que-precisa-conferir-para-entrar-na-base).

## Avaliação de qualidade

### Como foi medido

- **Gabarito** (`avaliacao/gabarito.json`): os 8 indicadores dos 3 documentos (24 campos), com
  status esperado, valor, página e valor do ano anterior, lidos à mão na camada de texto dos PDFs,
  sem passar pelo índice nem pelos agentes. Traz também os valores que as fórmulas devem dar, os
  alertas automáticos que devem disparar, as seis inconsistências do texto dos documentos
  conferidas no PDF e os termos e fontes esperados nas cinco perguntas.
- **Avaliação** (`scripts/avaliar.py`): lê a base pelo servidor MCP e compara. Mede status,
  valor, página, ano anterior, valor calculado, **invenção** (o que a empresa não informa e o
  sistema gravou como informado), alertas, inconsistências da fonte, perguntas e, direto no
  indexador, se cada citação guardada existe no trecho citado.

```bash
uv run python scripts/avaliar.py --usuario <usuário> --senha <senha>
```

### Resultado (15ª execução, a que está em `entrega/`)

A 15ª é a execução final com PydanticAI e LangGraph, o raciocínio ligado e a memória dos
especialistas.

| Medida | Resultado |
|---|---|
| Status certo (informado ou não encontrado) | 24 de 24 |
| Valor informado certo | 13 de 13 |
| Página certa | 13 de 13 |
| Valor do ano anterior certo | 9 de 9 |
| Valor calculado certo | 10 de 10 |
| Invenção (não informado gravado como informado) | 0 de 11 |
| Alertas automáticos esperados que dispararam | 2 de 2 |
| Inconsistências conhecidas do texto da fonte registradas | 4 de 6 |
| Perguntas com o conteúdo e a fonte esperados | 5 de 5 |
| Citações que existem literalmente no trecho citado | 33 de 33 |

A tabela campo a campo está em [`entrega/avaliacao.md`](entrega/avaliacao.md).

O que ficou abaixo do máximo: das seis inconsistências conhecidas no texto dos documentos, o
validador registrou quatro. É a medida que mais varia entre execuções (de 3 a 5), com qualquer
uma das duas bases.

Sobre essa medida: a lista de inconsistências do gabarito é o que foi conferido no PDF, não tudo o
que existe. Duas saíram da minha leitura; as outras quatro foram apontadas pelo validador em alguma
execução e só entraram no gabarito depois de conferidas no PDF (um ano trocado numa nota da Brasal,
duas datas erradas em quadros da Millet, a frase do EBITDA consolidado da Light).

### Como a qualidade foi construída: o histórico das execuções

Cada execução real do relatório completo, nos três PDFs e com a base zerada, foi avaliada contra o
gabarito. Cada erro virou uma conferência no servidor MCP, com teste, e não só um ajuste de
prompt. Todas as linhas abaixo usam o gabarito final.

| # | Tempo | Custo | Status | Valor | Calculado | Invenção | Perguntas | O que a avaliação mostrou → o que entrou |
|---|---|---|---|---|---|---|---|---|
| 1 | 118 s | — | — | — | — | — | — | Ainda sem gabarito. Na leitura: perguntas respondidas antes das extrações; "alavancagem" de 76%; capex dobrado; sem ano anterior → ordem imposta pelo servidor; a fonte tem de mencionar o indicador; uma célula, um campo; ano anterior lido pelo servidor |
| 2 | 132 s | US$ 0,36 | 23/24 | 12/13 | 5/10 | 1/11 | 4/5 | Margem inventada a partir da frase do EBITDA; lucro tirado do texto, sem o sinal; valores de apoio "gravados" sem terem sido → tipo do número; demonstração só de tabela; fechamento exige os valores de apoio; validador no modelo forte |
| 3 | 169 s | US$ 0,63 | 23/24 | 12/13 | 6/10 | 0/11 | 5/5 | Componente da nota de caixa gravado como caixa; "o documento não traz o apoio" dito com as linhas na página → linha com o nome do valor; prova para a falta de apoio; rótulo sem diferença de acento |
| 4 | 207 s | US$ 0,62 | 22/24 | 10/13 | 10/10 | 0/11 | 3/5 | Dívida líquida (num quadro) e alavancagem (numa frase) dadas como ausentes; linha sem rótulo → o que tem valor não pode ser descartado; valor calculado cita as páginas de origem |
| 5 | 206 s | US$ 0,67 | 24/24 | 13/13 | 10/10 | 0/11 | 0/5 | Extração sem erro. O orquestrador delegou as perguntas sem o enunciado, e as respostas eram a outras perguntas → enunciado oficial exigido pelo servidor; pendências do agente como contexto; gabarito das perguntas mais exigente |
| 6 | 147 s | US$ 0,63 | 24/24 | 13/14 | 9/9 | 0/10 | 5/5 | Linha de tabela citada como "texto": capex da Light só com o imobilizado → texto não vale em chunk de tabela; o capex informado é um total; valor de demonstração vem da própria demonstração |
| 7 | 501 s | US$ 1,05 | — | — | — | — | — | **Incompleta.** Um portão novo tomou o financiamento "BNDES - Capex" por capex informado, e a extração da Light não fechou → regra restrita aos quatro indicadores para os quais foi pensada |
| 8 | 163 s | US$ 0,73 | 24/24 | 12/13 | 10/10 | 0/11 | 4/5 | EBITDA da distribuidora da Light, com uma nota; motivo da receita de uma controlada → entre as frases de EBITDA, vale a que bate com a demonstração; prompts do analista e do pesquisador |
| 9 | 116 s | US$ 0,64 | 24/24 | 13/13 | 10/10 | 0/11 | 5/5 | Sem erro nos indicadores nem nas perguntas |
| 10 | 156 s | US$ 0,69 | 24/24 | 12/13 | 10/10 | 0/11 | 5/5 | Repetição da anterior: o lucro da Millet veio da linha dos controladores (14.743 de 18.891) → o lucro é o total, antes da participação dos minoritários |
| 11 | 297 s | US$ 0,86 | 24/24 | 13/13 | 10/10 | 0/11 | 5/5 | Só ficou completa na 3ª rodada da conferência final. Os operandos do EBITDA da Light não foram gravados, e o alerta do sinal omitido não disparou → operandos exigidos mesmo com o EBITDA informado |
| 12 | 130 s | US$ 0,63 | 24/24 | 13/13 | 10/10 | 0/11 | 5/5 | A entregue com o SDK de agentes da OpenAI. Sem erro nos indicadores, nos alertas automáticos nem nas perguntas |
| 13 | ~3 min | US$ 0,75 | 24/24 | 13/13 | 8/10 | 0/11 | 5/5 | **Primeira com PydanticAI e LangGraph.** O total da nota de empréstimos da Brasal (3.417) gravado como a parte circulante: dívida contada duas vezes, e a dívida líquida e a alavancagem calculadas erradas → empréstimo circulante e não circulante vêm do balanço |
| 14 | 146 s | US$ 0,58 | 24/24 | 13/13 | 10/10 | 0/11 | 5/5 | Sem erro nos indicadores. Ainda sem raciocínio registrado: sem um esforço de raciocínio configurado, o modelo não raciocina → `ESFORCO_RACIOCINIO` |
| 15 | 332 s | US$ 1,02 | 24/24 | 13/13 | 10/10 | 0/11 | 5/5 | **A entregue.** Com o raciocínio ligado. A segunda chamada do extrator da Light, a que levava a memória, foi recusada pela OpenAI (erro 400: chamada de tool sem o raciocínio que a precedeu); a terceira fechou a extração → a memória tira também o id do item, com teste e conferido numa conversa real |

As execuções 1 a 8 rodaram com os serviços fora do Docker; da 9 em diante, pelos contêineres.
Da 13 em diante, com PydanticAI e LangGraph.

O que o histórico mostra:

- **A invenção caiu para zero na 3ª execução e ficou em zero.** É a medida que o teste mais cobra:
  informação ausente como "não encontrado", sem estimativa.
- **O erro mudou de lugar a cada rodada.** O modelo pequeno procura o caminho mais curto: quando
  uma porta fecha, ele tenta a do lado (a nota como atalho, a linha de tabela citada como texto).
  Por isso as regras estão no servidor, e não no prompt.
- **Um portão pode errar** (execução 7). O sistema respondeu do jeito certo: não entregou
  relatório, terminou como `incompleta` e a auditoria mostrou onde travou.
- **Trocar a base não mudou a qualidade** (execuções 13 a 15): o mesmo status, zero invenção e
  as cinco perguntas. O erro da 13 foi de escolha de linha, do tipo que os portões cobrem, e virou
  portão.
- **A conferência final faz diferença** (execução 11): o orquestrador disse que tinha terminado
  duas vezes com pendências na base; o código devolveu a lista, e a terceira rodada fechou.
- **A avaliação também precisou ser corrigida.** A conferência das perguntas por nome de empresa
  e fonte contou como certas respostas a outras perguntas (execução 5), e o gabarito das
  inconsistências da fonte estava incompleto (o validador achou quatro que eu não tinha visto).

### Estabilidade entre execuções

Comparando indicador a indicador (mesmo status, mesma origem e mesmo valor):

| Comparação | Indicadores iguais | O que mudou |
|---|---|---|
| 9 e 10 | 23 de 24 | lucro da Millet (a linha dos controladores) |
| 11 e 12 | 23 de 24 | EBITDA calculado da Light (operandos não gravados na 11) |
| 9 e 12 | 24 de 24 | nada |
| 14 e 15 (PydanticAI e LangGraph) | 23 de 24 | dívida líquida da Light: o mesmo valor, lido da tabela (9.032.236 R$ mil) na 14 e da frase (9.032 R$ milhões) na 15; os dois estão certos |

Os dois campos que variaram até a 12ª viraram portão. O status dos 24 campos e as cinco perguntas
foram iguais nas execuções 9 a 15, com as duas bases. O que mais varia é a leitura do validador:
ele registrou de 3 a 5 das 6 inconsistências conhecidas, e não as mesmas a cada vez. Tempo e custo
também variam: de 116 a 332 s e de US$ 0,58 a US$ 1,02.

## Custo e desempenho

Medido na 15ª execução (a entregue): relatório completo dos três documentos, com a base zerada,
pelos contêineres, num Core i5-10400 sem GPU. Os especialistas de uma onda rodam em paralelo.

| | |
|---|---|
| Tempo total, do pedido ao relatório montado | **332 s** |
| Chamadas ao modelo | 160 |
| Tokens | 3,0 milhões (87% da entrada em cache) |
| Custo | **US$ 1,02** |
| Chamadas de tool no servidor MCP | 284, das quais 21 recusadas por um portão e refeitas |
| Decisões do orquestrador (ondas) | 7 |
| Delegações (A2A) | 20 |
| Passos de raciocínio registrados | 72 |

Por documento (extração, revisão, análise e resumo; os tempos somam o trabalho dos quatro
especialistas, que em parte roda em paralelo):

| Documento | Páginas | Tempo dos especialistas | Tokens | Custo |
|---|---|---|---|---|
| Brasal | 51 | 155 s | 552 mil | US$ 0,26 |
| Millet | 43 | 192 s | 962 mil | US$ 0,32 |
| Light | 124 | 253 s | 1,25 milhão | US$ 0,35 |

As cinco perguntas, a montagem do relatório e o orquestrador somam os US$ 0,09 restantes.

Por agente:

| Agente | Modelo | Chamadas | Tokens | Custo |
|---|---|---|---|---|
| extrator | gpt-5.4-mini | 61 | 1,80 milhão | US$ 0,33 |
| validador | gpt-5.4 | 26 | 526 mil | US$ 0,47 |
| analista | gpt-5.4-mini | 25 | 393 mil | US$ 0,11 |
| pesquisador | gpt-5.4-mini | 21 | 131 mil | US$ 0,05 |
| redator | gpt-5.4-mini | 13 | 51 mil | US$ 0,02 |
| orquestrador | gpt-5.4-mini | 14 | 104 mil | US$ 0,03 |

- **O raciocínio quase dobrou custo e tempo.** A 14ª (mesma base, sem raciocínio) custou
  US$ 0,58 em 146 s; a 15ª, US$ 1,02 em 332 s, com o dobro de tokens de saída (44 mil contra 22
  mil), que são os mais caros. Parte do tempo foi a extração da Light, que precisou de três
  chamadas (o erro da memória, ver o histórico). A qualidade medida foi a mesma. Com
  `ESFORCO_RACIOCINIO` vazio, o raciocínio desliga e o custo volta ao da 14ª; o que se perde é o
  porquê de cada escolha no trace.
- O **validador** é perto de metade do custo: é o único no modelo forte, lê páginas inteiras, e é
  quem mais raciocina.
- O **extrator** é quem mais gasta tokens, quase todos em cache: cada tabela lida fica no
  histórico e é reenviada a cada passo.
- **Uma conversa** custa por volta de US$ 0,01 a 0,03 por turno (pergunta respondida com fontes,
  raciocínio ligado). O terceiro turno de uma conversa medida levou ao pesquisador 2 chamadas
  anteriores, cerca de 9 mil tokens de memória.
- Preços usados (por milhão de tokens, da página de cada modelo em 13/11/2026): gpt-5.4-mini a
  US$ 0,75 de entrada, 0,075 em cache e 4,50 de saída; gpt-5.4 a US$ 2,50, 0,25 e 15,00. Ficam em
  `PRECOS`, no `.env`.
- **Indexação**, que acontece antes e uma vez por documento (medida no
  [indexador](../indexer/README.md#resultados-medidos)): 86 s para os três PDFs com o Azure
  Document Intelligence (cerca de US$ 2,18 pelas 218 páginas, a preço de tabela) ou 26 min com o
  Docling, sem custo por página; mais US$ 0,03 de embeddings e de leitura de gráficos.

Somando: cerca de **US$ 1 por lote de três documentos** com o raciocínio ligado (US$ 0,6 sem ele)
e o Docling, ou de US$ 2,8 a 3,2 com a leitura pela Azure, e de **2 a 6 minutos** de agentes.

## Decisões e trade-offs

| Decisão | Por quê | O que custa |
|---|---|---|
| **O agente aponta, o código lê** (portões no servidor MCP) | O modelo erra número com confiança. Lendo a célula e conferindo o trecho, um valor errado tem de ser um erro de *escolha*, que é mais raro e que a avaliação mede | Mais chamadas de tool; depende de as tabelas do índice estarem bem reconstruídas |
| **Regras no servidor, não no prompt** | Prompt pede; portão garante. Cada regra que só estava no prompt foi quebrada em alguma execução | Mais código e mais testes; o servidor fica específico de demonstrações financeiras |
| **Orquestrador decide, código confere** | Pedido em texto livre e agentes novos sem mudar código. A ordem e a completude, que não podem falhar, ficaram no servidor e na conferência final | O orquestrador gasta tokens para fazer o que um roteiro fixo faria de graça no caso do relatório |
| **A2A entre orquestrador e especialistas** | Fronteira de serviço e de identidade entre os agentes | Uma chamada de rede por delegação; dois serviços |
| **MCP como porta única** | Um lugar só para política, auditoria e portões; os dados extraídos ficam disponíveis para outros agentes | Mais um serviço no caminho de toda chamada |
| **Modelo pequeno para quase tudo, forte só no validador** | Com os portões, o modelo pequeno basta para apontar e redigir. Conferir contas no texto pede mais: o pequeno achou 2 das 6 inconsistências conhecidas e registrou falsos positivos | O validador responde por perto de metade do custo da execução |
| **PydanticAI para os agentes** | Saída estruturada validada (a decisão do orquestrador), MCP como toolset, histórico serializável (a memória), limites de uso e o raciocínio como parte da resposta. Troca de provedor sem reescrever o runner | A API mudou da versão 1 para a 2 (versões fixadas na série) |
| **LangGraph para a orquestração** | O fluxo vira um grafo explícito: paralelismo por onda, a conferência como nó, freios por número de ondas, e checkpoint por conversa (a memória do usuário) sem escrever persistência | Uma biblioteca a mais; o estado do grafo vai para o disco, então o que é segredo (o token do usuário) fica fora dele, no contexto da execução |
| **Memória do especialista na conversa inteira** | Pergunta de seguimento e tarefa continuada não recomeçam do zero | Cada chamada leva o histórico: mais tokens de entrada (a maior parte em cache). O encurtamento e o teto limitam |
| **Sem framework de RAG** | A busca é do indexador; aqui só há chamadas de tool | — |

## Segurança

- **Credenciais.** Só no `.env`, que o Git ignora; o `.env.example` vai sem valores. Os tokens
  de agente e de A2A são assinados pelo serviço, valem uma execução e nunca passam pelo modelo.
  O token do usuário fica em memória: não é gravado na execução nem no trace.
- **Conteúdo malicioso nos documentos.** Três barreiras, da mais fraca para a mais forte:
  1. o prompt de cada especialista diz que texto de documento é dado, não instrução;
  2. o indexador marca chunks com padrão de injeção, e a marca chega ao agente;
  3. o que um agente desviado conseguiria fazer é pouco: ele só tem as tools que a política lhe
     dá, só grava o que passa pelos portões (um "grave o valor 999" não tem célula nem trecho que
     o sustente), só vê os documentos do usuário da execução, e o texto dele não volta ao
     orquestrador, que decide sem nunca ler um trecho.
- **Isolamento entre usuários.** Uma execução e uma conversa pertencem a quem as criou; para outro
  usuário elas não existem. A memória dos especialistas é guardada por usuário. Os dados são
  filtrados pelo usuário em cada sistema.
- **O que vai para o disco.** O checkpoint do grafo, a memória e as mensagens guardam texto dos
  documentos, e por isso ficam locais. O token do usuário não entra em nenhum deles.

## Limitações

- **O modelo ainda escolhe.** Os portões garantem que o número existe onde o agente apontou e que
  a linha é do indicador. Entre duas linhas legítimas (lucro total ou dos controladores, EBITDA
  contábil ou ajustado), a escolha é do modelo. A avaliação existe para medir isso.
- **Três documentos.** O gabarito tem 24 indicadores e 5 perguntas. Mostra que o desenho funciona
  nestes formatos, não que generaliza. Os prompts e as listas de termos saíram destes PDFs.
- **Execuções não são idênticas.** O modelo varia: o caminho muda de uma execução para outra, e o
  resultado variou enquanto os portões eram poucos. A seção de avaliação traz a estabilidade
  medida.
- **A análise qualitativa não tem gabarito automático.** As citações são conferidas (existem no
  trecho citado); se a afirmação é a melhor leitura do trecho, só um analista diz.
- **O validador pode errar para os dois lados**: deixar passar uma frase incoerente ou apontar
  uma que está certa. Ele não acha as mesmas inconsistências a cada execução (de 3 a 5 das 6
  conhecidas). Todo alerta dele sai com a citação, para o analista julgar.
- **Um portão novo pode travar um campo.** Aconteceu na 7ª execução (ver o histórico). O
  sistema não entrega relatório nesse caso: termina como `incompleta` e diz o que faltou.
- **O orquestrador gasta chamadas à toa.** Ele ainda delega itens que a lista marca como não
  liberados; o servidor recusa a gravação, e o trabalho daquela chamada se perde.
- **Execuções em memória.** O serviço roda uma execução por tarefa assíncrona, sem fila. Se cair
  no meio, a execução fica como `interrompida`; o que já foi gravado continua na base, e pedir de
  novo retoma do que falta.
- **Documentos anuais.** Os três PDFs são demonstrações de 2022 com a coluna de 2021. Não há
  dados trimestrais, e "período" aqui é o exercício.
- **A memória custa tokens.** Cada chamada de um especialista numa conversa longa leva o
  histórico dele. O encurtamento e o teto limitam, e a maior parte entra como cache, mas o custo
  cresce com a conversa. O teto é por chamada, não por conversa.
- **O raciocínio registrado é um resumo.** É o que a OpenAI devolve, não a cadeia completa do
  modelo. Explica a escolha na maior parte das vezes; não é uma prova dela.
- **A classificação da mensagem é do modelo.** Se ele classificar uma pergunta legítima como
  fora do escopo, o usuário recebe as opções e precisa reformular. O erro é para o lado seguro:
  nada sem fonte chega à tela. O trace mostra a classificação de cada turno.

## Caminho para produção no Azure

Para milhares de documentos por trimestre:

- **Leitura.** Azure AI Document Intelligence (`prebuilt-layout`) no lugar do Docling: nos três
  PDFs o parse caiu de 26 min para 86 s. Em lote, com fila (Service Bus) e workers em Container
  Apps Jobs, escalando por tamanho da fila.
- **Índice.** Azure AI Search no lugar do Qdrant: busca híbrida, filtros por documento e por
  usuário e semantic ranker, com o mesmo contrato de chunk.
- **Modelos.** Azure OpenAI pelo AI Foundry, com implantações separadas para o modelo pequeno e
  o forte. A extração e a análise por documento são independentes: rodam em paralelo e, quando o
  prazo permite, em lote (Batch API, metade do preço).
- **Agentes.** O orquestrador e os especialistas como agentes do Foundry Agent Service, que já
  fala MCP e A2A. Cada agente com identidade no Entra ID (Agent ID), no lugar dos segredos
  compartilhados. O servidor MCP atrás do API Management.
- **Roteiro fixo para o caminho conhecido.** Em volume, o relatório padrão não precisa de um
  orquestrador decidindo: a ordem já está no servidor. Um fluxo durável (Durable Functions)
  chamaria os especialistas na ordem das pendências, e o orquestrador com modelo ficaria para os
  pedidos em texto livre.
- **Observabilidade.** OpenTelemetry para o Application Insights: um trace por execução, do
  pedido até cada chamada de tool. Painéis com custo por documento, taxa de rejeição dos portões
  por agente e taxa de execuções incompletas. Queda nesses números depois de uma mudança de
  prompt ou de modelo é o alarme.
- **Avaliação contínua.** O gabarito cresce com cada documento conferido por um analista, e a
  avaliação roda a cada mudança de prompt, de modelo ou de portão, antes de ir para produção
  (avaliações do AI Foundry).
- **Custo.** O cache de prompt já responde pela maior parte dos tokens de entrada. O que mais
  pesa é o histórico que cresce a cada chamada de tool: resumir resultados antigos e devolver
  tabelas mais curtas reduz. Reprocessar só o que mudou (a impressão do índice já diz o quê).

## Estrutura do código

```
agents/
├─ agentes/                 um .md por agente (o orquestrador também)
├─ app/
│  ├─ main.py               API do orquestrador: conversas, execuções, mensagens
│  ├─ grafo.py              uma execução como grafo LangGraph: orquestrador, delegar, conferencia, fim
│  ├─ a2a.py                cliente A2A: catálogo e delegação
│  ├─ especialistas.py      serviço A2A: card, endpoint, só o orquestrador entra, memória, relato
│  ├─ runner.py             roda um agente PydanticAI: modelo, toolset MCP, passos, raciocínio, freios
│  ├─ memoria.py            histórico de cada especialista por conversa, e o encurtamento
│  ├─ registry.py           lê os arquivos de agente, sem reiniciar
│  ├─ tokens.py  mcp.py     os dois tokens; a conexão MCP de cada agente
│  ├─ execucoes.py          execuções, conversas, trace e mensagens em arquivo
│  ├─ costs.py              tokens e custo por agente e por documento
│  ├─ evaluation.py         a conta da avaliação contra o gabarito
│  ├─ security.py  config.py  cli.py
├─ avaliacao/gabarito.json  valores conferidos à mão nos PDFs
├─ scripts/                 avaliar.py, gerar_entrega.py
├─ entrega/                 relatório, JSON por documento (enxuto e completo), respostas e avaliação
└─ tests/                   67 testes sem rede: modelo com roteiro, servidor MCP em memória,
                            A2A de verdade por ASGI e grafo com checkpoint em memória
```
