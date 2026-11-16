# Servidor MCP

A única porta dos agentes para documentos e ferramentas. Decide o que cada agente pode chamar,
confere tudo o que é gravado, calcula com fórmulas fixas, registra cada chamada e monta o
relatório. Os fluxos estão desenhados em [docs/fluxos.html](docs/fluxos.html).

```mermaid
flowchart LR
    AG[agente] -->|token do agente + JWT do usuário| ID[identidade]
    ID --> PO[config/policy.yaml] --> T[tool] --> PT[portão] --> DB[(SQLite)]
    T -->|busca, chunks| IDX[indexador]
    PO -. recusada .-> AG
    PT -. rejeitada: o que não conferiu .-> AG
    ID & PO & T --> AU[(auditoria)]
```

## Governança

- **Dois eixos**: o token do agente (JWT curto assinado com `MCP_AGENT_SECRET`) decide quais
  tools existem; o JWT do usuário (`X-User-Token`, repassado só ao indexador) decide quais
  documentos existem. Os dois têm de ser do mesmo usuário.
- **`config/policy.yaml`**: tools e limite de chamadas por agente, aplicada no servidor
  (`tools/list` só mostra o permitido), relida sem reiniciar. Agente fora do arquivo não tem
  acesso. Cada tool que grava tem um dono; o orquestrador não lê trecho de documento.
- **Auditoria**: toda chamada, com argumentos, resultado e hash da política (`GET /auditoria`).

## Portões

| Gravação | O que é conferido |
|---|---|
| `salvar_indicador` | O agente aponta chunk, linha e coluna; o servidor lê a célula, confere o ano da coluna, a unidade e que a linha é do indicador; o ano anterior sai da mesma linha. Texto: o trecho existe no chunk e contém o número |
| `salvar_nao_encontrado` | O servidor procura os termos no documento; se aparecem com valor, recusa mostrando a página. A busca vira evidência |
| `fechar_extracao` | Os 8 indicadores com valor ou ausência; valores de apoio para calcular o que falta; validações automáticas |
| análise, alertas, respostas | Citação literal no chunk; números só por marcador (`{{doc.campo}}`) ou dentro de trecho citado |

Inconsistências do próprio documento: `candidatos_inconsistencia` varre todos os chunks com regras
([app/regras/inconsistencias.py](app/regras/inconsistencias.py)) e devolve ao validador os trechos
suspeitos, cada um com o motivo. A regra acha; o validador lê e decide: registra
(`registrar_alerta`) ou descarta dizendo por quê (`descartar_candidato`: o motivo fica guardado e
na auditoria, fora do relatório). `concluir_revisao` recusa enquanto houver candidato sem
julgamento. Nos quadros, o candidato traz uma `prova`: os mesmos números (com separador de
milhar) achados em outro quadro do documento, com a coluna e o ano de lá.

| Regra | Marca |
|---|---|
| `conta_na_frase` | valor, variação e comparável na mesma frase que não fecham (2% de folga para arredondamento; 1,5 p.p. no percentual) |
| `variacao_contra_extracao` | frase com o valor de um indicador e uma variação em reais que não bate com os dois períodos da extração |
| `ano_comparado_com_ele_mesmo` | "encerrou 2021 em 3,32x, menor do que no 2021" |
| `projecao_para_o_passado` | cenário ou projeção para um ano anterior ao do documento |
| `data_do_quadro` | a frase que apresenta o quadro fala de uma data que não está nas colunas; duas colunas com o mesmo título e ano |

Calculado (variação, EBITDA, margem, dívida líquida, alavancagem, capex) usa fórmula fixa e nunca
aparece como informado. As validações (`ebitda_divergente`, `margem_divergente`,
`periodo_nao_confirmado`…) viram alertas com os dois lados da conta.

## Pendências

`pendencias` define a ordem: extração → revisão das fontes e análise → perguntas (com tudo de
todos os documentos) e resumos → relatório. As tools de gravação recusam o que chega antes da
hora, e o que ficou mais antigo que sua base volta como desatualizado. `montar_relatorio` gera o
Markdown a partir da base.

Com `doc_id`, `pendencias` lista só o que falta para o relatório daquela empresa (extração,
revisão, análise e resumo dela, sem as perguntas comparativas), e `montar_relatorio_empresa`
monta o relatório dela: resumo, indicadores, análise e alertas. Ele vale para a impressão do
documento em que foi montado.

## Rodar

```bash
cp .env.example .env          # só segredos: MCP_AGENT_SECRET, ADMIN_KEY
docker compose up -d --build  # :8100, endpoint MCP em /mcp (Streamable HTTP)
uv run pytest
```

A pasta `config/` é montada no contêiner: `policy.yaml` (quem vê e chama o quê) e `ajustes.yaml`
(tamanho do que a busca devolve ao modelo) valem sem build nem restart. Erro aparece em
`GET /health` (`erro_politica`, `erro_ajustes`).

## Mapa do código

```
app/
├─ main.py               aplicação HTTP: /mcp, /health, /governanca, /auditoria
├─ config.py             .env (segredos) + config/ajustes.yaml
│
├─ tools/                AS TOOLS, um arquivo por assunto
│  ├─ documentos.py      listar_documentos, buscar_trechos, ler_chunk, ler_pagina
│  ├─ extracao.py        salvar_indicador, salvar_nao_encontrado, fechar_extracao, ler_extracao
│  ├─ validacao.py       registrar_alerta, ler_alertas, concluir_revisao
│  ├─ analise.py         salvar_item_analise, registrar_ausencia_analise, ler_analise
│  ├─ respostas.py       comparar_indicadores, salvar_resposta, ler_respostas
│  └─ relatorio.py       salvar_resumo, pendencias, montar_relatorio(_empresa), ler_relatorio(_empresa)
│
├─ governanca/           QUEM PODE O QUÊ, em toda chamada
│  ├─ servidor.py        tools/list e tools/call: política, auditoria, span
│  ├─ identidade.py      token do agente + login do usuário
│  ├─ politica.py        config/policy.yaml, relida quando muda
│  ├─ auditoria.py       uma linha por chamada
│  ├─ catalogo.py        o @tool e o registro das tools
│  └─ erros.py           recusa de portão e erros que voltam ao agente
│
├─ regras/               O QUE OS PORTÕES CONFEREM
│  ├─ campos.py          os 8 indicadores, os valores de apoio, as categorias e as perguntas
│  ├─ formulas.py        valores calculados e validações automáticas
│  ├─ numeros.py         números no formato brasileiro e unidades
│  ├─ tabelas.py         ler a célula de uma tabela
│  ├─ texto.py           citação literal, número solto, alfabeto
│  ├─ evidencias.py      onde um indicador aparece e a prova de "não encontrado"
│  ├─ inconsistencias.py candidatos a erro do próprio documento, para o validador julgar
│  ├─ pendencias.py      o que falta e em que ordem
│  ├─ marcadores.py      {{receita_liquida}} → valor com unidade e página
│  ├─ relatorios.py      os relatórios em Markdown
│  └─ extracao_enxuta.py a extração no formato enxuto
│
└─ dados/                ONDE FICA
   ├─ banco.py           SQLite
   ├─ modelos.py         as tabelas
   ├─ base.py            leitura e gravação, sempre por usuário
   ├─ visoes.py          linhas do banco → dicionários para os agentes
   ├─ documentos.py      trechos em memória + a impressão do documento
   └─ indexador.py       cliente da API do indexador

config/                  policy.yaml (quem pode o quê) e ajustes.yaml
```

Para criar uma tool: uma função com `@tool` no arquivo do assunto, em `app/tools/`, e o nome
dela na entrada do agente em `config/policy.yaml`.
