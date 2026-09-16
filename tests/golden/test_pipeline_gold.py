"""端到端 Gold Test：真实项目全流程必须落在期望状态上。

期望值写在 config/sample_a.json 的 ``expected`` 段——测试与配置同源，
避免"文档说一套、代码算一套"。
"""

from __future__ import annotations

import json
import unittest

from tests.support import (
    BID_TEXT, LINT_RULES, MATERIALS, PERF_RULES, PROFILE, QUAL_RULES,
    load_json, load_materials,
)
from jty_bidcompiler.adapters.repository import LocalMaterialRepository
from jty_bidcompiler.matchers.engine import RequirementMatcher
from jty_bidcompiler.validators.bid_lint import BidLinter


_PROJECT_NUMBER = (json.loads(PROFILE.read_text(encoding="utf-8"))["project_number"] if PROFILE.exists() else "")


def _project_number() -> str:
    if not PROFILE.exists():
        return ""
    return json.loads(PROFILE.read_text(encoding="utf-8"))["project_number"]


@unittest.skipUnless(PROFILE.exists(), "缺少本地项目配置 config/sample_a.json（不入库）")
class TestPipelineExpectations(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = load_json(PROFILE)
        cls.expected = cls.profile["expected"]
        cls.repo = LocalMaterialRepository(MATERIALS)
        cls.matcher = RequirementMatcher(
            cls.repo, rules_path=QUAL_RULES, perf_rules_path=PERF_RULES,
            project={"fields": {"deadline": {"value_normalized": "2026-09-18 11:00"}}},
            bidder=cls.profile["bidder"],
        )
        cls.requirements = cls._requirements()
        cls.matrix = cls.matcher.match(cls.requirements)
        cls.entries = {e["requirement_id"]: e for e in cls.matrix["entries"]}

    @staticmethod
    def _tender_path():
        rel = json.loads(PROFILE.read_text(encoding="utf-8"))["inputs"]["tender"]
        return PROFILE.parents[1] / rel

    @classmethod
    def _requirements(cls):
        """直接构造必要要求（避免测试依赖生成产物的存在）。"""
        from jty_bidcompiler.parsers.tender_document import load_tender
        from jty_bidcompiler.parsers.tender_requirements import extract_requirements

        path = cls._tender_path()
        if not path.exists():
            raise unittest.SkipTest("缺少招标文件 fixture")
        text, meta = load_tender(path)
        return extract_requirements(text, document_path=str(path),
                                    document_sha256=meta["sha256"])["requirements"]

    # ------------------------------------------------------------------ #
    def test_qualification_verdicts(self):
        for rid, expect in self.expected["qualification_verdicts"].items():
            with self.subTest(requirement=rid):
                self.assertEqual(self.entries[rid]["status"], expect)

    def test_qualification_performance_selects_sample_a_first_station(self):
        e = self.entries["QUAL-3-3"]
        self.assertEqual(e["status"], "PASS")
        self.assertEqual(e["selected"], "M-PERF-001")
        self.assertIn("CONTRACT_TITLE_NOT_BINDING_OBJECT", e["reason"])

    def test_golden_performance_cases_in_matrix(self):
        e = self.entries["QUAL-3-3"]
        cands = {c["material_id"]: c for c in e["candidates"]}
        for mid, spec in self.expected["performance_cases"].items():
            with self.subTest(material=mid, name=spec["name"]):
                self.assertIn(mid, cands)
                self.assertEqual(cands[mid]["status"], spec["verdict"])

    def test_no_false_alarm_on_closed_items(self):
        """已闭环项不得出现在缺失信息里（误报是这张表最大的敌人）。"""
        for rid, e in self.entries.items():
            blob = " ".join(e.get("missing_info") or []) + e.get("reason", "")
            for item in self.expected["must_not_report_missing"]:
                with self.subTest(requirement=rid, item=item):
                    self.assertNotIn(item, blob)

    def test_financial_years_all_covered(self):
        e = self.entries["SCORE-BIZ-FIN"]
        self.assertEqual(e["status"], "PASS")
        for year in ("2023", "2024", "2025"):
            self.assertIn(year, e["reason"])

    def test_credit_queries_fresh(self):
        self.assertEqual(self.entries["QUAL-143-14"]["status"], "PASS")

    def test_real_open_items_are_reported_in_gap_sheet(self):
        """真实未决项必须出现在《缺失材料清单》里（按类别核对，与交付物同源）。"""
        import tempfile
        from pathlib import Path

        from jty_bidcompiler.generators.xlsx_gaps import write_missing_materials
        import openpyxl

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "gaps.xlsx"
            write_missing_materials(self.matrix, self.requirements, out,
                                    project_title=self.profile["title"])
            wb = openpyxl.load_workbook(out)
            ws = wb["缺失材料清单"]
            cats = [r[1] for r in ws.iter_rows(min_row=3, values_only=True) if r[1]]
        for item in self.expected["must_report_missing"]:
            with self.subTest(item=item):
                self.assertIn(item, cats, f"缺失清单缺少「{item}」；实际类别：{cats}")
        self.assertNotIn("审计报告", " ".join(cats))

    def test_bid_security_waits_for_company(self):
        self.assertEqual(self.entries["COMM-1"]["status"], "WAIT_COMPANY")

    def test_expired_certificate_not_used(self):
        """已过期证件状态为排除/过期，不会进入 PASS。"""
        expired = [m for m in load_materials()
                   if m["verification_status"] in ("EXPIRED", "REJECTED")]
        self.assertTrue(expired)
        for m in expired:
            self.assertIsNone(m.get("source_pages") or None) if m["material_id"] == "M-PER-001C" else None


@unittest.skipUnless(PROFILE.exists() and BID_TEXT.exists(),
                     "缺少本地项目配置或投标文件文本 fixture（不入库）")
class TestBidLintOnRealBid(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        profile = load_json(PROFILE)
        cls.repo = LocalMaterialRepository(MATERIALS)
        cls.matcher = RequirementMatcher(
            cls.repo, rules_path=QUAL_RULES, perf_rules_path=PERF_RULES,
            project={"fields": {"deadline": {"value_normalized": "2026-09-18 11:00"}}},
            bidder=profile["bidder"],
        )
        reqs = TestPipelineExpectations._requirements()
        cls.matrix = cls.matcher.match(reqs)
        cls.bid_text = BID_TEXT.read_text(encoding="utf-8", errors="replace") if BID_TEXT.exists() else ""
        linter = BidLinter(
            rules_path=LINT_RULES,
            project={"fields": {
                "deadline": {"value": "2026 年09 月18 日11:00",
                             "value_normalized": "2026-09-18 11:00"},
                "project_number": {"value": _project_number()},
                "bid_security": {"value": "人民币2 万元", "value_normalized": 20000.0},
            }},
            matrix=cls.matrix, materials=load_materials(),
            bid_text=cls.bid_text, bid_text_label="V4")
        cls.result = linter.run()
        cls.checks = {c["check_id"]: c for c in cls.result["checks"]}

    def test_no_legacy_project_pollution(self):
        self.assertEqual(self.checks["LINT-LEGACY-PROJECT"]["status"], "PASS")

    def test_no_hard_failures(self):
        fails = [c["check_id"] for c in self.result["checks"] if c["status"] == "FAIL"]
        self.assertEqual(fails, [], f"存在 FAIL 项：{fails}")

    def test_legal_keyword_uses_are_not_flagged(self):
        c = self.checks["LINT-INTERNAL-WORDS"]
        self.assertEqual(c["status"], "PASS")
        self.assertIn("合法用法", c["title"])
        for h in c["hits"]:
            self.assertTrue(h.get("suppressed"))

    @unittest.skipUnless(BID_TEXT.exists(), "缺少投标文件文本 fixture（本地）")
    def test_expired_certificate_absent_from_bid(self):
        """已过期且被明确排除的证件，不得出现在投标文件正文里。"""
        self.assertEqual(self.checks["LINT-EXPIRED-CERT"]["status"], "PASS")
        excluded = [m for m in load_materials()
                    if m.get("verification_status") in ("EXPIRED", "REJECTED")
                    and m.get("certificate_number")]
        for m in excluded:
            with self.subTest(material=m["material_id"]):
                self.assertNotIn(m["certificate_number"], self.bid_text)

    def test_pending_items_are_company_side(self):
        for cid in ("LINT-PERSONNEL", "LINT-PRICE", "LINT-BID-SECURITY"):
            with self.subTest(check=cid):
                c = self.checks[cid]
                self.assertEqual(c["status"], "WAIT_COMPANY")
                self.assertEqual(c["owner"], "COMPANY")

    def test_summary_is_three_valued(self):
        self.assertEqual(set(self.result["summary"]) , {"PASS", "FAIL", "WAIT_COMPANY"})


if __name__ == "__main__":
    unittest.main()
