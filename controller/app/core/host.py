from __future__ import annotations

import logging
import os
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


# 给安卓之外的东西（控制器、网关、画面容器、宿主自己）留出的余量。
# 一台安卓实例吃掉宿主几乎全部内存时，表现不是「跑得慢」而是整机卡死。
HOST_RESERVE_MB = 1536
HOST_RESERVE_RATIO = 0.15


@lru_cache(maxsize=1)
def resources() -> dict[str, Any]:
    """宿主的 CPU 核数与物理内存。

    控制器跑在容器里，但 /proc/meminfo 与 /proc/cpuinfo 都不做命名空间隔离，
    读到的就是宿主的真实规格 —— 正好用来判断某个性能档位在这台机器上开不开得起。
    """
    mem_total_mb = 0
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                mem_total_mb = int(line.split()[1]) // 1024
                break
    except (OSError, ValueError):
        pass

    cpus = os.cpu_count() or 0
    reserve = max(HOST_RESERVE_MB, int(mem_total_mb * HOST_RESERVE_RATIO)) if mem_total_mb else 0
    return {
        "cpu_count": cpus,
        "memory_total_mb": mem_total_mb,
        # 单台安卓实例最多能要多少内存（再多就该整机 swap / OOM 了）
        "memory_budget_mb": max(0, mem_total_mb - reserve),
        "reserved_mb": reserve,
    }


def fits_host(*, memory_mb: int = 0, cpu_limit: float = 0) -> tuple[bool, str]:
    """这个规格在当前宿主上开不开得起。返回 (行不行, 说明)。"""
    res = resources()
    budget = int(res["memory_budget_mb"] or 0)
    cpus = int(res["cpu_count"] or 0)

    if memory_mb and budget and memory_mb > budget:
        return False, (
            f"这台宿主只有 {res['memory_total_mb'] // 1024}GB 内存，"
            f"给安卓实例的上限约 {budget // 1024}GB（要给控制器、网关、画面容器留出余量）。"
            f"该规格要 {memory_mb // 1024}GB，开起来会把整机拖死。"
            "请选低一档，或给宿主/虚拟机加内存。"
        )
    if cpu_limit and cpus and cpu_limit > cpus:
        return False, f"该规格要 {cpu_limit:g} 核，而宿主只有 {cpus} 核。"
    return True, ""
