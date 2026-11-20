# Domínios

Cada pasta é um **pacote de domínio**: o que muda de um tipo de documento para outro (que
documentos, de quem é cada um, o que extrair, o que analisar, que perguntas fazer, o que a tela
diz). O núcleo dos sistemas é o mesmo para todos. A decisão e as fases estão em
[docs/decisoes/0007](../docs/decisoes/0007-dominios-plugaveis.md).

| Pacote | Documentos |
|---|---|
| [`financeiro/`](financeiro/dominio.yaml) (padrão) | Demonstrações financeiras de empresas |

## Criar um domínio

1. Copie `financeiro/` para `dominios/<id>/` (o `id` é o nome da pasta: minúsculas, números e `_`).
2. Edite o `dominio.yaml`. O formato, com cada chave explicada, está em
   [`comum/pacotes.py`](../comum/pacotes.py):
   - `documento`: os tipos aceitos e frases típicas das primeiras páginas (`reconhecer`), que
     servem para sugerir o domínio no envio;
   - `entidade`: de quem é o documento (empresa, paciente, contrato);
   - `recorte`: `periodo` (exercício ou trimestre, com o anterior como comparável), `data` (um
     dia, como a data do exame) ou `nenhum`;
   - `metadados`: o que o indexador extrai do documento inteiro; um deles com `papel: entidade` e,
     com recorte, um com `papel: recorte`. Cada valor só vale com o trecho da página que o prova;
   - `campos`: o que os agentes extraem, com o tipo (`valor` em dinheiro, `percentual`,
     `multiplo`, `numero` com `unidade`, `data`, `texto`);
   - `categorias` da análise, `perguntas` padrão do relatório e `textos` da interface.
3. `make dominios` valida todos os pacotes e mostra o que cada um define. Um pacote inválido
   impede a subida dos sistemas, com o motivo.
4. Contas e regras que não cabem em YAML (fórmulas, inconsistências): um plugin em Python no
   sistema que as usa, apontado por `plugin`. Sem plugin, o domínio usa só os portões genéricos:
   citação literal, número só por marcador ou citação, ausência com prova.

Os pacotes ficam montados em `/app/dominios` nos contêineres (só leitura). Mudou um pacote:
reinicie os contêineres, porque o esquema das tools que os agentes veem sai dele.
