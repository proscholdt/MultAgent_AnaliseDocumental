# syntax=docker/dockerfile:1
# Orquestrador: a API do chat e o grafo (LangGraph) que decide, delega por A2A e confere.
# Porta 8200. O contexto do build é a pasta agents/ (ver docker-compose.yml).
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
# Os agentes vão na imagem como padrão; o compose monta a pasta por cima, para que editar um
# agente valha na hora, sem reconstruir.
COPY agentes ./agentes
COPY avaliacao/gabarito.json ./avaliacao/gabarito.json
EXPOSE 8200
CMD ["uvicorn", "app.orquestrador.api:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8200"]
