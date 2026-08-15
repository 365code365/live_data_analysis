from __future__ import annotations

import logging
import platform
from functools import lru_cache
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# /proc/filesystems 和 /proc/misc 不做命名空间隔离，
# 所以在控制器容器里读到的就是宿主内核的真实情况。
PROC_FILESYSTEMS = Path("/proc/filesystems")
PROC_MISC = Path("/proc/misc")

BINDER_HELP = (
    "宿主内核没有 binder 支持，redroid 安卓容器无法启动（会立刻退出并反复重启）。\n"
    "· Linux 宿主：sudo ./scripts/host-setup.sh 加载 binder_linux 模块"
    "（Ubuntu 需先装 linux-modules-extra-$(uname -r)）\n"
    "· macOS / Windows 的 Docker Desktop：LinuxKit 内核不带 binder，无法修复。"
    "请把本项目部署到 Linux 主机，或在本机开一台带 binder 模块的 Linux 虚拟机"
    "（Lima/UTM + Ubuntu + linux-modules-extra）后在虚拟机内运行。\n"
    "· 控制器、代理网关、VNC 容器不受影响，可以先在本机调通代理与采集规则。"
)


def _kernel_supports(fs_name: str) -> bool:
    try:
        return fs_name in PROC_FILESYSTEMS.read_text()
    except OSError:
        return False


def _misc_device(name: str) -> bool:
    try:
        return any(line.split()[-1] == name for line in PROC_MISC.read_text().splitlines() if line.strip())
    except OSError:
        return False


@lru_cache(maxsize=1)
def capabilities() -> dict[str, Any]:
    """宿主内核能力探测（结果缓存，重启控制器才会重新探测）。"""
    binder = _kernel_supports("binder") or Path("/dev/binderfs").exists()
    tun = _misc_device("tun") or Path("/dev/net/tun").exists()
    kernel = platform.release()
    caps = {
        "kernel": kernel,
        "binder": binder,
        "tun": tun,
        "docker_desktop": "linuxkit" in kernel.lower(),
        "android_supported": binder,
        "hints": [],
    }
    if not binder:
        caps["hints"].append(BINDER_HELP)
    if not tun:
        caps["hints"].append(
            "宿主缺少 tun 设备，代理网关无法建隧道。Linux 执行 sudo modprobe tun。"
        )
    return caps


def require_android_support() -> None:
    caps = capabilities()
    if not caps["android_supported"]:
        raise RuntimeError(BINDER_HELP)
