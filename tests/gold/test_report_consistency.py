"""报告与产物一致性测试（GPT 1.5.1 第 6 项）。

v1.5 事故：阶段报告手写"资格选用 M-PERF-003"，selected_materials.json 实际是
M-PERF-002——两边都"对"，合起来就是矛盾。本测试锁住三件事：

1. selected ∈ 三分法的 QUALIFICATION_ELIGIBLE（选择结果内部自洽）；
2. QUALIFICATION_ELIGIBLE 与 SCORE_ELIGIBLE 不相交，且并集 == 五要素 PASS 候选；
3. 阶段报告中提到的资格业绩选用 id 与 JSON 一致（文档不许手抄漂移）。
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(p: Path):
    return json.loads(Path(p).read_text(encoding="utf-8"))


class TestSelectionInternalConsistency(unittest.TestCase):
    def test_each_run_selection_matches_matrix(self):
        runs = [
            ROOT / "projects/sample_b/run-003-final",
            ROOT / "projects/sample_a",
        ]
        cases = [r for r in runs if (r / "selected_materials.json").exists()]
        if not cases:
            self.skipTest("本机没有可检查的运行产物（真实项目产物不入库）")
        for run_dir in cases:
            with self.subTest(run=run_dir.name):
                matrix = _load(run_dir / "qualification_matrix.json")
                selection = _load(run_dir / "selected_materials.json")
                tri = selection["performance_eligibility"]

                # 遍历**全部**带候选的 PERFORMANCE entry（资格 QUAL-3-3 + 评分
                # SCORE-BIZ-PERF）。v1.5.2 只验第一个，导致评分假 PASS 带着全绿。
                perf_entries = [e for e in matrix["entries"]
                                if e["requirement_type"] == "PERFORMANCE"
                                and e.get("candidates")]
                self.assertGreaterEqual(len(perf_entries), 2,
                                        "资格业绩与评分业绩都应有候选")
                for perf in perf_entries:
                    with self.subTest(entry=perf["requirement_id"]):
                        pass_ids = {c["material_id"] for c in perf["candidates"]
                                    if c["status"] == "PASS"}
                        elig_ids = {c["material_id"] for c in perf["candidates"]
                                    if c.get("eligibility") == "SCORE_ELIGIBLE"
                                    and c.get("status") == "PASS"}
                        if perf["requirement_id"] == "QUAL-3-3":
                            # 资格条目：三分法三元组必须与 PASS 候选一致
                            qual = set(tri["QUALIFICATION_ELIGIBLE"])
                            score = set(tri["SCORE_ELIGIBLE"])
                            self.assertFalse(qual & score,
                                             "同一业绩不能既是资格选用又是评分可用")
                            self.assertEqual(qual | score, pass_ids,
                                             "三分法必须覆盖全部 PASS 候选")
                            if perf.get("selected"):
                                self.assertIn(perf["selected"], qual)
                        else:
                            # 评分条目：资格占用的业绩不得再作为评分 PASS
                            if tri["QUALIFICATION_ELIGIBLE"]:
                                for mid in tri["QUALIFICATION_ELIGIBLE"]:
                                    c = next((c for c in perf["candidates"]
                                              if c["material_id"] == mid), None)
                                    if c:
                                        self.assertNotEqual(
                                            c.get("eligibility"), "SCORE_ELIGIBLE",
                                            f"{mid} 已被资格占用，不得再计入评分")


class TestReportMatchesJson(unittest.TestCase):
    """文档里的关键结论必须与 JSON 一致（不许手抄漂移）。"""

    @staticmethod
    def _report_path():
        """本地私有阶段报告（不入库）；找不到时本类用例自动跳过。"""
        hits = sorted((ROOT / "docs").glob("阶段1.5*报告.md"))
        return hits[0] if hits else (ROOT / "docs" / "_missing_.md")

    def test_report_selection_matches_json(self):
        if not self._report_path().exists():
            self.skipTest("阶段报告为本地私有文档，未纳入仓库")
        run_dir = ROOT / "projects/sample_b/run-005-final"
        if not (run_dir / "selected_materials.json").exists():
            self.skipTest("run-005-final 尚未生成")
        tri = _load(run_dir / "selected_materials.json")["performance_eligibility"]
        report = self._report_path().read_text(encoding="utf-8")
        for mid in tri["QUALIFICATION_ELIGIBLE"]:
            with self.subTest(selected=mid):
                self.assertIn(mid, report,
                              f"阶段报告未写明 JSON 中的资格选用 {mid}")
        for mid in tri["SCORE_ELIGIBLE"]:
            with self.subTest(score_eligible=mid):
                self.assertIn(mid, report,
                              f"阶段报告未写明 JSON 中的评分可用 {mid}")


if __name__ == "__main__":
    unittest.main()


class TestCrossViewConsistency(unittest.TestCase):
    """GPT 1.5.3-hotfix：selected_materials.entries 与 performance_eligibility 必须一致。

    v1.5.3 事故：SCORE-BIZ-PERF 选中 M-PERF-003（评分可用），但 entry 级
    eligibility 被错标为 QUALIFICATION_ELIGIBLE——如果 Word assembler 消费
    entry 级字段，就会把尧钢错当资格材料。
    """

    def _load_run(self, run_dir):
        matrix = _load(ROOT / run_dir / "qualification_matrix.json")
        selection = _load(ROOT / run_dir / "selected_materials.json")
        return matrix, selection

    def test_sample_b_cross_view(self):
        run_dir = "projects/sample_b/run-005-final"
        try:
            matrix, selection = self._load_run(run_dir)
        except FileNotFoundError:
            self.skipTest("run-005-final 尚未生成")
        entries = {e["requirement_id"]: e for e in selection["entries"]}
        tri = selection["performance_eligibility"]

        # 逐条断言（GPT 指定的两个最小用例）
        self.assertEqual(entries["QUAL-3-3"]["selected"], "M-PERF-002")
        self.assertEqual(entries["QUAL-3-3"]["eligibility"], "QUALIFICATION_ELIGIBLE")
        self.assertEqual(entries["SCORE-BIZ-PERF"]["selected"], "M-PERF-003")
        self.assertEqual(entries["SCORE-BIZ-PERF"]["eligibility"], "SCORE_ELIGIBLE")

    def test_entry_eligibility_matches_tri_view(self):
        """通用不变量：entry 级 eligibility 必须与三分法汇总一致。"""
        run_dir = "projects/sample_b/run-005-final"
        try:
            matrix, selection = self._load_run(run_dir)
        except FileNotFoundError:
            self.skipTest("run-005-final 尚未生成")
        tri = selection["performance_eligibility"]
        qual_set = set(tri["QUALIFICATION_ELIGIBLE"])
        score_set = set(tri["SCORE_ELIGIBLE"])

        for e in selection["entries"]:
            mid, elig, role = e.get("selected"), e.get("eligibility"), e.get("role")
            if not mid or not elig or elig == "SHOWCASE_ONLY":
                continue
            with self.subTest(requirement=e["requirement_id"], selected=mid, eligibility=elig):
                if elig == "QUALIFICATION_ELIGIBLE":
                    self.assertIn(mid, qual_set,
                                  f"{mid} 标为资格可用但不在三分法资格集合中")
                    self.assertNotIn(mid, score_set,
                                     f"{mid} 不能同时是评分可用")
                elif elig == "SCORE_ELIGIBLE":
                    self.assertIn(mid, score_set,
                                  f"{mid} 标为评分可用但不在三分法评分集合中")
                    self.assertNotIn(mid, qual_set,
                                     f"{mid} 不能同时是资格可用")

    def test_sample_a_cross_view(self):
        run_dir = "projects/sample_a"
        try:
            matrix, selection = self._load_run(run_dir)
        except FileNotFoundError:
            self.skipTest("对照项目产物尚未生成（本地）")
        entries = {e["requirement_id"]: e for e in selection["entries"]}
        # 对照项目：资格选用该业绩，评分新增为空
        self.assertEqual(entries["QUAL-3-3"]["eligibility"], "QUALIFICATION_ELIGIBLE")
        score_entry = entries.get("SCORE-BIZ-PERF")
        if score_entry and score_entry.get("selected"):
            # 评分新增为空 → selected 回退到资格占用业绩，
            # eligibility 正确为 QUALIFICATION_ELIGIBLE（不算评分加分）
            self.assertIn(score_entry["eligibility"],
                          ("SCORE_ELIGIBLE", "QUALIFICATION_ELIGIBLE"))
