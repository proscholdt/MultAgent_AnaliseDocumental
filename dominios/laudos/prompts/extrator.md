---
descricao: Extrai os resultados de UM laudo de exames, com a origem de cada valor, e fecha a extração. Precisa do doc_id.
---
## Neste domínio: laudos de exames laboratoriais

Cada documento é UM laudo. O `rotulo_linha` é o nome do exame, como está na primeira coluna; o `rotulo_coluna` é o cabeçalho da coluna do resultado, geralmente "Resultado". A célula pode trazer a unidade junto ("92 mg/dL"): o servidor lê e confere a unidade. Num resultado em texto corrido ("Resultado: 92 mg/dL"), o `valor` é só o número (`92`, `5,8`).

- `periodo` fica sempre `null`: o resultado é da data da coleta do laudo.
- O laudo não traz o exame: `salvar_nao_encontrado`, com o motivo ("o laudo não traz este exame").
- A coluna do resultado nunca é a dos valores de referência.

### Os exames

| campo | o exame | unidade |
|---|---|---|
| `glicose` | glicose, glicemia de jejum | mg/dL |
| `hemoglobina_glicada` | hemoglobina glicada, HbA1c | % |
| `colesterol_total` | colesterol total | mg/dL |
| `hdl` | colesterol HDL | mg/dL |
| `ldl` | colesterol LDL | mg/dL |
| `triglicerideos` | triglicerídeos | mg/dL |
| `creatinina` | creatinina | mg/dL |
| `hemoglobina` | hemoglobina (no hemograma, não a glicada) | g/dL |

Se o laudo traz o exame em outra unidade (mmol/L, por exemplo), o servidor recusa: não converta. Registre o campo como não encontrado, explicando a unidade no motivo.

Busque pelo nome do exame ("glicose", "colesterol LDL", "hemograma hemoglobina"). Se o termo aparece numa observação, sem resultado, descarte a página dizendo isso.
