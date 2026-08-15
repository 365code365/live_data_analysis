from __future__ import annotations

import logging
import re
import time
from typing import Any

from ..core.android import AndroidDevice
from .base import CollectError, PlatformAdapter

log = logging.getLogger(__name__)

USER_ID_RE = re.compile(r"(?:user/profile/)?([0-9a-f]{24})")
ROOM_ID_RE = re.compile(r"\b(\d{8,25})\b")


class XiaohongshuAdapter(PlatformAdapter):
    key = "xiaohongshu"
    display_name = "小红书"

    def build_deeplinks(self, target: str) -> list[str]:
        target = (target or "").strip()
        links: list[str] = []
        launch = self.config.get("launch", {})

        def add(tmpl: str, value: str) -> None:
            uri = tmpl.format(target=value)
            if uri not in links:
                links.append(uri)

        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://", target) and not target.startswith(("http://", "https://")):
            links.append(target)

        m = ROOM_ID_RE.search(target)
        if m:
            for tmpl in launch.get("room_id_deeplinks", []):
                add(tmpl, m.group(1))

        m = USER_ID_RE.search(target)
        if m:
            for tmpl in launch.get("user_id_deeplinks", []):
                add(tmpl, m.group(1))

        if target.startswith(("http://", "https://")) and target not in links:
            links.append(target)

        for tmpl in launch.get("deeplinks", []):
            add(tmpl, target)
        return links

    def enter_room(self, dev: AndroidDevice, target: str) -> None:
        """小红书直播间的 deeplink 版本差异大，deeplink 全失败时
        回落到「打开主页 → 点头像/直播中标记」这条路。"""
        try:
            super().enter_room(dev, target)
            return
        except CollectError as exc:
            log.warning("小红书 deeplink 进入失败(%s)，尝试主页入口", exc)

        m = USER_ID_RE.search(target or "")
        if not m:
            raise CollectError(f"deeplink 进入失败且 target 中没有用户 id，无法回落: {target!r}")

        profile_tmpl = self.config.get("launch", {}).get("profile_deeplink", "xhsdiscover://user/{target}")
        dev.open_deeplink(profile_tmpl.format(target=m.group(1)), self.package or None)
        time.sleep(float(self.config.get("launch", {}).get("wait_seconds", 8)))

        tree = self._safe_tree(dev)
        markers = self.config.get("launch", {}).get("profile_live_markers", ["直播中", "正在直播"])
        hits = tree.find_by_pattern(markers)
        if not hits:
            raise CollectError("主页上没有「直播中」标记，主播可能没在开播")
        x, y = hits[0].clickable_self_or_ancestor().center
        dev.tap(x, y)
        time.sleep(float(self.config.get("launch", {}).get("wait_seconds", 8)))

        if not self.in_live_room(self._safe_tree(dev)):
            raise CollectError("点击主页直播入口后仍未进入直播间")

    def extract_live_info(self, tree: Any):  # noqa: ANN401
        info = super().extract_live_info(tree)
        if info.viewer_count is None:
            fallback = self.config.get("live_info", {}).get("viewer_fallback_patterns", [])
            text = self._match_text(tree, fallback)
            if text:
                info.viewer_count = self._number_from(text, fallback)
                info.viewer_text = text
        return info
