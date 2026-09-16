"""资格条款 → 结构化条件（**按内容识别，不按条款号**）。

v0.1.0 的教训：条件表是按条款号（3.1/3.2/3.3/3.4）写死的一套项目语义，于是另一个
项目的 3.2（电力工程施工总承包二级 + 安全生产许可证）被拿"承装修试许可证"去判，
判出了**错误的 PASS**——结论看着对，依据完全错。

正确做法：只读条款原文，从文字里推断它到底要什么。这样同一个编译器既能处理
"承装（修、试）电力设施许可证 承修、承试类三级或以上"，也能处理
"电力工程施工总承包二级及以上资质"，还能处理两者写在同一句里的情况。

条款可能同时包含多个条件（例如施工总承包资质与安全生产许可证写在同一句），
因此输出是条件**列表**，外层用 ``ALL_OF`` 组合。
"""

from __future__ import annotations

import re
from typing import Optional

#: 中文数字（含大写）→ 阿拉伯数字，用于"贰级"="二级"
_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8,
           "九": 9, "十": 10, "壹": 1, "贰": 2, "叁": 3, "肆": 4, "伍": 5, "陆": 6,
           "柒": 7, "捌": 8, "玖": 9}

_LEVEL_RE = re.compile(r"([一二三四五壹贰叁肆伍])级")
_KV_RE = re.compile(r"(\d{1,4})\s*(?:kV|KV|Kv|kv|千伏)")
_MIN_DATE_RE = re.compile(r"自(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_MIN_COUNT_RE = re.compile(r"(?:至少|不少于|具有|须具有|提供)?\s*(\d{1,2})\s*项")
_BEFORE_DATE_RE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")

#: 许可证名称 → 素材类别（用于把条款文字映射到素材库分类）
LICENSE_CATEGORY = {
    "承装（修、试）电力设施许可证": "资质证书_承装修试",
    "承装(修、试)电力设施许可证": "资质证书_承装修试",
    "承装修试": "资质证书_承装修试",
    "电力工程施工总承包": "资质证书_施工总承包",
    "机电工程施工总承包": "资质证书_施工总承包",
    "建筑业企业资质": "资质证书_施工总承包",
    "安全生产许可证": "安全生产许可证",
    "营业执照": "营业执照",
    "法人证书": "营业执照",
}

#: 业绩对象词（按长度降序匹配，保证"输电线路铁塔"优先于"输电线路"）。
#: 只放**通用设备/设施词**，不放任何具体项目的设备名——词表覆盖不到的项目，
#: 走 :func:`_objects_from_clause` 从句子里取，绝不用某一个项目的对象词兜底。
OBJECT_LEXICON = (
    # 变配电类
    "高压开关柜", "开关柜", "输电线路铁塔", "输电线路", "架空线路", "电力电缆",
    "变电站", "配电室", "变压器", "环网柜", "箱变", "线路", "铁塔",
    # 过程监测与市政管网类
    "监测终端", "在线监测", "监控系统", "仪器仪表", "传感器", "泵站", "阀门",
    "管网", "管道",
)

#: 业绩性质词
NATURE_LEXICON = ("改造或维修", "维护维修", "维护及维修", "运行维护", "改造", "维修",
                  "检修", "维护", "抢修", "运维", "新建", "安装", "检测", "试验")

#: 复合词 → 原子词。**这是"输电线路铁塔维护维修"这类要求能不能跨项目复用的关键**：
#: 招标要求写"输电线路铁塔"，而合同里只会写"110kV 线路维护"或"铁塔校正"——
#: 要求字面全等就永远匹配不上。所以把要求拆成原子语义单元，再拿原子去比对合同原文。
OBJECT_ATOMS = {
    "高压开关柜": ["开关柜"],
    "开关柜": ["开关柜"],
    "输电线路铁塔": ["输电线路", "线路", "铁塔"],
    "输电线路": ["输电线路", "线路", "铁塔"],
    "架空线路": ["架空线路", "线路", "杆塔"],
    "电力电缆": ["电缆"],
    "变电站": ["变电站"],
    "配电室": ["配电室"],
    "变压器": ["变压器"],
    "环网柜": ["环网柜"],
    "箱变": ["箱变"],
    "线路": ["线路"],
    "铁塔": ["铁塔", "杆塔"],
    "监测终端": ["监测终端"],
    "在线监测": ["监测"],
    "监控系统": ["监控"],
    "仪器仪表": ["仪表"],
    "传感器": ["传感器"],
    "泵站": ["泵站"],
    "阀门": ["阀门"],
    "管网": ["管网"],
    "管道": ["管道"],
}

#: 性质复合词 → 原子词（"维护维修"= 维护 + 维修）
NATURE_ATOMS = {
    "改造或维修": ["改造", "维修"],
    "维护维修": ["维护", "维修"],
    "维护及维修": ["维护", "维修"],
    "运行维护": ["维护", "运维"],
    "改造": ["改造"],
    "维修": ["维修"],
    "检修": ["检修"],
    "维护": ["维护"],
    "抢修": ["抢修"],
    "运维": ["运维"],
    "新建": ["新建"],
    "安装": ["安装"],
    "检测": ["检测"],
    "试验": ["试验"],
}

#: 从句子里抽对象短语时，要剥掉的**限定语**（时间、电压等级、项数、套话）
_OBJ_QUALIFIER_RE = re.compile(
    r"(?:自\s*\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日|至投标截止时间|"
    r"以合同签订日期为准|不低于|不少于|至少|类似|"
    r"\d{1,4}(?:\.\d+)?\s*(?:kV|KV|kV|千伏)|及以上|以上|及以下|以下|"
    r"电压等级|等级|\d{1,2}\s*项|投标人|须具有|需具有|具有|提供|有效的|有效)"
)

#: 抽出来的词片若是这些套话，不算工作对象
_OBJECT_STOPWORDS = (
    "业绩", "合同", "扫描件", "证明材料", "材料", "项目", "时间", "截止", "要求",
    "内容", "范围", "服务", "工程", "施工", "投标", "证明", "文件", "资料",
)

#: 对象短语内部的连接词（"供电与通信" → 供电 / 通信）
_OBJ_CONNECTORS = ("以及", "并且", "和", "与", "及", "或", "、", "，", ",", "；", ";")

_OBJ_TAIL_MAX = 40   # 性质词之前最多回看多少字


def _atoms(phrases: list[str], table: dict[str, list[str]]) -> list[str]:
    out: list[str] = []
    for p in phrases:
        for a in table.get(p, [p]):
            if a not in out:
                out.append(a)
    return out


def _level_of(text: str) -> Optional[int]:
    m = _LEVEL_RE.search(text)
    return _CN_NUM.get(m.group(1)) if m else None


def _kv_of(text: str) -> Optional[int]:
    vals = [int(m.group(1)) for m in _KV_RE.finditer(text)]
    return max(vals) if vals else None


def classify_qualification(text: str) -> dict:
    """把资格条款原文映射为结构化条件。

    返回 ``{"rule": ..., ...}``；多条件时返回 ``{"rule": "ALL_OF", "checks": [...]}``。
    无法识别时返回 ``{"rule": "UNCLASSIFIED"}``（由引擎记 NOT_EVALUATED 并告警，
    绝不静默当满足）。
    """
    checks: list[dict] = []
    t = text

    # 1) 联合体：只影响"是否允许"，不需要素材
    if "联合体" in t:
        allowed = bool(re.search(r"联合体[^。]{0,12}?(?:是|为)?\s*(?:是|允许|接受)", t))
        if re.search(r"(?:否|不允许|不接受)", t):
            allowed = False
        return {
            "rule": "DECLARATION",
            "consortium_allowed": allowed,
            "evidence": [],
            "location": "三、联合体协议书（如适用）",
            "classified_from": "联合体条款",
        }

    # 2) 业绩：出现"业绩"且带电压等级
    if "业绩" in t and _kv_of(t):
        checks.append(_performance_condition(t))

    # 3) 许可证类（可能多条）
    if "承装" in t and "电力设施许可" in t:
        checks.append(_license_condition(t, "承装（修、试）电力设施许可证"))
    if "施工总承包" in t:
        hint = "电力工程施工总承包" if "电力工程施工总承包" in t else "机电工程施工总承包"
        checks.append(_license_condition(t, hint))
    if "安全生产许可证" in t:
        checks.append({
            "rule": "CERTIFICATE_VALIDITY",
            "material_category": "安全生产许可证",
            "must_cover": "TENDER_DEADLINE",
            "classified_from": "安全生产许可证条款",
            "note": "有效期须覆盖投标截止日",
        })

    # 4) 营业执照 / 法人证书（只在没有更具体条件时单独成立，避免和上面重复）
    if not checks and re.search(r"营业执照|法人证书", t):
        checks.append({
            "rule": "MATERIAL_PRESENCE",
            "material_category": "营业执照",
            "subject": "投标人",
            "classified_from": "营业执照/法人证书条款",
        })
    elif re.search(r"营业执照|法人证书", t) and not any(
            c.get("material_category") == "营业执照" for c in checks):
        # 与许可证同句出现时也要求营业执照（如"具备…资质，具有有效的营业执照"）
        checks.insert(0, {
            "rule": "MATERIAL_PRESENCE",
            "material_category": "营业执照",
            "subject": "投标人",
            "classified_from": "营业执照/法人证书条款",
        })

    if not checks:
        return {"rule": "UNCLASSIFIED", "classified_from": "未识别",
                "note": "未能从条款文字识别出可判定的条件类型"}

    if len(checks) == 1:
        out = dict(checks[0])
        out.setdefault("evidence", _evidence_of(t))
        return out

    return {
        "rule": "ALL_OF",
        "checks": checks,
        "evidence": _evidence_of(t),
        "classified_from": "同一条款含多个条件",
    }


def _license_condition(text: str, hint: str) -> dict:
    level = _level_of(text)
    cond = {
        "rule": "CERTIFICATE_LEVEL",
        "material_category": LICENSE_CATEGORY.get(hint, "资质证书_承装修试"),
        "license": hint,
        "min_level": level if level is not None else None,
        "classified_from": f"许可/资质条款（{hint}）",
    }
    # 承装修试常写成"承修、承试类三级或以上"，需要额外的类别校验。
    # 关键：只在"…类"这个类别清单语境里取词。许可证名称"承装（修、试）电力设施许可证"
    # 里也有"承装"二字，但那不是对类别的要求——按裸词抓会把"承装"也当成必需类别，
    # 白白把门槛抬高一档。
    # 显式给出（可能为空列表）：空列表表示"条款未限定类别"，而不是"没解析到"
    cond["classes"] = _class_list_of(text) if hint.startswith("承装") else []
    return cond


def _class_list_of(text: str) -> list[str]:
    """抽取"承修、承试类"这类类别清单。"""
    m = re.search(r"((?:承[装修试][、,及和]?)+)类", text)
    if not m:
        return []
    return [c for c in ("承装", "承修", "承试") if c in m.group(1)]


def _performance_condition(text: str) -> dict:
    """从业绩条款里抽出：起始日期、最少项数、电压门槛、对象、性质。"""
    not_before = None
    m = _MIN_DATE_RE.search(text)
    if m:
        not_before = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    else:
        m = _BEFORE_DATE_RE.search(text)
        if m:
            not_before = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    count = 1
    m = _MIN_COUNT_RE.search(text)
    if m:
        count = max(1, int(m.group(1)))

    objects = [w for w in OBJECT_LEXICON if w in text]
    natures = [w for w in NATURE_LEXICON if w in text]
    if objects:
        keywords, source = _atoms(objects, OBJECT_ATOMS), "lexicon"
    else:
        keywords = _objects_from_clause(text)
        source = "clause_derived" if keywords else "unresolved"

    return {
        "rule": "PERFORMANCE_FIVE_ELEMENTS",
        "not_before": not_before or "2023-01-01",
        "date_basis": "合同签订日期",
        "min_kv": _kv_of(text),
        "object_keywords": keywords,
        #: 对象词的来源。``lexicon``＝通用词表命中（可据此判"不满足"）；
        #: ``clause_derived``＝从句子里推断（合同未出现该表述时只能判"证据不足"）；
        #: ``unresolved``＝没推出来（引擎判"证据不足"，绝不默认成某个设备名）。
        "object_keywords_source": source,
        "nature_keywords": _atoms(natures, NATURE_ATOMS),
        "object_phrases": objects,
        "nature_phrases": natures,
        "min_count": count,
        "classified_from": "业绩条款",
        "evidence": _evidence_of(text),
    }


def _objects_from_clause(text: str) -> list[str]:
    """词表覆盖不到时，从业绩条款句子里抽工作对象短语。

    中文里"…X 改造或维修…"的 X 就是工作对象，所以取**性质词之前**的那一小句，
    剥掉电压等级/项数/时间等限定语，再按连接词切成词片。

    例：「须具有1 项10kV 以上电压等级供水管网监测终端野外供电与通信改造或维修业绩」
        → ``["供水管网监测终端野外供电", "通信"]``

    这是**推断**，不是权威口径：调用方必须把来源标成 ``clause_derived``，
    合同里没用同样表述时只能判"证据不足"，不能据此判"不满足"。
    """
    idx = min((text.find(n) for n in NATURE_LEXICON if n in text), default=-1)
    if idx < 0:
        return []
    # 对象短语与性质词同句：先取性质词之前的一小段，再切到最后一个小句
    head = re.split(r"[，。；、\s]", text[:idx][-_OBJ_TAIL_MAX:])[-1]
    head = _OBJ_QUALIFIER_RE.sub("", head)
    chunks = [head]
    for connector in _OBJ_CONNECTORS:
        chunks = [piece for ch in chunks for piece in ch.split(connector)]
    out: list[str] = []
    for ch in chunks:
        ch = ch.strip("（）()《》\"'“” 　-—_")
        if len(ch) < 2 or ch in _OBJECT_STOPWORDS or ch in out:
            continue
        out.append(ch)
    return out


def _evidence_of(text: str) -> list[str]:
    """条款里要求的证明材料类别（用于矩阵"需提供证据"列）。"""
    out: list[str] = []
    if "合同扫描件" in text or "合同" in text:
        out.append("业绩合同")
    for kw, cat in (("营业执照", "营业执照"), ("资质证书", "资质证书"),
                    ("安全生产许可证", "安全生产许可证"),
                    ("财务", "财务审计报告"), ("信用", "信用查询")):
        if kw in text and cat not in out:
            out.append(cat)
    return out
