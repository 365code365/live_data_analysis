#!/usr/bin/env bash
# 投屏稳定性体检：把「容器侧断链」和「浏览器整页重载」分开看。
#
# 用法: ./scripts/check-screen-health.sh [设备ID] [观察分钟数]
#      ./scripts/check-screen-health.sh 1 10
set -uo pipefail

DEV="${1:-1}"
MINUTES="${2:-10}"
API="${LDM_API:-http://localhost:8000}"
PREFIX="${LDM_PREFIX:-ldm}"

# 在 lima 虚拟机里跑时自动转发 docker 命令
if command -v docker >/dev/null 2>&1 && docker ps >/dev/null 2>&1 \
   && docker ps --format '{{.Names}}' | grep -q "^${PREFIX}_vnc_${DEV}$"; then
  DOCKER="docker"
elif command -v limactl >/dev/null 2>&1; then
  DOCKER="limactl shell ${LDM_LIMA_INSTANCE:-ldm} -- docker"
else
  echo "找不到设备容器 ${PREFIX}_vnc_${DEV}" >&2
  exit 1
fi

VNC="${PREFIX}_vnc_${DEV}"
echo "── 设备 ${DEV} 最近 ${MINUTES} 分钟 ──────────────────────────"

echo "· 投屏进程"
starts=$($DOCKER logs --since "${MINUTES}m" "$VNC" 2>&1 | grep -c "启动 scrcpy" || true)
echo "    scrcpy 启动次数: ${starts}  （>1 说明投屏进程在反复重启，看 /tmp/scrcpy.log）"

echo "· VNC 客户端连接"
conns=$($DOCKER exec "$VNC" sh -c "grep -c 'Got connection from client' /tmp/x11vnc.log 2>/dev/null" 2>/dev/null | tr -d '\r')
echo "    累计连接次数: ${conns:-0}"
$DOCKER exec "$VNC" sh -c "grep 'Got connection from client' /tmp/x11vnc.log 2>/dev/null | tail -5" 2>/dev/null | sed 's/^/    /'
cat <<'EOF'
    判读:
      * 正常：打开一个控制台页面 → 只多一次连接，之后长期不变
      * 每 10 秒左右规律新增 → 浏览器在整页重载 iframe（前端问题）
      * 伴随 "rfbProcessClientProtocolVersion: client gone" → 页面 JS 没走完握手
EOF

echo "· 浏览器回报的投屏状态（服务端事件流）"
curl -s -m 15 "${API}/api/events?limit=200" 2>/dev/null | python3 -c "
import sys, json
try:
    items = json.load(sys.stdin).get('items', [])
except Exception:
    print('    读取事件失败（控制器没起？）'); raise SystemExit
rows = [e for e in items if e.get('source') == 'screen' and str(e.get('device_id')) == '$DEV']
if not rows:
    print('    暂无回报。打开控制台页面后会自动上报 connecting/connected/disconnected')
for e in rows[:8]:
    print(f\"    {e['created_at'][:19]} [{e['level']}] {e['message']}\")
" 2>/dev/null

echo "· 画面内容"
$DOCKER exec "$VNC" sh -c "command -v xwd >/dev/null 2>&1 || (apt-get update -qq >/dev/null 2>&1; apt-get install -y -qq x11-apps >/dev/null 2>&1); xwd -display :0 -root -silent 2>/dev/null | gzip -1 | wc -c" 2>/dev/null \
  | awk '{print "    X 屏幕压缩字节: " $1 "   （约 20 = 全黑；几十万 = 有画面）"}'

echo
echo "· 服务端链路握手"
python3 "$(dirname "$0")/check-vnc-handshake.py" "${LDM_HOST:-localhost}" \
  "$(curl -s -m 10 "${API}/api/devices/${DEV}" 2>/dev/null | python3 -c 'import sys,json;print(json.load(sys.stdin).get("novnc_port",""))' 2>/dev/null)" 2>&1 \
  | sed 's/^/    /'
