"""从产物 JSON 自动生成报告关键结果（GPT 1.5.1 第 6 项）。

背景：v1.5 阶段报告里手写的"资格选用某业绩"与 JSON 实际内容不一致——
人写报告与机器产物脱节。今后关键结论一律由本脚本从产物生成，文档引用脚本
输出，不再手抄。

用法：
    python scripts/render_summary.py --run-dir projects/sample_b/run-003-final
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def render(run_dir: Path) -> str:
    matrix = json.loads((run_dir / "qualification_matrix.json").read_text(encoding="utf-8"))
    selection = json.loads((run_dir / "selected_materials.json").read_text(encoding="utf-8"))
    status = json.loads((run_dir / "bid_status.json").read_text(encoding="utf-8"))
    manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    project = json.loads((run_dir / "project.json").read_text(encoding="utf-8"))

    entries = {e["requirement_id"]: e for e in matrix["entries"]}
    perf = entries.get("QUAL-3-3", {})
    tri = selection.get("performance_eligibility", {})
    gate = status.get("gate", {})

    lines = [
        f"## 关键结果（自动生成自 {run_dir.name}）",
        "",
        f"- 项目：{project['fields']['project_name']['value']}（{project['fields']['project_number']['value']}）",
        f"- 运行：run_id={manifest['run_id']}，git={str(manifest.get('git_commit'))[:8]}，"
        f"耗时 {manifest.get('duration_seconds')}s，抽取告警 {len(manifest.get('extraction_issues') or [])} 条",
        f"- 要求判定汇总：{matrix['summary']}",
        f"- 资格业绩选用：**{perf.get('selected')}**（{next((c['status'] for c in perf.get('candidates', []) if c['material_id'] == perf.get('selected')), 'N/A')}）",
        f"- 三分法：资格可用 {tri.get('QUALIFICATION_ELIGIBLE')} ｜ 评分可用 {tri.get('SCORE_ELIGIBLE')} ｜ 仅展示 {len(tri.get('SHOWCASE_ONLY', []))} 项",
        f"- Gate：hard_fail={gate.get('hard_fail')}，wait_company={gate.get('wait_company')}，"
        f"compiler_todo={gate.get('compiler_todo')}，可提交={gate.get('submittable')}",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--write", default=None, help="把摘要写入指定文件（默认只打印）")
    args = ap.parse_args()
    text = render(ROOT / args.run_dir)
    print(text)
    if args.write:
        out = ROOT / args.write
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
