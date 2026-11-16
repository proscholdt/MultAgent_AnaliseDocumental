# syntax=docker/dockerfile:1
# Redator: Escreve o resumo executivo de uma empresa (até 5 linhas, precisa do doc_id), monta o
# relatório de uma empresa (com o doc_id) e, quando tudo o mais estiver pronto, monta o
# relatório consolidado. Não lê os documentos, só a base.
# Serviço A2A próprio, na porta 8205; só o orquestrador o chama. O contexto do build é a
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
COPY agentes/redator/redator.md ./agentes/redator/redator.md
ENV ESPECIALISTA=redator
EXPOSE 8205
CMD ["uvicorn", "app.especialistas.servico:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8205"]
