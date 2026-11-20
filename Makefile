# Comandos do dia a dia do projeto. Rode `make` (ou `make ajuda`) para ver a lista.
#
# Funciona no Git Bash e no PowerShell: no Windows, sem um sh no PATH, o make usa o cmd.exe, por
# isso as receitas são comandos simples, uma linha por sistema.
#
# Usuário e senha: os comandos que falam com o sistema pedem USUARIO e SENHA (a do indexador).
#   make pergunta USUARIO=avaliacao SENHA=xxx TEXTO="Qual empresa teve a maior margem EBITDA?"
#   make resultados USUARIO=avaliacao SENHA=xxx
# Também valem as variáveis de ambiente AVALIACAO_USUARIO e AVALIACAO_SENHA: definidas uma vez no
# terminal, os comandos dispensam USUARIO e SENHA.
#
# Coleção: sem COLECAO, a coleção padrão do usuário (a primeira do domínio financeiro). Para outra:
#   make resultados COLECAO=<id da coleção> SAIDA=../resultados/laudos

USUARIO ?= $(AVALIACAO_USUARIO)
SENHA ?= $(AVALIACAO_SENHA)
DOC ?=
S ?= agents
RODAR ?=
TEXTO ?=
CONVERSA ?=
COLECAO ?= $(AVALIACAO_COLECAO)
SAIDA ?= ../resultados

# Cada sistema Python tem o próprio ambiente (uv). Um VIRTUAL_ENV herdado do terminal (o do indexador,
# ativado pelo editor) faz o uv avisar a cada comando nos outros sistemas: o make não o repassa.
unexport VIRTUAL_ENV

.DEFAULT_GOAL := ajuda
.PHONY: ajuda urls bancos subir subir-dev descer-dev reconstruir descer status logs zerar zerar-base testes testes-indexer testes-mcp \
        testes-agentes testes-frontend lint comum chaves validar-agentes dominios avaliar-intencao inconsistencias resultados extracao \
        analise perguntas validacao relatorio avaliar pendencias pergunta

# Para quem pede login: para com a mensagem se faltar usuário ou senha.
precisa_login = $(if $(and $(USUARIO),$(SENHA)),,$(error Informe USUARIO=... e SENHA=..., a conta do indexador))
# Para o que apaga dados: só roda com CONFIRMA=sim.
precisa_confirmar = $(if $(filter sim,$(CONFIRMA)),,$(error Isto apaga dados. Para confirmar: make $@ CONFIRMA=sim))

ajuda:
	@echo Uso: make COMANDO [VARIAVEL=valor]
	@echo ---------------------------------------------------------------------------------------
	@echo subir ............................ liga tudo: indexador, observabilidade, MCP, agentes, frontend, sala
	@echo reconstruir ...................... o mesmo, refazendo as imagens: use depois de mudar codigo
	@echo subir-dev / descer-dev ........... metade dos conteineres: sem Kafka, um servico de especialistas, sem paineis
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
	@echo dominios ......................... valida os pacotes de dominio e mostra o que cada um define
	@echo comum ............................ copia o codigo de comum/ (telemetria) para os sistemas
	@echo chaves ........................... uma chave Ed25519 por servico, no lugar do segredo compartilhado
	@echo avaliar-intencao ................. o roteador de intencao contra 52 mensagens rotuladas, centesimos de centavo
	@echo ---------------------------------------------------------------------------------------
	@echo pergunta USUARIO SENHA TEXTO ..... uma mensagem no chat pelo terminal, com a resposta e as fontes
	@echo .                                  CONVERSA=conv-... continua uma conversa: e a menor?
	@echo inconsistencias USUARIO SENHA DOC  candidatos por regra, sem modelo, gratis; DOC e opcional
	@echo avaliar USUARIO SENHA ............ nota da base contra o gabarito, sem modelo
	@echo pendencias USUARIO SENHA DOC ..... o que falta na base, a lista do orquestrador; DOC e opcional
	@echo ---------------------------------------------------------------------------------------
	@echo Resultados em resultados/, lidos da base, gratis. Com RODAR=sim os agentes geram tudo antes, US$$ 0,15
	@echo resultados USUARIO SENHA ......... todos os itens abaixo de uma vez
	@echo extracao USUARIO SENHA ........... item 2: um JSON por documento, com pagina e trecho
	@echo analise USUARIO SENHA ............ item 3: motivos, riscos, guidance, eventos e tom
	@echo perguntas USUARIO SENHA .......... item 4: as 5 perguntas, com as fontes
	@echo validacao USUARIO SENHA .......... item 5: inconsistencias e nao encontrados
	@echo relatorio USUARIO SENHA .......... item 6: o relatorio consolidado e a nota contra o gabarito
	@echo ---------------------------------------------------------------------------------------
	@echo Exemplo: make pergunta USUARIO=avaliacao SENHA=xxx TEXTO="Qual empresa teve a maior margem EBITDA?"
	@echo Exemplo: make resultados USUARIO=avaliacao SENHA=xxx
	@echo Exemplo: make relatorio USUARIO=avaliacao SENHA=xxx RODAR=sim

urls:
	@echo Chat e envio de PDFs .... http://localhost:3000
	@echo Sala dos agentes ........ http://localhost:3300
	@echo Grafana ................. http://localhost:3001
	@echo Prometheus .............. http://localhost:9090
	@echo Kafka UI ................ http://localhost:8080
	@echo Qdrant, painel .......... http://localhost:6333/dashboard
	@echo Indexador, API .......... http://localhost:8000/docs
	@echo Servidor MCP ............ http://localhost:8100/health
	@echo Orquestrador ............ http://localhost:8200/docs
	@echo Agent Card, extrator .... http://localhost:8201/agentes/extrator/.well-known/agent-card.json
	@echo .                         os outros: 8202 validador, 8203 analista, 8204 pesquisador, 8205 redator
	@echo Postgres do indexador ... localhost:5432, banco, usuario e senha: indexer
	@echo SQLite do MCP e agentes . make bancos, para abrir no DBeaver

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

# Modo dev: metade dos contêineres. Sem Kafka (fila local dentro da API do indexador), um serviço só
# para os cinco especialistas e sem observabilidade nem sala. Os arquivos docker-compose.dev.yml de
# cada sistema dizem o que muda.
DEV = -f docker-compose.yml -f docker-compose.dev.yml

subir-dev:
	cd indexer && docker compose $(DEV) up -d --build --no-deps postgres qdrant api
	cd mcp-server && docker compose $(DEV) up -d --build
	cd agents && docker compose $(DEV) up -d --build --no-deps especialistas orquestrador
	cd frontend && docker compose up -d --build
	@echo Pronto, no modo dev. Veja os enderecos com: make urls

descer-dev:
	cd frontend && docker compose down
	cd agents && docker compose $(DEV) down
	cd mcp-server && docker compose $(DEV) down
	cd indexer && docker compose $(DEV) down

reconstruir:
	cd indexer && docker compose up -d --build
	cd observability && docker compose up -d
	cd mcp-server && docker compose up -d --build
	cd agents && docker compose up -d --build --remove-orphans
	cd frontend && docker compose up -d --build
	cd sala && docker compose up -d --build
	@echo Pronto. Veja os enderecos com: make urls

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
	cd mcp-server && uv run ruff check . ../dominios
	cd agents && uv run ruff check . ../ferramentas

comum:
	python ferramentas/comum.py

chaves:
	cd agents && uv run python -m app.ferramentas.cli gerar-chaves --pasta ../chaves

validar-agentes:
	cd agents && uv run python -m app.ferramentas.cli validar

dominios:
	cd mcp-server && uv run python -m app.ferramentas.dominios

avaliar-intencao:
	cd agents && uv run python scripts/avaliar_intencao.py

# --- usar o sistema ---------------------------------------------------------------------------

# Os SQLite ficam dentro dos volumes Docker. O script copia cada um pelo backup do próprio SQLite,
# apaga antes as cópias antigas (com os -wal e -shm do DBeaver) e mostra as tabelas com o número de linhas.
bancos:
	@python ferramentas/bancos.py

inconsistencias:
	$(precisa_login)
	@cd mcp-server && docker compose exec -T mcp-server python -m app.ferramentas.candidatos --usuario $(USUARIO) --senha $(SENHA) $(if $(DOC),--doc $(DOC),)

# Os resultados de cada item vão para resultados/, lidos da base. Com RODAR=sim, o relatório completo
# é pedido aos agentes antes (gera todos os itens de uma vez).
gerar_resultados = cd agents && uv run python scripts/gerar_entrega.py --usuario $(USUARIO) --senha $(SENHA) --saida $(SAIDA) $(if $(COLECAO),--colecao $(COLECAO),) $(if $(1),--partes $(1),) $(if $(filter sim,$(RODAR)),--rodar,)

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

pergunta:
	$(precisa_login)
	$(if $(TEXTO),,$(error Informe TEXTO="a mensagem"))
	@cd agents && uv run python scripts/perguntar.py --usuario $(USUARIO) --senha $(SENHA) --texto "$(TEXTO)" $(if $(CONVERSA),--conversa $(CONVERSA),) $(if $(COLECAO),--colecao $(COLECAO),)

pendencias:
	$(precisa_login)
	@cd agents && uv run python scripts/pendencias.py --usuario $(USUARIO) --senha $(SENHA) $(if $(DOC),--doc $(DOC),) $(if $(COLECAO),--colecao $(COLECAO),)

avaliar:
	$(precisa_login)
	@cd agents && uv run python scripts/avaliar.py --usuario $(USUARIO) --senha $(SENHA) --sem-guardar $(if $(COLECAO),--colecao $(COLECAO),)
