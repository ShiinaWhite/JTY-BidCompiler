"""P0-5 / P0-6 对抗性用例：信用条款 fail-closed 与模板锚点。

P0-5：映射不到证据来源的信用条款必须"证据不足"，不得拿任意信用材料顶上；
      同一 requirement 下 material 候选唯一，且总项 PASS 时被选中的候选不能还是待定。
P0-6：招标文件里没写的要求（模板锚点未命中）不得变成 requirement，
      尤其不得变成"公司的待办"。
"""

from __future__ import annotations

import json
import unittest

from tests.adversarial.fixtures import (
    AdversarialCase, M_CREDIT_COURT, M_CREDIT_GSXT, TENDER_NO_COMMERCIAL_ANCHORS,
)


class TestCreditFailClosed(AdversarialCase):
    def _run(self, materials=None):
        return self.run_pipeline(self.write_tender(TENDER_NO_COMMERCIAL_ANCHORS),
                                 materials if materials is not None else [])

    def test_unknown_credit_clause_is_insufficient(self):
        """（5）省级交通运输主管部门严重失信名单 —— 没有对应映射 → 证据不足。"""
        result = self._run([M_CREDIT_GSXT, M_CREDIT_COURT])
        entry = result["entries"]["QUAL-143-5"]
        self.assertEqual(entry["status"], "EVIDENCE_INSUFFICIENT")
        self.assertIsNone(entry["selected"])
        self.assertNotIn("PASS", [c["status"] for c in entry["candidates"]])

    def test_mapped_credit_clauses_use_their_own_evidence(self):
        """已知的两条：各自映射到对应平台的查询结果，且互不串用。"""
        result = self._run([M_CREDIT_GSXT, M_CREDIT_COURT])
        gsxt = result["entries"]["QUAL-143-3"]        # 严重违法失信 → 公示系统
        court = result["entries"]["QUAL-143-4"]       # 失信被执行人 → 执行信息公开网
        self.assertEqual(gsxt["status"], "PASS")
        self.assertEqual(court["status"], "PASS")
        self.assertEqual(gsxt["selected"], "M-ADV-GSXT")
        self.assertEqual(court["selected"], "M-ADV-COURT")
        self.assertIn("国家企业信用信息公示系统", gsxt["reason"])
        self.assertIn("执行信息公开", court["reason"])

    def test_wrong_platform_material_cannot_satisfy_a_clause(self):
        """只给"执行信息公开网"的材料时，"公示系统"那条必须证据不足，不得顶替。"""
        result = self._run([M_CREDIT_COURT])
        gsxt = result["entries"]["QUAL-143-3"]
        self.assertEqual(gsxt["status"], "EVIDENCE_INSUFFICIENT")
        self.assertIsNone(gsxt["selected"])
        court = result["entries"]["QUAL-143-4"]
        self.assertEqual(court["status"], "PASS")
        self.assertEqual(court["selected"], "M-ADV-COURT")

    def test_candidates_unique_and_consistent(self):
        result = self._run([M_CREDIT_GSXT, M_CREDIT_COURT])
        for rid in ("QUAL-143-3", "QUAL-143-4", "QUAL-143-5"):
            entry = result["entries"][rid]
            ids = [c["material_id"] for c in entry["candidates"]]
            with self.subTest(requirement=rid):
                self.assertEqual(len(ids), len(set(ids)), "material_id 必须唯一")
                for c in entry["candidates"]:
                    self.assertIn(c["status"], ("PASS", "FAIL", "EVIDENCE_INSUFFICIENT",
                                                "WAIT_COMPANY", "NOT_EVALUATED"))

    def test_missing_credit_evidence_never_becomes_pass(self):
        """素材库为空时，任何信用条款都不得 PASS。"""
        result = self._run()
        for rid, entry in result["entries"].items():
            if not rid.startswith("QUAL-143-"):
                continue
            with self.subTest(requirement=rid):
                self.assertNotEqual(entry["status"], "PASS")


class TestTemplateAnchorsDoNotInventRequirements(AdversarialCase):
    """P0-6：没命中的模板锚点只留告警，不生成 requirement。"""

    def test_no_commercial_requirements_when_clauses_absent(self):
        result = self._run()
        ids = {r["id"] for r in result["requirements"]["requirements"]}
        for rid in ("COMM-1", "COMM-2", "COMM-3", "COMM-4", "COMM-5",
                    "COMM-6", "COMM-7", "COMM-9", "COMM-10"):
            with self.subTest(requirement=rid):
                self.assertNotIn(rid, ids, f"招标文件没写的 {rid} 不该出现在 requirements 里")

    def test_absent_anchors_are_reported_as_issues(self):
        result = self._run()
        issues = " ".join(result["requirements"]["extraction_issues"])
        for rid in ("COMM-1", "COMM-5", "COMM-9"):
            with self.subTest(requirement=rid):
                self.assertIn(rid, issues, "未命中的锚点必须留下可追溯的提示")

    def test_no_company_todo_for_absent_clauses(self):
        """没有保证金条款 → 不得让公司去交保证金；没有 CA 签章 → 不得派签章待办。

        判定看**条目**而不是文本：disclaimer 里出现"保证金"这类词是合规说明，
        真正的错误是"派出一个招标文件里不存在的待办条目"。
        """
        status = self._run()["status"]
        rows = (status.get("closed", []) + status.get("hard_fail", [])
                + status.get("waiting_company", []) + status.get("compiler_todo", [])
                + status.get("materials_pending", []))
        ids = [r.get("requirement_id") for r in rows]
        for rid in ("COMM-1", "COMM-2", "COMM-3", "COMM-4", "COMM-5",
                    "COMM-6", "COMM-7", "COMM-9", "COMM-10"):
            with self.subTest(requirement=rid):
                self.assertNotIn(rid, ids, f"{rid} 不存在于招标文件，不该出现在待办里")

    def _run(self):
        return self.run_pipeline(self.write_tender(TENDER_NO_COMMERCIAL_ANCHORS), [])


if __name__ == "__main__":
    unittest.main()
