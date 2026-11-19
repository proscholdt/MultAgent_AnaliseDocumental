# Código comum

Arquivos usados igual por mais de um sistema. A fonte é esta pasta; cada sistema tem uma cópia
(o build Docker de cada um só enxerga a própria pasta, por isso não é um pacote instalado).

| Arquivo | Cópias |
|---|---|
| `telemetria.py` | `indexer/app/`, `mcp-server/app/`, `agents/app/` |

Mudou um arquivo daqui: `make comum` copia para os sistemas. A CI roda
`python ferramentas/comum.py --conferir` e falha se alguma cópia divergir.
