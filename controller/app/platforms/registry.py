from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Optional

import yaml

from ..config import settings
from .base import PlatformAdapter
from .douyin import DouyinAdapter
from .xiaohongshu import XiaohongshuAdapter

log = logging.getLogger(__name__)

BUILTIN_SELECTORS_DIR = Path(__file__).parent / "selectors"

_ADAPTER_CLASSES: dict[str, type[PlatformAdapter]] = {
    DouyinAdapter.key: DouyinAdapter,
    XiaohongshuAdapter.key: XiaohongshuAdapter,
}

_cache: dict[str, PlatformAdapter] = {}
_lock = threading.Lock()


def _selector_paths(key: str) -> list[Path]:
    """内置配置 + 可选外挂目录（外挂优先，做深合并）。"""
    paths = [BUILTIN_SELECTORS_DIR / f"{key}.yaml"]
    if settings.selectors_dir:
        paths.append(Path(settings.selectors_dir) / f"{key}.yaml")
    return paths


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(key: str) -> dict[str, Any]:
    config: dict[str, Any] = {}
    for path in _selector_paths(key):
        if not path.exists():
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if not isinstance(data, dict):
                log.warning("选择器文件格式不对（应为映射）: %s", path)
                continue
            config = _deep_merge(config, data)
            log.info("已加载选择器配置 %s", path)
        except Exception as exc:
            log.error("读取选择器配置失败 %s: %s", path, exc)
    return config


def get_adapter(key: str) -> PlatformAdapter:
    key = str(key)
    with _lock:
        adapter = _cache.get(key)
        if adapter is not None:
            return adapter
        cls: Optional[type[PlatformAdapter]] = _ADAPTER_CLASSES.get(key)
        if cls is None:
            raise KeyError(f"未知平台: {key}（可选: {', '.join(_ADAPTER_CLASSES)}）")
        adapter = cls(load_config(key))
        _cache[key] = adapter
        return adapter


def reload_selectors() -> list[str]:
    """改完 YAML 不用重启容器，调这里热加载。"""
    with _lock:
        _cache.clear()
    keys = list(_ADAPTER_CLASSES)
    for k in keys:
        get_adapter(k)
    return keys


def list_adapters() -> list[dict[str, Any]]:
    out = []
    for key, cls in _ADAPTER_CLASSES.items():
        cfg = load_config(key)
        out.append(
            {
                "key": key,
                "display_name": cls.display_name,
                "package": cfg.get("package"),
                "has_config": bool(cfg),
                "config_files": [str(p) for p in _selector_paths(key) if p.exists()],
            }
        )
    return out
