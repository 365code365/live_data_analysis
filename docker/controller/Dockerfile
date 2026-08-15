FROM python:3.11-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai

# 国内构建默认换源；想用官方源就 --build-arg APT_MIRROR= --build-arg PIP_INDEX_URL=
ARG APT_MIRROR=mirrors.aliyun.com
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/

RUN set -eux; \
    if [ -n "${APT_MIRROR}" ]; then \
      for f in /etc/apt/sources.list.d/debian.sources /etc/apt/sources.list; do \
        [ -f "$f" ] || continue; \
        sed -i \
          -e "s|http://deb.debian.org/debian|http://${APT_MIRROR}/debian|g" \
          -e "s|http://security.debian.org/debian-security|http://${APT_MIRROR}/debian-security|g" \
          "$f"; \
      done; \
    fi; \
    rm -f /etc/apt/apt.conf.d/docker-clean; \
    echo 'Binary::apt::APT::Keep-Downloaded-Packages "true";' > /etc/apt/apt.conf.d/keep-cache

# adb: 设备控制 / 录屏拉取；ffmpeg: 录像分段合并
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
        adb ffmpeg curl ca-certificates tzdata procps

WORKDIR /app

COPY controller/requirements.txt /app/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install ${PIP_INDEX_URL:+--index-url ${PIP_INDEX_URL}} \
        ${PIP_INDEX_URL:+--trusted-host $(echo "${PIP_INDEX_URL}" | sed -E 's#^https?://([^/]+).*#\1#')} \
        -r /app/requirements.txt

COPY controller/app /app/app

RUN mkdir -p /app/data/screenshots /app/data/recordings /app/data/dumps

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/system/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
