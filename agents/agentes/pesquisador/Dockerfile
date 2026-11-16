# syntax=docker/dockerfile:1
# Pesquisador: Responde a UMA pergunta sobre os documentos, comparando empresas quando preciso,
# e grava a resposta com as fontes. Use para as cinco perguntas do relatório (informe o
# pergunta_id) e para perguntas livres do usuário.
# Serviço A2A próprio, na porta 8204; só o orquestrador o chama. O contexto do build é a
# pasta agents/ (ver docker-compose.yml).
FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.12.7 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-default-groups
COPY app ./app
COPY config ./config
# Só o arquivo deste agente; o compose monta a pasta por cima, para que editar o prompt valha
# na hora, sem reconstruir.
COPY agentes/pesquisador/pesquisador.md ./agentes/pesquisador/pesquisador.md
ENV ESPECIALISTA=pesquisador
EXPOSE 8204
CMD ["uvicorn", "app.especialistas.servico:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8204"]
