# Domínios

Cada pasta é um **pacote de domínio**: tudo o que muda de um tipo de documento para outro (que
documentos, de quem é cada um, o que extrair, o que analisar, que perguntas fazer, o que a tela
diz e, quando precisa, o código). O núcleo dos sistemas é o mesmo para todos e não tem vocabulário
de domínio nenhum (`make generico` confere). Decisões em
[docs/decisoes/0007](../docs/decisoes/0007-dominios-plugaveis.md) (os pacotes) e
[0008](../docs/decisoes/0008-nucleo-generico.md) (o núcleo genérico e os plugins).

| Pacote | Documentos |
|---|---|
| [`financeiro/`](financeiro/dominio.yaml) (padrão) | Demonstrações financeiras de empresas. Prompts, frases do roteador, gabarito e plugin em Python nos dois sistemas: fórmulas, validações, regras da fonte e inconsistências em reais (servidor MCP); os títulos fixos das demonstrações e a reserva de metadados (indexador) |
| [`laudos/`](laudos/dominio.yaml) | Laudos de exames laboratoriais (fictícios). Só YAML e prompts curtos, sem plugin; recorte por data |
| [`contratos/`](contratos/dominio.yaml) | Contratos (fictícios e uma minuta em branco). Só YAML e prompts; sem recorte, com campos de texto e de data e dinheiro sem plugin |
| [`_modelo/`](_modelo/dominio.yaml) | O modelo de onde `make novo-dominio` copia (pasta com `_` na frente não é um pacote) |

## Criar um domínio

1. `make novo-dominio ID=<id>` copia o modelo para `dominios/<id>/` (o `id` é o nome da pasta:
   minúsculas, números e `_`).
2. Edite o `dominio.yaml`. O formato, com cada chave explicada, está em
   [`comum/pacotes.py`](../comum/pacotes.py):
   - `documento`: os tipos aceitos e frases típicas das primeiras páginas (`reconhecer`), que
     servem para sugerir o domínio no envio;
   - `entidade`: de quem é o documento (empresa, paciente, contrato);
   - `recorte`: `periodo` (exercício ou trimestre, com o anterior como comparável), `data` (um
     dia, como a data do exame) ou `nenhum`;
   - `metadados`: o que o indexador extrai do documento inteiro; um deles com `papel: entidade` e,
     com recorte, um com `papel: recorte`. Cada valor só vale com o trecho da página que o prova;
   - `campos`: o que os agentes extraem, com o tipo (`valor` em reais, `percentual`, `multiplo`,
     `numero` com `unidade`, `data` em dd/mm/aaaa, `texto`), os `termos` procurados quando o agente
     diz que o campo não existe e a `mencao` que a linha ou a frase tem de ter;
   - `categorias` da análise (com uma `descricao` do que entra em cada uma), `perguntas` padrão
     do relatório e `textos` da interface e do relatório (`textos.relatorio`).
3. Opcionais, ao lado do `dominio.yaml`:
   - `prompts/<agente>.md`: o que cada agente precisa saber deste domínio, no fim do prompt dele
     (`modo: complemento`, o padrão). O prompt base de cada agente é o método, com as palavras do
     domínio por marcador (`$entidade`, `$documentos`, `$campos`, `$categorias`, `$n_perguntas`,
     `$perguntas`, `$recorte`): o complemento só diz o que é deste domínio. O cabeçalho pode dar a
     `descricao` que o orquestrador lê sobre o agente. `modo: substitui` troca o prompt inteiro;
   - `intencoes.yaml`: as frases de exemplo do roteador do chat e as `palavras_genericas` de um
     nome (as que não identificam a entidade), no formato de `financeiro/intencoes.yaml`. Sem ele,
     quem decide a intenção é o orquestrador;
   - `avaliacao/gabarito.json`: a nota automática ao fim de cada relatório (campos de texto pelo
     que o documento escreve, datas em dd/mm/aaaa); `avaliacao/busca.yaml`: os casos da avaliação
     da busca (`indexer/scripts/eval_search.py --dominio <id>`).
4. `make dominios` valida todos os pacotes e mostra o que cada um define. Um pacote inválido
   impede a subida dos sistemas, com o motivo.
5. Regras que não cabem em YAML (fórmulas, conferências da fonte, inconsistências que só valem
   neste tipo de documento): um plugin em Python, dentro do pacote, apontado por `plugin`:
   - `plugin/mcp/__init__.py` define `Plugin`, subclasse de `PluginMCP`
     ([mcp-server/app/plugins/interface.py](../mcp-server/app/plugins/interface.py));
   - `plugin/indexador.py` define `Plugin`, subclasse de `PluginIndexador`
     ([indexer/app/plugins/interface.py](../indexer/app/plugins/interface.py)).

   Cada gancho tem o comportamento neutro na interface: o plugin sobrescreve só o que precisa, e
   importa do sistema só o que está em `app/plugins/api.py`. O de
   [`financeiro/plugin/`](financeiro/plugin/) é o exemplo. Sem plugin, o domínio usa só os
   portões genéricos: citação literal, número só por marcador ou citação, ausência com prova,
   célula lida pelo código, unidade conferida.

## Ganchos do plugin

| Sistema | Gancho | Para quê |
|---|---|---|
| MCP | `validar_pacote` | exigências aos campos do pacote (as fórmulas precisam dos operandos) |
| MCP | `escopo_da_coluna`, `escopo_alternativo`, `ler_trecho` | a parte da entidade de um valor (consolidado), e o sinal dito no texto (prejuízo) |
| MCP | `conferir_fonte`, `campos_que_se_excluem`, `dica_celula_repetida`, `avisos_do_valor` | a linha ou a frase apontada é mesmo do campo |
| MCP | `evidencia_de_valor` | campo que o documento traz com valor não pode ser dado como não encontrado |
| MCP | `conferir_fechamento`, `calcular`, `campos_conferidos_por_calculo`, `validar` | valores de apoio, fórmulas e validações ao fechar a extração |
| MCP | `campos_enxutos` | os campos e a ordem da extração enxuta |
| MCP | `regras_inconsistencia` | regras do domínio na varredura das frases, na ordem |
| MCP | `conferir_item_analise`, `categoria_na_leitura`, `ordenar_analise`, `tipo_de_fonte`, `sujeitos_de_negacao` | a análise qualitativa |
| indexador | `titulo_de_secao`, `rodape_conhecido`, `linha_de_subtitulo` | a estrutura própria deste tipo de documento, no corte em trechos e no parser |
| indexador | `metadados_reserva`, `instrucao_figuras` | metadados por regra quando o LLM não prova; de que documentos são as figuras |

Os pacotes (e os plugins) ficam montados em `/app/dominios` nos contêineres (só leitura). Mudou um
pacote: reinicie os contêineres, porque o esquema das tools que os agentes veem sai dele.
