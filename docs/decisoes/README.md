# Registros de decisão

Uma decisão de arquitetura por arquivo: o contexto, o que foi decidido e o que custa. O histórico
de cada regra (a execução real que a motivou) fica aqui e nos testes, não em comentários longos no
código.

| # | Decisão | Estado |
|---|---|---|
| [0001](0001-portoes-no-servidor-mcp.md) | O modelo aponta, o servidor MCP lê e confere | aceita |
| [0002](0002-nucleo-sem-infraestrutura.md) | Regras puras, portas e casos de uso | aceita |
| [0003](0003-dominio-em-yaml.md) | Indicadores, categorias e perguntas em YAML; fórmulas em Python | aceita |
| [0004](0004-chave-propria-por-servico.md) | Chave Ed25519 por serviço no lugar do segredo compartilhado | aceita |
| [0005](0005-postgres-para-replicas.md) | Postgres opcional para várias réplicas | aceita |
| [0006](0006-modo-dev.md) | Modo dev com fila local e um serviço de especialistas | aceita |
| [0007](0007-dominios-plugaveis.md) | Domínios plugáveis: o que muda de um tipo de documento para outro fica num pacote | em andamento |
