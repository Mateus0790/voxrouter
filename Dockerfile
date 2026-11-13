# VoxRouter — API OpenAI-compatible para TTS/STT
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    VOXROUTER_HOST=0.0.0.0 \
    VOXROUTER_PORT=8000

COPY pyproject.toml README.md LICENSE ./
COPY voxrouter ./voxrouter

RUN pip install --upgrade pip \
 && pip install .

# config.example.yaml vira o padrão; monte config.yaml em produção
COPY config.example.yaml ./config.yaml

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')" || exit 1

CMD ["python", "-m", "voxrouter", "--config", "config.yaml", "--host", "0.0.0.0", "--port", "8000"]
