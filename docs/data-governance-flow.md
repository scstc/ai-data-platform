# AI 数据平台 —— 多格式数据治理流程

> 版本:v1.0 | 日期:2026-06-30 | 状态:设计文档

## 概览

本文档定义 AI 数据平台对**异构格式文件**(文本类 + 多媒体类)的端到端治理流程,从原始文件到训练/推理平台可消费的标准数据集。

### 设计原则

1. **分层解耦**:接入层(异构 → 统一格式)、治理层(data-juicer)、交付层(训练友好)三层分离
2. **统一数据集**:多文件合并为带血缘的单一数据集,而非逐文件一对一转换
3. **路径引用**:多媒体内容不进数据集,只存路径,实际文件走对象存储
4. **可复现**:全程声明式配置,输入 + config → 确定性产出

---

## 一、数据源分类与流转模型

### 1.1 数据源分类矩阵

#### 1.1.1 文件类数据源

| 类别 | 格式 | 处理方式 | 最终 schema |
|---|---|---|---|
| **纯文本** | txt, log, md | 直接加载 | `{text}` |
| **结构化** | csv, tsv, xlsx, json, jsonl | 解析为列 | `{col1, col2, ...}` |
| **文档** | pdf, docx, pptx, html | markitdown 提取文本 | `{text, source_file, page?}` |
| **图片** | png, jpg, gif, webp | 路径引用 + 对象存储 | `{text?, images: [path]}` |
| **音频** | mp3, wav, flac, m4a | 路径引用 + 对象存储 | `{text?, audios: [path]}` |
| **视频** | mp4, avi, mov, mkv | 路径引用 + 对象存储 | `{text?, videos: [path]}` |

#### 1.1.2 结构化数据源

| 类别 | 来源 | 处理方式 | 落地格式 | 特点 |
|---|---|---|---|---|
| **数据库** | MySQL(goldendb)、PostgreSQL、专有库 | SELECT → records → `land_records` | **parquet**(失败兜底 jsonl) | 批量/增量采集,保留列类型 |
| **API推送** | 外部系统 Webhook | POST → `land_push_records` 同步落地 | jsonl(归并为新版本) | 实时,幂等去重 |

> **落地格式总原则**:接入层统一经 `land_records` 出口落地。**结构化源(数据库)优先 parquet**(列式存储天然适合表数据、保留列类型);parquet schema 推断失败(空/嵌套/异构)时**兜底回退 jsonl**,采集照常成功(D2 红线:不因格式问题丢数据)。文本/媒体源走 jsonl/manifest。

#### 1.1.3 存储类数据源

| 类别 | 协议 | 具体产品 | 连接方式 | 适配策略 |
|---|---|---|---|---|
| **S3 兼容存储** | S3 API | 华为云 OBS、MinIO、AWS S3、阿里 OSS、腾讯 COS | boto3 / s3fs(只换 `endpoint_url`) | **统一连接器** |
| **HDFS** | WebHDFS REST | Hadoop 分布式文件系统 | httpx 调 WebHDFS(无需本地 Hadoop 客户端) | **独立连接器** |

> **关键认知 1**:S3 兼容的几个产品(OBS/MinIO/...)底层都是 S3 协议,**唯一区别是 `endpoint_url`**,一套代码全搞定,不需要为 MinIO 单独写连接器。HDFS 是完全不同的协议,需独立适配。详见 2.5 节。

> **关键认知 2**:HDFS 上"文件放在哪"与"文件是什么格式"是正交的两件事。本期取舍:
> - **原始文件类**(csv/txt/word/pdf/媒体)→ ✅ 本期支持,按扩展名走与文件上传相同的解析流程(`normalize_to_records`)
> - **数仓格式类**(parquet/orc/Hive 分区表)→ ⛔ 不从 HDFS 拉文件;数仓数据走 **Hive/Doris 直连**(需求3),由引擎读成行
>
> 详见 2.5.3 节。

### 1.2 核心约束

| 约束项 | 说明 | 解法 |
|---|---|---|
| **formatter 互斥** | data-juicer 单目录只能命中一种 formatter | 预处理统一转 jsonl |
| **schema 对齐** | 多源拼接要求列结构一致 | 接入层强制规范化 |
| **媒体不内嵌** | 图/音/视频不进数据集文件,只存路径 | OBS 归档 + 路径索引 |
| **血缘溯源** | 需知道每条样本来自哪个原始文件/表/API | 注入 source_type + 来源元数据字段 |
| **实时性差异** | 文件/数据库批量采集 vs API实时推送 | API推送同步落地 + 幂等键去重,多次推送归并为版本 |

---

## 二、端到端流程设计

```
┌──────────────────────────────────────────────────────────────────┐
│ 第一阶段:接入层(Backend landing service)                          │
├──────────────────────────────────────────────────────────────────┤
│ 输入:多种异构数据源                                               │
│ 输出:统一 jsonl + 元数据(DatasetVersion)                         │
│                                                                  │
│ 文件类 ──┬── pdf/docx/pptx ──[markitdown→去格式]──▶ {text, meta} │
│         ├── csv/xlsx ──────[列映射]──────▶ {text, col1...}      │
│         └── txt/log ───────[逐行]────────▶ {text}               │
│                                                                  │
│ 媒体类 ──┬── 图/音/视频 ──[OBS归档]──▶ 原样存储                   │
│         └────────────────[生成索引]──▶ {images/audios/videos}  │
│                                                                  │
│ 数据库 ──── SQL查询 ──[分批读取+列映射]──▶ {text, source_db...}  │
│                                                                  │
│ API推送 ─── Webhook ──[同步落地+版本归并]──▶ {records, 累积为新版本}│
│                                                                  │
│ 存储类 ──┬── S3兼容(OBS/MinIO) ──[s3fs,换endpoint]──▶ 读取/归档  │
│         └── HDFS ──────────────[pyarrow/WebHDFS]──▶ 拉取到本地   │
│                                                                  │
│ 统一产物:dataset_v1_raw.jsonl (带 source_type/来源元数据)       │
└──────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│ 第二阶段:治理层(data-juicer pipeline)                             │
├──────────────────────────────────────────────────────────────────┤
│ 输入:dataset_v1_raw.jsonl (from DB managed version)              │
│ 配置:process.yaml (用户在前端配置算子流水线)                      │
│                                                                  │
│ 算子流水线:                                                       │
│   1. 质量过滤(Filter)                                            │
│      - language_id_score_filter  # 语言检测                      │
│      - text_length_filter        # 长度卡阈值                    │
│      - perplexity_filter         # 困惑度(可选,需 LLM)           │
│      - image_aesthetics_filter   # 图片美学(媒体集)              │
│                                                                  │
│   2. 去重(Deduplicator)                                          │
│      - document_minhash_deduplicator  # 跨文档去重               │
│      - image_deduplicator             # 图片感知哈希             │
│                                                                  │
│   3. 合规(Mapper)                                                │
│      - remove_specific_chars_mapper   # PII 清洗                 │
│      - image_face_blur_mapper         # 人脸打码                 │
│                                                                  │
│   4. 增强(可选)                                                  │
│      - video_captioning_mapper        # 视频生成文本描述         │
│                                                                  │
│ 产物:dataset_v1_clean.jsonl + _stats.jsonl                       │
└──────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│ 第三阶段:交付层(Export to training platform)                      │
├──────────────────────────────────────────────────────────────────┤
│ 格式转换:jsonl → parquet (列式存储)                              │
│ 分片策略:export_shard_size=256MB (分布式读)                      │
│                                                                  │
│ 交付物清单:                                                       │
│   ├── corpus-00-of-10.parquet   # 元数据(轻量,GB 级可能几十 MB) │
│   ├── corpus-01-of-10.parquet                                   │
│   ├── ...                                                       │
│   ├── corpus_stats.jsonl        # 质量审计                      │
│   ├── process.yaml              # 可复现凭证                     │
│   └── [媒体目录 or OBS bucket]  # 实际的图/音/视频文件           │
│                                                                  │
│ 存储策略:                                                         │
│   - 元数据(parquet):平台数据库 or S3                             │
│   - 媒体文件:OBS (10.60.1.60:55433 对应的对象存储)              │
│   - 路径格式:obs://bucket/train/media/img_0001.png              │
└──────────────────────────────────────────────────────────────────┘
```

### 2.1 PDF 类型识别与处理分流

#### 2.1.1 PDF 的两种类型

| 类型 | 特征 | 如何识别 | 处理方式 | 工具 |
|-----|------|---------|---------|------|
| **文本型 PDF** | 可复制文字,能搜索 | 在阅读器中能选中/复制文本 | markitdown 直接提取 | pdfplumber |
| **扫描型 PDF** | 不能复制,每页是图片 | 无法选中文字,或提取后为空 | **必须 OCR** | Unlimited-OCR |

**实际场景**:企业文档库经常两种混杂(如:合同扫描件 + Word 转的报告)。

#### 2.1.2 自动识别流程

```
PDF 上传
  ↓
markitdown 尝试提取
  ↓
判断:提取文本长度 > 50 字符/页?
  ├─ YES → 文本型 PDF,直接使用
  │         ↓
  │    去格式(markdown → plain text)
  │         ↓
  │    写入 jsonl
  │
  └─ NO  → 疑似扫描型 PDF
            ↓
       OCR 识别(Unlimited-OCR)
            ↓
       对比两者结果,取更长的
            ↓
       写入 jsonl
```

**代码实现**:
```python
def _detect_pdf_type(text: str, page_count: int = 1) -> bool:
    """判断 PDF 是否为扫描型。
    
    Args:
        text: markitdown 提取的文本
        page_count: PDF 页数
    
    Returns:
        True = 扫描型(需 OCR), False = 文本型
    """
    # 启发式规则:每页平均少于 50 个非空字符 → 判定为扫描型
    non_whitespace_chars = len(''.join(text.split()))
    avg_chars_per_page = non_whitespace_chars / max(page_count, 1)
    
    return avg_chars_per_page < 50
```

#### 2.1.3 OCR 方案:百度 Unlimited-OCR

扫描型 PDF 统一用 **百度 Unlimited-OCR**(开源,一次性长文档解析):

| 方案 | 特点 | 优点 | 注意 |
|-----|------|------|------|
| **Unlimited-OCR** | 百度开源,One-shot 长文档解析 | • 中文准确率高<br>• 支持复杂排版(表格/多栏)<br>• 免费开源 | • GPU 模式更快<br>• 首次下载模型 ~1GB |

**资源建议**:

| 数据规模 | 运行方式 | 说明 |
|---------|---------|------|
| 小规模(< 1000 页/月) | CPU 单机 | 慢但够用 |
| 中规模(1000~1 万页/月) | 单 GPU | 一次性长文档解析快 |
| 大规模(> 1 万页/月) | GPU 集群 | 分布式处理 |

#### 2.1.4 Unlimited-OCR 集成方案

**项目地址**:[https://github.com/baidu/Unlimited-OCR](https://github.com/baidu/Unlimited-OCR)  
**核心能力**:One-shot Long-horizon Parsing(一次性解析整个长文档,而非逐页)

**安装**:
```bash
# 克隆仓库
git clone https://github.com/baidu/Unlimited-OCR.git
cd Unlimited-OCR

# 安装依赖
pip install -r requirements.txt

# 下载模型(首次运行自动下载,约 1GB)
python demo.py --help
```

**代码实现**:
```python
# backend/app/services/landing.py

def _ocr_pdf_unlimited(file_path: str) -> str:
    """使用 Unlimited-OCR 识别扫描型 PDF。
    
    优势:一次性解析整个文档,保留复杂排版(表格/多栏)。
    """
    import subprocess
    import json
    import tempfile
    
    # 调用 Unlimited-OCR CLI
    # (实际集成时可能需要作为 Python 包导入,这里演示命令行方式)
    output_file = tempfile.NamedTemporaryFile(suffix='.json', delete=False)
    
    try:
        result = subprocess.run(
            [
                'python', 'Unlimited-OCR/demo.py',
                '--pdf', file_path,
                '--output', output_file.name,
                '--lang', 'ch'  # 中文模式
            ],
            capture_output=True,
            text=True,
            timeout=300  # 5 分钟超时
        )
        
        if result.returncode != 0:
            raise RuntimeError(f"Unlimited-OCR 失败:{result.stderr}")
        
        # 解析输出(格式取决于实际 API)
        with open(output_file.name, 'r', encoding='utf-8') as f:
            ocr_result = json.load(f)
        
        # 提取文本(假设输出格式为 {'text': '...'})
        return ocr_result.get('text', '')
    
    finally:
        os.unlink(output_file.name)


async def land_upload(...):
    """完整流程:PDF 类型自动识别 + 分流处理。"""
    ...
    if file_ext == "pdf":
        # Step 1: markitdown 尝试提取
        md_content = _get_markitdown().convert(file_obj).text_content
        plain_text = _markdown_to_plain_text(md_content)
        
        # Step 2: 判断 PDF 类型
        page_count = _get_pdf_page_count(file_path)  # 用 PyMuPDF 获取页数
        
        if _detect_pdf_type(plain_text, page_count):
            # 扫描型 PDF → Unlimited-OCR
            logger.info(f"检测到扫描型 PDF:{filename}({page_count} 页),启动 OCR...")
            
            # 保存临时文件
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(await file.read())
                tmp_path = tmp.name
            
            try:
                ocr_text = _ocr_pdf_unlimited(tmp_path)
                
                # 取更长的结果
                if len(ocr_text) > len(plain_text):
                    plain_text = ocr_text
                    logger.info(f"OCR 成功,提取 {len(ocr_text)} 字符")
            
            finally:
                os.unlink(tmp_path)
        
        else:
            # 文本型 PDF → 直接使用
            logger.info(f"文本型 PDF:{filename},直接提取")
        
        # Step 3: 写入 jsonl
        record = {
            "text": plain_text,
            "source_file": filename,
            "pdf_type": "scanned" if _detect_pdf_type(plain_text, page_count) else "text",
            ...
        }
```

**性能参考**(基于 Unlimited-OCR 项目说明):
- CPU:10 页 PDF 约 2-5 分钟
- GPU(V100):10 页 PDF 约 30-60 秒
- 准确率:中文印刷体 ~92%,复杂表格 ~85%

---

### 2.2 文档格式处理细节(Markdown → Plain Text)

#### 2.2.1 为什么要去格式?

markitdown 提取 PDF/DOCX 后输出的是 **markdown 格式**,包含格式标记:

```markdown
# 第一章 公司概况

公司成立于 **2010 年**,主营业务包括:

- 产品 A
- 产品 B

详见 [官网](https://example.com)
```

如果直接当纯文本喂给 data-juicer 算子,会有问题:

| 问题 | 影响算子 | 举例 |
|-----|---------|------|
| **格式符号干扰语言检测** | `language_id_score_filter` | `# Title` / `[link](url)` 里的英文标记拉低中文得分 |
| **长度统计不准** | `text_length_filter` | 包含 markdown 语法字符,实际内容更短 |
| **去重误判** | `document_minhash_deduplicator` | 同一段话,格式不同(`**text**` vs `text`)算不同内容 |

#### 2.2.2 处理方案对比

| 方案 | 适用场景 | 优点 | 缺点 | 采纳 |
|-----|---------|------|------|------|
| **A. 保留 markdown** | 代码/文档模型训练(如 Codex) | 保留结构信息(标题/列表) | data-juicer 算子统计偏差 | ❌ |
| **B. 去格式 → plain text** | **LLM 训练(如 GPT)** | 算子统计准确,通用 | 丢失结构信息 | ✅ **推荐** |
| **C. 按章节拆分** | RAG 检索 | 细粒度语义单元 | 实现复杂,跨章节断裂 | 可选 |

**当前项目采用方案 B**,理由:
- 用户场景:**LLM 训练**(需要纯文本)
- 通用性:适配 data-juicer 所有文本算子
- 简单:接入层一次性处理,治理层无需感知格式

#### 2.2.3 去格式转换流程

```
PDF/DOCX ──[markitdown]──▶ Markdown ──[去格式]──▶ Plain Text ──[写 jsonl]──▶ 数据集
```

**具体步骤**:
1. **markitdown 提取**:PDF → markdown 字符串(保留格式)
2. **Markdown → HTML**:用 `markdown` 库渲染
3. **HTML → Plain Text**:用 `BeautifulSoup` 去标签
4. **清理**:删除多余空行、特殊字符
5. **写入 jsonl**:`{"text": "第一章 公司概况 公司成立于 2010 年..."}`

**转换效果对比**:

| 阶段 | 内容示例 | 字符数 |
|-----|---------|--------|
| **原始 markdown** | `# 标题\n\n公司成立于 **2010年**\n\n- 产品A` | 35(含格式) |
| **去格式后** | `标题 公司成立于 2010年 产品A` | 18(纯内容) |

#### 2.2.4 代码实现(接入层)

```python
# backend/app/services/landing.py

def _markdown_to_plain_text(md: str) -> str:
    """Markdown → plain text,删除格式标记但保留内容。
    
    用于 LLM 训练场景,确保 data-juicer 算子统计准确。
    """
    import re
    import markdown
    from bs4 import BeautifulSoup
    
    # Step 1: markdown → HTML
    html = markdown.markdown(
        md,
        extensions=['extra', 'nl2br'],  # 支持表格、换行
    )
    
    # Step 2: HTML → plain text(去标签)
    soup = BeautifulSoup(html, 'html.parser')
    
    # 特殊处理:列表项之间加换行,避免粘连
    for tag in soup.find_all(['li', 'p', 'h1', 'h2', 'h3']):
        tag.append('\n')
    
    # 提取纯文本
    plain = soup.get_text(separator=' ')
    
    # Step 3: 清理
    # 删除多余空格/空行
    plain = re.sub(r' +', ' ', plain)          # 多空格 → 单空格
    plain = re.sub(r'\n{3,}', '\n\n', plain)   # 多空行 → 双空行
    # 删除首尾空白
    plain = plain.strip()
    
    return plain


# 在文档接入时调用
async def land_upload(...):
    ...
    if file_ext in DOC_FORMATS:  # pdf/docx/pptx/html
        # 提取 markdown
        md_content = _get_markitdown().convert(file_obj).text_content
        
        # ✅ 新增:去格式转纯文本
        plain_text = _markdown_to_plain_text(md_content)
        
        # 写入 jsonl
        record = {
            "text": plain_text,  # 纯文本,无格式标记
            "source_file": filename,
            "doc_id": f"sha256:{hashlib.sha256(content).hexdigest()}",
            "ingest_batch": datetime.now(UTC).isoformat(),
        }
        ...
```

#### 2.2.5 可选:保留原始 markdown(双轨制)

如果未来需要保留格式(如训练代码模型),可以采用**双轨存储**:

```python
# 接入时同时保存两个版本
{
  "text": "标题 公司成立于 2010年...",  # 去格式,供治理层使用
  "text_raw": "# 标题\n\n公司成立于 **2010年**...",  # 保留 markdown,供归档
  "source_file": "年报.pdf"
}
```

治理层算子只操作 `text` 列,`text_raw` 透传到产物,训练时按需选择。

---

### 2.3 数据库接入详解

> **实现现状**:数据库直连已落地为 `backend/app/services/connectors/` 连接器族,经 `__init__.py::REGISTRY` 按 `(type, db_kind)` 派发。各连接器镜像统一结构(`probe` / `list_tables` / `run_ingest`),最终汇到 `land_records` 落地。**注意落地格式分两档**(见 2.3.3),本节描述真实结构而非示意代码。

#### 2.3.1 支持的数据库(REGISTRY 真实映射)

需求列出的 8 个库 + PostgreSQL,共 9 个 `db_kind` 全部已注册:

| db_kind | 连接器类 | 文件 | 驱动 | 协议复用 | 落地格式 | 状态 |
|---------|---------|------|------|---------|---------|------|
| postgresql | `PgConnector` | pg.py | asyncpg | — | parquet | 可真连 |
| **hologres** | `PgConnector` | pg.py | asyncpg | PG 线协议 | parquet | 承诺级 |
| **kingbase** | `PgConnector` | pg.py | asyncpg | PG 线协议 | parquet | 承诺级 |
| **gaussdb** | `PgConnector` | pg.py | asyncpg | PG 线协议(端口走 config) | parquet | 承诺级 |
| **goldendb** | `MysqlConnector` | mysql.py | asyncmy(懒 import) | MySQL 协议 | parquet | 有本地 MySQL 可测 |
| **dameng**(达梦) | `DamengConnector` | proprietary.py | dmPython | — | jsonl | 结构就绪 |
| **sequoiadb**(巨杉) | `SequoiaConnector` | proprietary.py | pysequoiadb | — | jsonl | 结构就绪 |
| **hive** | `HiveConnector` | proprietary.py | pyhive | — | jsonl | 结构就绪 |
| **doris** | `DorisConnector` | proprietary.py | asyncmy | (MySQL 协议但握手有差异,降档) | jsonl | 结构就绪 |

> **Hive / Doris 是数仓引擎直连,不是数仓格式文件**:它们 `type="database"`,走 JDBC/SQL 发 `SELECT`,**引擎自己把底层 parquet/orc 读成行返回**,你拿到的是 dict 列表,不碰物理文件。这与"拉 HDFS 上的 .parquet 文件"是两条完全不同的路(后者不在本期范围,见 2.5.3)。

> **驱动缺失不崩**:所有连接器驱动懒 import,模块顶层不依赖。驱动未装时 `probe` 返回 `(False, 0, not-ready 文案)`,`list_tables`/`run_ingest` 抛 `ConnectorNotReady`,绝不伪造 success(Rule 12 Fail loud)。proprietary.py 的 4 个(达梦/巨杉/Hive/Doris)为"结构就绪/承诺级",装驱动 + 真库现场后激活即可。

> **增量采集**:PG 族支持(by mtime/name);GoldenDB 暂不支持增量;proprietary.py 各连接器视实现。

#### 2.3.2 接入流程(真实)

```
前端配置数据源(连接信息) + 采集任务(extract:表/查询/增量)
  ↓
connector.probe()        # 测试连接,不可达则诚实失败
  ↓
connector.list_tables()  # 列用户表(排除系统 schema)
  ↓
connector.run_ingest()
  ├─ _build_queries(task.extract)   # 按配置构造 SELECT
  ├─ 执行查询 → records (dict 列表)
  ├─ apply_filter_operators(task, records)  # 落地前可选跑 DJ 算子筛/清洗
  └─ land_records(...)              # 落地(格式分两档,见 2.3.3)
       ↓
     PG族/GoldenDB → storage_format="parquet"(schema 失败兜底 jsonl)
     达梦/巨杉/Hive/Doris → 当前走默认 jsonl
  ↓
建 Dataset + DatasetVersion(data_type="sql", source_kind="database")
```

#### 2.3.3 落地格式:两档(需对账,勿一概而论)

`land_records` 的 `storage_format` **默认是 `"jsonl"`**,parquet 需连接器**显式传**。实测两档:

| 连接器 | 是否传 `storage_format="parquet"` | 实际落地 |
|--------|----------------------------------|---------|
| `PgConnector`(pg.py)、`MysqlConnector`(mysql.py) | ✅ 显式传 | **parquet**(schema 失败兜底 jsonl) |
| `DamengConnector` / `SequoiaConnector` / `HiveConnector` / `DorisConnector`(proprietary.py) | ❌ 未传 | **jsonl**(吃默认值) |

```python
# pg.py / mysql.py:显式 parquet
ds, ver = await land_records(
    session, records,
    dataset_name=name,
    data_type="sql", semantic_type="structured",
    source_kind="database",
    storage_format="parquet",      # ★ 显式传才落 parquet
)

# proprietary.py(达梦/巨杉/Hive/Doris):未传 → 默认 jsonl
pair = await land_records(
    session=session, name=ds_name, records=rows,
    data_type="sql",
    source_uri=f"hive://...",
    job_id=job_id,                 # 无 storage_format → land_records 默认 "jsonl"
)
```

> **现状差异已知**:PG族/GoldenDB 已对齐 parquet;proprietary.py 4 个连接器尚未传 `storage_format`,落 jsonl。若希望全部结构化源统一 parquet,需在 proprietary.py 的 4 处 `land_records` 调用补 `storage_format="parquet"`(本节如实记录现状,不掩盖)。

**为什么结构化源宜用 parquet**:

| 维度 | parquet | jsonl |
|-----|---------|-------|
| **列类型** | 保留(int/float/timestamp 不退化成字符串) | 全是文本,类型丢失 |
| **存储** | 列式压缩,表数据体积小 | 行式,冗余大 |
| **下游读取** | 列裁剪、谓词下推,训练/分析高效 | 必须全行扫描 |
| **适配** | 天然契合关系表的二维结构 | 适合半结构化/嵌套 |

**兜底机制**(`land_records` 内部):parquet schema 推断失败(空记录 / 嵌套字段 / 列类型异构)时,自动回退 `records_to_jsonl_bytes` 写 jsonl,`effective_format="jsonl"`,**采集不因格式问题失败**(D2 红线)。

#### 2.3.4 前端配置界面

```typescript
// 数据源配置页
interface DBSourceConfig {
  name: string;             // 数据源名称
  type: 'mysql' | 'postgresql' | 'mongodb';
  connection: {
    host: string;
    port: number;
    database: string;
    username: string;
    password: string;       // 加密存储
  };
  query: string;            // SQL查询
  textColumn: string;       // 哪列当text
  retainColumns?: string[]; // 保留其他列
  schedule?: string;        // cron表达式(定时采集)
}
```

**关键功能**:
- **测试连接**:点击后后端尝试连接,显示成功/失败
- **SQL 预览**:显示查询结果前 10 行,确认列映射正确
- **定时采集**:配置 cron 表达式,自动定期采集增量数据

---

### 2.4 API 推送接入详解

#### 2.4.1 架构设计

```
外部系统
  ↓
POST /api/v1/ingest/push/{token}   (token 在路径,绑定 DataSource type='api')
  ↓
按 token 查 DataSource → 校验
  ↓
land_push_records(同步落地)
  ├─ 幂等键检查(内存 TTL 10min)
  ├─ 语义归一
  └─ 归并:首次建 Dataset 并回写 boundDatasetId;后续产出新 DatasetVersion
  ↓
返回新版本 id(同步,无队列)
```

**设计取舍**:当前为**同步落地**(端点直接落库),不引入 Redis 队列/消费者——实现简单、链路短、失败即时可见。代价是高并发瞬时推送无削峰缓冲;若未来推送量大需削峰,再引入队列(本期不做)。

#### 2.4.2 前端:生成 Webhook

**数据集详情页**增加"API 推送"标签:

```typescript
// 显示 Webhook 信息(token 在路径里,不是 query 参数)
const webhookInfo = {
  url: `https://adp.example.com/api/v1/ingest/push/${token}`,
  token: "dj_push_abc123...",  // 随机生成,绑定 DataSource(type='api')
  usage: `curl -X POST "${url}" \\
    -H "Content-Type: application/json" \\
    -d '{"records":[{"content":"用户反馈..."}], "semanticType":"text"}'`
};
```

#### 2.4.3 后端实现(真实:同步落地,无 Redis 队列)

> **现状澄清**:API 推送**已实现**为 `connectors/push.py::PushConnector`(注册在 `REGISTRY[("api", None)]`),入站由端点直接调 `land_push_records` **同步落地**,**不走 Redis 队列 / 后台消费者**(此前文档里那套 Redis + supervisord 架构是设计设想,与代码不符,这里更正)。

```python
# 入站端点:POST /api/v1/ingest/push/{token}(token 在路径)
# 按 token 查到 DataSource(type='api')→ 调核心落地函数
async def land_push_records(
    session, datasource, records, *,
    semantic_type=None,        # 入参 > config.semanticType > data_type 推断
    idempotency_key=None,      # 同 key 在 TTL(10min)内重复 → 返回首版,不重复落地
):
    # 1. 幂等键检查(内存缓存版,生产化需 DB 持久去重)
    # 2. 语义归一(apply_semantic_spec)
    # 3. 归并到同一数据集:
    #    - 首次推送 → 创建 Dataset,id 写回 config["boundDatasetId"]
    #    - 后续推送 → 加载该 Dataset,产出新 DatasetVersion(version_no+1)
    # 4. 诚实失败:落盘/commit 失败抛 LandingError + 回滚(不伪成功)
    ...
```

**PushConnector 的 Protocol 行为**(注册表要求三方法齐全):
- `probe` → 推送型本地永远就绪,返回 `(True, 0, "api 推送连接器就绪")`
- `list_tables` → 推送无"表列表"语义,返回 `[]`
- `run_ingest` → 推送不走采集任务拉取路径,抛 `ConnectorNotReady`(入站只经端点)

#### 2.4.4 关键设计点

| 设计 | 真实做法 | 说明 |
|-----|---------|------|
| **多次推送归并** | `config["boundDatasetId"]` | 首次建数据集并回写绑定;后续推送累积为新版本(note="api 推送 #N") |
| **幂等** | 内存 TTL 缓存(10 min) | 同 `idempotency_key` 重复推送返回首版 id;**本期内存版,生产化需 DB 持久去重** |
| **落地格式** | `land_records` 默认 jsonl | 推送数据多为半结构化,走 jsonl |
| **失败处理** | 抛 `LandingError` + Session 回滚 | 不伪成功(Rule 12) |

---

### 2.5 存储类数据源接入(S3 兼容 + HDFS)

#### 2.5.1 两类存储的本质区别

| 维度 | S3 兼容存储 | HDFS |
|-----|-----------|------|
| **协议** | S3 API(HTTP REST) | WebHDFS REST(HTTP) |
| **产品** | 华为云 OBS、MinIO、AWS S3、阿里 OSS | Hadoop 分布式文件系统 |
| **客户端** | `boto3` / `s3fs`(统一) | `httpx` 调 WebHDFS(本项目实现) |
| **环境依赖** | 无(纯 Python SDK) | 无需本地 Hadoop 客户端(WebHDFS 纯 HTTP);需可达 NameNode |
| **适配成本** | 低(复用一套代码) | 中(独立连接器,已实现) |
| **适用场景** | 媒体归档、数据集分发、云端处理 | 已有 Spark/Hive 数仓的历史大数据 |

#### 2.5.2 S3 兼容存储:统一连接器(只换 endpoint)

**核心认知**:OBS / MinIO / AWS S3 底层都是 S3 协议,**唯一区别是 `endpoint_url`**。现有 `external_store.py` 的 S3 代码改个 endpoint 就能连 MinIO,**不需要为 MinIO 单独写连接器**。

```python
# backend/app/services/external_store.py
# 同一套代码,通过配置区分不同 S3 兼容产品

S3_PRESETS = {
    "obs": {
        # 华为云 OBS(你们在用)
        "endpoint_url": "https://obs.cn-north-4.myhuaweicloud.com",
        "region": "cn-north-4",
    },
    "minio": {
        # 内网自建 MinIO
        "endpoint_url": "http://10.60.1.x:9000",
        "region": "us-east-1",  # MinIO 默认 region,可任意
    },
    "aws_s3": {
        # AWS S3(endpoint 为 None 走默认)
        "endpoint_url": None,
        "region": "us-east-1",
    },
}

def get_s3_client(preset: str, access_key: str, secret_key: str):
    """根据预设获取 S3 客户端,OBS/MinIO/S3 通用。"""
    import boto3
    cfg = S3_PRESETS[preset]
    return boto3.client(
        "s3",
        endpoint_url=cfg["endpoint_url"],   # ✅ 唯一区别就在这
        region_name=cfg["region"],
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )
```

**data-juicer 同理**:`DefaultS3DataLoadStrategy` / `RayS3DataLoadStrategy` 都接受 `endpoint_url` 参数,YAML 里配上即可连 MinIO:

```yaml
dataset:
  configs:
    - type: remote
      source: s3
      path: s3://my-bucket/data/corpus.jsonl
      endpoint_url: http://10.60.1.x:9000   # 内网 MinIO
      # aws_access_key_id / aws_secret_access_key 走环境变量
```

#### 2.5.3 HDFS:独立连接器

> **实现现状**:HDFS 接入已落地为 `backend/app/services/connectors/hdfs.py`,走 **WebHDFS REST**(httpx,无需本地 Hadoop 客户端/驱动)。纯函数(`_build_webhdfs_url` / `_parse_liststatus`)可单测;`probe`/`list_tables`/`run_ingest` 为端到端承诺级,需真实集群验证。无可达 NameNode 时诚实失败(`ConnectorNotReady`),绝不伪造 success。

**核心认知:HDFS 上有两类内容,本期只接原始文件**

HDFS 只是"文件放在哪"(分布式文件系统),"文件是什么格式"是另一回事。两类区别 + 本期取舍:

| HDFS 上的内容 | 本质 | 处理路径 | 本期 |
|--------------|------|---------|------|
| **原始文件类**(csv/txt/json/word/pdf/媒体) | 文件 | WebHDFS OPEN 拉取 → 按扩展名 `normalize_to_records` → `land_records` | ✅ 本期支持(需求1) |
| **数仓格式类**(parquet/orc/Hive 分区表) | 结构化表数据 | —— | ⛔ **不在本期范围**;数仓数据请走**数据库直连**(Hive/Doris 连接器,需求3),引擎读成行 |

> **为什么数仓格式文件不在本期范围**:需求1 明确 HDFS 是"拉文件"(与 S3 同构),且 `normalize_to_records` 不认 parquet/orc。需要数仓数据时,正确路径是需求3 的 **Hive/Doris 直连**——由引擎把底层 parquet 读成行返回(见 2.3.1),而不是直接拉 HDFS 上的物理 parquet 文件自己解析。两条路不重复。

**真实拉取逻辑**(`hdfs.py::run_ingest`,按扩展名分流):

```python
# 从 HDFS 路径 OPEN 取字节,按扩展名推断格式
filename = hdfs_path.rstrip("/").rsplit("/", 1)[-1]
ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "txt"

records = normalize_to_records(content, ext)   # ← 按扩展名走对应解析

ds, ver = await land_records(
    session, records,
    dataset_name=dataset_name,
    source_kind="hdfs",        # 三轴来源:HDFS
    source_format=ext,         # 格式=拉取对象的原始扩展名
    produced_by_job_id=job_id,
)
```

**落地格式**:
- **原始文件类**(本期):沿用文件接入规则。文本/文档 → jsonl;csv 等结构化 → 经 `land_records` 可走 parquet
- **数仓格式类**(parquet/orc):本期不从 HDFS 直接拉文件解析;需数仓数据走 Hive/Doris 直连(需求3)

**增量采集**:HDFS 当前 `run_ingest` 不经 LISTSTATUS 取 mtime,故仅支持 `by=name`(按路径字典序增量);配 `by=mtime` 会显式 warn 并降级为全量(不静默,Rule 12)。

#### 2.5.4 选型建议

| 你的情况 | 建议 |
|---------|------|
| 内网自建对象存储 | **MinIO**(复用 S3 代码,加 endpoint 即可) |
| 已有华为云 OBS | 继续用(你们现状) |
| HDFS 上是**原始文件**(csv/word/txt) | ✅ 现在就能接(WebHDFS 拉取 → 按扩展名解析) |
| HDFS 上是**数仓表**(parquet/orc/Hive) | ⛔ 不从 HDFS 拉文件;走 Hive/Doris 直连(需求3) |
| 没有 Hadoop 集群 | HDFS 连接器结构就绪但诚实失败,无集群时不投入真连验证 |

---

## 三、分场景实施方案

### 3.1 场景 A:纯文本数据集(PDF + Word + TXT 混合)

**输入示例**:
```
upload/
├── 2023年报.pdf
├── 产品规格.docx
├── 用户反馈.txt
└── changelog.md
```

**接入层处理**(`landing.py::land_upload`):
```python
# markitdown 提取 → 去格式 → 落地 jsonl
# 完整实现见第二章 2.1.4 节

# 处理流程:
# 1. PDF/DOCX → markitdown → markdown 字符串
# 2. markdown → HTML → plain text (去格式)
# 3. 写入 jsonl

# 产物:managed DatasetVersion, format='jsonl'
{
  "text": "第一章 公司概况 公司成立于 2010 年,主营业务包括产品 A 和产品 B...",  # 纯文本,无 markdown 标记
  "source_file": "2023年报.pdf",
  "source_page": 1,
  "doc_id": "sha256:abc123...",
  "ingest_batch": "2026-06-30T10:00:00Z"
}
```

**关键点**:
- `text` 字段已去除 markdown 格式(`#` 标题、`**` 加粗、`-` 列表等)
- 保证 data-juicer 算子统计准确(语言检测、长度、去重)
- 如需保留原始 markdown,可选双轨制(增加 `text_raw` 字段)

**治理层配置**(前端算子市场选择 → 生成 YAML):
```yaml
# 由 job_runner.py 生成并传递给 dj-process
dataset_path: /tmp/job_xyz/input.jsonl  # 从 DB 物化的 managed version
export_path: /tmp/job_xyz/output.parquet

process:
  # 1. 语言过滤(只保留中文)
  - language_id_score_filter:
      lang: zh
      min_score: 0.8
  
  # 2. 长度过滤
  - text_length_filter:
      min_len: 50      # 至少 50 字符
      max_len: 100000  # 单文档不超过 10 万字
  
  # 3. 跨文档去重(MinHash)
  - document_minhash_deduplicator:
      tokenization: character
      num_permutations: 128
      jaccard_threshold: 0.85
  
  # 4. 清洗特殊字符
  - remove_specific_chars_mapper:
      chars_to_remove: '\x00﻿'  # 删除空字节、BOM

export_shard_size: 268435456  # 256MB
keep_stats_in_res_ds: false
export_stats: true
```

**交付物**:
- `corpus-00-of-03.parquet` (3 个分片,假设总共 600MB)
- `corpus_stats.jsonl` (各文档的语言得分、去重状态)
- `process.yaml` (上面的配置)

---

### 3.2 场景 B:CSV 表格数据

**输入示例**:`用户评论.csv`(10 万行,列:`user_id,comment,rating`)

**接入层**(`landing.py::land_upload` 已支持 CSV):
```python
# 解析 CSV → jsonl,每行一条
# 问题:CSV 的列结构与纯文本不同,需统一为 text
# 解法:在接入时做列映射
{
  "text": row["comment"],  # 指定文本列
  "user_id": row["user_id"],
  "rating": row["rating"],
  "source_file": "用户评论.csv",
  "row_id": 12345
}
```

**治理层**:同场景 A,但可能需要额外算子:
```yaml
process:
  # CSV 专属:列选择(只保留需要的列)
  - columns_selector:
      retain_columns: [text, user_id, rating, source_file]
  
  # 后续跟纯文本一样:语言过滤、去重等
  - ...
```

---

### 3.3 场景 C:多模态数据集(图片 + 文本描述)

**输入示例**:
```
upload/
├── images/
│   ├── product_001.jpg
│   ├── product_002.png
│   └── ...
└── captions.jsonl   # 用户提供的索引文件
```

`captions.jsonl` 内容:
```jsonl
{"image_path": "images/product_001.jpg", "caption": "红色连衣裙"}
{"image_path": "images/product_002.png", "caption": "蓝色牛仔裤"}
```

**接入层处理**:
1. 图片上传到 **OBS**(调用 `external_store.upload_object`)
2. 改写路径为 OBS URI:
   ```jsonl
   {"images": ["obs://adp-media/prod/img_001.jpg"], "text": "红色连衣裙", "source_file": "product_001.jpg"}
   ```
3. 落地为 `format='manifest'` 的 DatasetVersion

**治理层**(`process.yaml` for 多模态):
```yaml
dataset_path: /tmp/job_xyz/input.jsonl
export_path: /tmp/job_xyz/output.parquet

# 关键:data-juicer 需要知道图片/视频/音频字段名
image_key: images
audio_key: audios
video_key: videos

process:
  # 1. 图片质量过滤
  - image_aesthetics_filter:
      min_score: 0.4  # 美学得分 >= 0.4
      hf_scorer_model: shunk031/aesthetics-predictor-v2-sac-logos-ava1-l14-linearMSE
  
  # 2. 图片分辨率过滤
  - image_aspect_ratio_filter:
      min_ratio: 0.5   # 宽高比 0.5~2.0
      max_ratio: 2.0
  
  # 3. NSFW 检测(合规)
  - image_nsfw_filter:
      hf_nsfw_model: Falconsai/nsfw_image_detection
      score_threshold: 0.5
  
  # 4. 图片去重(感知哈希)
  - image_deduplicator:
      method: phash
      hamming_distance: 4

export_shard_size: 268435456
keep_stats_in_res_ds: false
export_stats: true
```

**交付物**:
- `corpus-*.parquet` (轻量,只有元数据 + 路径)
- **OBS bucket** `obs://adp-media/prod/` (实际图片,GB~TB 级)
- 训练平台读取时:解析 `images` 列的路径 → 按需从 OBS 下载

---

### 3.4 场景 D:视频数据集

**特殊性**:视频体积大(单文件可能几 GB),需要:
1. **分段切片**(长视频 → 多个短片段)
2. **关键帧提取 + 自动打标**(生成文本描述)
3. **分布式处理**(Ray executor)

**接入层**:同场景 C,视频上传到 OBS。

**治理层**(`process.yaml` for 视频):
```yaml
executor_type: ray  # 大规模视频用分布式
ray_address: auto   # 连接现有 Ray 集群(如果有)

dataset_path: /tmp/job_xyz/input.jsonl
export_path: /tmp/job_xyz/output.parquet

video_key: videos

process:
  # 1. 从 OBS 下载到本地临时目录
  - obs_download_file_mapper:
      download_field: videos
      save_dir: /tmp/dj_videos
      endpoint_url: http://10.60.1.60:9000  # 你们的 OBS endpoint
  
  # 2. 时长过滤
  - video_duration_filter:
      min_duration: 5   # 至少 5 秒
      max_duration: 300 # 最多 5 分钟
  
  # 3. 分辨率过滤
  - video_resolution_filter:
      min_width: 480
      min_height: 360
  
  # 4. 长视频切片(10 秒一段)
  - video_split_by_duration_mapper:
      split_duration: 10
      keep_original_sample: false
      save_dir: /tmp/dj_processed_videos
  
  # 5. 自动生成文本描述(关键帧 → BLIP caption)
  - video_captioning_from_frames_mapper:
      frame_sample_strategy: uniform
      frame_num: 3  # 每段采 3 帧
      hf_img2seq: Salesforce/blip-image-captioning-base
  
  # 6. 上传处理后的视频到 OBS 新目录
  - obs_upload_file_mapper:
      upload_field: videos
      obs_bucket: adp-media
      obs_prefix: train/processed_videos/
      remove_local: true  # 上传后删本地,省空间

export_shard_size: 536870912  # 512MB
```

**关键差异**:
- 输入可能 100 个视频(500GB)→ 输出 1000 个片段(每段 10s,50GB)
- 元数据从 100 行 → 1000 行(分段后样本数增加)
- 产物 parquet 仍然轻量,媒体文件在 OBS 的新前缀下

---

### 3.5 场景 E:数据库采集(用户反馈表)

**诉求**:从业务数据库采集用户反馈,用于情感分析模型训练

**输入**:PostgreSQL 数据库,`user_feedback` 表(5000 行)

| 列名 | 类型 | 说明 |
|-----|------|------|
| id | int | 主键 |
| user_id | int | 用户ID |
| content | text | 反馈内容 |
| rating | int | 评分(1-5) |
| created_at | timestamp | 创建时间 |

**接入层处理**:

1. **前端配置数据源**:
   ```yaml
   数据源名称: 业务库-用户反馈
   类型: PostgreSQL
   连接: 
     host: 10.60.1.60
     port: 5432
     database: business_db
     username: readonly_user
     password: ******
   ```

2. **配置查询和列映射**:
   ```sql
   SELECT id, user_id, content, rating, created_at
   FROM user_feedback
   WHERE created_at >= '2026-01-01'
   ORDER BY created_at DESC
   ```
   - text列: `content`
   - 保留列: `user_id`, `rating`, `created_at`

3. **测试连接** → `probe()` 成功,`list_tables()` 列出可选表

4. **执行采集** → `pg.py::run_ingest`:构造 SELECT → records → `apply_filter_operators` → `land_records(storage_format="parquet")`

**接入产物**(落地为 **parquet**,以下用 jsonl 形式展示行内容):
```jsonl
{"id": 123, "user_id": 456, "content": "产品很好用,但希望增加暗黑模式", "rating": 4, "created_at": "2026-06-15T10:30:00Z"}
{"id": 124, "user_id": 789, "content": "客服响应太慢,等了2小时", "rating": 2, "created_at": "..."}
```
- 落地格式 **parquet**(`rating`/`created_at` 保留为 int/timestamp,不退化成字符串)
- DatasetVersion 元数据:`data_type="sql"`、`semantic_type="structured"`、`source_kind="database"`
- schema 推断失败(嵌套/异构列)→ 自动兜底回退 jsonl,采集照常成功

> **注意**:数据库接入**不做 text 列映射**(那是文本场景的处理)。表的所有列原样保留为 parquet 列;哪列当训练文本由下游治理层 / 训练侧按列名取用。

**治理层配置**(以 `content` 列为文本目标):
```yaml
text_keys: content   # 指定哪列作为文本字段供算子处理
process:
  # 1. 语言过滤
  - language_id_score_filter:
      lang: zh
      min_score: 0.8
  
  # 2. 长度过滤(过滤过短的反馈)
  - text_length_filter:
      min_len: 10
      max_len: 500
  
  # 3. 去重(相同内容只保留一次)
  - document_minhash_deduplicator:
      threshold: 0.9
```

**交付**:
- `user_feedback_clean.parquet`(4200 条,过滤 800 条短/重复内容)
- 列类型保留:按 `rating`(int)分析情感分布、按 `created_at`(timestamp)做时间序列

---

### 3.6 场景 F:API 推送(实时告警数据)

**诉求**:接收监控系统实时推送的告警,用于异常检测模型训练

**输入**:外部监控系统,每分钟推送 10-100 条告警

**接入层处理**:

1. **前端生成 Webhook**(token 在路径,绑定 `type='api'` 的 DataSource):
   ```
   URL: https://adp.example.com/api/v1/ingest/push/dj_push_xyz456
   ```

2. **外部系统调用**(Python 示例):
   ```python
   import requests

   webhook_url = "https://adp.example.com/api/v1/ingest/push/dj_push_xyz456"

   # 监控系统产生告警时调用;records 为推送记录数组
   payload = {
       "records": [
           {
               "content": "[严重] API响应时间超过5秒,服务:user-service,节点:prod-01",
               "severity": "critical",
               "service": "user-service",
               "node": "prod-01",
               "metric_value": 5.2,
           }
       ],
       "semanticType": "text",          # 可选,显式指定语义类型
       "idempotencyKey": "alert-20260630-1015",  # 可选,TTL 内去重
   }

   response = requests.post(webhook_url, json=payload)
   print(response.json())  # 返回新 DatasetVersion id(同步落地)
   ```

3. **后端处理**(`land_push_records`,同步):
   - 按 token 查 DataSource → 校验
   - 幂等键检查(内存 TTL 10min,同 key 返回首版)
   - 语义归一 → 归并到 `boundDatasetId` 数据集,产出新 DatasetVersion

**产物**(每次推送累积为新版本,落地 jsonl):
```jsonl
{"content": "[严重] API响应时间超过5秒,服务:user-service,节点:prod-01", "severity": "critical", "service": "user-service", "node": "prod-01", "metric_value": 5.2}
{"content": "[警告] 磁盘使用率85%,服务:db-master,节点:prod-02", "severity": "warning", ...}
```

**治理层配置**:
```yaml
text_keys: content   # 指定文本字段
process:
  # 1. 按严重程度过滤(只保留 critical 和 warning)
  - python_lambda_filter:
      lambda_fn: "lambda row: row.get('severity') in ['critical', 'warning']"
  
  # 2. 去重(相同告警只保留一次)
  - document_deduplicator:
      method: "simhash"
      threshold: 2
```

**交付**:
- 治理后导出 `alerts_clean.parquet`(治理层 dj-process 阶段产出)
- 可按 `severity` / `service` 分类分析

**前端显示**(可做):
- 推送累积版本数、最后推送时间
- token 校验失败计数

---

## 四、项目集成实施

### 4.1 后端改造(已有基础,需扩展)

#### 4.1.1 接入层增强(`app/services/landing.py`)

**当前状态**:
- ✅ 支持 CSV/XLSX/JSONL/TXT
- ✅ 文档提取(markitdown)
- ✅ 二进制零拷贝(图/音/视频原样归档)

**需补充**:

##### 1. PDF 类型识别 + OCR(已在 2.1 节给出完整代码)

```python
# 核心函数(完整实现见 2.1.2 / 2.1.4 节)
def _detect_pdf_type(text: str, page_count: int) -> bool:
    """判断 PDF 是否为扫描型。"""
    ...

def _ocr_pdf_unlimited(file_path: str) -> str:
    """Unlimited-OCR 识别扫描型 PDF(One-shot 长文档解析)。"""
    ...

# 接入时自动分流
if file_ext == "pdf":
    md_text = markitdown_extract(file)
    if _detect_pdf_type(md_text, page_count):
        text = _ocr_pdf_unlimited(file)  # 扫描型 → Unlimited-OCR
    else:
        text = _markdown_to_plain_text(md_text)  # 文本型 → 去格式
```

**依赖安装**:
```bash
# Unlimited-OCR(扫描型 PDF 唯一 OCR 方案)
git clone https://github.com/baidu/Unlimited-OCR.git
pip install -r Unlimited-OCR/requirements.txt
pip install pymupdf  # PDF 页数/页转图
```

##### 2. Markdown 去格式(已在 2.2.4 节给出完整代码)

```python
def _markdown_to_plain_text(md: str) -> str:
    """Markdown → plain text,删除格式标记但保留内容。"""
    import re
    import markdown
    from bs4 import BeautifulSoup
    
    html = markdown.markdown(md, extensions=['extra', 'nl2br'])
    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup.find_all(['li', 'p', 'h1', 'h2', 'h3']):
        tag.append('\n')
    plain = soup.get_text(separator=' ')
    plain = re.sub(r' +', ' ', plain)
    plain = re.sub(r'\n{3,}', '\n\n', plain)
    return plain.strip()

# 在文档接入时调用
async def land_upload(...):
    if file_ext in DOC_FORMATS:  # pdf/docx/pptx
        md_content = _get_markitdown().convert(file_obj).text_content
        plain_text = _markdown_to_plain_text(md_content)  # ✅ 去格式
        record = {"text": plain_text, "source_file": filename, ...}
```

**依赖安装**:
```bash
# backend/.venv
pip install markdown beautifulsoup4
```

##### 3. 数据库接入(已在 2.3.3 节给出完整代码)

```python
# backend/app/services/connectors/{pg,mysql,proprietary}.py
# 各连接器镜像统一结构:probe / list_tables / run_ingest

class PgConnector:  # mysql.py / proprietary.py 同构
    async def probe(self) -> tuple[bool, int, str]: ...     # 测连接,不可达诚实失败
    async def list_tables(self) -> list[str]: ...           # 列用户表
    async def run_ingest(self, task, ...) -> list[tuple]:   # 采集落地
        queries = _build_queries(task.extract)              # 按配置构造 SELECT
        # 执行 → records → 可选 apply_filter_operators →
        ds, ver = await land_records(
            session, records,
            data_type="sql", semantic_type="structured",
            source_kind="database",
            storage_format="parquet",   # ★ 结构化数据落地为 parquet
        )
```

**依赖安装**:
```bash
pip install asyncpg          # PostgreSQL 族(pg.py)
pip install "asyncmy>=0.2.9" # MySQL/goldendb(mysql.py,懒 import,未装则诚实失败)
```

> **落地是 parquet 不是 jsonl**:数据库是结构化源,`storage_format="parquet"` 保留列类型;schema 推断失败兜底回退 jsonl(见 2.3.3)。

##### 4. API 推送接入(已实现,见 2.4 节)

```python
# backend/app/services/connectors/push.py
# PushConnector 注册在 REGISTRY[("api", None)];入站经端点同步落地,无 Redis 队列

async def land_push_records(
    session, datasource, records, *,
    semantic_type=None,        # 入参 > config.semanticType > data_type 推断
    idempotency_key=None,      # 内存 TTL(10min)去重
):
    """POST /api/v1/ingest/push/{token} 端点调用的核心落地:
    幂等检查 → 语义归一 → 归并到 boundDatasetId 数据集 → 产出新 DatasetVersion。
    失败抛 LandingError + 回滚(不伪成功)。"""
    ...
```

> **无额外依赖**:同步落地,不需要 Redis / supervisord 消费者。幂等为内存版,生产化需 DB 持久去重(已知后续增强点)。

##### 5. 存储类接入(S3 兼容 + HDFS,已在 2.5 节给出完整代码)

```python
# backend/app/services/external_store.py — S3 兼容统一连接器
def get_s3_client(preset: str, access_key: str, secret_key: str):
    """OBS/MinIO/AWS S3 通用,唯一区别是 endpoint_url。"""
    cfg = S3_PRESETS[preset]  # obs / minio / aws_s3
    return boto3.client("s3", endpoint_url=cfg["endpoint_url"], ...)

# backend/app/services/connectors/hdfs.py — HDFS 独立连接器(WebHDFS REST)
class HdfsConnector:
    async def probe(self) -> tuple[bool, int, str]: ...  # 无 NameNode 诚实失败
    async def run_ingest(self, task, ...):
        # WebHDFS OPEN 取字节 → 按扩展名 normalize_to_records → land_records
        ...
```

**依赖安装**:
```bash
# S3 兼容(OBS/MinIO/S3 通用,通常已装)
pip install boto3 s3fs

# HDFS:WebHDFS 走 httpx(已装),无需本地 Hadoop 客户端/驱动
# 本期只拉原始文件;数仓数据走 Hive/Doris 直连(需求3),不在 HDFS 拉文件路径里
```

> **MinIO 不需要单独连接器**:复用 S3 代码,配置 `endpoint_url` 即可。HDFS 连接器(`connectors/hdfs.py`)已实现 WebHDFS 拉取,**本期接原始文件;数仓数据走 Hive/Doris 直连**(见 2.5.3 / 2.3.1)。

##### 6. 批量媒体接入(生成 manifest jsonl)

```python
async def land_media_batch(
    files: list[UploadFile],
    captions: dict[str, str] | None,  # 文件名 → 描述映射
    obs_bucket: str,
    db: AsyncSession,
    user_id: int,
    dataset_id: int,
) -> DatasetVersion:
    """批量上传图/音/视频,生成 manifest jsonl。"""
    manifest_rows = []
    for file in files:
        # 上传到 OBS
        obs_key = f"media/{secrets.token_urlsafe(16)}{Path(file.filename).suffix}"
        await upload_object(obs_bucket, obs_key, await file.read())
        
        # 生成索引行
        media_field = _DATA_TYPE_TO_MEDIA_FIELD[media_kind(file.filename)]
        manifest_rows.append({
            media_field: [f"obs://{obs_bucket}/{obs_key}"],
            "text": captions.get(file.filename, ""),
            "source_file": file.filename,
            "doc_id": f"sha256:{hashlib.sha256(file.filename.encode()).hexdigest()}"
        })
    
    # 写 manifest jsonl → 落地为 DatasetVersion
    manifest_content = "\n".join(json.dumps(r, ensure_ascii=False) for r in manifest_rows)
    version = await land_records(manifest_content.encode(), "manifest", db, user_id, dataset_id)
    return version
```

##### 7. CSV 列映射(指定 text 列)

```python
async def land_csv_with_mapping(
    content: bytes,
    text_column: str,  # 用户指定哪列当 text
    retain_columns: list[str] | None,
    ...
) -> DatasetVersion:
    """CSV 接入时做列映射,统一为 {text, ...} schema。"""
    import csv
    import io
    
    reader = csv.DictReader(io.StringIO(content.decode('utf-8')))
    rows = []
    for row in reader:
        if text_column not in row:
            raise ValueError(f"CSV 缺少指定的 text 列:{text_column}")
        
        # 映射:指定列 → text
        record = {"text": row[text_column]}
        
        # 保留其他列(如果指定)
        if retain_columns:
            for col in retain_columns:
                if col in row and col != text_column:
                    record[col] = row[col]
        
        # 注入元数据
        record.update({
            "source_file": "...",
            "row_id": reader.line_num,
        })
        rows.append(record)
    
    # 写 jsonl → 落地
    jsonl_content = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
    version = await land_records(jsonl_content.encode(), "jsonl", db, user_id, dataset_id)
    return version
```

#### 4.1.2 治理层编排(`app/services/job_runner.py`)

**当前状态**:
- ✅ 从 DB 读 DatasetVersion → 物化为本地 jsonl
- ✅ 生成 `process.yaml` 并调用 `dj-process`
- ✅ 输出产物 → 新建 DatasetVersion

**需补充**:
```python
# 1. 多模态物化时,处理 OBS 路径
async def materialize_version_with_media(
    version: DatasetVersion,
    work_dir: Path,
) -> Path:
    """物化 manifest 数据集,路径保持为 OBS URI(不下载实际文件)。
    
    dj-process 的多模态算子会在运行时懒加载媒体(或用 download_mapper)。
    """
    if version.format != MANIFEST_FORMAT:
        return await materialize_version(version, work_dir)  # 原逻辑
    
    # manifest 格式:直接把 jsonl 写到工作目录,路径列不动
    input_path = work_dir / "input.jsonl"
    async with aiofiles.open(version.object_key, "r") as f:
        content = await f.read()
    async with aiofiles.open(input_path, "w") as f:
        await f.write(content)
    return input_path

# 2. 生成 YAML 时注入 image/audio/video_key
def build_process_yaml(operators: list[OperatorSpec], version: DatasetVersion, ...) -> dict:
    config = {
        "dataset_path": str(input_path),
        "export_path": str(output_path),
        "process": [op.to_dj_config() for op in operators],
        ...
    }
    
    # 多模态数据集需指定字段名
    if version.format == MANIFEST_FORMAT:
        config.update({
            "image_key": "images",
            "audio_key": "audios",
            "video_key": "videos",
        })
    
    return config
```

#### 4.1.3 对象存储抽象(`app/services/external_store.py`)

**当前状态**:
- ✅ S3 支持(AWS / 华为云 OBS)
- ✅ 上传/下载/列举/删除

**需补充**:
```python
# OBS 批量操作(data-juicer 算子会用)
async def obs_download_batch(
    bucket: str,
    keys: list[str],
    local_dir: Path,
) -> dict[str, Path]:
    """批量下载媒体文件,返回 OBS key → 本地路径映射。"""
    ...

async def obs_upload_batch(
    bucket: str,
    local_files: list[Path],
    prefix: str,
) -> dict[Path, str]:
    """批量上传,返回本地路径 → OBS key 映射。"""
    ...
```

---

### 4.2 前端改造

#### 4.2.1 数据集上传页增强

**新增功能**:
1. **媒体批量上传** + 拖拽描述配对
   ```typescript
   interface MediaUploadItem {
     file: File;
     caption: string;  // 用户手填或自动生成(OCR/ASR)
     preview: string;  // 缩略图
   }
   ```

2. **CSV 列映射界面**(上传 CSV 时弹出)
   ```typescript
   interface CsvColumnMapping {
     textColumn: string;      // 必选,哪列当 text
     retainColumns: string[]; // 可选,保留哪些额外列
   }
   ```

3. **接入预览**(上传后显示前 10 条样本,确认无误后 landing)

#### 4.2.2 算子市场筛选

**当前**(from `frontend/src/pages/Market`):
- 按分类(Filter/Mapper/Deduplicator)筛选
- 搜索算子名称

**需新增**:
- **按适用数据类型筛选**:纯文本 / 多模态-图片 / 多模态-视频
- 标签示例:
  ```typescript
  enum DatasetType {
    TEXT = 'text',           // 纯文本算子(所有数据集可用)
    IMAGE = 'image',         // 图片算子(需 manifest + images 字段)
    VIDEO = 'video',
    AUDIO = 'audio',
  }
  ```

#### 4.2.3 任务配置页

**流程**:
1. 选择输入数据集(显示格式标签:`jsonl` / `manifest-image` / `manifest-video`)
2. 从市场拖拽算子 → 可视化流水线(DAG)
3. 每个算子展开参数配置(表单)
4. 提交 → 后端生成 `process.yaml` → 执行 Job

**关键点**:
- 根据输入数据集类型**自动过滤**不兼容算子(例如纯文本集不显示 `image_*_filter`)
- 算子参数校验(例如 `perplexity_filter` 需要 LLM 配置,无配置时提示)

---

### 4.3 数据库 Schema 扩展

#### 4.3.1 DatasetVersion 表

**新增列**:
```sql
ALTER TABLE dataset_version ADD COLUMN media_stats JSONB;
-- 存储媒体统计:{"image_count": 120, "total_size_mb": 450, "avg_resolution": "1920x1080"}

ALTER TABLE dataset_version ADD COLUMN semantic_schema JSONB;
-- 存储 schema 信息(列名 + 语义类型),便于前端展示和算子适配
-- 例:{"text": "text", "images": "image_path_list", "user_id": "identifier"}
```

#### 4.3.2 Job 表

**新增列**:
```sql
ALTER TABLE job ADD COLUMN executor_type VARCHAR(20) DEFAULT 'default';
-- 'default' | 'ray',支持分布式执行(视频/大规模数据集)

ALTER TABLE job ADD COLUMN resource_usage JSONB;
-- 记录资源消耗:{"peak_memory_mb": 2048, "gpu_hours": 0.5, "input_size_mb": 1024, "output_size_mb": 856}
```

---

## 五、典型用户故事

### 故事 1:法律文书数据集(纯文本)

**角色**:数据工程师小李

**诉求**:整理 5000 份判决书 PDF,清洗后用于法律 AI 训练

**操作流程**:
1. **上传**:拖拽 5000 个 PDF 到平台 → 自动提取文本(markitdown)→ 落地为 `judgments_raw` 数据集
2. **配置治理流水线**:
   - `language_id_score_filter`(只保留中文)
   - `text_length_filter`(过滤少于 500 字的)
   - `document_minhash_deduplicator`(去除重复判决)
   - `remove_specific_chars_mapper`(清洗特殊字符)
3. **执行** → 等待 10 分钟(5000 文档 × 平均 5 页 ≈ 25000 样本)
4. **查看结果**:
   - 原始 25000 样本 → 过滤后 18500 样本(去重 6000,过短 500)
   - 下载 `judgments_clean.parquet` + `_stats.jsonl`
5. **交付**:上传到训练平台的 S3 bucket

---

### 故事 2:电商商品图数据集(多模态)

**角色**:算法工程师小王

**诉求**:10 万张商品图 + 人工标注的描述,清洗后训练图文检索模型

**操作流程**:
1. **上传**:
   - 批量上传 10 万张图片(ZIP 包,自动解压)
   - 上传 `captions.csv`(两列:`image_name, caption`)
   - 平台自动生成 manifest jsonl + 上传图片到 OBS
2. **配置流水线**:
   - `image_aesthetics_filter`(美学得分 >= 0.4)
   - `image_aspect_ratio_filter`(宽高比 0.8~1.5,接近正方形)
   - `image_nsfw_filter`(过滤不合规图片)
   - `image_deduplicator`(去除重复上传)
3. **执行**(GPU 机,10 分钟)
4. **结果**:
   - 10 万张 → 8.5 万张(过滤 1.2 万低质量,去重 3000)
   - 产物:`products.parquet`(轻量,5MB)+ OBS 媒体目录(12GB)
5. **训练**:PyTorch DataLoader 读 parquet → 按需从 OBS 懒加载图片

---

### 故事 3:视频采访数据集(多模态 + 分布式)

**角色**:研究员小张

**诉求**:500 段采访视频(每段 10~30 分钟),提取 10 秒片段并自动生成描述

**操作流程**:
1. **上传**:500 个 MP4(共 300GB)→ 上传到 OBS,生成 manifest
2. **配置流水线**:
   - `video_duration_filter`(只保留 5~30 分钟的)
   - `video_split_by_duration_mapper`(切成 10 秒片段)
   - `video_captioning_from_frames_mapper`(关键帧 → 生成描述)
   - `obs_upload_file_mapper`(处理后的片段上传回 OBS)
3. **执行**:
   - 环境:Ray 集群(4 台 GPU 机,每台 2 卡)
   - 耗时:3 小时
4. **结果**:
   - 500 段视频 → 45000 个 10 秒片段
   - 产物:parquet(200MB,含自动生成的文本描述)+ OBS 处理后视频(50GB,压缩+切片后)
5. **训练**:视频理解模型,按片段 ID 懒加载

---

## 六、关键技术决策

### 6.1 为什么统一转 jsonl 而不是保留原格式?

| 方案 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| **保留原格式**(PDF/CSV 混存)| 不丢信息 | data-juicer 单目录只能命中一种 formatter,混不了 | ❌ 不可行 |
| **统一 jsonl** | data-juicer 天然支持,schema 对齐 | 需预处理 | ✅ 采纳 |

### 6.2 媒体文件为什么不嵌入数据集?

| 方案 | 存储 | 加载 | 分发 | 结论 |
|---|---|---|---|---|
| **Base64 嵌入** | jsonl 体积爆炸(GB → TB)| 必须全量读 | 网络传输慢 | ❌ |
| **路径引用 + OBS** | jsonl 轻量(MB 级)| 懒加载,按需取 | CDN 加速 | ✅ |

### 6.3 为什么要分三层(接入/治理/交付)?

**解耦理由**:
- **接入层**:对接多种数据源(上传/S3/DB/API),产出统一格式
- **治理层**:纯粹的算子流水线,不关心数据从哪来
- **交付层**:适配不同训练框架(PyTorch/TF/Spark)

好处:中间任一层改动,不影响其他层。

### 6.4 为什么用百度 Unlimited-OCR

扫描型 PDF 统一用 Unlimited-OCR,理由:

| 维度 | Unlimited-OCR |
|-----|--------------|
| **长文档处理** | One-shot 一次性解析整个文档,不必逐页循环拼接 |
| **复杂排版** | 支持表格/多栏/公式,中文准确率高 |
| **成本** | 开源免费,数据不出本地 |
| **资源** | 模型 ~1GB,GPU 模式更快(CPU 也可跑) |

> 本期 OCR 只用 Unlimited-OCR 单一方案,不引入 PaddleOCR / 云 API,保持依赖与运维简单。

---

## 七、下一步行动

### Phase 1:基础能力补齐(1 周)

- [ ] 后端:`landing.py` 增加 **PDF 类型自动识别**(`_detect_pdf_type`)
- [ ] 后端:集成 **Unlimited-OCR**(扫描型 PDF 处理)
- [ ] 后端:`landing.py` 增加 `land_media_batch`(媒体批量接入)
- [ ] 后端:`landing.py` 增加 `land_csv_with_mapping`(CSV 列映射)
- [ ] 后端:`job_runner.py` 支持 manifest 数据集物化
- [ ] 前端:媒体批量上传组件(拖拽 + 描述配对)
- [ ] 前端:CSV 列映射界面
- [ ] 前端:PDF 上传后显示类型标签(文本型/扫描型)

### Phase 2:算子市场增强(3 天)

- [ ] 算子元数据增加 `applicable_types: ["text", "image", ...]`
- [ ] 前端:按数据类型筛选算子
- [ ] 前端:任务配置页根据输入数据集类型自动过滤算子

### Phase 3:多模态 Pipeline 验证(1 周)

- [ ] 端到端跑通「图片批量上传 → 美学过滤 + 去重 → 导出 parquet」
- [ ] 端到端跑通「视频上传 → 切片 + 自动打标 → Ray 分布式执行」
- [ ] 性能测试:10 万图片数据集治理耗时 / 资源消耗

### Phase 4:数据库接入(连接器已实现,补外围)

- [x] 后端:`connectors/{pg,mysql,proprietary}.py` 连接器族(probe/list_tables/run_ingest)
- [x] 后端:`land_records(storage_format="parquet")` 结构化落地 + 兜底回退 jsonl
- [ ] 前端:数据源配置页(连接信息、表/查询选择、增量配置)
- [ ] 前端:测试连接按钮(调 `probe`)+ 表列表(调 `list_tables`)
- [ ] 文档:《数据库接入指南》(支持的库、增量采集、parquet 落地)

### Phase 5:API 推送接入(连接器已实现,补外围)

- [x] 后端:`connectors/push.py::PushConnector` + `land_push_records`(同步落地)
- [x] 后端:按 token 查 DataSource + boundDatasetId 版本归并 + 内存幂等
- [ ] 后端:幂等去重升级为 DB 持久版(内存版生产化增强点)
- [ ] 前端:生成 Webhook URL + token(带复制按钮)
- [ ] 前端:推送累积版本数 / 最后推送时间显示
- [ ] 文档:《API 推送集成指南》(Webhook 用法、records 格式、幂等键)

### Phase 5.5:存储类接入(S3 兼容 + HDFS)

- [ ] 后端:`external_store.py` 增加 `S3_PRESETS`(obs/minio/aws_s3 预设)
- [ ] 后端:统一 `get_s3_client`(只换 endpoint)
- [ ] 前端:数据源配置页增加存储类型选择(OBS/MinIO/S3)
- [ ] 验证:内网 MinIO 读写跑通(复用现有 OBS 代码)
- [x] 后端:`connectors/hdfs.py`(WebHDFS,原始文件类已支持)
- [~] HDFS 数仓格式(parquet/orc):本期范围外,数仓数据走 Hive/Doris 直连(需求3)
- [ ] 验证:真实 HDFS 集群端到端拉取(承诺级,需集群)

### Phase 6:训练平台对接(1 周)

- [ ] 编写训练平台数据加载器(PyTorch DataLoader from parquet + OBS)
- [ ] 文档:《数据集交付规范》(parquet schema / OBS 路径约定)
- [ ] 示例:CLIP 训练脚本,直接消费平台产出的图文数据集

---

## 八、FAQ

### Q1:PDF + CSV 能在一个数据集里吗?

**A**:可以,但要**预处理统一为 jsonl**。两者都转成 `{text, source_file, ...}` schema,然后合并。CSV 需指定哪列当 `text`。

### Q2:markitdown 提取的 markdown 格式怎么处理?

**A**:看训练场景,**推荐去格式**(当前项目采用):

| 场景 | 处理方式 | 理由 |
|-----|---------|------|
| **LLM 训练**(如 GPT) | markdown → plain text(去格式) | ✅ 推荐:算子统计准确,通用 |
| 代码/文档模型 | 保留 markdown | 需要学习格式标记 |
| RAG 检索 | 按章节拆分 | 细粒度检索 |

**去格式实现**:接入层用 `markdown` + `BeautifulSoup` 转纯文本(代码见 2.2.4 节)。

**为什么要去格式?**
- markdown 包含格式符号(`#` 标题、`**` 加粗、`[]()`链接)
- 直接喂给 data-juicer 会干扰语言检测、长度统计、去重
- 去格式后:`"# 标题\n\n**内容**"` → `"标题 内容"`(纯文本)

### Q3:如何判断 PDF 是文本型还是扫描型?

**A**:两种方法:

**方法 1:手动判断**(上传前)
- 用 PDF 阅读器打开,试着选中/复制文字
- 能复制 → 文本型,markitdown 可处理
- 不能复制 → 扫描型,需要 OCR

**方法 2:自动判断**(系统实现)
```python
# 尝试提取,如果每页平均少于 50 字符 → 扫描型
def _detect_pdf_type(text, page_count):
    avg_chars = len(text.strip()) / page_count
    return avg_chars < 50  # True = 扫描型
```

**系统会自动处理**:文本型 → 直接提取,扫描型 → OCR 识别

### Q4:扫描型 PDF 用什么 OCR 工具?

**A**:统一用 **百度 Unlimited-OCR**(开源,本期唯一 OCR 方案):

| 工具 | 优势 | 注意 | 成本 |
|-----|------|------|------|
| **Unlimited-OCR** | • One-shot 一次性长文档解析<br>• 复杂排版(表格/多栏)准确率高<br>• 中文好、数据不出本地 | GPU 模式更快(CPU 慢 5-10 倍) | 免费 |

**安装**:
```bash
git clone https://github.com/baidu/Unlimited-OCR.git
pip install -r Unlimited-OCR/requirements.txt
```

**性能**:GPU 模式下,10 页 PDF 约 30-60 秒。

### Q5:OCR 准确率多少?会有错误吗?

**A**:Unlimited-OCR 实测大致:
- **中文印刷体**:~92%
- **手写体**:60-80%(质量差的更低)
- **复杂排版**(表格/公式):~85%

**错误类型**:
- 相似字混淆(如:己/已、未/末)
- 多栏排版顺序错乱
- 数学公式识别不全

**建议**:
- 训练前用 data-juicer 的 `text_length_filter` / `alphanumeric_filter` 过滤明显错误的样本
- 对准确率要求高的场景(如法律文书),OCR 后人工抽检

### Q6:视频数据集会不会特别慢?

**A**:看规模。小数据集(< 100 视频)单机够用;大规模(1000+ 视频)建议:
1. 用 Ray 分布式(4 台机器并行,线性提速)
2. 视频算子选择性启用(例如只做时长过滤,不做 caption 生成)

### Q7:多模态数据集的存储成本?

**A**:
- **元数据**(parquet):可忽略,10 万样本 < 50MB
- **媒体文件**(OBS):主要成本,10 万图片 ≈ 50GB,1000 视频 ≈ 500GB
- **优化**:
  - 压缩(JPEG quality 85,视频 H.265)
  - 冷热分离(训练完的数据集降到归档存储)
  - CDN 缓存(高频访问的数据集)

### Q8:如何保证血缘溯源?

**A**:接入时强制注入字段:
```json
{
  "text": "...",
  "source_file": "2023年报.pdf",      // 原始文件名
  "doc_id": "sha256:abc123...",      // 内容哈希
  "ingest_batch": "2026-06-30T10:00:00Z",  // 采集批次
  "source_page": 5                   // 可选,来自第几页
}
```

这些字段在治理流水线中**不会被删除**,最终进入交付产物。

### Q9:能不能一键复现?

**A**:可以。每个 Job 记录:
- 输入数据集版本 ID(不可变)
- `process.yaml`(算子配置)
- data-juicer 版本(镜像 tag)
- 执行时间戳

给定这些信息,重跑 Job → 产出完全一致(确定性算子)。非确定性算子(如 LLM caption)需固定 seed。

### Q10:数据库和文件源能在一个数据集里吗?

**A**:可以!所有源最终都转 jsonl,schema 对齐后合并。

**示例场景**:
- 文件源:用户手册 PDF(产品说明)
- 数据库源:user_feedback 表(用户反馈)
- 合并后:统一训练客服问答模型

**关键**:两者都转成 `{text, ...}` schema,用 `source_type` 字段区分来源。

### Q11:数据库账号密码如何安全存储?

**A**:三层防护:
1. **加密存储**:用 `cryptography.fernet` 加密后存 PostgreSQL,密钥存环境变量 `ADP_DB_SECRET_KEY`
2. **最小权限**:只配置只读账号,禁止 DELETE/UPDATE
3. **审计日志**:记录每次数据库访问(用户、时间、查询语句)

**代码示例**:
```python
from cryptography.fernet import Fernet
import os

cipher = Fernet(os.environ['ADP_DB_SECRET_KEY'].encode())
encrypted_pwd = cipher.encrypt(password.encode())  # 存DB
password = cipher.decrypt(encrypted_pwd).decode()  # 读取时解密
```

### Q12:API 推送如何防止恶意攻击?

**A**:三层防护:
1. **Token 校验**:每个推送数据源独立 token(随机生成、可重置),token 在 URL 路径
2. **IP 白名单**(可选):只允许可信 IP 推送
3. **速率限制**(可选,规划中):每 token 每分钟限流,超过返回 429
4. **幂等键**:同 `idempotencyKey` 在 TTL 内去重,削弱重放攻击影响

> 注:当前为同步落地(无队列),限流如需引入可在端点前置一层计数器。

### Q13:数据库采集会拖垮生产库吗?

**A**:五个最佳实践避免影响:
1. **只读账号**:GRANT SELECT ONLY
2. **从库/数仓采集**:不直接查主库
3. **分批查询**:LIMIT 1000 + 游标,避免大查询
4. **低峰时段**:定时采集设在凌晨 2-4 点
5. **连接池**:限制最大连接数(max_overflow=5)

**监控指标**:
- 采集耗时(超过 5 分钟告警)
- 数据库 CPU/内存(超过 70% 暂停采集)

### Q14:API 推送会丢数据吗?如何保证可靠?

**A**:当前是**同步落地**(无队列),可靠性靠"即时反馈 + 幂等":
1. **同步返回**:`land_push_records` 落库成功才返回新版本 id;失败抛 `LandingError` + Session 回滚,推送方立即收到错误(不伪成功)
2. **幂等键**:推送带 `idempotencyKey`,同 key 在 TTL(10min)内重复推送返回首版 id,不重复落地 —— **推送方失败重试是安全的**
3. **推送方负责重试**:落地失败时由推送方按错误响应重试(配合幂等键避免重复)

**已知局限**:
- 幂等是**内存版**(进程重启即失效),生产化需 DB 持久去重
- 无队列削峰,高并发瞬时推送可能压力集中在落库 —— 若未来推送量大再引入队列(本期不做)

### Q15:MinIO 需要单独写连接器吗?

**A**:**不需要**。MinIO 是 S3 兼容存储,和华为云 OBS、AWS S3 用同一套 `boto3`/`s3fs` 代码,**唯一区别是 `endpoint_url`**:

```python
# OBS
endpoint_url = "https://obs.cn-north-4.myhuaweicloud.com"
# MinIO(内网)
endpoint_url = "http://10.60.1.x:9000"
```

现有 `external_store.py` 的 S3 代码加个 endpoint 预设即可连 MinIO,data-juicer 的 YAML 也只需配 `endpoint_url`。

### Q16:HDFS 是什么?要支持吗?

**A**:HDFS 是 Hadoop 分布式文件系统,大数据生态(Spark/Hive 数仓)的底层存储,把大文件切块分散存多机、每块 3 副本。

**和 S3 的区别**:协议完全不同(WebHDFS REST vs S3 API),不能复用 S3 代码,需独立连接器(已实现为 `connectors/hdfs.py`,走 WebHDFS,**无需本地 Hadoop 客户端**)。

**关键:HDFS 上两类内容,本期只接原始文件**:
| HDFS 上的内容 | 处理 | 本期 |
|--------------|------|------|
| 原始文件(csv/txt/word/pdf) | WebHDFS 拉取 → 按扩展名 `normalize_to_records` | ✅ 本期支持(需求1) |
| 数仓格式(parquet/orc/Hive 表) | 走 Hive/Doris 直连(需求3),引擎读成行 | ⛔ 不从 HDFS 拉文件 |

"文件放在哪(HDFS)"和"文件是什么格式"是正交的——HDFS 上既可能是原始 word/csv,也可能是数仓 parquet 表。本期 HDFS 只拉原始文件;要用数仓数据,走需求3 的 Hive/Doris 直连(引擎替你把底层 parquet 读成行),两条路不重复。

### Q17:多种存储源能混在一个数据集吗?

**A**:可以。S3 兼容(OBS/MinIO)、HDFS 拉取的内容,最终都经 `land_records` 落地,用 `source_kind` 区分来源(`obs`/`minio`/`hdfs`/`database`)。注意落地格式按数据性质走:文本/文档 → jsonl,结构化(数据库/数仓表)→ parquet,schema 对齐后可合并。

### Q18:为什么数据库落地是 parquet 而不是 jsonl?

**A**:数据库是**结构化源**,连接器调 `land_records(storage_format="parquet")`:
- **保留列类型**:int/float/timestamp 不退化成字符串(jsonl 全是文本)
- **列式压缩**:表数据体积小,下游列裁剪/谓词下推高效
- **天然契合**:关系表是二维结构,parquet 就是为此设计

**兜底**:parquet schema 推断失败(空记录/嵌套字段/列类型异构)时,`land_records` 自动回退 `records_to_jsonl_bytes` 写 jsonl,`effective_format="jsonl"`,采集不因格式问题失败(D2 红线)。

**对比**:文本/文档源(PDF/Word/上传 txt)走 jsonl,媒体源走 manifest jsonl——落地格式按数据性质定,不是一刀切。

---

**文档维护**:本文档随项目演进持续更新,当前版本对应 `ai-data-platform@dev` 分支。




