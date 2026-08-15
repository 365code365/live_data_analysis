#!/bin/bash
# Xvfb → scrcpy → x11vnc → noVNC 链路。任一环节挂掉会自愈重启。
set -uo pipefail

log() { printf '[vnc %s] %s\n' "$(date '+%H:%M:%S')" "$*"; }

DISPLAY="${DISPLAY:-:0}"; export DISPLAY

# 关键：强制 SDL 走软件渲染。
# scrcpy 默认用 OpenGL 渲染，在 Xvfb 上画面进的是 GLX 缓冲，x11vnc 抓不到，
# 结果就是 noVNC 里一片黑（scrcpy 日志却显示一切正常）。
# 软件渲染会把帧真正画进 X 的 framebuffer，x11vnc 才抓得到。
export SDL_VIDEODRIVER="${SDL_VIDEODRIVER:-x11}"
export SDL_RENDER_DRIVER="${SDL_RENDER_DRIVER:-software}"
export LIBGL_ALWAYS_SOFTWARE=1
ADB_TARGET="${ADB_TARGET:-127.0.0.1:5555}"
SCREEN_WIDTH="${SCREEN_WIDTH:-720}"
SCREEN_HEIGHT="${SCREEN_HEIGHT:-1280}"
SCREEN_DEPTH="${SCREEN_DEPTH:-24}"
VNC_PORT="${VNC_PORT:-5900}"
NOVNC_PORT="${NOVNC_PORT:-6080}"
VNC_PASSWORD="${VNC_PASSWORD:-}"
VNC_VIEW_ONLY="${VNC_VIEW_ONLY:-false}"

# scrcpy 窗口要贴合虚拟屏，留一点余量给标题栏（无边框模式下不需要）
export SCRCPY_SERVER_PATH="${SCRCPY_SERVER_PATH:-/usr/share/scrcpy/scrcpy-server}"

CHILD_PIDS=()
cleanup() {
  for p in "${CHILD_PIDS[@]:-}"; do kill "$p" 2>/dev/null || true; done
  pkill -f scrcpy 2>/dev/null || true
  exit 0
}
trap cleanup TERM INT

# ── Xvfb ─────────────────────────────────────────────────────────────────
Xvfb "$DISPLAY" -screen 0 "${SCREEN_WIDTH}x${SCREEN_HEIGHT}x${SCREEN_DEPTH}" -nolisten tcp -noreset +extension GLX +render &
CHILD_PIDS+=("$!")
for i in $(seq 1 40); do
  xdpyinfo -display "$DISPLAY" >/dev/null 2>&1 && break
  sleep 0.25
done
xdpyinfo -display "$DISPLAY" >/dev/null 2>&1 || { log "Xvfb 启动失败"; exit 1; }
log "Xvfb 就绪 ${SCREEN_WIDTH}x${SCREEN_HEIGHT}"

# ── x11vnc ───────────────────────────────────────────────────────────────
X11VNC_ARGS=(-display "$DISPLAY" -rfbport "$VNC_PORT" -forever -shared -noxdamage -repeat -nowf -xkb)
if [[ -n "$VNC_PASSWORD" ]]; then
  mkdir -p /root/.vnc
  x11vnc -storepasswd "$VNC_PASSWORD" /root/.vnc/passwd >/dev/null 2>&1
  X11VNC_ARGS+=(-rfbauth /root/.vnc/passwd)
  log "VNC 已启用密码认证"
else
  X11VNC_ARGS+=(-nopw)
  log "警告: VNC 未设置密码（VNC_PASSWORD 为空）。端口仅在容器网络内暴露，"
  log "      如果把 noVNC 端口暴露到公网，请务必设置 VNC_PASSWORD 或加一层反代鉴权。"
fi
[[ "$VNC_VIEW_ONLY" == "true" ]] && X11VNC_ARGS+=(-viewonly)

x11vnc "${X11VNC_ARGS[@]}" >/tmp/x11vnc.log 2>&1 &
CHILD_PIDS+=("$!")
log "x11vnc 监听 :$VNC_PORT"

# ── noVNC ────────────────────────────────────────────────────────────────
websockify --web=/usr/share/novnc/ "$NOVNC_PORT" "localhost:${VNC_PORT}" >/tmp/websockify.log 2>&1 &
CHILD_PIDS+=("$!")
log "noVNC 监听 :$NOVNC_PORT  →  http://<host>:<mapped>/vnc.html?autoconnect=1&resize=scale"

# ── 等安卓起来 ───────────────────────────────────────────────────────────
adb start-server >/dev/null 2>&1
log "等待安卓设备 $ADB_TARGET ..."
for i in $(seq 1 180); do
  adb connect "$ADB_TARGET" >/dev/null 2>&1
  state="$(adb -s "$ADB_TARGET" get-state 2>/dev/null || true)"
  if [[ "$state" == "device" ]]; then
    boot="$(adb -s "$ADB_TARGET" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r\n')"
    [[ "$boot" == "1" ]] && break
  fi
  sleep 2
done
if [[ "$(adb -s "$ADB_TARGET" get-state 2>/dev/null || true)" != "device" ]]; then
  log "警告: 6 分钟内没等到安卓设备，仍会持续重试投屏"
fi
log "安卓设备状态: $(adb -s "$ADB_TARGET" get-state 2>/dev/null || echo offline)"

# ── scrcpy 版本适配（Debian bookworm 是 1.25，新版是 2.x/3.x）────────────
SCRCPY_VER="$(scrcpy --version 2>/dev/null | head -1 | grep -oE '[0-9]+\.[0-9]+' | head -1)"
SCRCPY_MAJOR="${SCRCPY_VER%%.*}"
log "scrcpy 版本: ${SCRCPY_VER:-unknown}"

# scrcpy 2.x 与 1.x 的码率参数名不同，按主版本号分支
run_scrcpy() {
  local args=()
  if [[ "${SCRCPY_MAJOR:-1}" -ge 2 ]]; then
    args=(-s "$ADB_TARGET" --window-borderless --window-x 0 --window-y 0
          --window-width "$SCREEN_WIDTH" --window-height "$SCREEN_HEIGHT"
          --stay-awake --max-fps "${SCRCPY_MAX_FPS:-30}"
          --video-bit-rate "${SCRCPY_BITRATE:-8M}" --no-audio
          --render-driver=software)
  else
    args=(-s "$ADB_TARGET" --window-borderless --window-x 0 --window-y 0
          --window-width "$SCREEN_WIDTH" --window-height "$SCREEN_HEIGHT"
          --stay-awake --max-fps "${SCRCPY_MAX_FPS:-30}"
          --bit-rate "${SCRCPY_BITRATE:-8M}"
          --render-driver=software)
  fi
  [[ "${SCRCPY_MAX_SIZE:-0}" != "0" ]] && args+=(--max-size "$SCRCPY_MAX_SIZE")
  log "启动 scrcpy: ${args[*]}"
  scrcpy "${args[@]}"
}

# ── 投屏守护：断线自动重连 ───────────────────────────────────────────────
while :; do
  adb connect "$ADB_TARGET" >/dev/null 2>&1
  run_scrcpy >>/tmp/scrcpy.log 2>&1
  code=$?
  log "scrcpy 退出 (code=$code)，3 秒后重连 …"
  tail -5 /tmp/scrcpy.log 2>/dev/null | sed 's/^/[scrcpy] /'
  adb disconnect "$ADB_TARGET" >/dev/null 2>&1 || true
  sleep 3
done
