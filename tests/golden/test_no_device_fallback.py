"""护栏：业绩对象词只能来自本项目条款，**绝不默认成某个设备名**。

背景（真出过的事故）：`qualification_classifier` 早期在"词表没命中"时回落成
``["开关柜"]``——那是第一个落地项目的设备。后果是换一个项目后，编译器会拿
A 项目的设备名去判 B 项目的业绩，既可能误判满足，也可能误判不满足。

本用例锁死三条不变量：

1. 规则文件里不得内置任何项目的对象词（``rules/performance_five_elements.json``
   的 ``E4.required_any`` 必须为空）；
2. 条款推不出对象时为**空**，并且 E4 = ``UNKNOWN``、判定 = ``EVIDENCE_INSUFFICIENT``，
   **不得**出现 PASS / FAIL；
3. 结论文本里不得出现任何具体设备默认词。

用到的语料全部在用例内构造（一段推不出对象的业绩条款 + 一份本该"够格"的合同），
不依赖任何真实项目资料，因此在公开仓库里也会执行。
"""

from __future__ import annotations

import json
import shutil
import unittest

from tests.support import PERF_RULES
from jty_bidcompiler.matchers.performance import (
    FiveElementEvaluator, UNKNOWN,
)
from jty_bidcompiler.parsers.qualification_classifier import classify_qualification

#: 设备默认词的"黑名单"：这些词在结论里出现＝又偷偷用了某个项目的设备口径
DEVICE_DEFAULTS = ("开关柜", "变压器", "变电站", "环网柜", "箱变", "配电室", "铁塔",
                   "架空线路", "电力电缆")

#: 一段**推不出工作对象**的业绩条款：有"业绩"、有电压等级（所以会被当成业绩条款），
#: 但没有任何性质词（改造/维修/维护/检修/…），也没有词表里的设备词。
#: 结果必须是 object_keywords=[] 且 source=unresolved。
CLAUSE_WITHOUT_OBJECT = (
    "投标人自2023 年1 月1 日至投标截止时间（以合同签订日期为准），"
    "须具有1 项10kV 及以上电压等级的相关业绩，须提供合同扫描件。"
)

#: 一份"其它要素都够格"的合成合同：主体、时间、电压等级都对得上，
#: 只有"工作对象"因为条款没解析出来而无法认定。
MATERIAL = {
    "material_id": "M-GUARD-001",
    "category": "业绩合同",
    "title": "（合成）某 10kV 设备检修框架合同",
    "company": "合成投标人有限公司",
    "contract_party": "（合成）某单位",
    "contract_date": "2024-06-01",
    "expiry_date": None,
    "voltage_level": "10kV",
    "voltage_level_kv": 10,
    "project_type": "设备检修",
    "keywords": ["10kV", "设备", "检修"],
    "verification_status": "VERIFIED",
    "evidence_spans": [
        {"label": "主体与签订日期",
         "text": "买方：（合成）某单位；卖方：合成投标人有限公司；签订日期：2024年6月1日。",
         "source_ref": "合成合同 封面"},
        {"label": "服务范围",
         "text": "对站内 10kV 设备进行检修、维护及故障处理，含二次装置校验。",
         "source_ref": "合成合同 正文"},
    ],
}

BIDDER = "合成投标人有限公司"
DEADLINE = "2026-06-30"


class TestNoDeviceFallback(unittest.TestCase):
    # ------------------------------------------------------------------ #
    def test_rule_file_has_no_object_default(self):
        """规则文件不得内置对象词——它只提供类型与纪律，不提供项目口径。"""
        rules = json.loads(PERF_RULES.read_text(encoding="utf-8"))
        e4 = next(e for e in rules["elements"] if e["id"] == "E4")
        self.assertEqual(e4["params"].get("required_any"), [],
                         "E4.required_any 必须为空：对象词只能由本项目条款注入")
        # 反例词表（must_not_be_only_these）是**否定**清单，允许出现设备/线路词；
        # 除此之外的正文里不得出现任何具体设备名。
        negative = set(e4["params"].get("must_not_be_only_these") or [])
        blob = json.dumps(rules, ensure_ascii=False)
        for word in negative:
            blob = blob.replace(word, "")
        for word in DEVICE_DEFAULTS:
            with self.subTest(word=word):
                self.assertNotIn(word, blob, f"规则文件里出现了具体设备名：{word}")

    # ------------------------------------------------------------------ #
    def test_clause_without_object_yields_unresolved(self):
        """推不出对象时必须是空列表 + unresolved，不得回落成任何设备名。"""
        cond = classify_qualification(CLAUSE_WITHOUT_OBJECT)
        self.assertEqual(cond["rule"], "PERFORMANCE_FIVE_ELEMENTS")
        self.assertEqual(cond["min_kv"], 10)
        self.assertEqual(cond["object_keywords"], [])
        self.assertEqual(cond["object_keywords_source"], "unresolved")
        for word in DEVICE_DEFAULTS:
            with self.subTest(word=word):
                self.assertNotIn(word, json.dumps(cond, ensure_ascii=False))

    # ------------------------------------------------------------------ #
    def test_missing_object_never_yields_pass_or_fail(self):
        """对象没解析出来时：E4 = UNKNOWN，整体 = EVIDENCE_INSUFFICIENT。"""
        cond = classify_qualification(CLAUSE_WITHOUT_OBJECT)
        ev = FiveElementEvaluator.from_rules_file(
            PERF_RULES, deadline=DEADLINE, condition=cond)
        self.assertEqual(ev.params["E4"].get("required_any"), [],
                         "空对象词必须原样传进引擎，不能回落到规则文件默认值")

        verdict = ev.evaluate(MATERIAL, bidder=BIDDER)
        e4 = next(e for e in verdict.elements if e.element_id == "E4")
        self.assertEqual(e4.status, UNKNOWN)
        self.assertNotEqual(verdict.verdict, "PASS")
        self.assertNotEqual(verdict.verdict, "FAIL")
        self.assertEqual(verdict.verdict, "EVIDENCE_INSUFFICIENT")
        self.assertIn("E4", verdict.blocking_elements)
        blob = " ".join([verdict.verdict_text, e4.reason]
                        + [e.reason for e in verdict.elements])
        for word in DEVICE_DEFAULTS:
            with self.subTest(word=word):
                self.assertNotIn(word, blob, f"结论里出现了设备默认词：{word}")

    # ------------------------------------------------------------------ #
    def test_no_condition_at_all_is_also_insufficient(self):
        """连条件都不传（规则文件默认）时同样不得判 PASS/FAIL。"""
        ev = FiveElementEvaluator.from_rules_file(PERF_RULES, deadline=DEADLINE)
        verdict = ev.evaluate(MATERIAL, bidder=BIDDER)
        self.assertEqual(verdict.verdict, "EVIDENCE_INSUFFICIENT")
        e4 = next(e for e in verdict.elements if e.element_id == "E4")
        self.assertEqual(e4.status, UNKNOWN)

    # ------------------------------------------------------------------ #
    def test_pipeline_level_verdict_is_insufficient(self):
        """整条 pipeline（要求 → 匹配 → 选择）也必须落在"证据不足"，且不选任何素材。"""
        import tempfile
        from pathlib import Path

        from jty_bidcompiler.adapters.repository import LocalMaterialRepository
        from jty_bidcompiler.generators.selection import build_selection
        from jty_bidcompiler.matchers.engine import RequirementMatcher

        requirement = {
            "id": "QUAL-3-3",
            "chapter": "第一章 招标公告 3.3",
            "source_text": CLAUSE_WITHOUT_OBJECT,
            "source_page": 1,
            "source_section": "第一章 3.投标人资格要求",
            "source_quote": CLAUSE_WITHOUT_OBJECT,
            "req_type": "QUALIFICATION",
            "severity": "HARD",
            "condition": classify_qualification(CLAUSE_WITHOUT_OBJECT),
            "condition_text": "业绩要求（对象未解析）",
            "evidence_required": ["业绩合同"],
            "response_location": "六（二）",
        }
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        manifest = tmp / "materials.json"
        manifest.write_text(json.dumps({"materials": [MATERIAL]}, ensure_ascii=False),
                            encoding="utf-8")
        repo = LocalMaterialRepository(manifest)
        matcher = RequirementMatcher(
            repo, rules_path=PERF_RULES.parent / "qualification_rules.json",
            perf_rules_path=PERF_RULES,
            project={"fields": {"deadline": {"value_normalized": "2026-06-30 09:00"}}},
            bidder=BIDDER,
        )
        matrix = matcher.match([requirement])
        entry = matrix["entries"][0]
        self.assertEqual(entry["status"], "EVIDENCE_INSUFFICIENT")
        self.assertIsNone(entry["selected"], "证据不足时不得选中任何业绩")
        blob = json.dumps(entry, ensure_ascii=False)
        for word in DEVICE_DEFAULTS:
            with self.subTest(word=word):
                self.assertNotIn(word, blob)

        selection = build_selection(matrix, [requirement], repo.list_materials(),
                                    project_title="（合成）护栏用例", bidder=BIDDER)
        self.assertEqual(selection["performance_eligibility"]["QUALIFICATION_ELIGIBLE"], [])
        self.assertEqual(selection["performance_eligibility"]["SCORE_ELIGIBLE"], [])


if __name__ == "__main__":
    unittest.main()
