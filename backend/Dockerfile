FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    tzdata \
 && rm -rf /var/lib/apt/lists/*

# 先单独 COPY requirements.txt 利用 docker 层缓存
COPY requirements.txt .
RUN pip install -r requirements.txt

# 再 COPY 应用代码
COPY app ./app
COPY alembic.ini ./
COPY migrations ./migrations 2>/dev/null || true

# 数据目录
RUN mkdir -p /data
ENV DATABASE_URL=sqlite:////data/cloudhelper.db
ENV PYTHONPATH=/app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://127.0.0.1:8000/api/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
