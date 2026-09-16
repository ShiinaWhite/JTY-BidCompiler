"""选择结果与投标状态两个正式产物。

进入 Word 装配（MVP-6～9）之前必须把"选了什么、为什么、还差什么"固化成文件：

* ``selected_materials.json`` —— 每条要求选定哪份素材（含三分法归属）；
  **装配层只能消费这个结果，不允许自己重新做资格判断**。
  否则装配脚本里会长出第二套资格逻辑，两边迟早判得不一样。
* ``bid_status.json`` —— 哪些已闭环、哪些等公司、哪些绝不允许编译器代做。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

#: 三分法：一份真实业绩合同可以是什么角色
ELIGIBILITY = {
    "QUALIFICATION_ELIGIBLE": "可用于满足资格要求（进入六（二）资格证明与七（三）业绩）",
    "SCORE_ELIGIBLE": "满足资格同口径，可用于商务评分加分（每项 1 分）",
    "SHOWCASE_ONLY": "仅作企业业绩展示（七（三）），不得计入资格或评分",
}

#: 编译器绝不代做的公司真实事项
NEVER_AUTO_FILL = [
    "投标报价金额与税率",
    "拟派人员姓名/岗位/职称",
    "保证金缴纳凭证",
    "签章与签署日期",
    "任何未在素材库中核验过的证书编号或有效期",
]


def build_selection(matrix: dict, requirements: list[dict], materials: list[dict],
                    *, project_title: str = "", bidder: str = "") -> dict:
    req_by_id = {r["id"]: r for r in requirements}
    mat_by_id = {m["material_id"]: m for m in materials}

    entries = []
    for entry in matrix.get("entries", []):
        req = req_by_id.get(entry["requirement_id"], {})
        selected = entry.get("selected")
        mat = mat_by_id.get(selected) if selected else None
        entries.append({
            "requirement_id": entry["requirement_id"],
            "requirement_type": entry.get("requirement_type"),
            "severity": entry.get("severity"),
            "status": entry["status"],
            "role": entry.get("role") or _role_of(entry, req),
            "selected": selected,
            "selected_title": mat.get("title") if mat else None,
            "eligibility": _eligibility_of(entry, mat),
            "evidence_refs": [s.get("source_ref") for s in (mat or {}).get("evidence_spans") or []][:8],
            "reason": entry.get("reason"),
            "owner": entry.get("owner"),
        })

    # 业绩三分法汇总：一份合同是真实业绩 ≠ 它能算资格/评分业绩
    perf_entries = [e for e in matrix.get("entries", [])
                    if e.get("requirement_type") == "PERFORMANCE" and e.get("candidates")]
    qual_entry = next((e for e in perf_entries
                       if (e.get("role")
                           or _role_of(e, req_by_id.get(e["requirement_id"], {}))) == "QUALIFICATION"),
                      None)
    tri = {"QUALIFICATION_ELIGIBLE": [], "SCORE_ELIGIBLE": [], "SHOWCASE_ONLY": []}
    if qual_entry:
        passing = [c["material_id"] for c in qual_entry["candidates"] if c["status"] == "PASS"]
        selected = qual_entry.get("selected")
        for mid in passing:
            tri["QUALIFICATION_ELIGIBLE" if mid == selected else "SCORE_ELIGIBLE"].append(mid)
        for c in qual_entry["candidates"]:
            if c["status"] != "PASS":
                tri["SHOWCASE_ONLY"].append(c["material_id"])
        tri["_rule"] = ("资格可用=满足招标资格口径且被选为资格业绩；评分可用=同样满足口径的其他业绩"
                        "（招标通常规定'每增加1项有效业绩加1分'）；"
                        "其余真实业绩仅作企业展示，不得计入资格或评分")
    # 自带独立口径的评分业绩项（原文未声明"同资格要求"）：它判出来的业绩属"评分可用"，
    # 但绝不能混进"资格可用"——那等于把没通过资格口径的业绩说成资格业绩。
    for e in perf_entries:
        if e is qual_entry:
            continue
        for c in e["candidates"]:
            mid = c["material_id"]
            if (c["status"] == "PASS" and mid not in tri["SCORE_ELIGIBLE"]
                    and mid not in tri["QUALIFICATION_ELIGIBLE"]):
                tri["SCORE_ELIGIBLE"].append(mid)

    return {
        "schema_version": "1.0",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "project": {"title": project_title, "bidder": bidder},
        "eligibility_lexicon": ELIGIBILITY,
        "note": "装配层只能消费本文件的选择结果，不得自行重做资格判断。",
        "entries": entries,
        "performance_eligibility": tri,
    }


def build_bid_status(matrix: dict, requirements: list[dict], project: dict,
                     *, project_title: str = "", materials: list[dict] | None = None) -> dict:
    req_by_id = {r["id"]: r for r in requirements}
    fields = project.get("fields", {})
    deadline = (fields.get("deadline") or {}).get("value_normalized") or \
               (fields.get("deadline") or {}).get("value")

    closed, waiting, compiler_todo, hard_fail = [], [], [], []
    for entry in matrix.get("entries", []):
        req = req_by_id.get(entry["requirement_id"], {})
        row = {
            "requirement_id": entry["requirement_id"],
            "severity": entry.get("severity"),
            "requirement_type": entry.get("requirement_type"),
            "summary": (req.get("source_text") or "")[:80],
            "reason": entry.get("reason"),
        }
        status = entry["status"]
        if status == "FAIL" and entry.get("severity") == "HARD":
            hard_fail.append(row)
        elif status == "PASS":
            closed.append(row)
        elif entry.get("owner") == "COMPILER":
            compiler_todo.append(row)
        elif status in ("WAIT_COMPANY", "EVIDENCE_INSUFFICIENT"):
            waiting.append({**row,
                            "missing_info": entry.get("missing_info") or [],
                            "owner": entry.get("owner") or "COMPANY"})

    # 缺口不只有"要求"这一路来源：素材库自己标了 PENDING_COMPANY 的条目
    # （如设备台账、环境体系证书）无论本项目有没有对应评分项，都是公司侧待办。
    materials_pending = []
    for m in materials or []:
        if m.get("verification_status") == "PENDING_COMPANY":
            materials_pending.append({
                "material_id": m["material_id"],
                "category": m.get("category"),
                "title": m.get("title"),
                "notes": m.get("notes"),
            })

    summary = matrix.get("summary", {})
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "project": {"title": project_title, "deadline": deadline},
        "gate": {
            "hard_fail": len(hard_fail),
            "hard_pass": len([c for c in closed if c["severity"] == "HARD"]),
            "wait_company": len(waiting),
            "compiler_todo": len(compiler_todo),
            "verdict_summary": summary,
            "submittable": len(hard_fail) == 0 and len(waiting) == 0,
        },
        "closed": closed,
        "hard_fail": hard_fail,
        "waiting_company": waiting,
        "materials_pending": materials_pending,
        "compiler_todo": compiler_todo,
        "never_auto_fill": NEVER_AUTO_FILL,
        "disclaimer": ("报价、人员、保证金、签章属公司真实事项，编译器只做完整性检查，"
                       "不代填、不虚构。"),
    }


def _role_of(entry: dict, req: dict) -> str:
    sev = entry.get("severity")
    if sev == "HARD":
        return "QUALIFICATION"
    if sev == "SCORE":
        return "SCORE"
    if sev == "NORMAL":
        return "RESPONSE"
    return "INFO"


def _eligibility_of(entry: dict, material: dict | None) -> str | None:
    """entry 级三分法归属。

    v1.5.3-hotfix：之前把"候选 status==PASS"一律映射为 QUALIFICATION_ELIGIBLE，
    导致**评分业绩被错标为资格可用**（资格占用与评分新增混为一谈）。
    现在优先读候选自带的 eligibility 字段（由 performance scorer 按资格占用/评分可用判定），
    仅在无该字段时才按角色兜底。
    """
    if material is None:
        return None
    selected = entry.get("selected")
    for c in entry.get("candidates") or []:
        if c.get("material_id") == selected:
            # 评分处理器已按"资格占用 / 评分可用 / 仅展示"标好——直接信任
            if c.get("eligibility"):
                return c["eligibility"]
            break
    # 兜底：资格条目 PASS → 资格可用
    if entry.get("requirement_type") == "PERFORMANCE":
        return "QUALIFICATION_ELIGIBLE" if entry.get("status") == "PASS" else "SHOWCASE_ONLY"
    return None


def write_json(data: dict, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p
