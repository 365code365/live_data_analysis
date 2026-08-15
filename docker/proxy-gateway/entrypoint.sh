#!/bin/bash
# 代理网关入口。
#   PROXY_URL 为空       → 直连模式（不建 tun，仅保持容器存活）
#   PROXY_URL 非空       → 建 tun0，tun2socks 接管默认路由，DNS 全量劫持到本地 dnsproxy
#
# 安卓容器以 network_mode=container:<本容器> 方式共享此 netns，
# 因此它的全部流量（含 UDP、DNS）都走这里出去，不会漏真实 IP。
set -uo pipefail

log() { printf '[gw %s] %s\n' "$(date '+%H:%M:%S')" "$*"; }
die() { printf '[gw ERROR] %s\n' "$*" >&2; exit 1; }

TUN_NAME="${TUN_NAME:-tun0}"
TUN_ADDR="${TUN_ADDR:-198.18.0.1}"
TUN_MASK="${TUN_MASK:-15}"
TUN_MTU="${TUN_MTU:-1500}"
KILL_SWITCH="${KILL_SWITCH:-true}"
DISABLE_IPV6="${DISABLE_IPV6:-true}"
LOGLEVEL="${LOGLEVEL:-info}"
DNS_UPSTREAM="${DNS_UPSTREAM:-https://1.1.1.1/dns-query,tls://1.1.1.1}"
DNS_FALLBACK="${DNS_FALLBACK:-tcp://1.1.1.1:53,tcp://8.8.8.8:53}"
DNS_BOOTSTRAP="${DNS_BOOTSTRAP:-1.1.1.1:53,8.8.8.8:53}"
PROXY_URL="${PROXY_URL:-}"

PIDS=()
cleanup() {
  log "收到退出信号，清理子进程"
  for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null || true; done
  wait 2>/dev/null || true
}
trap cleanup TERM INT

# ── 探测原始默认路由 ─────────────────────────────────────────────────────
DEFAULT_ROUTE="$(ip -4 route show default | head -1)"
[[ -z "$DEFAULT_ROUTE" ]] && die "容器内没有默认路由，无法初始化网关"
UPLINK_GW="$(awk '{for(i=1;i<=NF;i++) if($i=="via"){print $(i+1); exit}}' <<<"$DEFAULT_ROUTE")"
UPLINK_IF="$(awk '{for(i=1;i<=NF;i++) if($i=="dev"){print $(i+1); exit}}' <<<"$DEFAULT_ROUTE")"
LOCAL_SUBNET="$(ip -4 -o route show scope link dev "$UPLINK_IF" | awk '{print $1}' | head -1)"
log "上行链路: dev=$UPLINK_IF gw=$UPLINK_GW subnet=${LOCAL_SUBNET:-unknown}"

# ── IPv6 关闭（避免绕过 tun 泄漏）────────────────────────────────────────
if [[ "$DISABLE_IPV6" == "true" ]]; then
  sysctl -w net.ipv6.conf.all.disable_ipv6=1 >/dev/null 2>&1 || true
  # 回环必须放行：容器内很多组件（尤其 adb）会优先用 ::1 连本地服务，
  # 一刀切 DROP 会让这些连接静默卡死，而不是立刻失败。
  ip6tables -A OUTPUT -o lo -j ACCEPT >/dev/null 2>&1 || true
  ip6tables -A INPUT  -i lo -j ACCEPT >/dev/null 2>&1 || true
  # 对外用 REJECT 而不是 DROP：出网要立刻报错，不能让调用方一直等超时。
  ip6tables -A OUTPUT ! -o lo -j REJECT --reject-with icmp6-adm-prohibited >/dev/null 2>&1 \
    || ip6tables -A OUTPUT ! -o lo -j REJECT >/dev/null 2>&1 || true
  ip6tables -P FORWARD DROP >/dev/null 2>&1 || true
  log "IPv6 出网已禁止（回环放行，避免本地连接卡死）"
fi

# ── 直连模式 ─────────────────────────────────────────────────────────────
if [[ -z "$PROXY_URL" || "$PROXY_URL" == "direct" ]]; then
  log "PROXY_URL 未设置 → 直连模式（出口 IP 即宿主机 IP）"
  log "出口: $(/usr/local/bin/egress-ip 2>/dev/null || echo 'unknown')"
  while :; do sleep 3600; done
fi

# ── 解析代理服务器地址，钉死走物理网卡，防止路由环 ───────────────────────
proxy_host() {
  local url="$1" rest hostport
  rest="${url#*://}"      # 去 scheme
  rest="${rest%%/*}"      # 去 path
  hostport="${rest##*@}"  # 去 user:pass@
  # 兼容 [ipv6]:port
  if [[ "$hostport" == \[*\]* ]]; then
    printf '%s' "${hostport%%\]*}]"
  else
    printf '%s' "${hostport%%:*}"
  fi
}

PHOST="$(proxy_host "$PROXY_URL")"
[[ -z "$PHOST" ]] && die "无法从 PROXY_URL 解析出代理主机: $PROXY_URL"

PROXY_IPS=()
if [[ "$PHOST" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  PROXY_IPS+=("$PHOST")
else
  log "解析代理域名 $PHOST ..."
  while read -r ip; do [[ -n "$ip" ]] && PROXY_IPS+=("$ip"); done < <(
    getent ahostsv4 "$PHOST" 2>/dev/null | awk '{print $1}' | sort -u
  )
  [[ ${#PROXY_IPS[@]} -eq 0 ]] && die "代理域名 $PHOST 解析失败"
fi
log "代理服务器 IP: ${PROXY_IPS[*]}"

for ip in "${PROXY_IPS[@]}"; do
  ip route replace "$ip/32" via "$UPLINK_GW" dev "$UPLINK_IF" 2>/dev/null \
    && log "已固定路由 $ip → $UPLINK_IF" \
    || log "警告: 固定路由 $ip 失败（可能与本地子网重叠，忽略）"
done

# 把 URL 里的域名换成解析好的 IP：接管 DNS 之后再去解析代理域名会形成
# 「解析要走代理、走代理先要解析」的死锁，这里提前固化掉。
PROXY_URL_EFF="$PROXY_URL"
if [[ ! "$PHOST" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  _scheme="${PROXY_URL%%://*}"
  _rest="${PROXY_URL#*://}"
  _creds=""
  if [[ "$_rest" == *"@"* ]]; then
    _creds="${_rest%@*}@"
    _hostport="${_rest##*@}"
  else
    _hostport="$_rest"
  fi
  _hostport="${_hostport%%/*}"
  _port=""
  [[ "$_hostport" == *:* ]] && _port="${_hostport##*:}"
  PROXY_URL_EFF="${_scheme}://${_creds}${PROXY_IPS[0]}${_port:+:$_port}"
  echo "${PROXY_IPS[0]} $PHOST" >> /etc/hosts
  log "代理地址已固化: $PHOST → ${PROXY_IPS[0]}"
fi

# ── 建 tun 设备 ──────────────────────────────────────────────────────────
ip tuntap add mode tun dev "$TUN_NAME" 2>/dev/null || die "创建 $TUN_NAME 失败（需要 --cap-add NET_ADMIN 与 /dev/net/tun）"
ip addr add "${TUN_ADDR}/${TUN_MASK}" dev "$TUN_NAME"
ip link set dev "$TUN_NAME" mtu "$TUN_MTU" up
log "$TUN_NAME 已就绪 ${TUN_ADDR}/${TUN_MASK} mtu=$TUN_MTU"

# ── 启动 tun2socks ───────────────────────────────────────────────────────
tun2socks \
  --device "$TUN_NAME" \
  --proxy "$PROXY_URL_EFF" \
  --interface "$UPLINK_IF" \
  --loglevel "$LOGLEVEL" &
T2S_PID=$!
PIDS+=("$T2S_PID")
sleep 1
kill -0 "$T2S_PID" 2>/dev/null || die "tun2socks 启动失败，请检查 PROXY_URL 格式（socks5://user:pass@host:port 或 http://host:port）"
log "tun2socks 已启动 (pid=$T2S_PID) → $PROXY_URL"

# ── 切换默认路由到 tun ───────────────────────────────────────────────────
ip route del default 2>/dev/null || true
ip route add default via "$TUN_ADDR" dev "$TUN_NAME" metric 1 \
  || die "默认路由切换失败"
log "默认路由已切到 $TUN_NAME"

# ── 本地 DNS：dnsproxy(DoH) + 全量劫持 53 端口 ───────────────────────────
DNS_ARGS=(-l 127.0.0.1 -l 0.0.0.0 -p 53 --cache --cache-min-ttl=30)
IFS=',' read -ra _ups <<<"$DNS_UPSTREAM"
for u in "${_ups[@]}"; do [[ -n "$u" ]] && DNS_ARGS+=(-u "$u"); done
# 兜底上游：部分网络会封 DoH/DoT，这里用 TCP 明文兜底。
# 注意它同样走 tun0 → 代理出去，所以不会泄漏真实 IP。
IFS=',' read -ra _fbs <<<"${DNS_FALLBACK:-}"
for f in "${_fbs[@]}"; do [[ -n "$f" ]] && DNS_ARGS+=(-f "$f"); done
IFS=',' read -ra _boots <<<"$DNS_BOOTSTRAP"
for b in "${_boots[@]}"; do [[ -n "$b" ]] && DNS_ARGS+=(-b "$b"); done

dnsproxy "${DNS_ARGS[@]}" >/tmp/dnsproxy.log 2>&1 &
DNS_PID=$!
PIDS+=("$DNS_PID")
sleep 1
if kill -0 "$DNS_PID" 2>/dev/null; then
  log "dnsproxy 已启动 (pid=$DNS_PID) upstream=$DNS_UPSTREAM fallback=${DNS_FALLBACK:-无}"
  # 必须插到 nat OUTPUT 链首：docker 内置 DNS(127.0.0.11) 自带 DNAT 规则，
  # 用 -A 追加会被它抢先命中，DNS 就绕过了代理（会泄漏真实位置）。
  iptables -t nat -I OUTPUT 1 -p udp --dport 53 -j REDIRECT --to-ports 53 2>/dev/null \
    && log "UDP/53 已劫持到本地 dnsproxy"
  iptables -t nat -I OUTPUT 1 -p tcp --dport 53 -j REDIRECT --to-ports 53 2>/dev/null \
    && log "TCP/53 已劫持到本地 dnsproxy"
  # bootstrap 的明文 DNS 目标要放行，否则 dnsproxy 会劫持自己形成环。
  # 用 -I 插到上面两条之前。
  for b in "${_boots[@]}"; do
    bip="${b%%:*}"
    [[ -n "$bip" ]] || continue
    iptables -t nat -I OUTPUT 1 -p udp -d "$bip" --dport 53 -j RETURN 2>/dev/null || true
    iptables -t nat -I OUTPUT 1 -p tcp -d "$bip" --dport 53 -j RETURN 2>/dev/null || true
  done
else
  log "警告: dnsproxy 启动失败，DNS 将直接经代理转发（见 /tmp/dnsproxy.log）"
  cat /tmp/dnsproxy.log >&2 || true
fi

# ── kill switch：tun2socks 挂掉时不允许流量裸奔 ──────────────────────────
if [[ "$KILL_SWITCH" == "true" ]]; then
  iptables -N LDM_KS 2>/dev/null || iptables -F LDM_KS
  iptables -A LDM_KS -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
  [[ -n "$LOCAL_SUBNET" ]] && iptables -A LDM_KS -d "$LOCAL_SUBNET" -j ACCEPT
  iptables -A LDM_KS -d 127.0.0.0/8 -j ACCEPT
  for ip in "${PROXY_IPS[@]}"; do iptables -A LDM_KS -d "$ip" -j ACCEPT; done
  iptables -A LDM_KS -j REJECT --reject-with icmp-net-unreachable
  iptables -C OUTPUT -o "$UPLINK_IF" -j LDM_KS 2>/dev/null \
    || iptables -A OUTPUT -o "$UPLINK_IF" -j LDM_KS
  log "kill switch 已启用：除代理服务器与本地网段外，禁止绕过 $TUN_NAME 出网"
fi

# ── 出口自检 ─────────────────────────────────────────────────────────────
for i in 1 2 3 4 5; do
  if out="$(/usr/local/bin/egress-ip 2>/dev/null)"; then
    log "出口校验通过: $out"
    printf '%s' "$out" > /tmp/egress.json
    break
  fi
  log "出口校验重试 $i/5 ..."
  sleep 3
done
[[ -f /tmp/egress.json ]] || log "警告: 出口 IP 校验未通过，代理可能不可用（容器继续运行，可在控制台重试）"

log "网关就绪，等待安卓容器接入"
wait -n "${PIDS[@]}"
code=$?
log "关键子进程退出 (code=$code)，网关退出以触发重启"
cleanup
exit "$code"
