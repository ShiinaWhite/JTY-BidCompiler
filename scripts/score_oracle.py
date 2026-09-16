"""把某次运行结果与 Gold oracle 对账，输出覆盖率/准确率报告。

用法：
    python scripts/score_oracle.py --project sample_b --run-dir projects/sample_b/run-001-blind
    python scripts/score_oracle.py --project sample_b --run-dir projects/sample_b/run-002-fixed --label run-002

本脚本属于**测试侧**：它读取 tests/gold/ 下的期望值，src/ 不会也不允许读取它们。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))  # 让 tests.gold.oracle 可导入

from tests.gold import oracle as oracle_mod  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True, help="Gold 项目名（tests/gold/<name>/）")
    ap.add_argument("--run-dir", required=True, help="运行产物目录")
    ap.add_argument("--label", default=None, help="报告标签，默认取 run-dir 末级目录名")
    ap.add_argument("--out-dir", default="reports")
    args = ap.parse_args()

    run_dir = ROOT / args.run_dir
    label = args.label or run_dir.name

    project_json = json.loads((run_dir / "project.json").read_text(encoding="utf-8"))
    requirements = json.loads((run_dir / "requirements.json").read_text(encoding="utf-8"))
    matrix = json.loads((run_dir / "qualification_matrix.json").read_text(encoding="utf-8"))
    materials = json.loads((run_dir / "materials.json").read_text(encoding="utf-8"))["materials"]
    bid_status_path = run_dir / "bid_status.json"
    if bid_status_path.exists():
        bs = json.loads(bid_status_path.read_text(encoding="utf-8"))
        matrix["_materials_pending"] = bs.get("materials_pending", [])

    p_oracle = oracle_mod.load_oracle(args.project, "project")
    r_oracle = oracle_mod.load_oracle(args.project, "requirements")
    q_oracle = oracle_mod.load_oracle(args.project, "qualification")
    s_oracle = oracle_mod.load_oracle(args.project, "bid_status")

    project_score = oracle_mod.score_project(project_json, p_oracle)
    req_score = oracle_mod.score_requirements(requirements["requirements"], r_oracle)
    qual_score = oracle_mod.score_qualification(matrix, q_oracle, materials=materials)
    status_score = oracle_mod.score_bid_status(
        project_json, matrix, s_oracle,
        materials_before=len(materials), materials_after=len(materials),
    )
    summary = oracle_mod.summarize(project_score, req_score, qual_score, status_score)
    md = oracle_mod.render_markdown(label, summary, project_score, req_score,
                                    qual_score, status_score)

    out_dir = ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {"label": label, "run_dir": args.run_dir, "summary": summary,
               "project": project_score, "requirements": req_score,
               "qualification": qual_score, "bid_status": status_score}
    (out_dir / f"oracle-score-{label}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / f"oracle-score-{label}.md").write_text(md, encoding="utf-8")

    print(md)
    print(f"\n已写入 reports/oracle-score-{label}.json / .md")

    # 退出码：Gate 不满足即非零（便于 CI/脚本判定）
    gate_ok = (
        (summary["HARD_recall"] or 0) >= 1.0
        and summary.get("score_objective_errors", 0) == 0
        and summary["false_pass"] == 0
        and summary["false_fail"] == 0
        and summary["fabrication_count"] == 0
        and summary["materials_stable"]
    )
    print("GATE:", "PASS" if gate_ok else "FAIL")
    return 0 if gate_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
