FROM python:3.11-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai

# adb: 设备控制 / 录屏拉取；ffmpeg: 录像分段合并
RUN apt-get update && apt-get install -y --no-install-recommends \
        adb ffmpeg curl ca-certificates tzdata procps \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY controller/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY controller/app /app/app

RUN mkdir -p /app/data/screenshots /app/data/recordings /app/data/dumps

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/system/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
