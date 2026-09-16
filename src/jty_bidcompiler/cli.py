"""JTY-BidCompiler 命令行入口。

五条命令对应 MVP-1～5，外加 ``run`` 一条龙与 ``validate`` 产物契约校验::

    jty parse-tender --tender <pdf> --out-dir <dir>     # MVP-1 + MVP-2
    jty materials    --manifest <json> --out-dir <dir>  # MVP-3
    jty match        --profile sample_a                # MVP-4（含 Excel）
    jty lint         --profile sample_a                # MVP-5
    jty run          --profile sample_a                # 全流程
    jty validate     --dir projects/sample_a           # 产物 schema 校验

设计上刻意保持"薄"：命令只做参数解析与编排，业务逻辑全在 parsers/matchers/
generators/validators 里，方便单独测试与将来做 Web 服务。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, project_root
from .adapters.repository import LocalMaterialRepository, dump_materials
from .generators.selection import build_bid_status, build_selection, write_json as write_selection_json
from .generators.xlsx_gaps import write_missing_materials
from .generators.xlsx_matrix import write_requirements_matrix
from .runmanifest import RunManifest
from .matchers.engine import RequirementMatcher
from .parsers.tender_document import load_tender, sha256_file
from .parsers.tender_fields import extract_project
from .parsers.tender_requirements import extract_requirements
from .schemas import load_schema, validate as schema_validate, SchemaError
from .validators.bid_lint import BidLinter, render_markdown
from .validators import contamination

ROOT = project_root()


def _load_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json(obj, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def _resolve(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else (ROOT / p)


# --------------------------------------------------------------------------- #
# MVP-1 + MVP-2
# --------------------------------------------------------------------------- #
def cmd_parse_tender(args) -> int:
    tender = _resolve(args.tender)
    out_dir = _resolve(args.out_dir)
    text, meta = load_tender(tender)
    print(f"[MVP-1] 读取招标文件：{meta['pages']} 页，sha256={meta['sha256'][:12]}…")

    project = extract_project(text, document_path=str(tender), document_title=meta["title"])
    schema_validate(project, load_schema(ROOT / "schemas" / "project.schema.json"))
    found = sum(1 for f in project["fields"].values() if f["value"] is not None)
    _write_json(project, out_dir / "project.json")
    print(f"[MVP-1] project.json：{found}/{len(project['fields'])} 字段命中")

    rq = extract_requirements(text, document_path=str(tender), document_sha256=meta["sha256"])
    schema_validate(rq, load_schema(ROOT / "schemas" / "requirements.schema.json"))
    _write_json(rq, out_dir / "requirements.json")
    by_sev: dict[str, int] = {}
    for r in rq["requirements"]:
        by_sev[r["severity"]] = by_sev.get(r["severity"], 0) + 1
    print(f"[MVP-2] requirements.json：{len(rq['requirements'])} 条 {by_sev}")
    if rq["extraction_issues"]:
        for issue in rq["extraction_issues"]:
            print(f"         ⚠ {issue}")

    xlsx = write_requirements_matrix(
        rq["requirements"], None, out_dir / args.matrix_name, project_title=meta["title"]
    )
    print(f"[MVP-2] 投标要求矩阵：{xlsx}")
    return 0


# --------------------------------------------------------------------------- #
# MVP-3
# --------------------------------------------------------------------------- #
def cmd_materials(args) -> int:
    out_dir = _resolve(args.out_dir)
    repo = LocalMaterialRepository(_resolve(args.manifest),
                                  asset_root=_resolve(args.asset_root) if args.asset_root else None)
    data = dump_materials(repo, out_dir / "materials.json")
    schema_validate(data, load_schema(ROOT / "schemas" / "materials.schema.json"))
    cats: dict[str, int] = {}
    for m in data["materials"]:
        cats[m["category"]] = cats.get(m["category"], 0) + 1
    print(f"[MVP-3] materials.json：{len(data['materials'])} 条素材，{len(cats)} 个类别")
    for k, v in sorted(cats.items(), key=lambda x: -x[1]):
        print(f"         {k}: {v}")
    return 0


# --------------------------------------------------------------------------- #
# MVP-4
# --------------------------------------------------------------------------- #
def _load_profile(profile: str | Path) -> dict:
    path = Path(profile)
    if not path.exists():
        path = ROOT / "config" / f"{profile}.json"
    if not path.exists():
        raise FileNotFoundError(f"找不到 profile：{profile}")
    return _load_json(path)


def cmd_match(args) -> int:
    prof = _load_profile(args.profile)
    out_dir = _resolve(prof["outputs"]["dir"])
    project = _load_json(out_dir / prof["outputs"]["project"])
    rq = _load_json(out_dir / prof["outputs"]["requirements"])
    repo = LocalMaterialRepository(_resolve(prof["inputs"]["materials"]),
                                  asset_root=_resolve(prof["inputs"]["asset_root"]))

    matcher = RequirementMatcher(
        repo,
        rules_path=_resolve(prof["rules"]["qualification"]),
        perf_rules_path=_resolve(prof["rules"]["performance"]),
        project=project,
        bidder=prof["bidder"],
    )
    matrix = matcher.match(rq["requirements"])
    schema_validate(matrix, load_schema(ROOT / "schemas" / "qualification_matrix.schema.json"))
    _write_json(matrix, out_dir / prof["outputs"]["matrix"])
    print(f"[MVP-4] qualification_matrix.json：{matrix['summary']}")

    xlsx = write_requirements_matrix(
        rq["requirements"], matrix, out_dir / prof["outputs"]["matrix_xlsx"],
        project_title=prof["title"],
    )
    print(f"[MVP-4] 投标要求矩阵：{xlsx}")

    sec = (project.get("fields", {}).get("bid_security", {}) or {}).get("value_normalized")
    dl = (project.get("fields", {}).get("deadline", {}) or {}).get("value_normalized")
    gaps = write_missing_materials(
        matrix, rq["requirements"], out_dir / prof["outputs"]["gaps_xlsx"],
        bid_security_cny=sec, project_title=prof["title"], deadline=dl,
    )
    print(f"[MVP-4] 缺失材料清单：{gaps}")

    # 选择结果与投标状态：装配层将来只消费这两个文件，不重做资格判断
    selection = build_selection(matrix, rq["requirements"], repo.list_materials(),
                               project_title=prof["title"], bidder=prof["bidder"])
    sel_path = write_selection_json(selection, out_dir / prof["outputs"].get(
        "selection", "selected_materials.json"))
    status = build_bid_status(matrix, rq["requirements"], project,
                             project_title=prof["title"], materials=repo.list_materials())
    st_path = write_selection_json(status, out_dir / prof["outputs"].get(
        "bid_status", "bid_status.json"))
    tri = selection["performance_eligibility"]
    print(f"[MVP-4] selected_materials.json：资格可用 {len(tri['QUALIFICATION_ELIGIBLE'])} 项 ｜ "
          f"评分可用 {len(tri['SCORE_ELIGIBLE'])} 项 ｜ 仅展示 {len(tri['SHOWCASE_ONLY'])} 项")
    print(f"[MVP-4] bid_status.json：闭环 {status['gate']['hard_pass']} 项硬性 ｜ "
          f"待公司 {status['gate']['wait_company']} 项 ｜ 不可提交={not status['gate']['submittable']}")

    for e in matrix["entries"]:
        if e["severity"] != "HARD":
            continue
        mark = {"PASS": "PASS", "FAIL": "FAIL", "WAIT_COMPANY": "WAIT_COMPANY",
                "EVIDENCE_INSUFFICIENT": "EV-INSUFF", "NOT_EVALUATED": "NOT-EVAL"}[e["status"]]
        print(f"         {mark:<12s} {e['requirement_id']:<14s} {e['reason'][:70]}")
    return 0


# --------------------------------------------------------------------------- #
# MVP-5
# --------------------------------------------------------------------------- #
def cmd_lint(args) -> int:
    prof = _load_profile(args.profile)
    out_dir = _resolve(prof["outputs"]["dir"])
    project = _load_json(out_dir / prof["outputs"]["project"])
    matrix = _load_json(out_dir / prof["outputs"]["matrix"])
    materials = _load_json(prof["inputs"]["materials"])["materials"]

    bid_text, label = "", ""
    if args.bid_text or prof["inputs"].get("bid_text"):
        p = _resolve(args.bid_text or prof["inputs"]["bid_text"])
        if p.exists():
            bid_text = p.read_text(encoding="utf-8", errors="replace")
            label = p.name
        else:
            print(f"         ⚠ 投标文件文本不存在，跳过文本层检查：{p}")

    linter = BidLinter(rules_path=_resolve(prof["rules"]["lint"]), project=project,
                       matrix=matrix, materials=materials,
                       bid_text=bid_text, bid_text_label=label,
                       legacy_keywords=(prof.get("contamination") or {}).get("forbidden_tokens"))
    result = linter.run()
    schema_validate(result, load_schema(ROOT / "schemas" / "bid_lint.schema.json"))
    _write_json(result, out_dir / prof["outputs"]["lint_json"])
    md = render_markdown(result, project_title=prof["title"])
    (out_dir / prof["outputs"]["lint_md"]).write_text(md, encoding="utf-8")

    print(f"[MVP-5] bid-lint：{result['summary']}")
    for c in result["checks"]:
        print(f"         {c['status']:<13s} {c['title']}")
    return 0


# --------------------------------------------------------------------------- #
def cmd_run(args) -> int:
    import datetime as _dt

    prof = _load_profile(args.profile)
    out_dir = str(_resolve(prof["outputs"]["dir"]))
    run_id = args.run_id or f"{prof['profile']}-{_dt.datetime.now():%Y%m%dT%H%M%S}"

    with RunManifest(ROOT, run_id, profile=prof["profile"],
                     config_path=(ROOT / "config" / f"{args.profile}.json"),
                     inputs=prof["inputs"], outputs_dir=out_dir) as mf:
        ns = argparse.Namespace(
            tender=str(_resolve(prof["inputs"]["tender"])), out_dir=out_dir,
            matrix_name=prof["outputs"]["matrix_xlsx"],
        )
        rc = cmd_parse_tender(ns)
        if rc:
            return rc
        ns = argparse.Namespace(manifest=str(_resolve(prof["inputs"]["materials"])),
                                out_dir=out_dir,
                                asset_root=str(_resolve(prof["inputs"]["asset_root"])))
        rc = cmd_materials(ns)
        if rc:
            return rc
        ns = argparse.Namespace(profile=args.profile)
        rc = cmd_match(ns)
        if rc:
            return rc
        ns = argparse.Namespace(profile=args.profile, bid_text=None)
        rc = cmd_lint(ns)
        if rc:
            return rc
        rc = cmd_validate(argparse.Namespace(dir=out_dir))

        # 把抽取告警与运行告警写进 manifest，便于"为什么这次结果不一样"的追溯
        rq_path = Path(out_dir) / prof["outputs"]["requirements"]
        if rq_path.exists():
            mf.issues(json.loads(rq_path.read_text(encoding="utf-8")).get(
                "extraction_issues", []))
        # 跨项目污染检查：任一项目的产物不得出现其他项目的变量
        cont_cfg = prof.get("contamination") or {}
        if cont_cfg.get("forbidden_tokens"):
            result = contamination.scan_directory(
                out_dir, forbidden_tokens=cont_cfg["forbidden_tokens"],
                forbidden_label=cont_cfg.get("forbidden_project", ""))
            (Path(out_dir) / "contamination.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            if result["clean"]:
                print(f"[contamination] 干净：未发现 {result['forbidden_project']} 项目变量"
                      f"（扫描 {result['files_scanned']} 个产物）")
            else:
                print(f"❌ [contamination] 发现 {result['hit_count']} 处"
                      f" {result['forbidden_project']} 项目变量污染：")
                for h in result["hits"][:10]:
                    print(f"   {h['file']} {h['path']} ← {h['token']}｜{h['snippet'][:60]}")
                rc = 4

        mf.write(status="ok" if rc == 0 else "error")
        print(f"[run] run_manifest.json 已写出：run_id={run_id}")
        return rc


def cmd_validate(args) -> int:
    d = _resolve(args.dir)
    pairs = [
        ("project.json", "project.schema.json"),
        ("requirements.json", "requirements.schema.json"),
        ("materials.json", "materials.schema.json"),
        ("qualification_matrix.json", "qualification_matrix.schema.json"),
        ("bid-lint.json", "bid_lint.schema.json"),
    ]
    bad = 0
    for inst, sch in pairs:
        ip, sp = d / inst, ROOT / "schemas" / sch
        if not ip.exists():
            print(f"         ⚠ 跳过（不存在）：{inst}")
            continue
        try:
            schema_validate(_load_json(ip), load_schema(sp))
            print(f"         ✓ {inst}")
        except SchemaError as exc:
            bad += 1
            print(f"         ✗ {inst} → {exc}")
    print(f"[validate] {'全部通过' if not bad else f'{bad} 个产物不合规'}")
    return 1 if bad else 0


# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="jty", description="JTY-BidCompiler：招标文件 → 可审第一稿（PoC MVP-1～5）"
    )
    p.add_argument("--version", action="version", version=f"jty-bidcompiler {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("parse-tender", help="MVP-1+2：招标文件 → project.json / requirements.json / 矩阵")
    s.add_argument("--tender", required=True)
    s.add_argument("--out-dir", required=True)
    s.add_argument("--matrix-name", default="投标要求矩阵.xlsx")
    s.set_defaults(func=cmd_parse_tender)

    s = sub.add_parser("materials", help="MVP-3：素材 manifest → materials.json")
    s.add_argument("--manifest", required=True)
    s.add_argument("--out-dir", required=True)
    s.add_argument("--asset-root", default=None)
    s.set_defaults(func=cmd_materials)

    s = sub.add_parser("match", help="MVP-4：要求×素材 → 资格矩阵 + 两张 Excel")
    s.add_argument("--profile", default="sample_a")
    s.set_defaults(func=cmd_match)

    s = sub.add_parser("lint", help="MVP-5：投标文件自动 QA")
    s.add_argument("--profile", default="sample_a")
    s.add_argument("--bid-text", default=None)
    s.set_defaults(func=cmd_lint)

    s = sub.add_parser("run", help="跑完整流程（MVP-1～5）")
    s.add_argument("--profile", default="sample_a")
    s.add_argument("--run-id", default=None, help="运行标识（写入 run_manifest.json）")
    s.set_defaults(func=cmd_run)

    s = sub.add_parser("validate", help="校验产物是否符合 schema")
    s.add_argument("--dir", required=True)
    s.set_defaults(func=cmd_validate)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except SchemaError as exc:
        print(f"❌ 产物不符合 schema：{exc}", file=sys.stderr)
        return 2
    except NotImplementedError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
