"""P1 已知缺口：**记录当前行为**的对抗性用例（本轮不改判定，只钉住现状）。

这两个问题不阻塞本轮（改动会扩散到五要素的判定口径），但必须有 fixture 把
"现在的行为到底是什么"钉死，并且必须在 backlog 里留档：
任何一天改了实现，这里会立刻失败，提醒同步更新 docs/SOURCE_REVIEW_BACKLOG.md。

P1-A：E1 合同主体在**证据片段里没有出现投标人**时，仍会凭素材登记主体判 SATISFIED。
      设计纪律是"五要素 PASS 都要有合同原文引用"，因此这是一处口径缺口。
P1-B：E3 电压等级取**所有证据片段的最高 kV**，可能把"接入条件 110kV"当成"工作电压 110kV"。
"""

from __future__ import annotations

import unittest
from pathlib import Path

from tests.adversarial.fixtures import AdversarialCase, BIDDER, _mat, _SEAL

ROOT = Path(__file__).resolve().parents[2]
BACKLOG = ROOT / "docs" / "SOURCE_REVIEW_BACKLOG.md"

#: P1-A：证据片段里没有投标人，只有素材登记主体
M_NO_PARTY_IN_TEXT = _mat(
    "M-ADV-NOPARTY", "（合成）10kV 开关柜改造合同（片段未含当事人）",
    [("服务范围", "对 10kV 开关柜 12 台进行改造，并承担改造后的故障维修。")],
    kv=10)

#: P1-B：接入条件 110kV，实际作业 35kV
M_HIGH_ACCESS_ONLY = _mat(
    "M-ADV-ACCESS", "（合成）35kV 开关柜改造（含 110kV 接入条件说明）",
    [_SEAL,
     ("接入条件", "本工程 110kV 接入系统条件由甲方提供，乙方不承担该部分工作。"),
     ("工作内容", "实际工作内容为 35kV 开关柜 12 台改造及维修。")],
    kv=35)


def _evaluate(material: dict, condition: dict):
    from jty_bidcompiler.matchers.performance import FiveElementEvaluator
    from tests.support import PERF_RULES

    ev = FiveElementEvaluator.from_rules_file(PERF_RULES, deadline="2026-06-30",
                                              condition=condition)
    return ev.evaluate(material, bidder=BIDDER)


class TestP1A_PartyEvidenceIsRegistryOnly(AdversarialCase):
    """P1-A：E1 目前可以只凭素材登记主体成立（没有合同原文引用）。"""

    COND = {"not_before": "2023-01-01", "min_kv": 10,
            "object_keywords": ["开关柜"], "object_keywords_source": "lexicon",
            "nature_keywords": ["改造", "维修"]}

    def test_current_behaviour(self):
        v = _evaluate(M_NO_PARTY_IN_TEXT, self.COND)
        e1 = next(e for e in v.elements if e.element_id == "E1")
        self.assertEqual(e1.status, "SATISFIED", "当前行为：凭素材登记主体成立")
        self.assertIn("素材登记主体", e1.reason)
        # 证据引文来自素材登记，而不是合同原文——这一点必须写在引文里，不能藏起来
        quotes = " ".join(ev.get("quote", "") for ev in e1.evidence)
        self.assertIn("素材登记主体", quotes)

    def test_verdict_still_requires_all_five(self):
        """即使 E1 靠登记主体成立，其余要素仍必须齐全才可能 PASS。"""
        v = _evaluate(M_NO_PARTY_IN_TEXT, self.COND)
        self.assertEqual(v.verdict, "PASS")
        statuses = {e.element_id: e.status for e in v.elements}
        self.assertEqual(set(statuses.values()), {"SATISFIED"})

    def test_backlog_records_the_gap(self):
        self.assertTrue(BACKLOG.exists(), "缺少 docs/SOURCE_REVIEW_BACKLOG.md")
        text = BACKLOG.read_text(encoding="utf-8")
        self.assertIn("P1-A", text)
        self.assertIn("E1", text)


class TestP1B_VoltageTakesMaximumAcrossSpans(AdversarialCase):
    """P1-B：E3 取所有片段里的最高 kV，可能高估实际工作电压。"""

    COND = {"not_before": "2023-01-01", "min_kv": 110,
            "object_keywords": ["开关柜"], "object_keywords_source": "lexicon",
            "nature_keywords": ["改造", "维修"]}

    def test_current_behaviour_uses_max_kv(self):
        v = _evaluate(M_HIGH_ACCESS_ONLY, self.COND)
        e3 = next(e for e in v.elements if e.element_id == "E3")
        self.assertEqual(e3.status, "SATISFIED",
                         "当前行为：110kV 出现在接入条件里即被当作满足 ≥110kV")
        self.assertIn("110", e3.reason)
        # 证据里同时保留 110kV 与 35kV 的引用，便于人工复核"到底是哪个电压"
        quotes = " ".join(ev.get("quote", "") for ev in e3.evidence)
        self.assertIn("110kV", quotes)
        self.assertIn("35kV", quotes)

    def test_object_is_not_required_next_to_voltage(self):
        """电压证据与对象证据目前**没有**绑定：这是 P1-B 的缺口本身。"""
        v = _evaluate(M_HIGH_ACCESS_ONLY, self.COND)
        e3 = next(e for e in v.elements if e.element_id == "E3")
        high = [ev.get("quote", "") for ev in e3.evidence if "110kV" in ev.get("quote", "")]
        self.assertTrue(high, "110kV 的引用应当出现在 E3 证据里")
        for q in high:
            with self.subTest(quote=q[:40]):
                self.assertNotIn("开关柜", q,
                                 "110kV 证据与工作对象没有绑定——这正是 P1-B 记录的缺口")

    def test_backlog_records_the_gap(self):
        self.assertTrue(BACKLOG.exists(), "缺少 docs/SOURCE_REVIEW_BACKLOG.md")
        text = BACKLOG.read_text(encoding="utf-8")
        self.assertIn("P1-B", text)
        self.assertIn("E3", text)


if __name__ == "__main__":
    unittest.main()
