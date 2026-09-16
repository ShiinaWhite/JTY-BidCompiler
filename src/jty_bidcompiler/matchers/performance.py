"""业绩「五要素」判定引擎。

判定规则完全来自招标原文与人工核验口径（rules/performance_five_elements.json +
按项目注入的 condition）：

    五要素 ＝ 合同主体 ＋ 签订时间（≥条款起始日） ＋ 电压等级（≥条款门槛）
              ＋ 工作对象（＝**本项目条款解析出的对象**） ＋ 工作性质（＝条款要求的性质）

五条**必须同时成立**，且每一条都要能引用合同原文。
门槛值与词表逐项目来自招标原文（``condition``），代码里不内置任何项目的对象词：
项目对象解析不出来时，E4 判「证据不足」，**绝不默认成某个设备名**。

两条纪律（这是本引擎存在的意义）：
* 禁止扩大解释——"线路缆化改造"不是"设备改造"，"新建"不是"改造或维修"；
* 证据不足 ≠ 不满足——只拿到标题、拿不到正文时判"证据不足，不能认定"，
  而不是替公司编一个满足。

输入是素材记录里的 ``evidence_spans``（已核验的合同原文片段）。这一点很关键：
引擎判的是**合同原文**，不是素材标题，也不是模型的印象。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field as dc_field
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Optional

SATISFIED = "SATISFIED"
PARTIAL = "PARTIAL"
NOT_SATISFIED = "NOT_SATISFIED"
UNKNOWN = "UNKNOWN"

VERDICT_TEXT = {
    "PASS": "明确满足",
    "FAIL": "明确不满足",
    "EVIDENCE_INSUFFICIENT": "证据不足，不能认定",
}

# 电压：必须是 kV/千伏，且后面不能紧跟字母（排除 5000KVA 这类容量单位）
_KV_RE = re.compile(r"(\d{1,4}(?:\.\d+)?)\s*(?:kV|KV|kv|Kv|千伏)(?![a-zA-Z])")
_DATE_RE = re.compile(r"(20\d{2})\s*[-年./]\s*(\d{1,2})\s*[-月./]\s*(\d{1,2})")

# 对象语境中的"强/弱"工作动词：强=改造维修类，弱=仅检查试验类
_STRONG_OBJECT_VERBS = ("改造", "维修", "检修", "抢修", "更换", "升级", "维护", "定检")
_WEAK_OBJECT_VERBS = ("检查", "试验", "检测", "清扫", "巡视")

# 线路类对象词：出现这些且**没有**任何设备类词时，性质关键词不算数
_LINE_WORDS = ("线路", "缆化", "电缆隧道", "杆塔", "光缆", "架空", "导线", "铁塔")
# 设备类**通用**词（只作语境护栏：判断"改造/维修"是不是作用在设备本体上）。
# 这不是对象默认值：对象词一律来自本项目条款（E4.required_any）。
_OBJECT_WORDS = ("设备", "变配电", "柜", "变压器", "间隔", "继电保护", "配电", "装置", "仪器")

_WINDOW = 30  # 关键词上下文窗口（字符）


@dataclass
class ElementResult:
    element_id: str
    name: str
    status: str
    reason: str
    evidence: list[dict] = dc_field(default_factory=list)

    @property
    def blocking(self) -> bool:
        return self.status in (NOT_SATISFIED, UNKNOWN)


@dataclass
class PerformanceVerdict:
    material_id: str
    title: str
    verdict: str                       # PASS / FAIL / EVIDENCE_INSUFFICIENT
    elements: list[ElementResult]
    blocking_elements: list[str]
    manual_review_flags: list[str] = dc_field(default_factory=list)

    @property
    def verdict_text(self) -> str:
        return VERDICT_TEXT[self.verdict]

    def to_dict(self) -> dict:
        return {
            "material_id": self.material_id,
            "title": self.title,
            "verdict": self.verdict,
            "verdict_text": self.verdict_text,
            "blocking_elements": self.blocking_elements,
            "manual_review_flags": self.manual_review_flags,
            "elements": [
                {
                    "element_id": e.element_id,
                    "name": e.name,
                    "status": e.status,
                    "reason": e.reason,
                    "evidence": e.evidence,
                }
                for e in self.elements
            ],
        }

    def evidence_chain(self) -> list[dict]:
        chain: list[dict] = []
        for e in self.elements:
            for ev in e.evidence:
                chain.append({
                    "claim": f"{e.name}：{e.reason}",
                    "quote": ev.get("quote", ""),
                    "material_id": self.material_id,
                    "source_ref": ev.get("source_ref"),
                    "source_page": ev.get("source_page"),
                })
        return chain


class FiveElementEvaluator:
    """单份业绩材料的五要素判定器。"""

    def __init__(self, rules: dict, *, deadline: Optional[str] = None,
                 condition: Optional[dict] = None):
        """``condition`` 是**从招标文件条款里解析出来的**业绩条件。

        这是泛化的关键：同一套引擎，不同项目传进来不同的阈值与词表，例如
        ``{min_kv:10, object_keywords:["<本项目对象>"], nature_keywords:["改造","维修"]}``
        或 ``{min_kv:110, object_keywords:["<本项目对象原子>"], nature_keywords:["维护","维修"]}``。
        阈值和词表都来自招标原文，代码里不写死任何一个项目的口径；
        条件缺失或对象词为空时，对应要素判「证据不足」，而不是拿默认设备名顶上。
        """
        self.rules = rules
        params = {e["id"]: dict(e.get("params", {})) for e in rules.get("elements", [])}
        cond = condition or {}
        if cond.get("not_before"):
            params.setdefault("E2", {})["not_before"] = cond["not_before"]
        if cond.get("min_kv") is not None:
            params.setdefault("E3", {})["min_kv"] = cond["min_kv"]
        if "object_keywords" in cond:
            # 显式给出（可能是空列表）：空列表＝"条款没解析出对象"，
            # 此时必须让 E4 判"证据不足"，不能回落到规则文件里某个项目的设备名。
            params.setdefault("E4", {})["required_any"] = cond["object_keywords"]
        if cond.get("object_keywords_source"):
            params.setdefault("E4", {})["keywords_source"] = cond["object_keywords_source"]
        if cond.get("nature_keywords"):
            params.setdefault("E5", {})["accept_any"] = cond["nature_keywords"]
        self.params = params
        self.condition = cond
        win = params.get("E2", {})
        self.not_before = win.get("not_before", "2023-01-01")
        self.deadline = deadline

    # ------------------------------------------------------------------ #
    @classmethod
    def from_rules_file(cls, path: str | Path, *, deadline: Optional[str] = None,
                        condition: Optional[dict] = None):
        return cls(json.loads(Path(path).read_text(encoding="utf-8")),
                   deadline=deadline, condition=condition)

    # ------------------------------------------------------------------ #
    def evaluate(self, material: dict, *, bidder: Optional[str] = None) -> PerformanceVerdict:
        spans = list(material.get("evidence_spans") or [])
        bidder = bidder or (self.params.get("E1", {}).get("bidder_names") or [None])[0]

        results = [
            self._e1_party(material, spans, bidder),
            self._e2_date(material, spans),
            self._e3_voltage(material, spans),
            self._e4_object(material, spans),
            self._e5_nature(material, spans),
        ]
        blocking = [r.element_id for r in results if r.blocking]

        if any(r.status == NOT_SATISFIED for r in results):
            verdict = "FAIL"
        elif blocking:
            verdict = "EVIDENCE_INSUFFICIENT"
        else:
            verdict = "PASS"

        flags = self._manual_flags(material, results, verdict)
        return PerformanceVerdict(
            material_id=material.get("material_id", ""),
            title=material.get("title", ""),
            verdict=verdict,
            elements=results,
            blocking_elements=blocking,
            manual_review_flags=flags,
        )

    # ------------------------------------------------------------------ #
    # E1 合同主体
    def _e1_party(self, material: dict, spans: list[dict], bidder: Optional[str]) -> ElementResult:
        name = self.params.get("E1", {}).get("name", "合同主体")
        company = material.get("company") or ""
        candidates = [bidder] if bidder else []
        candidates += [n for n in (self.params.get("E1", {}).get("bidder_names") or []) if n]
        candidates = list(dict.fromkeys(candidates))

        # 合同原文里的当事人证据优先（能同时带出合同编号、甲乙方署名）
        evidence: list[dict] = []
        matched_quote = None
        for span in spans:
            text = span.get("text", "")
            for cand in candidates:
                if cand and cand in text:
                    matched_quote = _excerpt(text, cand)
                    evidence.append({"quote": matched_quote, "source_ref": span.get("source_ref"),
                                     "source_page": span.get("source_page")})
                    break
            if matched_quote:
                break
        if not spans:
            return ElementResult("E1", name, UNKNOWN, "无证据片段，无法确认合同主体", [])

        if matched_quote:
            return ElementResult("E1", name, SATISFIED,
                                 f"合同原文出现投标人名称（{company or bidder}）", evidence)
        for cand in candidates:
            if cand and cand in company:
                return ElementResult("E1", name, SATISFIED,
                                     f"素材登记主体为投标人（{company}）",
                                     [{"quote": f"素材登记主体：{company}",
                                       "source_ref": material.get("source_file")}])
        return ElementResult("E1", name, UNKNOWN,
                             "素材未登记主体，且证据片段中未出现投标人名称", evidence)

    # ------------------------------------------------------------------ #
    # E2 签订时间
    def _e2_date(self, material: dict, spans: list[dict]) -> ElementResult:
        name = self.params.get("E2", {}).get("name", "签订时间")
        raw = material.get("contract_date")
        source_ref = material.get("source_file")
        evidence: list[dict] = []
        if not spans and not raw:
            return ElementResult("E2", name, UNKNOWN, "无证据片段且未登记签订日期", [])

        d = _parse_date(raw) if raw else None
        if d is None:
            for span in spans:
                m = _DATE_RE.search(span.get("text", ""))
                if m:
                    d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                    evidence.append({"quote": _excerpt(span["text"], m.group(0)),
                                     "source_ref": span.get("source_ref")})
                    source_ref = span.get("source_ref")
                    break
        if d is None:
            return ElementResult("E2", name, UNKNOWN, "未能从素材或证据片段中确定合同签订日期", evidence)

        if evidence == []:
            evidence.append({"quote": f"合同签订日期：{raw}", "source_ref": source_ref})

        not_before = _parse_date(self.not_before)
        if d < not_before:
            return ElementResult("E2", name, NOT_SATISFIED,
                                 f"合同签订日期 {d.isoformat()} 早于 {not_before.isoformat()}",
                                 evidence)
        if self.deadline:
            dl = _parse_date(self.deadline)
            if dl and d > dl:
                return ElementResult("E2", name, NOT_SATISFIED,
                                     f"合同签订日期 {d.isoformat()} 晚于投标截止日 {dl.isoformat()}",
                                     evidence)
        return ElementResult("E2", name, SATISFIED,
                             f"合同签订日期 {d.isoformat()} 落在 2023-01-01 至投标截止日之间", evidence)

    # ------------------------------------------------------------------ #
    # E3 电压等级
    def _e3_voltage(self, material: dict, spans: list[dict]) -> ElementResult:
        name = self.params.get("E3", {}).get("name", "电压等级")
        min_kv = float(self.params.get("E3", {}).get("min_kv", 10))
        evidence: list[dict] = []
        found: list[tuple[float, str, Optional[str]]] = []

        kv_field = material.get("voltage_level_kv")
        if isinstance(kv_field, (int, float)):
            found.append((float(kv_field), f"素材登记电压等级：{material.get('voltage_level')}",
                          material.get("source_file")))
        for span in spans:
            for m in _KV_RE.finditer(span.get("text", "")):
                found.append((float(m.group(1)), _excerpt(span["text"], m.group(0)),
                              span.get("source_ref")))

        if not found:
            return ElementResult("E3", name, UNKNOWN, "证据片段中未出现 kV/千伏 表述", evidence)

        found.sort(key=lambda x: -x[0])
        best_kv, best_quote, best_ref = found[0]
        evidence = [{"quote": q, "source_ref": r} for _, q, r in found[:4]]
        if best_kv >= min_kv:
            return ElementResult("E3", name, SATISFIED,
                                 f"最高电压等级 {best_kv:g}kV ≥ {min_kv:g}kV", evidence)
        return ElementResult("E3", name, NOT_SATISFIED,
                             f"最高电压等级 {best_kv:g}kV 低于 {min_kv:g}kV", evidence)

    # ------------------------------------------------------------------ #
    # E4 工作对象
    def _e4_object(self, material: dict, spans: list[dict]) -> ElementResult:
        spec = self.params.get("E4", {})
        name = spec.get("name", "工作对象")
        keywords = spec.get("required_any") or []
        if not keywords:
            # 没有对象词就没法判对象——这时候必须"证据不足"，
            # 不能默认成某个项目的对象名（那会把 A 项目的口径悄悄带给别的项目）。
            return ElementResult("E4", name, UNKNOWN,
                                 "业绩条件未解析出工作对象关键词，需人工确认条款口径", [])
        if not spans:
            # 没有证据片段 ≠ 证据表明不满足。缺证据只能判"证据不足"，
            # 否则等于替公司下了一个它没做过的结论。
            return ElementResult("E4", name, UNKNOWN, "无证据片段，无法确认工作对象", [])
        evidence: list[dict] = []
        partial: list[dict] = []

        for span in spans:
            text = span.get("text", "")
            for kw in keywords:
                for m in re.finditer(re.escape(kw), text):
                    window = text[max(0, m.start() - _WINDOW): m.end() + _WINDOW]
                    quote = _excerpt(text, kw)
                    if any(v in window for v in _STRONG_OBJECT_VERBS):
                        evidence.append({"quote": quote, "source_ref": span.get("source_ref"),
                                         "source_page": span.get("source_page")})
                    elif any(v in window for v in _WEAK_OBJECT_VERBS):
                        partial.append({"quote": quote, "source_ref": span.get("source_ref"),
                                        "source_page": span.get("source_page")})
        if evidence:
            return ElementResult("E4", name, SATISFIED,
                                 f"合同原文以{'/'.join(keywords)}为工作对象，且处于改造维修类语境", evidence)
        if partial:
            return ElementResult("E4", name, PARTIAL,
                                 f"合同原文出现{'/'.join(keywords)}，但语境仅为检查/试验类（非工作对象）",
                                 partial)
        if spec.get("keywords_source") == "clause_derived":
            # 对象词是从条款里**推断**的，不是通用词表里的设备名：
            # 合同没用同样的表述，只能说明"我没有把握"，不能替公司判"不满足"。
            return ElementResult("E4", name, UNKNOWN,
                                 f"合同证据片段未出现条款推断的对象表述（{'/'.join(keywords)}）；"
                                 "该对象词由条款推断，口径需人工确认", [])
        return ElementResult("E4", name, NOT_SATISFIED,
                             f"合同证据片段全文未出现{'/'.join(keywords)}；"
                             f"其他对象（线路迁改/缆化改造/杆塔等）不得扩张解释为{'/'.join(keywords)}",
                             [])

    # ------------------------------------------------------------------ #
    # E5 工作性质
    def _e5_nature(self, material: dict, spans: list[dict]) -> ElementResult:
        spec = self.params.get("E5", {})
        name = spec.get("name", "工作性质")
        accept = spec.get("accept_any", ["改造", "维修", "检修", "抢修"])
        if not spans:
            return ElementResult("E5", name, UNKNOWN, "无证据片段，无法确认工作性质", [])
        evidence: list[dict] = []
        line_only: list[dict] = []
        rejects: list[dict] = []

        for span in spans:
            text = span.get("text", "")
            for kw in accept:
                for m in re.finditer(re.escape(kw), text):
                    window = text[max(0, m.start() - _WINDOW): m.end() + _WINDOW]
                    quote = _excerpt(text, kw)
                    is_line = any(w in window for w in _LINE_WORDS)
                    has_object = any(w in window for w in _OBJECT_WORDS)
                    if is_line and not has_object:
                        line_only.append({"quote": quote, "source_ref": span.get("source_ref")})
                    else:
                        evidence.append({"quote": quote, "source_ref": span.get("source_ref"),
                                         "source_page": span.get("source_page")})
            for rk in spec.get("reject_if_only", []):
                if rk in text:
                    rejects.append({"quote": _excerpt(text, rk),
                                    "source_ref": span.get("source_ref")})

        if evidence:
            return ElementResult("E5", name, SATISFIED,
                                 f"合同原文含改造/维修类责任表述（{'/'.join(accept)}）", evidence)
        if line_only:
            return ElementResult("E5", name, NOT_SATISFIED,
                                 f"性质关键词（{'/'.join(accept)}）仅出现在线路/缆化语境，"
                                 "不构成对设备本体的改造或维修", line_only)
        detail = "、".join(sorted({q["quote"].split("：")[0][:14] for q in rejects[:3]}))
        return ElementResult(
            "E5", name, NOT_SATISFIED,
            "合同证据片段未出现改造/维修/检修/抢修等性质表述"
            + (f"；仅有新建/安装/试验/检测类表述（{detail}）" if rejects else ""),
            rejects[:3],
        )

    # ------------------------------------------------------------------ #
    def _manual_flags(self, material: dict, results: list[ElementResult],
                      verdict: str) -> list[str]:
        """需要人工/评标口径终审的标记（不改变判定，只提示复核）。"""
        flags: list[str] = []
        if verdict != "PASS":
            return flags
        title = material.get("title") or ""
        # 用**本项目的条件词**判断，而不是写死某个项目的对象名：
        # 对象词必须来自本项目条款（E4.required_any，由 parsers 注入）。
        objects = self.params.get("E4", {}).get("required_any") or []
        natures = self.params.get("E5", {}).get("accept_any") or []
        if objects and natures:
            has_object = any(o in title for o in objects)
            has_nature = any(n in title for n in natures)
            if not has_object or not has_nature:
                flags.append("CONTRACT_TITLE_NOT_BINDING_OBJECT")
        element_status = {r.element_id: r.status for r in results}
        if element_status.get("E4") == PARTIAL:
            flags.append("PARTIAL_ELEMENT_E4")
        return flags


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _parse_date(text: Optional[str]) -> Optional[date]:
    if not text:
        return None
    m = _DATE_RE.search(str(text))
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _excerpt(text: str, needle: str, span: int = 26, *, whole_limit: int = 200) -> str:
    """取证据引用。

    证据片段本来就不长（一条合同条款），此时**整条引用**比截取更能说明问题——
    合同编号、甲乙方署名这类身份信息往往就在片段开头，截掉就丢了。
    只有超长片段才回退到关键词窗口。
    """
    text = text.strip()
    if len(text) <= whole_limit:
        return text
    idx = text.find(needle)
    if idx < 0:
        return text[: span * 2]
    start = max(0, idx - span)
    end = min(len(text), idx + len(needle) + span)
    out = text[start:end].replace("\n", " ").strip()
    if start > 0:
        out = "…" + out
    if end < len(text):
        out = out + "…"
    return out


def evaluate_all(materials: Iterable[dict], evaluator: FiveElementEvaluator,
                 *, bidder: Optional[str] = None) -> list[PerformanceVerdict]:
    return [evaluator.evaluate(m, bidder=bidder) for m in materials]
