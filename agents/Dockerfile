# syntax=docker/dockerfile:1
# Uma imagem só para os dois serviços (orquestrador e especialistas): muda o comando.
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
# Os agentes vão na imagem como padrão; o compose monta a pasta por cima, para que criar ou
# editar um agente valha na hora, sem reconstruir.
COPY agentes ./agentes
EXPOSE 8200 8201
CMD ["uvicorn", "app.main:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8200"]
