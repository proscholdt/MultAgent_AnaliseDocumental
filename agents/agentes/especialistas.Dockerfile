# syntax=docker/dockerfile:1
# Todos os especialistas num serviço só (modo dev: docker-compose.dev.yml). Sem ESPECIALISTA, o
# serviço atende todos os arquivos de agentes/; com ele, só aquele (como as imagens de cada pasta).
# O contexto do build é a pasta agents/.
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
COPY agentes ./agentes
EXPOSE 8201
CMD ["uvicorn", "app.especialistas.servico:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8201"]
