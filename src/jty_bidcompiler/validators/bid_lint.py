"""bid-lint：投标文件自动 QA（MVP-5）。

第一版覆盖 11 项检查（清单见 rules/lint_rules.json）：

1.  旧项目/被否决业绩污染扫描        6. 关键字段缺失
2.  内部工作词扫描                   7. 证件有效期覆盖（过期证件是否仍在文件中）
3.  空白占位与未填项扫描             8. 业绩时间与电压复核
4.  旧标准编号残留扫描               9-11. 人员 / 报价 / 保证金待确认
5.  资格项判定汇总

输入只需要两样：**投标文件正文**（可以是 V4 docx 抽出的文本）与 **编译器产物**
（project.json / qualification_matrix.json / materials manifest）。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from ..matchers.performance import _parse_date

PASS, FAIL, WAIT = "PASS", "FAIL", "WAIT_COMPANY"


class BidLinter:
    def __init__(
        self,
        *,
        rules_path: str | Path,
        project: dict,
        matrix: dict,
        materials: list[dict],
        bid_text: str = "",
        bid_text_label: str = "",
        legacy_keywords: list | None = None,
    ):
        self.rules = json.loads(Path(rules_path).read_text(encoding="utf-8"))
        self.project = project
        self.matrix = matrix
        self.materials = materials
        self.bid_text = bid_text or ""
        self.lines = self.bid_text.splitlines()
        self.bid_text_label = bid_text_label
        # "旧项目/被否决业绩"关键词表来自 config/<profile>.json 的
        # contamination.forbidden_tokens——规则文件里不放真实项目词。
        self.legacy_keywords = list(legacy_keywords or [])
        self.deadline = self._deadline()
        self.checks: list[dict] = []

    # ------------------------------------------------------------------ #
    def run(self) -> dict:
        for spec in self.rules["checks"]:
            handler = getattr(self, f"_check_{spec['check_id'].lower().replace('-', '_')}", None)
            if handler is None:
                self._add(spec, WAIT, "未实现该检查项（PoC 第一期未覆盖）", owner="COMPILER")
                continue
            handler(spec)
        summary = {PASS: 0, FAIL: 0, WAIT: 0}
        for c in self.checks:
            summary[c["status"]] = summary.get(c["status"], 0) + 1
        return {
            "schema_version": "1.0",
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "targets": {
                "bid_text": self.bid_text_label or None,
                "bid_text_chars": len(self.bid_text),
                "project": (self.project.get("source_document") or {}).get("path"),
                "matrix_entries": len(self.matrix.get("entries", [])),
                "materials": len(self.materials),
                "deadline": self.deadline,
            },
            "checks": self.checks,
            "summary": summary,
        }

    # ------------------------------------------------------------------ #
    def _deadline(self) -> Optional[str]:
        f = (self.project.get("fields") or {}).get("deadline") or {}
        v = f.get("value_normalized") or f.get("value")
        return str(v)[:10] if v else None

    def _add(self, spec: dict, status: str, detail: str, *, hits: list[dict] | None = None,
             action: str | None = None, owner: str | None = None,
             title: str | None = None) -> None:
        self.checks.append({
            "check_id": spec["check_id"],
            "title": title or spec["title"],
            "status": status,
            "severity": spec["severity"],
            "rule": spec.get("check_id"),
            "detail": detail,
            "hits": hits or [],
            "action": action or spec.get("action"),
            "owner": owner,
        })

    # ------------------------------------------------------------------ #
    # 关键词类
    def _keyword_scan(self, keywords: Iterable) -> list[dict]:
        """关键词扫描。

        词表项可以是字符串，也可以是 ``{"keyword": .., "allow_context": [..]}``。
        ``allow_context`` 用来压掉**合法用法**造成的误报——例如"待补"出现在
        "待补齐、核实后再行办理"这种合同语句里，或"占位"指施工方案里的"现有元件占位"。
        误报比漏报更伤：公司一旦被假警报训练到忽略这张表，QA 就失效了。
        """
        hits: list[dict] = []
        for item in keywords:
            if isinstance(item, str):
                kw, allow = item, []
            else:
                kw, allow = item.get("keyword", ""), item.get("allow_context") or []
            if not kw:
                continue
            for i, line in enumerate(self.lines, start=1):
                if kw not in line:
                    continue
                allowed = next((a for a in allow if a in line), None)
                hits.append({
                    "quote": line.strip()[:180],
                    "location": f"{self.bid_text_label or '投标文件'} 第{i}段",
                    "line": i,
                    "rule": kw,
                    "note": (f"命中关键词「{kw}」，但属已知合法用法（{allowed}），列为提示"
                             if allowed else f"命中关键词「{kw}」"),
                    "suppressed": bool(allowed),
                })
        return hits

    def _keywords_of(self, spec: dict) -> list:
        """关键词来源：规则内联，或从项目配置注入（去真实数据化）。"""
        params = spec.get("params") or {}
        if "keywords" in params:
            return params["keywords"]
        if "keywords_from_config" in params:
            return self.legacy_keywords
        return []

    def _check_lint_legacy_project(self, spec: dict) -> None:
        if not self._keywords_of(spec):
            self._add(spec, PASS,
                      "未配置本项目「旧项目关键词」词表（config contamination.forbidden_tokens），"
                      "该检查跳过",
                      owner="COMPILER", title="旧项目污染（词表未配置）")
            return
        hits = self._keyword_scan(self._keywords_of(spec))
        if hits:
            uniq = sorted({h["rule"] for h in hits})
            self._add(spec, FAIL,
                      f"投标文件命中 {len(hits)} 处旧项目/被否决业绩关键词：{'、'.join(uniq)}",
                      hits=hits[:40], owner="COMPILER",
                      title=f"旧项目污染（{'、'.join(uniq)}）")
        else:
            self._add(spec, PASS, "未命中任何旧项目/被否决业绩关键词", owner="COMPILER",
                      title="旧项目污染")

    def _check_lint_internal_words(self, spec: dict) -> None:
        hits = self._keyword_scan(self._keywords_of(spec))
        real = [h for h in hits if not h.get("suppressed")]
        soft = [h for h in hits if h.get("suppressed")]
        if real:
            uniq = sorted({h["rule"] for h in real})
            self._add(spec, FAIL,
                      f"投标文件残留 {len(real)} 处内部用语：{'、'.join(uniq)}",
                      hits=real[:40], owner="COMPILER",
                      title=f"内部用语残留（{'、'.join(uniq)}）")
        elif soft:
            uniq = sorted({h["rule"] for h in soft})
            self._add(spec, PASS,
                      f"关键词 {'、'.join(uniq)} 仅以合法用法出现（{len(soft)} 处），无内部用语残留",
                      hits=soft[:10], owner="COMPILER",
                      title=f"内部用语（{'、'.join(uniq)} 为合法用法）")
        else:
            self._add(spec, PASS, "未命中内部工作词/内部编号", owner="COMPILER",
                      title="内部用语")

    def _check_lint_placeholder_empty(self, spec: dict) -> None:
        hits = []
        for pat in spec["params"]["patterns"]:
            for i, line in enumerate(self.lines, start=1):
                for m in re.finditer(pat, line):
                    hits.append({
                        "quote": line.strip()[:180],
                        "location": f"{self.bid_text_label or '投标文件'} 第{i}段",
                        "line": i,
                        "rule": pat,
                        "note": "疑似编辑痕迹/占位符",
                    })
        if hits:
            self._add(spec, FAIL, f"发现 {len(hits)} 处空白占位/编辑痕迹", hits=hits[:40],
                      owner="COMPILER")
        else:
            self._add(spec, PASS, "未发现空白占位符", owner="COMPILER",
                      title="空白占位")

    def _check_lint_obsolete_standard(self, spec: dict) -> None:
        hits = self._keyword_scan(self._keywords_of(spec))
        if hits:
            uniq = sorted({h["rule"] for h in hits})
            self._add(spec, FAIL,
                      f"发现 {len(hits)} 处旧标准编号：{'、'.join(uniq)}（需技术负责人确认是否替换）",
                      hits=hits[:30], owner="COMPANY",
                      title=f"旧标准编号（{'、'.join(uniq)}）")
        else:
            self._add(spec, PASS, "未发现旧标准编号残留", owner="COMPILER",
                      title="旧标准编号")

    # ------------------------------------------------------------------ #
    # 判定汇总类
    def _check_lint_qual_verdicts(self, spec: dict) -> None:
        entries = [e for e in self.matrix.get("entries", []) if e["severity"] == "HARD"]
        fails = [e for e in entries if e["status"] == "FAIL"]
        waits = [e for e in entries if e["status"] == "WAIT_COMPANY"]
        evid = [e for e in entries if e["status"] == "EVIDENCE_INSUFFICIENT"]
        hits = [{
            "quote": f"{e['requirement_id']}：{e['reason']}",
            "location": "qualification_matrix.json",
            "rule": e["requirement_id"],
            "note": e["status"],
        } for e in fails]
        if fails:
            self._add(spec, FAIL,
                      f"{len(fails)} 项硬性要求明确不满足：" +
                      "、".join(e["requirement_id"] for e in fails),
                      hits=hits, owner="COMPANY",
                      title=f"资格项（{len(fails)} 项不满足）")
            return
        if evid:
            self._add(spec, WAIT,
                      f"{len(evid)} 项硬性要求证据不足：" +
                      "、".join(e["requirement_id"] for e in evid),
                      owner="COMPANY", title=f"资格项（{len(evid)} 项证据不足）")
            return
        todo = [e for e in entries if e["status"] == "NOT_EVALUATED"]
        if waits:
            self._add(spec, WAIT,
                      f"{len(waits)} 项硬性要求待公司提供：" +
                      "、".join(e["requirement_id"] for e in waits),
                      owner="COMPANY",
                      title=f"资格项（{len(waits)} 项待提供）")
            return
        if todo:
            self._add(spec, PASS,
                      f"{len(entries)} 项硬性要求无失败项；另有 {len(todo)} 项预装配待办"
                      f"（待 Word 内容生成后核验）：" +
                      "、".join(e["requirement_id"] for e in todo[:8]) +
                      ("…" if len(todo) > 8 else ""),
                      owner="COMPILER",
                      title=f"资格项（{len(todo)} 项预装配待办）")
            return
        self._add(spec, PASS, f"{len(entries)} 项硬性要求全部满足", owner="COMPILER",
                  title="资格项")

    def _check_lint_missing_fields(self, spec: dict) -> None:
        fields = self.project.get("fields") or {}
        missing = [n for n in spec["params"]["required"] if not (fields.get(n) or {}).get("value")]
        waits = []
        for name, f in fields.items():
            if not f.get("value"):
                waits.append(name)
        if missing:
            self._add(spec, FAIL, f"招标文件解析缺失关键字段：{'、'.join(missing)}",
                      owner="COMPILER")
        else:
            self._add(spec, PASS,
                      f"关键字段齐全（另 {len(waits)} 项属公司填写项："
                      f"{'、'.join(n for n in waits if n not in missing) or '无'}）",
                      owner="COMPILER", title="关键字段")

    def _check_lint_expired_cert(self, spec: dict) -> None:
        deadline = _parse_date(spec["params"].get("must_cover_date") == "TENDER_DEADLINE"
                               and self.deadline or spec["params"].get("must_cover_date"))
        hits, bad = [], []
        for m in self.materials:
            exp = _parse_date(m.get("expiry_date"))
            if exp is None or deadline is None or exp >= deadline:
                continue
            num = m.get("certificate_number")
            in_file = bool(num and self.bid_text and _flat(num) in _flat(self.bid_text))
            if in_file:
                bad.append(m)
                idx = self.bid_text.find(num)
                hits.append({
                    "quote": self.bid_text[max(0, idx - 60): idx + 80].replace("\n", " "),
                    "location": f"{self.bid_text_label or '投标文件'}",
                    "rule": m["material_id"],
                    "note": f"{m.get('person') or ''}{m.get('title')} 于 {m['expiry_date']} 到期，"
                            f"不覆盖投标截止日 {self.deadline}，但仍出现在投标文件中",
                })
        if bad:
            self._add(spec, FAIL,
                      f"{len(bad)} 份已过期证件仍出现在投标文件中：" +
                      "、".join(m["material_id"] for m in bad),
                      hits=hits, owner="COMPILER")
        else:
            self._add(spec, PASS,
                      f"未发现过期证件被装入（已核对 {len(self.materials)} 份素材的有效期）",
                      owner="COMPILER", title="证件有效期")

    def _check_lint_perf_window(self, spec: dict) -> None:
        entry = next((e for e in self.matrix.get("entries", [])
                      if e["requirement_id"] in spec["params"]["requirement_ids"]), None)
        if entry is None:
            self._add(spec, WAIT, "资格业绩要求未在矩阵中找到", owner="COMPILER")
            return
        selected = entry.get("selected")
        mat = next((m for m in self.materials if m["material_id"] == selected), None)
        if mat is None:
            self._add(spec, FAIL, f"资格业绩判定通过但未记录选定业绩（{entry['reason']}）",
                      owner="COMPILER")
            return
        hits = [{
            "quote": f"{mat['material_id']} 签订日期 {mat.get('contract_date')}，"
                     f"电压等级 {mat.get('voltage_level')}",
            "location": "materials manifest",
            "rule": mat["material_id"],
            "note": "业绩时间与电压复核",
        }]
        self._add(spec, PASS,
                  f"资格业绩时间/电压复核通过：{mat['material_id']}"
                  f"（{mat.get('contract_date')}，{mat.get('voltage_level')}）",
                  hits=hits, owner="COMPILER", title="业绩时间与电压")

    # ------------------------------------------------------------------ #
    # 待确认类
    def _pending_check(self, spec: dict, resolver) -> None:
        ids = spec["params"]["pending_ids"]
        hits, open_ids = [], []
        for pid in ids:
            item = self.rules.get("pending_items_catalog", {}).get(pid, {})
            title = item.get("title", pid)
            done = resolver(pid)
            if done:
                continue
            open_ids.append(pid)
            hits.append({"quote": f"{pid}：{title}", "location": "rules/lint_rules.json",
                         "rule": pid, "note": "未决"})
        if open_ids:
            self._add(spec, WAIT,
                      f"{len(open_ids)} 项待办理：{'、'.join(open_ids)}",
                      hits=hits, owner="COMPANY",
                      title=spec['title'])
        else:
            self._add(spec, PASS, f"{spec['title']}：已完成", owner="COMPANY")

    def _check_lint_personnel(self, spec: dict) -> None:
        def resolved(pid: str) -> bool:
            if pid == "Q-PER-03":
                return not any(m.get("verification_status") == "REJECTED"
                               and "身份证" in (m.get("title") or "")
                               for m in self.materials)
            return False
        self._pending_check(spec, resolved)

    def _check_lint_price(self, spec: dict) -> None:
        # 报价是否已填：正文出现"投标总报价"且带具体金额
        filled = bool(re.search(r"投标总报价[^\n]{0,40}?[\d,，.]+\s*元", self.bid_text))
        self._pending_check(spec, lambda pid: filled)

    def _check_lint_bid_security(self, spec: dict) -> None:
        """保证金是否已缴纳。

        注意：招标文件格式里本来就写着"缴纳保证金并提供缴纳凭证的扫描件"，
        所以**不能用文本关键词判断**——那会得到假 PASS。这里只认素材库里
        真实存在的缴纳凭证材料。
        """
        has_voucher = any(
            m.get("verification_status") == "VERIFIED"
            and "保证金" in (m.get("title") or "")
            for m in self.materials
        )
        self._pending_check(spec, lambda pid: has_voucher)


# --------------------------------------------------------------------------- #
def _flat(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def render_markdown(result: dict, *, project_title: str = "") -> str:
    lines = [
        f"# bid-lint 报告 —— {project_title}".rstrip(" ——"),
        "",
        f"- 生成时间：{result['generated_at']}",
        f"- 检查对象：{result['targets'].get('bid_text') or '（未提供投标文件正文，仅做产物侧检查）'}",
        f"- 结论汇总：PASS {result['summary'].get('PASS', 0)} ｜ "
        f"FAIL {result['summary'].get('FAIL', 0)} ｜ "
        f"WAIT_COMPANY {result['summary'].get('WAIT_COMPANY', 0)}",
        "",
        "> 判定口径：PASS=已确认无问题；FAIL=投标文件里确有毛病，必须改；"
        "WAIT_COMPANY=缺口在真实材料或公司决策，编译器改不了。",
        "",
    ]
    order = {FAIL: 0, WAIT: 1, PASS: 2}
    for c in sorted(result["checks"], key=lambda x: (order.get(x["status"], 9), x["check_id"])):
        lines.append(f"## {c['status']} {c['title']}")
        lines.append("")
        if c.get("detail"):
            lines.append(f"- 说明：{c['detail']}")
        if c.get("owner"):
            lines.append(f"- 责任方：{'公司' if c['owner'] == 'COMPANY' else ('用户' if c['owner'] == 'USER' else '编译器')}")
        if c.get("action"):
            lines.append(f"- 建议动作：{c['action']}")
        if c.get("hits"):
            lines.append(f"- 命中明细（{len(c['hits'])} 条，最多显示 8 条）：")
            for h in c["hits"][:8]:
                loc = h.get("location") or ""
                note = h.get("note") or ""
                lines.append(f"  - `{loc}` {note}")
                if h.get("quote"):
                    lines.append(f"    > {h['quote'][:200]}")
        lines.append("")
    return "\n".join(lines)
