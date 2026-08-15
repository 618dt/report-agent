# Backend image for Report Agent (FastAPI)
FROM python:3.12-slim-bookworm

# Default: Tsinghua PyPI mirror (override at build time if needed)
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST}

WORKDIR /app

# System deps commonly needed by cryptography / pymongo wheels
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# pywin32 is Windows-only; strip it for Linux images
COPY requirements.txt /tmp/requirements.txt
RUN grep -viE '^pywin32(==|>=|~=|!=|<|>|$)' /tmp/requirements.txt > /tmp/requirements.docker.txt \
    && pip install --upgrade pip \
    && pip install -r /tmp/requirements.docker.txt \
    && rm -f /tmp/requirements.txt /tmp/requirements.docker.txt

COPY run.py ./
COPY app ./app

# Runtime configs and logs are mounted / created at start
RUN mkdir -p /app/logs /app/app/configs

EXPOSE 8989

HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=5 \
    CMD curl -fsS http://127.0.0.1:8989/health || exit 1

CMD ["python", "run.py"]
