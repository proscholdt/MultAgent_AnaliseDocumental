# 0007 — Domínios plugáveis: o que muda de um tipo de documento para outro fica num pacote

**Contexto.** O sistema nasceu para demonstrações financeiras, e o domínio está costurado em
seis lugares: os metadados do indexador (empresa, período e tipo em colunas, prompt e regex), as
unidades e os títulos de seção do corte em chunks (R$ mil, DRE, notas explicativas), os campos e
as fórmulas do servidor MCP (o `dominio.yaml` existia, mas exigia os 16 campos das fórmulas), o
período sempre anual com comparável, os prompts e as intenções dos agentes, e os textos da
interface. O que é valioso já é genérico: parse, OCR, chunks por estrutura, busca híbrida, filas,
grafo de ondas, A2A, memória, roteador semântico e, principalmente, os portões (citação literal,
número só por marcador ou citação, ausência com prova).

**Decisão.** Cada domínio é uma pasta, `dominios/<id>/`, e o núcleo não sabe qual domínio roda:

```
dominios/<id>/
  dominio.yaml     o contrato (formato em comum/pacotes.py, validado na subida dos três sistemas):
                     documentos aceitos e frases para reconhecê-los · a entidade (de quem é o
                     documento) e o recorte (período, data ou nenhum) · os metadados que o
                     indexador extrai, cada um só com o trecho que prova · os campos tipados que os
                     agentes extraem · as categorias da análise · as perguntas padrão · os textos
  prompts/<agente>.md   o que o agente precisa saber deste domínio (o método fica no prompt base)
  intencoes.yaml   frases do roteador do chat
  avaliacao/       gabarito e mensagens rotuladas
```

O que não cabe em YAML é **plugin em Python** no sistema que o usa, apontado por `plugin`: as
fórmulas e validações (MCP), as regras de inconsistência (MCP), os títulos de seção e as unidades
do corte em chunks (indexador). Declarar fórmulas com condições e exceções em YAML seria uma
linguagem nova, mais difícil de testar que o Python (a mesma razão do 0003).

O usuário escolhe o domínio por **coleção**: cria uma coleção com um domínio e envia os PDFs nela;
no envio, o sistema compara o PDF com as frases `reconhecer` de cada pacote e avisa se ele parece
de outro domínio. Relatórios e comparações acontecem dentro de uma coleção. Documento e coleção de
antes dos pacotes são do domínio padrão, `financeiro`.

**Fases**, cada uma com commit e testes, e a mesma trava: o pacote financeiro continua com nota
máxima no gabarito.

| Fase | O que muda |
|---|---|
| 0 | O formato do pacote (`comum/pacotes.py`, copiado aos três sistemas), o pacote `financeiro` (o antigo `mcp-server/config/dominio.yaml` mais entidade, recorte, metadados e textos), o servidor MCP lendo o pacote, `make dominios` |
| 1 | Indexador: coleções com domínio, metadados dinâmicos (os do pacote, em vez das colunas empresa/período/tipo), prefixo e payload pelo pacote, sugestão de domínio no envio, títulos de seção e unidades pelo plugin |
| 2 | Servidor MCP: o domínio por execução (no token do agente), o esquema das tools por domínio, campos de tipos novos (número com unidade, data, texto), recorte por data, fórmulas e inconsistências por plugin, relatório pelo pacote |
| 3 | Agentes: o domínio da coleção na execução, o prompt base com o complemento do pacote, intenções e "entidade" genéricas, textos do pacote |
| 4 | Frontend: coleções, escolha do domínio, textos e rótulos do pacote |
| 5 | A prova: o pacote `laudos` (exames laboratoriais), com laudos fictícios gerados e PDFs de exemplo baixados da internet, e um gabarito |

**Prompts por domínio (fase 3).** O arquivo `dominios/<id>/prompts/<agente>.md` tem dois modos:
`complemento` (padrão: entra no fim do prompt do agente) e `substitui` (é o prompt do agente
naquele domínio; modelo, turnos e raciocínio continuam os do agente). Os prompts dos agentes
continuam sendo os do domínio financeiro: separá-los em "método" e "vocabulário" mexeria no texto
que dá a nota máxima no gabarito, e só uma rodada paga diria se piorou. O domínio de laudos traz os
seus prompts no modo `substitui`. A separação fina fica para quando houver um terceiro domínio.

**Respostas e relatório são da coleção.** As respostas (`p1`, `p2`…) e o relatório consolidado
eram guardados por usuário. Com duas coleções, a `p1` dos laudos substituía a `p1` do financeiro.
A rodada da fase 5 mostrou isso. Agora as duas tabelas têm `colecao_id`: a migração 0003 no
Postgres e a coluna acrescentada na subida no SQLite. O que é por documento (extração, análise,
alertas, resumo) já era separado, porque o documento é de uma coleção só. O pedido sem coleção
roda na coleção padrão do usuário, a mesma do envio sem coleção no indexador: a primeira do domínio
padrão. Os scripts (`make resultados`, `avaliar`, `pendencias`, `pergunta`) seguem a mesma regra e
aceitam `COLECAO=`.

**O que ficou de fora, de propósito.** Os títulos de demonstração (balanço, DRE, DFC), o rodapé
"as notas explicativas são parte integrante" e a detecção de R$ no corte em chunks continuam no
código do indexador, para todo domínio: são regex que só disparam em texto financeiro. Num laudo
ou num contrato não acham nada e não mudam nada. Levá-los para um plugin seria arrumação com risco
de mexer no corte das demonstrações sem ganho para os outros domínios.

**Consequência.** Um domínio novo é uma pasta: YAML, prompts e, se precisar de conta, um plugin
pequeno. O custo é uma camada de indireção em cada sistema (o pacote em vez de constantes) e o
esquema das tools gerado por domínio. Laudos médicos são dado sensível: o pacote de prova usa só
documentos fictícios ou modelos publicados por laboratórios, nunca exames de pacientes reais.
