"""开云手机时可选的固定档位：性能、屏幕、磁盘。

为什么做成固定档位而不是让用户填数字：
  * 内存/CPU 填错会直接 OOM 或卡死，分辨率填奇怪的值 redroid 会起不来；
  * 「不同配置不同价格」需要一组可枚举的规格才好定价与对账；
  * 用户想的是「要多快、屏幕多大、出口在哪」，不是 mem_limit 多少 MB。

套餐（Plan）里的规格优先级高于这里的档位：买了套餐就按套餐规格开机。
"""

from __future__ import annotations

from typing import Any, Optional

# ── 性能档位 ──────────────────────────────────────────────────────────────
# memory_mb / cpu_limit 会落到 docker 的 mem_limit 与 cpu_quota 上；
# disk_gb 是安卓 /data 卷的容量，宿主文件系统支持配额时才是硬限制（见 docker_manager）。
PERFORMANCE_TIERS: list[dict[str, Any]] = [
    {
        "code": "lite",
        "name": "轻量型",
        "cpu_limit": 2.0,
        "memory_mb": 2048,
        "disk_gb": 16,
        "note": "盯一个直播间够用，最省宿主资源",
    },
    {
        "code": "standard",
        "name": "标准型",
        "cpu_limit": 2.0,
        "memory_mb": 4096,
        "disk_gb": 32,
        "badge": "推荐",
        "note": "日常监控 + 录屏，App 不容易被系统杀掉",
    },
    {
        "code": "perf",
        "name": "高性能",
        "cpu_limit": 4.0,
        "memory_mb": 6144,
        "disk_gb": 64,
        "note": "多任务并行、长时间录屏",
    },
    {
        "code": "max",
        "name": "旗舰型",
        "cpu_limit": 6.0,
        "memory_mb": 8192,
        "disk_gb": 128,
        "note": "全高清分辨率 + 多开",
    },
]

# ── 屏幕档位 ──────────────────────────────────────────────────────────────
# 方向在开机时定死（redroid 没有传感器，建好之后转不了屏），所以横屏是独立档位。
SCREEN_PRESETS: list[dict[str, Any]] = [
    {
        "code": "hd_p",
        "name": "高清竖屏",
        "width": 720,
        "height": 1280,
        "dpi": 320,
        "orientation": "portrait",
        "badge": "推荐",
        "note": "抖音/小红书默认形态，最省资源",
    },
    {
        "code": "fhd_p",
        "name": "全高清竖屏",
        "width": 1080,
        "height": 1920,
        "dpi": 420,
        "orientation": "portrait",
        "note": "商品价格小字看得更清，采集更准",
    },
    {
        "code": "hd_l",
        "name": "高清横屏",
        "width": 1280,
        "height": 720,
        "dpi": 320,
        "orientation": "landscape",
        "note": "宽屏显示器上铺满，适合多窗口盯播",
    },
    {
        "code": "fhd_l",
        "name": "全高清横屏",
        "width": 1920,
        "height": 1080,
        "dpi": 420,
        "orientation": "landscape",
        "note": "大屏投屏演示",
    },
]

# ── 磁盘档位（安卓 /data 容量，GB）────────────────────────────────────────
DISK_OPTIONS: list[int] = [16, 32, 64, 128, 256]

DEFAULT_PERF = "standard"
DEFAULT_SCREEN = "hd_p"


def _find(items: list[dict[str, Any]], code: Optional[str]) -> Optional[dict[str, Any]]:
    if not code:
        return None
    for item in items:
        if item["code"] == code:
            return item
    return None


def performance(code: Optional[str]) -> Optional[dict[str, Any]]:
    return _find(PERFORMANCE_TIERS, code)


def screen(code: Optional[str]) -> Optional[dict[str, Any]]:
    return _find(SCREEN_PRESETS, code)


def perf_name(code: Optional[str]) -> str:
    tier = performance(code)
    return tier["name"] if tier else ""


def screen_name(code: Optional[str]) -> str:
    preset = screen(code)
    return preset["name"] if preset else ""


def valid_disk(value: Optional[int]) -> int:
    """只接受档位里的容量，0 表示不设。"""
    if not value:
        return 0
    value = int(value)
    return value if value in DISK_OPTIONS else 0


__all__ = [
    "PERFORMANCE_TIERS",
    "SCREEN_PRESETS",
    "DISK_OPTIONS",
    "DEFAULT_PERF",
    "DEFAULT_SCREEN",
    "performance",
    "screen",
    "perf_name",
    "screen_name",
    "valid_disk",
]
