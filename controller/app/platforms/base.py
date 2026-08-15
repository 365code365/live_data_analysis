from __future__ import annotations

import logging
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

from ..core.android import AndroidDevice
from .uitree import (
    Node,
    UITree,
    compile_pattern,
    first_group,
    normalize_key,
    parse_cn_number,
    parse_hierarchy,
    parse_price,
)

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────
@dataclass
class ProductItem:
    position: Optional[int] = None
    title: Optional[str] = None
    price: Optional[float] = None
    price_text: Optional[str] = None
    origin_price: Optional[float] = None
    sales_text: Optional[str] = None
    stock_text: Optional[str] = None
    coupon_text: Optional[str] = None
    product_id: Optional[str] = None
    labels: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return normalize_key(self.title or "") or normalize_key("|".join(self.labels[:2]))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LiveInfo:
    is_live: bool = True
    room_id: Optional[str] = None
    room_title: Optional[str] = None
    anchor_name: Optional[str] = None
    viewer_count: Optional[int] = None
    viewer_text: Optional[str] = None
    like_count: Optional[int] = None
    follower_count: Optional[int] = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CollectResult:
    live: LiveInfo
    products: list[ProductItem] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)
    screenshot_path: Optional[str] = None
    dump_path: Optional[str] = None
    warnings: list[str] = field(default_factory=list)


class CollectError(RuntimeError):
    pass


# ──────────────────────────────────────────────────────────────────────────
class PlatformAdapter:
    """平台采集适配器基类。

    子类只需要处理「怎么进直播间」这件平台特有的事，
    直播间信息与商品列表的提取是通用启发式 + YAML 规则。
    """

    key: str = "base"
    display_name: str = "Base"

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config or {}
        self.package: str = self.config.get("package", "")
        self.packages: list[str] = [self.package, *self.config.get("package_alternatives", [])]
        self.packages = [p for p in self.packages if p]

    # ── 子类可覆盖 ────────────────────────────────────────────────────
    def build_deeplinks(self, target: str) -> list[str]:
        """把用户填的 target 转成一串候选 deeplink，按顺序尝试。"""
        target = (target or "").strip()
        out: list[str] = []
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://", target):
            out.append(target)
        elif target.startswith("http://") or target.startswith("https://"):
            out.append(target)
        for tmpl in self.config.get("launch", {}).get("deeplinks", []):
            out.append(tmpl.format(target=target))
        web = self.config.get("launch", {}).get("web_url")
        if web:
            out.append(web.format(target=target))
        # 去重保序
        seen: set[str] = set()
        return [u for u in out if not (u in seen or seen.add(u))]

    def in_live_room(self, tree: UITree) -> bool:
        markers = self.config.get("live_info", {}).get("room_markers", [])
        if markers and tree.find_by_pattern(markers):
            return True
        # 没配 marker 时：进入直播间通常同时存在评论入口与在线人数
        return bool(self._viewer_node(tree)) or bool(self._product_entry(tree))

    # ── 主流程 ────────────────────────────────────────────────────────
    def collect(
        self,
        dev: AndroidDevice,
        *,
        target: str,
        want_products: bool = True,
        want_comments: bool = False,
        screenshot_path: Optional[Path] = None,
        dump_path: Optional[Path] = None,
        max_scrolls: Optional[int] = None,
    ) -> CollectResult:
        warnings: list[str] = []
        dev.wake()

        tree = self._safe_tree(dev)
        if not self._is_current_app(dev) or not self.in_live_room(tree):
            self.enter_room(dev, target)
            tree = self._safe_tree(dev)

        live = self.extract_live_info(tree)
        live.room_id = live.room_id or self._target_room_id(target)

        result = CollectResult(live=live, warnings=warnings)

        if screenshot_path is not None:
            try:
                dev.screenshot(screenshot_path)
                result.screenshot_path = str(screenshot_path)
            except Exception as exc:
                warnings.append(f"截图失败: {exc}")

        if dump_path is not None:
            try:
                Path(dump_path).parent.mkdir(parents=True, exist_ok=True)
                Path(dump_path).write_text(tree.raw, encoding="utf-8")
                result.dump_path = str(dump_path)
            except Exception as exc:
                warnings.append(f"控件树落盘失败: {exc}")

        if not live.is_live:
            warnings.append("直播已结束或不在直播间")
            return result

        if want_comments:
            try:
                result.comments = self.extract_comments(tree)
            except Exception as exc:
                warnings.append(f"弹幕提取失败: {exc}")

        if want_products:
            try:
                result.products = self.collect_products(dev, tree, max_scrolls=max_scrolls)
            except Exception as exc:
                log.warning("商品采集失败: %s", exc)
                warnings.append(f"商品采集失败: {exc}")

        return result

    # ── 进直播间 ──────────────────────────────────────────────────────
    def enter_room(self, dev: AndroidDevice, target: str) -> None:
        launch_cfg = self.config.get("launch", {})
        wait = float(launch_cfg.get("wait_seconds", 8))
        links = self.build_deeplinks(target)
        if not links:
            raise CollectError(f"无法为 target={target!r} 构造进入直播间的链接")

        if self.package and not dev.is_installed(self.package):
            raise CollectError(f"设备上没有安装 {self.package}，请先在控制台安装 APK")

        last_error = ""
        for uri in links:
            log.info("尝试进入直播间: %s", uri)
            try:
                out = dev.open_deeplink(uri, self.package or None)
                if "Error" in out or "Exception" in out:
                    last_error = out.strip().splitlines()[-1] if out.strip() else "am start 失败"
                    continue
            except Exception as exc:
                last_error = str(exc)
                continue

            deadline = time.time() + wait
            while time.time() < deadline:
                time.sleep(2)
                try:
                    tree = self._safe_tree(dev)
                except Exception:
                    continue
                if self._dismiss_popups(dev, tree):
                    tree = self._safe_tree(dev)
                if self.in_live_room(tree):
                    log.info("已进入直播间 (%s)", uri)
                    return
            last_error = f"打开 {uri} 后未识别到直播间界面"

        raise CollectError(f"进入直播间失败: {last_error}")

    def _dismiss_popups(self, dev: AndroidDevice, tree: UITree) -> bool:
        """关掉「继续观看/我知道了/允许」这类挡路弹窗。"""
        patterns = self.config.get("popups", {}).get("dismiss_texts", [])
        if not patterns:
            return False
        for node in tree.find_by_pattern(patterns):
            hit = node.clickable_self_or_ancestor()
            if hit.area <= 0:
                continue
            x, y = hit.center
            log.info("关闭弹窗: %s", node.label)
            dev.tap(x, y)
            time.sleep(1.2)
            return True
        return False

    # ── 直播间信息 ────────────────────────────────────────────────────
    def extract_live_info(self, tree: UITree) -> LiveInfo:
        cfg = self.config.get("live_info", {})
        info = LiveInfo()

        end_markers = cfg.get("end_markers", [])
        if end_markers and tree.find_by_pattern(end_markers):
            info.is_live = False

        # 在线人数 / 人气
        vnode = self._viewer_node(tree)
        if vnode is not None:
            info.viewer_text = vnode.label
            info.viewer_count = self._number_from(vnode.label, cfg.get("viewer_patterns", []))

        # 点赞
        like_text = self._match_text(tree, cfg.get("like_patterns", []), cfg.get("like_region"))
        if like_text:
            info.like_count = self._number_from(like_text, cfg.get("like_patterns", []))

        # 粉丝数
        fans_text = self._match_text(tree, cfg.get("follower_patterns", []), cfg.get("follower_region"))
        if fans_text:
            info.follower_count = self._number_from(fans_text, cfg.get("follower_patterns", []))

        # 主播昵称
        info.anchor_name = self._extract_anchor(tree, cfg)

        # 直播间标题
        info.room_title = self._extract_title(tree, cfg, exclude={info.anchor_name or ""})

        # room id：有些版本会把 id 写在 content-desc 里
        rid = self._match_text(tree, cfg.get("room_id_patterns", []))
        if rid:
            info.room_id = first_group(cfg.get("room_id_patterns", []), rid)

        info.extra = {"labels": tree.all_labels()[:60]}
        return info

    def _viewer_node(self, tree: UITree) -> Optional[Node]:
        cfg = self.config.get("live_info", {})
        pats = cfg.get("viewer_patterns", [])
        if not pats:
            return None
        nodes = tree.find_by_pattern(pats, region=cfg.get("viewer_region"))
        if not nodes and cfg.get("viewer_region"):
            nodes = tree.find_by_pattern(pats)
        return nodes[0] if nodes else None

    def _product_entry(self, tree: UITree) -> Optional[Node]:
        cfg = self.config.get("products", {}).get("entry", {})
        pats = list(cfg.get("text_patterns", []))
        if not pats:
            return None
        nodes = tree.find_by_pattern(pats, region=cfg.get("region"))
        if not nodes:
            nodes = tree.find_by_pattern(pats)
        return nodes[0] if nodes else None

    def _extract_anchor(self, tree: UITree, cfg: dict[str, Any]) -> Optional[str]:
        anchor_cfg = cfg.get("anchor", {})
        # 1) 直接正则命中，如 content-desc="主播xxx"
        text = self._match_text(tree, anchor_cfg.get("patterns", []), anchor_cfg.get("region"))
        if text:
            got = first_group(anchor_cfg.get("patterns", []), text)
            if got:
                return got
        # 2) 「关注」按钮左边的那段文字通常就是昵称
        near = anchor_cfg.get("near_text")
        if near:
            hits = tree.find_by_pattern([near], exact=True)
            for hit in hits:
                same_row = [
                    n
                    for n in tree.text_nodes()
                    if n is not hit
                    and abs(n.center[1] - hit.center[1]) <= max(24, hit.height)
                    and n.x1 <= hit.center[0]
                    and len(n.label) >= 2
                ]
                if same_row:
                    same_row.sort(key=lambda n: -n.x1)
                    return same_row[0].label
        # 3) 顶部区域里最长的一段非数字文本
        region = anchor_cfg.get("region") or [0.0, 0.0, 0.7, 0.12]
        cands = [
            n.label
            for n in tree.nodes_in_region(region)
            if len(n.label) >= 2 and not self._is_noise(n.label, cfg.get("noise_patterns", []))
        ]
        return max(cands, key=len) if cands else None

    def _extract_title(self, tree: UITree, cfg: dict[str, Any], exclude: set[str]) -> Optional[str]:
        title_cfg = cfg.get("title", {})
        text = self._match_text(tree, title_cfg.get("patterns", []), title_cfg.get("region"))
        if text:
            return text
        region = title_cfg.get("region") or [0.0, 0.0, 1.0, 0.22]
        min_len = int(title_cfg.get("min_len", 4))
        cands = [
            n.label
            for n in tree.nodes_in_region(region)
            if len(n.label) >= min_len
            and n.label not in exclude
            and not self._is_noise(n.label, cfg.get("noise_patterns", []))
        ]
        return max(cands, key=len) if cands else None

    @staticmethod
    def _is_noise(text: str, noise_patterns: Sequence[str]) -> bool:
        if not text:
            return True
        if re.fullmatch(r"[\d.,:：万亿%\s]+", text):
            return True
        return any(compile_pattern(p).search(text) for p in noise_patterns)

    def _match_text(
        self,
        tree: UITree,
        patterns: Sequence[str],
        region: Optional[Sequence[float]] = None,
    ) -> Optional[str]:
        if not patterns:
            return None
        nodes = tree.find_by_pattern(patterns, region=region)
        if not nodes and region:
            nodes = tree.find_by_pattern(patterns)
        return nodes[0].label if nodes else None

    @staticmethod
    def _number_from(text: str, patterns: Sequence[str]) -> Optional[int]:
        raw = first_group(patterns, text) if patterns else text
        return parse_cn_number(raw or text)

    @staticmethod
    def _target_room_id(target: str) -> Optional[str]:
        m = re.search(r"(\d{10,})", target or "")
        if m:
            return m.group(1)
        m = re.search(r"live\.douyin\.com/(\w+)", target or "")
        return m.group(1) if m else None

    # ── 商品 ──────────────────────────────────────────────────────────
    def collect_products(
        self,
        dev: AndroidDevice,
        tree: UITree,
        *,
        max_scrolls: Optional[int] = None,
    ) -> list[ProductItem]:
        pcfg = self.config.get("products", {})
        if not pcfg:
            return []

        panel_tree = self._open_product_panel(dev, tree)
        if panel_tree is None:
            raise CollectError("找不到商品入口（购物袋/小黄车），可能本场没带货或界面已改版")

        merged: dict[str, ProductItem] = {}
        order: list[str] = []
        limit = max_scrolls if max_scrolls is not None else int(pcfg.get("max_scrolls", 8))

        current = panel_tree
        for round_idx in range(max(1, limit)):
            items = self.extract_products(current)
            new_count = 0
            for item in items:
                k = item.key
                if not k:
                    continue
                if k not in merged:
                    merged[k] = item
                    order.append(k)
                    new_count += 1
                else:
                    merged[k] = self._merge_item(merged[k], item)
            log.debug("商品第 %s 轮：本轮 %s 条，新增 %s 条", round_idx + 1, len(items), new_count)
            if round_idx + 1 >= limit:
                break
            if new_count == 0 and round_idx > 0:
                break
            if not self._scroll_product_list(dev, current):
                break
            time.sleep(float(pcfg.get("scroll_settle_seconds", 1.2)))
            try:
                current = self._safe_tree(dev)
            except Exception as exc:
                log.warning("滚动后取控件树失败: %s", exc)
                break

        for idx, k in enumerate(order, start=1):
            if merged[k].position is None:
                merged[k].position = idx

        self._close_product_panel(dev)
        return [merged[k] for k in order]

    def _merge_item(self, old: ProductItem, new: ProductItem) -> ProductItem:
        for f in ("title", "price", "price_text", "origin_price", "sales_text", "stock_text", "coupon_text", "product_id"):
            if getattr(old, f) in (None, "") and getattr(new, f) not in (None, ""):
                setattr(old, f, getattr(new, f))
        if old.position is None:
            old.position = new.position
        return old

    def _open_product_panel(self, dev: AndroidDevice, tree: UITree) -> Optional[UITree]:
        pcfg = self.config.get("products", {})
        markers = pcfg.get("panel_markers", [])

        if markers and tree.find_by_pattern(markers):
            return tree  # 面板已经开着

        entry_cfg = pcfg.get("entry", {})
        candidates: list[tuple[int, int]] = []

        node = self._product_entry(tree)
        if node is not None:
            candidates.append(node.clickable_self_or_ancestor().center)

        for region in entry_cfg.get("fallback_regions", []):
            box = tree.region_box(region)
            candidates.append(((box[0] + box[2]) // 2, (box[1] + box[3]) // 2))

        for x, y in candidates:
            log.info("点击商品入口 (%s, %s)", x, y)
            dev.tap(x, y)
            time.sleep(float(pcfg.get("panel_open_seconds", 2.5)))
            try:
                new_tree = self._safe_tree(dev)
            except Exception:
                continue
            if not markers or new_tree.find_by_pattern(markers) or self.extract_products(new_tree):
                return new_tree
            # 点错了就退回去
            dev.back()
            time.sleep(1.2)
        return None

    def _close_product_panel(self, dev: AndroidDevice) -> None:
        try:
            dev.back()
            time.sleep(0.8)
        except Exception:
            pass

    def _scroll_product_list(self, dev: AndroidDevice, tree: UITree) -> bool:
        pcfg = self.config.get("products", {})
        region = pcfg.get("list_region", [0.0, 0.35, 1.0, 0.95])
        box = tree.region_box(region)
        scrollers = tree.scrollables(region=region)
        if scrollers:
            s = scrollers[0]
            box = (s.x0, s.y0, s.x1, s.y1)
        cx = (box[0] + box[2]) // 2
        y_from = int(box[1] + (box[3] - box[1]) * 0.78)
        y_to = int(box[1] + (box[3] - box[1]) * 0.28)
        if y_from - y_to < 60:
            return False
        dev.swipe(cx, y_from, cx, y_to, int(pcfg.get("swipe_duration_ms", 500)))
        return True

    def extract_products(self, tree: UITree) -> list[ProductItem]:
        """从控件树里抠商品卡片：以价格文本为锚点，向上找到卡片容器再拆字段。"""
        pcfg = self.config.get("products", {})
        price_patterns = pcfg.get(
            "price_patterns",
            [r"[¥￥]\s?([\d,]+(?:\.\d+)?)", r"([\d,]+(?:\.\d+)?)\s?元"],
        )
        region = pcfg.get("list_region", [0.0, 0.2, 1.0, 1.0])
        min_card_ratio = float(pcfg.get("min_card_width_ratio", 0.35))
        max_climb = int(pcfg.get("max_ancestor_climb", 7))
        title_min_len = int(pcfg.get("title_min_len", 4))
        ignore = pcfg.get("ignore_patterns", [])

        price_nodes = tree.find_by_pattern(price_patterns, region=region)
        cards: list[tuple[Node, Node]] = []  # (card, price_node)
        seen_bounds: set[tuple[int, int, int, int]] = set()

        for pnode in price_nodes:
            card = self._card_for(pnode, tree, min_card_ratio, max_climb, title_min_len, ignore)
            if card is None or card.bounds in seen_bounds:
                continue
            seen_bounds.add(card.bounds)
            cards.append((card, pnode))

        cards.sort(key=lambda c: (c[0].y0, c[0].x0))

        items: list[ProductItem] = []
        for idx, (card, pnode) in enumerate(cards, start=1):
            item = self._card_to_item(card, pnode, pcfg, title_min_len, ignore)
            if item is not None:
                item.position = idx
                items.append(item)
        return items

    def _card_for(
        self,
        pnode: Node,
        tree: UITree,
        min_card_ratio: float,
        max_climb: int,
        title_min_len: int,
        ignore: Sequence[str],
    ) -> Optional[Node]:
        min_width = tree.width * min_card_ratio
        best: Optional[Node] = None
        node: Optional[Node] = pnode
        climbed = 0
        while node is not None and climbed <= max_climb:
            labels = node.labels()
            has_title = any(
                len(lab) >= title_min_len and not self._looks_like_price(lab) and not self._matches(lab, ignore)
                for lab in labels
            )
            if node.width >= min_width and has_title and len(labels) >= 2:
                best = node
                break
            node = node.parent
            climbed += 1
        return best

    def _card_to_item(
        self,
        card: Node,
        pnode: Node,
        pcfg: dict[str, Any],
        title_min_len: int,
        ignore: Sequence[str],
    ) -> Optional[ProductItem]:
        labels = card.labels()
        price_patterns = pcfg.get("price_patterns", [r"[¥￥]\s?([\d,]+(?:\.\d+)?)"])

        price, price_text = parse_price(pnode.label, price_patterns)
        prices: list[float] = []
        for lab in labels:
            val, _ = parse_price(lab, price_patterns)
            if val is not None:
                prices.append(val)
        origin = None
        if price is not None and prices:
            higher = [p for p in prices if p > price]
            origin = max(higher) if higher else None

        sales = first_group(pcfg.get("sales_patterns", []), " ".join(labels)) if pcfg.get("sales_patterns") else None
        stock = first_group(pcfg.get("stock_patterns", []), " ".join(labels)) if pcfg.get("stock_patterns") else None
        coupon = first_group(pcfg.get("coupon_patterns", []), " ".join(labels)) if pcfg.get("coupon_patterns") else None
        pid = first_group(pcfg.get("product_id_patterns", []), " ".join(labels)) if pcfg.get("product_id_patterns") else None

        title_candidates = [
            lab
            for lab in labels
            if len(lab) >= title_min_len
            and not self._looks_like_price(lab)
            and not self._matches(lab, ignore)
            and lab not in {sales, stock, coupon}
        ]
        if not title_candidates:
            return None
        # 商品标题通常是卡片里最长的一段文字
        title = max(title_candidates, key=len)

        return ProductItem(
            title=title,
            price=price,
            price_text=price_text or pnode.label,
            origin_price=origin,
            sales_text=sales,
            stock_text=stock,
            coupon_text=coupon,
            product_id=pid,
            labels=labels[:12],
        )

    @staticmethod
    def _looks_like_price(text: str) -> bool:
        return bool(re.search(r"[¥￥]|^\s*[\d,]+(?:\.\d+)?\s*元?\s*$", text))

    @staticmethod
    def _matches(text: str, patterns: Sequence[str]) -> bool:
        return any(compile_pattern(p).search(text) for p in patterns)

    # ── 弹幕 ──────────────────────────────────────────────────────────
    def extract_comments(self, tree: UITree, limit: int = 50) -> list[str]:
        ccfg = self.config.get("comments", {})
        region = ccfg.get("region", [0.0, 0.55, 0.75, 0.92])
        ignore = ccfg.get("ignore_patterns", [])
        min_len = int(ccfg.get("min_len", 2))
        out: list[str] = []
        for node in tree.nodes_in_region(region):
            lab = node.label
            if len(lab) < min_len or self._matches(lab, ignore):
                continue
            if lab not in out:
                out.append(lab)
        return out[:limit]

    # ── 工具 ──────────────────────────────────────────────────────────
    def _is_current_app(self, dev: AndroidDevice) -> bool:
        if not self.packages:
            return True
        cur = dev.current_package()
        return cur in self.packages if cur else False

    def _safe_tree(self, dev: AndroidDevice, retries: int = 2) -> UITree:
        last: Exception | None = None
        for _ in range(retries + 1):
            try:
                return parse_hierarchy(dev.dump_hierarchy(compressed=True))
            except Exception as exc:
                last = exc
                time.sleep(1.5)
        raise CollectError(f"读取界面失败: {last}")
