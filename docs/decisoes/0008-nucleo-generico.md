# 0008 — Núcleo genérico: o código de um domínio mora no pacote dele

**Contexto.** Os domínios plugáveis (0007) puseram o YAML, os prompts e o gabarito de cada domínio
numa pasta, mas o núcleo continuava financeiro em quatro frentes:

- **Servidor MCP:** 15 ramos `atual().financeiro`, as fórmulas e as regras da fonte em
  `app/regras` e `app/tools`, e tools chamadas `salvar_indicador` e `montar_relatorio_empresa`.
- **Indexador:** o nome das demonstrações no corte em trechos e no parser do Azure.
- **Agentes:** prompts base escritos para balanços (laudos só funcionava substituindo os seis).
- **Saídas:** chaves `empresa` e `indicadores` nas saídas, no gabarito e no frontend.

**Decisão.** O núcleo não sabe de domínio nenhum. O que é de um domínio entra por um destes lugares:

| Onde | O quê |
|---|---|
| `dominio.yaml` | dados: entidade, recorte, metadados, campos (com `texto` e `data`), categorias (com `descricao`), perguntas e textos, inclusive os do relatório (`textos.relatorio`) |
| `prompts/<agente>.md` | o vocabulário e as regras de leitura de cada agente, no modo `complemento`; o cabeçalho pode trocar a `descricao` que o orquestrador lê |
| `intencoes.yaml` | as frases do roteador e as `palavras_genericas` de um nome |
| `plugin/mcp/` | código do servidor MCP atrás de `PluginMCP` (app/plugins/interface.py): cálculo, validações, conferência da fonte, fechamento, regras de inconsistência, análise |
| `plugin/indexador.py` | código do indexador atrás de `PluginIndexador`: títulos de seção de nome fixo, rodapé conhecido, subtítulo de tabela, reserva de metadados, frase das figuras |

O plugin é lido da pasta dos pacotes (a mesma montada nos contêineres), não é instalado, e importa
do núcleo só `app/plugins/api.py`. Cada gancho tem o comportamento neutro em `PluginGenerico`, o de
um domínio sem plugin.

**Prompts.**
- **Método e vocabulário.** O prompt base de cada agente é o método. As palavras do domínio entram
  por marcador (`$documentos`, `$entidade`, `$entidades`, `$campos`, `$categorias`, `$n_perguntas`,
  `$perguntas`, `$recorte`), trocado com `string.Template.safe_substitute`. É seguro porque os
  marcadores de valor (`{{doc_id.campo}}`) e o "R$" não são tocados.
- **Tools.** As descrições usam os mesmos marcadores e são trocadas no `tools/list`, por quem pede.
- **O que 0007 tinha adiado.** A separação em método e vocabulário ficara para quando houvesse um
  terceiro domínio, por medo de perder a nota. Ela foi feita e medida: o complemento do financeiro
  repete as regras de antes com o mesmo texto.

**Nomes.**
- **Tools:** `salvar_campo`, `comparar_campos`, `montar_relatorio_documento`, `ler_relatorio_documento`.
- **Chaves de saída:** `entidade`, `campos`, `comparavel_gravado_pelo_servidor`.
- **Decisão do orquestrador:** o campo `documento` e a intenção `relatorio_documento`.
- **Compatibilidade.** A extração publicada antes é lida no formato novo, sem mexer no banco. A
  tabela `relatorios_empresa` e a coluna `alternativa_consolidada` ficam com o nome, marcados para a
  guarda.
- **Qdrant.** A coleção passa a se chamar `documentos`; num índice antigo, ela é um alias da
  `financial_docs`.

**Guarda.** `make generico` (no `make lint` e no CI) lê o código de `*/app/` e falha se um nome ou
um texto do código voltar a ter vocabulário das demonstrações. Comentários e docstrings ficam de
fora, porque contam a história das regras. Uma linha que precisa do termo leva `# genérico: <motivo>`.

**Prova.**
- **Sem mudança para o modelo nas fases de mudança de lugar.** O contrato das tools do financeiro
  ficou igual byte a byte na fase do plugin do servidor MCP. Os 641 chunks dos três PDFs saíram
  iguais na fase do plugin do indexador.
- **Rodadas pagas do zero, com a base zerada:**
  - financeiro, depois dos prompts genéricos e das tools renomeadas: 24/24, 13/13, 9/9, 10/10, 0
    de 11 inventados, 2/2, 7/7, 5/5;
  - laudos com complementos: 32/32, 17/17, 0 de 15, 1/1, 4/4;
  - contratos, um terceiro domínio só com YAML e prompts (texto, data, dinheiro sem plugin, sem
    recorte): 40/40, 28/28, 0 de 12, 1/1 (a vigência que termina antes de começar), 4/4, 20/20.
    Os valores em reais sem escala ("R$ 120.000,00") e o valor por extenso ("cento e vinte mil
    reais", que não declara a unidade) entraram no núcleo nesta rodada;
  - a rodada final do financeiro, com o código todo: 24/24, 13/13, 9/9, 10/10, 0 de 11, 2/2, 7/7,
    5/5, 35/35.
- **Robustez achada no caminho.** Uma busca que caiu no meio deixava a memória do especialista
  com uma chamada de tool sem resposta, e o documento travava nas delegações seguintes. A memória
  passou a deixar de fora as chamadas sem resposta.

**Consequência.** Um domínio novo é `make novo-dominio ID=x` e editar o YAML. Prompts, frases e
plugin entram só quando ajudam. O custo é a indireção dos ganchos e um contrato de tools por
domínio (`mcp-server/tests/contratos/tools-<id>.json`).
