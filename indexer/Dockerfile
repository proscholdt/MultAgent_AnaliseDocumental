# syntax=docker/dockerfile:1
# Dois alvos:
#   light -> api e index-worker (sem Docling nem torch). Também serve ao parse-worker quando
#            o parser é a Azure (PARSER=azure): veja docker-compose.azure.yml.
#   parse -> parse-worker com o Docling (PyTorch só-CPU + modelos já baixados). Tem tudo do
#            alvo light, então roda os dois parsers.

FROM python:3.12-slim AS base
COPY --from=ghcr.io/astral-sh/uv:0.12.7 /uv /usr/local/bin/uv
# Fontes de sistema. Demonstrações financeiras costumam referenciar Arial/Times sem embutir
# a fonte; sem um substituto instalado, a página é renderizada com "caixinhas" no lugar das
# letras. Com o Docling, o modelo de layout passa a classificar páginas inteiras como figura
# (na Millet, 8 tabelas detectadas em vez de 37). Com a Azure, estraga o recorte dos gráficos,
# que é renderizado aqui. Liberation tem as mesmas métricas de Arial/Times/Courier.
RUN apt-get update \
 && apt-get install -y --no-install-recommends fontconfig fonts-liberation fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/* \
 && fc-cache -f
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    # Caches em caminho fixo: o que é baixado no build é o que a execução usa.
    HF_HOME=/opt/cache/huggingface \
    TIKTOKEN_CACHE_DIR=/opt/cache/tiktoken \
    FASTEMBED_CACHE_PATH=/opt/cache/fastembed
WORKDIR /app
COPY pyproject.toml uv.lock ./

# ---------------------------------------------------------------------------
FROM base AS light
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-default-groups
# Vocabulário do tiktoken e recursos do BM25 baixados no build, não na primeira requisição.
RUN python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')" \
 && python -c "from fastembed import SparseTextEmbedding; SparseTextEmbedding('Qdrant/bm25', language='portuguese')"
COPY app ./app
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# ---------------------------------------------------------------------------
FROM base AS parse
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-default-groups --group parse
# Modelos do Docling (layout, TableFormer e OCR) baixados no build: o worker não depende
# de rede para subir e o primeiro PDF não paga o download.
RUN python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')" \
 && python -c "from docling.datamodel.base_models import InputFormat; from docling.document_converter import DocumentConverter; DocumentConverter().initialize_pipeline(InputFormat.PDF)"
# Modelos do OCR dos recortes de gráfico (RapidOCR), também baixados no build.
RUN python -c "import numpy as np; from rapidocr import RapidOCR; RapidOCR()(np.full((64, 256, 3), 255, dtype=np.uint8))"
COPY app ./app
CMD ["python", "-m", "app.workers.parse_worker"]
