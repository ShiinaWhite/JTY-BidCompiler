# JTY-BidCompiler 仓库安全与可审计性审计

> 审计时间：2026-09-15 ｜ 审计范围：Git 全历史 + 工作区 + 跟踪文件集
> 审计执行：ZCode（只读扫描）+ 索引整理（不改写历史）

---

## 一、结论先行

| 问题 | 结论 |
|---|---|
| **当前仓库能否 private push** | ✅ **可以**（协作方需能接触真实数据，且明确告知历史遗留） |
| **当前仓库能否 public push** | ❌ **不可以**。Git 历史中已存在真实招标/投标/人员/金额数据 |
| 阻塞项 | 见 §5「公开发布阻塞清单」 |
| 本次是否已改写历史 | ❌ **没有**。按要求只报告，不做 `filter-repo` / `filter-branch` |

**一句话**：跟踪文件集已经清洗成"只有代码与契约"（201 → 64 个文件），
但**历史提交里仍留有真实数据**，因此公开推送被历史阻塞，而不是被当前文件阻塞。

---

## 二、扫描方法与证据

### 2.1 扫描范围
- 跟踪文件：`git ls-files` → **201 个**（审计前）
- Git 历史：`git log --all --name-only --diff-filter=A` → 全部新增文件类型

### 2.2 历史中提交过的文件类型

```
 90 json    44 py    28 xlsx    28 md    4 zip    2 txt    1 toml    1 sh    ...
```

**关键发现**：历史中存在 **28 个 .xlsx、2 个 .txt、4 个 .zip**，
它们全部是真实项目材料或其派生件（详见 §3）。这是公开推送的核心阻塞。

**未发现**：PDF / DOCX / DOC 原始件从未入库（`.gitignore` 早期规则已挡住）。

### 2.3 敏感模式扫描（跟踪文件，逐模式）

| 模式 | 命中 | 涉及文件 | 判定 |
|---|---|---|---|
| 手机号 | 12 处 | 投标文件正文摘录 | **SENSITIVE** |
| 身份证号 | 0 | — | — |
| 统一社会信用代码 | 27 处 | 素材 manifest、投标正文 | **SENSITIVE** |
| 银行账号形态 | 39 处 | 语料清单、素材 manifest | **SENSITIVE** |
| 建造师注册证号 | 8 处 | 素材 manifest、派生材料 | **SENSITIVE** |
| 安全生产考核证号 | 9 处 | 素材 manifest | **SENSITIVE** |
| 资质证书号 / 承装修试证号 / 安许证字 | 31 / 36 / 35 处 | 素材 manifest | **SENSITIVE** |
| 密钥 / token / password | **0** | — | ✅ 干净 |
| 平台账号 | **0** | — | ✅ 干净 |
| 本机绝对路径 | 8 处 | README、架构设计、语料清单 | **PRIVATE_TEST_DATA** |
| `evidence_spans` 合同原文 | 46 处 | 素材 manifest | **SENSITIVE** |

**未发现**：私钥、token、口令、平台账号、银行账户 **原文**（只有"开户银行/银行账号"这类字段名与形似数字的编号）。

---

## 三、三类定性（审计前 201 个跟踪文件）

### 3.1 SOURCE_PUBLIC_SAFE —— 可以公开

| 路径 | 文件数 | 说明 |
|---|---|---|
| `src/jty_bidcompiler/**` | 26 | 编译器源码（已脱敏，见 §4） |
| `schemas/*.schema.json` | 5 | JSON Schema 契约（已脱敏） |
| `rules/*.json` | 3 | 业务规则与词表（已脱敏；项目词表改由 config 注入） |
| `scripts/**` | 5 | 便捷脚本（已脱敏） |
| `tests/**` | 19 | 测试与 Gold oracle（依赖真实素材的用例自动跳过） |
| `pyproject.toml` / `.gitignore` / `.gitattributes` | 3 | 工程配置 |
| `README.md` | 1 | 项目说明（**待办**：去掉本机绝对路径） |
| `docs/JTY-BidCompiler架构设计.md`、`docs/MVP实施计划.md` | 2 | 架构与计划（**待办**：去掉本机绝对路径与真实项目名） |

### 3.2 PRIVATE_TEST_DATA —— 仅本地，不入库

| 路径 | 文件数 | 为什么 |
|---|---|---|
| `fixtures/<profile-A>/source/tender/*.txt` | 1 | 招标文件文本（招标人联系方式、平台条款） |
| `fixtures/<profile-A>/source/<profile-A>/公司待提供材料清单*.xlsx` | 3 | 公司内部待办清单 |
| `reports/**` | 16 | 评分报告与验收证据（含真实项目名、金额） |
| `config/<profile-A>.json`、`config/<profile-B>.json` | 2 | 真实项目配置（编号、投标人、金额词表） |
| `docs/<profile-A> Gold Test基准.md` 等 5 份 | 5 | 含真实项目名、人名、金额的基准与报告 |

### 3.3 SENSITIVE —— 绝不入库（且已进入历史）

| 路径 | 文件数 | 含量 |
|---|---|---|
| `fixtures/<profile-A>/materials/materials.<profile-A>.json` | 1 | 真实姓名 23 处、证书号 9 处、合同金额 10 处、**合同原文 `evidence_spans` 46 处** |
| `fixtures/<profile-A>/source/<profile-A>/bid_text.derived.txt` | 1 | 真实投标正文：手机号 7 处、统一社会信用代码、开户银行字段、金额 13 处、人员姓名 8 处 |
| `fixtures/<profile-A>/source/{sample_b-equivalent,stage1}/*.xlsx` | 9 | 人员材料核验表（姓名+证件）、业绩五要素核验表、设备清单、素材索引 |
| `projects/**` | 80 | 全部产物：`materials.json` 含证据原文；矩阵/Excel 含候选人、金额、条款 |
| `review-package/**` | 18 | 含 4 个 zip 交付包（打包了上述全部内容） |
| `fixtures/*/fixture_manifest.json` | 2 | 源文件**本机绝对路径** + SHA256 |

---

## 四、本次已执行的整理

### 4.1 `.gitignore` 重写（分层拦截）

```
fixtures/            ← 真实项目输入（保留 fixtures/README.md）
projects/            ← 真实项目输出
reports/ review-package/
config/sample_a.json  config/sample_b.json   ← 保留 config/example.json
docs/<含真实数据的基准与报告>
*.pdf *.docx *.xlsx *.zip *.rar *.7z     ← 二进制兜底
*.pem *.key *.env secrets.json credentials.json  ← 凭据兜底
```

### 4.2 从索引移除（磁盘文件零删除）

```
git rm -r --cached fixtures projects reports review-package tests/gold/<profile-B> \
    docs/*Gold Test基准.md docs/第一轮测试报告.md docs/阶段1.5*.md \
    docs/2026真实项目测试语料清单.md docs/ZCode交接*.md \
    config/sample_a.json config/sample_b.json
```

**跟踪文件数：201 → 64**。磁盘验证：fixtures 19 / projects 80 / reports 16 /
review-package 18 / config 2 / docs 7 个文件**全部仍在磁盘**，端到端跑通未受影响
（`jty run --profile sample_b` 复跑成功，产物与审计前一致）。

### 4.3 代码与真实数据解耦

| 位置 | 改动 |
|---|---|
| `matchers/engine.py` | `bidder` 由默认真实公司名 → **必填参数**（由 profile 注入）；注释里的真实人名/金额改为通用表述 |
| `validators/contamination.py` | 删除硬编码的 `PROJECT_VARIABLES`（真实项目名与金额）→ **纯 config 驱动** |
| `validators/bid_lint.py` | 新增 `_keywords_of()`：关键词可来自 config 注入；未配置时该检查显式跳过并注明 |
| `rules/lint_rules.json` | "旧项目关键词"由内联真实词表 → `keywords_from_config` 引用 |
| `rules/performance_five_elements.json` | `bidder_names` 去真实公司名 → 由 profile 注入 |
| `rules/qualification_rules.json` | `project` 字段去真实项目名与编号 |
| `schemas/materials.schema.json` | 示例金额去真实值 |
| `src/**` 注释 | 真实项目名/人名/编号/金额 → 通用代称（保留工程教训） |
| `tests/support.py` | 新增 `load_profile()` / `bidder_of()` / `requires_config()` |

**解耦后验证**：`grep -rn "（真实项目名/人名模式）" src/ rules/ schemas/ scripts/`
→ **0 命中**（仅剩"开关柜""输电线路铁塔"等**通用设备词**，属词表合理内容）。

### 4.4 测试可运行性

- 无真实素材时：依赖 fixtures/config 的用例**自动跳过**，其余照常运行
- 有真实素材时：**89/89 全绿**（含 3 项新增跨视图一致性断言）

---

## 五、公开发布阻塞清单

| # | 阻塞项 | 严重度 | 说明 |
|---|---|---|---|
| **B1** | **Git 历史含真实数据** | **P0** | 历史提交中有 28 个 xlsx、2 个 txt 派生件、4 个 zip 交付包，含真实人员姓名、证书编号、合同原文、手机号、金额。**这是公开推送的唯一硬阻塞** |
| ~~B2~~ | ~~README 与 docs 含本机绝对路径~~ | **已解决** | 跟踪文件中绝对路径 0 命中（统一改为 `<repo-root>` / `<source-projects-dir>` 占位符） |
| ~~B3~~ | ~~`tests/gold/sample_b/*.expected.json`~~ | **已解决** | 真实项目 oracle 移至 `projects/_local_oracles/<profile>/` 并排除；仓库内只留 `tests/gold/example/`（合成示例）。oracle 查找路径支持私有目录，公开环境下相关用例自动跳过 |
| ~~B4~~ | ~~真实项目名在 `docs/` 通用文档中作为示例出现~~ | **已解决** | 已统一改为"示例项目 A/B"；跟踪文件中真实项目名 0 命中 |
| B5 | 公开前建议补一份脱敏的 `config` 实样例 | P3 | 现有 `config/example.json` 已是可用的模板；若公开，建议再附一个"虚构项目"的完整 profile 作为端到端示例 |

### 解决路径（**需用户决策，本次不执行**）

| 方案 | 做法 | 代价 |
|---|---|---|
| **A. 保持私有** | 不处理历史，private push 即可 | 无 |
| **B. 公开新历史** | `git checkout --orphan public` 后用**当前干净跟踪集**重新首次提交，旧历史保留在私有远端 | 丢失历史追溯；需人工确认新首提交内容 |
| **C. 重写历史** | `git filter-repo --path fixtures --path projects ... --invert-paths` | **改写 commit hash**，所有协作方需重新克隆；**本次未执行** |
| **D. 只对可信方开源** | 走 B 或 A，接入方签署数据保密条款 | 无需清理历史 |

---

## 五之二、审计执行过程中追加完成的工作

| 项 | 内容 |
|---|---|
| 代码全量脱敏 | `src/`、`rules/`、`schemas/`、`scripts/`、`docs/`、`README.md`、`pyproject.toml` 中真实项目名/人名/编号/金额 → **0 命中**（`grep -rn "<真实项目名/人名/编号模式>"` 为空） |
| `bidder` 必填化 | `RequirementMatcher.__init__(bidder=...)` 去掉真实公司名默认值，改由 `config/<profile>.bidder` 注入 |
| 污染词表配置化 | `validators/contamination.py` 删除硬编码 `PROJECT_VARIABLES`；`rules/lint_rules.json` 的"旧项目关键词"改为 `keywords_from_config`；`BidLinter` 新增 `_keywords_of()`，未配置时显式跳过并注明 |
| 真实项目 oracle 私有化 | `tests/gold/sample_b/` → `projects/_local_oracles/sample_b/`（已排除）；`tests/gold/oracle.py` 支持双路径查找；新增 `tests/gold/example/` 合成示例 |
| 测试 profile 化 | `test_pipeline_sample_a.py` → `test_pipeline_gold.py`，路径与期望值全部读 `config/<profile>.json` |
| 测试可移植性 | 补齐 `skipUnless` 保护：缺 fixture / 缺 config / 缺私有 oracle 时自动跳过，而非报错 |
| 绝对路径清理 | `/tmp` 与 `D:\...` 类路径在跟踪文件中 0 命中 |

### 双环境验证结果

| 环境 | 结果 |
|---|---|
| **本地完整环境**（含真实素材/config/产物） | **91/91 tests OK**；端到端 `jty run` 正常，产物与审计前一致 |
| **模拟公开环境**（移出 fixtures/config/projects） | **39 项运行、25 项自动跳过、0 失败** |

---

## 六、日常纪律（防止再次污染）

1. **新增真实素材一律放 `fixtures/<profile>/`**，不要放进 `src/`、`tests/`、`docs/`
2. **测试里的期望值若来自真实条款**，放进 `tests/gold/<profile>/` 并确认该目录所属 profile 的风险等级
3. **代码注释不得写入真实项目名/人名/编号/金额**——写"示例项目 A""某 B 证"，教训一样能传达
4. **提交前跑一遍**：
   ```bash
   git status --porcelain               # 确认没有 fixtures/projects/reports 被 add
   git diff --cached --name-only | grep -E "^(fixtures|projects|reports|review-package)/" && echo "⚠ 疑似真实数据入库"
   ```
5. **配置进 config，不进代码**：项目名、编号、投标人、金额词表、污染词表全部走 `config/<profile>.json`
6. **交付包（zip）不入库**——它们必然打包了真实内容

---

## 七、审计后的仓库状态

```
跟踪文件：64
├─ src/          26   SOURCE_PUBLIC_SAFE（bidder 必填、词表配置化、注释通用化）
├─ tests/        19   SOURCE_PUBLIC_SAFE（缺真实数据时自动跳过；含合成示例 oracle）
├─ schemas/       5   SOURCE_PUBLIC_SAFE
├─ rules/         3   SOURCE_PUBLIC_SAFE（项目词表改为 config 注入）
├─ scripts/       5   SOURCE_PUBLIC_SAFE
├─ docs/          3   SOURCE_PUBLIC_SAFE（本审计文档 + 架构设计 + MVP 计划）
└─ (root)         4   pyproject / .gitignore / .gitattributes / README（待去绝对路径）

磁盘保留（不入库）：fixtures 20 / projects 85 / reports 16 / review-package 18 / config 2 / docs 5
                     （含 projects/_local_oracles/ 与 projects/_local_tools/ —— 真实 oracle 与项目专用脚本）
```

**未执行的操作（按要求）**：
- ❌ 未改写 Git 历史
- ❌ 未删除任何原始公司资料（磁盘文件全部保留）
- ❌ 未推送任何远端
- ❌ 未把仓库设为 public
