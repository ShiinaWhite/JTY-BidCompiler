"""《缺失材料清单.xlsx》生成（MVP-4 产物）。

这张表只回答一个问题：**还差什么，谁去办。**

因此刻意做了两处过滤：
1. 只收 ``WAIT_COMPANY`` / ``FAIL`` / ``EVIDENCE_INSUFFICIENT``，不收 PASS；
2. 责任方为 ``COMPILER`` 的项（内容质量、格式等编译器自己能干的活）单列一张
   sheet，不混进"要公司去办的事"——否则公司会被一堆不是它的活淹没。

真实项目里最容易误报的两项（资格业绩、近三年审计报告）在 Gold Test 中
应当**不出现**在本表主表里；这正是检验编译器是否真的可用的关键指标。
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

HEADERS = [
    ("序号", 6), ("材料类别", 22), ("需提供材料 / 信息", 46), ("具体要求", 60),
    ("需求原因", 40), ("关联要求编号", 18), ("当前状态", 20), ("责任方", 10),
]

_HEAD_FILL = PatternFill("solid", fgColor="7F3F00")
_HEAD_FONT = Font(color="FFFFFF", bold=True, size=10)
_THIN = Side(style="thin", color="B7B7B7")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
_OWNER_CN = {"COMPANY": "公司", "USER": "用户", "COMPILER": "编译器"}

#: 未决项目录（人写的口径，代码只负责对齐）
PENDING_CATALOG = {
    "项目团队": ("本项目实际拟派人员名单及岗位",
               "至少明确：项目经理、技术负责人、安全管理人员、质量管理人员、施工人员、"
               "继电保护/调试人员、电气试验人员、高压电工作业人员；并提供每人姓名、拟任岗位、"
               "性别、学历及专业、职称（如有）、参加工作年限。",
               "需要按实际拟派人员确定，编译器不得虚构"),
    "人员证件": ("拟派人员全部相关证明材料（每人一组）",
               "身份证、学历证书、职称证书、注册类证书（如有）、安全生产考核合格证书A/B/C证"
               "（如岗位涉及）、施工现场岗位证书、特种作业操作证、劳动合同/劳务合同、近期社保材料；"
               "所有有有效期的证件应覆盖 {deadline}。",
               "作为人员能力及持证上岗证明"),
    "设备机具": ("本项目实际能够投入的施工设备及试验仪器清单",
               "按实际情况填写：名称、型号、数量、自有/租赁/调配/拟采购、是否能投入本项目、"
               "检定或校准情况；并请逐项确认技术方案中承诺的施工与试验能力是否真实可落实。",
               "技术方案已承诺相应施工及试验能力，需确认实际能够落实"),
    "报价资料": ("本项目最终报价及材料设备品牌、型号",
               "投标总报价（含税）、大写金额、增值税税率；按本项目报价表格式填写各分项报价；"
               "材料及配件的规格型号/品牌/单价/合价（以本项目招标清单为准）。",
               "报价表和材料配件报价明细必须填写完整；报价属公司经营决策，编译器不得代填"),
    "投标保证金": ("投标保证金缴纳凭证",
                  "按本项目招标要求完成缴纳后，提供对应本项目的缴纳凭证扫描件（须从公司账户电汇）。",
                  "正式投标文件必须附保证金缴纳证明"),
    "其他": ("公司认为还需要补充的其他证明材料",
            "如公司另有能够证明企业实力、本项目人员能力、设备能力或类似项目经验的有效材料，可一并提供。",
            "用于最终投标材料完善"),
}


def write_missing_materials(
    matrix: dict,
    requirements: list[dict],
    out_path: str | Path,
    *,
    bid_security_cny: float | None = None,
    project_title: str = "",
    deadline: str | None = None,
) -> Path:
    # 保证金金额必须取**本项目**条款（v1.5 曾把 A 项目的金额带进 B 项目的清单）
    bid_security_text = None
    if bid_security_cny:
        wan = bid_security_cny / 10000
        bid_security_text = f"人民币{wan:g}万元"
    elif project_title:
        bid_security_text = "金额见本项目招标条款"
    req_by_id = {r["id"]: r for r in requirements}
    company_rows: dict[str, list[str]] = defaultdict(list)
    compiler_rows: list[tuple[str, str, str]] = []

    for entry in matrix.get("entries", []):
        status = entry["status"]
        if status == "PASS":
            continue
        req = req_by_id.get(entry["requirement_id"], {})
        missing = entry.get("missing_info") or []
        if not missing and status in ("EVIDENCE_INSUFFICIENT", "FAIL"):
            missing = [f"{entry['requirement_id']}：{entry.get('reason', '')}"]
        if entry.get("owner") == "COMPILER":
            for m in missing or [entry.get("reason", "")]:
                compiler_rows.append((entry["requirement_id"], m, status))
            continue
        # 归类到公司侧目录
        for item in missing or [entry.get("reason", "")]:
            cat = _classify(item, req)
            company_rows[cat].append(f"{entry['requirement_id']}({status}) {item}")
            company_rows.setdefault("_req_ids", {}).setdefault(cat, []).append(
                entry["requirement_id"])

    wb = Workbook()
    ws = wb.active
    ws.title = "缺失材料清单"
    _title(ws, f"{project_title} —— 缺失材料清单（需公司/用户提供）", len(HEADERS))
    ws.append([h for h, _ in HEADERS])
    _header(ws, len(HEADERS))

    n = 0
    for category, (need, detail, reason) in PENDING_CATALOG.items():
        rows = company_rows.get(category) or []
        # "其他"是常设兜底行：人工清单里一直都有它，且招标第六章（七）其他资料、
        # 十一"认为需要加以说明的其他内容"本身就是开放性要求，所以无条件保留。
        if not rows and category != "其他":
            continue
        n += 1
        reqs = sorted({r.split("(")[0] for r in rows if r.startswith(("QUAL", "COMM", "SCORE"))})

        if category == "投标保证金" and bid_security_text:
            need = f"{bid_security_text}{need}"
        # GPT 1.5.2 第1项：detail 里的截止日与业务要点必须来自**本项目**
        detail = detail.replace("{deadline}", deadline or "投标截止日")
        hints = _requirement_hints(
            category, requirements,
            company_rows.get("_req_ids", {}).get(category, []))
        if hints:
            detail = f"{detail}\n本项目相关条款：{hints}"
        ws.append([n, category, need, _clip(detail, 500), reason,
                   "、".join(reqs) or "—", "待提供", "公司"])
    _body(ws, len(HEADERS), start_row=3)
    ws.freeze_panes = "A3"

    ws2 = wb.create_sheet("编译器待办")
    ws2.append(["关联要求", "事项", "当前状态"])
    _header(ws2, 3, header_row=1)
    # 第五章条款数以十计，逐条罗列会把这张表淹掉；汇总成一行并给出条数，
    # 想看明细的去看 qualification_matrix.json 或投标要求矩阵。
    spec_rows = [r for r in compiler_rows if r[0].startswith("SPEC-")]
    other_rows = [r for r in compiler_rows if not r[0].startswith("SPEC-")]
    for rid, item, status in other_rows:
        ws2.append([rid, _clip(item, 400), status])
    if spec_rows:
        ws2.append([f"SPEC-* × {len(spec_rows)}",
                    f"第五章/技术规格书条款共 {len(spec_rows)} 条需在服务方案与偏差表中响应"
                    f"（含星号实质性条款，负偏离即否决）——详见投标要求矩阵",
                    "NOT_EVALUATED"])
    for col, width in zip("ABC", (18, 100, 20)):
        ws2.column_dimensions[col].width = width

    ws3 = wb.create_sheet("说明")
    for line in [
        ["本清单只列需要公司/用户动手的未决项；编译器自身待办见「编译器待办」表。"],
        ["已在素材库闭环的资格项（如资格业绩、近三年审计报告）不应出现在本表；"
         "若出现即为误报，需检查素材 manifest 与判定依据。"],
        ["报价、保证金、人员、签章属公司真实事项，编译器只做完整性检查，不代填、不虚构。"],
        [f"判定口径：{matrix.get('verdict_lexicon', {})}"],
    ]:
        ws3.append(line)
    ws3.column_dimensions["A"].width = 120

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    return out_path


# --------------------------------------------------------------------------- #
def _requirement_hints(category: str, requirements: list[dict], req_ids: list[str]) -> str:
    """从**当前项目**的 requirements 里取关联条款摘要（去项目化的关键）。

    缺失清单的"具体要求"必须来自本项目招标条款，而不是上一项目的记忆。
    """
    req_by_id = {r["id"]: r for r in requirements}
    seen, parts = set(), []
    for rid in req_ids:
        r = req_by_id.get(rid)
        if not r or rid in seen:
            continue
        seen.add(rid)
        text = (r.get("source_text") or "").strip()
        if text:
            parts.append(f"{rid}：{text[:80]}")
    return " ｜ ".join(parts[:3])


def _classify(item: str, req: dict) -> str:
    """把一条缺失信息归到公司侧目录。

    顺序有讲究："拟派人员名单"（定人）与"人员证件"（给证）是两件事，
    必须放在"人员"这个宽泛词之前判，否则两类会合并成一行，
    公司就看不出"先定人、再交证"这个先后关系。
    """
    # 先只用条目的文案分类：要求级文本（如落位"七（六）项目团队人员组成表"）
    # 覆盖面太宽，先看它会把"人员证件""项目团队"混成一类。
    cat = _classify_text(item)
    if cat != "其他":
        return cat
    return _classify_text(f"{req.get('source_text', '')} {req.get('response_location', '')}")


def _classify_text(text: str) -> str:
    if any(k in text for k in ("拟派人员名单", "项目团队", "团队人员")):
        return "项目团队"
    if any(k in text for k in ("设备", "仪器", "机具", "试验能力", "台账")):
        return "设备机具"
    if any(k in text for k in ("报价", "品牌", "型号", "税率", "分项报价")):
        return "报价资料"
    if any(k in text for k in ("保证金",)):
        return "投标保证金"
    if any(k in text for k in ("人员", "职称", "特种作业", "证件", "持证", "岗位")):
        return "人员证件"
    return "其他"


def _title(ws, text: str, ncols: int) -> None:
    ws.append([text])
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncols)
    c = ws.cell(row=1, column=1)
    c.font = Font(bold=True, size=13)
    c.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 26


def _header(ws, ncols: int, header_row: int = 2) -> None:
    for c in range(1, ncols + 1):
        cell = ws.cell(row=header_row, column=c)
        cell.fill = _HEAD_FILL
        cell.font = _HEAD_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = _BORDER


def _body(ws, ncols: int, *, start_row: int) -> None:
    for i, (_, width) in enumerate(HEADERS, start=1):
        ws.column_dimensions[get_column_letter(i)].width = width
    for r in range(start_row, ws.max_row + 1):
        for c in range(1, ncols + 1):
            cell = ws.cell(row=r, column=c)
            cell.alignment = Alignment(vertical="top", wrap_text=(c in (3, 4, 5)))
            cell.border = _BORDER
            cell.font = Font(size=9)


def _clip(text: str, limit: int) -> str:
    from ..io.textnorm import excel_safe

    text = excel_safe(text).strip()
    return text if len(text) <= limit else text[:limit] + "…"
