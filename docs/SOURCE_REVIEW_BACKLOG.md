# SOURCE_REVIEW_BACKLOG（源码级 review 未决项）

本文件记录**已知但本轮未修**的判定口径缺口。每一条都配了对抗性 fixture
（`tests/adversarial/`），改动实现时那些用例会立刻失败，提醒同步更新本文件。

记录原则：不写"以后再说"，要写清**现在是什么行为、为什么危险、修的话动哪里**。

---

## P1-A：E1 合同主体可以只凭"素材登记主体"成立

**现状**：`matchers/performance.py::_e1_party` 先找证据片段里的投标人名称；
找不到时，若素材的 `company` 字段等于投标人，仍判 `SATISFIED`，
理由写作"素材登记主体为投标人"，引文是 `素材登记主体：<公司名>`。

**为什么危险**：五要素的纪律是"每一条 PASS 都要能引用**合同原文**"。
现在这一条引用的是**素材库登记信息**，不是合同原文——如果素材登记错了
（比如把别家的合同挂到了本公司名下），E1 不会拦住它。

**现状下的实际影响**：有限。因为 E4（工作对象）与 E5（工作性质）仍要求合同原文，
且引文里明确写了"素材登记主体"，人工复核时看得见依据来自哪里。

**修的话动哪里**：`_e1_party` 在"无合同原文当事人"时应判 `UNKNOWN`
（证据不足）而不是 `SATISFIED`；连带需要复核 6 个黄金判例的 `blocking_elements`
（示例项目的 M-PERF-004/092/093 证据片段里本来就没有当事人名称，
改动会让 E1 进入阻断要素集合，属于 Gold 变更）。

**证据**：`tests/adversarial/test_p1_known_gaps.py::TestP1A_PartyEvidenceIsRegistryOnly`

---

## P1-B：E3 电压等级取"所有片段里的最高 kV"

**现状**：`matchers/performance.py::_e3_voltage` 把素材字段与全部证据片段里的
kV 数值收集起来，取**最大值**与门槛比较。

**为什么危险**：合同里出现"110kV 接入系统条件由甲方提供"这类表述时，
即使实际作业电压是 35kV，也会被判为满足"≥110kV"。
电压证据与对象/性质证据之间**没有关系绑定**。

**现状下的实际影响**：有限。要求 ≥110kV 的项目里，E4 仍要求对象词出现在
作业语境中；但"对象对、电压靠接入条件凑"的情形确实可能误判。

**修的话动哪里**：把电压证据与对象证据绑定——只承认"与工作对象在同一语境"
（同片段或同窗口）的 kV；否则降级为 `PARTIAL`/`UNKNOWN`。
这属于五要素判定口径的变更，需要重建黄金判例的期望值，故不在本轮做。

**证据**：`tests/adversarial/test_p1_known_gaps.py::TestP1B_VoltageTakesMaximumAcrossSpans`

---

## 已在本轮（Source Review Fix-1）修复、不再属于缺口

| 编号 | 问题 | 修复位置 |
|---|---|---|
| P0-1 | E4 = PARTIAL 仍可能拿 PASS | `performance.py::evaluate`（PARTIAL 阻断 PASS，单独列为 `partial_elements`） |
| P0-2 | E5 用"线路 vs 设备"全局品类先验判性质 | `performance.py::_e5_nature`（改为条款驱动的性质词 + 紧邻搭配否定） |
| P0-3 | 业绩结果存在 `_last_performance_result` 单槽共享 | `engine.py::_performance_for`（按 requirement_id 缓存，评分项只认 condition_ref） |
| P0-4 | 评分业绩默认继承资格口径、默认每项 1 分最多 4 项 | `tender_requirements.py::_performance_scoring_condition`（原文明示才引用，计分参数抽不到就留空） |
| P0-5 | 陌生信用条款 fail-open + 候选状态自相矛盾 | `engine.py::_h_credit`（映射不到即证据不足；一料一候选，状态与实际判定一致） |
| P0-6 | 模板锚点没命中仍生成 requirement | `tender_requirements.py::_extract_commercial_specs`（未命中只记 extraction_issue） |

附带修复（同一轮发现，非清单内）：

* `_forbidden_class`：1.4.3 各项的类型/是否需信用证据改为**按内容判断**（原先按第 12–15 项写死）；
* `_extract_qualification_items`：跨页续文在页码不连续/偏移越界时不再抛 `IndexError`；
* `NATURE_ATOMS`：补"运维 ↔ 运行维护"这类全称/简称等价；
* `generators/selection.py`：自带独立口径的评分业绩项，其业绩只进"评分可用"，不混进"资格可用"。
