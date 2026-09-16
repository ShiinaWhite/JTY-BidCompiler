"""对抗性测试用的**合成**语料与工具（不含任何真实项目/公司/人员数据）。

这些 fixture 专门用来复现"看起来能跑、其实在替公司下结论"的坏情况：
* 业绩对象只出现在检查类语境（E4 = PARTIAL）却想拿 PASS；
* 本项目对象就是线路/铁塔，却被"线路不算设备"的全局先验压掉；
* 同一项目有两套业绩口径，评分项却吃到别人的结果；
* 陌生信用条款被任意一份信用材料顶上；
* 模板锚点没命中的条目照样变成"公司待办"。

所有文本都是临时构造的，测试在临时目录里跑真实 pipeline。
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.support import PERF_RULES, QUAL_RULES

BIDDER = "合成测试投标人有限公司"
DEADLINE = "2026-06-30 09:00"


def _tender(*pages: str) -> str:
    """把若干页正文拼成带页标记的招标文件文本（页号连续，从 1 开始）。

    页号由代码生成，避免手写标记时漏页/串号——真实招标文件的页是连续的。
    """
    return "\n".join(f"===== PDF第{i}页 =====\n{body.strip()}\n"
                     for i, body in enumerate(pages, start=1))


# --------------------------------------------------------------------------- #
# 招标文件（合成）
# --------------------------------------------------------------------------- #

_COVER = """
合成市某监测设施改造服务项目
（招标编号：HCT-2026-0001）
招标文件
招 标 人：合成市某局
2026 年05 月
"""

_QUAL_BASE = """
  3.1 投标人须是中华人民共和国境内合法注册的法人或其他组织，具有有效的营业执照或法人证书；
  3.2 投标人须具有电子与智能化工程专业承包二级及以上资质，具有《安全生产许可证》；
"""

_FIRST_CHAPTER_HEAD = """
第一章 招标公告
项目编号：HCT-2026-0001
开标时间：2026 年06 月30 日09:00
3.投标人资格要求
"""

#: 两套业绩口径：3.3 线路/铁塔类，3.4 开关柜类
_PERF_CLAUSES_TWO = """
  3.3 投标人自2023 年1 月1 日至投标截止时间（以合同签订日期为准），须具有1 项
  110kV 及以上电压等级输电线路铁塔维护维修业绩，须提供合同扫描件；
  3.4 投标人自2023 年1 月1 日至投标截止时间（以合同签订日期为准），须具有1 项
  10kV 及以上电压等级开关柜改造或维修业绩，须提供合同扫描件；
  3.5 本项目是否允许联合体投标：否。
"""

#: 只有一套业绩口径（3.3 线路/铁塔类）
_PERF_CLAUSES_ONE = """
  3.3 投标人自2023 年1 月1 日至投标截止时间（以合同签订日期为准），须具有1 项
  110kV 及以上电压等级输电线路铁塔维护维修业绩，须提供合同扫描件；
  3.4 本项目是否允许联合体投标：否。
"""

_PRE_TABLE = """
第二章 投标人须知前附表
  1.10.1 分包：不允许
  3.3.1 投标有效期：90 日
  3.4.1 投标保证金的金额：人民币2.5 万元
"""

_SCORING_DIGEST = """
第三章 评标办法（综合评估法）
  2.2.1 分值构成（总分100 分）
  投标报价： 40 分
  商务部分： 10 分
  技术部分： 50 分
  2.2.2 评标基准价计算方法
  评标基准价为有效投标人经评审后的有效投标报价的最低价。
  2.2.4（1）投标报价 40 分
  评标价等于评标基准价，得满分；每高于评标基准价1%，在基础分的基础上扣1 分。
"""

_TECH_ITEMS = """
  服务方案（25 分）方案详尽周到、科学合理。优档（21≤得分≤25）。
  运维服务体系（25 分）服务体系健全，响应及时。优档（21≤得分≤25）。
"""

_TECH_ITEMS_FIVE = """
  监测实施方案（20 分）方案详尽周到、科学合理。优档（17≤得分≤20）。
  运维服务体系（10 分）服务体系健全，响应及时。优档（8≤得分≤10）。
  项目组织机构（5 分）组织机构图完整，职责清晰。优档（4≤得分≤5）。
  安全与环境保护措施（10 分）措施完善可操作。优档（8≤得分≤10）。
  应急预案（5 分）应急流程清晰。优档（4≤得分≤5）。
"""

_BUSINESS_TAIL = """
  企业综合实力及财务状况（2 分）近三年财务状况横向对比，需提供经审计的财务报告扫描件。
  主要商务条款响应程度（2 分）主要商务条款全部响应招标文件要求得2 分。
  投标文件编制质量（1 分）投标文件清晰完整、按招标文件要求格式、顺序编制。
"""


def _scoring_page(business_item: str, tech_items: str = _TECH_ITEMS_FIVE) -> str:
    return (f"2.2.4（2）商务评分标准\n{business_item.strip()}\n\n"
            f"{_BUSINESS_TAIL.strip()}\n"
            f"2.2.4（3）技术评分标准{tech_items}")


_SCORING_REF = """
  业绩（5 分）满足投标人资格要求业绩得1 分；每多提供1 个有效业绩加1 分，最多加4 分，
  业绩要求和证明材料同投标人资格要求，已经作为资格要求的业绩不重复计分。
"""
_SCORING_OWN = """
  业绩（5 分）投标人具有35kV 以上变电站运维业绩的，每提供1 项得1 分，最多加5 分。
"""
_SCORING_VAGUE = """
  业绩（5 分）投标人提供的类似项目业绩，由评标委员会根据项目情况酌情打分。
"""

_CHAPTER_5 = """
第五章 服务标准及要求
一、项目概况
  对既有监测设施进行改造与维护。
二、服务内容
  1.1 监测终端改造：完成既有终端的改造与调试。
*服务内容有缺项将取消投标资格。
"""

#: 两套业绩口径 + 评分项明示引用资格口径（引用目标不唯一 → 不得自动继承）
TENDER_TWO_PERFORMANCE_CLAUSES = _tender(
    _COVER,
    _FIRST_CHAPTER_HEAD + _QUAL_BASE + _PERF_CLAUSES_TWO,
    _PRE_TABLE,
    _SCORING_DIGEST,
    _scoring_page(_SCORING_REF),
    _CHAPTER_5,
)

#: 单套业绩口径 + 评分项明示引用（引用目标唯一 → 允许 condition_ref）
TENDER_SINGLE_PERFORMANCE_WITH_REF = _tender(
    _COVER,
    _FIRST_CHAPTER_HEAD + _QUAL_BASE + _PERF_CLAUSES_ONE,
    _PRE_TABLE,
    _SCORING_DIGEST,
    _scoring_page(_SCORING_REF),
    _CHAPTER_5,
)

#: 评分项自带独立口径（原文没有"同资格要求"字样）
TENDER_SCORING_INDEPENDENT = _tender(
    _FIRST_CHAPTER_HEAD + _QUAL_BASE + _PERF_CLAUSES_ONE,
    _SCORING_DIGEST,
    _scoring_page(_SCORING_OWN, _TECH_ITEMS),
)

#: 评分项只有名字、没有可判定规则（既无引用、也解析不出参数）
TENDER_SCORING_UNPARSED = _tender(
    _FIRST_CHAPTER_HEAD + _QUAL_BASE + _PERF_CLAUSES_ONE,
    _SCORING_DIGEST,
    _scoring_page(_SCORING_VAGUE, _TECH_ITEMS),
)

#: 没有任何商务模板条款的招标文件（无保证金、无 CA 签章、无分包条款）
TENDER_NO_COMMERCIAL_ANCHORS = _tender(
    _FIRST_CHAPTER_HEAD + _QUAL_BASE + """
备注：
  （1）本次招标采用资格后审。
""",
    """
  1.4.3 投标人不得存在下列情形之一：
  （1）为招标人不具有独立法人资格的附属机构（单位）；
  （2）与招标人存在利害关系且可能影响招标公正性；
  （3）被市场监督管理部门在国家企业信用信息公示系统列入严重违法失信企业名单；
  （4）被最高人民法院在"信用中国"网站列入失信被执行人名单；
  （5）被省级交通运输主管部门列入严重失信主体名单且未完成整改的；
  （6）法律法规或投标人须知前附表规定的其他情形。
""",
)

# --------------------------------------------------------------------------- #
# 素材库（合成）
# --------------------------------------------------------------------------- #


def _mat(mid: str, title: str, spans: list[tuple[str, str]], *,
         kv: int, date: str = "2024-06-01", category: str = "业绩合同",
         status: str = "VERIFIED", company: str = BIDDER) -> dict:
    return {
        "material_id": mid,
        "category": category,
        "title": title,
        "company": company,
        "person": None,
        "certificate_number": None,
        "issue_date": None,
        "expiry_date": None,
        "contract_party": "（合成）某甲方单位",
        "contract_date": date,
        "contract_amount": None,
        "voltage_level": f"{kv}kV",
        "voltage_level_kv": kv,
        "project_type": title,
        "keywords": [],
        "verification_status": status,
        "source_file": "tests/adversarial/fixtures.py",
        "source_pages": [],
        "evidence_spans": [{"label": lbl, "text": txt, "source_ref": lbl} for lbl, txt in spans],
        "notes": "合成测试数据",
    }


_SEAL = ("签章页", f"买方（甲方）：（合成）某甲方单位；卖方（乙方）：{BIDDER}；"
                   "签订日期：2024年6月1日。")

#: 线路类业绩：本项目对象就是线路/铁塔时应当被认可
M_LINE = _mat("M-ADV-LINE", "（合成）110kV 输电线路维护维修及铁塔校正合同",
              [_SEAL, ("服务范围", "110kV 输电线路维护维修、铁塔校正及附件更换，"
                                   "含杆塔基础检查与消缺。")], kv=110)

#: 柜类业绩：开关柜改造，语境为改造
M_CABINET = _mat("M-ADV-CABINET", "（合成）10kV 开关柜改造及维修合同",
                 [_SEAL, ("服务范围", "对 10kV 开关柜 12 台进行改造，并承担改造后的故障维修。")],
                 kv=10)

#: 线路缆化改造：不得扩张解释成"开关柜改造"
M_CABLE = _mat("M-ADV-CABLE", "（合成）35kV 输电线路缆化改造工程",
               [_SEAL, ("工程内容", "本项目 35kV 输电线路缆化改造，新建电缆隧道 391 米。")],
               kv=35)

#: 市政管网监测终端改造：不得受电力行业的对象先验影响
M_WATER = _mat("M-ADV-WATER", "（合成）供水管网监测终端改造及维修合同",
               [_SEAL, ("服务范围", "对供水管网监测终端及 10kV 供电回路进行改造，"
                                    "并承担改造后两年的故障维修。")], kv=10)

#: 开关柜只出现在"检查"语境：E4 应为 PARTIAL，不得凭其他条款凑成 PASS
M_WEAK_OBJECT = _mat("M-ADV-WEAK", "（合成）35kV 开关柜带电显示装置检查服务合同",
                     [_SEAL,
                      ("服务范围", "对 35kV 开关柜带电显示装置及五防性能进行检查。"),
                      ("责任条款", "乙方负责站内设备的维护、维修及故障处理。")],
                     kv=35)

#: 变电站运维业绩：满足"35kV 以上变电站运维"这类**独立评分口径**，
#: 但不满足 110kV 线路铁塔的**资格口径**（电压不够）——用来验证两套口径不互串
M_SUBSTATION = _mat("M-ADV-SUBSTATION", "（合成）35kV 变电站运行维护及维修合同",
                    [_SEAL, ("服务范围", "承担 35kV 变电站运行维护、日常维修及消缺工作。")],
                    kv=35)


def manifest(materials: list[dict], path: Path) -> Path:
    path.write_text(json.dumps({"materials": materials}, ensure_ascii=False), encoding="utf-8")
    return path


def _credit(mid: str, title: str, text: str, issue_date: str) -> dict:
    return {
        "material_id": mid, "category": "信用查询", "title": title,
        "company": BIDDER, "person": None, "certificate_number": None,
        "issue_date": issue_date, "expiry_date": None, "contract_party": None,
        "contract_date": None, "contract_amount": None, "voltage_level": None,
        "voltage_level_kv": None, "project_type": None, "keywords": [],
        "verification_status": "VERIFIED", "source_file": "tests/adversarial/fixtures.py",
        "source_pages": [], "evidence_spans": [{"label": "查询结果", "text": text,
                                                "source_ref": "合成查询结果"}],
        "notes": "合成测试数据",
    }


#: 信用查询材料：一份公示系统、一份执行信息公开网（各自只对应自己那条条款）
M_CREDIT_GSXT = _credit("M-ADV-GSXT", "（合成）国家企业信用信息公示系统查询结果",
                        "行政处罚 0 条；严重违法失信 0 条。", "2026-05-01")
M_CREDIT_COURT = _credit("M-ADV-COURT", "（合成）中国执行信息公开网失信被执行人查询结果",
                         "在全国范围内没有找到相关的结果。", "2026-05-01")


# --------------------------------------------------------------------------- #
# 临时工作区
# --------------------------------------------------------------------------- #
class AdversarialCase(unittest.TestCase):
    """公共底座：临时目录、合成语料、真实 pipeline 调用。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="adv-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    # -- 文件 -------------------------------------------------------------- #
    def write_tender(self, text: str, name: str = "tender.txt") -> Path:
        p = self.tmp / name
        p.write_text(text.replace("\r\n", "\n"), encoding="utf-8", newline="\n")
        return p

    def write_materials(self, materials: list[dict], name: str = "materials.json") -> Path:
        return manifest(materials, self.tmp / name)

    # -- pipeline ---------------------------------------------------------- #
    def requirements_of(self, tender: Path) -> dict:
        from jty_bidcompiler.parsers.tender_document import load_tender, sha256_file
        from jty_bidcompiler.parsers.tender_requirements import extract_requirements

        text, meta = load_tender(tender)
        return extract_requirements(text, document_path=str(tender),
                                    document_sha256=sha256_file(tender))

    def matcher(self, materials: list[dict], *, deadline: str = DEADLINE):
        from jty_bidcompiler.adapters.repository import LocalMaterialRepository
        from jty_bidcompiler.matchers.engine import RequirementMatcher

        repo = LocalMaterialRepository(self.write_materials(materials))
        return RequirementMatcher(
            repo, rules_path=QUAL_RULES, perf_rules_path=PERF_RULES,
            project={"fields": {"deadline": {"value_normalized": deadline}}},
            bidder=BIDDER,
        ), repo

    def run_pipeline(self, tender: Path, materials: list[dict]) -> dict:
        """要求 → 矩阵 → 选择 → 投标状态（全链路，不只看中间函数）。"""
        from jty_bidcompiler.generators.selection import build_bid_status, build_selection

        rq = self.requirements_of(tender)
        matcher, repo = self.matcher(materials)
        matrix = matcher.match(rq["requirements"])
        entries = {e["requirement_id"]: e for e in matrix["entries"]}
        project = {"fields": {"deadline": {"value_normalized": DEADLINE}}}
        selection = build_selection(matrix, rq["requirements"], repo.list_materials(),
                                    project_title="（合成）对抗性用例", bidder=BIDDER)
        status = build_bid_status(matrix, rq["requirements"], project,
                                  project_title="（合成）对抗性用例",
                                  materials=repo.list_materials())
        return {"requirements": rq, "matrix": matrix, "entries": entries,
                "selection": selection, "status": status,
                "by_req": {e["requirement_id"]: e for e in selection["entries"]}}


__all__ = [
    "AdversarialCase", "BIDDER", "DEADLINE",
    "TENDER_TWO_PERFORMANCE_CLAUSES", "TENDER_SINGLE_PERFORMANCE_WITH_REF",
    "TENDER_SCORING_INDEPENDENT", "TENDER_SCORING_UNPARSED",
    "TENDER_NO_COMMERCIAL_ANCHORS",
    "M_LINE", "M_CABINET", "M_CABLE", "M_WATER", "M_WEAK_OBJECT",
    "M_SUBSTATION", "M_CREDIT_GSXT", "M_CREDIT_COURT",
]
