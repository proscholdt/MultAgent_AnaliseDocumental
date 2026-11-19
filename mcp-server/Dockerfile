# syntax=docker/dockerfile:1
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
# Migrações do Alembic: aplicadas na subida quando a base é Postgres.
COPY alembic.ini ./
COPY migracoes ./migracoes
# A política e os ajustes vão na imagem como padrão; o compose monta a pasta config por cima,
# para que editar valha na hora, sem reconstruir.
COPY config ./config
EXPOSE 8100
CMD ["uvicorn", "app.main:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8100"]
