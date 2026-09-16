"""P0-3 / P0-4 对抗性用例：业绩口径的引用与隔离。

P0-3：资格业绩结果按 requirement_id 缓存，评分项只能消费 ``condition_ref`` 指定的那一条；
      requirements 顺序（含 SCORE 出现在 QUAL 之前）不得改变结果。
P0-4：只有原文明示"同资格要求"才允许继承资格口径；自带口径要独立解析；
      解析不出来就交人工（NOT_EVALUATED + 提取告警），绝不能偷偷继承或套用历史默认分值。
"""

from __future__ import annotations

import copy
import random
import unittest

from tests.adversarial.fixtures import (
    AdversarialCase, M_CABINET, M_CABLE, M_LINE, M_SUBSTATION,
    TENDER_SCORING_INDEPENDENT, TENDER_SCORING_UNPARSED,
    TENDER_SINGLE_PERFORMANCE_WITH_REF, TENDER_TWO_PERFORMANCE_CLAUSES,
)


def _scoring_item(rid: str, ref: str, *, per_item=1, max_items=4, qual_points=0) -> dict:
    """构造一条"引用某资格业绩条款"的评分项（模拟评分表里的多条业绩评分）。"""
    return {
        "id": rid, "chapter": "第三章 评标办法", "source_text": f"{rid} 引用 {ref}",
        "requirement_type": "PERFORMANCE", "severity": "SCORE",
        "condition": {"rule": "PERFORMANCE_SCORING", "condition_ref": ref,
                      "additional_points_per_item": per_item,
                      "max_additional_items": max_items,
                      "qualification_point_points": qual_points,
                      "qualification_item_points": qual_points,
                      "exclude_qualification_selected": True,
                      "classified_from": "对抗性用例构造"},
        "condition_text": "业绩评分", "evidence_required": ["业绩合同"],
        "response_location": "七（三）业绩", "source_page": 1,
        "source_section": "第三章", "source_quote": f"{rid}",
    }


class TestPerformanceReferenceIsolation(AdversarialCase):
    """P0-3：两套业绩口径 + 两条评分项，必须各认各的。"""

    def _requirements(self) -> list[dict]:
        rq = self.requirements_of(self.write_tender(TENDER_TWO_PERFORMANCE_CLAUSES))
        reqs = [r for r in rq["requirements"]
                if r["id"] in ("QUAL-3-3", "QUAL-3-4")]
        self.assertEqual({r["id"] for r in reqs}, {"QUAL-3-3", "QUAL-3-4"})
        reqs.append(_scoring_item("SCORE-PERF-A", "QUAL-3-3"))   # 线路口径
        reqs.append(_scoring_item("SCORE-PERF-B", "QUAL-3-4"))   # 柜类口径
        return reqs

    def _run(self, reqs: list[dict], materials: list[dict]) -> dict:
        matcher, repo = self.matcher(materials)
        matrix = matcher.match(copy.deepcopy(reqs))
        return {e["requirement_id"]: e for e in matrix["entries"]}

    def test_shuffled_order_gives_identical_result(self):
        reqs = self._requirements()
        materials = [M_LINE, M_CABINET, M_CABLE]
        base = self._run(reqs, materials)
        rnd = random.Random(20260630)
        for _ in range(6):
            shuffled = copy.deepcopy(reqs)
            rnd.shuffle(shuffled)
            got = self._run(shuffled, materials)
            for rid in base:
                with self.subTest(rid=rid):
                    self.assertEqual(got[rid]["status"], base[rid]["status"])
                    self.assertEqual(got[rid]["selected"], base[rid]["selected"])
                    self.assertEqual(got[rid]["reason"], base[rid]["reason"])

    def test_score_before_qual_is_identical(self):
        reqs = self._requirements()
        materials = [M_LINE, M_CABINET, M_CABLE]
        base = self._run(reqs, materials)
        score_first = ([r for r in reqs if r["severity"] == "SCORE"]
                       + [r for r in reqs if r["severity"] != "SCORE"])
        got = self._run(score_first, materials)
        for rid in base:
            with self.subTest(rid=rid):
                self.assertEqual(got[rid]["status"], base[rid]["status"])
                self.assertEqual(got[rid]["selected"], base[rid]["selected"])

    def test_two_references_do_not_cross(self):
        """A 只能吃线路口径，B 只能吃柜类口径——候选的判定也必须各归各的。"""
        entries = self._run(self._requirements(), [M_LINE, M_CABINET, M_CABLE])
        a, b = entries["SCORE-PERF-A"], entries["SCORE-PERF-B"]
        a_c = {c["material_id"]: c["status"] for c in a["candidates"]}
        b_c = {c["material_id"]: c["status"] for c in b["candidates"]}
        # 110kV 线路口径：只有线路合同满足；柜类合同应明确不满足（电压/对象都不对）
        self.assertEqual(a_c["M-ADV-LINE"], "PASS")
        self.assertEqual(a_c["M-ADV-CABINET"], "FAIL")
        self.assertEqual(a_c["M-ADV-CABLE"], "FAIL")
        # 10kV 开关柜口径：柜类满足，线路合同不满足
        self.assertEqual(b_c["M-ADV-CABINET"], "PASS")
        self.assertEqual(b_c["M-ADV-LINE"], "FAIL")
        self.assertEqual(b_c["M-ADV-CABLE"], "FAIL")
        # 资格选用与评分选用不得互串
        self.assertEqual(entries["QUAL-3-3"]["selected"], "M-ADV-LINE")
        self.assertEqual(entries["QUAL-3-4"]["selected"], "M-ADV-CABINET")

    def test_candidates_are_unique_per_requirement(self):
        entries = self._run(self._requirements(), [M_LINE, M_CABINET, M_CABLE])
        for rid, e in entries.items():
            ids = [c["material_id"] for c in e["candidates"]]
            with self.subTest(requirement=rid):
                self.assertEqual(len(ids), len(set(ids)), "同一 requirement 下 material_id 必须唯一")
                if e["status"] == "PASS" and e["selected"]:
                    sel = next(c for c in e["candidates"] if c["material_id"] == e["selected"])
                    self.assertNotIn(sel["status"], ("PENDING", "N/A"),
                                     "总项 PASS 时被选中的候选不得还是待定状态")

    def test_independent_scoring_口径_is_not_marked_qualification_eligible(self):
        """自带独立口径的评分项，其业绩不得混进"资格可用"三分法。"""
        result = self.run_pipeline(self.write_tender(TENDER_SCORING_INDEPENDENT),
                                   [M_LINE, M_CABINET, M_SUBSTATION])
        score = result["entries"]["SCORE-BIZ-PERF"]
        self.assertEqual(score["condition"]["rule"], "PERFORMANCE_FIVE_ELEMENTS")
        self.assertIsNone(score["condition"].get("condition_ref"))
        self.assertEqual(score["selected"], "M-ADV-SUBSTATION")
        tri = result["selection"]["performance_eligibility"]
        # 变电站在 35kV 运维口径下可用 → 记"评分可用"；它不是资格业绩，不得进"资格可用"
        self.assertIn("M-ADV-SUBSTATION", tri["SCORE_ELIGIBLE"])
        self.assertNotIn("M-ADV-SUBSTATION", tri["QUALIFICATION_ELIGIBLE"])
        self.assertIn("M-ADV-LINE", tri["QUALIFICATION_ELIGIBLE"])


class TestScoringClauseDoesNotInheritBlindly(AdversarialCase):
    """P0-4：引用必须来自原文明示；解析不出来不得继承。"""

    def test_parsed_scoring_clause_states_its_own_points(self):
        """本合成项目：资格业绩 1 分 + 每多 1 项 1 分、最多 4 分（全部由原文明示）。"""
        result = self.run_pipeline(self.write_tender(TENDER_SINGLE_PERFORMANCE_WITH_REF),
                                   [M_LINE, M_CABINET])
        cond = result["entries"]["SCORE-BIZ-PERF"]["condition"]
        self.assertEqual(cond["rule"], "PERFORMANCE_SCORING")
        self.assertEqual(cond["condition_ref"], "QUAL-3-3")
        self.assertEqual(cond["additional_points_per_item"], 1.0)
        self.assertEqual(cond["max_additional_items"], 4)
        self.assertEqual(cond["qualification_item_points"], 1.0)
        self.assertTrue(cond["exclude_qualification_selected"])

    def test_ambiguous_reference_is_not_guessed(self):
        """两套资格业绩口径时，引用目标不唯一 → 不得自动挑第一条。"""
        rq = self.requirements_of(self.write_tender(TENDER_TWO_PERFORMANCE_CLAUSES))
        score = next(r for r in rq["requirements"] if r["id"] == "SCORE-BIZ-PERF")
        self.assertEqual(score["condition"]["rule"], "SCORE_PERFORMANCE_UNPARSED")
        self.assertIsNone(score["condition"].get("condition_ref"))
        self.assertTrue(any("无法唯一确定引用的资格业绩条款" in i
                            for i in rq["extraction_issues"]))
        # 走 matcher：未评估，且不产出任何评分结论
        matcher, _ = self.matcher([M_LINE, M_CABINET])
        entry = matcher.match(rq["requirements"])
        e = next(x for x in entry["entries"] if x["requirement_id"] == "SCORE-BIZ-PERF")
        self.assertEqual(e["status"], "NOT_EVALUATED")
        self.assertEqual(e["owner"], "COMPILER")

    def test_unparseable_scoring_clause_is_not_evaluated(self):
        """评分项只有名字、没有规则 → NOT_EVALUATED + 提取告警，绝不继承资格口径。"""
        result = self.run_pipeline(self.write_tender(TENDER_SCORING_UNPARSED),
                                   [M_LINE, M_CABINET])
        score = result["entries"]["SCORE-BIZ-PERF"]
        self.assertEqual(score["status"], "NOT_EVALUATED")
        self.assertEqual(score["owner"], "COMPILER")
        self.assertIsNone(score["selected"])
        self.assertTrue(any("不得自动继承资格条件" in i
                            for i in result["requirements"]["extraction_issues"]))
        # 不得出现在"评分可用"里
        tri = result["selection"]["performance_eligibility"]
        self.assertNotIn("M-ADV-LINE", tri["SCORE_ELIGIBLE"])
        self.assertNotIn("M-ADV-CABINET", tri["SCORE_ELIGIBLE"])

    def test_unparsed_scoring_never_invents_points(self):
        """抽不到计分参数时不得出现"每项 1 分、最多 4 项"这类历史默认值。"""
        rq = self.requirements_of(self.write_tender(TENDER_SCORING_UNPARSED))
        score = next(r for r in rq["requirements"] if r["id"] == "SCORE-BIZ-PERF")
        cond = score["condition"]
        for key in ("additional_points_per_item", "max_additional_items"):
            self.assertNotIn(key, cond, f"{key} 不得凭空补一个默认值")


if __name__ == "__main__":
    unittest.main()
