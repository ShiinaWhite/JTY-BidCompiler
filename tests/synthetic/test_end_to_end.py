"""合成语料端到端：公开环境下**不依赖任何真实数据**也能验证全流程。

这份用例是公开仓库的验收闸门，用的是完全合成的招标文件与素材库
（``tests/synthetic/source/``，配置见 ``config/synthetic.json``）：

* 招标解析：12 个字段抽到 11 个（质保期条款缺失 → ``null``，不许猜值）
* 要求解析：资格 3.1～3.4、1.4.3 十六项、评分项、第五章星号条款
* 五要素业绩：合成合同 PASS、新建类合同 FAIL（性质不得扩张解释）
* 三分法：资格可用 / 评分可用 / 仅展示 各归其位
* 结论词表封闭、每条 PASS 都带证据引用
* 跨项目污染检查：产物里不得出现配置里列的其他项目变量
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.support import ROOT

PROFILE = ROOT / "config" / "synthetic.json"
PERF_RULES = ROOT / "rules" / "performance_five_elements.json"
QUAL_RULES = ROOT / "rules" / "qualification_rules.json"

VERDICTS = {"PASS", "FAIL", "EVIDENCE_INSUFFICIENT", "WAIT_COMPANY", "NOT_EVALUATED"}


@unittest.skipUnless(PROFILE.exists(), "缺少合成配置 config/synthetic.json")
class TestSyntheticEndToEnd(unittest.TestCase):
    """合成语料全流程（不读任何 fixtures/ 私有资料）。"""

    @classmethod
    def setUpClass(cls):
        from jty_bidcompiler.adapters.repository import LocalMaterialRepository
        from jty_bidcompiler.parsers.tender_document import load_tender
        from jty_bidcompiler.parsers.tender_fields import extract_project
        from jty_bidcompiler.parsers.tender_requirements import extract_requirements

        cls.profile = json.loads(PROFILE.read_text(encoding="utf-8"))
        cls.tender = ROOT / cls.profile["inputs"]["tender"]
        cls.manifest = ROOT / cls.profile["inputs"]["materials"]
        cls.text, cls.meta = load_tender(cls.tender)
        cls.project = extract_project(cls.text, document_path=str(cls.tender),
                                      document_title=cls.meta["title"])
        cls.rq = extract_requirements(cls.text, document_path=str(cls.tender),
                                      document_sha256=cls.meta["sha256"])
        cls.entries = {r["id"]: r for r in cls.rq["requirements"]}

        cls.repo = LocalMaterialRepository(cls.manifest)
        cls.matcher = cls._matcher()
        cls.matrix = cls.matcher.match(cls.rq["requirements"])
        cls.me = {e["requirement_id"]: e for e in cls.matrix["entries"]}

    @classmethod
    def _matcher(cls):
        from jty_bidcompiler.matchers.engine import RequirementMatcher

        return RequirementMatcher(
            cls.repo, rules_path=QUAL_RULES, perf_rules_path=PERF_RULES,
            project=cls.project, bidder=cls.profile["bidder"],
        )

    # ------------------------------------------------------------------ #
    # MVP-1
    def test_fields_extracted_with_evidence(self):
        fields = self.project["fields"]
        self.assertEqual(len(fields), 12)
        missing = [k for k, v in fields.items() if v["value"] is None]
        # 合成招标文件没有写质保期条款 → 必须是 null，而不是编一个值
        self.assertEqual(missing, ["warranty"])
        for key in ("project_name", "project_number", "tenderer", "deadline",
                    "bid_security", "platform"):
            with self.subTest(field=key):
                f = fields[key]
                self.assertIsNotNone(f["value"])
                self.assertTrue(f["source"]["quote"].strip(), "字段必须带原文引用")
                self.assertIsInstance(f["source"]["page"], int)

    def test_key_field_values(self):
        fields = self.project["fields"]
        self.assertEqual(fields["project_number"]["value"], "XYGS-2026-0417")
        self.assertEqual(fields["deadline"]["value_normalized"], "2026-06-20 09:30")
        self.assertEqual(fields["bid_security"]["value_normalized"], 15000.0)
        self.assertIs(fields["consortium_allowed"]["value_normalized"], False)

    def test_quote_is_verbatim_on_that_page(self):
        """引文必须真的出现在所标页码上（防止页码串位）。"""
        for key, f in self.project["fields"].items():
            if f["value"] is None:
                continue
            with self.subTest(field=key):
                page = self.text.page_text(f["source"]["page"])
                quote = f["source"]["quote"].replace(" ", "").replace("\n", "")
                page_flat = page.replace(" ", "").replace("\n", "")
                self.assertIn(quote, page_flat,
                              f"{key} 的引文在所标页 {f['source']['page']} 上找不到")

    # ------------------------------------------------------------------ #
    # MVP-2
    def test_requirements_extracted(self):
        ids = self.entries
        for rid in ("QUAL-3-1", "QUAL-3-2", "QUAL-3-3", "QUAL-3-4"):
            self.assertIn(rid, ids)
        self.assertEqual(self.entries["QUAL-3-3"]["requirement_type"], "PERFORMANCE")
        self.assertEqual(self.entries["QUAL-3-3"]["severity"], "HARD")
        # 编译器不做判定，判定交给匹配引擎
        self.assertEqual(self.entries["QUAL-3-3"]["status"], "NOT_EVALUATED")

    def test_extraction_has_no_issues(self):
        self.assertEqual(self.rq["extraction_issues"], [])

    def test_performance_condition_comes_from_clause_text(self):
        """业绩条件必须来自条款原文，而不是代码里的常量。"""
        cond = self.entries["QUAL-3-3"]["condition"]
        self.assertEqual(cond["rule"], "PERFORMANCE_FIVE_ELEMENTS")
        self.assertEqual(cond["min_kv"], 10)
        self.assertEqual(cond["not_before"], "2023-01-01")
        self.assertEqual(cond["min_count"], 1)
        self.assertEqual(sorted(cond["nature_keywords"]), ["改造", "维修"])
        # 对象词来自通用词表（不是任何具体项目的设备名）
        self.assertEqual(cond["object_keywords_source"], "lexicon")
        self.assertIn("监测终端", cond["object_keywords"])
        for kw in cond["object_keywords"]:
            self.assertIn(kw, self.entries["QUAL-3-3"]["source_text"])

    def test_sixteen_forbidden_situations(self):
        ids = [r for r in self.entries if r.startswith("QUAL-143-")]
        self.assertEqual(len(ids), 16)

    def test_scoring_items_carry_points(self):
        score = [r for r in self.rq["requirements"] if r["severity"] == "SCORE"]
        self.assertTrue(score)
        for r in score:
            with self.subTest(requirement=r["id"]):
                self.assertTrue(r["source_quote"].strip(), "评分项必须带原文引文")
                self.assertIsInstance(r["source_page"], int)

    def test_scoring_performance_inherits_qualification_condition(self):
        """评分业绩"要求同资格要求"时必须引用资格条件，不得重新猜一套口径。"""
        cond = self.entries["SCORE-BIZ-PERF"]["condition"]
        self.assertEqual(cond["rule"], "PERFORMANCE_SCORING")
        self.assertEqual(cond["condition_ref"], "QUAL-3-3")
        self.assertEqual(cond["additional_points_per_item"], 1)
        self.assertTrue(cond["exclude_qualification_selected"])

    def test_star_clause_is_its_own_hard_segment(self):
        """第五章星号条款是实质性条款，必须单独成段并按 HARD 处理。"""
        spec = [r for r in self.rq["requirements"] if r["id"].startswith("SPEC-")]
        self.assertTrue(spec, "第五章服务标准及要求未抽取到条款")
        starred = [r for r in spec if r["condition"].get("star_clause")]
        self.assertEqual(len(starred), 1, "合成招标文件里有一条星号条款")
        star = starred[0]
        self.assertEqual(star["severity"], "HARD")
        self.assertIn("服务内容有缺项", star["condition"]["star_text"])
        for r in spec:
            with self.subTest(requirement=r["id"]):
                self.assertTrue(r["source_quote"].strip() or r["source_text"].strip())

    # ------------------------------------------------------------------ #
    # MVP-4
    def test_closed_verdict_lexicon(self):
        for rid, e in self.me.items():
            with self.subTest(requirement=rid):
                self.assertIn(e["status"], VERDICTS)

    def test_qualification_verdicts(self):
        self.assertEqual(self.me["QUAL-3-1"]["status"], "PASS")
        self.assertEqual(self.me["QUAL-3-2"]["status"], "PASS")
        self.assertEqual(self.me["QUAL-3-3"]["status"], "PASS")
        self.assertEqual(self.me["SCORE-BIZ-FIN"]["status"], "PASS")
        self.assertEqual(self.me["COMM-1"]["status"], "WAIT_COMPANY")
        self.assertEqual(self.me["COMM-10"]["status"], "WAIT_COMPANY")
        self.assertEqual(self.me["QUAL-143-15"]["status"], "EVIDENCE_INSUFFICIENT")

    def test_every_pass_has_evidence_refs(self):
        for rid, e in self.me.items():
            if e["status"] != "PASS":
                continue
            with self.subTest(requirement=rid):
                chain = e.get("evidence_chain") or []
                self.assertTrue(chain, "PASS 必须带证据链")
                for link in chain:
                    self.assertTrue(str(link.get("quote", "")).strip(),
                                    "证据链每一环都必须引用原文")
                self.assertTrue(e.get("reason"))

    def test_performance_selects_the_qualified_contract(self):
        e = self.me["QUAL-3-3"]
        self.assertEqual(e["selected"], "M-PERF-001")
        cands = {c["material_id"]: c for c in e["candidates"]}
        self.assertEqual(cands["M-PERF-001"]["status"], "PASS")
        self.assertEqual(cands["M-PERF-002"]["status"], "PASS")
        # 新建类工程不得扩张解释成"改造或维修"
        self.assertEqual(cands["M-PERF-091"]["status"], "FAIL")

    def test_new_build_contract_is_rejected_on_nature_and_object(self):
        e = self.me["QUAL-3-3"]
        cand = next(c for c in e["candidates"] if c["material_id"] == "M-PERF-091")
        self.assertEqual(sorted(cand["blocking_elements"]), ["E4", "E5"])

    def test_pending_material_never_becomes_pass(self):
        """素材库自标"待公司提供"的条目不得成为 PASS 候选。"""
        for rid, e in self.me.items():
            for c in e.get("candidates") or []:
                if c["material_id"] in ("M-PER-900", "M-DEV-001"):
                    with self.subTest(requirement=rid, material=c["material_id"]):
                        self.assertNotEqual(c["status"], "PASS")

    def test_three_way_eligibility(self):
        from jty_bidcompiler.generators.selection import build_selection

        sel = build_selection(self.matrix, self.rq["requirements"],
                              self.repo.list_materials(),
                              project_title=self.profile["title"],
                              bidder=self.profile["bidder"])
        tri = sel["performance_eligibility"]
        self.assertEqual(tri["QUALIFICATION_ELIGIBLE"], ["M-PERF-001"])
        self.assertEqual(tri["SCORE_ELIGIBLE"], ["M-PERF-002"])
        self.assertEqual(tri["SHOWCASE_ONLY"], ["M-PERF-091"])
        by_req = {e["requirement_id"]: e for e in sel["entries"]}
        # 资格项归属资格可用；评分项归属评分可用——两个视图必须一致
        self.assertEqual(by_req["QUAL-3-3"]["eligibility"], "QUALIFICATION_ELIGIBLE")
        self.assertEqual(by_req["SCORE-BIZ-PERF"]["eligibility"], "SCORE_ELIGIBLE")

    def test_bid_status_gate(self):
        from jty_bidcompiler.generators.selection import build_bid_status

        st = build_bid_status(self.matrix, self.rq["requirements"], self.project,
                              project_title=self.profile["title"],
                              materials=self.repo.list_materials())
        gate = st["gate"]
        self.assertGreaterEqual(gate["hard_pass"], 4)
        self.assertGreater(gate["wait_company"], 0)
        self.assertFalse(gate["submittable"], "有公司侧待办时不得标记为可提交")

    # ------------------------------------------------------------------ #
    # 产物与污染
    def test_artifacts_validate_and_are_contamination_clean(self):
        from jty_bidcompiler.generators.xlsx_gaps import write_missing_materials
        from jty_bidcompiler.schemas import SchemaError, load_schema, validate
        from jty_bidcompiler.validators import contamination

        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        out = tmp / "run"
        out.mkdir()
        (out / "project.json").write_text(
            json.dumps(self.project, ensure_ascii=False), encoding="utf-8")
        (out / "requirements.json").write_text(
            json.dumps(self.rq, ensure_ascii=False), encoding="utf-8")
        (out / "qualification_matrix.json").write_text(
            json.dumps(self.matrix, ensure_ascii=False), encoding="utf-8")
        write_missing_materials(self.matrix, self.rq["requirements"],
                                out / "缺失材料清单.xlsx",
                                project_title=self.profile["title"])

        for inst, sch in (("project.json", "project.schema.json"),
                          ("requirements.json", "requirements.schema.json"),
                          ("qualification_matrix.json", "qualification_matrix.schema.json")):
            with self.subTest(artifact=inst):
                try:
                    validate(json.loads((out / inst).read_text(encoding="utf-8")),
                             load_schema(ROOT / "schemas" / sch))
                except SchemaError as exc:  # pragma: no cover - 失败即断言失败
                    self.fail(f"{inst} 不符合 schema：{exc}")

        cont = self.profile["contamination"]
        result = contamination.scan_directory(
            str(out), forbidden_tokens=cont["forbidden_tokens"],
            forbidden_label=cont["forbidden_project"])
        self.assertTrue(result["clean"], f"产物出现其他项目变量：{result['hits'][:3]}")


if __name__ == "__main__":
    unittest.main()
