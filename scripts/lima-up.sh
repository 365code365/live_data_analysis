#!/usr/bin/env bash
# 在 macOS 上把整条链路搬进一台带 binder 的 Linux 虚拟机。
#
# 背景：Docker Desktop 的 LinuxKit 内核不含 binder 模块，redroid 安卓容器
# 无法启动，且无法通过配置修复。Ubuntu 的 linux-modules-extra 带 binder_linux，
# 所以这里用 lima 起一台 Ubuntu 虚拟机，在里面跑 docker。
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTANCE="${LDM_LIMA_INSTANCE:-ldm}"
CPUS="${LDM_LIMA_CPUS:-6}"
MEMORY="${LDM_LIMA_MEMORY:-8GiB}"
DISK="${LDM_LIMA_DISK:-40GiB}"
APT_MIRROR="${APT_MIRROR-mirrors.aliyun.com}"

ok()   { printf '\033[32m[ OK ]\033[0m %s\n' "$*"; }
info() { printf '\033[36m[INFO]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[WARN]\033[0m %s\n' "$*"; }
die()  { printf '\033[31m[FAIL]\033[0m %s\n' "$*" >&2; exit 1; }

command -v limactl >/dev/null 2>&1 || die "没有 limactl。先执行： brew install lima"

# ── 磁盘余量提醒 ──────────────────────────────────────────────────────────
avail_gb=$(df -g / | awk 'NR==2{print $4}')
if [[ "${avail_gb:-99}" -lt 20 ]]; then
  warn "宿主可用磁盘只剩 ${avail_gb}GB。虚拟机镜像 + docker 镜像 + 安卓数据卷大约需要 12-15GB。"
fi

# ── 生成实例配置 ──────────────────────────────────────────────────────────
CONF="$PROJECT_DIR/lima/.ldm.generated.yaml"
sed -e "s|__PROJECT_DIR__|${PROJECT_DIR}|g" \
    -e "s|__CPUS__|${CPUS}|g" \
    -e "s|__MEMORY__|${MEMORY}|g" \
    -e "s|__DISK__|${DISK}|g" \
    -e "s|__APT_MIRROR__|${APT_MIRROR}|g" \
    -e "s|__INSTANCE__|${INSTANCE}|g" \
    "$PROJECT_DIR/lima/ldm.yaml.tmpl" > "$CONF"
ok "已生成 $CONF"

# ── 创建 / 启动 ───────────────────────────────────────────────────────────
if limactl list --format '{{.Name}}' 2>/dev/null | grep -qx "$INSTANCE"; then
  status=$(limactl list --format '{{.Status}}' "$INSTANCE" 2>/dev/null || echo Unknown)
  info "实例 $INSTANCE 已存在（${status}）"
  if [[ "$status" != "Running" ]]; then
    info "启动中，首次启动要装内核模块包，请耐心等待…"
    limactl start "$INSTANCE"
  fi
else
  info "创建虚拟机 ${INSTANCE}（cpus=$CPUS mem=$MEMORY disk=${DISK}）"
  info "要下载 Ubuntu 24.04 云镜像（约 600MB）+ 安装内核模块包，首次 5-15 分钟"
  limactl start --name="$INSTANCE" --tty=false "$CONF"
fi

# ── 验证 binder ───────────────────────────────────────────────────────────
info "验证虚拟机内核能力…"
limactl shell "$INSTANCE" -- bash -s <<'EOS'
set -u
printf '  kernel      : %s\n' "$(uname -r)"
if grep -qw binder /proc/filesystems; then
  printf '  binder      : \033[32m可用\033[0m\n'
else
  printf '  binder      : \033[31m不可用\033[0m\n'
  exit 1
fi
printf '  binderfs    : %s\n' "$(mountpoint -q /dev/binderfs && echo 已挂载 || echo 未挂载（redroid 以 privileged 运行时会自行挂载）)"
printf '  /dev/net/tun: %s\n' "$([ -e /dev/net/tun ] && echo 存在 || echo 缺失)"
printf '  docker      : %s\n' "$(docker version --format '{{.Server.Os}}/{{.Server.Arch}} {{.Server.Version}}' 2>/dev/null || echo 不可用)"
EOS

ok "虚拟机内核支持 binder，安卓容器可以跑了"

cat <<EOF

下一步（都在虚拟机里执行，项目目录已挂载到同样的路径）：

  limactl shell $INSTANCE
  cd $PROJECT_DIR
  cp -n .env.example .env
  # 数据落虚拟机本地盘，避免 SQLite 跑在共享目录上
  grep -q '^DATA_HOST_DIR=' .env || echo 'DATA_HOST_DIR=/var/lib/ldm/data' >> .env
  make build && make pull-android && make up

或者一条命令搞定：

  make lima-deploy

跑起来后，在 Mac 的浏览器里直接开 http://localhost:8000
（lima 会把虚拟机内监听的端口自动转发到 macOS 的 localhost）
EOF
