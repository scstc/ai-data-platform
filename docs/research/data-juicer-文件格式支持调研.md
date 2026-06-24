# data-juicer 文件格式支持调研

> 调研日期：2026-06-24
> 调研人：Simple
> 调研对象：本仓库 `data-juicer/` fork（阿里 datajuicer/data-juicer，本地稳定版在 `prod` 分支）
> 依据来源：**本地源码实测**（`data_juicer/format/*`、`pyproject.toml`、`config/config.py`）+ 火山引擎 LAS《AI 数据湖服务·数据集管理》需求 PDF（`docs/requirements/AI 数据湖服务_数据集管理_1775258264.pdf`）
> 关联需求：本平台「文件管理 / 数据治理」对用户上传办公文档（Word/Excel/PDF）的处理诉求

---

## 一、结论速览（TL;DR）

| 问题 | 结论 |
|------|------|
| data-juicer 支持哪些输入格式？ | text 系（`.docx/.pdf/.txt/.md/.tex` + 约 70 种代码源码）、`.json/.jsonl`、`.csv`、`.tsv`、`.parquet`，以及 HuggingFace 远程数据集 |
| 支持 Word？ | ✅ `.docx` 支持（`python-docx` 抽段落文本）；❌ 旧版 `.doc` 不支持 |
| 支持 PDF？ | ✅ 支持（`pdfplumber`，自动剔除表格框与页脚页码） |
| 支持 Excel？ | ❌ **不支持**。全仓无 excel/openpyxl 依赖，无对应 formatter；表格数据只能走 `.csv/.tsv/.parquet` |
| 处理后的产物格式？ | **不是原格式，默认导出 `jsonl`**（一行一个样本）；导出**仅支持 `jsonl/json/parquet` 三种**（`Exporter._router()`），与输入格式无关 |
| docx/pdf 在内部如何处理？ | 先被转成纯文本 `.txt` 写入缓存目录，再作为 text 样本进入 dataset——版式/图像/表格在转换时丢失 |
| 与 LAS 需求是否一致？ | 一致。LAS 数据集格式（Lance/Iceberg/CSV/JSONL/Parquet/Image/Audio/Video/Text/Lerobot）**同样不含 Word/Excel/PDF**，两边都需要「前置格式归一化」 |

**一句话**：用户上传的 docx/pdf 能被 data-juicer 直接吃（转 txt），但 **xlsx/.doc 必须先转**；产物统一是 jsonl/parquet 这类结构化语料，不会保留原文件格式。

---

## 二、data-juicer 输入格式支持（本地 fork 实测）

数据进入 data-juicer 由 **Formatter** 负责，按文件后缀自动分发——见 `data_juicer/format/load.py::load_formatter`：遍历各 formatter 的 `SUFFIXES`，命中数量最多的 formatter 胜出。

注册的 formatter 与支持后缀：

| Formatter | 文件 | 支持后缀 |
|-----------|------|----------|
| `TextFormatter` | `text_formatter.py` | `.docx` `.pdf` `.txt` `.md` `.markdown` `.tex` `.rst` `.html` `.xml` + 约 70 种**代码源码**（`.c/.cpp/.h/.cs/.py/.java/.js/.ts/.tsx/.go/.rs/.rb/.lua/.php/.pl/.sql/.sh/.bash/.ps1/.f90/...`、`Dockerfile`/`Makefile`） |
| `JsonFormatter` | `json_formatter.py` | `.json` `.jsonl` |
| `CsvFormatter` | `csv_formatter.py` | `.csv` |
| `TsvFormatter` | `tsv_formatter.py` | `.tsv` |
| `ParquetFormatter` | `parquet_formatter.py` | `.parquet` |
| `EmptyFormatter` | `empty_formatter.py` | （空，占位用） |

外加 `HuggingFaceFormatter`（`formatter.py`），可直接读取 HuggingFace Hub 上的远程数据集，不受本地后缀约束。

> 后缀不匹配时 `load.py` 会抛 `No suitable formatter found ... Supported extensions: [...]`，把全部支持的扩展名打出来——排错很方便。

---

## 三、Word / Excel / PDF 支持情况详解

### 3.1 依赖与能力一览

| 类型 | 支持 | 依赖（`pyproject.toml`） | 抽取函数 |
|------|------|--------------------------|----------|
| Word `.docx` | ✅ | `python-docx`（第 98 行） | `extract_txt_from_docx` |
| 旧版 Word `.doc` | ❌ | — | 不在 `SUFFIXES`，需先用 LibreOffice/Word 转 `.docx` |
| PDF `.pdf` | ✅ | `pdfplumber`（第 96 行） | `extract_txt_from_pdf` |
| Excel `.xlsx/.xls` | ❌ | **无**（全仓未引入 openpyxl/xlrd/pandas-read-excel） | 无对应 formatter |

### 3.2 docx / pdf 的转换逻辑（关键）

两个函数都在 `data_juicer/format/text_formatter.py`，行为是**有损纯文本抽取**，不是版式还原：

- **`extract_txt_from_docx(fn, tgt_path)`**：`doc.paragraphs` 逐段取 `para.text`，过滤空段，`\n` 拼接成文本，输出同名 `.txt`。**只取正文段落，不含表格、图片、页眉页脚。**
- **`extract_txt_from_pdf(fn, tgt_path)`**：用 `pdfplumber.open()` 逐页 `extract_text()`，且：
  - 调 `page.find_tables()` 找到表格后用 `page.outside_bbox(table.bbox)` **剔除表格区域**（避免表格被抽成一团乱码文本）；
  - 末尾若是页码则裁掉；
  - 各页文本 `\n` 拼接，输出同名 `.txt`。

抽取产物写到 `DATA_JUICER_CACHE_HOME` 下的缓存目录，再由 text formatter 统一加载为 dataset 样本（一个文件 → 一条样本，文本落在配置的 `text_keys` 字段里）。

> 含义：data-juicer 的定位是 **LLM 语料处理**，关心的是文本内容质量（清洗/过滤/去重），不负责保留文档版式。复杂的 PDF（多栏、扫描件、图表为主）抽取效果会打折，扫描件需要先 OCR。

### 3.3 加密文件支持

`text_formatter.py` 还内置了 `_decrypt_and_extract`：若配置 `decrypt_after_reading=True`，会先用 Fernet 解密再走上面的 docx/pdf 抽取（内存中完成，不落明文）。本项目当前未用到，但说明 data-juicer 对「上传后加密存储」是有原生支持的。

---

## 四、处理后的产物格式

**默认导出 `jsonl`，不是原文件格式。**

依据 `data_juicer/config/config.py`：

- `--export_path` 默认 `./outputs/hello_world/hello_world.jsonl`
- `--export_type` 可显式指定；不指定则由 `export_path` 后缀推断
- **导出仅支持 `jsonl / json / parquet` 三种**（`data_juicer/core/exporter.py::Exporter._router()`，L454-463；`_get_suffix` 注释明确 "We only support [jsonl, json, parquet]"）；**不支持 csv/tsv 导出**（csv/tsv 只能作输入）
- 导出格式**与输入格式完全解耦**——输入 docx 还是 csv 都不影响，只由 `export_path` 后缀决定
- 另有 `--export_shard_size`（按字节分片）、`--export_in_parallel`（并行导出）等

数据流向（N 种输入 → 1 张内部表 → 3 种导出）：

```
.docx/.pdf/.txt/代码 ──formatter(转txt)──▶ ┐
.csv/.tsv           ──formatter────────▶  ├──▶ Dataset ──pipeline(清洗/过滤/去重算子)──▶ export ──▶ .jsonl (默认)
.parquet            ──formatter────────▶  ┘   (一张表:行=样本,列=字段)                    │  .json
.json/.jsonl        ──formatter────────▶                                                    └  .parquet
```

> 一条 `.docx` 进 data-juicer，产物是 jsonl 里的**一行**（含你配置的文本字段），而不是一个新的 `.docx`。无论输入什么格式，产物永远是 jsonl/json/parquet 三选一。

> 注意：csv/tsv 可作输入但**不能作输出**。若下游非要 csv，需在导出 jsonl/parquet 后自行转换。

---

## 五、对照 LAS《数据集管理》需求

需求 PDF（火山引擎 LAS「AI 数据湖服务」数据集管理）定义的数据集可选格式：

> Lance / Iceberg / CSV / JSONL / Parquet / Image / Audio / Video / Text / Lerobot

**两边格式对照**：

| 能力 | data-juicer（输入） | LAS 数据集格式 | 一致性 |
|------|---------------------|----------------|--------|
| 结构化表格 | csv / tsv / parquet / json | CSV / JSONL / Parquet / (Lance/Iceberg) | ✅ 一致 |
| 纯文本 | txt / md / docx / pdf / 代码 | Text | ✅ 一致（docx/pdf 在 data-juicer 侧先转 txt） |
| 多模态 | 图像/音频/视频算子（mappers） | Image / Audio / Video | ✅ 各自覆盖 |
| Word/Excel/PDF 直接入库 | ❌（docx/pdf 走转 txt；excel 不支持） | ❌（不在格式列表） | ✅ **一致：都不直接收** |
| 数据湖格式 | ❌ | Lance / Iceberg | ⚠️ data-juicer 无，需入库时转换 |

**核心结论**：参考产品 LAS 同样**不把 Word/Excel/PDF 作为数据集格式直接消费**，必须先归一化。本项目与参考产品在此点完全对齐——办公文档需要一道**前置格式转换**工序。

---

## 六、对本项目的落地建议

### 6.1 推荐方案：上传/治理任务前置「格式归一化算子」

在「文件管理 → 上传」或「数据治理任务」入口加一个归一化步骤，复用 data-juicer 已有的抽取函数，避免重造轮子：

| 用户上传 | 归一化为 | 复用 / 实现 | 落点 |
|----------|----------|-------------|------|
| `.docx` | `.txt`（或 jsonl 一行） | data-juicer `extract_txt_from_docx` | Text / JSONL 数据集；data-juicer 直吃 |
| `.pdf` | `.txt`（去表格去页码） | data-juicer `extract_txt_from_pdf` | 同上 |
| `.xlsx/.xls` | `.csv` / `.parquet` | `pandas.read_excel`（需补 `openpyxl` 依赖） | CSV / Parquet 数据集 |
| `.doc`（旧版） | 先转 `.docx` 再抽文本 | LibreOffice headless (`soffice --convert-to docx`) | 否则两边都不认 |
| `.csv/.jsonl/.parquet/.txt` | 原样 | — | 直接入库 / 进 data-juicer |

### 6.2 边界与风险（需要在方案里明确）

- **PDF 扫描件**：`pdfplumber` 抽不出文字，需先 OCR（如 PaddleOCR / Tesseract）再走文本流。
- **Excel 多 sheet / 公式 / 图表**：`pandas.read_excel` 默认只读第一个 sheet，多 sheet 需指定；公式取值不取公式串；图表丢弃。
- **docx 表格/图片**：`extract_txt_from_docx` 只取段落正文，表格内容会丢——若业务需要表格，要改用遍历 `doc.tables` 或换 `python-docx` + 表格抽取逻辑。
- **产物统一 jsonl**：与 LAS「另存为方舟仅支持 jsonl」一致，导出环节天然对齐，无需额外适配。

### 6.3 不建议的做法

- 不要尝试给 data-juicer **新增 excel formatter** 去原生支持 xlsx——投入产出比低，LAS 也不要求数据集直接收 excel；在上传侧转 csv 更简单、更通用（符合 Rule 2 简单优先）。
- 不要让 docx/pdf **保留原格式**进入下游——data-juicer 的价值在文本语料处理，保留版式会脱离其设计目标。

---

## 七、附录：源码定位清单

| 关注点 | 位置 |
|--------|------|
| Formatter 注册与后缀分发 | `data_juicer/format/load.py::load_formatter` |
| Formatter 基类 / 统一格式化 | `data_juicer/format/formatter.py` |
| Text formatter + docx/pdf 抽取 | `data_juicer/format/text_formatter.py`（`extract_txt_from_docx` L51、`extract_txt_from_pdf` L72、`SUFFIXES` L111） |
| 各结构化 formatter | `csv_formatter.py` / `tsv_formatter.py` / `json_formatter.py` / `parquet_formatter.py` |
| 导出默认值与参数 | `data_juicer/config/config.py`（`--export_path` L221、`--export_type` L227） |
| 导出格式路由（仅 jsonl/json/parquet） | `data_juicer/core/exporter.py`（`_router` L454、`to_jsonl/to_json/to_parquet`） |
| 依赖声明 | `pyproject.toml`（`pdfplumber` L96、`python-docx` L98） |

> 说明：本调研以**本地 fork 源码**为权威依据，上游阿里文档可能滞后或不反映 fork 改动。代码大幅更新后需在 `data-juicer/` 内重跑 `/understand` 刷新知识图谱。
