# Comandos do dia a dia do projeto. Rode `make` (ou `make ajuda`) para ver a lista.
#
# Funciona no Git Bash e no PowerShell: no Windows, sem um sh no PATH, o make usa o cmd.exe, por
# isso as receitas são comandos simples, uma linha por sistema.
#
# Usuário e senha: os comandos que falam com o sistema pedem USUARIO e SENHA (a do indexador).
#   make inconsistencias USUARIO=avaliacao SENHA=xxx DOC=Millet
# Também valem as variáveis de ambiente AVALIACAO_USUARIO e AVALIACAO_SENHA.

USUARIO ?= $(AVALIACAO_USUARIO)
SENHA ?= $(AVALIACAO_SENHA)
DOC ?=
S ?= agents
RODAR ?=

.DEFAULT_GOAL := ajuda
.PHONY: ajuda urls bancos subir reconstruir descer status logs zerar zerar-base testes testes-indexer testes-mcp \
        testes-agentes testes-frontend lint validar-agentes inconsistencias resultados extracao analise perguntas \
        validacao relatorio avaliar

# Para quem pede login: para com a mensagem se faltar usuário ou senha.
precisa_login = $(if $(and $(USUARIO),$(SENHA)),,$(error Informe USUARIO=... e SENHA=..., a conta do indexador))
# Para o que apaga dados: só roda com CONFIRMA=sim.
precisa_confirmar = $(if $(filter sim,$(CONFIRMA)),,$(error Isto apaga dados. Para confirmar: make $@ CONFIRMA=sim))

ajuda:
	@echo Uso: make COMANDO [VARIAVEL=valor]
	@echo ---------------------------------------------------------------------------------------
	@echo subir ............................ liga tudo: indexador, observabilidade, MCP, agentes, frontend, sala
	@echo reconstruir ...................... o mesmo, refazendo as imagens: use depois de mudar codigo
	@echo descer ........................... desliga tudo, sem apagar nada
	@echo status ........................... o que esta rodando
	@echo logs S=agents .................... logs de um sistema: indexer, mcp-server, agents, frontend, sala
	@echo urls ............................. os enderecos de cada tela
	@echo bancos ........................... copia os SQLite para bancos/, para abrir no DBeaver
	@echo ---------------------------------------------------------------------------------------
	@echo zerar CONFIRMA=sim ............... apaga TUDO: documentos, usuarios, base, memoria, traces
	@echo zerar-base CONFIRMA=sim .......... apaga so a base do MCP e a memoria dos agentes, mantem os PDFs
	@echo ---------------------------------------------------------------------------------------
	@echo testes ........................... testes de todos os sistemas, sem rede e sem custo
	@echo testes-indexer, testes-mcp ....... um sistema so; tambem testes-agentes e testes-frontend
	@echo lint ............................. ruff nos tres sistemas Python
	@echo validar-agentes .................. confere os arquivos dos agentes
	@echo ---------------------------------------------------------------------------------------
	@echo inconsistencias USUARIO SENHA DOC  candidatos por regra, sem modelo, gratis; DOC e opcional
	@echo avaliar USUARIO SENHA ............ nota da base contra o gabarito, sem modelo
	@echo ---------------------------------------------------------------------------------------
	@echo Resultados em resultados/, lidos da base, gratis. Com RODAR=sim os agentes geram tudo antes, US$$ 0,15
	@echo resultados USUARIO SENHA ......... todos os itens abaixo de uma vez
	@echo extracao USUARIO SENHA ........... item 2: um JSON por documento, com pagina e trecho
	@echo analise USUARIO SENHA ............ item 3: motivos, riscos, guidance, eventos e tom
	@echo perguntas USUARIO SENHA .......... item 4: as 5 perguntas, com as fontes
	@echo validacao USUARIO SENHA .......... item 5: inconsistencias e nao encontrados
	@echo relatorio USUARIO SENHA .......... item 6: o relatorio consolidado e a nota contra o gabarito
	@echo ---------------------------------------------------------------------------------------
	@echo Exemplo: make resultados USUARIO=avaliacao SENHA=xxx
	@echo Exemplo: make relatorio USUARIO=avaliacao SENHA=xxx RODAR=sim

urls:
	@echo Chat e envio de PDFs .... http://localhost:3000
	@echo Sala dos agentes ........ http://localhost:3300
	@echo Grafana ................. http://localhost:3001
	@echo Kafka UI ................ http://localhost:8080
	@echo Qdrant, painel .......... http://localhost:6333/dashboard
	@echo Indexador, API .......... http://localhost:8000/docs
	@echo Servidor MCP ............ http://localhost:8100/health
	@echo Orquestrador ............ http://localhost:8200/docs

# --- ligar e desligar -------------------------------------------------------------------------
# A ordem importa: o indexador cria a rede que os outros usam, e a sala usa a da observabilidade.

subir:
	cd indexer && docker compose up -d
	cd observability && docker compose up -d
	cd mcp-server && docker compose up -d
	cd agents && docker compose up -d --remove-orphans
	cd frontend && docker compose up -d
	cd sala && docker compose up -d
	@echo Pronto. Veja os enderecos com: make urls

reconstruir:
	cd indexer && docker compose up -d --build
	cd observability && docker compose up -d
	cd mcp-server && docker compose up -d --build
	cd agents && docker compose up -d --build --remove-orphans
	cd frontend && docker compose up -d --build
	cd sala && docker compose up -d --build

descer:
	cd sala && docker compose down
	cd frontend && docker compose down
	cd agents && docker compose down
	cd mcp-server && docker compose down
	cd observability && docker compose down
	cd indexer && docker compose down

status:
	docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

logs:
	cd $(S) && docker compose logs -f --tail 100

# --- zerar ------------------------------------------------------------------------------------

zerar:
	$(precisa_confirmar)
	cd sala && docker compose down -v
	cd frontend && docker compose down -v
	cd agents && docker compose down -v
	cd mcp-server && docker compose down -v
	cd observability && docker compose down -v
	cd indexer && docker compose down -v
	@echo Tudo apagado. Para recomecar: make subir, crie a conta em http://localhost:3000 e envie os PDFs.

zerar-base:
	$(precisa_confirmar)
	cd agents && docker compose down -v
	cd mcp-server && docker compose down -v
	cd mcp-server && docker compose up -d
	cd agents && docker compose up -d --remove-orphans
	@echo Base do MCP e memoria dos agentes zeradas. Documentos e usuarios continuam no indexador.

# --- testes -----------------------------------------------------------------------------------

testes: testes-indexer testes-mcp testes-agentes testes-frontend

testes-indexer:
	cd indexer && uv run pytest -q

testes-mcp:
	cd mcp-server && uv run pytest -q

testes-agentes:
	cd agents && uv run pytest -q

testes-frontend:
	cd frontend && npm test

lint:
	cd indexer && uv run ruff check .
	cd mcp-server && uv run ruff check .
	cd agents && uv run ruff check .

validar-agentes:
	cd agents && uv run python -m app.ferramentas.cli validar

# --- usar o sistema ---------------------------------------------------------------------------

# Os SQLite ficam dentro dos volumes Docker. A cópia usa o backup do próprio SQLite, que leva junto
# o que ainda está no arquivo -wal; copiar o .db com o serviço gravando pode dar um arquivo pela metade.
copiar_sqlite = docker exec $(1) python -c "import sqlite3; sqlite3.connect('$(2)').backup(sqlite3.connect('/tmp/copia.db'))" && docker cp $(1):/tmp/copia.db bancos/$(3) && docker exec $(1) rm -f /tmp/copia.db

bancos:
	@python -c "import os; os.makedirs('bancos', exist_ok=True)"
	@$(call copiar_sqlite,mcp-server-mcp-server-1,/data/mcp.db,mcp.db)
	@$(call copiar_sqlite,agents-orquestrador-1,/data/conversas.db,agentes-conversas.db)
	@$(call copiar_sqlite,agents-extrator-1,/data/memoria.db,memoria-extrator.db)
	@$(call copiar_sqlite,agents-validador-1,/data/memoria.db,memoria-validador.db)
	@$(call copiar_sqlite,agents-analista-1,/data/memoria.db,memoria-analista.db)
	@$(call copiar_sqlite,agents-pesquisador-1,/data/memoria.db,memoria-pesquisador.db)
	@$(call copiar_sqlite,agents-redator-1,/data/memoria.db,memoria-redator.db)
	@echo Copias em bancos/ - abra no DBeaver como SQLite. Rode de novo para atualizar.
	@echo Postgres do indexador, direto: localhost:5432, banco indexer, usuario indexer, senha indexer

inconsistencias:
	$(precisa_login)
	@cd mcp-server && docker compose exec -T mcp-server python -m app.ferramentas.candidatos --usuario $(USUARIO) --senha $(SENHA) $(if $(DOC),--doc $(DOC),)

# Os resultados de cada item vão para resultados/, lidos da base. Com RODAR=sim, o relatório completo
# é pedido aos agentes antes (gera todos os itens de uma vez).
gerar_resultados = cd agents && uv run python scripts/gerar_entrega.py --usuario $(USUARIO) --senha $(SENHA) --saida ../resultados $(if $(1),--partes $(1),) $(if $(filter sim,$(RODAR)),--rodar,)

resultados:
	$(precisa_login)
	@$(call gerar_resultados,)

extracao:
	$(precisa_login)
	@$(call gerar_resultados,extracao)

analise:
	$(precisa_login)
	@$(call gerar_resultados,analise)

perguntas:
	$(precisa_login)
	@$(call gerar_resultados,perguntas)

validacao:
	$(precisa_login)
	@$(call gerar_resultados,validacao)

relatorio:
	$(precisa_login)
	@$(call gerar_resultados,relatorio)

avaliar:
	$(precisa_login)
	@cd agents && uv run python scripts/avaliar.py --usuario $(USUARIO) --senha $(SENHA) --sem-guardar
