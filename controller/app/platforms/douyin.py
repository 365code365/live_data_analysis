from __future__ import annotations

import logging
import re
from typing import Any

from .base import PlatformAdapter

log = logging.getLogger(__name__)

WEB_RID_RE = re.compile(r"live\.douyin\.com/(\w+)")
ROOM_ID_RE = re.compile(r"\b(\d{15,25})\b")
SEC_UID_RE = re.compile(r"(MS4wLjABAAAA[\w-]+)")


class DouyinAdapter(PlatformAdapter):
    key = "douyin"
    display_name = "抖音"

    def build_deeplinks(self, target: str) -> list[str]:
        """抖音直播间的 target 可以是这几种，逐一映射成候选 deeplink：

        * 长数字 room_id（19 位左右）
        * live.douyin.com/<web_rid> 链接或纯 web_rid（短数字）
        * v.douyin.com 短链（直接丢给 App 让它自己跳）
        * sec_uid（MS4wLjABAAAA...）
        """
        target = (target or "").strip()
        links: list[str] = []
        launch = self.config.get("launch", {})

        def add(tmpl: str, value: str) -> None:
            uri = tmpl.format(target=value)
            if uri not in links:
                links.append(uri)

        # 已经是 scheme 的直接用
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://", target) and not target.startswith(("http://", "https://")):
            links.append(target)

        m = SEC_UID_RE.search(target)
        if m:
            for tmpl in launch.get("sec_uid_deeplinks", []):
                add(tmpl, m.group(1))

        m = ROOM_ID_RE.search(target)
        if m:
            for tmpl in launch.get("room_id_deeplinks", []):
                add(tmpl, m.group(1))

        m = WEB_RID_RE.search(target)
        web_rid = m.group(1) if m else (target if re.fullmatch(r"\d{4,14}", target) else None)
        if web_rid:
            for tmpl in launch.get("web_rid_deeplinks", []):
                add(tmpl, web_rid)
            add("https://live.douyin.com/{target}", web_rid)

        if target.startswith(("http://", "https://")) and target not in links:
            links.append(target)

        for tmpl in launch.get("deeplinks", []):
            add(tmpl, target)

        return links

    def extract_live_info(self, tree: Any):  # noqa: ANN401
        info = super().extract_live_info(tree)
        # 抖音把「在线人数」和「总观看」混排，取不到在线时退一步用总观看
        if info.viewer_count is None:
            fallback = self.config.get("live_info", {}).get("viewer_fallback_patterns", [])
            text = self._match_text(tree, fallback)
            if text:
                info.viewer_count = self._number_from(text, fallback)
                info.viewer_text = text
        return info
