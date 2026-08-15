# 安卓画面容器：Xvfb 虚拟屏 + scrcpy 投屏 + x11vnc + noVNC
# 与 redroid 容器共享同一 netns，所以直接连 127.0.0.1:5555 拿画面。
#
# 基础镜像用 ubuntu 24.04：Debian bookworm/trixie 都没有 scrcpy 包，
# ubuntu universe 里有（1.25），省掉从源码编译 SDL2/ffmpeg 的一大堆依赖。
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# scrcpy 会拖进整套 ffmpeg + SDL2（200+ 个包），走官方源很慢。
# 默认换阿里云镜像；想用官方源就 --build-arg APT_MIRROR=
ARG APT_MIRROR=mirrors.aliyun.com

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

# 缓存挂载：重试或改 Dockerfile 后不用重新下载几百个 deb
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
        xvfb x11vnc x11-utils \
        scrcpy adb \
        novnc websockify \
        procps tini \
    && ln -sf /usr/share/novnc/vnc.html /usr/share/novnc/index.html

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENV DISPLAY=":0" \
    ADB_TARGET="127.0.0.1:5555" \
    SCREEN_WIDTH="720" \
    SCREEN_HEIGHT="1280" \
    SCREEN_DEPTH="24" \
    VNC_PORT="5900" \
    NOVNC_PORT="6080" \
    VNC_VIEW_ONLY="false" \
    SCRCPY_MAX_SIZE="0" \
    SCRCPY_BITRATE="8M" \
    SCRCPY_MAX_FPS="30"

EXPOSE 5900 6080

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
