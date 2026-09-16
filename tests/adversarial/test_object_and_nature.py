"""P0-1 / P0-2 对抗性用例：五要素的"性质与对象关系"。

P0-1：E4 = PARTIAL（对象只出现在检查/试验语境）时，**不得**因其余要素满足而 PASS。
P0-2：性质判定不得再按"线路 = 不算、设备 = 才算"的全局先验做，
      必须与"本项目要求的对象"有关：
        Case A 本项目对象就是线路/铁塔 → 线路维护维修业绩应当被认可；
        Case B 本项目对象是开关柜 → 线路缆化改造不得因"出现改造"而算数；
        Case C 市政管网/监测终端项目 → 不受电力行业对象先验影响。
"""

from __future__ import annotations

import json
import unittest

from tests.adversarial.fixtures import (
    AdversarialCase, M_CABINET, M_CABLE, M_LINE, M_WATER, M_WEAK_OBJECT,
    TENDER_TWO_PERFORMANCE_CLAUSES,
)


class TestPartialBlocksPass(AdversarialCase):
    """P0-1：PARTIAL 阻断 PASS，但不得被拔高成 FAIL。"""

    def _verdict(self, condition: dict, material: dict):
        from jty_bidcompiler.matchers.performance import FiveElementEvaluator
        from tests.support import PERF_RULES

        ev = FiveElementEvaluator.from_rules_file(PERF_RULES, deadline="2026-06-30",
                                                  condition=condition)
        return ev.evaluate(material, bidder=material["company"])

    def test_element_level_and_verdict(self):
        cond = {"not_before": "2023-01-01", "min_kv": 10,
                "object_keywords": ["开关柜"], "object_keywords_source": "lexicon",
                "nature_keywords": ["改造", "维修"]}
        v = self._verdict(cond, M_WEAK_OBJECT)
        elements = {e.element_id: e.status for e in v.elements}
        self.assertEqual(elements["E1"], "SATISFIED")
        self.assertEqual(elements["E2"], "SATISFIED")
        self.assertEqual(elements["E3"], "SATISFIED")
        self.assertEqual(elements["E4"], "PARTIAL")
        self.assertEqual(elements["E5"], "SATISFIED")
        # 其余要素满足 + E4 PARTIAL → 证据不足，绝不是 PASS
        self.assertEqual(v.verdict, "EVIDENCE_INSUFFICIENT")
        self.assertNotEqual(v.verdict, "PASS")
        self.assertIn("E4", v.partial_elements)
        self.assertNotIn("E4", v.blocking_elements)  # PARTIAL 不是"明确不满足"

    def test_pipeline_never_reports_partial_as_pass(self):
        """整条 pipeline：候选、选择、投标状态都不得把 PARTIAL 当满足。"""
        result = self.run_pipeline(self.write_tender(TENDER_TWO_PERFORMANCE_CLAUSES),
                                   [M_WEAK_OBJECT, M_CABINET])
        entry = result["entries"]["QUAL-3-4"]          # 开关柜口径
        self.assertEqual(entry["status"], "PASS")      # 有 M-CABINET 顶上
        weak = next(c for c in entry["candidates"] if c["material_id"] == "M-ADV-WEAK")
        self.assertEqual(weak["status"], "EVIDENCE_INSUFFICIENT")
        self.assertEqual(weak["five_elements"]["E4"], "PARTIAL")
        self.assertEqual(weak["partial_elements"], ["E4"])
        # 弱对象合同不得进入资格/评分可用集合（只能作企业展示）
        tri = result["selection"]["performance_eligibility"]
        self.assertNotIn("M-ADV-WEAK", tri["QUALIFICATION_ELIGIBLE"])
        self.assertNotIn("M-ADV-WEAK", tri["SCORE_ELIGIBLE"])
        self.assertIn("M-ADV-WEAK", tri["SHOWCASE_ONLY"])


class TestNatureRelationToProjectObject(AdversarialCase):
    """P0-2：性质与对象的关系由本项目条款决定，不用全局品类先验。"""

    def _verdict(self, condition: dict, material: dict):
        from jty_bidcompiler.matchers.performance import FiveElementEvaluator
        from tests.support import PERF_RULES

        ev = FiveElementEvaluator.from_rules_file(PERF_RULES, deadline="2026-06-30",
                                                  condition=condition)
        return ev.evaluate(material, bidder=material["company"])

    def test_case_a_line_object_project_accepts_line_work(self):
        cond = {"not_before": "2023-01-01", "min_kv": 110,
                "object_keywords": ["输电线路", "线路", "铁塔", "杆塔"],
                "object_keywords_source": "lexicon",
                "nature_keywords": ["维护", "维修"]}
        v = self._verdict(cond, M_LINE)
        status = {e.element_id: e.status for e in v.elements}
        self.assertEqual(status["E4"], "SATISFIED")
        self.assertEqual(status["E5"], "SATISFIED")
        self.assertEqual(v.verdict, "PASS")

    def test_case_b_cabinet_project_rejects_cable_reroute(self):
        cond = {"not_before": "2023-01-01", "min_kv": 10,
                "object_keywords": ["开关柜"], "object_keywords_source": "lexicon",
                "nature_keywords": ["改造", "维修"]}
        v = self._verdict(cond, M_CABLE)
        status = {e.element_id: e.status for e in v.elements}
        self.assertEqual(status["E4"], "NOT_SATISFIED")     # 对象不匹配
        self.assertEqual(status["E5"], "NOT_SATISFIED")     # "缆化改造"不构成改造
        self.assertEqual(v.verdict, "FAIL")

    def test_case_c_municipal_object_unaffected_by_power_priors(self):
        cond = {"not_before": "2023-01-01", "min_kv": 10,
                "object_keywords": ["监测终端", "管网"],
                "object_keywords_source": "lexicon",
                "nature_keywords": ["改造", "维修"]}
        v = self._verdict(cond, M_WATER)
        status = {e.element_id: e.status for e in v.elements}
        self.assertEqual(status["E4"], "SATISFIED")
        self.assertEqual(status["E5"], "SATISFIED")
        self.assertEqual(v.verdict, "PASS")

    def test_no_device_or_line_wordlist_in_engine(self):
        """引擎里不得再有"线路词表 / 设备词表"这种对象品类硬编码。"""
        from pathlib import Path

        import jty_bidcompiler.matchers.performance as perf

        src = Path(perf.__file__).read_text(encoding="utf-8")
        for banned in ("_LINE_WORDS", "_OBJECT_WORDS"):
            self.assertNotIn(banned, src, f"{banned} 仍然存在：对象品类先验没删干净")

    def test_full_pipeline_line_project_selects_line_contract(self):
        """Case A 走完整 pipeline：线路口径的资格业绩应当被判满足并选用。"""
        result = self.run_pipeline(self.write_tender(TENDER_TWO_PERFORMANCE_CLAUSES),
                                   [M_LINE, M_CABINET, M_CABLE])
        line_entry = result["entries"]["QUAL-3-3"]        # 110kV 线路铁塔口径
        self.assertEqual(line_entry["status"], "PASS")
        self.assertEqual(line_entry["selected"], "M-ADV-LINE")
        # 本项目有两套资格业绩口径，评分条款虽然写了"同投标人资格要求"，
        # 但引用目标不唯一 → 不得自动挑一条，交人工确认
        score = result["entries"]["SCORE-BIZ-PERF"]
        self.assertEqual(score["condition"]["rule"], "SCORE_PERFORMANCE_UNPARSED")
        self.assertEqual(score["status"], "NOT_EVALUATED")
        self.assertEqual(score["owner"], "COMPILER")


if __name__ == "__main__":
    unittest.main()
