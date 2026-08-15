# 安卓画面容器：Xvfb 虚拟屏 + scrcpy 投屏 + x11vnc + noVNC
# 与 redroid 容器共享同一 netns，所以直接连 127.0.0.1:5555 拿画面。
#
# 基础镜像用 ubuntu 24.04：Debian bookworm/trixie 都没有 scrcpy 包，
# ubuntu universe 里有（1.25），省掉从源码编译的一大堆依赖。
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        xvfb x11vnc x11-utils xdotool \
        scrcpy adb \
        novnc websockify \
        fonts-noto-cjk \
        procps iproute2 curl ca-certificates tini \
    && ln -sf /usr/share/novnc/vnc.html /usr/share/novnc/index.html \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENV DISPLAY=":0" \
    ADB_TARGET="127.0.0.1:5555" \
    SCREEN_WIDTH="720" \
    SCREEN_HEIGHT="1280" \
    SCREEN_DEPTH="24" \
    VNC_PORT="5900" \
    NOVNC_PORT="6080" \
    VNC_PASSWORD="" \
    VNC_VIEW_ONLY="false" \
    SCRCPY_MAX_SIZE="0" \
    SCRCPY_BITRATE="8M" \
    SCRCPY_MAX_FPS="30"

EXPOSE 5900 6080

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
