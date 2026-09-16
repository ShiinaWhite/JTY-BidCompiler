"""要求 × 素材 → 资格矩阵（MVP-4）。

结构固定为四步，任何一条要求都必须走完：::

    requirement → candidate materials → evidence → verdict

判定词封闭为五种状态（与 requirements.schema.json 一致）：::

    PASS                 满足（有素材、有证据、且在有效期内）
    FAIL                 明确不满足（素材存在但条件不成立）
    EVIDENCE_INSUFFICIENT 证据不足（素材在，但关键原文/字段缺失）
    WAIT_COMPANY         等公司提供（缺口在真实材料，编译器无能为力）
    NOT_EVALUATED        规则引擎不判（内容质量类，属生成/人工评审范围）

最重要的分工：**"缺"和"不合规"是两件事**。缺 → WAIT_COMPANY；有但不达标 → FAIL。
把这两者混在一起，公司看不出该去补材料还是该换方案。
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from ..adapters.repository import MaterialRepository
from .performance import (
    ElementResult,
    FiveElementEvaluator,
    NOT_SATISFIED,
    _parse_date,
)

PASS, FAIL, EVID, WAIT, NOTEVAL = (
    "PASS", "FAIL", "EVIDENCE_INSUFFICIENT", "WAIT_COMPANY", "NOT_EVALUATED",
)

#: 判定词表（沿用人工核验口径；禁止模糊词）
VERDICT_LEXICON = {
    "PASS": "满足",
    "FAIL": "明确不满足",
    "EVIDENCE_INSUFFICIENT": "证据不足，不能认定",
    "WAIT_COMPANY": "待公司提供",
    "NOT_EVALUATED": "未评估",
}

#: 不参与判定的素材状态
_UNUSABLE = {"REJECTED", "EXPIRED"}

#: 可作为 PASS 候选的素材核验状态（项目时点有效性由 _as_of_status 再判）
_PASS_ELIGIBLE = {"VERIFIED", "PARTIALLY_VERIFIED"}


def _status_allows_pass(status: str) -> bool:
    """单一事实源：某项目时点状态能否作为 PASS 候选。

    v1.5.1 的教训：_pass_eligible 认了 VALID_AS_OF_PROJECT，match() 的护栏
    却还在用 ``status not in _PASS_ELIGIBLE`` 判断，结果存在"有效证件被自己的护栏拦掉"的真实事故。
    所有判断必须走这一个函数。
    """
    return status in _PASS_ELIGIBLE or status == "VALID_AS_OF_PROJECT"



class RequirementMatcher:
    def __init__(
        self,
        repo: MaterialRepository,
        *,
        rules_path: str | Path,
        perf_rules_path: str | Path,
        project: dict | None = None,
        bidder: str,  # 必填：投标人法定名称由 profile 提供，代码里不留真实公司名
    ):
        self.repo = repo
        self.rules = json.loads(Path(rules_path).read_text(encoding="utf-8"))
        self._rules = self.rules
        self.perf_rules_path = perf_rules_path
        self.project = project or {}
        self.bidder = bidder
        self.deadline = self._deadline()
        self.evaluator = FiveElementEvaluator.from_rules_file(
            perf_rules_path, deadline=self.deadline
        )

    # ------------------------------------------------------------------ #
    def _deadline(self) -> Optional[str]:
        f = (self.project.get("fields") or {}).get("deadline") or {}
        value = f.get("value_normalized") or f.get("value")
        return str(value)[:10] if value else None

    def _as_of_status(self, material: dict) -> str:
        """素材在**本项目时点**的有效状态。

        素材上的静态 EXPIRED 只说明"在它来源的那个项目已过期"；证件是否有效
        必须以**当前项目的投标截止日**重新判定：同一张证书在截止日较早的项目里
        仍然有效，在截止日较晚的项目里已经过期（判别测试见 tests/gold）。
        """
        raw = material.get("verification_status")
        exp = _parse_date(material.get("expiry_date"))
        if exp is not None and self.deadline:
            dl = _parse_date(self.deadline)
            if dl and exp >= dl and raw == "EXPIRED":
                return "VALID_AS_OF_PROJECT"
            if dl and exp < dl:
                return "EXPIRED_AS_OF_PROJECT"
        return raw or "UNKNOWN"

    def _pass_eligible(self, materials: list[dict]) -> list[dict]:
        """可作为 PASS 候选的素材：内容已核验，且在**本项目时点**有效。"""
        return [m for m in materials if _status_allows_pass(self._as_of_status(m))]

    def _grouped(self, materials: list[dict]) -> dict:
        """把素材按项目时点状态分组（供候选列表明细）。"""
        groups: dict[str, list[dict]] = {}
        for m in materials:
            groups.setdefault(self._as_of_status(m), []).append(m)
        return groups

    # ------------------------------------------------------------------ #
    def match(self, requirements: list[dict]) -> dict:
        self._req_by_id = {r["id"]: r for r in requirements}
        # 业绩判定按 **requirement_id** 缓存：资格项与引用它的评分项共用同一份结果，
        # 既不重复计算，也不会出现"评分项吃到别的资格条款结果"的串线
        # （v1.5.3 的单槽 _last_performance_result 就是那么出错的）。
        self._perf_cache: dict[str, dict] = {}
        entries = []
        for req in requirements:
            entries.append(self._match_one(req))
        for e in entries:
            # 条件随产物落盘：评审时"依据是什么"不用翻代码
            e["condition"] = (self._req_by_id.get(e["requirement_id"]) or {}).get("condition")
        # 候选卫生（GPT 1.5.1）：同一 requirement 下 material_id 唯一；
        # PASS 候选必须是本项目时点可用的素材——否则就是"总状态 WAIT、候选偷偷假 PASS"。
        for e in entries:
            seen: set[str] = set()
            uniq: list[dict] = []
            for c in e.get("candidates") or []:
                mid = c.get("material_id")
                if mid in seen:
                    continue
                seen.add(mid)
                if c.get("status") == PASS:
                    m = self.repo.get(mid or "")
                    if m is not None and not _status_allows_pass(self._as_of_status(m)):
                        c = {**c, "status": FAIL,
                             "reasons": (c.get("reasons") or [])
                                        + [f"护栏：素材本项目时点状态为 {self._as_of_status(m)}，"
                                           "不得作为 PASS 候选"]}
                uniq.append(c)
            e["candidates"] = uniq

        summary = {k: 0 for k in (PASS, FAIL, EVID, WAIT, NOTEVAL)}
        for e in entries:
            summary[e["status"]] = summary.get(e["status"], 0) + 1
        return {
            "schema_version": "1.0",
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "verdict_lexicon": VERDICT_LEXICON,
            "source": self.repo.describe(),
            "entries": entries,
            "summary": summary,
        }

    # ------------------------------------------------------------------ #
    def _match_one(self, req: dict) -> dict:
        cond = req.get("condition") or {}
        rule = cond.get("rule")
        # 1.4.3 的禁止情形里，只有失信/不良记录类需要信誉材料自证；
        # 其余（关联关系、主体状态等）属承诺声明，不该去查信用查询结果。
        if rule == "NEGATIVE_LIST_SELFCHECK":
            rule = ("CREDIT_QUERY_FRESHNESS"
                    if "信用查询" in (req.get("evidence_required") or [])
                    else "DECLARATION")
        handler = {
            "ALL_OF": self._h_all_of,
            "MATERIAL_PRESENCE": self._h_material_presence,
            "CERTIFICATE_LEVEL": self._h_certificate_level,
            "CERTIFICATE_VALIDITY": self._h_certificate_validity,
            "PERFORMANCE_FIVE_ELEMENTS": self._h_performance,
            "PERFORMANCE_SCORING": self._h_performance_scoring,
            "SCORE_PERFORMANCE_UNPARSED": self._h_score_performance_unparsed,
            "DECLARATION": self._h_declaration,
            "CREDIT_QUERY_FRESHNESS": self._h_credit,
            "FINANCIAL_YEARS": self._h_financial,
            "BID_SECURITY_PAID": self._h_pending_company,
            "NO_SUBCONTRACTING": self._h_declaration,
            "PRICE_COMPLETENESS": self._h_price_completeness,
            "SPEC_CLAUSE": self._h_spec_clause,
            "PERSONNEL_CERTIFIED": self._h_personnel,
            "EQUIPMENT_LEDGER": self._h_equipment,
            "TERMS_RESPONSIVENESS": self._h_terms,
            "FORMAT_QUALITY": self._h_format,
            "BID_VALIDITY_DAYS": self._h_declaration,
            "SUBMIT_BEFORE_DEADLINE": self._h_declaration,
            "SCAN_COPIES": self._h_declaration,
            "ESIGN_REQUIRED": self._h_declaration,
            "NO_NEGATIVE_DEVIATION": self._h_declaration,
            "PRICE_WEIGHT": self._h_info,
        }.get(rule)
        if handler is None:
            return self._entry(req, NOTEVAL, [], [],
                               "无对应匹配规则（内容质量类，属生成/人工评审范围）",
                               missing=[], owner="COMPILER")
        return handler(req, cond)

    # ------------------------------------------------------------------ #
    # 通用组装
    @staticmethod
    def _entry(req: dict, status: str, candidates: list[dict], chain: list[dict],
               reason: str, *, selected: str | None = None,
               missing: list[str] | None = None, owner: str | None = None) -> dict:
        return {
            "requirement_id": req["id"],
            "requirement_type": req.get("requirement_type", "OTHER"),
            "severity": req.get("severity", "NORMAL"),
            "status": status,
            "selected": selected,
            "candidates": candidates,
            "evidence_chain": chain,
            "reason": reason,
            "missing_info": missing or [],
            "owner": owner,
        }

    # ------------------------------------------------------------------ #
    # 各规则处理器
    def _h_all_of(self, req, cond):
        """同一条款含多个条件（如"施工总承包资质 + 安全生产许可证"写在同一句）。

        组合规则：任一 FAIL → FAIL；否则任一 EVIDENCE_INSUFFICIENT → 证据不足；
        否则任一 WAIT_COMPANY → 待公司；其余情况全 PASS 才算 PASS。
        """
        children = cond.get("checks") or []
        if not children:
            return self._entry(req, NOTEVAL, [], [], "ALL_OF 条件为空，需人工确认")
        sub_entries, cands, chain, missing, reasons = [], [], [], [], []
        for child in children:
            sub_req = dict(req)
            sub_req["condition"] = child
            sub = self._match_one(sub_req)
            sub_entries.append(sub)
            cands.extend(sub.get("candidates") or [])
            chain.extend(sub.get("evidence_chain") or [])
            missing.extend(sub.get("missing_info") or [])
            reasons.append(f"{child.get('license') or child.get('material_category') or child.get('rule')}"
                           f"={sub['status']}")

        statuses = [s["status"] for s in sub_entries]
        if FAIL in statuses:
            status = FAIL
        elif EVID in statuses:
            status = EVID
        elif WAIT in statuses:
            status = WAIT
        elif NOTEVAL in statuses:
            status = EVID
        else:
            status = PASS
        selected = next((s.get("selected") for s in sub_entries
                         if s.get("selected")), None)
        return self._entry(req, status, cands, chain,
                           "综合条件（ALL_OF）：" + "；".join(reasons),
                           selected=selected, missing=missing,
                           owner="COMPANY" if status in (FAIL, EVID, WAIT) else None)

    def _h_certificate_validity(self, req, cond):
        """证书存在且有效期覆盖投标截止日（如安全生产许可证）。"""
        category = cond.get("material_category")
        mats = self._pass_eligible(self.repo.by_category(category))
        if not mats:
            return self._entry(req, FAIL, [], [], f"未找到{category}",
                               missing=[category], owner="COMPANY")
        covering = [m for m in mats if self._covers(m)]
        cands = []
        for m in mats:
            if m in covering:
                cands.append(self._cand(m, PASS, [f"有效期至 {m.get('expiry_date')}"]))
            elif m.get("expiry_date"):
                cands.append(self._cand(m, FAIL, [f"有效期至 {m.get('expiry_date')}，"
                                                  f"未覆盖 {self.deadline}"]))
            else:
                cands.append(self._cand(m, EVID, ["有效期未登记"]))
        if covering:
            m = covering[0]
            chain = [{"claim": f"{category}有效期覆盖投标截止日",
                      "quote": _first_quote(m) or f"{m.get('title')}（有效期至 {m.get('expiry_date')}）",
                      "material_id": m["material_id"], "source_ref": m.get("source_file")}]
            return self._entry(req, PASS, cands, chain,
                               f"{category}有效且覆盖 {self.deadline}", selected=m["material_id"])
        expired = [m for m in mats if m.get("expiry_date")]
        if expired:
            return self._entry(req, FAIL, cands, [],
                               f"{category}有效期未覆盖投标截止日 {self.deadline}",
                               missing=[f"{category}（有效期不足）"], owner="COMPANY")
        return self._entry(req, EVID, cands, [],
                           f"{category}有效期未登记，无法确认覆盖投标截止日",
                           missing=[f"{category} 有效期"], owner="COMPANY")

    def _h_material_presence(self, req, cond):
        category = cond.get("material_category")
        mats = self.repo.by_category(category)
        usable = self._pass_eligible(mats)
        cands = []
        for m in mats:
            if m in usable:
                cands.append(self._cand(m, PASS, [f"状态 {self._as_of_status(m)}"]))
            else:
                cands.append(self._cand(m, FAIL, [f"本项目时点状态 {self._as_of_status(m)}"]))
        if usable:
            m = usable[0]
            chain = [{
                "claim": f"{category}已具备",
                "quote": _first_quote(m) or m.get("title", ""),
                "material_id": m["material_id"],
                "source_ref": m.get("source_file"),
            }]
            return self._entry(req, PASS, cands, chain,
                               f"已具备{category}（{m['material_id']}）", selected=m["material_id"])
        if mats:
            return self._entry(req, FAIL, cands, [],
                               f"素材存在但不可用（状态：{[m.get('verification_status') for m in mats]}）",
                               missing=[f"有效的{category}"], owner="COMPANY")
        return self._entry(req, FAIL, [], [],
                           f"未找到{category}素材", missing=[category], owner="COMPANY")

    def _h_certificate_level(self, req, cond):
        """证书类别 + 等级校验（通用）。

        必须在**素材关键词里**同时找到许可证名与等级，例如：
        * 示例项目A："承装（修、试）电力设施许可证" + "承修类三级/承试类三级"
        * 示例项目B："电力工程施工总承包" + "贰级"（大写数字，需归一为"二级"）

        许可证名对不上就标 N/A 不参与比对——否则"建筑业企业资质证书"会被拿去
        顶替"承装修试许可证"，产出假 PASS。
        """
        lic_cat = cond.get("material_category", "资质证书_承装修试")
        required_classes = cond.get("required_classes") or cond.get("classes") or []
        license_name = cond.get("license") or cond.get("license_name") or ""
        if not license_name and not required_classes:
            # 既不限定证书名也不限定类别 —— 这个条件没有可判定内容。
            # 宁可报错，也不能"看起来通过"（v0.1.0 就因为空列表把校验整个绕过了）。
            raise ValueError(f"{req['id']}: 证书类条件既无 license 也无 required_classes，"
                             "校验会被空列表绕过")
        min_level = cond.get("min_level")
        all_mats = self._pass_eligible(self.repo.by_category(lic_cat))
        mats = [m for m in all_mats if license_name and license_name in _material_blob(m)]
        skipped = [m for m in all_mats if m not in mats]
        cands = [self._cand(m, "N/A", [f"关键词中不含「{license_name}」，不参与比对"])
                 for m in skipped]
        chain, missing = [], []

        if not all_mats:
            return self._entry(req, FAIL, cands, [], f"未找到{lic_cat}素材",
                               missing=[f"{license_name}（{lic_cat}）"], owner="COMPANY")

        target, fail_reasons, unknown = None, [], []
        for m in mats:
            blob = _material_blob(m)
            missing_classes = [c for c in required_classes if c not in blob]
            level = _level_in(blob)
            if missing_classes:
                cands.append(self._cand(m, FAIL, [f"缺类别：{'/'.join(missing_classes)}"]))
                fail_reasons.append(f"{m['material_id']}：缺类别 {'/'.join(missing_classes)}")
                continue
            if min_level is None:
                target = m
                cands.append(self._cand(m, PASS, [f"含「{license_name}」（条款未设等级门槛）"]))
                continue
            if level is None:
                cands.append(self._cand(m, EVID, [f"等级未登记，无法与「{_num_to_cn(min_level)}级」比对"]))
                unknown.append(f"{m['material_id']} 等级未登记")
                continue
            if level <= min_level:
                target = m
                cands.append(self._cand(m, PASS, [f"等级{_num_to_cn(level)}级 ≥ 要求{_num_to_cn(min_level)}级"]))
            else:
                cands.append(self._cand(m, FAIL, [
                    f"等级不满足（要求{_num_to_cn(min_level)}级或以上，实际{_num_to_cn(level)}级）"]))
                fail_reasons.append(
                    f"{m['material_id']}：等级不满足（要求{_num_to_cn(min_level)}级或以上，"
                    f"实际{_num_to_cn(level)}级）")

        if target is None:
            if unknown and not fail_reasons:
                return self._entry(req, EVID, cands, [],
                                   "证书在册但等级未登记，无法判定：" + "；".join(unknown),
                                   missing=unknown, owner="COMPANY")
            reason = f"未找到满足「{license_name}」"
            if required_classes:
                reason += f"（{'/'.join(required_classes)}类）"
            if min_level is not None:
                reason += f"{_num_to_cn(min_level)}级或以上"
            reason += "的证书"
            if fail_reasons:
                reason += "；" + "；".join(fail_reasons)
            return self._entry(req, FAIL, cands, [], reason,
                               missing=[f"{license_name}（{lic_cat}）"], owner="COMPANY")
        chain.append({
            "claim": f"{license_name}类别与等级满足",
            "quote": _first_quote(target) or target.get("title", ""),
            "material_id": target["material_id"],
            "source_ref": target.get("source_file"),
        })
        return self._entry(req, PASS, cands, chain,
                           f"{license_name}满足" +
                           (f"（{_num_to_cn(level)}级）" if min_level is not None else ""),
                           selected=target["material_id"])

    def _evaluate_performance(self, condition: dict) -> dict:
        """业绩五要素的**唯一**评估实现：资格项与评分项都走这里。

        返回 ``{"verdicts", "pass_ids", "selected", "evidence"}``。
        素材状态护栏（MATERIAL_STATUS）在这里统一施加，任何调用方都绕不过去。
        """
        mats = self.repo.by_category("业绩合同")
        evaluator = FiveElementEvaluator.from_rules_file(
            self.perf_rules_path, deadline=self.deadline, condition=condition)
        verdicts = [evaluator.evaluate(m, bidder=self.bidder) for m in mats]
        # 五要素只看合同内容；但**素材状态是资格红线**：
        # REJECTED / PENDING / 本项目时点已过期的业绩，即使五要素全部满足，
        # 也不得成为 PASS 候选（v1.5 的"总状态 WAIT、候选偷偷 PASS"就是这里漏的）。
        for v, m in zip(verdicts, mats):
            if v.verdict == "PASS" and not _status_allows_pass(self._as_of_status(m)):
                v.verdict = "FAIL"
                v.blocking_elements = list(v.blocking_elements) + ["MATERIAL_STATUS"]
                v.elements.append(ElementResult(
                    "MATERIAL_STATUS", "素材状态", NOT_SATISFIED,
                    f"素材在本项目时点状态为 {self._as_of_status(m)}，不得作为资格业绩",
                    [{"quote": f"{m.get('title')}（{m.get('verification_status')}）",
                      "source_ref": m.get("source_file")}]))
        passing = [v for v in verdicts if v.verdict == "PASS"]
        return {
            "verdicts": verdicts,
            "pass_ids": [v.material_id for v in passing],
            "selected": passing[0].material_id if passing else None,
            "evidence": passing[0].evidence_chain() if passing else [],
        }

    def _performance_for(self, requirement_id: str, condition: dict) -> dict:
        """按 requirement_id 取业绩评估结果（同一 requirement 只算一次）。

        评分项只能通过 ``condition_ref`` 指定的资格条件拿到结果——
        没有"取最近一次资格判定"这种隐式通路。
        """
        cache = getattr(self, "_perf_cache", None)
        if cache is None:
            cache = self._perf_cache = {}
        if requirement_id not in cache:
            cache[requirement_id] = self._evaluate_performance(condition)
        return cache[requirement_id]

    def _h_performance(self, req, cond):
        """业绩判定：阈值与词表**全部来自招标条款**（cond），不来自代码。

        同时输出三分法：QUALIFICATION_ELIGIBLE / SCORE_ELIGIBLE / SHOWCASE_ONLY。
        不能因为一份合同是真实业绩就自动算资格业绩——这是本轮明确要补的口径。
        """
        min_count = int(cond.get("min_count", 1))
        base = self._performance_for(req["id"], cond)
        verdicts = base["verdicts"]
        passing = [v for v in verdicts if v.verdict == "PASS"]
        # 角色：资格业绩 vs **自带独立口径的评分业绩**。
        # 后者（评分条款自己写了"35kV 以上运维业绩"这类口径、没引用资格条件）判出来的
        # 业绩是"评分可用"，不是"资格可用"——不能混进资格三分法里。
        is_scoring = req.get("severity") == "SCORE" or bool(cond.get("scoring"))
        elig_pass = "SCORE_ELIGIBLE" if is_scoring else "QUALIFICATION_ELIGIBLE"
        role_text = "评分口径" if is_scoring else "五要素"

        crit = (f"要求：≥{cond.get('min_kv')}kV、对象={'/'.join(cond.get('object_keywords') or [])}、"
                f"性质={'/'.join(cond.get('nature_keywords') or [])}、"
                f"{cond.get('not_before')} 后、≥{min_count} 项")

        cands = []
        for v in verdicts:
            cands.append({
                "material_id": v.material_id,
                "status": v.verdict,
                "score": None,
                "reasons": [v.verdict_text] + [f"{e.element_id} {e.reason}"
                                               for e in v.elements if e.blocking],
                "five_elements": {e.element_id: e.status for e in v.elements},
                "blocking_elements": v.blocking_elements,
                "partial_elements": v.partial_elements,
                "eligibility": elig_pass if v.verdict == "PASS" else "SHOWCASE_ONLY",
            })

        if len(passing) >= min_count:
            best = passing[0]
            chain = best.evidence_chain()
            if best.manual_review_flags:
                chain.append({
                    "claim": "需按评标口径终审的标记：" + "/".join(best.manual_review_flags),
                    "quote": "合同主名称未直接包含要求中的对象/性质字样，判定基于服务范围与责任条款原文",
                    "material_id": best.material_id,
                    "source_ref": best.title,
                })
            reason = (f"有 {len(passing)} 项业绩满足{role_text}（要求 ≥{min_count} 项）；"
                      f"选用 {best.material_id}：{best.verdict_text}（{crit}）")
            if best.manual_review_flags:
                reason += f"；需人工终审：{'/'.join(best.manual_review_flags)}"
            return self._entry(req, PASS, cands, chain, reason, selected=best.material_id)

        insufficient = [v for v in verdicts if v.verdict == "EVIDENCE_INSUFFICIENT"]
        if insufficient and not any(v.verdict == "FAIL" for v in verdicts):
            return self._entry(req, EVID, cands, [],
                               f"候选业绩均证据不足：无法从现有材料确认{role_text}成立",
                               missing=["满足要求的业绩合同原件（含双方盖章页/签订时间/关键信息页）"],
                               owner="COMPANY")
        return self._entry(req, FAIL, cands, [],
                           f"无业绩满足{role_text}（{crit}）：全部候选均为明确不满足",
                           missing=[f"满足要求的业绩合同（{crit}）"], owner="COMPANY")

    def _h_performance_scoring(self, req, cond):
        """商务评分"业绩"项：**只**消费 condition_ref 指定的资格条件结果。

        计分规则由本项目条款结构化给出（解析层 _performance_scoring_condition）；
        条款没写明计分参数时，这里不编造分值，只报"需人工按评标办法核定"。
        资格项结果按 requirement_id 缓存，与 requirements 顺序无关，
        也不会出现"评分项吃到另一条资格业绩口径"的串线。
        """
        ref_id = cond.get("condition_ref")
        ref_req = (self._req_by_id or {}).get(ref_id) if ref_id else None
        if not ref_id or not ref_req:
            return self._entry(req, NOTEVAL, [], [],
                               f"评分业绩未给出可追溯的资格条件引用（condition_ref={ref_id}），"
                               "不得自行推断业绩口径", owner="COMPILER")
        ref_cond = ref_req.get("condition") or {}
        if ref_cond.get("rule") != "PERFORMANCE_FIVE_ELEMENTS":
            return self._entry(req, NOTEVAL, [], [],
                               f"评分业绩引用的 {ref_id} 不是五要素业绩条件"
                               f"（rule={ref_cond.get('rule')}），需人工确认口径",
                               owner="COMPILER")
        base = self._performance_for(ref_id, ref_cond)

        qual_selected = base["selected"]
        exclude = cond.get("exclude_qualification_selected", True)
        per_item = cond.get("additional_points_per_item")
        max_add = cond.get("max_additional_items")
        qual_points = cond.get("qualification_item_points")
        # 计分参数必须来自条款；抽不到就不算分，不用历史项目的默认值兜底
        scorable = None not in (per_item, max_add, qual_points)

        score_eligible = [v for v in base["verdicts"]
                          if v.verdict == "PASS"
                          and not (exclude and v.material_id == qual_selected)]
        score_ids = [v.material_id for v in score_eligible]
        points = (qual_points + min(len(score_eligible), max_add) * per_item
                  if scorable else None)

        cands = []
        for v in base["verdicts"]:
            if v.material_id == qual_selected and exclude:
                cands.append({"material_id": v.material_id, "status": PASS,
                              "score": None,
                              "reasons": ["资格占用：不重复计分"],
                              "eligibility": "QUALIFICATION_ELIGIBLE"})
            elif v.verdict == "PASS":
                cands.append({"material_id": v.material_id, "status": PASS,
                              "score": per_item,
                              "reasons": [f"评分可用（+{per_item} 分）" if per_item is not None
                                          else "评分可用（单项分值未在条款中明示）"],
                              "eligibility": "SCORE_ELIGIBLE"})
            else:
                cands.append({"material_id": v.material_id, "status": FAIL,
                              "score": 0,
                              "reasons": [v.verdict_text]
                                         + [f"{e.element_id} {e.reason}" for e in v.elements
                                            if e.blocking],
                              "eligibility": "SHOWCASE_ONLY"})

        if scorable:
            scoring_note = (f"资格业绩（{qual_selected}）"
                            f"{'计基础分 ' + str(qual_points) + ' 分' if qual_points else '不重复计分'}；"
                            f"评分新增 {len(score_eligible)} 项 × {per_item} 分（最多 {max_add} 分）")
        else:
            scoring_note = ("资格业绩计分规则未在条款中结构化解析，"
                            "评分新增项数已列明，分值需人工按评标办法核定")
        chain = [{
            "claim": scoring_note,
            "quote": "、".join(score_ids) or "（无额外评分业绩）",
            "material_id": score_ids[0] if score_ids else qual_selected,
            "source_ref": None,
        }]

        if qual_selected is None:
            return self._entry(req, FAIL, cands, chain,
                               "资格业绩未满足，评分业绩无从计分",
                               missing=["满足资格口径的业绩合同"], owner="COMPANY")

        if scorable:
            reason = (f"资格选用 {qual_selected}"
                      f"（{'基础分 ' + str(qual_points) + ' 分' if qual_points else '不重复计分'}），"
                      f"评分新增 {len(score_eligible)} 项：{'、'.join(score_ids) or '无'}；"
                      f"本项预计得分 {points} 分")
        else:
            reason = (f"资格选用 {qual_selected}，评分新增 {len(score_eligible)} 项："
                      f"{'、'.join(score_ids) or '无'}；"
                      "计分规则未在条款中明示，得分需人工按评标办法核定")
        return self._entry(req, PASS, cands, chain, reason,
                           selected=(score_ids[0] if score_ids else qual_selected))

    def _h_score_performance_unparsed(self, req, cond):
        """业绩评分条款的口径没解析出来：交人工，**不得**自动继承资格条件。

        宁可这一项显示"未评估 / 需人工确认"，也不能拿资格业绩口径替它判分——
        那会把一个没人确认过的评分规则当成事实写进投标决策。
        """
        why = cond.get("why") or "评分业绩条款未能解析出可判定的口径"
        return self._entry(req, NOTEVAL, [], [], f"{why}；需人工确认后重跑", owner="COMPILER")

    def _h_credit(self, req, cond):
        """信誉 HARD 条款：requirement-specific 证据映射 + 项目时点有效期。

        两条纪律（都是被真实事故逼出来的）：

        * **fail-closed**：条款没能映射到具体证据来源时，判"证据不足（需人工确认口径）"，
          绝不拿任意一份信用查询材料顶上去——那是 fail-open，等于给陌生条款发通行证；
        * **一个 material 一个 candidate**：候选状态必须与实际判断一致，
          不允许"总项 PASS、候选还停在 PENDING"这种自相矛盾的产物。
        """
        import re as _re

        category = "信用查询"
        all_mats = self.repo.by_category(category)
        # 通用映射来自 rules；客户特定词（集团名等）由 profile 追加，不写进规则文件
        mapping = list(self.rules.get("credit_evidence_mapping") or [])
        mapping += list((self.project.get("credit_evidence_extra") or []))

        text = " ".join([req.get("source_text") or "", req.get("source_quote") or ""])
        flat = _re.sub(r"\s+", "", text)

        # 1) 找到本条款对应的证据要求
        rule_hit = None
        for m_rule in mapping:
            if any(kw in flat for kw in m_rule.get("require_any", [])):
                rule_hit = m_rule
                break

        if rule_hit is None:
            # 映射不到 → 证据不足（口径待确认），绝不 fail-open。
            # 候选状态必须落在封闭词表内：这里是"未评估"，不是"不合格"。
            cands = [self._cand(m, NOTEVAL,
                                ["条款未映射到具体证据来源，本条未对该素材作判定"])
                     for m in all_mats]
            return self._entry(
                req, EVID, cands, [],
                "信用条款未能映射到具体证据来源，需先确认口径（不得以任意信用查询材料顶替）",
                missing=["与条款语义对应的信用查询证据（口径待确认）"], owner="COMPILER")

        def _semantically_matches(m_material: dict) -> bool:
            blob = _material_blob(m_material)
            return any(kw in blob for kw in rule_hit.get("evidence_any", []))

        matching = [m for m in all_mats if _semantically_matches(m)]
        label = rule_hit.get("label", "信用查询材料")
        deadline_date = _parse_date(self.deadline) if self.deadline else None

        # 2) 一个 material 只生成一个候选，状态与实际判断一致
        cands: list[dict] = []
        fresh, future, undated, other = [], [], [], []
        for m in all_mats:
            if m not in matching:
                other.append(m)
                continue
            d = _parse_date(m.get("issue_date"))
            if d is None:
                undated.append(m)
            elif deadline_date is None or d <= deadline_date:
                fresh.append(m)
            else:
                future.append(m)
        for m in other:
            cands.append(self._cand(m, FAIL, ["与本条款证据语义不对应"]))
        for m in undated:
            cands.append(self._cand(m, EVID, ["查询日期未登记"]))
        for m in future:
            cands.append(self._cand(
                m, EVID, [f"查询日期 {m.get('issue_date')} 晚于投标截止日 {self.deadline}"
                          "（未来证据不能证明历史投标资格）"]))
        for m in fresh:
            cands.append(self._cand(
                m, PASS, [f"查询日期 {m.get('issue_date')}（≤ 投标截止日）"]))

        if not matching:
            return self._entry(req, EVID, cands, [],
                               f"缺少与条款语义对应的证据：{label}",
                               missing=[label], owner="COMPANY")

        # 3) as-of-project：证据日期必须 <= 投标截止日（未来证据无效）
        if fresh:
            m = fresh[0]
            chain = [{
                "claim": f"{label}：查询日期 {m.get('issue_date')} ≤ 投标截止日 {self.deadline}",
                "quote": _first_quote(m) or (m.get("title") or ""),
                "material_id": m["material_id"],
                "source_ref": m.get("source_file"),
            }]
            note = f"{label}在投标截止日前取得（{len(fresh)} 项）"
            if future:
                note += f"；另有 {len(future)} 项查询日期晚于截止日，不得作为证据"
            return self._entry(req, PASS, cands, chain, note,
                               selected=m["material_id"])

        reasons = []
        if future:
            reasons.append(f"{len(future)} 项查询日期晚于投标截止日 {self.deadline}"
                           "（未来证据不能证明历史投标资格）")
        if undated:
            reasons.append(f"{len(undated)} 项查询日期未登记")
        return self._entry(req, EVID, cands, [],
                           f"{label}证据时间无效：" + "；".join(reasons),
                           missing=[f"{label}（须在投标截止日 {self.deadline} 前查询）"],
                           owner="COMPANY")

    def _h_financial(self, req, cond):
        years = cond.get("years") or cond.get("required_years") or [2023, 2024, 2025]
        mats = self._pass_eligible(self.repo.by_category("财务审计报告"))
        by_year: dict[int, dict] = {}
        for m in mats:
            for y in years:
                if str(y) in (m.get("title") or "") or str(y) in (m.get("keywords") or []):
                    by_year.setdefault(y, m)
        cands, chain, missing = [], [], []
        for y in years:
            m = by_year.get(y)
            if m:
                cands.append(self._cand(m, PASS, [f"{y}年度审计报告在册"]))
                chain.append({
                    "claim": f"{y}年度审计报告已具备",
                    "quote": _first_quote(m) or f"{m.get('title')}（{m.get('certificate_number')}）",
                    "material_id": m["material_id"],
                    "source_ref": m.get("source_file"),
                })
            else:
                missing.append(f"{y}年度审计报告")
                cands.append({"material_id": f"(缺失){y}年度审计报告", "status": WAIT,
                              "reasons": ["未在素材库中找到"]})
        if missing:
            return self._entry(req, WAIT, cands, chain,
                               f"缺 {len(missing)} 个年度审计报告：{'、'.join(missing)}",
                               missing=missing, owner="COMPANY")
        return self._entry(req, PASS, cands, chain,
                           f"{'/'.join(str(y) for y in years)} 年度审计报告齐全",
                           selected=by_year[years[0]]["material_id"])

    def _h_equipment(self, req, cond):
        """设备机具：招标要求的是"所列机械工具优于检修需求"，但方案里承诺的设备
        必须有真实台账/检定证书支撑，否则属"写得出、拿不出"。"""
        mats = self.repo.by_category("设备机具")
        pending = [m for m in mats if m.get("verification_status") == "PENDING_COMPANY"]
        verified = [m for m in mats if m.get("verification_status") == "VERIFIED"]
        cands = [self._cand(m, PASS, ["已核验"]) for m in verified]
        cands += [self._cand(m, WAIT, ["待公司确认"]) for m in pending]
        if pending or not mats:
            return self._entry(req, WAIT, cands, [],
                               "设备机具能力已在技术方案中承诺，但缺少公司实际台账/检定证明",
                               missing=["本项目实际能够投入的施工设备及试验仪器清单"
                                        "（含技术方案所承诺的全部施工与试验能力）"],
                               owner="COMPANY")
        return self._entry(req, PASS, cands, [], "设备机具台账已核验",
                           selected=verified[0]["material_id"])

    def _h_personnel(self, req, cond):
        all_certs = self.repo.by_category("人员证书")
        groups = self._grouped(all_certs)
        certs = self._pass_eligible(all_certs)          # 只有已核验且本项目时点有效才能 PASS
        expired = groups.get("EXPIRED_AS_OF_PROJECT", [])
        pending = groups.get("PENDING_COMPANY", [])
        rejected = groups.get("REJECTED", [])

        cands = [self._cand(m, PASS, [f"{m.get('person') or m.get('title')}"]) for m in certs]
        cands += [self._cand(m, FAIL, [f"截至投标截止日 {self.deadline} 已过期"]) for m in expired]
        cands += [self._cand(m, WAIT, ["待公司提供"]) for m in pending]
        cands += [self._cand(m, FAIL, ["素材状态为排除"]) for m in rejected]

        chain = [{
            "claim": f"已具备 {len(certs)} 份已核验且本项目时点有效的人员材料",
            "quote": "、".join(sorted({(m.get('person') or m.get('title') or '')[:6] for m in certs})),
            "material_id": certs[0]["material_id"] if certs else None,
            "source_ref": certs[0].get("source_file") if certs else None,
        }] if certs else []
        missing = ["本项目实际拟派人员名单及岗位（需公司确认后据实填写）"]
        if expired:
            missing.append("有效期覆盖投标截止日的证件（"
                           + "、".join(f"{m.get('person')}" for m in expired) + "）")
        if pending:
            missing.append("特种作业操作证等关键岗位证件")
        if missing:
            return self._entry(req, WAIT, cands, chain,
                               "人员材料部分缺失/过期，需公司确认实际拟派人员并补齐证件",
                               missing=missing, owner="COMPANY")
        return self._entry(req, PASS, cands, chain, "拟派人员证件齐全",
                           selected=certs[0]["material_id"])

    def _h_pending_company(self, req, cond):
        """公司实际操作类事项。文案（金额/形式）必须从**本项目条款**动态生成——
        v1.5 里写死过某个项目的保证金金额，导致另一个项目的待办带着错误的金额。"""
        missing = []
        if cond.get("rule") == "BID_SECURITY_PAID":
            text = "投标保证金缴纳凭证"
            amount = cond.get("amount_cny")
            if amount:
                text += f"（{_fmt_cny(amount)}，{cond.get('form') or '电汇'}）"
            missing.append(text)
        return self._entry(req, WAIT, [], [],
                           "该事项须由公司实际执行后提供凭证", missing=missing, owner="COMPANY")

    def _h_price_completeness(self, req, cond):
        """报价缺漏项完整性：必须等公司把价格填进去才能核对，编译器只能提醒。"""
        return self._entry(req, WAIT, [], [],
                           "报价完整性需在报价表填写完成后核对（缺漏项超比例将被否决）",
                           missing=["投标报价及分项报价明细（含材料配件单价与合价）"],
                           owner="COMPANY")

    def _h_spec_clause(self, req, cond):
        """第五章/技术规格书条款。

        这些是"必须在服务方案与偏差表里响应"的义务。响应文本尚未生成（MVP-7），
        因此对编译器而言是待办，而不是"满足"。星号实质性条款负偏离即否决，
        单独提示出来。
        """
        is_star = bool(cond.get("star_clause"))
        note = ("★星号实质性条款，负偏离即否决，必须在偏差表中逐条响应"
                if is_star else "须在服务方案与商务和技术偏差表中响应")
        return self._entry(req, NOTEVAL, [], [], note, missing=[], owner="COMPILER")

    def _h_terms(self, req, cond):
        return self._entry(req, WAIT, [], [],
                           "需报价与商务条款填写完成后逐条核对响应",
                           missing=["投标报价及分项报价明细"], owner="COMPANY")

    def _h_format(self, req, cond):
        return self._entry(req, NOTEVAL, [], [],
                           "格式质量项由 bid-lint 与人工复核判定（属编译器生成范围）",
                           missing=[], owner="COMPILER")

    #: 需要公司**实际操作**才能完成的声明类事项 → WAIT_COMPANY
    _DECL_WAIT_COMPANY = {"SUBMIT_BEFORE_DEADLINE", "ESIGN_REQUIRED"}

    def _h_declaration(self, req, cond):
        """承诺/格式类条款的预装配状态。

        v1.5 把这类条款直接判 PASS，等于把"以后可以自动生成"当成了"已经生成"。
        收口规则（GPT 1.5.1）：
        * 平台递交 / CA 电子签章 —— 须公司实际操作 → WAIT_COMPANY；
        * 其余承诺类（有效期承诺、扫描件格式、分包承诺、联合体声明、1.4.3 自查…）
          —— 需等 Word 内容真正生成后才能核验 → NOT_EVALUATED + COMPILER_TODO，
          在此之前**不得 PASS**。
        """
        rule = cond.get("rule")
        if rule in self._DECL_WAIT_COMPANY:
            action = "平台递交" if rule == "SUBMIT_BEFORE_DEADLINE" else "CA 电子签章"
            return self._entry(req, WAIT, [], [],
                               f"须由公司完成{action}后该项才算闭环（预装配阶段不判定）",
                               missing=[f"{action}（公司实际操作）"], owner="COMPANY")
        return self._entry(req, NOTEVAL, [], [],
                           "承诺/格式类响应：需待 Word 内容生成后核验，预装配阶段不得提前 PASS",
                           missing=[], owner="COMPILER")

    def _h_info(self, req, cond):
        return self._entry(req, NOTEVAL, [], [], "信息项，不参与判定", missing=[])

    # ------------------------------------------------------------------ #
    def _covers(self, material: dict) -> bool:
        """证件有效期是否覆盖投标截止日。"""
        exp = _parse_date(material.get("expiry_date"))
        if exp is None or not self.deadline:
            return False
        dl = _parse_date(self.deadline)
        return bool(dl and exp >= dl)

    def _within_days(self, d: date, days: int) -> bool:
        if not self.deadline:
            return True
        dl = _parse_date(self.deadline)
        return bool(dl and d >= dl - timedelta(days=days))

    @staticmethod
    def _cand(material: dict, status: str, reasons: list[str]) -> dict:
        return {
            "material_id": material.get("material_id", "?"),
            "status": status,
            "score": None,
            "reasons": reasons,
        }



#: 大写中文数字 → 阿拉伯（"贰级" 要能当 "二级" 用）
_CN_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
              "壹": 1, "贰": 2, "叁": 3, "肆": 4, "伍": 5}
_LEVEL_RE = re.compile(r"([一二三四五壹贰叁肆伍])级")


def _material_blob(material: dict) -> str:
    """素材的可比对文本：标题 + 关键词 + 证据片段。"""
    parts = [material.get("title") or ""]
    parts.extend(material.get("keywords") or [])
    for span in material.get("evidence_spans") or []:
        parts.append(span.get("text") or "")
    return " ".join(parts)


def _level_in(text: str):
    """从文本里取等级（1=一级 … 5=五级）；取不到返回 None。"""
    levels = [_CN_DIGITS[m.group(1)] for m in _LEVEL_RE.finditer(text)
              if m.group(1) in _CN_DIGITS]
    return min(levels) if levels else None


def _first_quote(material: dict) -> Optional[str]:
    for span in material.get("evidence_spans") or []:
        text = span.get("text")
        if text:
            return text
    return None


def _num_to_cn(n: int) -> str:
    return {1: "一", 2: "二", 3: "三", 4: "四", 5: "五"}.get(n, str(n))


def _fmt_cny(v: float) -> str:
    """把元转成中文金额写法（如 72000 → '7.2万元'）。待办文案一律用本项目金额。"""
    if v >= 10000:
        wan = v / 10000
        text = f"{wan:g}万元"
        return text
    return f"{v:g}元"
