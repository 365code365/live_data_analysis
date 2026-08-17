#!/bin/bash
# 把安卓的声音以 HTTP mp3 流的形式吐出来，浏览器 <audio> 直接就能播。
#
# 链路： scrcpy(--audio-source=output) → PulseAudio null sink → 本脚本 ffmpeg → HTTP
#
# ffmpeg 的 http 输出加 -listen 1 只接一个客户端，客户端断开后进程退出，
# 所以外面套一层循环，来一个连接服务一个。控制台场景够用；
# 需要多人同时听就在前面挂一层转发（如 nginx 或 icecast）。
set -uo pipefail

log() { printf '[audio %s] %s\n' "$(date '+%H:%M:%S')" "$*"; }

AUDIO_PORT="${AUDIO_PORT:-6081}"
AUDIO_BITRATE="${AUDIO_BITRATE:-96k}"
AUDIO_SINK="${AUDIO_SINK:-ldm}"
export PULSE_SERVER="${PULSE_SERVER:-unix:/run/pulse/native}"

log "音频流端口 :$AUDIO_PORT  源=${AUDIO_SINK}.monitor  码率=$AUDIO_BITRATE"

while :; do
  # -re 不要加：这是实时源，加了反而会漂
  ffmpeg -hide_banner -loglevel error \
    -f pulse -i "${AUDIO_SINK}.monitor" \
    -ac 2 -ar 44100 \
    -c:a libmp3lame -b:a "$AUDIO_BITRATE" \
    -flush_packets 1 \
    -f mp3 -listen 1 -headers $'Access-Control-Allow-Origin: *\r\nCache-Control: no-store\r\n' \
    "http://0.0.0.0:${AUDIO_PORT}" 2>/tmp/audio.err

  code=$?
  # 正常情况：听众断开 → ffmpeg 退出 → 立刻重新监听
  if [[ -s /tmp/audio.err ]]; then
    tail -3 /tmp/audio.err | sed 's/^/[audio] /'
  fi
  log "监听重置 (code=$code)，1 秒后继续"
  sleep 1
done
