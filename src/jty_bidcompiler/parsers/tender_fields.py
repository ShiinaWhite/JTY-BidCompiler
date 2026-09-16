"""MVP-1：招标文件 → project.json。

每个字段都由**规则**抽取（正则/关键词锚定），并同时记录：
页码、原文引用、所属章节、置信度、命中规则 id。
抽不到就写 ``value=null`` + ``reason``，绝不猜值。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field
from datetime import datetime
from typing import Callable, Iterable, Optional

from .. import __version__
from ..io.textnorm import Hit, PageText, TenderText, fold
from .tender_document import sha256_file

SCHEMA_VERSION = "1.0"

FIELDS = (
    "project_name", "project_number", "tenderer", "agency", "location",
    "deadline", "service_period", "warranty", "bid_security",
    "consortium_allowed", "platform", "submission_format",
)

_CHAPTER = re.compile(r"第[一二三四五六七八九十]+章[^\n]{0,24}")

#: 条款边界（用于没有句末标点的字段值）。
#:
#: 招标文件里 "2.3 服务期：三年" 后面**没有句号**——它以换行结束；而扁平串把换行
#: 吃掉了，于是 `服务期：(.+)` 会一路吃到 "2.4 服务地点：…"，实测得到 "三年2.4服务地点…"。
#: 因此对这类字段统一加一个边界：下一个条款编号（``\d+\.\d``）或句末标记或页末。
_STOP = r"(?=\d+\.\d|[\x1f]|$)"


# --------------------------------------------------------------------------- #
# 值规范化
# --------------------------------------------------------------------------- #

def norm_date_cn(text: str) -> Optional[str]:
    """'2026 年09 月18 日11:00' → '2026-09-18 11:00'。"""
    m = re.search(
        r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*(?:(\d{1,2})\s*[:：]\s*(\d{2}))?",
        text,
    )
    if not m:
        return None
    y, mo, d, hh, mm = m.groups()
    out = f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
    if hh is not None and mm is not None:
        out += f" {int(hh):02d}:{mm}"
    return out


def norm_money_cn(text: str) -> Optional[float]:
    """'人民币2 万元' → 20000.0（单位：元）。"""
    m = re.search(r"([\d,，.]+)\s*(亿|万|千)?\s*元", text)
    if not m:
        return None
    num = float(m.group(1).replace(",", "").replace("，", ""))
    scale = {"亿": 1e8, "万": 1e4, "千": 1e3, None: 1.0}[m.group(2)]
    return num * scale


def norm_yesno_cn(text: str) -> Optional[bool]:
    t = text.strip()
    if re.search(r"^(否|不允许|不接受|不要求)", t):
        return False
    if re.search(r"^(是|允许|接受|要求)", t):
        return True
    return None


# --------------------------------------------------------------------------- #
# 字段规则
# --------------------------------------------------------------------------- #

@dataclass
class FieldSpec:
    field: str
    rule_id: str
    pattern: str
    kind: str = "FLAT"            # FLAT=整页去空格检索；LINE=逐行检索
    group: int = 1
    prefer_pages: tuple[int, ...] = ()
    confidence: float = 0.9
    normalizer: Optional[Callable[[str], object]] = None
    section: Optional[str] = None
    note: Optional[str] = None
    candidates: list[dict] = dc_field(default_factory=list)


FIELD_SPECS: tuple[FieldSpec, ...] = (
    FieldSpec("project_name", "F-PN-1", r"^\s*([\u4e00-\u9fa5（）()A-Za-z0-9]{8,80}?项目)\s*$",
              kind="LINE", prefer_pages=(1,), confidence=0.95, section="封面"),
    FieldSpec("project_name", "F-PN-2", r"本招标项目([\u4e00-\u9fa5（）()A-Za-z0-9]{8,80}?)，招标人为",
              prefer_pages=(3, 1), confidence=0.9, section="第一章 招标公告"),
    # 编号形态随平台而异（字母前缀 + 数字），不绑定某一家平台：
    # F-PNO-1/2 要求"字母开头 + 数字"，F-PNO-3 兜底纯数字编号。
    # 数字段允许内部带连字符（如 XYGS-2026-0417），但必须以数字收尾，
    # 否则会把编号后面的分隔符一起吞掉。
    FieldSpec("project_number", "F-PNO-1",
              r"招标编号[:：]?\s*([A-Za-z]{2,10}[-_]?\d[\d\-_]{1,18}\d)",
              prefer_pages=(1,), confidence=0.95, section="封面"),
    FieldSpec("project_number", "F-PNO-2",
              r"项目编号[:：]?\s*([A-Za-z]{2,10}[-_]?\d[\d\-_]{1,18}\d)",
              prefer_pages=(3,), confidence=0.95, section="第一章 招标公告"),
    FieldSpec("project_number", "F-PNO-3",
              r"(?:招标|项目)编号[:：]?\s*([0-9][0-9\-_]{4,18}[0-9])",
              prefer_pages=(1, 3), confidence=0.85, section="封面 / 第一章"),
    FieldSpec("tenderer", "F-TD-1", r"招标人为([^,。;；]{4,40}?)[。;；]",
              prefer_pages=(3,), confidence=0.95, section="第一章 招标公告"),
    FieldSpec("tenderer", "F-TD-2", r"招标人[:：]([^,。;；]{4,40})",
              prefer_pages=(1,), confidence=0.9, section="封面"),
    FieldSpec("tenderer", "F-TD-3", r"名称[:：]([^,。;；]{4,40}?)地址[:：]",
              prefer_pages=(4,), confidence=0.85, section="第一章 招标公告 8."),
    FieldSpec("agency", "F-AG-1", r"招标代理机构[:：]([^（(,，。;；]{4,40})",
              prefer_pages=(1,), confidence=0.95, section="封面"),
    FieldSpec("location", "F-LOC-1", r"服务地点[:：]([^。]{2,60}?)" + _STOP,
              prefer_pages=(3, 4), confidence=0.95, section="第一章 招标公告 2.4"),
    FieldSpec("deadline", "F-DL-1", r"开标时间[:：]?\s*(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日\s*\d{1,2}\s*[:：]\s*\d{2})",
              prefer_pages=(3,), confidence=0.95, normalizer=norm_date_cn,
              section="第一章 招标公告 开标时间"),
    FieldSpec("deadline", "F-DL-2", r"投标文件应于(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日\s*\d{1,2}\s*[:：]\s*\d{2})之前递交",
              prefer_pages=(4,), confidence=0.9, normalizer=norm_date_cn,
              section="第一章 招标公告 5."),
    FieldSpec("service_period", "F-SP-1", r"服务期[:：]([^。]{2,60}?)" + _STOP,
              prefer_pages=(3,), confidence=0.95, section="第一章 招标公告 2.3"),
    FieldSpec("warranty", "F-WT-1", r"质量保证期为([^。，,;；]{2,60})",
              prefer_pages=(68,), confidence=0.9, section="第五章 服务标准及要求 3"),
    # 服务类项目常用"质保期/保证期"表述（示例：维修内容质保期 1 年）
    FieldSpec("warranty", "F-WT-2", r"质保期为([^。，,;；]{2,40}?)" + _STOP,
              prefer_pages=(41, 43), confidence=0.85, section="第四章 合同条款"),
    FieldSpec("warranty", "F-WT-3", r"质保期\(?([一二三四五六七八九十\d]{1,3}年)\)?",
              prefer_pages=(41,), confidence=0.9, section="第四章 合同条款"),
    FieldSpec("bid_security", "F-BS-1", r"投标保证金的金额[:：]?(人民币[\d.]+\s*(?:万元|元))",
              prefer_pages=(8,), confidence=0.95, normalizer=norm_money_cn,
              section="第二章 投标人须知前附表 3.4.1"),
    # 联合体的问法各项目不同："是否允许联合体投标" / "是否接受联合体报价"
    FieldSpec("consortium_allowed", "F-CA-1", r"本项目是否(?:允许|接受)联合体(?:投标|报价)[:：]?([^。]{1,10}?)" + _STOP,
              prefer_pages=(3, 4, 5), confidence=0.95, normalizer=norm_yesno_cn,
              section="第一章 招标公告 3.4"),
    FieldSpec("consortium_allowed", "F-CA-2", r"是否(?:允许|接受)联合体(?:投标|报价)[:：]?([^。]{1,10}?)" + _STOP,
              prefer_pages=(6, 7, 8), confidence=0.9, normalizer=norm_yesno_cn,
              section="第二章 投标人须知前附表 1.4.2"),
    # 平台名随招标人而异：取"××平台"（可带 URL）的通用形态，不写死某一家。
    FieldSpec("platform", "F-PF-1", r"([一-龥A-Za-z]{4,20}平台)\(?(https?://[\w./-]+)?",
              prefer_pages=(4, 5, 3), confidence=0.9, section="第一章 招标公告 4.4/9.5"),
    FieldSpec("submission_format", "F-SF-1", r"(投标文件全部采用电子文档[^。]{0,60}。)",
              prefer_pages=(8,), confidence=0.95,
              section="第二章 投标人须知前附表 3.7.3"),
    FieldSpec("submission_format", "F-SF-2", r"(须在投标文件使用CA数字证书加盖投标单位电子印章[^。]{0,40}。)",
              prefer_pages=(8,), confidence=0.85,
              section="第二章 投标人须知前附表 3.7.3"),
)


# --------------------------------------------------------------------------- #
# 抽取
# --------------------------------------------------------------------------- #

@dataclass
class _Match:
    """一次规则命中：字段值区间 + 整条匹配区间（用于生成可读证据引用）。"""

    value: Hit
    whole: Hit


def _iter_hits(page: PageText, spec: FieldSpec) -> Iterable[_Match]:
    """在单页上找该规则的命中。

    FLAT 模式在**折叠后**的文本上检索，因此模式本身也要先折叠：
    否则模式里的 ``。``/``，`` 与折叠后的 ``.``/``,`` 对不上（这是最容易踩的坑）。
    LINE 模式在原文上检索，模式保持原样。
    """
    if spec.kind == "LINE":
        for m in re.finditer(spec.pattern, page.raw, re.M):
            if not m.group(spec.group):
                continue
            value = Hit(0, 0, m.start(spec.group), m.end(spec.group), m.group(spec.group))
            whole = Hit(0, 0, m.start(), m.end(), m.group(0).strip())
            yield _Match(value=value, whole=whole)
    else:
        pattern = fold(spec.pattern)
        for m in re.finditer(pattern, page.flat):
            vs, ve = m.span(spec.group)
            if ve <= vs:
                continue
            yield _Match(
                value=page.hit_from_flat_span(vs, ve),
                whole=page.hit_from_flat_span(m.start(), m.end()),
            )


def _nearest_section(page: PageText, hit: Hit) -> Optional[str]:
    """命中处之前最近的章节标题。"""
    before = page.raw[: hit.raw_start]
    chapters = list(_CHAPTER.finditer(before))
    if chapters:
        return chapters[-1].group(0).strip()
    return None


def _value_of(hit: Hit, spec: FieldSpec) -> object:
    """字段值去掉内嵌空白：PDF 常在数字/公司名中间断行断词。"""
    return re.sub(r"\s+", "", hit.text)


def extract_field(text: TenderText, spec: FieldSpec) -> dict:
    """按一条规则扫描全文，返回命中列表（含证据）。"""
    order = list(spec.prefer_pages) + [
        p for p in range(1, len(text) + 1) if p not in spec.prefer_pages
    ]
    found: list[dict] = []
    for page_no in order:
        if page_no > len(text):
            continue
        page = text.page(page_no)
        for match in _iter_hits(page, spec):
            raw_value = _value_of(match.value, spec)
            if not raw_value:
                continue
            found.append({
                "value": raw_value,
                "page": page_no,
                # 证据引用取**整条匹配**（值+定位词），并压掉换行，保证人能读
                "quote": _clean_quote(page, match.whole),
                "section": _nearest_section(page, match.value) or spec.section,
                "rule_id": spec.rule_id,
                "confidence": spec.confidence,
            })
            break  # 同一页只取第一处，避免同页重复计数
        if len(found) >= 3:
            break
    return found


def _clean_quote(page: PageText, hit: Hit) -> str:
    """把证据引用整理成单行、长度可控的原文片段。"""
    quote = re.sub(r"\s+", " ", page.raw[hit.raw_start:hit.raw_end]).strip()
    if len(quote) > 200:
        quote = quote[:200] + "…"
    return quote


def extract_project(text: TenderText, *, document_path: str, document_title: str = "") -> dict:
    """产出 project.json 数据结构。"""
    fields: dict[str, dict] = {}
    specs_by_field: dict[str, list[FieldSpec]] = {}
    for spec in FIELD_SPECS:
        specs_by_field.setdefault(spec.field, []).append(spec)

    for name in FIELDS:
        hits: list[dict] = []
        chosen: Optional[dict] = None
        for spec in specs_by_field.get(name, []):
            hits = extract_field(text, spec)
            if hits:
                chosen = hits[0]
                break

        if chosen is None:
            fields[name] = {
                "value": None,
                "source": None,
                "source_page": None,
                "source_section": None,
                "confidence": 0.0,
                "extraction_method": "NOT_FOUND",
                "reason": "全部规则均未命中：招标文件中未出现该字段的可用表述",
                "candidates": [],
            }
            continue

        spec = next(s for s in specs_by_field[name] if s.rule_id == chosen["rule_id"])
        value = chosen["value"]
        normalized = None
        if spec.normalizer is not None:
            normalized = spec.normalizer(value)
        fields[name] = {
            "value": value,
            "value_normalized": normalized,
            "source": {
                "document": document_path,
                "page": chosen["page"],
                "quote": chosen["quote"],
                "section": chosen["section"],
                "match_rule": chosen["rule_id"],
            },
            "source_page": chosen["page"],
            "source_section": chosen["section"],
            "confidence": chosen["confidence"],
            "extraction_method": "RULE_REGEX",
            "candidates": [
                {
                    "document": document_path,
                    "page": h["page"],
                    "quote": h["quote"],
                    "section": h["section"],
                    "match_rule": h["rule_id"],
                }
                for h in hits[1:3]
            ],
        }

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_by": {
            "tool": "jty-bidcompiler",
            "tool_version": __version__,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "pipeline": "MVP-1 extract_project (rule-based, evidence-tracking)",
        },
        "source_document": {
            "path": document_path,
            "sha256": sha256_file(document_path) if _exists(document_path) else "0" * 64,
            "pages": len(text),
            "title": document_title,
        },
        "fields": fields,
    }


def _exists(path: str) -> bool:
    from pathlib import Path

    return Path(path).exists()
