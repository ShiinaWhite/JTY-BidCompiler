"""黄金判例：业绩五要素判定必须与人工核验结论一致。

这是整个 PoC 的**验收闸门**（见 docs/MVP实施计划.md §验收）：
如果这里错了，后面的 Word 自动装配毫无意义，直接停止扩展。

人工核验结论来源：
* 业绩五要素核验表（回料调查，本地）
* 资格业绩五要素核验表（stage1 盘点，本地）
"""

from __future__ import annotations

import unittest

from tests.support import PERF_RULES, material, load_json, bidder_of

BIDDER = bidder_of("sample_a")
from jty_bidcompiler.matchers.performance import (
    FiveElementEvaluator, NOT_SATISFIED, PARTIAL, SATISFIED, UNKNOWN,
)

DEADLINE = "2026-09-18"

#: 本项目的业绩口径（来自其招标文件「投标人资格要求」业绩条款原文）。
#: 生产流程里这份条件由 parsers.qualification_classifier 从句子里解析后注入；
#: 这里显式写出来，是为了让**引擎测试不依赖规则文件里的任何默认值**
#: （rules/performance_five_elements.json 已不含任何项目的对象词）。
CONDITION = {
    "not_before": "2023-01-01",
    "min_kv": 10,
    "object_keywords": ["开关柜"],
    "object_keywords_source": "lexicon",
    "nature_keywords": ["改造", "维修"],
    "min_count": 1,
}


#: 人工核验结论（不得修改；改了就等于改验收标准）
GOLDEN = {
    "M-PERF-001": {"name": "（示例）运维框架合同", "verdict": "PASS",
                   "flags": ["CONTRACT_TITLE_NOT_BINDING_OBJECT"]},
    "M-PERF-091": {"name": "（示例）新建输变电工程", "verdict": "FAIL", "blocking": ["E4", "E5"]},
    "M-PERF-092": {"name": "（示例）线路迁改工程", "verdict": "FAIL", "blocking": ["E4", "E5"]},
    # 人工核验对"线路缆化改造"同时标了"对象不满足"与"性质不满足"，
    # 因此阻断要素是 E4+E5。
    "M-PERF-093": {"name": "（示例）线路缆化改造", "verdict": "FAIL", "blocking": ["E4", "E5"]},
    # 次要判例：人工判"部分涉及(要素4) + 性质不满足(要素5)"
    "M-PERF-004": {"name": "（示例）变电站检测服务", "verdict": "FAIL", "blocking": ["E5"]},
    "M-PERF-003": {"name": "（示例）线路维护维修（对象不含柜类设备）",
                   "verdict": "FAIL", "blocking": ["E4"]},
}


class TestFiveElements(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 条件显式注入：引擎不得依赖规则文件里的项目默认值
        cls.ev = FiveElementEvaluator.from_rules_file(PERF_RULES, deadline=DEADLINE,
                                                      condition=CONDITION)

    def _verdict(self, mid):
        return self.ev.evaluate(material(mid), bidder=BIDDER)

    def test_golden_cases_match_manual_review(self):
        for mid, expect in GOLDEN.items():
            with self.subTest(material=mid, name=expect["name"]):
                v = self._verdict(mid)
                self.assertEqual(v.verdict, expect["verdict"],
                                 f"{expect['name']} 判定应为 {expect['verdict']}，实际 {v.verdict}："
                                 f"{[e.reason for e in v.elements if e.blocking]}")

    def test_blocking_elements_match(self):
        for mid, expect in GOLDEN.items():
            if "blocking" not in expect:
                continue
            with self.subTest(material=mid):
                v = self._verdict(mid)
                self.assertEqual(sorted(v.blocking_elements), sorted(expect["blocking"]))

    def test_manual_review_flags(self):
        v = self._verdict("M-PERF-001")
        self.assertEqual(v.manual_review_flags, GOLDEN["M-PERF-001"]["flags"])

    def test_verdict_vocabulary_is_closed(self):
        """禁止"可能满足/大概率满足"这类模糊结论。"""
        from jty_bidcompiler.matchers.performance import VERDICT_TEXT

        forbidden = load_json(PERF_RULES)["discipline"]["forbidden_verdict_words"]
        for mid in GOLDEN:
            v = self._verdict(mid)
            for word in forbidden:
                self.assertNotIn(word, v.verdict_text)
            self.assertIn(v.verdict_text, set(VERDICT_TEXT.values()))

    def test_every_pass_has_quoted_evidence(self):
        """明确满足必须带合同原文引用——没有引用的满足无效。"""
        v = self._verdict("M-PERF-001")
        self.assertEqual(v.verdict, "PASS")
        chain = v.evidence_chain()
        self.assertGreaterEqual(len(chain), 5)
        joined = " ".join(c["quote"] for c in chain)
        # 证据链必须含对象、性质、电压等级等关键原文（编号类随项目而异，不写死）
        for key in ("开关柜", "检修", "维修", "35KV"):
            self.assertIn(key, joined, f"证据链缺少关键原文：{key}")
        for c in chain:
            self.assertTrue(c["quote"].strip())
            self.assertEqual(c["material_id"], "M-PERF-001")

    def test_e4_object_needs_no_interpretation_stretch(self):
        """线路/缆化对象不得扩张解释为柜类设备（关键失败点）。"""
        v = self._verdict("M-PERF-093")
        e4 = next(e for e in v.elements if e.element_id == "E4")
        self.assertEqual(e4.status, NOT_SATISFIED)
        self.assertIn("不得扩张解释", e4.reason)

    def test_e5_rejects_line_work_nature(self):
        """'缆化改造'里的'改造'不算开关柜改造。"""
        v = self._verdict("M-PERF-093")
        e5 = next(e for e in v.elements if e.element_id == "E5")
        self.assertEqual(e5.status, NOT_SATISFIED)
        self.assertIn("线路", e5.reason)

    def test_weak_context_yields_partial_not_satisfied(self):
        """开关柜只作为被检查对象出现时，E4 应为 PARTIAL 而不是 SATISFIED。"""
        v = self._verdict("M-PERF-004")
        e4 = next(e for e in v.elements if e.element_id == "E4")
        self.assertEqual(e4.status, PARTIAL)

    def test_voltage_parsing_ignores_kva(self):
        """5000KVA 是容量不是电压，不能被当成 5000kV。"""
        v = self._verdict("M-PERF-001")
        e3 = next(e for e in v.elements if e.element_id == "E3")
        self.assertEqual(e3.status, SATISFIED)
        self.assertIn("35", e3.reason)
        self.assertNotIn("5000", e3.reason)

    def test_missing_evidence_yields_insufficient_not_pass(self):
        """只有标题、没有正文证据时，必须判"证据不足"，不能替公司编满足。"""
        bare = {
            "material_id": "M-TEST-BARE",
            "title": "某开关柜改造合同",
            "category": "业绩合同",
            "company": BIDDER,
            "contract_date": "2024-06-01",
            "voltage_level": "10kV",
            "voltage_level_kv": 10,
            "verification_status": "VERIFIED",
            "source_file": None,
            "evidence_spans": [],
        }
        v = self.ev.evaluate(bare, bidder=BIDDER)
        self.assertEqual(v.verdict, "EVIDENCE_INSUFFICIENT")
        self.assertIn("E4", v.blocking_elements)
        self.assertEqual(v.verdict_text, "证据不足，不能认定")


if __name__ == "__main__":
    unittest.main()
