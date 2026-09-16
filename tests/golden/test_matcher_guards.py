"""匹配引擎的护栏测试：防止"看起来能跑"的假通过。

真实事故模式：
* 条件字段名写错 → 校验被空列表绕过 → 明明没有资质却判 PASS；
* 没有素材 → 报 FAIL（其实是"缺"不是"不合规"）→ 公司方向跑偏。
这两类都由本文件锁住。
"""

from __future__ import annotations

import unittest

from tests.support import MATERIALS, PERF_RULES, QUAL_RULES, bidder_of, load_json, load_materials

_BIDDER = bidder_of("sample_a")
from jty_bidcompiler.adapters.repository import LocalMaterialRepository
from jty_bidcompiler.matchers.engine import RequirementMatcher


def _matcher() -> RequirementMatcher:
    return RequirementMatcher(
        LocalMaterialRepository(MATERIALS), rules_path=QUAL_RULES,
        perf_rules_path=PERF_RULES,
        project={"fields": {"deadline": {"value_normalized": "2026-09-18 11:00"}}},
        bidder=_BIDDER,
    )


class TestCertificateGuard(unittest.TestCase):
    def test_empty_condition_is_rejected_loudly(self):
        """既没限定证书名也没限定类别 → 这个条件判不了，必须报错而不是静默通过。

        v0.1.0 真实事故：条件里写 classes、处理函数读 required_classes，
        空列表把校验整个绕过，没有资质的项目也被判成 PASS。
        """
        m = _matcher()
        req = {"id": "QUAL-3-2", "requirement_type": "QUALIFICATION", "severity": "HARD",
               "condition": {"rule": "CERTIFICATE_LEVEL",
                             "material_category": "资质证书_承装修试",
                             "classes": [], "min_level": 3}}
        with self.assertRaises(ValueError) as ctx:
            m._match_one(req)
        self.assertIn("required_classes", str(ctx.exception))

    def test_wrong_level_fails(self):
        """把要求提到二级，三级证书就必须判不满足。"""
        m = _matcher()
        req = {"id": "QUAL-3-2", "requirement_type": "QUALIFICATION", "severity": "HARD",
               "condition": {"rule": "CERTIFICATE_LEVEL",
                             "material_category": "资质证书_承装修试",
                             "license": "承装（修、试）电力设施许可证",
                             "classes": ["承修", "承试"], "min_level": 2,
                             "also_requires": ["安全生产许可证"]}}
        entry = m._match_one(req)
        self.assertEqual(entry["status"], "FAIL")
        # 原因里必须写出实际等级，人工复核才知道差在哪
        self.assertIn("实际三级", entry["reason"])

    def test_unrelated_certificate_not_counted(self):
        """建筑业企业资质证书不能冒充承装修试许可证。"""
        m = _matcher()
        req = {"id": "QUAL-3-2", "requirement_type": "QUALIFICATION", "severity": "HARD",
               "condition": {"rule": "CERTIFICATE_LEVEL",
                             "material_category": "资质证书_承装修试",
                             "license": "承装（修、试）电力设施许可证",
                             "classes": ["承修", "承试"], "min_level": 3}}
        entry = m._match_one(req)
        self.assertEqual(entry["status"], "PASS")
        self.assertEqual(entry["selected"], "M-CERT-001")
        # 建筑业企业资质证书已归属"资质证书_施工总承包"类别，根本不该出现在候选里
        self.assertNotIn("M-CERT-003", [c["material_id"] for c in entry["candidates"]])

    def test_level_numeric_forms_are_equivalent(self):
        """『贰级』=『二级』：招标写二级、证书写贰级，必须能对上。"""
        m = _matcher()
        req = {"id": "QUAL-X", "requirement_type": "QUALIFICATION", "severity": "HARD",
               "condition": {"rule": "CERTIFICATE_LEVEL",
                             "material_category": "资质证书_施工总承包",
                             "license": "电力工程施工总承包", "min_level": 2}}
        entry = m._match_one(req)
        self.assertEqual(entry["status"], "PASS")
        self.assertEqual(entry["selected"], "M-CERT-003")

    def test_wrong_category_fails(self):
        """拿承装修试类别去判电力工程施工总承包 → 必须 FAIL，不得假 PASS。"""
        m = _matcher()
        req = {"id": "QUAL-X2", "requirement_type": "QUALIFICATION", "severity": "HARD",
               "condition": {"rule": "CERTIFICATE_LEVEL",
                             "material_category": "资质证书_承装修试",
                             "license": "电力工程施工总承包", "min_level": 2}}
        self.assertEqual(m._match_one(req)["status"], "FAIL")

    def test_all_of_with_unknown_expiry_is_evidence_insufficient(self):
        """ALL_OF 子项中某证书有效期未登记 → 证据不足，不能替公司认定不合格。"""
        m = _matcher()
        req = {"id": "QUAL-Y", "requirement_type": "QUALIFICATION", "severity": "HARD",
               "condition": {"rule": "ALL_OF", "checks": [
                   {"rule": "CERTIFICATE_LEVEL",
                    "material_category": "资质证书_承装修试",
                    "license": "承装（修、试）电力设施许可证",
                    "classes": ["承修", "承试"], "min_level": 3},
                   {"rule": "CERTIFICATE_VALIDITY", "material_category": "管理体系认证"},
               ]}}
        entry = m._match_one(req)
        self.assertEqual(entry["status"], "EVIDENCE_INSUFFICIENT")
        self.assertTrue(any("有效期" in x for x in entry["missing_info"]))

    def test_all_of_missing_child_fails(self):
        m = _matcher()
        req = {"id": "QUAL-Z", "requirement_type": "QUALIFICATION", "severity": "HARD",
               "condition": {"rule": "ALL_OF", "checks": [
                   {"rule": "CERTIFICATE_LEVEL",
                    "material_category": "资质证书_承装修试",
                    "license": "承装（修、试）电力设施许可证",
                    "classes": ["承修", "承试"], "min_level": 3},
                   {"rule": "CERTIFICATE_VALIDITY", "material_category": "不存在的证书类别"},
               ]}}
        entry = m._match_one(req)
        self.assertEqual(entry["status"], "FAIL")
        self.assertIn("不存在的证书类别", entry["missing_info"])


class TestMissingVsNonCompliant(unittest.TestCase):
    def test_missing_material_is_fail_for_hard_requirement(self):
        """资格类硬要求缺材料 = 明确不满足（不可提交），不是"待提供"。"""
        m = _matcher()
        req = {"id": "QUAL-3-1X", "requirement_type": "QUALIFICATION", "severity": "HARD",
               "condition": {"rule": "MATERIAL_PRESENCE", "material_category": "不存在的类别"}}
        entry = m._match_one(req)
        self.assertEqual(entry["status"], "FAIL")

    def test_company_side_gap_is_wait(self):
        """采购/缴纳这类只能由公司办的事 = 待公司提供。"""
        m = _matcher()
        req = {"id": "COMM-1", "requirement_type": "COMMERCIAL", "severity": "HARD",
               "condition": {"rule": "BID_SECURITY_PAID"}}
        entry = m._match_one(req)
        self.assertEqual(entry["status"], "WAIT_COMPANY")
        self.assertEqual(entry["owner"], "COMPANY")

    def test_unknown_rule_is_not_evaluated_with_compiler_owner(self):
        m = _matcher()
        req = {"id": "SCORE-X", "requirement_type": "TECHNICAL", "severity": "SCORE",
               "condition": {"rule": "SOMETHING_NEW"}}
        entry = m._match_one(req)
        self.assertEqual(entry["status"], "NOT_EVALUATED")
        self.assertEqual(entry["owner"], "COMPILER")

    def test_negative_list_non_credit_is_declaration(self):
        m = _matcher()
        req = {"id": "QUAL-143-9", "requirement_type": "QUALIFICATION", "severity": "HARD",
               "evidence_required": [], "condition": {"rule": "NEGATIVE_LIST_SELFCHECK"}}
        entry = m._match_one(req)
        # GPT 1.5.1 第5项：承诺类不得提前 PASS，待 Word 生成后核验
        self.assertEqual(entry["status"], "NOT_EVALUATED")
        self.assertEqual(entry["owner"], "COMPILER")

    def test_negative_list_credit_uses_credit_queries(self):
        m = _matcher()
        req = {"id": "QUAL-143-14", "requirement_type": "CREDIT", "severity": "HARD",
               "evidence_required": ["信用查询"],
               "condition": {"rule": "NEGATIVE_LIST_SELFCHECK"}}
        entry = m._match_one(req)
        self.assertEqual(entry["status"], "PASS")
        self.assertIn("信用", entry["reason"])


class TestRepositoryAbstraction(unittest.TestCase):
    def test_paperless_adapter_is_explicitly_unimplemented(self):
        from jty_bidcompiler.adapters.repository import (
            PaperlessMaterialRepository, open_repository,
        )

        repo = PaperlessMaterialRepository("https://paperless.local", "token")
        self.assertEqual(repo.describe()["kind"], "paperless_rest")
        with self.assertRaises(NotImplementedError):
            repo.list_materials()
        with self.assertRaises(NotImplementedError):
            open_repository("https://paperless.local")

    def test_local_repository_contract(self):
        repo = LocalMaterialRepository(MATERIALS)
        self.assertTrue(repo.list_materials())
        self.assertTrue(repo.by_category("业绩合同"))
        perf = repo.get("M-PERF-001")
        self.assertEqual(perf["contract_date"], "2024-11-27")
        self.assertTrue(repo.evidence_of("M-PERF-001"))
        self.assertEqual(repo.evidence_of("M-DEV-001"), [])
        self.assertIsNone(repo.get("不存在"))


if __name__ == "__main__":
    unittest.main()
