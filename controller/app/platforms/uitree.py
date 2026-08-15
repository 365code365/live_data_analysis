"""控件树解析工具。

采集抖音/小红书这类高度混淆的 App，靠 resource-id 定位很快就会失效。
这里的做法是：把 uiautomator 的 XML 解析成带坐标的节点树，
再用「文本正则 + 区域 + 版面聚类」的启发式规则提取信息，
规则全部来自 selectors/*.yaml，改配置即可适配 App 改版。
"""

from __future__ import annotations

import re
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Iterable, Iterator, Optional, Pattern, Sequence

BOUNDS_RE = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")
CN_NUM_RE = re.compile(r"^\s*([\d,]+(?:\.\d+)?)\s*([万亿wWkK]?)")
PRICE_FALLBACK_RE = re.compile(r"[¥￥]\s?([\d,]+(?:\.\d+)?)")

_UNIT = {"": 1, "万": 10_000, "w": 10_000, "W": 10_000, "亿": 100_000_000, "k": 1_000, "K": 1_000}


# ──────────────────────────────────────────────────────────────────────────
@dataclass
class Node:
    cls: str = ""
    text: str = ""
    desc: str = ""
    rid: str = ""
    pkg: str = ""
    bounds: tuple[int, int, int, int] = (0, 0, 0, 0)
    clickable: bool = False
    scrollable: bool = False
    checkable: bool = False
    selected: bool = False
    depth: int = 0
    parent: Optional["Node"] = field(default=None, repr=False)
    children: list["Node"] = field(default_factory=list, repr=False)

    # ── 几何 ──────────────────────────────────────────────────────────
    @property
    def x0(self) -> int:
        return self.bounds[0]

    @property
    def y0(self) -> int:
        return self.bounds[1]

    @property
    def x1(self) -> int:
        return self.bounds[2]

    @property
    def y1(self) -> int:
        return self.bounds[3]

    @property
    def width(self) -> int:
        return max(0, self.x1 - self.x0)

    @property
    def height(self) -> int:
        return max(0, self.y1 - self.y0)

    @property
    def area(self) -> int:
        return self.width * self.height

    @property
    def center(self) -> tuple[int, int]:
        return (self.x0 + self.x1) // 2, (self.y0 + self.y1) // 2

    @property
    def label(self) -> str:
        """节点自身可见文字：text 优先，其次 content-desc。"""
        return (self.text or self.desc or "").strip()

    # ── 遍历 ──────────────────────────────────────────────────────────
    def walk(self) -> Iterator["Node"]:
        yield self
        for child in self.children:
            yield from child.walk()

    def ancestors(self) -> Iterator["Node"]:
        node = self.parent
        while node is not None:
            yield node
            node = node.parent

    def labels(self) -> list[str]:
        """子树内全部可见文字，按版面顺序（先上后左）。"""
        items = [(n.y0, n.x0, n.label) for n in self.walk() if n.label]
        items.sort(key=lambda t: (t[0], t[1]))
        seen: set[str] = set()
        out: list[str] = []
        for _, _, lab in items:
            if lab not in seen:
                seen.add(lab)
                out.append(lab)
        return out

    def clickable_self_or_ancestor(self) -> "Node":
        if self.clickable:
            return self
        for anc in self.ancestors():
            if anc.clickable:
                return anc
        return self

    def __hash__(self) -> int:  # 让 Node 可以进 set（按身份）
        return id(self)


@dataclass
class UITree:
    root: Node
    width: int
    height: int
    raw: str = ""

    # ── 查询 ──────────────────────────────────────────────────────────
    def nodes(self) -> Iterator[Node]:
        return self.root.walk()

    def text_nodes(self) -> list[Node]:
        return [n for n in self.nodes() if n.label]

    def all_labels(self) -> list[str]:
        return self.root.labels()

    def find_by_pattern(
        self,
        patterns: Sequence[str | Pattern[str]],
        *,
        region: Optional[Sequence[float]] = None,
        exact: bool = False,
    ) -> list[Node]:
        pats = [compile_pattern(p) for p in patterns]
        box = self.region_box(region) if region else None
        out = []
        for node in self.text_nodes():
            if box and not self.in_box(node, box):
                continue
            lab = node.label
            for pat in pats:
                if (pat.fullmatch(lab) if exact else pat.search(lab)):
                    out.append(node)
                    break
        return out

    def has_any_text(self, patterns: Sequence[str]) -> bool:
        return bool(self.find_by_pattern(patterns))

    def scrollables(self, *, region: Optional[Sequence[float]] = None) -> list[Node]:
        box = self.region_box(region) if region else None
        out = [n for n in self.nodes() if n.scrollable and n.area > 0]
        if box:
            out = [n for n in out if self.in_box(n, box)]
        return sorted(out, key=lambda n: -n.area)

    # ── 区域 ──────────────────────────────────────────────────────────
    def region_box(self, region: Sequence[float]) -> tuple[int, int, int, int]:
        x0, y0, x1, y1 = region
        return (
            int(x0 * self.width),
            int(y0 * self.height),
            int(x1 * self.width),
            int(y1 * self.height),
        )

    @staticmethod
    def in_box(node: Node, box: tuple[int, int, int, int]) -> bool:
        """节点中心点落在区域内即算命中（比全包含更宽容）。"""
        cx, cy = node.center
        return box[0] <= cx <= box[2] and box[1] <= cy <= box[3]

    def nodes_in_region(self, region: Sequence[float]) -> list[Node]:
        box = self.region_box(region)
        return [n for n in self.text_nodes() if self.in_box(n, box)]


# ──────────────────────────────────────────────────────────────────────────
def parse_hierarchy(xml: str) -> UITree:
    """把 uiautomator XML 解析成节点树。"""
    root_el = ET.fromstring(xml)

    def build(el: ET.Element, parent: Optional[Node], depth: int) -> Node:
        attrs = el.attrib
        node = Node(
            cls=attrs.get("class", ""),
            text=(attrs.get("text") or "").strip(),
            desc=(attrs.get("content-desc") or "").strip(),
            rid=attrs.get("resource-id", ""),
            pkg=attrs.get("package", ""),
            bounds=parse_bounds(attrs.get("bounds", "")),
            clickable=attrs.get("clickable") == "true",
            scrollable=attrs.get("scrollable") == "true",
            checkable=attrs.get("checkable") == "true",
            selected=attrs.get("selected") == "true",
            depth=depth,
            parent=parent,
        )
        for child_el in el:
            node.children.append(build(child_el, node, depth + 1))
        return node

    # <hierarchy> 本身不是控件，取它的第一个子节点当根；没有就用它自己
    if root_el.tag == "hierarchy":
        children = list(root_el)
        if children:
            root = build(children[0], None, 0)
            # 多窗口时把其余窗口也挂上
            for extra in children[1:]:
                extra_node = build(extra, root, 1)
                root.children.append(extra_node)
        else:
            root = Node(cls="hierarchy")
    else:
        root = build(root_el, None, 0)

    w = max((n.x1 for n in root.walk()), default=0) or 1
    h = max((n.y1 for n in root.walk()), default=0) or 1
    return UITree(root=root, width=w, height=h, raw=xml)


def parse_bounds(raw: str) -> tuple[int, int, int, int]:
    m = BOUNDS_RE.search(raw or "")
    if not m:
        return (0, 0, 0, 0)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)))


_pattern_cache: dict[str, Pattern[str]] = {}


def compile_pattern(p: str | Pattern[str]) -> Pattern[str]:
    if isinstance(p, re.Pattern):
        return p
    cached = _pattern_cache.get(p)
    if cached is None:
        cached = re.compile(p)
        _pattern_cache[p] = cached
    return cached


def first_group(patterns: Iterable[str], text: str) -> Optional[str]:
    for pat in patterns:
        m = compile_pattern(pat).search(text)
        if m:
            return (m.group(1) if m.groups() else m.group(0)).strip()
    return None


def parse_cn_number(text: str) -> Optional[int]:
    """把 "1.2万" / "3,456" / "8.9w" 这类中文计数转成整数。"""
    if not text:
        return None
    t = unicodedata.normalize("NFKC", text).strip()
    m = CN_NUM_RE.match(t)
    if not m:
        return None
    try:
        value = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    return int(value * _UNIT.get(m.group(2), 1))


def parse_price(text: str, patterns: Sequence[str] = ()) -> tuple[Optional[float], Optional[str]]:
    """返回 (金额, 原始文本)。"""
    if not text:
        return None, None
    t = unicodedata.normalize("NFKC", text)
    raw = first_group(patterns, t) if patterns else None
    if raw is None:
        m = PRICE_FALLBACK_RE.search(t)
        raw = m.group(1) if m else None
    if raw is None:
        return None, None
    try:
        return float(str(raw).replace(",", "").replace("¥", "").replace("￥", "")), text.strip()
    except ValueError:
        return None, text.strip()


_KEY_STRIP = re.compile(r"[\s\u3000【】\[\]（）()！!，,。.、/\\|~·\-—_:：\"'*#]+")


def normalize_key(title: str) -> str:
    """商品标题归一化成稳定键，用于跨次采集对齐同一个商品。"""
    if not title:
        return ""
    t = unicodedata.normalize("NFKC", title).lower()
    t = _KEY_STRIP.sub("", t)
    return t[:80]
