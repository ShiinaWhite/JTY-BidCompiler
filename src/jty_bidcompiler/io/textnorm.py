"""中文标书文本的规整与反查。

招标 PDF 的文本层有几个固定毛病，所有下游解析都必须先过这一层：

* 数字前后夹空格：``2026 年09 月18 日``、``10KV 及以上``、``2 万元``；
* 中英文标点混用：``（``/``(``、``：``/``:``；
* 上下标被拆到独立行、页码与页眉混进正文。

因此这里提供 :class:`PageText`：保留原始字符用于**取证**，同时提供去空格的
扁平串用于**检索**，并维护扁平串下标 → 原始串下标的映射，保证命中的任意片段
都能还原为原文（证据链要求原文引用，不允许只有规范化后的样子）。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable, Iterator, Sequence

_WS = re.compile(r"\s+")

#: 句末句号在扁平串里的替身。
#:
#: 为什么不用 "."：招标文件里 "2.4 服务地点" 这种**条款编号**的小数点也是 "."，
#: 一旦把中文句号也折叠成 "."，`服务期：三年。2.4 服务地点：…` 就会被切成
#: "三年2"（真实踩过的坑）。用一个不会出现在正文里的标记字符区分句末与小数点，
#: 模式里继续写 "。"（fold 时同样转成这个标记）即可。
SENTENCE_END = "\x1f"

# 只在检索时折叠的等价字符；取证一律回原文
_PUNCT_EQ = {
    "（": "(", "）": ")", "，": ",", "。": SENTENCE_END, "：": ":", "；": ";",
    "、": ",", "“": '"', "”": '"', "‘": "'", "’": "'", "－": "-", "—": "-",
    "～": "~", "％": "%", "／": "/",
}


def fold(text: str) -> str:
    """生成用于检索的扁平串：去所有空白 + 标点等价折叠 + 全角转半角（仅字母数字）。"""
    out = []
    for ch in text:
        if ch.isspace():
            continue
        ch = _PUNCT_EQ.get(ch, ch)
        if ord(ch) > 0x2000 and unicodedata.category(ch).startswith("L"):
            pass  # 中文汉字保持原样
        out.append(ch)
    s = "".join(out)
    # 全角字母数字 → 半角（平台项目编号之类常被 OCR 成全角）
    s = "".join(
        chr(ord(c) - 0xFEE0) if 0xFF10 <= ord(c) <= 0xFF5A else c for c in s
    )
    return s


@dataclass
class Hit:
    """一次命中：扁平串区间 + 还原出的原文区间。"""

    start: int          # 扁平串起点
    end: int            # 扁平串终点（不含）
    raw_start: int
    raw_end: int
    text: str           # 命中的**原文**（非规范化）

    def __str__(self) -> str:  # pragma: no cover - 便于调试
        return self.text


@dataclass
class PageText:
    """单页文本 + 扁平检索视图。"""

    page: int                       # 1-based 页码（= PDF 实际页）
    raw: str
    _flat: str = field(init=False, repr=False)
    _map: list[int] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        flat_chars: list[str] = []
        mapping: list[int] = []
        for i, ch in enumerate(self.raw):
            if ch.isspace():
                continue
            mapped = _PUNCT_EQ.get(ch, ch)
            if 0xFF10 <= ord(mapped) <= 0xFF5A:
                mapped = chr(ord(mapped) - 0xFEE0)
            flat_chars.append(mapped)
            mapping.append(i)
        self._flat = "".join(flat_chars)
        self._map = mapping

    @property
    def flat(self) -> str:
        return self._flat

    def find(self, needle: str, start: int = 0) -> Hit | None:
        """在扁平视图中检索 *needle*（needle 也会被折叠），返回可还原原文的命中。"""
        n = fold(needle)
        if not n:
            return None
        idx = self._flat.find(n, start)
        if idx < 0:
            return None
        return self._hit(idx, idx + len(n))

    def find_all(self, needle: str) -> Iterator[Hit]:
        n = fold(needle)
        if not n:
            return
        pos = 0
        while True:
            idx = self._flat.find(n, pos)
            if idx < 0:
                return
            yield self._hit(idx, idx + len(n))
            pos = idx + 1

    def search(self, pattern: str, flags: int = 0) -> Hit | None:
        """正则检索（在扁平串上跑，故模式里不要写空白）。"""
        m = re.search(pattern, self._flat, flags)
        if not m:
            return None
        return self._hit(m.start(), m.end())

    def search_all(self, pattern: str, flags: int = 0) -> Iterator[Hit]:
        for m in re.finditer(pattern, self._flat, flags):
            yield self._hit(m.start(), m.end())

    def context(self, hit: Hit, before: int = 40, after: int = 60) -> str:
        """给出命中处的原文上下文（证据展示用，按原始字符切）。"""
        s = max(0, hit.raw_start - before)
        e = min(len(self.raw), hit.raw_end + after)
        return _WS.sub(" ", self.raw[s:e]).strip()

    def line_of(self, hit: Hit) -> str:
        """命中所在的整行原文。"""
        s = self.raw.rfind("\n", 0, hit.raw_start) + 1
        e = self.raw.find("\n", hit.raw_end)
        if e < 0:
            e = len(self.raw)
        return _WS.sub(" ", self.raw[s:e]).strip()

    def hit_from_flat_span(self, flat_start: int, flat_end: int) -> Hit:
        """把扁平串上的区间还原成带原文的 :class:`Hit`（公开入口）。"""
        return self._hit(flat_start, flat_end)

    def _hit(self, flat_start: int, flat_end: int) -> Hit:
        raw_start = self._map[flat_start]
        raw_end = self._map[flat_end - 1] + 1
        return Hit(flat_start, flat_end, raw_start, raw_end, self.raw[raw_start:raw_end])

    # 便于调试打印
    def __repr__(self) -> str:  # pragma: no cover
        return f"<PageText p{self.page} {len(self.raw)}ch>"


class TenderText:
    """整份招标文件的文本视图，按页保留。"""

    def __init__(self, pages: Sequence[PageText]):
        self.pages: list[PageText] = list(pages)

    def __len__(self) -> int:
        return len(self.pages)

    def __iter__(self) -> Iterator[PageText]:
        return iter(self.pages)

    def page(self, n: int) -> PageText:
        """按 1-based 页码取页。"""
        return self.pages[n - 1]

    def all_pages(self) -> Iterable[PageText]:
        return self.pages

    def search(self, pattern: str, flags: int = 0) -> Iterator[tuple[PageText, Hit]]:
        """跨页正则检索，按页序产出 (页, 命中)。"""
        for p in self.pages:
            for hit in p.search_all(pattern, flags):
                yield p, hit

    def find_first(self, needle: str) -> tuple[PageText, Hit] | None:
        for p in self.pages:
            hit = p.find(needle)
            if hit:
                return p, hit
        return None

    def page_text(self, n: int) -> str:
        return self.pages[n - 1].raw


def humanize(text: str) -> str:
    """把扁平串里的句末标记还原成中文句号，供人阅读/写盘使用。"""
    return (text or "").replace(SENTENCE_END, "。")


_ILLEGAL_XLSX = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")


def excel_safe(text: str) -> str:
    """去掉 openpyxl 不接受的非法控制字符（保留制表符与换行）。"""
    return _ILLEGAL_XLSX.sub("", humanize(text or ""))
