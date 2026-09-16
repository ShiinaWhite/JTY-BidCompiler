# JTY-BidCompiler（投标编译器 / PoC）

把一份招标文件变成**可审的第一稿**的编译器：招标文件 → 结构化要求 → 素材匹配 → 缺口清单 → 投标文件自动 QA。
每个结论都带证据位置（哪一页、哪一句），判不了就说"证据不足"，不猜。

> **数据声明**：本仓库中的项目、公司、人员、证书、合同和投标数据**均为合成示例或脱敏测试数据**；
> **真实生产数据不包含在公开仓库中**。详见 [SECURITY.md](SECURITY.md)。

PoC 阶段只做 5 件事，并要求这 5 件事可靠：

| 能力 | 输入 | 输出 |
|---|---|---|
| MVP-1 招标解析 | 招标文件（PDF 或带页标记的文本） | `project.json` |
| MVP-2 要求解析 | 同上 | `requirements.json`、`投标要求矩阵.xlsx` |
| MVP-3 素材 manifest | 公司素材目录 / 已核验核验表 | `materials.json` |
| MVP-4 要求×素材匹配 | 上述两者 | `qualification_matrix.json`、`缺失材料清单.xlsx`、`selected_materials.json`、`bid_status.json` |
| MVP-5 自动 QA | 投标文件文本 + 上述产物 | `bid-lint.json`、`bid-lint.md` |

Word 自动装配（MVP-6～9）**不在本轮**。详见 [docs/MVP实施计划.md](docs/MVP实施计划.md)。

## 设计红线

1. **一切结论必须带证据位置。** 每个字段、每条判定都要能回答"这句话出自哪一页哪一句"。
2. **规则优先，LLM 不背锅。** 能用确定性规则判的绝不用模型判；模型只允许做"语义归并"，且必须回传原文引用。
3. **不许猜值。** 素材字段缺失就写 `null`，并进入缺失清单，而不是填一个像样的数。
4. **判定词表是封闭的。** 只允许 `PASS` / `FAIL` / `EVIDENCE_INSUFFICIENT` / `WAIT_COMPANY` / `NOT_EVALUATED`；禁止"可能满足""大概率满足"这类模糊结论（有测试守着）。
5. **不替公司下结论。** 真实人员、真实报价、投标保证金、平台递交与电子签章一律 `WAIT_COMPANY`，编译器不代填。
6. **素材来源可替换。** 上层业务规则只依赖 `MaterialRepository` 抽象；本地目录只是第一版 adapter。
7. **只读外部项目。** 编译器从不写回源项目目录；所有产物落在本项目目录内。

## 环境

Python ≥ 3.11。零新增第三方依赖（标准库 + 已安装的 `openpyxl`、`pymupdf`）。
自测用标准库 `unittest`（不用 pytest）；JSON Schema 校验由内置极简校验器完成（`src/jty_bidcompiler/schemas/validate.py`）。

```bash
export PYTHONPATH=src        # Windows: set PYTHONPATH=src
python -m jty_bidcompiler.cli --help
```

## 快速开始（合成示例，不需要任何真实数据）

仓库自带一份**完全合成**的招标文件与素材库，可直接跑通全流程：

```bash
export PYTHONPATH=src
python -m jty_bidcompiler.cli run --profile synthetic
```

产物落在 `projects/synthetic/run-001/`（该目录不入库），包括：

- `project.json`、`requirements.json` —— 字段与要求（每条带页码与原文引文）
- `qualification_matrix.json` —— 要求 × 素材的逐条判定
- `投标要求矩阵.xlsx`、`缺失材料清单.xlsx`
- `selected_materials.json` —— 三分法归属（资格可用 / 评分可用 / 仅展示）
- `bid_status.json` —— 能否闭环、哪些项等公司
- `bid-lint.json`、`bid-lint.md`、`contamination.json`、`run_manifest.json`

合成语料见 [tests/synthetic/](tests/synthetic/)：
`source/tender/synthetic_tender.txt`（带 `===== PDF第N页 =====` 页标记的招标文件）
与 `source/materials.synthetic.json`（合成素材库）。

## 换成自己的项目

1. 复制 `config/example.json` 为 `config/<你的项目>.json`，填写招标文件路径、素材库路径、投标人全称
   （真实项目配置**不要**提交，`.gitignore` 已排除）。
2. 把招标文件与素材库放进 `fixtures/<profile>/`（同样不入库）。
3. `python -m jty_bidcompiler.cli run --profile <你的项目>`

单条命令：

```bash
python -m jty_bidcompiler.cli parse-tender --tender <招标文件> --out-dir <输出目录>   # MVP-1 + MVP-2
python -m jty_bidcompiler.cli materials    --manifest <素材库.json> --out-dir <输出目录>  # MVP-3
python -m jty_bidcompiler.cli match        --profile <项目>                          # MVP-4（含两张 Excel）
python -m jty_bidcompiler.cli lint         --profile <项目>                          # MVP-5
python -m jty_bidcompiler.cli validate     --dir <输出目录>                          # 产物 schema 校验
```

## 运行测试

```bash
export PYTHONPATH=src
python -m unittest discover -s tests -t . -v
```

- **合成示例与纯逻辑测试**（`tests/unit/`、`tests/golden/`、`tests/gold/`）在任何克隆里都会执行。
- **需要真实语料的测试会自动 skip**，并在 skip 原因里写明缺什么，例如：
  `缺少项目配置 config/sample_a.json（真实项目配置不入库）`、`缺少招标文件 fixture`。
  skip 是**设计行为**，不是失败；`FAIL` 必须为 0。
- 只想跑合成示例的端到端用例：

```bash
python -m unittest tests.synthetic.test_end_to_end -v
```

## 目录

```
src/jty_bidcompiler/
  parsers/      招标文件 → 结构化（字段 / 要求 / 资格条款语义 / 第五章条款）
  matchers/     五要素业绩判定 + 要求×素材匹配引擎
  generators/   Excel 产物（投标要求矩阵 / 缺失材料清单）+ 选择结果与投标状态
  validators/   bid-lint 自动 QA + 跨项目污染检查
  adapters/     MaterialRepository 抽象 + LocalMaterialRepository
  schemas/      JSON Schema 极简校验器
  assemblers/   （占位）Word 自动装配，MVP-6～9
  io/           文本规范化（去空白、折叠标点、页码映射、回填原文）
config/         运行配置（example 模板 + synthetic 合成示例）
rules/          业务规则与词表（可读、可改、可进版本库）
schemas/        JSON Schema 原文（对外契约）
tests/          单元测试 / 黄金判例 / 合成示例端到端
docs/           架构设计 / MVP 实施计划 / 仓库公开审计
```

## 安全边界

- 不修改、不覆盖任何源项目正式文件（历史项目正式文件一律只读）。
- 不写入生产目录；所有输出在 `<repo-root>` 内。
- 不调用付费 API，不上传公司资料到第三方。
- 不自动填真实报价、不虚构人员、不自动提交投标。

## 许可

[MIT](LICENSE)。
