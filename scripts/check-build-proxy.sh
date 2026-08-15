#!/usr/bin/env bash
# 诊断「docker build 极慢 / 随机 500」这类问题：
# Docker Desktop 会继承宿主系统代理，代理软件关掉后端口没人监听，
# 构建期每个 apt/pip 请求都要先撞一次死代理，慢且随机失败。
set -uo pipefail

ok()   { printf '\033[32m[ OK ]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[WARN]\033[0m %s\n' "$*"; }
err()  { printf '\033[31m[BAD ]\033[0m %s\n' "$*"; }

echo "── Docker 守护进程代理 ───────────────────────────────"
docker info 2>/dev/null | grep -iE "^ (HTTP|HTTPS|No) Proxy" || echo "  （未配置）"

echo
echo "── 宿主系统代理 ─────────────────────────────────────"
DEAD=0
if [[ "$(uname -s)" == "Darwin" ]]; then
  enabled=$(scutil --proxy | awk '/HTTPEnable/{print $3}')
  host=$(scutil --proxy | awk '/HTTPProxy/{print $3}')
  port=$(scutil --proxy | awk '/HTTPPort/{print $3}')
  if [[ "${enabled:-0}" == "1" && -n "${port:-}" ]]; then
    echo "  系统 HTTP 代理: ${host}:${port}"
    if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
      ok "代理端口 ${port} 有进程监听，正常"
    else
      err "代理端口 ${port} 没有任何进程监听 —— 这就是构建慢/失败的原因"
      DEAD=1
    fi
  else
    ok "系统未启用 HTTP 代理"
  fi
else
  echo "  ${http_proxy:-（无 http_proxy 环境变量）}"
fi

echo
echo "── 结论 ─────────────────────────────────────────────"
if [[ $DEAD -eq 1 ]]; then
  cat <<'EOF'
  三选一即可：

  1) 本项目已默认绕过（推荐，无需额外操作）
       make build            # 内部传入空的 proxy build-arg + 国内镜像源

  2) 关掉宿主系统代理
       系统设置 → 网络 → 代理 → 关闭 HTTP/HTTPS 代理
       然后 Docker Desktop → Settings → Resources → Proxies 取消 "Use system proxy" → Apply & restart

  3) 把代理软件重新打开（让端口重新有人监听）
EOF
else
  cat <<'EOF'
  代理不是瓶颈。构建慢的其他常见原因：
    * VNC 镜像里 scrcpy 依赖整套 ffmpeg + SDL2，200+ 个包，首次 3-8 分钟属正常
    * 已配置 apt 缓存挂载，重试/改 Dockerfile 不会重新下载
    * 换更快的源： make build-vnc APT_MIRROR=mirrors.tuna.tsinghua.edu.cn
EOF
fi
