"""第五章/技术规格书的条款抽取（NORMAL 级要求）。

v0.1.0 完全没解析这一章，于是服务类项目里"24 小时待命""2 小时响应/4 小时到场/
8 小时抢修""月度巡检""季度无人机巡检""检测报告 7 日内""项目经理/专职安全员/
高压电工/高处作业证"这些**实打实的履约义务**一条都没进矩阵 —— 覆盖率 0%。

这里做的是通用抽取（不针对任何具体项目）：

1. 先定位"服务标准及要求 / 技术规格书 / 服务要求 / 技术要求"这一章的页码范围；
2. 在章内按条款编号（``8.1`` / ``1.2`` / ``四、`` 等）切段；
3. 每段产出一条 NORMAL 要求；段内带星号（``*``）的升级为 HARD
   （招标文件通常规定星号条款为实质性条款，负偏离即否决）；
4. 段内出现"人员/证书/持证"等词时归为 PERSONNEL 类型，便于后续挂人员素材。

设计上宁多勿漏：多出来的条款人工一眼能扫掉，漏掉的没人知道。
"""

from __future__ import annotations

import re
from typing import Optional

from ..io.textnorm import SENTENCE_END, TenderText, humanize

#: 章标题候选（按命中优先级）
SPEC_CHAPTER_KEYWORDS = ("服务标准及要求", "技术规格书", "服务要求", "技术要求", "技术规范")

_CHAPTER_NO = re.compile(r"第([一二三四五六七八九十]+)章")
#: 中文数字标题（"三、服务范围"）。
#: 注意：这些正则是跑在**已扁平化**的文本上的，那里的"、"已经变成","，
#: 所以模式里必须同时接受两种写法——否则一个标题都认不出来（踩过）。
_CN_HEADING = re.compile(r"(?:^|[^0-9A-Za-z])([一二三四五六七八九十]{1,3})[、,]")
#: 条款编号：一级 1~15、二级 1~30；且后面不能接计量单位。
#: 不能写成任意两位数字——"23.9公里""1.12"这类**数值**会被误当成条款号，
#: 正文从此处被切碎（实测切出一条以"…共有铁塔77基…"开头的垃圾要求）。
_UNIT_TAIL = r"(?![公里米千伏基次条台套支座组相回])"
_AR_CLAUSE = re.compile(r"(?:[1-9]|1[0-5])\.\d{1,2}(?=[\u4e00-\u9fa5])" + _UNIT_TAIL)
_AR_TOP = re.compile(r"(?<![\d.])(?:[1-9]|1[0-5])\.(?=[\u4e00-\u9fa5])" + _UNIT_TAIL)

#: 星号实质性条款自成一段（否则会黏在上一条末尾，把普通条款误升级为 HARD）。
#: 必须排除型号里的星号——"2*JL/G1A""1*500型电力电缆"都是乘号写法，不是实质性条款标记。
_STAR_BOUNDARY = re.compile(r"(?<![0-9A-Za-z/*])\*(?=[\u4e00-\u9fa5])")

_STAR_RE = re.compile(r"(?<![0-9A-Za-z/*])\*[\u4e00-\u9fa5][^\s]{3,60}")
_HARD_HINTS = ("取消投标资格", "否决其投标", "否决投标", "投标将被否决", "无效投标")

#: 人员类信号词。刻意**不含**"资格""证书"这类泛词——"取消投标资格""资格证书"
#: 都会命中，实测把"服务内容缺项即取消资格"的星号条款误判成了人员类要求。
_PERSONNEL_HINTS = ("人员", "持证", "作业证", "电工", "职称", "安全员", "项目经理", "拟派")
_EVIDENCE_HINTS = (
    ("检测报告", "检测报告"), ("试验报告", "试验报告"), ("巡检报告", "巡检报告"),
    ("方案", "实施方案"), ("台账", "运维台账"), ("记录", "服务记录"),
)


def find_spec_chapter(text: TenderText, *, min_page: int = 4) -> Optional[tuple[int, int, str]]:
    """定位服务标准/技术规格章节，返回 (起始页, 结束页, 章标题)。"""
    starts: list[tuple[int, str]] = []
    for page in text.all_pages():
        if page.page <= min_page:      # 跳过封面与目录
            continue
        head = page.flat[:60]
        m = _CHAPTER_NO.search(head)
        if not m:
            continue
        title = head[m.start():m.start() + 30]
        if any(k in title for k in SPEC_CHAPTER_KEYWORDS):
            starts.append((page.page, title))
    if not starts:
        return None
    start_page, title = starts[0]

    end_page = len(text)
    for page in text.all_pages():
        if page.page <= start_page:
            continue
        head = page.flat[:40]
        if _CHAPTER_NO.search(head) and not any(k in head for k in SPEC_CHAPTER_KEYWORDS):
            end_page = page.page - 1
            break
    return start_page, end_page, title


class _ChapterText:
    """章范围内文本 + 逐字符页码/块映射（跨页取证用）。"""

    def __init__(self, text: TenderText, start: int, end: int):
        self.flat = ""
        self.page_at: list[int] = []
        self.off_at: list[int] = []
        self.pages = {}
        for p in text.all_pages():
            if start <= p.page <= end:
                # PDF 文本层把页码印在页首，拼成连续正文时它会插进句子中间
                # （实测得到"…安全目标和指标122不发生人身轻伤…"）。逐页剥掉页首数字。
                stripped = re.sub(r"^\d{1,3}", "", p.flat)
                shift = len(p.flat) - len(stripped)
                self.flat += stripped
                self.page_at.extend([p.page] * len(stripped))
                self.off_at.extend(range(shift, shift + len(stripped)))
                self.pages[p.page] = p

    def quote(self, start: int, end: int, *, context: int = 0) -> str:
        if end <= start:
            return ""
        page_no = self.page_at[start]
        page = self.pages[page_no]
        s = self.off_at[start]
        e = self.off_at[min(end, len(self.flat)) - 1] + 1
        hit = page.hit_from_flat_span(s, e)
        raw = page.raw[hit.raw_start:hit.raw_end]
        return humanize(re.sub(r"\s+", " ", raw)).strip()


def _segments(chapter: _ChapterText) -> list[tuple[int, int]]:
    """按条款编号切段，返回 [(start, end)]。"""
    flat = chapter.flat
    bounds: set[int] = {0}
    for pattern in (_AR_CLAUSE, _AR_TOP):
        for m in pattern.finditer(flat):
            bounds.add(m.start())
    for m in _CN_HEADING.finditer(flat):
        bounds.add(m.start(1))
    for m in _STAR_BOUNDARY.finditer(flat):
        bounds.add(m.start())
    ordered = sorted(b for b in bounds if 0 <= b < len(flat))

    raw = [(s, ordered[i + 1] if i + 1 < len(ordered) else len(flat))
           for i, s in enumerate(ordered)]

    # 合并"只有标题没有正文"的短段（如"九、维保考核内容"）到下一段：
    # 光一条标题不构成可响应的要求，标题+首条内容才是人读到的完整义务。
    merged: list[tuple[int, int]] = []
    for s, e in raw:
        if merged and (merged[-1][1] - merged[-1][0]) < 25:
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))
    return [(s, e) for s, e in merged if e - s >= 10]


def extract_spec_requirements(text: TenderText, *, min_page: int = 4) -> tuple[list[dict], list[str]]:
    """返回 (要求列表, 告警列表)。页码为 1-based PDF 实际页。"""
    found = find_spec_chapter(text, min_page=min_page)
    if not found:
        return [], ["未定位到服务标准/技术规格章节，第五章要求未抽取"]
    start, end, title = found
    chapter = _ChapterText(text, start, end)

    items: list[dict] = []
    seen: set[str] = set()
    for n, (s, e) in enumerate(_segments(chapter), start=1):
        body = chapter.flat[s:e].strip(" " + SENTENCE_END)
        if len(body) < 12:
            continue
        key = re.sub(r"\s+", "", body)[:80]
        if key in seen:
            continue
        seen.add(key)

        stars = _STAR_RE.findall(body)
        hard = bool(stars) or any(h in body for h in _HARD_HINTS)
        # 类型只看**本条各自的正文**：星号实质性条款常常被并入相邻段，
        # 用合并后的整段判断类型会被邻段语义带偏（实测把"服务内容缺项"判成了人员类）。
        nature_text = stars[0] if stars else body
        req_type = "PERSONNEL" if any(h in nature_text for h in _PERSONNEL_HINTS) else "TECHNICAL"
        evidence = [label for kw, label in _EVIDENCE_HINTS if kw in body][:3]

        items.append({
            "id": f"SPEC-{start + n:03d}",
            "chapter": title,
            "source_text": humanize(body)[:600],
            "source_page": chapter.page_at[s],
            "source_section": f"第{start}-{end}页 条款段 {n}",
            "source_quote": chapter.quote(s, min(e, s + 200)),
            "req_type": req_type,
            "severity": "HARD" if hard else "NORMAL",
            "evidence_required": evidence,
            "condition": {"rule": "SPEC_CLAUSE",
                          "star_clause": bool(stars),
                          "star_text": stars[0] if stars else None},
            "condition_text": (humanize(f"实质性条款（星号）：{stars[0]}") if stars
                               else "服务/技术规格条款，须在服务方案与偏差表中响应"),
            "response_location": "八、服务方案 / 五、商务和技术偏差表",
            "note": ("含星号实质性条款，负偏离即否决" if hard else None),
        })
    return items, []


def chapter_page_range(text: TenderText, *, min_page: int = 4) -> Optional[tuple[int, int]]:
    found = find_spec_chapter(text, min_page=min_page)
    return (found[0], found[1]) if found else None
