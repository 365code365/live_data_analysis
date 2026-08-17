#!/bin/bash
# 画面链路： Xvfb → scrcpy(软件渲染) → x11vnc → noVNC
# 声音链路： scrcpy(--audio-source=output) → PulseAudio null sink → ffmpeg → HTTP mp3
# 任一环节挂掉都会自愈重启。
set -uo pipefail

log() { printf '[vnc %s] %s\n' "$(date '+%H:%M:%S')" "$*"; }

DISPLAY="${DISPLAY:-:0}"; export DISPLAY
ADB_TARGET="${ADB_TARGET:-127.0.0.1:5555}"
SCREEN_WIDTH="${SCREEN_WIDTH:-720}"
SCREEN_HEIGHT="${SCREEN_HEIGHT:-1280}"
SCREEN_DEPTH="${SCREEN_DEPTH:-24}"
VNC_PORT="${VNC_PORT:-5900}"
NOVNC_PORT="${NOVNC_PORT:-6080}"
AUDIO_PORT="${AUDIO_PORT:-6081}"
VNC_PASSWORD="${VNC_PASSWORD:-}"
VNC_VIEW_ONLY="${VNC_VIEW_ONLY:-false}"
ENABLE_AUDIO="${ENABLE_AUDIO:-true}"
AUDIO_SINK="${AUDIO_SINK:-ldm}"

# scrcpy 默认用 OpenGL 渲染，画面进的是 GLX 缓冲，x11vnc 抓不到，
# 结果就是 noVNC 里一片黑而 scrcpy 日志一切正常。必须走软件渲染。
export SDL_VIDEODRIVER="${SDL_VIDEODRIVER:-x11}"
export SDL_RENDER_DRIVER="${SDL_RENDER_DRIVER:-software}"
export LIBGL_ALWAYS_SOFTWARE=1
export SDL_AUDIODRIVER="${SDL_AUDIODRIVER:-pulseaudio}"
export PULSE_SERVER="${PULSE_SERVER:-unix:/run/pulse/native}"

# 自己编的 scrcpy 装在 /usr/local，发行版包在 /usr/share
for p in /usr/local/share/scrcpy/scrcpy-server /usr/share/scrcpy/scrcpy-server; do
  [[ -f "$p" ]] && export SCRCPY_SERVER_PATH="$p" && break
done

CHILD_PIDS=()
cleanup() {
  for p in "${CHILD_PIDS[@]:-}"; do kill "$p" 2>/dev/null || true; done
  pkill -f scrcpy 2>/dev/null || true
  pkill -f audio-stream 2>/dev/null || true
  exit 0
}
trap cleanup TERM INT

# ── Xvfb ─────────────────────────────────────────────────────────────────
# docker restart 后 /tmp 里的锁文件还在，Xvfb 会拒绝启动：
#   Fatal server error: Server is already active for display 0
# 容器里不可能有另一个 X 在跑，直接清掉残留。
DISPLAY_NUM="${DISPLAY#:}"
DISPLAY_NUM="${DISPLAY_NUM%%.*}"
if [[ -e "/tmp/.X${DISPLAY_NUM}-lock" ]]; then
  log "清理上次残留的 X 锁文件"
  rm -f "/tmp/.X${DISPLAY_NUM}-lock" "/tmp/.X11-unix/X${DISPLAY_NUM}" 2>/dev/null || true
fi
rm -f /tmp/scrcpy.log /tmp/x11vnc.log /tmp/audio.err 2>/dev/null || true

# Xvfb 的帧缓冲上限在启动时就定死了（xrandr 只能在这个上限内改），
# 所以按长边开一块正方形，之后就能自由地在竖屏 WxH 和横屏 HxW 之间切。
FB_MAX="$SCREEN_WIDTH"; [[ "$SCREEN_HEIGHT" -gt "$FB_MAX" ]] && FB_MAX="$SCREEN_HEIGHT"

Xvfb "$DISPLAY" -screen 0 "${FB_MAX}x${FB_MAX}x${SCREEN_DEPTH}" \
  -nolisten tcp -noreset +extension GLX +render +extension RANDR &
XVFB_PID=$!
CHILD_PIDS+=("$XVFB_PID")
xvfb_ready=0
for i in $(seq 1 40); do
  # X socket 存在但没人监听时 xdpyinfo 会一直阻塞，必须加超时
  if timeout 2 xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then xvfb_ready=1; break; fi
  kill -0 "$XVFB_PID" 2>/dev/null || { log "Xvfb 进程已退出"; break; }
  sleep 0.5
done
[[ "$xvfb_ready" == "1" ]] || { log "Xvfb 启动失败"; exit 1; }

# ── 帧缓冲尺寸 ───────────────────────────────────────────────────────────
fb_size() { timeout 2 xdpyinfo 2>/dev/null | awk '/^  dimensions:/{print $2; exit}'; }

set_fb() {
  local w="$1" h="$2"
  [[ "$(fb_size)" == "${w}x${h}" ]] && return 0
  # 缩小帧缓冲时 xrandr 会抱怨「output 比新尺寸大」并回一个 BadValue，
  # 但 RRSetScreenSize 已经生效了（Xvfb 的 output 是个假的，无所谓）。
  xrandr --fb "${w}x${h}" >/dev/null 2>&1
  [[ "$(fb_size)" == "${w}x${h}" ]]
}

if set_fb "$SCREEN_WIDTH" "$SCREEN_HEIGHT"; then
  log "Xvfb 就绪 ${SCREEN_WIDTH}x${SCREEN_HEIGHT}（可切横屏，上限 ${FB_MAX}x${FB_MAX}）"
else
  log "警告: 改帧缓冲失败，退回 ${FB_MAX}x${FB_MAX}（画面会有黑边，横屏也不会自动跟随）"
fi

# ── PulseAudio（给 scrcpy 一个可写的声卡）────────────────────────────────
AUDIO_OK=0
if [[ "$ENABLE_AUDIO" == "true" ]]; then
  mkdir -p /run/pulse /var/lib/pulse
  # 系统模式：容器里一切都是 root，per-user 模式 PulseAudio 会直接拒绝启动
  pulseaudio --system --disallow-exit --exit-idle-time=-1 -n \
    --load="module-native-protocol-unix auth-anonymous=1 socket=/run/pulse/native" \
    --load="module-null-sink sink_name=${AUDIO_SINK} sink_properties=device.description=${AUDIO_SINK}" \
    --log-target=file:/tmp/pulse.log >/dev/null 2>&1 &
  CHILD_PIDS+=("$!")
  for i in $(seq 1 30); do
    if pactl info >/dev/null 2>&1; then AUDIO_OK=1; break; fi
    sleep 0.5
  done
  if [[ "$AUDIO_OK" == "1" ]]; then
    pactl set-default-sink "$AUDIO_SINK" >/dev/null 2>&1 || true
    log "PulseAudio 就绪，默认输出 = ${AUDIO_SINK}（null sink）"
    /usr/local/bin/audio-stream &
    CHILD_PIDS+=("$!")
    log "音频流已启动 → http://<host>:${AUDIO_PORT}/"
  else
    log "警告: PulseAudio 起不来，本次禁用声音（详见 /tmp/pulse.log）"
    tail -5 /tmp/pulse.log 2>/dev/null | sed 's/^/[pulse] /'
  fi
else
  log "ENABLE_AUDIO=false，跳过声音链路"
fi

# ── x11vnc ───────────────────────────────────────────────────────────────
# -nowf 会让部分环境抓不到内容，这里不再使用；剪贴板要双向同步，别加 -nosel
# -xrandr resize：帧缓冲尺寸变了（手机转屏）就用 NewFBSize 通知客户端，
# 而不是让 x11vnc 自己退出。noVNC 支持 NewFBSize，画面会跟着变形状。
X11VNC_ARGS=(-display "$DISPLAY" -rfbport "$VNC_PORT" -forever -shared -noxdamage -repeat -xkb
             -xrandr resize)
if [[ -n "$VNC_PASSWORD" ]]; then
  mkdir -p /root/.vnc
  x11vnc -storepasswd "$VNC_PASSWORD" /root/.vnc/passwd >/dev/null 2>&1
  X11VNC_ARGS+=(-rfbauth /root/.vnc/passwd)
  log "VNC 已启用密码认证"
else
  X11VNC_ARGS+=(-nopw)
  log "警告: VNC 未设置密码，请勿把端口直接暴露到公网"
fi
[[ "$VNC_VIEW_ONLY" == "true" ]] && X11VNC_ARGS+=(-viewonly)

x11vnc "${X11VNC_ARGS[@]}" >/tmp/x11vnc.log 2>&1 &
CHILD_PIDS+=("$!")
log "x11vnc 监听 :$VNC_PORT"

# ── noVNC ────────────────────────────────────────────────────────────────
websockify --web=/usr/share/novnc/ "$NOVNC_PORT" "localhost:${VNC_PORT}" >/tmp/websockify.log 2>&1 &
CHILD_PIDS+=("$!")
log "noVNC 监听 :$NOVNC_PORT"

# ── 等安卓起来 ───────────────────────────────────────────────────────────
# adb 34 的 mDNS 自动发现在受限网络命名空间里会把 start-server 挂死
export ADB_MDNS=0 ADB_MDNS_AUTO_CONNECT=0 ADB_MDNS_OPENSCREEN=0

# redroid 的 adbd 监听 5555，正好落在 adb 的模拟器端口区间(5554-5585)，
# 用 127.0.0.1 连会被当成 emulator-5554 并卡在 offline。换成本容器非回环地址。
if [[ "$ADB_TARGET" == 127.0.0.1:* || "$ADB_TARGET" == localhost:* ]]; then
  self_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  if [[ -n "$self_ip" ]]; then
    ADB_TARGET="${self_ip}:${ADB_TARGET##*:}"
    log "adb 目标改用非回环地址 $ADB_TARGET"
  fi
fi

timeout 30 adb start-server >/dev/null 2>&1 || log "警告: adb start-server 超时，后续自行重试"
log "等待安卓设备 $ADB_TARGET ..."
for i in $(seq 1 180); do
  adb connect "$ADB_TARGET" >/dev/null 2>&1
  if [[ "$(timeout 10 adb -s "$ADB_TARGET" get-state 2>/dev/null || true)" == "device" ]]; then
    boot="$(timeout 10 adb -s "$ADB_TARGET" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r\n')"
    [[ "$boot" == "1" ]] && break
  fi
  sleep 2
done
log "安卓设备状态: $(timeout 10 adb -s "$ADB_TARGET" get-state 2>/dev/null || echo offline)"

# ── 画面铺满守护 ─────────────────────────────────────────────────────────
# 两个必须解决的问题：
#  1. scrcpy 自己算的初始窗口尺寸不可靠。同一个镜像、同样的 --window-width/height，
#     有时给 720x1280（对），有时给 351x624（约 48.75%），于是 VNC 里手机只占左上角
#     一小块，其余是黑的。它算窗口尺寸时会参考 X 的可用区域并做 HiDPI 换算，
#     在 Xvfb + 无窗口管理器的环境下这个推断不成立。这里不跟它较劲，直接钉死几何。
#  2. 手机转横屏时 scrcpy 会按新画面比例改窗口，但帧缓冲还是竖的，
#     画面就变成上下大黑边。检测到横比例就把帧缓冲也转过来。
screen_fit() {
  local wid geo w h tw th last_orient=""
  # 把配置的分辨率归一成「竖屏短边 x 长边」，这样不管设备本身是竖的(720x1280)
  # 还是横的(1280x720)，两个朝向的目标尺寸都算得对。
  local pw="$SCREEN_WIDTH" ph="$SCREEN_HEIGHT"
  if (( pw > ph )); then local t="$pw"; pw="$ph"; ph="$t"; fi
  while :; do
    sleep 2
    wid="$(xdotool search --class scrcpy 2>/dev/null | tail -1)"
    [[ -z "$wid" ]] && continue
    # --shell 输出形如 WINDOW=..\nX=..\nY=..\nWIDTH=..\nHEIGHT=..
    geo="$(xdotool getwindowgeometry --shell "$wid" 2>/dev/null)"
    w="$(sed -n 's/^WIDTH=//p' <<<"$geo")"
    h="$(sed -n 's/^HEIGHT=//p' <<<"$geo")"
    [[ -z "$w" || -z "$h" ]] && continue
    # 刚创建时可能是 1x1 之类的中间态，别拿它判断朝向
    (( w < 100 || h < 100 )) && continue

    if (( w > h )); then
      tw="$ph"; th="$pw"
      [[ "$last_orient" != "landscape" ]] && { log "画面为横屏，帧缓冲 ${tw}x${th}"; last_orient="landscape"; }
    else
      tw="$pw"; th="$ph"
      [[ "$last_orient" != "portrait" ]] && { log "画面为竖屏，帧缓冲 ${tw}x${th}"; last_orient="portrait"; }
    fi

    set_fb "$tw" "$th" || true
    # 窗口不等于整块帧缓冲就纠正（比例一致，所以不会拉伸失真）
    if [[ "$w" != "$tw" || "$h" != "$th" ]]; then
      xdotool windowmove "$wid" 0 0 windowsize "$wid" "$tw" "$th" >/dev/null 2>&1
    fi
  done
}

screen_fit &
CHILD_PIDS+=("$!")
log "画面铺满守护已启动"

SCRCPY_VER="$(scrcpy --version 2>/dev/null | head -1 | grep -oE '[0-9]+\.[0-9]+' | head -1)"
SCRCPY_MAJOR="${SCRCPY_VER%%.*}"
log "scrcpy 版本: ${SCRCPY_VER:-unknown}"

# ── 投屏守护 ─────────────────────────────────────────────────────────────
run_scrcpy() {
  local with_audio="$1"
  local args=(-s "$ADB_TARGET"
              --window-borderless --window-x 0 --window-y 0
              --window-width "$SCREEN_WIDTH" --window-height "$SCREEN_HEIGHT"
              --stay-awake --max-fps "${SCRCPY_MAX_FPS:-30}")

  # 3.x 会对 --render-driver 报 "Could not set render driver"，
  # 但 SDL_RENDER_DRIVER 环境变量本身就生效（实测能被 x11vnc 抓到画面），
  # 所以只对老版本传这个参数，避免日志里出现误导人的告警。
  [[ "${SCRCPY_MAJOR:-1}" -lt 3 ]] && args+=(--render-driver=software)

  if [[ "${SCRCPY_MAJOR:-1}" -ge 2 ]]; then
    args+=(--video-bit-rate "${SCRCPY_BITRATE:-8M}")
    if [[ "$with_audio" == "yes" ]]; then
      # output = 转发系统混音（安卓 11+）。失败时 scrcpy 只警告不退出。
      args+=(--audio-source=output --audio-codec=opus --audio-buffer=120)
    else
      args+=(--no-audio)
    fi
  else
    args+=(--bit-rate "${SCRCPY_BITRATE:-8M}")
  fi
  [[ "${SCRCPY_MAX_SIZE:-0}" != "0" ]] && args+=(--max-size "$SCRCPY_MAX_SIZE")
  # 屏幕灭着的时候投出来是全黑，顺手点亮（控制器也会周期性保证常亮）
  adb -s "$ADB_TARGET" shell "input keyevent KEYCODE_WAKEUP; svc power stayon true" >/dev/null 2>&1 || true

  log "启动 scrcpy（音频=${with_audio}）"
  scrcpy "${args[@]}"
}

want_audio="no"
[[ "$AUDIO_OK" == "1" && "${SCRCPY_MAJOR:-1}" -ge 2 ]] && want_audio="yes"
short_fail=0

while :; do
  adb connect "$ADB_TARGET" >/dev/null 2>&1
  start_ts=$(date +%s)
  run_scrcpy "$want_audio" >>/tmp/scrcpy.log 2>&1
  code=$?
  elapsed=$(( $(date +%s) - start_ts ))
  tail -5 /tmp/scrcpy.log 2>/dev/null | sed 's/^/[scrcpy] /'

  # 连续秒退两次就认为是音频参数的问题，降级成无声继续投屏
  if [[ "$elapsed" -lt 5 && "$want_audio" == "yes" ]]; then
    short_fail=$((short_fail + 1))
    if [[ "$short_fail" -ge 2 ]]; then
      log "scrcpy 连续秒退，降级为无声投屏"
      want_audio="no"
      short_fail=0
    fi
  else
    short_fail=0
  fi

  log "scrcpy 退出 (code=$code, 存活 ${elapsed}s)，3 秒后重连"
  adb disconnect "$ADB_TARGET" >/dev/null 2>&1 || true
  sleep 3
done
