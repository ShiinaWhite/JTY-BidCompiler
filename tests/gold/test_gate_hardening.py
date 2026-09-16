"""阶段 1.5.1 Gate 收口护栏测试（GPT 指定的 9 项修复的回归锁）。

覆盖：
1. 保证金待办金额/形式必须来自本项目条款（两个示例项目的金额交叉回归）
2. PENDING/REJECTED/EXPIRED-as-of-project 素材不得成为 PASS 候选；候选 id 唯一
3. M-PER-900 不得同时 PASS + WAIT
4. 证件有效期按项目时点判断（同一张证书在两个项目时点下判定相反）
5. 声明/格式类不得提前 PASS（预装配状态语义）
9. 跨项目污染检查
"""

from __future__ import annotations

import unittest

from tests.support import (
    LINT_RULES, MATERIALS, PERF_RULES, QUAL_RULES, bidder_of, load_materials,
)

_BIDDER = bidder_of("sample_a")
from tests.gold.oracle import OracleNotFound, load_oracle
from jty_bidcompiler.adapters.repository import InMemoryMaterialRepository
from jty_bidcompiler.matchers.engine import RequirementMatcher
from jty_bidcompiler.validators import contamination

from pathlib import Path as _P

ROOT = _P(__file__).resolve().parents[2]


def _matcher(materials, *, deadline):
    return RequirementMatcher(
        InMemoryMaterialRepository(materials), rules_path=QUAL_RULES,
        perf_rules_path=PERF_RULES,
        project={"fields": {"deadline": {"value_normalized": deadline + " 15:00"}}},
        bidder=_BIDDER,
    )


def _req(rid, cond, *, severity="HARD", rtype="QUALIFICATION"):
    return {"id": rid, "severity": severity, "requirement_type": rtype, "condition": cond}


BID_SECURITY = {"rule": "BID_SECURITY_PAID", "amount_cny": 72000, "form": "电汇"}


@unittest.skipUnless(MATERIALS.exists(), "真实素材 manifest 不在本机（不入库）")
class TestBidSecurityAmountIsProjectSpecific(unittest.TestCase):
    """GPT 1.5.1 第 1 项：待办金额/形式从本项目条款动态生成。"""

    def test_sample_b_amount_is_72k(self):
        entry = _matcher(load_materials(), deadline="2026-07-16")._match_one(
            _req("COMM-1", BID_SECURITY))
        text = " ".join(entry["missing_info"])
        self.assertIn("7.2万元", text)
        self.assertNotIn("2万元", text.replace("7.2万元", ""))

    def test_sample_a_amount_is_20k(self):
        entry = _matcher(load_materials(), deadline="2026-09-18")._match_one(
            _req("COMM-1", {"rule": "BID_SECURITY_PAID", "amount_cny": 20000, "form": "电汇"}))
        text = " ".join(entry["missing_info"])
        self.assertIn("2万元", text)
        self.assertNotIn("7.2万元", text)

    def test_static_text_gone_from_code_and_rules(self):
        from jty_bidcompiler.matchers import engine
        import inspect
        src = inspect.getsource(engine)
        self.assertNotIn("人民币2万元", src)
        rules_text = LINT_RULES.read_text(encoding="utf-8")
        self.assertNotIn("人民币2万元", rules_text)


@unittest.skipUnless(MATERIALS.exists(), "真实素材 manifest 不在本机（不入库）")
class TestCandidateHygiene(unittest.TestCase):
    """GPT 1.5.1 第 2/3 项：不可用素材不得成为 PASS 候选；候选 id 唯一。"""

    def test_pending_material_never_pass_candidate(self):
        entry = _matcher(load_materials(), deadline="2026-07-16")._match_one(
            _req("SCORE-TECH-STAFF", {"rule": "PERSONNEL_CERTIFIED"}))
        pass_ids = [c["material_id"] for c in entry["candidates"] if c["status"] == "PASS"]
        self.assertNotIn("M-PER-900", pass_ids)
        ids = [c["material_id"] for c in entry["candidates"]]
        self.assertEqual(len(ids), len(set(ids)), "候选 material_id 必须唯一")

    def test_performance_rejected_material_downgraded(self):
        """被排除的候选（REJECTED）即使五要素满足也不得成为 PASS 候选。"""
        entry = _matcher(load_materials(), deadline="2026-07-16")._match_one(
            _req("QUAL-3-3", {"rule": "PERFORMANCE_FIVE_ELEMENTS",
                              "not_before": "2023-01-01", "min_kv": 35,
                              "object_keywords": ["开关柜"], "nature_keywords": ["改造", "维修"],
                              "min_count": 1}))
        cand = {c["material_id"]: c for c in entry["candidates"]}["M-PERF-091"]
        self.assertNotEqual(cand["status"], "PASS")


@unittest.skipUnless(MATERIALS.exists(), "真实素材 manifest 不在本机（不入库）")
class TestExpiryIsProjectTimeBased(unittest.TestCase):
    """GPT 1.5.1 第 4 项：证件有效性以 tender deadline 为准。"""

    B_CERT = "M-PER-001B"   # 一张有明确到期日的证件（到期日介于两个项目截止日之间）

    def test_valid_for_sample_b_deadline(self):
        matcher = _matcher(load_materials(), deadline="2026-07-16")
        m = matcher.repo.get(self.B_CERT)
        self.assertEqual(matcher._as_of_status(m), "VALID_AS_OF_PROJECT")

    def test_expired_for_sample_a_deadline(self):
        matcher = _matcher(load_materials(), deadline="2026-09-18")
        m = matcher.repo.get(self.B_CERT)
        self.assertEqual(matcher._as_of_status(m), "EXPIRED_AS_OF_PROJECT")

    def test_b_cert_pass_candidate_for_sample_b_only(self):
        for deadline, expect_pass in (("2026-07-16", True), ("2026-09-18", False)):
            with self.subTest(deadline=deadline):
                entry = _matcher(load_materials(), deadline=deadline)._match_one(
                    _req("SCORE-TECH-STAFF", {"rule": "PERSONNEL_CERTIFIED"}))
                pass_ids = [c["material_id"] for c in entry["candidates"]
                            if c["status"] == "PASS"]
                (self.assertIn if expect_pass else self.assertNotIn)(
                    self.B_CERT, pass_ids)


@unittest.skipUnless(MATERIALS.exists(), "真实素材 manifest 不在本机（不入库）")
class TestNoPrematurePass(unittest.TestCase):
    """GPT 1.5.1 第 5 项：声明/格式类不得提前 PASS。"""

    DECLARATION_RULES = {
        "BID_VALIDITY_DAYS", "SCAN_COPIES", "NO_NEGATIVE_DEVIATION",
        "NO_SUBCONTRACTING", "DECLARATION",
    }
    COMPANY_ACTION_RULES = {"SUBMIT_BEFORE_DEADLINE", "ESIGN_REQUIRED"}

    def test_declarations_are_not_pass(self):
        matcher = _matcher(load_materials(), deadline="2026-07-16")
        for rule in self.DECLARATION_RULES:
            with self.subTest(rule=rule):
                entry = matcher._match_one(_req("X-" + rule[:6], {"rule": rule}))
                self.assertEqual(entry["status"], "NOT_EVALUATED")
                self.assertEqual(entry["owner"], "COMPILER")

    def test_company_actions_wait_for_company(self):
        matcher = _matcher(load_materials(), deadline="2026-07-16")
        for rule in self.COMPANY_ACTION_RULES:
            with self.subTest(rule=rule):
                entry = matcher._match_one(_req("X-" + rule[:6], {"rule": rule}))
                self.assertEqual(entry["status"], "WAIT_COMPANY")
                self.assertEqual(entry["owner"], "COMPANY")


class TestContaminationValidator(unittest.TestCase):
    """跨项目污染检查（自包含用例，不依赖真实项目配置）。"""

    #: 用两个虚构项目构造词表——公开环境下也能跑
    TOKENS = [
        "示例项目甲", "CCTC00000001", "2099-01-01",
        {"pattern": r"人民币1\.5万元", "label": "保证金金额 人民币1.5万元（示例项目甲口径）"},
        "智能除湿装置",
    ]

    def test_synthetic_pollution_is_caught(self):
        import json
        import tempfile

        from pathlib import Path as _Path
        with tempfile.TemporaryDirectory() as tmp:
            d = _Path(tmp)
            bad = {"entries": [{"requirement_id": "COMM-1",
                                "missing_info": ["投标保证金缴纳凭证（人民币1.5万元，电汇）"]}]}
            (d / "qualification_matrix.json").write_text(
                json.dumps(bad, ensure_ascii=False), encoding="utf-8")
            result = contamination.scan_directory(
                d, forbidden_tokens=self.TOKENS, forbidden_label="示例项目甲")
            self.assertFalse(result["clean"])
            self.assertEqual(result["hits"][0]["token"],
                             "保证金金额 人民币1.5万元（示例项目甲口径）")

    def test_xlsx_and_md_are_scanned(self):
        """.xlsx 与 .md 也必须扫（v1.5.2 漏洞：只扫 JSON 会漏掉最终 Excel）。"""
        import tempfile

        from pathlib import Path as _Path
        from openpyxl import Workbook

        with tempfile.TemporaryDirectory() as tmp:
            d = _Path(tmp)
            wb = Workbook()
            wb.active["D5"] = "材料配件含智能除湿装置"
            wb.save(d / "gaps.xlsx")
            (d / "report.md").write_text("本项目截止日 2099-01-01", encoding="utf-8")
            result = contamination.scan_directory(
                d, forbidden_tokens=self.TOKENS, forbidden_label="示例项目甲")
            files = {h["file"] for h in result["hits"]}
            self.assertIn("gaps.xlsx", files)
            self.assertIn("report.md", files)

    def test_amount_pattern_has_boundary(self):
        """金额 token 必须带边界，避免大金额误命中其中子串。"""
        pat = {"pattern": r"(?<![0-9.])2万元"}
        self.assertFalse(contamination._token_hits("人民币7.2万元", pat))
        self.assertTrue(contamination._token_hits("人民币2万元", pat))

    def test_input_copies_are_excluded(self):
        """素材库 materials.json（公司级）与 contamination.json 自身不参与扫描。"""
        import json
        import tempfile

        from pathlib import Path as _Path
        with tempfile.TemporaryDirectory() as tmp:
            d = _Path(tmp)
            (d / "materials.json").write_text(
                json.dumps({"notes": "示例项目甲 的历史说明"}), encoding="utf-8")
            (d / "contamination.json").write_text(
                json.dumps({"forbidden_project": "示例项目甲"}), encoding="utf-8")
            result = contamination.scan_directory(
                d, forbidden_tokens=self.TOKENS, forbidden_label="示例项目甲")
            self.assertTrue(result["clean"], f"不应命中输入副本：{result['hits']}")

    def test_run_outputs_are_clean_both_ways(self):
        """真实项目的产物必须双向干净（本地有产物时执行）。"""
        for profile, run_dir in (
            ("sample_b", ROOT / "projects/sample_b/run-005-final"),
            ("sample_a", ROOT / "projects/sample_a"),
        ):
            if not run_dir.exists():
                continue
            loaded = contamination.load_profile_tokens(profile)
            if not loaded:
                continue
            tokens, label = loaded
            with self.subTest(profile=profile):
                result = contamination.scan_directory(run_dir, forbidden_tokens=tokens,
                                                      forbidden_label=label)
                self.assertTrue(result["clean"],
                                f"{profile} 产物含 {label} 变量：{result['hits'][:3]}")


if __name__ == "__main__":
    unittest.main()
