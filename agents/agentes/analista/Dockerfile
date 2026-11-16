# syntax=docker/dockerfile:1
# Analista: Faz a análise qualitativa de UM documento (motivos do resultado, riscos, guidance,
# eventos relevantes e tom da administração), cada item com a citação literal que o sustenta.
# Precisa do doc_id.
# Serviço A2A próprio, na porta 8203; só o orquestrador o chama. O contexto do build é a
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
COPY agentes/analista/analista.md ./agentes/analista/analista.md
ENV ESPECIALISTA=analista
EXPOSE 8203
CMD ["uvicorn", "app.especialistas.servico:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8203"]
