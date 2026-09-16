"""MVP-2：招标文件 → requirements.json（投标要求矩阵）。

抽取对象分四块，全部**锚定原文**，不做语义推断：

1. 第一章「3.投标人资格要求」逐条（3.1~3.4）——资格硬门槛；
2. 第二章「1.4.3 投标人不得存在下列情形之一」逐项（否决项，含信誉三连）；
3. 第三章「2.2.4 评分标准」逐项（商务 10 分 / 技术 50 分 / 报价 40 分）；
4. 关键商务与格式要求（保证金、有效期、递交、签章加密、投标文件组成、实质性条款）。

每条要求都带：原文（逐字）、页码、章节、类型、重要性、需提供证据类别、应放置位置。
**status 一律写 NOT_EVALUATED**——判定权在 matchers，解析器不做结论。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field
from datetime import datetime
from typing import Iterable, Optional

from ..io.textnorm import SENTENCE_END, PageText, TenderText, fold, humanize
from .qualification_classifier import classify_qualification
from .tender_spec import extract_spec_requirements

import hashlib
import json
from pathlib import Path

SCHEMA_VERSION = "1.0"

# 评分项名称前缀：招商文件里 "2.2.4(2)商务评分标准业绩(5分)" 这类连写需要剥前缀
_SCORE_PREFIXES = (
    "2.2.4(1)", "2.2.4(2)", "2.2.4(3)", "2.2.4(4)", "2.2.4",
    "其他因素评分标准", "投标报价评分标准", "商务评分标准", "技术评分标准",
    "评分标准", "评分因素", "条款号",
)

# 目录清单（用于给评分项定类型/证据/落位；未列入的评分项按 OTHER 处理）
_SCORE_CATALOG = {
    "业绩": dict(
        rid="SCORE-BIZ-PERF", block="商务", points=5, req_type="PERFORMANCE",
        evidence=["业绩合同"], location="七（三）业绩",
        # 只标记"这是一条业绩评分项"：**是否引用资格口径、每项几分、上限几项
        # 都必须由条款原文决定**。历史上这里写了项目 A 的计分规则当默认值，
        # 换个项目就会把别人的口径当成本项目的口径（见 _performance_scoring_condition）。
        condition={"rule": "PERFORMANCE_FIVE_ELEMENTS", "scoring_candidate": True},
    ),
    "企业综合实力及财务状况": dict(
        rid="SCORE-BIZ-FIN", block="商务", points=2, req_type="FINANCIAL",
        evidence=["财务审计报告"], location="七（二）投标人财务状况",
        condition={"rule": "FINANCIAL_YEARS", "years": [2023, 2024, 2025]},
    ),
    "主要商务条款响应程度": dict(
        rid="SCORE-BIZ-TERMS", block="商务", points=2, req_type="COMMERCIAL",
        evidence=[], location="五、商务和技术偏差表",
        condition={"rule": "TERMS_RESPONSIVENESS"},
    ),
    "投标文件编制质量": dict(
        rid="SCORE-BIZ-QUALITY", block="商务", points=1, req_type="FORMAT",
        evidence=["业绩信息统计表"], location="全册格式",
        condition={"rule": "FORMAT_QUALITY",
                   "note": "含评标办法索引；业绩信息统计表须另附可编辑版且内容一致"},
    ),
    "服务范围,服务内容,服务依据,服务工作目标": dict(
        rid="SCORE-TECH-SCOPE", block="技术", points=5, req_type="TECHNICAL",
        evidence=[], location="八、服务方案", condition=None,
    ),
    "服务机构设置和岗位职责": dict(
        rid="SCORE-TECH-ORG", block="技术", points=5, req_type="PERSONNEL",
        evidence=["人员证书"], location="八、服务方案 / 七（六）项目团队人员组成表",
        condition={"rule": "PERSONNEL_CERTIFIED", "note": "特殊工种人员须有相应资格证书"},
    ),
    "服务方案": dict(
        rid="SCORE-TECH-PLAN", block="技术", points=20, req_type="TECHNICAL",
        evidence=[], location="八、服务方案", condition=None,
    ),
    "安全环保管理措施": dict(
        rid="SCORE-TECH-HSE", block="技术", points=5, req_type="TECHNICAL",
        evidence=[], location="八、服务方案", condition=None,
    ),
    "质量管理措施": dict(
        rid="SCORE-TECH-QA", block="技术", points=5, req_type="TECHNICAL",
        evidence=[], location="八、服务方案", condition=None,
    ),
    "检修机械工具": dict(
        rid="SCORE-TECH-TOOLS", block="技术", points=5, req_type="TECHNICAL",
        evidence=["设备机具"], location="八、服务方案",
        condition={"rule": "EQUIPMENT_LEDGER",
                   "note": "方案所列机械工具须有真实设备台账/检定证明支撑"},
    ),
    "售后服务保证措施": dict(
        rid="SCORE-TECH-AFTERSALE", block="技术", points=5, req_type="TECHNICAL",
        evidence=[], location="十、售后服务和质保期服务计划", condition=None,
    ),
}

_COMMERCIAL_SPECS: tuple[dict, ...] = (
    dict(
        rid="COMM-1", anchor=r"投标保证金的金额[:：]?人民币([\d.]+)万元",
        chapter="第二章 投标人须知前附表 3.4.1", req_type="COMMERCIAL", severity="HARD",
        response_location="四、投标保证金",
        evidence_required=["保证金凭证"],
        condition={"rule": "BID_SECURITY_PAID", "amount_cny": None, "form": "电汇"},
        condition_text="按招标要求缴纳投标保证金并提供缴纳凭证扫描件",
    ),
    dict(
        rid="COMM-2", anchor=r"投标有效期[:：]?(\d{1,3})日",
        chapter="第二章 投标人须知前附表 3.3.1", req_type="COMMERCIAL", severity="HARD",
        response_location="一、投标函",
        evidence_required=[],
        condition={"rule": "BID_VALIDITY_DAYS", "days": None},
        condition_text="投标有效期须满足招标文件规定的天数（实质性商务要求）",
    ),
    dict(
        rid="COMM-3", anchor=r"投标文件应于(\d{4}年\d{1,2}月\d{1,2}日\d{1,2}[:：]\d{2})之前递交到",
        chapter="第一章 招标公告 5.", req_type="COMMERCIAL", severity="HARD",
        response_location="平台递交",
        evidence_required=[],
        condition={"rule": "SUBMIT_BEFORE_DEADLINE"},
        condition_text="在投标截止时间前通过招标平台成功递交加密电子投标文件",
    ),
    dict(
        rid="COMM-4", anchor=r"(投标文件全部采用电子文档[^。]{0,60}。)",
        chapter="第二章 投标人须知前附表 3.7.3", req_type="FORMAT", severity="HARD",
        response_location="全册",
        evidence_required=[],
        condition={"rule": "SCAN_COPIES"},
        condition_text="投标文件全部电子文档，所附证书证件均为扫描件",
    ),
    dict(
        rid="COMM-5", anchor=r"(须在投标文件使用CA数字证书加盖投标单位电子印章[^。]{0,40}。)",
        chapter="第二章 投标人须知前附表 3.7.3", req_type="FORMAT", severity="HARD",
        response_location="全册签章位",
        evidence_required=[],
        condition={"rule": "ESIGN_REQUIRED"},
        condition_text="使用 CA 数字证书加盖投标单位电子印章",
    ),
    dict(
        rid="COMM-6", anchor=r"(技术实质性响应[^。]{0,120}。)",
        chapter="第三章 2.1.3 响应性评审标准", req_type="TECHNICAL", severity="HARD",
        response_location="五、商务和技术偏差表 + 八/九/十技术章节",
        evidence_required=[],
        condition={"rule": "NO_NEGATIVE_DEVIATION"},
        condition_text="带星号（*）条款属实质性条款，不允许负偏离，否则否决投标",
    ),
    dict(
        rid="COMM-7", anchor=r"(需提供经审计的财务报告扫描件[^。]{0,80}。)",
        chapter="第三章 2.2.4(2) 商务评分标准", req_type="FINANCIAL", severity="SCORE",
        response_location="七（二）投标人财务状况",
        evidence_required=["财务审计报告"],
        condition={"rule": "FINANCIAL_YEARS", "years": [2023, 2024, 2025]},
        condition_text="提供经审计的财务报告扫描件（含财务报表）",
    ),
    dict(
        rid="COMM-9", anchor=r"分包(?:[^。]{0,4})?(不允许|允许)",
        chapter="第二章 投标人须知前附表 1.10.1", req_type="COMMERCIAL", severity="HARD",
        response_location="八、服务方案（分包说明）+ 偏差表",
        evidence_required=[],
        condition={"rule": "NO_SUBCONTRACTING"},
        condition_text="分包要求（不允许分包时以承诺方式响应）",
    ),
    dict(
        rid="COMM-10", anchor=r"投标报价缺漏项的要求[^。]{0,160}",
        chapter="第三章 3.1.3 投标报价缺漏项的要求", req_type="COMMERCIAL", severity="HARD",
        response_location="二、报价表（分项报价完整性）",
        evidence_required=[],
        condition={"rule": "PRICE_COMPLETENESS"},
        condition_text="缺漏项达到投标总价一定比例的将被否决，报价须完整",
    ),
    # 注：v0.1.0 曾用固定锚点抽"特殊工种人员具有相应资格证书"。该句其实是某个项目的
    # "服务机构设置和岗位职责"评分项标准里的一句，现已由通用评分项抽取覆盖；
    # 换项目后措辞完全不同（如"人员配备…高压电工证"），固定锚点只会制造
    # "未命中锚点"噪音，故移除。
)


@dataclass
class _Block:
    page: PageText
    flat: str

    def flat_span_quote(self, start: int, end: int) -> str:
        hit = self.page.hit_from_flat_span(start, end)
        return re.sub(r"\s+", " ", self.page.raw[hit.raw_start:hit.raw_end]).strip()

    def line_quote(self, start: int, end: int) -> str:
        """取命中所在整行（更适合做'原文引用'，人能看懂上下文）。"""
        hit = self.page.hit_from_flat_span(start, end)
        line = self.page.line_of(hit)
        return line or self.flat_span_quote(start, end)


def _blocks(text: TenderText) -> list[_Block]:
    return [_Block(page=p, flat=p.flat) for p in text.all_pages()]


def _find_block(blocks: list[_Block], needle: str) -> tuple[int, int] | None:
    """返回 (block_index, flat_index)。"""
    target = fold(needle)
    for i, b in enumerate(blocks):
        idx = b.flat.find(target)
        if idx >= 0:
            return i, idx
    return None


def _cut(text: str, limit: int = 400) -> str:
    text = humanize(text).strip()
    return text if len(text) <= limit else text[:limit] + "…"


_PAGE_FOOTER = re.compile(r"[；;." + SENTENCE_END + r"]?\s*\d{1,3}\s*$")


def _strip_page_footer_digits(body: str) -> str:
    """去掉句尾混进来的页码（PDF 文本层会把页脚数字插进正文流）。"""
    if re.search(r"[；;." + SENTENCE_END + r"]\s*\d{1,3}\s*$", body):
        return _PAGE_FOOTER.sub("", body)
    return body


# --------------------------------------------------------------------------- #
# 1. 资格要求 3.x
# --------------------------------------------------------------------------- #

def _extract_qualification_items(blocks: list[_Block], *, pages: int = 3) -> list[dict]:
    """抽取「3.投标人资格要求」下的 3.1 / 3.2 / … 条款。

    **必须跨页**：示例项目的 3.2 与 3.3 就跨越了页边界（3.2 的"提供有效期内的
    安全生产许可证"落在下一页，3.3 整条在下一页）。v0.1.0 只看命中页，直接漏掉了
    3.3（资格业绩）—— 这是 HARD recall 的最大单点失败。

    跨页用逐字符页码映射还原，保证每条条款的页码与原文引用都落在正确的一页。
    """
    found = _find_block(blocks, "3.投标人资格要求")
    if not found:
        return []
    bi, idx = found

    combined = ""
    page_at: list[int] = []
    off_at: list[int] = []
    block_at: list[_Block] = []
    for b in blocks[bi:bi + pages]:
        combined += b.flat
        page_at.extend([b.page.page] * len(b.flat))
        off_at.extend(range(len(b.flat)))
        block_at.extend([b] * len(b.flat))
    combined, page_at, off_at, block_at = (
        combined[idx:], page_at[idx:], off_at[idx:], block_at[idx:]
    )

    starts = list(re.finditer(r"3\.([1-9])(?![\d.])", combined))
    if not starts or starts[0].group(1) != "1":
        return []

    note = combined.find("备注")
    end_all = note if note > 0 else len(combined)

    items: list[dict] = []
    for n, m in enumerate(starts):
        s = m.start()
        if s >= end_all:
            break
        e = starts[n + 1].start() if n + 1 < len(starts) else end_all
        e = min(e, end_all)
        no = m.group(1)
        body = combined[s:e].strip().rstrip("；;." + SENTENCE_END)
        # 原文还原：条款可能跨页（示例项目 3.2 就跨页）。off_at 是"页内偏移"，
        # 跨页后直接相加会越界或错位（实测会得到空引用），因此先夹回起始页，
        # 再把续页正文补在后面，保证 source_text 是完整一条。
        block = block_at[s]
        last = min(e, len(combined)) - 1
        while last > s and page_at[last] != page_at[s]:
            last -= 1
        hit = block.page.hit_from_flat_span(off_at[s], off_at[last] + 1)
        quote = re.sub(r"\s+", " ", block.page.raw[hit.raw_start:hit.raw_end]).strip()
        if last + 1 < min(e, len(combined)):
            nxt = block_at[last + 1]
            cont_end = min(e, len(combined)) - 1
            # 续页补充只在"确实还是下一段"时做：页码不连续（招标文件只提供了部分页）
            # 或偏移越界时，宁可不拼续文，也不让解析崩在索引上。
            try:
                cont = nxt.page.hit_from_flat_span(off_at[last + 1], off_at[cont_end] + 1)
            except (IndexError, ValueError):
                cont = None
            if cont is not None:
                tail = re.sub(r"\s+", " ", nxt.page.raw[cont.raw_start:cont.raw_end]).strip()
                if tail:
                    quote = f"{quote} {tail}"
        items.append({
            "id": f"QUAL-3-{no}",
            "chapter": "第一章 招标公告",
            "source_text": _cut(quote, 600),
            "flat_body": body,
            "source_page": page_at[s],
            "source_section": f"第一章 3.{no}",
            "source_quote": _cut(quote, 300),
            "no": no,
        })
    return items


# --------------------------------------------------------------------------- #
# 2. 1.4.3 否决情形
# --------------------------------------------------------------------------- #

#: 1.4.3 各情形的分类（编号 → (类型, 是否需要信誉类证据)）
#: 1.4.3 各项的**内容特征**：出现这些词的是"需要信用/信誉材料自证"的不良记录
_CREDIT_HINTS = ("信用", "失信", "惩戒", "骗取中标", "严重违约", "重大工程质量",
                 "不良记录", "黑名单")
#: 主体资格状态类（属于资格判断，但不是信用查询能证明的）
_STATUS_HINTS = ("暂停", "取消投标资格", "吊销", "责令停业", "清算", "破产", "履约能力")


def _forbidden_class(body: str) -> tuple[str, bool]:
    """1.4.3 各项的类型与"是否需要信用证据"由**内容**判断，不按项号写死。

    1.4.3 的条目数与顺序随平台而异（示例项目是 16 条，其他项目可能 6 条或换顺序）；
    按第 12～15 项钉死，换一份文件就会把"失信名单"判成承诺类（不去查证据），
    或把"关联关系"判成信用类（去要一份查不到的查询结果）。
    """
    if any(h in body for h in _CREDIT_HINTS):
        return "CREDIT", True
    if any(h in body for h in _STATUS_HINTS):
        return "QUALIFICATION", False
    return "OTHER", False


def _extract_forbidden_situations(blocks: list[_Block]) -> list[dict]:
    """抽取 1.4.3「投标人不得存在下列情形之一」的逐项内容。

    该清单允许条款内部出现括号（如「（单位）」「（www.gsxt.gov.cn）」），
    所以不能用"括号配对"切分；这里改成：找出所有形如 ``(N)`` 的编号，
    只保留从 1 开始**严格递增**的那一串，再按相邻编号切片。
    跨页用逐字符页码映射还原，保证 source_page 与引用都落在正确的一页。
    """
    found = _find_block(blocks, "投标人不得存在下列情形之一")
    if not found:
        return []
    bi, idx = found

    combined = ""
    page_at: list[int] = []       # 该字符属于哪一页
    off_at: list[int] = []        # 该字符在**本页 flat 串**中的下标（用于还原原文）
    block_at: list[_Block] = []
    for b in blocks[bi:bi + 2]:          # 该清单最多跨两页
        combined += b.flat
        page_at.extend([b.page.page] * len(b.flat))
        off_at.extend(range(len(b.flat)))
        block_at.extend([b] * len(b.flat))
    combined = combined[idx:]
    page_at = page_at[idx:]
    off_at = off_at[idx:]
    block_at = block_at[idx:]

    marks = [(m.start(), m.end(), int(m.group(1)))
             for m in re.finditer(r"\((\d{1,2})\)", combined)]
    seq: list[tuple[int, int, int]] = []
    expected = 1
    for mark in marks:
        if mark[2] == expected:
            seq.append(mark)
            expected += 1
    if len(seq) < 5:
        return []

    # 该清单之后紧接的下一节标题（用于给最后一项封口）
    _NEXT_SECTION = re.compile(r"\d\.\d[\u4e00-\u9fa5]")

    items: list[dict] = []
    for i, (s, e, no) in enumerate(seq):
        end = seq[i + 1][0] if i + 1 < len(seq) else min(len(combined), s + 260)
        body = combined[e:end].strip().rstrip("；;")
        if i + 1 == len(seq):
            nxt = _NEXT_SECTION.search(body)
            if nxt and nxt.start() > 0:
                body = body[: nxt.start()].rstrip("；;.")
        body = _strip_page_footer_digits(body).strip().rstrip("；;")
        if len(body) < 6:
            continue
        req_type, needs_credit = _forbidden_class(body)
        last = min(end, len(combined)) - 1
        block = block_at[s]
        quote_hit = block.page.hit_from_flat_span(off_at[s], off_at[last] + 1)
        quote = re.sub(r"\s+", " ", block.page.raw[quote_hit.raw_start:quote_hit.raw_end]).strip()
        items.append({
            "id": f"QUAL-143-{no}",
            "chapter": "第二章 投标人须知 1.4.3",
            "source_text": _cut(body, 400),
            "source_page": page_at[s],
            "source_section": "第二章 1.4.3",
            "source_quote": _cut(quote, 300),
            "req_type": req_type,
            "needs_credit_evidence": needs_credit,
            "no": no,
        })
    return items


# --------------------------------------------------------------------------- #
# 3. 评分项
# --------------------------------------------------------------------------- #

def _trim_score_prefix(name: str) -> str:
    changed = True
    while changed:
        changed = False
        for prefix in _SCORE_PREFIXES:
            p = fold(prefix)
            if name.startswith(p) and len(name) > len(p):
                name = name[len(p):]
                changed = True
    return name


_BLOCK_BY_MARKER = {"1": "报价", "2": "商务", "3": "技术", "4": "其他"}
_BLOCK_CODE = {"报价": "PRICE", "商务": "BIZ", "技术": "TECH", "其他": "OTHER"}
_BLOCK_MARKER = re.compile(r"2\.2\.4\((\d)\)")
_ITEM_POINTS = re.compile(r"\((\d{1,2})分\)")


def _extract_scoring_items(blocks: list[_Block]) -> list[dict]:
    """通用评分项抽取。

    v0.1.0 依赖一份**项目专属的名称目录**（"服务方案""检修机械工具"…），
    换成另一个项目（"维保方案""维保制度流程""人员配备"…）就只认出 4 条商务项。
    现在改为：凡形如「名称（N分）」的条目全部收下，用最近的 ``2.2.4(N)`` 标记
    判断它属于报价/商务/技术；名称命中已知语义表时才附加证据与落位要求，
    未命中的照收不误（宁多勿漏——多出来的条目由人工一眼扫掉，漏掉的没人知道）。
    """
    items: list[dict] = []
    seen: set[str] = set()
    current_block = "技术"

    for bi, block in enumerate(blocks):
        prev_end = 0          # 每页重置：否则上一页的偏移会把本页首个条目切空
        markers = [(m.start(), _BLOCK_BY_MARKER.get(m.group(1), current_block))
                   for m in _BLOCK_MARKER.finditer(block.flat)]
        mi = 0
        matches = list(_ITEM_POINTS.finditer(block.flat))
        for i, m in enumerate(matches):
            while mi < len(markers) and markers[mi][0] < m.start():
                current_block = markers[mi][1]
                mi += 1
            if not markers and mi == 0 and bi > 0:
                pass  # 沿用上一页的块归属
            name = _score_item_name(block.flat, max(prev_end, m.start() - 160), m.start())
            prev_end = m.end()
            if len(name) < 2 or name in seen:
                continue
            seen.add(name)
            points = int(m.group(1))
            tail_end = (matches[i + 1].start() if i + 1 < len(matches)
                        else min(len(block.flat), m.end() + 400))
            standard = block.flat[m.end():tail_end]
            meta = _catalog_lookup(name, current_block)
            items.append({
                "id": meta["rid"],
                "name": name,
                "points": points,
                "block_name": current_block,
                "req_type": meta["req_type"],
                "evidence": meta["evidence"],
                "location": meta["location"],
                "condition": meta["condition"],
                "source_page": block.page.page,
                "source_text": _cut(f"{name}({points}分){standard}", 600),
                "source_quote": block.flat_span_quote(
                    max(0, m.start() - len(name)), m.end()),
            })
    _assign_blocks(items, _block_totals(blocks))
    items.append(_price_item(blocks))
    return items


def _block_totals(blocks: list[_Block]) -> dict:
    """读 2.2.1 分值构成，拿到报价/商务/技术各多少分。"""
    for block in blocks:
        if "分值构成" not in block.flat:
            continue
        seg = block.flat[block.flat.find("分值构成"):][:200]
        out = {}
        for label, key in (("投标报价", "报价"), ("商务部分", "商务"), ("技术部分", "技术")):
            m = re.search(rf"{label}[:：]?\s*(\d{{1,3}})\s*分", seg)
            if m:
                out[key] = int(m.group(1))
        if out:
            return out
    return {}


def _assign_blocks(items: list[dict], totals: dict) -> None:
    """用分值构成反推每个评分项属于报价/商务/技术。

    为什么不用"最近的 2.2.4(N) 标记"：那张表是跨页排版的，2.2.4(3)技术评分标准
    这一格常常排在**它所属条目的中间甚至后面**（示例项目 p31 就是这样），按位置就近判断
    会把技术项判成商务项。

    分值总和是招标文件自己写死的：商务 10 分、技术 50/40 分。按出现顺序累加，
    正好凑满商务总分的归商务，剩下的归技术——这是数据说话，不是猜位置。
    """
    if not totals.get("商务") and not totals.get("技术"):
        return  # 没读到分值构成，保留标记法结果
    order = [it for it in items if it["id"] != "SCORE-PRICE-1"]
    acc = 0
    biz_target = totals.get("商务")
    for it in order:
        if biz_target and acc < biz_target:
            it["block_name"] = "商务"
            acc += it["points"]
        else:
            it["block_name"] = "技术"
        it["id"] = _rid_for(it["id"], it["name"], it["block_name"])


def _rid_for(old_id: str, name: str, block_name: str) -> str:
    code = _BLOCK_CODE.get(block_name, "OTHER")
    tail = old_id.split("-")[-1]
    return f"SCORE-{code}-{tail}"


def _score_item_name(flat: str, start: int, end: int) -> str:
    """从 "….2.2.4(3)技术评分标准维保方案(含铁塔扶正/校正)" 中切出条目名。"""
    seg = flat[start:end]
    seg = seg.rsplit(SENTENCE_END, 1)[-1]          # 只用最后一句，去掉上一条的评分标准正文
    seg = re.sub(r"^\d+(?:\.\d+)*", "", seg)       # 2.2.4
    seg = re.sub(r"^\(\d\)", "", seg)              # (3)
    seg = _trim_score_prefix(seg)
    seg = seg.strip("()（） ")
    # 名称内部允许带括号说明（如"维保方案（含铁塔扶正/校正）"），不去掉
    return seg


def _catalog_lookup(name: str, block_name: str) -> dict:
    """命中已知语义表就复用它的证据/落位；否则按块给一个通用落位。"""
    key = next((k for k in _SCORE_CATALOG
                if k.replace(",", "") == name.replace(",", "") or k == name), None)
    if key:
        meta = dict(_SCORE_CATALOG[key])
        meta["rid"] = f"SCORE-{_BLOCK_CODE[block_name]}-{_slug(name)}"
        return meta
    # 名称未登记时，至少按名字给出可判定的条件与类型——
    # 否则这条评分项会掉进"规则引擎不判"的黑洞，公司看不到它其实要真实人员/设备。
    if re.search(r"人员|项目经理|安全员|配备|证", name):
        req_type, cond = "PERSONNEL", {"rule": "PERSONNEL_CERTIFIED"}
    elif re.search(r"设备|机具|工具|仪器|机械", name):
        req_type, cond = "TECHNICAL", {"rule": "EQUIPMENT_LEDGER"}
    else:
        req_type, cond = "TECHNICAL", None
    return {
        "rid": f"SCORE-{_BLOCK_CODE[block_name]}-{_slug(name)}",
        "req_type": req_type,
        "evidence": ["人员证书"] if req_type == "PERSONNEL" else [],
        "location": {"报价": "二、报价表", "商务": "五、商务和技术偏差表",
                     "技术": "八、服务方案", "其他": "十一、其他说明"}[block_name],
        "condition": cond,
    }


#: 常见评分项名 → ASCII 代号（保证编号可读且跨运行稳定）
_SLUG_TERMS = (
    ("业绩", "PERF"), ("财务状况", "FIN"), ("商务条款", "TERMS"), ("编制质量", "QUALITY"),
    ("人员配备", "STAFF"), ("项目经理", "PM"), ("组织机构", "ORG"), ("应急预案", "EMERG"),
    ("安全", "HSE"), ("环境", "HSE"), ("制度", "SYS"), ("流程", "SYS"), ("方案", "PLAN"),
    ("服务范围", "SCOPE"), ("质量", "QA"), ("售后", "AFTERSALE"), ("机械工具", "TOOLS"),
    ("设备", "EQUIP"), ("机具", "EQUIP"), ("维保", "MAINT"), ("巡检", "PATROL"),
    ("培训", "TRAIN"), ("进度", "SCHEDULE"), ("报价", "PRICE"),
    ("机构", "ORG"), ("岗位", "ORG"), ("职责", "DUTY"), ("配备", "STAFF"),
    ("技术", "TECH"), ("响应", "RESP"), ("承诺", "COMMIT"),
)

_SLUG_CACHE: dict[str, str] = {}


def _slug(name: str) -> str:
    """生成**稳定且 ASCII** 的编号片段。

    不能用内置 ``hash()``：Python 的字符串哈希带随机盐，换个进程就变，
    产物 id 会漂移，跨运行对比和回归测试都会失效。未登记的名单用 sha1 摘要兜底。
    """
    if name in _SLUG_CACHE:
        return _SLUG_CACHE[name]
    ascii_part = re.sub(r"[^A-Za-z0-9]+", "", name).upper()
    if ascii_part:
        slug = ascii_part[:12]
    else:
        slug = next((code for kw, code in _SLUG_TERMS if kw in name), "")
        if not slug:
            slug = "X" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:7].upper()
    _SLUG_CACHE[name] = slug
    return slug


def _price_item(blocks: list[_Block]) -> dict:
    """报价评分项：写法是"投标报价 50 分"，没有括号，单独抽取。"""
    weight = _extract_weight(blocks)
    weight_text = f"{weight['quote']} " if weight else ""
    for block in blocks:
        m = re.search(r"投标报价(\d{1,3})分", block.flat)
        if not m:
            continue
        tail = block.flat[m.end():m.end() + 300]
        base = re.search(r"得基础分(\d{1,2})分", tail)
        return {
            "id": "SCORE-PRICE-1",
            "name": "投标报价",
            "points": int(m.group(1)),
            "block_name": "报价",
            "req_type": "COMMERCIAL",
            "evidence": [],
            "location": "二、报价表",
            "condition": {"rule": "PRICE_WEIGHT", "price": int(m.group(1))},
            "source_page": block.page.page,
            "source_text": _cut(f"{weight_text}投标报价（{m.group(1)}分）{tail}", 600),
            "source_quote": block.flat_span_quote(m.start(), m.end()),
            "base_score": int(base.group(1)) if base else None,
        }
    return {
        "id": "SCORE-PRICE-1", "name": "投标报价", "points": 0, "block_name": "报价",
        "req_type": "COMMERCIAL", "evidence": [], "location": "二、报价表",
        "condition": {"rule": "PRICE_WEIGHT"}, "source_page": 1,
        "source_text": "", "source_quote": None,
    }


# --------------------------------------------------------------------------- #
# 4. 关键商务/格式要求
# --------------------------------------------------------------------------- #

def _extract_weight(blocks: list[_Block]) -> Optional[dict]:
    """解析 2.2.1 分值构成（报价/商务/技术各多少分）——不同项目分值不同，必须从原文读。"""
    for block in blocks:
        m = re.search(r"分值构成[^。]{0,20}", block.flat)
        if not m:
            continue
        seg = block.flat[m.start():m.start() + 200]
        values: dict[str, object] = {}
        for label, key in (("投标报价", "price"), ("商务部分", "business"),
                           ("技术部分", "technical"), ("其他评分因素", "other")):
            mm = re.search(rf"{label}[:：]?\s*(\d{{1,3}})\s*分", seg)
            if mm:
                values[key] = int(mm.group(1))
        mt = re.search(r"总分\s*(\d{1,3})\s*分", seg)
        if mt:
            values["total"] = int(mt.group(1))
        if values:
            return {"values": values, "page": block.page.page,
                    "quote": _cut(seg, 200)}
    return None


def _extract_commercial_specs(blocks: list[_Block]) -> tuple[list[dict], list[str]]:
    """关键商务/格式要求：按**模板锚点**抽取，返回 (命中的要求, 未命中的提示)。

    纪律（v1.6 修正）：**招标文件里没有的要求，不应该变成 requirement**。
    锚点没命中说明这份招标文件根本没写这一条；若照样生成 requirement，
    matcher 会按 condition 判出 WAIT_COMPANY，凭空给公司派一个不存在的活
    （例如文件里没有保证金条款，却让公司去交保证金）。
    未命中的锚点只记 extraction_issue，供人工核对锚点文案是否过时。
    """
    out: list[dict] = []
    issues: list[str] = []
    for spec in _COMMERCIAL_SPECS:
        pattern = fold(spec["anchor"])
        hit: Optional[dict] = None
        for block in blocks:
            m = re.search(pattern, block.flat)
            if not m:
                continue
            cond = dict(spec["condition"])
            # 把锚点捕获到的数字写进条件
            if "万元" in spec["anchor"] and m.groups():
                cond["amount_cny"] = float(m.group(1)) * 1e4
            elif "投标有效期" in spec["anchor"] and m.groups():
                cond["days"] = int(m.group(1))
            hit = {
                "id": spec["rid"],
                "chapter": spec["chapter"],
                "source_text": _cut(block.flat_span_quote(m.start(), m.end()), 400),
                "source_page": block.page.page,
                "source_section": spec["chapter"],
                "source_quote": block.line_quote(m.start(), m.end()),
                "req_type": spec["req_type"],
                "severity": spec["severity"],
                "condition": cond,
                "condition_text": spec["condition_text"],
                "evidence_required": spec["evidence_required"],
                "response_location": spec["response_location"],
            }
            break
        if hit is None:
            issues.append(f"模板锚点未命中，未生成要求：{spec['rid']}"
                          f"（{spec['chapter']}）——该招标文件可能未包含此项")
            continue
        out.append(hit)
    return out, issues


# --------------------------------------------------------------------------- #
# 组装
# --------------------------------------------------------------------------- #

def extract_requirements(text: TenderText, *, document_path: str, document_sha256: str,
                         source_meta: Optional[dict] = None) -> dict:
    blocks = _blocks(text)
    requirements: list[dict] = []
    issues: list[str] = []

    def add(req: dict) -> None:
        req.setdefault("severity", "HARD")
        req.setdefault("status", "NOT_EVALUATED")
        req.setdefault("evaluated_by", "NOT_EVALUATED")
        req.setdefault("missing_info", [])
        req.setdefault("evidence_chain", [])
        req.setdefault("matched_materials", [])
        req.setdefault("verdict_reason", None)
        req.setdefault("condition_text", None)
        req.setdefault("note", None)
        requirements.append(req)

    # --- 资格 3.x（HARD，不满足即否决）
    # 条件由**条款内容**推断（qualification_classifier），绝不按条款号写死——
    # 示例项目A 的 3.2 是承装修试许可证，示例项目B 的 3.2 是电力工程施工总承包二级，
    # 按编号硬绑定就会拿错证书去判，产出"结论对、依据错"的假 PASS。
    for item in _extract_qualification_items(blocks):
        cond = classify_qualification(item["flat_body"])
        req_type = "PERFORMANCE" if cond.get("rule") in ("PERFORMANCE_FIVE_ELEMENTS",) or (
            cond.get("rule") == "ALL_OF" and any(
                c.get("rule") == "PERFORMANCE_FIVE_ELEMENTS" for c in cond.get("checks", []))
        ) else "QUALIFICATION"
        if cond.get("rule") == "UNCLASSIFIED":
            issues.append(f"{item['id']} 资格条款未能识别条件类型，需人工确认："
                          f"{item['source_text'][:60]}")
        add({
            "id": item["id"],
            "chapter": item["chapter"],
            "source_text": item["source_text"],
            "requirement_type": req_type,
            "severity": "HARD",
            "condition": cond,
            "condition_text": cond.get("classified_from"),
            "evidence_required": cond.get("evidence", []),
            "response_location": cond.get("location", "六（二）投标人资格要求相关证明材料"),
            "source_page": item["source_page"],
            "source_section": item["source_section"],
            "source_quote": item["source_quote"],
        })
    if not any(r["id"].startswith("QUAL-3-") for r in requirements):
        issues.append("未抽到第一章 3.x 资格要求——需要人工确认锚点文案")

    # --- 1.4.3 否决情形（HARD）
    for item in _extract_forbidden_situations(blocks):
        add({
            "id": item["id"],
            "chapter": item["chapter"],
            "source_text": item["source_text"],
            "requirement_type": item["req_type"],
            "severity": "HARD",
            "condition": {"rule": "NEGATIVE_LIST_SELFCHECK"},
            "evidence_required": ["信用查询"] if item["needs_credit_evidence"] else [],
            "response_location": "七（五）企业信誉 / 七（七）其他资料",
            "source_page": item["source_page"],
            "source_section": item["source_section"],
            "source_quote": item["source_quote"],
        })
    if not any(r["id"].startswith("QUAL-143-") for r in requirements):
        issues.append("未抽到第二章 1.4.3 禁止情形——需要人工确认锚点文案")

    # --- 评分项（SCORE）
    for item in _extract_scoring_items(blocks):
        add({
            "id": item["id"],
            "chapter": f"第三章 评标办法 2.2.4（{'商务' if item['block_name'] == '商务' else '技术'}）",
            "source_text": item["source_text"],
            "requirement_type": item["req_type"],
            "severity": "SCORE",
            "condition": item["condition"],
            "condition_text": f"{item['name']}（{item['points']}分）",
            "evidence_required": item["evidence"],
            "response_location": item["location"],
            "source_page": item["source_page"],
            "source_section": f"第三章 2.2.4（{item['block_name']}）",
            "source_quote": item["source_quote"],
            "note": f"分值 {item['points']} 分",
        })

    # --- 关键商务/格式要求（只登记**招标文件里确实写了**的条目）
    commercial_items, commercial_issues = _extract_commercial_specs(blocks)
    for item in commercial_items:
        req = {
            "id": item["id"],
            "chapter": item["chapter"],
            "source_text": item["source_text"],
            "requirement_type": item["req_type"],
            "severity": item["severity"],
            "condition": item["condition"],
            "condition_text": item["condition_text"],
            "evidence_required": item["evidence_required"],
            "response_location": item["response_location"],
            "source_page": item["source_page"] or 1,
            "source_section": item["source_section"],
            "source_quote": item["source_quote"],
        }
        add(req)
    issues.extend(commercial_issues)

    # --- 第五章/技术规格书条款（NORMAL；含星号实质性条款时升级为 HARD）
    spec_items, spec_issues = extract_spec_requirements(text)
    for item in spec_items:
        add({
            "id": item["id"],
            "chapter": item["chapter"],
            "source_text": item["source_text"],
            "requirement_type": item["req_type"],
            "severity": item["severity"],
            "condition": item["condition"],
            "condition_text": item["condition_text"],
            "evidence_required": item["evidence_required"],
            "response_location": item["response_location"],
            "source_page": item["source_page"],
            "source_section": item["source_section"],
            "source_quote": item["source_quote"],
            "note": item.get("note"),
        })
    issues.extend(spec_issues)

    # --- 分值构成（信息项；权重从原文解析，不写死）
    weight = _extract_weight(blocks)
    if weight:
        add({
            "id": "SCORE-WEIGHT-1",
            "chapter": "第三章 评标办法 2.2.1",
            "source_text": weight["quote"],
            "requirement_type": "COMMERCIAL",
            "severity": "INFO",
            "condition": {"rule": "PRICE_WEIGHT", **weight["values"]},
            "condition_text": "总分 %s 分：报价 %s / 商务 %s / 技术 %s" % (
                weight["values"].get("total"), weight["values"].get("price"),
                weight["values"].get("business"), weight["values"].get("technical")),
            "evidence_required": [],
            "response_location": "二、报价表",
            "source_page": weight["page"],
            "source_section": "第三章 2.2.1",
            "source_quote": weight["quote"],
        })

    # ── 评分业绩与资格业绩的引用语义（GPT 1.5.3 第 1/3 项）──
    # 评分业绩条款：只有在原文**明示引用资格口径**时才允许 condition_ref；
    # 否则独立解析它自己的口径；解析不出来就交人工，绝不偷偷继承资格条件
    # （v1.5 的 bug：不管理解不理解，都拿第一条资格业绩顶上）。
    qual_perf_ids = [r["id"] for r in requirements
                     if r.get("severity") == "HARD"
                     and (r.get("condition") or {}).get("rule") == "PERFORMANCE_FIVE_ELEMENTS"]
    for r in requirements:
        c = r.get("condition") or {}
        if not c.get("scoring_candidate"):
            continue
        text = r.get("source_text") or ""
        if _references_qualification(text):
            if len(qual_perf_ids) == 1:
                r["condition"] = _performance_scoring_condition(text, qual_perf_ids[0])
            else:
                r["condition"] = {"rule": "SCORE_PERFORMANCE_UNPARSED",
                                  "why": "评分条款引用了资格口径，但资格要求中五要素业绩条款"
                                         f"不唯一（{qual_perf_ids or '无'}），无法确定引用目标"}
                issues.append(f"{r['id']} 无法唯一确定引用的资格业绩条款"
                              f"（候选：{qual_perf_ids or '无'}），需人工确认口径")
        else:
            own = classify_qualification(text)
            if own.get("rule") == "PERFORMANCE_FIVE_ELEMENTS":
                # 评分项自带完整口径（电压/时间/性质/对象）→ 独立条件，不引用资格项
                r["condition"] = {**own, "scoring": True,
                                  "classified_from": "评分业绩条款（独立口径，原文未声明同资格要求）"}
            else:
                r["condition"] = {"rule": "SCORE_PERFORMANCE_UNPARSED",
                                  "why": "评分业绩条款未声明同资格要求，且未能从其原文解析出"
                                         "可判定的业绩口径（电压/时间/性质/对象）"}
                issues.append(f"{r['id']} 业绩评分口径未能解析（原文未声明同资格要求），"
                              "需人工确认；不得自动继承资格条件")

    return {
        "schema_version": SCHEMA_VERSION,
        "source_document": {
            "path": document_path,
            "sha256": document_sha256,
            "pages": len(text),
        },
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "requirements": requirements,
        "extraction_issues": issues,
    }


def load_rules(rules_path: str | Path) -> dict:
    return json.loads(Path(rules_path).read_text(encoding="utf-8"))


#: 评分条款**明示引用资格口径**的通用表述（同投标人资格要求 / 要求同资格要求 /
#: 同资格审查要求 …）。只有命中这个信号，才允许 condition_ref 指向资格条件。
_SCORING_REF_RE = re.compile(r"同(?:投标人)?资格(?:审查)?要求")

#: 计分参数（只在原文明示时解析；解析不到就留空，绝不用历史项目的默认值）
_PER_ITEM_RE = re.compile(r"每(?:多|增加|新增)[^。\x1f]{0,40}?[加得](\d+(?:\.\d+)?)分")
_MAX_ADD_RE = re.compile(r"最多加(\d+(?:\.\d+)?)分")
_QUAL_ITEM_POINTS_RE = re.compile(r"资格(?:要求)?业绩(?:本身)?(?:得|计)(\d+(?:\.\d+)?)分")


def _references_qualification(text: str) -> bool:
    """评分条款是否**明示**引用资格要求（而不是我们替它假设引用）。"""
    return bool(_SCORING_REF_RE.search(text))


def _performance_scoring_condition(text: str, qual_perf_id: str | None) -> dict:
    """结构化"要求同资格要求"的评分规则。

    不复制五要素词表（防止两份规则漂移），只记 ``condition_ref`` 指向资格条款，
    并把**本项目自己在原文里写明的**计分规则结构化出来：

      * qualification_item_points：资格业绩本身计几分（原文写"资格业绩得N分"才有；
        只写"不重复计分"时为 0）
      * additional_points_per_item / max_additional_items：每增加一项的得分与上限
        （由"每…得N分"与"最多加M分"换算；任一项原文没写就留 ``None``）

    抽不到的参数一律留空——判分时宁可说"需人工按评标办法核定"，
    也不能拿别的项目的默认值凑一个像样的数。
    """
    per_item = float(m.group(1)) if (m := _PER_ITEM_RE.search(text)) else None
    cap_points = float(m.group(1)) if (m := _MAX_ADD_RE.search(text)) else None
    max_items = None
    if cap_points is not None and per_item:
        max_items = int(cap_points // per_item)

    qual_points = float(m.group(1)) if (m := _QUAL_ITEM_POINTS_RE.search(text)) else None
    if qual_points is None and "不重复计分" in text:
        qual_points = 0.0

    return {
        "rule": "PERFORMANCE_SCORING",
        "condition_ref": qual_perf_id,
        "additional_points_per_item": per_item,
        "max_additional_items": max_items,
        "max_additional_points": cap_points,
        "qualification_item_points": qual_points,
        "exclude_qualification_selected": "不重复计分" in text,
        "classified_from": "评分业绩条款（原文声明同资格要求）",
    }
