"""Gold oracle 度量器。

职责：把编译器产物与人工建立的 Gold 期望值对账，算出可量化的覆盖率与准确率。

架构约束（用户明确要求）：
* Gold 期望值放在 ``tests/gold/<项目>/``，**src/ 绝不读取**；
* 本模块属于测试侧，只单向依赖 src（导入编译器产物解析出的 JSON），不反向被 src 依赖；
* 度量结果同时输出 JSON（机器用）与 Markdown（人看）。

度量定义：
* **recall**：Gold 中某条要求是否被抽出——把期望 tokens 与抽取结果的 source_text
  都做归一化后，要求包含全部 tokens；
* **page accuracy**：在已抽出的条目里，source_page 是否落在期望页集合内；
* **type/severity accuracy**：在已抽出的条目里，分类是否正确。

recall 用"是否存在任一条抽取结果满足 tokens"判定，因此对 id 命名差异免疫——
id 是编译器内部编号，不是事实；原文才是事实。
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Iterable, Optional

GOLD_DIR = Path(__file__).resolve().parent

#: oracle 存放位置（按优先级）：
#:   ① tests/gold/<name>/            —— 可公开的**合成**示例 oracle
#:   ② projects/_local_oracles/<name>/ —— 真实项目 oracle（PRIVATE_TEST_DATA，不入库）
#: 真实项目 oracle 含招标条款原文与材料 ID，只在本机存在，公开环境下自动不可用。
GOLD_DIRS = (
    GOLD_DIR,
    GOLD_DIR.parents[1] / "projects" / "_local_oracles",
)


# --------------------------------------------------------------------------- #
# 归一化
# --------------------------------------------------------------------------- #
_PUNCT = {
    "，": ",", "。": ".", "、": ",", "；": ";", "：": ":", "（": "(", "）": ")",
    "“": '"', "”": '"', "‘": "'", "’": "'", "－": "-", "—": "-", "～": "~",
    "％": "%", "／": "/", "《": "<", "》": ">",
}


def norm(text: str) -> str:
    """去空白 + 全角转半角 + 标点统一。两侧都用它，避免表示差异造成假阴性。"""
    if not text:
        return ""
    out = []
    for ch in text:
        if ch.isspace():
            continue
        ch = _PUNCT.get(ch, ch)
        if 0xFF01 <= ord(ch) <= 0xFF5E:
            ch = chr(ord(ch) - 0xFEE0)
        elif ch == "\u3000":
            continue
        out.append(ch)
    return unicodedata.normalize("NFKC", "".join(out))


def contains_all(haystack: str, tokens: Iterable[str]) -> bool:
    h = norm(haystack)
    return all(norm(t) in h for t in tokens)


# --------------------------------------------------------------------------- #
# 载入
# --------------------------------------------------------------------------- #
class OracleNotFound(FileNotFoundError):
    """oracle 不存在——真实项目 oracle 不入库，公开环境下属正常情况。"""


def oracle_path(project: str, name: str) -> Path | None:
    for root in GOLD_DIRS:
        p = root / project / f"{name}.expected.json"
        if p.is_file():
            return p
    return None


def load_oracle(project: str, name: str) -> dict:
    p = oracle_path(project, name)
    if p is None:
        raise OracleNotFound(
            f"缺少 oracle {project}/{name}（真实项目 oracle 属 PRIVATE_TEST_DATA，不入库）")
    return json.loads(p.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# project.json
# --------------------------------------------------------------------------- #
def score_project(project_json: dict, oracle: dict) -> dict:
    fields = project_json.get("fields", {})
    rows = []
    for name, spec in oracle["fields"].items():
        got = fields.get(name) or {}
        value = got.get("value")
        value_norm = got.get("value_normalized")
        page = got.get("source_page")
        check = spec["check"]
        ok = False
        detail = ""

        if check == "must_contain_all":
            ok = value is not None and contains_all(str(value), spec["tokens"])
            detail = f"值={value!r}"
        elif check == "must_contain_any":
            ok = value is not None and any(norm(t) in norm(str(value)) for t in spec["tokens"])
            detail = f"值={value!r}"
        elif check == "expected_bool":
            ok = value_norm is spec["value"]
            detail = f"值={value!r} 归一={value_norm!r}"
        else:  # pragma: no cover
            raise ValueError(f"未知 check：{check}")

        page_ok: Optional[bool] = None
        if "pages" in spec:
            page_ok = page in spec["pages"]
            if ok and not page_ok:
                detail += f" 页码={page}（期望 {spec['pages']}）"

        rows.append({
            "field": name,
            "check": check,
            "ok": bool(ok),
            "page_ok": page_ok,
            "required": spec.get("required", True),
            "page": page,
            "expected_pages": spec.get("pages"),
            "value": value,
            "detail": detail,
        })
    required = [r for r in rows if r["required"]]
    return {
        "total": len(rows),
        "passed": sum(1 for r in rows if r["ok"]),
        "required_total": len(required),
        "required_passed": sum(1 for r in required if r["ok"]),
        "fields": rows,
    }


# --------------------------------------------------------------------------- #
# requirements.json
# --------------------------------------------------------------------------- #
def score_requirements(requirements: list[dict], oracle: dict) -> dict:
    def bucket(severity: str) -> list[dict]:
        return oracle.get(severity.lower(), [])

    out: dict = {"by_severity": {}, "misses": [], "matched": []}
    for severity in ("HARD", "SCORE", "NORMAL"):
        expected = bucket(severity)
        found, page_hits, type_hits = 0, 0, 0
        rows = []
        for exp in expected:
            # 候选可能不止一个：评分项的 source_text 会带上一段"评分标准"正文，
            # 其中常含下一条的名称。因此不能"命中即取第一个"，要选**最贴题**的那个：
            # 关键词出现位置最靠前的（名称在开头的那条才是它本身）。
            best, best_pos = None, None
            for req in requirements:
                if req.get("severity") != severity:
                    continue
                text = norm(req.get("source_text") or "")
                if not contains_all(text, exp["tokens"]):
                    continue
                pos = min(text.find(norm(t)) for t in exp["tokens"] if norm(t) in text)
                if best_pos is None or pos < best_pos:
                    best, best_pos = req, pos
            hit = best
            if hit:
                found += 1
                pages = set(exp.get("pages") or [exp.get("page")])
                pages |= set(exp.get("alt_pages") or [])
                page_ok = hit.get("source_page") in pages
                page_hits += int(page_ok)
                type_ok = hit.get("requirement_type") == exp.get("type")
                type_hits += int(type_ok)
                rows.append({"id": exp["id"], "matched": hit["id"],
                             "page": hit.get("source_page"), "page_ok": page_ok,
                             "type": hit.get("requirement_type"),
                             "type_ok": type_ok})
            else:
                rows.append({"id": exp["id"], "matched": None, "page_ok": False,
                             "type_ok": False})
                out["misses"].append({"severity": severity, "id": exp["id"],
                                      "tokens": exp["tokens"],
                                      "expected_pages": exp.get("pages", [exp.get("page")]),
                                      "note": exp.get("note")})
        total = len(expected)
        out["by_severity"][severity] = {
            "expected": total,
            "found": found,
            "recall": round(found / total, 4) if total else None,
            "page_accuracy": round(page_hits / found, 4) if found else None,
            "type_accuracy": round(type_hits / found, 4) if found else None,
            "rows": rows,
        }
        out["matched"].extend(rows)
    return out


# --------------------------------------------------------------------------- #
# qualification_matrix.json
# --------------------------------------------------------------------------- #
def score_qualification(matrix: dict, oracle: dict, *, materials: list[dict]) -> dict:
    entries = {e["requirement_id"]: e for e in matrix.get("entries", [])}
    results: dict = {"verdicts": [], "errors": []}

    for rid, spec in oracle["qualification_verdicts"].items():
        entry = entries.get(rid)
        if entry is None:
            results["errors"].append({"requirement": rid, "kind": "MISSING",
                                      "detail": "矩阵中没有该要求"})
            results["verdicts"].append({"requirement": rid, "ok": False, "got": None,
                                        "expect": spec["expect"]})
            continue
        got = entry["status"]
        ok = got == spec["expect"]
        results["verdicts"].append({"requirement": rid, "ok": ok, "got": got,
                                    "expect": spec["expect"], "reason": spec.get("reason")})
        if not ok:
            results["errors"].append({"requirement": rid, "kind": "WRONG_VERDICT",
                                      "expect": spec["expect"], "got": got,
                                      "detail": entry.get("reason")})

    # 业绩逐项：必须 FAIL 的 / 可以选的
    perf = oracle["qualification_verdicts"].get("QUAL-3-3", {})
    entry = entries.get("QUAL-3-3") or {}
    cands = {c["material_id"]: c for c in entry.get("candidates", [])}
    perf_rows = []
    for mid, why in (perf.get("must_be_fail") or {}).items():
        c = cands.get(mid)
        got = c["status"] if c else "NOT_EVALUATED"
        ok = got == "FAIL"
        perf_rows.append({"material": mid, "expect": "FAIL", "got": got, "ok": ok, "why": why})
        if not ok:
            results["errors"].append({"requirement": "QUAL-3-3", "kind": "WRONG_PERF_VERDICT",
                                      "material": mid, "expect": "FAIL", "got": got, "detail": why})
    for mid, why in (perf.get("ambiguous_must_not_select") or {}).items():
        c = cands.get(mid)
        got = c["status"] if c else "NOT_EVALUATED"
        selected = entry.get("selected") == mid
        ok = not selected and got in ("FAIL", "EVIDENCE_INSUFFICIENT", "NOT_EVALUATED")
        perf_rows.append({"material": mid, "expect": "FAIL_or_INSUFFICIENT", "got": got,
                          "ok": ok, "why": why})
        if not ok:
            results["errors"].append({"requirement": "QUAL-3-3", "kind": "WRONG_PERF_VERDICT",
                                      "material": mid, "expect": "不得选为资格业绩", "got": got,
                                      "detail": why})
    selected = entry.get("selected")
    allowed = perf.get("selected_in")
    if allowed:
        ok = selected in allowed
        perf_rows.append({"material": "(selected)", "expect": f"∈{allowed}", "got": selected, "ok": ok})
        if not ok:
            results["errors"].append({"requirement": "QUAL-3-3", "kind": "WRONG_SELECTION",
                                      "expect": allowed, "got": selected})
    results["performance"] = perf_rows

    # 商务类期望
    comm_rows = []
    for rid, spec in (oracle.get("commercial_expectations") or {}).items():
        if rid.startswith("_") or not isinstance(spec, dict):
            continue  # oracle 里的说明字段，不是可断言的期望
        entry = entries.get(rid)
        got = entry["status"] if entry else None
        ok = got == spec["expect"]
        comm_rows.append({"requirement": rid, "expect": spec["expect"], "got": got, "ok": ok})
        if not ok:
            results["errors"].append({"requirement": rid, "kind": "WRONG_VERDICT",
                                      "expect": spec["expect"], "got": got})
    # 客观评分项 oracle（GPT 1.5.3）：检查 eligible/selected/excluded
    score_rows = []
    for rid, spec in (oracle.get("score_objective") or {}).items():
        if rid.startswith("_") or not isinstance(spec, dict):
            continue
        entry = entries.get(rid)
        if entry is None:
            score_rows.append({"item": rid, "ok": False, "got": "矩阵中无此要求"})
            continue
        cands = {c["material_id"]: c for c in entry.get("candidates", [])}
        eligible = sorted(c["material_id"] for c in entry.get("candidates", [])
                          if c.get("eligibility") == "SCORE_ELIGIBLE"
                          and c.get("status") == "PASS")
        exp_elig = sorted(spec.get("expected_eligible_materials") or [])
        ok_elig = eligible == exp_elig
        excluded_ok = all(
            (cands.get(mid) or {}).get("status") != "PASS"
            for mid in spec.get("expected_excluded_must_not_pass") or [])
        exp_sel = spec.get("expected_selected")
        ok_sel = entry.get("selected") == exp_sel
        ok = ok_elig and excluded_ok and ok_sel
        score_rows.append({
            "item": rid, "ok": ok,
            "eligible": eligible, "expected_eligible": exp_elig,
            "selected": entry.get("selected"), "expected_selected": exp_sel,
            "excluded_ok": excluded_ok,
        })

    results["commercial"] = comm_rows
    results["score_objective"] = score_rows
    results["score_objective_errors"] = sum(1 for r in score_rows if not r["ok"])

    # 资格类不得出现"错误 PASS"：任何 HARD 且 expect=PASS 的项若 got!=PASS 就是错 FAIL；
    # 反之 expect!=PASS 的项若 got=PASS 就是错 PASS。上面 verdicts 已覆盖，这里统计计数。
    results["false_pass_count"] = sum(
        1 for v in results["verdicts"]
        if v["got"] == "PASS" and v["expect"] != "PASS"
    )
    results["false_fail_count"] = sum(
        1 for v in results["verdicts"]
        if v["got"] in ("FAIL", "EVIDENCE_INSUFFICIENT") and v["expect"] == "PASS"
    )
    return results


# --------------------------------------------------------------------------- #
# bid_status
# --------------------------------------------------------------------------- #
def score_bid_status(project_json: dict, matrix: dict, oracle: dict,
                     *, materials_before: int, materials_after: int) -> dict:
    entries = {e["requirement_id"]: e for e in matrix.get("entries", [])}
    rows = []

    for spec in oracle.get("must_be_resolved_by_compiler", []):
        key = spec["key"] if isinstance(spec, dict) else spec.split(" ")[0]
        label = spec.get("label", key) if isinstance(spec, dict) else spec
        entry = entries.get(key)
        if entry is None:
            rows.append({"item": f"{key} {label}", "ok": False, "detail": "矩阵中无此要求"})
            continue
        ok = entry["status"] == "PASS"
        rows.append({"item": f"{key} {label}", "ok": ok, "got": entry["status"]})

    # 公司侧待办有两路来源：① 要求级 WAIT_COMPANY；② 素材库自标 PENDING_COMPANY
    pending_materials = {m["material_id"] for m in (matrix.get("_materials_pending") or [])}
    for spec in oracle.get("must_be_wait_company", []):
        if isinstance(spec, dict) and spec.get("material"):
            mid = spec["material"]
            ok = mid in pending_materials
            rows.append({"item": f"{mid} {spec.get('item','')}", "ok": ok,
                         "got": "PENDING" if ok else "缺该素材待办",
                         "why": spec.get("why")})
            continue
        key = spec["key"] if isinstance(spec, dict) else spec.split(" ")[0]
        item = spec.get("item", key) if isinstance(spec, dict) else spec
        entry = entries.get(key)
        got = entry["status"] if entry else None
        ok = got == "WAIT_COMPANY"
        rows.append({"item": f"{key} {item}", "ok": ok, "got": got, "why": spec.get("why")})

    blob = json.dumps(matrix, ensure_ascii=False)
    fabrications = [p for p in oracle.get("no_fabrication_invariants", {})
                    .get("forbidden_patterns_in_outputs", []) if p in blob]
    # 素材库条目数必须不变（编译器不得凭空造素材）
    stable = materials_before == materials_after
    return {
        "rows": rows,
        "passed": sum(1 for r in rows if r["ok"]),
        "total": len(rows),
        "fabrication_hits": fabrications,
        "fabrication_count": len(fabrications),
        "materials_stable": stable,
        "materials_before": materials_before,
        "materials_after": materials_after,
    }


# --------------------------------------------------------------------------- #
# 报告
# --------------------------------------------------------------------------- #
def summarize(project_score: dict, req_score: dict, qual_score: dict,
              status_score: dict) -> dict:
    hard = req_score["by_severity"]["HARD"]
    return {
        "project_required_recall": round(
            project_score["required_passed"] / project_score["required_total"], 4)
        if project_score["required_total"] else None,
        "HARD_recall": hard["recall"],
        "HARD_page_accuracy": hard["page_accuracy"],
        "HARD_type_accuracy": hard["type_accuracy"],
        "SCORE_recall": req_score["by_severity"]["SCORE"]["recall"],
        "SCORE_page_accuracy": req_score["by_severity"]["SCORE"]["page_accuracy"],
        "NORMAL_recall": req_score["by_severity"]["NORMAL"]["recall"],
        "NORMAL_page_accuracy": req_score["by_severity"]["NORMAL"]["page_accuracy"],
        "qualification_errors": len(qual_score["errors"]),
        "score_objective_errors": qual_score.get("score_objective_errors", 0),
        "false_pass": qual_score["false_pass_count"],
        "false_fail": qual_score["false_fail_count"],
        "bid_status_passed": status_score["passed"],
        "bid_status_total": status_score["total"],
        "fabrication_count": status_score["fabrication_count"],
        "materials_stable": status_score["materials_stable"],
    }


def render_markdown(run_id: str, summary: dict, project_score: dict, req_score: dict,
                    qual_score: dict, status_score: dict) -> str:
    lines = [
        f"# Gold Oracle 评分 —— {run_id}",
        "",
        "## 总览",
        "",
        "| 指标 | 值 | Gate |",
        "|---|---|---|",
        f"| project 字段命中 | 必检 {project_score['required_passed']}/{project_score['required_total']}"
        f"（全表 {project_score['total']} 项） | — |",
        f"| **HARD recall** | **{_pct(summary['HARD_recall'])}** | = 100% |",
        f"| HARD 页码准确率 | {_pct(summary['HARD_page_accuracy'])} | — |",
        f"| HARD 分类准确率 | {_pct(summary['HARD_type_accuracy'])} | — |",
        f"| SCORE recall | {_pct(summary['SCORE_recall'])} | 量化即可 |",
        f"| SCORE 页码准确率 | {_pct(summary['SCORE_page_accuracy'])} | — |",
        f"| NORMAL recall | {_pct(summary['NORMAL_recall'])} | 量化即可 |",
        f"| NORMAL 页码准确率 | {_pct(summary['NORMAL_page_accuracy'])} | — |",
        f"| 资格判定错误 | {summary['qualification_errors']} | = 0 |",
        f"| 客观评分项错误 | {summary['score_objective_errors']} | = 0 |",
        f"| 资格错误 PASS | {summary['false_pass']} | = 0 |",
        f"| 资格错误 FAIL | {summary['false_fail']} | = 0 |",
        f"| 投标状态核对 | {status_score['passed']}/{status_score['total']} | — |",
        f"| 公司事实编造 | {summary['fabrication_count']} | = 0 |",
        f"| 素材库条目稳定 | {'是' if summary['materials_stable'] else '否'} | 是 |",
        "",
    ]
    if req_score["misses"]:
        lines += ["## 漏抽明细（按严重度）", ""]
        for m in req_score["misses"]:
            lines.append(f"- **{m['severity']} {m['id']}** 期望页 {m['expected_pages']}｜"
                         f"tokens={'/'.join(m['tokens'])}")
            if m.get("note"):
                lines.append(f"  - {m['note']}")
        lines.append("")
    else:
        lines += ["## 漏抽明细", "", "无漏抽。", ""]

    if qual_score["errors"]:
        lines += ["## 资格判定错误", ""]
        for e in qual_score["errors"]:
            lines.append(f"- `{e['requirement']}` {e['kind']}：期望 {e.get('expect')}，"
                         f"实际 {e.get('got')}｜{e.get('detail', '')}")
        lines.append("")

    lines += ["## 逐字段（project）", "", "| 字段 | 结果 | 页 | 期望页 | 值 |", "|---|---|---|---|---|"]
    for r in project_score["fields"]:
        mark = "✓" if r["ok"] else ("—" if not r["required"] else "✗")
        lines.append(f"| {r['field']} | {mark} | {r['page']} | {r['expected_pages']} | "
                     f"{str(r['value'])[:40]} |")
    lines.append("")
    lines += ["## 逐条（HARD）", "", "| Oracle | 匹配到的要求 | 页 | 页✓ | 类型✓ |", "|---|---|---|---|---|"]
    for r in req_score["by_severity"]["HARD"]["rows"]:
        lines.append(f"| {r['id']} | {r['matched'] or '—'} | {r.get('page')} | "
                     f"{'✓' if r['page_ok'] else '✗'} | {'✓' if r['type_ok'] else '✗'} |")
    lines.append("")
    lines += ["## 逐条（SCORE）", "", "| Oracle | 匹配到的要求 | 页 | 页✓ | 类型✓ |", "|---|---|---|---|---|"]
    for r in req_score["by_severity"]["SCORE"]["rows"]:
        lines.append(f"| {r['id']} | {r['matched'] or '—'} | {r.get('page')} | "
                     f"{'✓' if r['page_ok'] else '✗'} | {'✓' if r['type_ok'] else '✗'} |")
    lines.append("")
    lines += ["## 逐条（NORMAL，仅列页码不符者）", "",
              "| Oracle | 匹配到的要求 | 页 | 页✓ |", "|---|---|---|---|"]
    for r in req_score["by_severity"]["NORMAL"]["rows"]:
        if not r["page_ok"]:
            lines.append(f"| {r['id']} | {r['matched'] or '—'} | {r.get('page')} | ✗ |")
    lines.append("")
    lines += ["## 投标状态核对", "", "| 项 | 结果 | 实际 | 说明 |", "|---|---|---|---|"]
    for r in status_score["rows"]:
        lines.append(f"| {r['item'][:48]} | {'✓' if r['ok'] else '✗'} | {r.get('got') or '—'} | "
                     f"{(r.get('why') or r.get('detail') or '')[:40]} |")
    lines.append("")
    return "\n".join(lines)


def _pct(x) -> str:
    return "—" if x is None else f"{x * 100:.1f}%"
