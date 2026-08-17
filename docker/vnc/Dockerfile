# 安卓画面 + 声音容器
#   Xvfb 虚拟屏 → scrcpy 投屏(软件渲染) → x11vnc → noVNC   画面
#   scrcpy 音频 → PulseAudio null sink → ffmpeg → HTTP mp3   声音
#
# 为什么要自己编 scrcpy：发行版仓库里的 scrcpy 是 1.25，不支持音频转发。
# 音频从 scrcpy 2.0 才有。这里编 3.3.3 —— 它是最后一个用 SDL2 的大版本，
# 4.x 起要 SDL3，而 Ubuntu 24.04 只有 SDL2，再编一份 SDL3 不值得。
# 服务端 jar 用官方预编译产物，省掉在镜像里装 Android SDK。

ARG APT_MIRROR=mirrors.aliyun.com
ARG SCRCPY_VERSION=3.3.3

# ── 构建阶段 ──────────────────────────────────────────────────────────────
FROM ubuntu:24.04 AS builder

ARG APT_MIRROR
ARG SCRCPY_VERSION
ENV DEBIAN_FRONTEND=noninteractive

RUN set -eux; \
    if [ -n "${APT_MIRROR}" ]; then \
      for f in /etc/apt/sources.list.d/ubuntu.sources /etc/apt/sources.list; do \
        [ -f "$f" ] || continue; \
        sed -i \
          -e "s|http://ports.ubuntu.com/ubuntu-ports|http://${APT_MIRROR}/ubuntu-ports|g" \
          -e "s|http://archive.ubuntu.com/ubuntu|http://${APT_MIRROR}/ubuntu|g" \
          -e "s|http://security.ubuntu.com/ubuntu|http://${APT_MIRROR}/ubuntu|g" \
          "$f"; \
      done; \
    fi; \
    rm -f /etc/apt/apt.conf.d/docker-clean; \
    echo 'Binary::apt::APT::Keep-Downloaded-Packages "true";' > /etc/apt/apt.conf.d/keep-cache

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl \
        gcc pkg-config meson ninja-build \
        libsdl2-dev libavcodec-dev libavdevice-dev libavformat-dev \
        libavutil-dev libswresample-dev libusb-1.0-0-dev

WORKDIR /build
RUN set -eux; \
    curl -fsSL -o src.tar.gz \
      "https://github.com/Genymobile/scrcpy/archive/refs/tags/v${SCRCPY_VERSION}.tar.gz"; \
    mkdir -p src && tar xzf src.tar.gz -C src --strip-components=1; \
    curl -fsSL -o /build/scrcpy-server \
      "https://github.com/Genymobile/scrcpy/releases/download/v${SCRCPY_VERSION}/scrcpy-server-v${SCRCPY_VERSION}"; \
    cd src; \
    meson setup out --buildtype=release --strip -Db_lto=true \
        -Dcompile_server=false -Dprebuilt_server=/build/scrcpy-server; \
    ninja -C out; \
    DESTDIR=/out ninja -C out install; \
    # compile_server=false 时 meson 会跳过 server 子目录，连带跳过预编译 jar 的
    # 安装步骤，客户端启动时会报 "scrcpy-server does not exist"。这里手动装。
    install -Dm644 /build/scrcpy-server /out/usr/local/share/scrcpy/scrcpy-server; \
    test -f /out/usr/local/share/scrcpy/scrcpy-server; \
    find /out -type f | sed 's|^/out||' | sort

# ── 运行阶段 ──────────────────────────────────────────────────────────────
FROM ubuntu:24.04

ARG APT_MIRROR
ENV DEBIAN_FRONTEND=noninteractive

RUN set -eux; \
    if [ -n "${APT_MIRROR}" ]; then \
      for f in /etc/apt/sources.list.d/ubuntu.sources /etc/apt/sources.list; do \
        [ -f "$f" ] || continue; \
        sed -i \
          -e "s|http://ports.ubuntu.com/ubuntu-ports|http://${APT_MIRROR}/ubuntu-ports|g" \
          -e "s|http://archive.ubuntu.com/ubuntu|http://${APT_MIRROR}/ubuntu|g" \
          -e "s|http://security.ubuntu.com/ubuntu|http://${APT_MIRROR}/ubuntu|g" \
          "$f"; \
      done; \
    fi; \
    rm -f /etc/apt/apt.conf.d/docker-clean; \
    echo 'Binary::apt::APT::Keep-Downloaded-Packages "true";' > /etc/apt/apt.conf.d/keep-cache

# ffmpeg 顺带把 libav* 运行库都带上了，scrcpy 直接可用
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
        xvfb x11vnc x11-utils \
        adb \
        novnc websockify \
        ffmpeg libsdl2-2.0-0 libusb-1.0-0 \
        pulseaudio pulseaudio-utils \
        procps tini ca-certificates \
    && ln -sf /usr/share/novnc/vnc.html /usr/share/novnc/index.html \
    && usermod -aG pulse-access root

COPY --from=builder /out/usr/local/ /usr/local/
RUN ldconfig \
    && scrcpy --version | head -2 \
    && test -f /usr/local/share/scrcpy/scrcpy-server \
    && echo "scrcpy-server 已就位"

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
COPY audio-stream.sh /usr/local/bin/audio-stream
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/audio-stream

ENV DISPLAY=":0" \
    ADB_TARGET="127.0.0.1:5555" \
    SCREEN_WIDTH="720" \
    SCREEN_HEIGHT="1280" \
    SCREEN_DEPTH="24" \
    VNC_PORT="5900" \
    NOVNC_PORT="6080" \
    AUDIO_PORT="6081" \
    VNC_VIEW_ONLY="false" \
    ENABLE_AUDIO="true" \
    AUDIO_BITRATE="96k" \
    AUDIO_SINK="ldm" \
    SCRCPY_MAX_SIZE="0" \
    SCRCPY_BITRATE="8M" \
    SCRCPY_MAX_FPS="30" \
    PULSE_SERVER="unix:/run/pulse/native" \
    PULSE_RUNTIME_PATH="/run/pulse"

EXPOSE 5900 6080 6081

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
