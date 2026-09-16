# JTY-BidCompiler 架构设计

> 投标编译器 PoC ｜ 版本 0.1.0 ｜ 2026-09-15
> 定位：把"收到招标文件 → 第一份完整可审稿"的时间压缩下来，同时**不降低质量**。

---

## 1. 系统边界

### 输入
| 输入 | 形态 | 来源 |
|---|---|---|
| 招标文件 | PDF（带文本层）或已提取的带页标记文本 | 招标平台下载 |
| 公司素材 | 素材 manifest（JSON）+ 素材本体（本地目录；未来 Paperless） | 公司素材库 |
| 已核验核验表 | Excel（人员五要素、证件核验等） | 历史项目人工产物 |
| 待生成投标文件 | DOCX（文本层 + media） | 编译器产物 |

### 输出
| 产物 | 说明 |
|---|---|
| `project.json` | 项目基本信息，12 字段，每字段带页码/原文/置信度 |
| `requirements.json` | 投标要求矩阵数据（资格/否决项/评分项/商务格式） |
| `materials.json` | 素材 manifest |
| `qualification_matrix.json` | 要求×素材判定 + 证据链 |
| `<profile>-投标要求矩阵.xlsx` | 给人看的矩阵 |
| `<profile>-缺失材料清单.xlsx` | 给人看的缺口清单 |
| `bid-lint.json` / `.md` | 自动 QA 结果 |

### 明确不做（PoC 阶段）
复杂 Web 前端、用户系统、数据库集群、微服务、AI 聊天界面。
Word 自动装配（MVP-6～9）在第二阶段，且必须以 MVP-1～5 的可靠性为前提。

---

## 2. 分层架构

```
                    ┌──────────────────────────────────────────┐
   招标文件 PDF ───▶ │ parsers/   文档加载 → 字段/要求抽取        │
                    │  tender_document / tender_fields /        │
                    │  tender_requirements                     │
                    └───────────────┬──────────────────────────┘
                                    │ project.json / requirements.json
                                    ▼
   公司素材 ────────▶ ┌──────────────────────────────────────────┐
   （本地目录 /       │ adapters/  MaterialRepository（可替换）    │
     Paperless）      │  LocalMaterialRepository / Paperless…    │
                    └───────────────┬──────────────────────────┘
                                    │ materials.json
                                    ▼
                    ┌──────────────────────────────────────────┐
                    │ matchers/  要求 → 候选 → 证据 → 判定       │
                    │  engine.py（规则分派）                    │
                    │  performance.py（业绩五要素）              │
                    └───────────────┬──────────────────────────┘
                                    │ qualification_matrix.json
                       ┌────────────┴────────────┐
                       ▼                         ▼
          ┌────────────────────────┐  ┌──────────────────────────┐
          │ generators/            │  │ validators/              │
          │  xlsx_matrix           │  │  bid_lint（含文本层扫描）  │
          │  xlsx_gaps             │  └──────────────────────────┘
          └────────────────────────┘
                       │                         │
                       ▼                         ▼
              两张 Excel（给人看）        bid-lint.json / .md
```

依赖方向**单向向下**：`validators → matchers → adapters →（parsers 独立）`。
上层永不知道素材是本地文件还是 Paperless 文档，这是 §7 的替换前提。

---

## 3. 数据契约

五份 JSON Schema 在 `schemas/`，是**对外契约**；`src/jty_bidcompiler/schemas/validate.py`
是内置的零依赖校验器（支持 type/required/properties/additionalProperties/items/enum/
const/pattern/minimum/maximum/minItems/anyOf/not/$ref）。所有产物在写盘前都必须过校验，
`jty validate` 可随时复核。

### project.json —— 有值必须有出处

```json
{
  "deadline": {
    "value": "2026 年09 月18 日11:00",
    "value_normalized": "2026-09-18 11:00",
    "source": { "document": "...pdf", "page": 3,
                "quote": "开标时间：2026 年09 月18 日11:00",
                "section": "第一章 招标公告 开标时间", "match_rule": "F-DL-1" },
    "source_page": 3,
    "source_section": "第一章 招标公告 开标时间",
    "confidence": 0.95,
    "extraction_method": "RULE_REGEX"
  }
}
```

Schema 用 `anyOf + not` 强制这条规则：**value 非 null ⇒ source 必须是完整证据对象**；
value 为 null ⇒ source 为 null 且必须给 `reason`。
"只存最终值不存证据位置"在结构上就通不过校验。

### requirements.json —— 保留招标原文

每条要求必带 `source_text`（逐字原文）、`source_page`、`severity`、`status`、
`condition`（机器可判的结构化条件）。资格条款的原文**永不改写后丢弃**——
将来口径有争议时，唯一权威是招标原文。

### materials.json —— 不许猜值

字段缺失写 `null`。`verification_status` 取值
`VERIFIED / PARTIALLY_VERIFIED / PENDING_COMPANY / REJECTED / EXPIRED`。
`evidence_spans[]` 是该素材**已核验的原文片段**，也是五要素判定的唯一输入。

### qualification_matrix.json —— 判定 + 证据链

判定词封闭（`verdict_lexicon` 写进产物本身）：PASS / FAIL / EVIDENCE_INSUFFICIENT /
WAIT_COMPANY / NOT_EVALUATED。每条判定带 `evidence_chain[]`，元素是
`{claim, quote, material_id, source_ref}`——**没有引用的判定视为不合格输出**。

---

## 4. 关键设计决策

### 4.1 证据位置强制（红线 1）
招标 PDF 的文本层有固定毛病：`2026 年09 月18 日`、`10KV 及以上` 这类数字夹空格，
以及页脚数字插进正文流。因此 `io/textnorm.py` 提供 `PageText`：
- `flat` 去空格 + 标点折叠，用于**检索**；
- `raw` 保留原始字符，用于**取证**；
- 维护 flat→raw 下标映射，保证任何命中都能还原原文。

踩过的坑记在代码里：模式串也必须先 `fold()`，否则模式里的 `。`/`，`
与折叠后的 `.`/`,` 对不上——这是一个"看起来命中率为 0"的隐蔽 bug。

### 4.2 判定词封闭（红线 2）
人工核验口径是 `明确满足 / 明确不满足 / 证据不足，不能认定`。
引擎把这三个词写死在 `VERDICT_TEXT`，并在规则文件里列了**禁词表**
（"可能满足""大概率满足""类似业绩"…）。测试 `test_verdict_vocabulary_is_closed`
会扫描所有判定文案确保不出现禁词。

### 4.3 "缺"与"不合规"必须分开
这是最容易被做错、也最影响公司使用体验的一点：

| 情形 | 判定 | 责任方 |
|---|---|---|
| 素材库根本没有某材料 | 资格类 → `FAIL`（不可提交） | 公司 |
| 有素材但不达标（等级不够/已过期） | `FAIL` | 公司 |
| 有素材但关键信息缺失（有效期未登记） | `EVIDENCE_INSUFFICIENT` | 公司 |
| 只能由公司实际执行的事（缴纳保证金、定人、报价） | `WAIT_COMPANY` | 公司 |
| 内容质量类（服务方案写得好不好） | `NOT_EVALUATED` | 编译器/人工 |

如果只有"通过/不通过"，公司会把"还没交保证金"和"文件里写错了旧项目名"当成
同一类问题——前者要等，后者要立刻改。

### 4.4 规则优先，LLM 不背锅（红线 3）
能用确定性规则判的绝不用模型判。当前**全部判定均为纯规则**，未调用任何模型。
将来引入模型的唯一场景是"语义归并"（例如把不同写法的公司名归一），
且必须回传原文引用；模型不得直接产出判定结论。

### 4.5 素材来源可替换（红线 4）
见 §7。

---

## 5. 业绩五要素引擎（本 PoC 的核心）

### 5.1 规则来源
招标文件第一章 3.3 原文：

> 投标人自2023 年1 月1 日至投标截止时间（以合同签订日期为准），须具有1 项10KV 及以上
> 电压等级高压开关柜改造或维修业绩，须提供合同扫描件，合同扫描件至少包含：合同买卖双方
> 盖章页、合同签订时间和业绩要求中能体现10KV 及以上电压等级变配电设备改造或维修业绩信息页；

五要素 = 合同主体 ＋ 签订时间(≥2023-01-01) ＋ 电压等级(≥10kV) ＋ 工作对象(=高压开关柜)
＋ 工作性质(=改造或维修)，**五项同时成立**且**每项都要能引用合同原文**。

### 5.2 逐元素实现要点

| 要素 | 判定方式 | 关键反例（必须判不满足） |
|---|---|---|
| E1 主体 | 合同原文出现投标人名称（优先取带合同编号的身份片段） | — |
| E2 时间 | 签订日期 ∈ [2023-01-01, 投标截止] | 2022 年合同 |
| E3 电压 | 证据片段中最大 kV 值 ≥10 | **`5000KVA` 是容量不是电压**（用负向断言排除） |
| E4 对象 | 出现"开关柜"，且处于改造维修类语境；仅"检查/试验"语境 → `PARTIAL` | "新建变电站""线路迁改""缆化改造""杆塔" |
| E5 性质 | 出现 改造/维修/检修/抢修；且该词所在语境不是**纯线路**语境 | "缆化改造"里的"改造"不算；"新建/安装/试验/检测"不算 |

### 5.3 三值结论与人工复核标记
- 五要素全 SATISFIED → `PASS`（明确满足）；
- 任一 NOT_SATISFIED → `FAIL`（明确不满足）；
- 证据片段为空导致 UNKNOWN → `EVIDENCE_INSUFFICIENT`（证据不足，不能认定）。

另有一类**不改变判定、只提示复核**的标记，例如
`CONTRACT_TITLE_NOT_BINDING_OBJECT`：合同主名称不含"开关柜改造/维修"字样，
但正文服务范围明确列举开关柜——这种情况可以判满足（招标要求的是业绩实质，
不是合同标题），但必须标注请评标口径终审，且**禁止改写合同名称去凑条件**。

### 5.4 验收判例（Golden Cases）
| 判例 | 人工结论 | 引擎输出 |
|---|---|---|
| 满足口径的合同 | 明确满足（+需终审口径） | PASS + `CONTRACT_TITLE_NOT_BINDING_OBJECT` |
| 不满足口径的合同（新建/安装类） | 明确不满足（对象/性质） | FAIL，阻断 E4+E5 |
| 不满足口径的合同（线路迁改类） | 明确不满足 | FAIL，阻断 E4+E5 |
| 不满足口径的合同（线路缆化类） | 明确不满足 | FAIL，阻断 E4+E5 |
| 不满足口径的合同（检测服务类） | 明确不满足（要素5） | FAIL，阻断 E5，E4=PARTIAL |

这五条是**验收闸门**：做不到就停止扩展，不去做 Word 装配。

---

## 6. Word 层预留设计（MVP-6～9，本轮不实现）

首个项目已经用血换来的经验，必须在架构里提前留位：

| 能力 | 预留位置 | 要点 |
|---|---|---|
| DOCX 模板与样式 | `templates/` | 保留母版 styles.xml/编号定义，禁止重建 |
| Heading / 目录 | `assemblers/` | TOC 只用 Word 更新，**不用 LibreOffice 另存**（跨引擎重排会丢内容） |
| 表格 | `assemblers/` | 表格数、列宽、跨页重复标题行 |
| 图片与扫描件 | `assemblers/media.py` | **media 原始字节直通**，禁止二次编码（101MB 文档里 193 张图，重编码必崩） |
| 扫描件旋转 | `assemblers/` | DrawingML 旋转属性原样携带 |
| 分页与页脚 | `assemblers/` | 分节符、页脚起始页码、封面无页码 |
| 页数与 PDF QA | `validators/` | 导出 PDF 后逐项核对（标题不拆分、长图不跨页切断、无空白页） |
| SHA256 | 全链路 | 每个中间产物与最终文件都记哈希，便于冻结与复现 |
| 导出 PDF | `scripts/` | COM 导出在 100MB 级文档上极慢（实测 >15 分钟无产出），需超时与降级策略 |

`assemblers/__init__.py` 目前是空占位，不做任何"看起来能跑"的假实现。

---

## 7. Paperless 适配器预留

上层的全部业务规则只依赖 `MaterialRepository` 抽象：

```python
class MaterialRepository(ABC):
    def describe() -> dict            # 来源描述，写进产物便于复现
    def list_materials() -> list[dict] # 素材清单
    def by_category(cat) / get(id) / evidence_of(id) / asset_handle(id)
```

- 第一版：`LocalMaterialRepository`（manifest JSON + 可选本体目录）
- 未来：`PaperlessMaterialRepository`，映射方案已写在类文档里：
  - `GET /api/documents/?tags=<category>` → 素材清单
  - `document.id` → `P-<id>`；`custom_fields` → 证书号/日期/金额/电压
  - `document.content` → `evidence_spans[].text`
  - `/api/documents/<id>/download/` → `asset_handle`
- `open_repository()` 现在对 `http(s)://` 会**明确报错**而不是假装能用。
  测试 `test_paperless_adapter_is_explicitly_unimplemented` 锁住这个行为。

替换成本：新增一个 adapter 类 + 一个 `open_repository` 分支，业务规则与规则文件零改动。

---

## 8. 规则与配置

```
rules/
  performance_five_elements.json   五要素定义、阈值、禁词、人工复核标记
  qualification_rules.json         资格规则（营业执照/承装修试等级/安许/业绩/信誉/财务）
  lint_rules.json                  11 项 QA 检查 + 未决项目录
config/
  sample_a.json                   项目 profile（输入/输出/规则路径 + Gold Test 期望值）
```

**期望值也写在配置里**（`expected` 段），测试与配置同源，
避免"文档说一套、代码算一套"。

---

## 9. 扩展点

| 要扩展的事 | 改哪里 | 不用改什么 |
|---|---|---|
| 换一个招标文件 | 新增 `config/<项目>.json`；如版式差异大，补 `FIELD_SPECS` 锚点文案 | matchers / generators / validators |
| 新增资格条件类型 | `rules/qualification_rules.json` + `engine.py` 加一个 handler | 解析器、产物 schema |
| 调整判定口径 | 改 `rules/*.json`（阈值、词表） | 代码 |
| 素材改存 Paperless | 新增 adapter | 全部业务规则 |
| 新增 QA 检查 | `rules/lint_rules.json` + `bid_lint.py` 加 `_check_*` | 其他层 |

---

## 10. 安全边界（硬约束）

- 不修改、不覆盖任何源项目正式文件（历史项目正式文件一律只读）；
- 所有输出落在 `<repo-root>`；
- 不调用付费 API；不上传公司资料到第三方；不自动提交投标；
- 不自动填真实报价；不虚构项目人员；
- 编译器只做"**它自己能确定的事**"，凡是需要公司真实材料/决策的，一律输出待办而不是编造。

---

## 11. 目录结构

```
JTY-BidCompiler/
├─ config/            项目 profile（含 Gold Test 期望值）
├─ docs/              架构设计 / Gold Test 基准 / MVP 计划 / 测试报告
├─ fixtures/          只读测试素材（含 fixture_manifest.json 与哈希）
├─ projects/<项目>/    产物（JSON + Excel）
├─ rules/             业务规则与词表
├─ schemas/           JSON Schema 契约（5 份）
├─ scripts/           便捷脚本
├─ src/jty_bidcompiler/
│   ├─ adapters/      MaterialRepository（本地/Paperless）
│   ├─ assemblers/    Word 装配（MVP-6～9 占位）
│   ├─ generators/    Excel 产物
│   ├─ io/            文本规整与取证（PageText）
│   ├─ matchers/      判定引擎
│   ├─ parsers/       招标文件解析
│   ├─ schemas/       数据模型 + 零依赖校验器
│   ├─ validators/    bid-lint
│   └─ cli.py         命令行
└─ tests/             单元测试 + 黄金判例
```
