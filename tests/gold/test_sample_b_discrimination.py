"""判别性验证：把关键素材拿走，判定必须翻转。

为什么要单独做这件事：
    只核对"结论是否等于期望值"是抓不到**假 PASS** 的。某项目 3.2 要的是
    "电力工程施工总承包二级"，v0.1.0 却拿"承装修试许可证"去判，照样输出 PASS——
    结论看着对，依据完全错；一旦公司真缺施工总承包资质，它也会说"满足"。

    判别性验证专门防这个：把支撑该结论的素材移除，
    判定必须随之变成 FAIL；不变，就说明判定压根没用到那份素材。

Gold 期望值来自 tests/gold/sample_b/qualification.expected.json 的
``discriminating_materials``（src/ 不读该文件）。
"""

from __future__ import annotations

import unittest

from tests.support import PERF_RULES, QUAL_RULES, bidder_of, load_json, load_materials

_BIDDER = bidder_of("sample_a")
from tests.gold.oracle import OracleNotFound, load_oracle
from jty_bidcompiler.adapters.repository import InMemoryMaterialRepository
from jty_bidcompiler.matchers.engine import RequirementMatcher

try:
    QUAL_ORACLE = load_oracle("sample_b", "qualification")
except OracleNotFound as exc:  # 私有 oracle 不在本机 → 跳过
    QUAL_ORACLE = None
    _SKIP_REASON = str(exc)

#: 判别用例要判哪条要求（key 与 oracle 里的 requirement 对应）
REQUIREMENT_BY_CASE = {
    "QUAL-3-1": {"id": "QUAL-3-1", "severity": "HARD", "requirement_type": "QUALIFICATION",
                 "condition": {"rule": "MATERIAL_PRESENCE", "material_category": "营业执照"}},
    "QUAL-3-2": {"id": "QUAL-3-2", "severity": "HARD", "requirement_type": "QUALIFICATION",
                 "condition": {"rule": "ALL_OF", "checks": [
                     {"rule": "CERTIFICATE_LEVEL",
                      "material_category": "资质证书_施工总承包",
                      "license": "电力工程施工总承包", "min_level": 2, "classes": []},
                     {"rule": "CERTIFICATE_VALIDITY", "material_category": "安全生产许可证"},
                 ]}},
    "QUAL-3-2b": {"id": "QUAL-3-2b", "severity": "HARD", "requirement_type": "QUALIFICATION",
                  "condition": {"rule": "ALL_OF", "checks": [
                      {"rule": "CERTIFICATE_LEVEL",
                       "material_category": "资质证书_施工总承包",
                       "license": "电力工程施工总承包", "min_level": 2, "classes": []},
                      {"rule": "CERTIFICATE_VALIDITY", "material_category": "安全生产许可证"},
                  ]}},
    "QUAL-3-3": {"id": "QUAL-3-3", "severity": "HARD", "requirement_type": "PERFORMANCE",
                 "condition": {"rule": "PERFORMANCE_FIVE_ELEMENTS",
                               "not_before": "2023-01-01", "min_kv": 110,
                               "object_keywords": ["输电线路", "线路", "铁塔"],
                               "nature_keywords": ["维护", "维修"], "min_count": 1}},
}


def _matcher(materials: list[dict]) -> RequirementMatcher:
    return RequirementMatcher(
        InMemoryMaterialRepository(materials),
        rules_path=QUAL_RULES, perf_rules_path=PERF_RULES,
        project={"fields": {"deadline": {"value_normalized": "2026-07-16 15:00"}}},
        bidder=_BIDDER,
    )


@unittest.skipUnless(QUAL_ORACLE is not None,
                     "私有项目 oracle 不在本机（属 PRIVATE_TEST_DATA，不入库）")
class TestDiscriminatingMaterials(unittest.TestCase):
    """移除关键素材 → 判定必须翻转。不翻转即"结论对、依据错"。"""

    def test_oracle_declares_discriminating_cases(self):
        cases = QUAL_ORACLE.get("discriminating_materials", {}).get("cases", [])
        self.assertTrue(cases, "oracle 未定义判别性用例")

    def test_removing_key_material_flips_verdict(self):
        cases = QUAL_ORACLE.get("discriminating_materials", {}).get("cases", [])
        for case in cases:
            with self.subTest(case=case["requirement"], remove=case["remove"]):
                req = REQUIREMENT_BY_CASE.get(case["requirement"])
                self.assertIsNotNone(req, f"用例 {case['requirement']} 未定义对应要求")

                # 基线：素材齐全时应当满足
                baseline = _matcher(load_materials())._match_one(dict(req))
                self.assertEqual(baseline["status"], "PASS",
                                 f"基线不满足，用例本身失效：{baseline['reason']}")

                # 移除关键素材后必须翻转
                reduced = InMemoryMaterialRepository(load_materials()).without(case["remove"])
                after = RequirementMatcher(
                    reduced, rules_path=QUAL_RULES, perf_rules_path=PERF_RULES,
                    project={"fields": {"deadline": {"value_normalized": "2026-07-16 15:00"}}},
                    bidder=_BIDDER,
                )._match_one(dict(req))
                self.assertEqual(
                    after["status"], case["expect_after"],
                    f"移除 {case['remove']} 后判定仍为 {after['status']}——"
                    f"说明判定依据不是这些素材（{case.get('why','')}）")

    def test_cross_category_certificate_cannot_substitute(self):
        """把施工总承包证书挪到别的类别，3.2 必须不满足（防拿错证书顶替）。"""
        mats = load_materials()
        tampered = []
        for m in mats:
            m = dict(m)
            if m["material_id"] == "M-CERT-003":
                m["category"] = "其他"
            tampered.append(m)
        entry = _matcher(tampered)._match_one(dict(REQUIREMENT_BY_CASE["QUAL-3-2"]))
        self.assertEqual(entry["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
