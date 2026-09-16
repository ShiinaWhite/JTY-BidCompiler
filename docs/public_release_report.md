# 公开仓库发布报告（PUBLIC_PUSH_READY）

本文件由 `verify_public_commit.py` 依据**真实核验数据**生成，不是手写声明。

## 结论

```
PUBLIC_PUSH_READY = YES
```

## 1. 新仓库形态

| 项 | 值 |
|---|---|
| 目录 | `JTY-BidCompiler-Public`（与内部生产仓库物理分离） |
| 根提交（仓库起点） | `a3a546a Initial public release of JTY-BidCompiler` |
| 提交数 | 3（**根提交 1 个**；含本次快照提交——本报告随它一起入库） |
| 提交者 | JTY BidCompiler <compiler@jty.local> |
| 分支 | 1 个（`main`） |
| tag / 远端 | 0 个 tag ／ 1 个远端（尚未 push） |
| 提交内文件 | 74 个 |

文件来源：`git archive HEAD`（**只取内部仓库已跟踪文件**）+ 公开专有文件
（`LICENSE`、`SECURITY.md`、公开 `README.md`、合成示例 `config/synthetic.json` 与
`tests/synthetic/**`、扫描报告）。不使用旧 `.git`、不 clone 后删历史、不递归复制工作目录。

## 2. 排除在外的私有数据类别

| 类别 | 处理方式 |
|---|---|
| `fixtures/`（真实招标/投标文件、核验表、扫描件） | 未导出（未跟踪） |
| `projects/`（运行产物、Excel、评分报告） | 未导出（未跟踪） |
| `reports/`、`review-package/`（交付包） | 未导出（未跟踪） |
| 真实项目 `config/*.json`（含投标人全称、真实路径） | 未导出（未跟踪） |
| 私有 Gold oracle（含招标条款原文 token） | 未导出（未跟踪） |
| 真实公司素材库（人员、证件号、合同金额、证据原文） | 未导出（未跟踪） |
| 二进制原件（pdf/docx/xlsx/zip） | 未导出（`.gitignore` 兜底 + 未跟踪） |

原始公司资料**没有被删除、没有被移动**，只是不进入公开仓库。

## 3. 扫描结果

| 层次 | 对象 | 结果 |
|---|---|---|
| 导出目录（文本 + json/md/txt/yaml/toml + xlsx） | 79 个文件 | 敏感命中 **0** |
| Git 对象库（含历史 blob） | 82 个 blob（共 110 个对象） | 敏感命中 **0** |
| 提交内路径 | 74 条 | 私有目录 **0** 个，二进制兜底类型 **0** 个 |

扫描类别（词表本身保存在仓库之外的内部脚本，不随公开仓库发布）：
真实项目名（中文/拼音）、招标编号、人员姓名、手机号、身份证号、证书编号、
统一社会信用代码、合同编号、本机绝对路径、token/密钥。

## 4. 公开环境测试

```
Ran 93 tests in 0.724s / OK (skipped=25)
```

- FAIL / ERROR：**0**
- 跳过：均为**设计性跳过**（缺少真实语料或真实项目产物），原因逐条写在用例里：

| skip 原因 | 次数 |
|---|---|
| 缺少本地项目配置 config/sample_a.json（不入库） | 9 |
| 缺少本地项目配置或投标文件文本 fixture（不入库） | 6 |
| 缺少项目配置 config/sample_a.json（真实项目配置不入库） | 5 |
| run-005-final 尚未生成 | 2 |
| 对照项目产物尚未生成（本地） | 1 |
| 阶段报告为本地私有文档，未纳入仓库 | 1 |
| 本机没有可检查的运行产物（真实项目产物不入库） | 1 |

合成示例端到端用例 `tests/synthetic/test_end_to_end.py` 在**没有任何真实数据**的条件下全部执行，
覆盖：字段抽取与引文回填、资格/业绩/信用条款、五要素业绩判定、三分法归属、产物 schema 校验、
跨项目污染检查。

## 5. 复现方式

```bash
# 1) 从内部仓库重新导出（含代号替换、覆盖公开专有文件、扫描、跑公开测试、刷新扫描报告）
python scripts_private/build_public_release.py \
  --source <内部仓库> --target JTY-BidCompiler-Public
# 2) 在导出目录里建立全新历史
git init -b main && git add -A && git commit -m "Initial public release of JTY-BidCompiler"
# 3) 提交后核验（历史体检 + 全对象扫描 + 复跑测试 + 生成本报告）
python scripts_private/verify_public_commit.py --repo JTY-BidCompiler-Public
```

导出脚本与核验脚本刻意放在**两个仓库之外**（`scripts_private/`），
因为它们含有内部代号映射与敏感词表。

> 本报告在 Source Review Fix-1 提交之前用导出后核验数据生成，随该提交一起入库；报告自身不含任何真实项目、公司、人员或证件信息。
