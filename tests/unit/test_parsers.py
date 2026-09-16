"""MVP-1 / MVP-2 解析器测试（跑真实招标文件 fixture）。

这些用例锁的是**页码与原文引用**：解析结果必须能回到招标文件的具体位置。
只断言"抽到了值"是不够的——值对、页码错，人工复核时一样找不到依据。
"""

from __future__ import annotations

import unittest

from tests.support import requires_tender, tender_text, load_json, ROOT, load_profile

_PROJECT_NUMBER = load_profile("sample_a")["project_number"]
from jty_bidcompiler.parsers.tender_fields import extract_project
from jty_bidcompiler.parsers.tender_requirements import extract_requirements


@requires_tender()
class TestFieldExtraction(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        text, meta = tender_text()
        cls.meta = meta
        cls.project = extract_project(text, document_path=str(meta["path"]),
                                      document_title=meta["title"])

    def test_all_twelve_fields_found(self):
        missing = [k for k, v in self.project["fields"].items() if v["value"] is None]
        self.assertEqual(missing, [], f"未抽到的字段：{missing}")

    def test_key_values_and_pages(self):
        f = self.project["fields"]
        self.assertEqual(f["project_number"]["value"], _PROJECT_NUMBER)
        self.assertEqual(f["project_number"]["source_page"], 1)
        self.assertTrue(f["project_name"]["value"], "项目名称不应为空")
        self.assertEqual(f["deadline"]["value_normalized"], "2026-09-18 11:00")
        self.assertEqual(f["bid_security"]["value_normalized"], 20000.0)
        self.assertIs(f["consortium_allowed"]["value_normalized"], False)
        self.assertTrue(f["agency"]["value"], "招标代理机构不应为空")

    def test_every_field_carries_evidence_location(self):
        for name, field in self.project["fields"].items():
            with self.subTest(field=name):
                self.assertIsNotNone(field["source_page"])
                self.assertIsNotNone(field["source"])
                self.assertTrue(field["source"]["quote"].strip())
                self.assertGreater(field["confidence"], 0)

    def test_quote_is_verbatim_from_that_page(self):
        """引用的原文必须真的出现在所标页码上（防止页码串位）。"""
        text, _ = tender_text()
        for name, field in self.project["fields"].items():
            with self.subTest(field=name):
                page = text.page(field["source_page"]).raw
                quote = field["source"]["quote"]
                # 引文可能被压缩空白，比对时同样压缩
                self.assertIn(
                    quote.replace(" ", "")[:12],
                    page.replace(" ", "").replace("\n", ""),
                    f"{name} 的引文在所标页 {field['source_page']} 上找不到",
                )


@requires_tender()
class TestRequirementExtraction(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        text, meta = tender_text()
        cls.rq = extract_requirements(text, document_path=str(meta["path"]),
                                      document_sha256=meta["sha256"])

    def test_no_extraction_issues(self):
        self.assertEqual(self.rq["extraction_issues"], [])

    def test_qualification_items(self):
        ids = [r["id"] for r in self.rq["requirements"]]
        for rid in ("QUAL-3-1", "QUAL-3-2", "QUAL-3-3", "QUAL-3-4"):
            self.assertIn(rid, ids)

    def test_qualification_source_text_is_verbatim(self):
        r = self._by_id("QUAL-3-3")
        self.assertIn("2023", r["source_text"])
        self.assertIn("10KV", r["source_text"])
        self.assertIn("高压开关柜改造或维修", r["source_text"])
        self.assertIn("合同签订日期为准", r["source_text"])
        self.assertEqual(r["source_page"], 3)
        self.assertEqual(r["severity"], "HARD")
        self.assertEqual(r["requirement_type"], "PERFORMANCE")
        self.assertEqual(r["status"], "NOT_EVALUATED")  # 解析器不下判定

    def test_performance_condition_is_parsed_from_requirement_text(self):
        """业绩条件的阈值与词表必须来自条款原文，不能来自代码常量。"""
        cond = self._by_id("QUAL-3-3")["condition"]
        self.assertEqual(cond["rule"], "PERFORMANCE_FIVE_ELEMENTS")
        self.assertEqual(cond["not_before"], "2023-01-01")
        self.assertEqual(cond["min_kv"], 10)
        self.assertEqual(cond["object_keywords"], ["开关柜"])
        self.assertEqual(cond["nature_keywords"], ["改造", "维修"])
        self.assertEqual(cond["min_count"], 1)

    def test_certificate_condition_is_all_of_with_classes(self):
        """3.2 同句含两个条件（承装修试 + 安许），必须是 ALL_OF。"""
        cond = self._by_id("QUAL-3-2")["condition"]
        self.assertEqual(cond["rule"], "ALL_OF")
        checks = {c["rule"]: c for c in cond["checks"]}
        self.assertIn("CERTIFICATE_LEVEL", checks)
        self.assertIn("CERTIFICATE_VALIDITY", checks)
        lic = checks["CERTIFICATE_LEVEL"]
        self.assertEqual(lic["classes"], ["承修", "承试"])  # 不含"承装"——那是证书名里的字
        self.assertEqual(lic["min_level"], 3)
        self.assertEqual(lic["material_category"], "资质证书_承装修试")

    def test_condition_is_content_driven_not_clause_number_driven(self):
        """换项目后同号条款语义不同，条件必须跟着文字走。

        示例项目A 的 3.2 = 承装修试许可证；示例项目B 的 3.2 = 电力工程施工总承包二级。
        按条款号硬绑定的话，B 会被拿承装修试去判，产出假 PASS。
        """
        from jty_bidcompiler.parsers.qualification_classifier import classify_qualification

        other_project_32 = ("3.2 资格要求2：投标人须具备电力工程施工总承包二级及以上资质"
                     "(提供资质证书扫描件），提供有效期内的安全生产许可证（提供证书扫描件）；")
        cond = classify_qualification(other_project_32)
        self.assertEqual(cond["rule"], "ALL_OF")
        lic = next(c for c in cond["checks"] if c["rule"] == "CERTIFICATE_LEVEL")
        self.assertIn("电力工程施工总承包", lic["license"])
        self.assertEqual(lic["material_category"], "资质证书_施工总承包")
        self.assertEqual(lic["min_level"], 2)
        self.assertEqual(lic["classes"], [])

    def test_forbidden_situations_complete_16_items(self):
        ids = {r["id"] for r in self.rq["requirements"] if r["id"].startswith("QUAL-143-")}
        self.assertEqual(len(ids), 16)
        self.assertIn("QUAL-143-13", ids)   # gsxt 严重违法失信
        self.assertIn("QUAL-143-15", ids)   # 集团/行业失信惩戒

    def test_credit_items_require_credit_evidence(self):
        r = self._by_id("QUAL-143-14")
        self.assertEqual(r["requirement_type"], "CREDIT")
        self.assertEqual(r["evidence_required"], ["信用查询"])
        plain = self._by_id("QUAL-143-9")
        self.assertEqual(plain["evidence_required"], [])

    def test_scoring_items_points(self):
        score = {r["id"]: r for r in self.rq["requirements"] if r["severity"] == "SCORE"}
        self.assertEqual(score["SCORE-TECH-PLAN"]["note"], "分值 20 分")
        self.assertEqual(score["SCORE-BIZ-PERF"]["severity"], "SCORE")

    def test_commercial_conditions_parsed(self):
        self.assertEqual(self._by_id("COMM-1")["condition"]["amount_cny"], 20000.0)
        self.assertEqual(self._by_id("COMM-2")["condition"]["days"], 120)

    def test_every_requirement_has_page_and_text(self):
        for r in self.rq["requirements"]:
            with self.subTest(rid=r["id"]):
                self.assertGreaterEqual(r["source_page"], 1)
                self.assertTrue(r["source_text"].strip() or r.get("note"))

    def _by_id(self, rid: str) -> dict:
        for r in self.rq["requirements"]:
            if r["id"] == rid:
                return r
        self.fail(f"缺少要求 {rid}")


if __name__ == "__main__":
    unittest.main()
