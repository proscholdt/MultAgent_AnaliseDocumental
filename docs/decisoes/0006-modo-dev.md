# 0006 — Modo dev com fila local e um serviço de especialistas

**Contexto.** Subir tudo exigia ~22 contêineres (Kafka, workers, 6 serviços de agentes,
observabilidade e sala) para trabalhar com três PDFs.

**Decisão.** `make subir-dev` usa os `docker-compose.dev.yml`: o indexador com `fila: local` (uma
fila em processo com o mesmo contrato do Kafka; a API roda o parse e a indexação), os cinco
especialistas num serviço só e nada de observabilidade. O modo completo continua o padrão.

**Consequência.** Dois modos para manter. No dev, o que está na fila se perde se a API cair, e um
especialista que trava atrapalha os outros; o isolamento de permissões continua no MCP.
