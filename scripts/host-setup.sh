#!/usr/bin/env bash
# 宿主机准备 / 自检：redroid 需要 binder，代理网关需要 /dev/net/tun
set -uo pipefail

CHECK_ONLY=0
[[ "${1:-}" == "--check" ]] && CHECK_ONLY=1

ok()   { printf '\033[32m[ OK ]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[WARN]\033[0m %s\n' "$*"; }
err()  { printf '\033[31m[FAIL]\033[0m %s\n' "$*"; }

FAILED=0

# ── docker ────────────────────────────────────────────────────────────────
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  ok "docker 可用：$(docker version --format '{{.Server.Os}}/{{.Server.Arch}}' 2>/dev/null)"
else
  err "docker 不可用（未安装或当前用户无权限）"; FAILED=1
fi

HOST_OS="$(uname -s)"

# ── binder（redroid 必需）─────────────────────────────────────────────────
check_binder() {
  if [[ -d /dev/binderfs ]] || [[ -e /dev/binder ]]; then
    ok "宿主机已有 binder 设备"
    return 0
  fi
  if grep -qw binder /proc/filesystems 2>/dev/null || grep -qw binderfs /proc/filesystems 2>/dev/null; then
    ok "内核支持 binderfs（redroid 以 privileged 运行时会自行挂载）"
    return 0
  fi
  return 1
}

load_binder() {
  for mod in binder_linux ashmem_linux; do
    if modprobe "$mod" devices="binder,hwbinder,vndbinder" 2>/dev/null || modprobe "$mod" 2>/dev/null; then
      ok "已加载内核模块 $mod"
    else
      warn "无法加载 ${mod}（可能已内建，或需要安装 linux-modules-extra-$(uname -r)）"
    fi
  done
  mkdir -p /dev/binderfs 2>/dev/null
  mountpoint -q /dev/binderfs 2>/dev/null || mount -t binder binder /dev/binderfs 2>/dev/null || true
}

if [[ "$HOST_OS" == "Linux" ]]; then
  if ! check_binder; then
    if [[ $CHECK_ONLY -eq 0 && $EUID -eq 0 ]]; then
      load_binder
      check_binder || { err "binder 仍不可用；请安装 linux-modules-extra 或换支持 binder 的内核"; FAILED=1; }
    else
      err "binder 不可用。请执行： sudo ./scripts/host-setup.sh"; FAILED=1
    fi
  fi

  # tun
  if [[ -e /dev/net/tun ]]; then
    ok "/dev/net/tun 存在（代理网关可用）"
  else
    if [[ $CHECK_ONLY -eq 0 && $EUID -eq 0 ]]; then
      modprobe tun && mkdir -p /dev/net && mknod /dev/net/tun c 10 200 && chmod 600 /dev/net/tun
      ok "已创建 /dev/net/tun"
    else
      err "/dev/net/tun 缺失，代理网关无法启动。请执行： sudo modprobe tun"; FAILED=1
    fi
  fi

  # 建议参数
  cur_max=$(sysctl -n fs.inotify.max_user_instances 2>/dev/null || echo 0)
  if [[ "$cur_max" -lt 1024 ]]; then
    if [[ $CHECK_ONLY -eq 0 && $EUID -eq 0 ]]; then
      sysctl -w fs.inotify.max_user_instances=8192 >/dev/null && ok "已调高 fs.inotify.max_user_instances"
    else
      warn "fs.inotify.max_user_instances=$cur_max 偏低，多实例安卓建议 >=8192"
    fi
  else
    ok "fs.inotify.max_user_instances=$cur_max"
  fi
else
  warn "当前宿主机为 ${HOST_OS}（Docker Desktop）。控制器 / 网关 / VNC 容器可正常运行；"
  warn "redroid 依赖 Docker Desktop 虚拟机内核的 binderfs 支持，能否启动请以实际结果为准。"
  warn "验证命令： docker run --rm --privileged \$REDROID_IMAGE /system/bin/sh -c 'echo binder-ok'"
  # Docker Desktop 的 VM 里通常自带 tun
  ok "跳过 binder / tun 的宿主机检查"
fi

# ── 目录 ──────────────────────────────────────────────────────────────────
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ $CHECK_ONLY -eq 0 ]]; then
  mkdir -p "$PROJECT_DIR"/data/{screenshots,recordings,dumps,android} "$PROJECT_DIR"/apks
  ok "数据目录已就绪：$PROJECT_DIR/data"
fi

if [[ ! -f "$PROJECT_DIR/.env" ]]; then
  warn ".env 不存在，请先 cp .env.example .env 并修改 HOST_PROJECT_DIR"
else
  hp=$(grep -E '^HOST_PROJECT_DIR=' "$PROJECT_DIR/.env" | cut -d= -f2-)
  if [[ "$hp" == "$PROJECT_DIR" ]]; then
    ok "HOST_PROJECT_DIR 正确：$hp"
  else
    warn "HOST_PROJECT_DIR=$hp 与实际项目路径 $PROJECT_DIR 不一致，设备容器挂载会失败"
  fi
fi

echo
if [[ $FAILED -eq 0 ]]; then ok "自检通过"; else err "自检存在阻塞项，见上方 FAIL"; exit 1; fi
