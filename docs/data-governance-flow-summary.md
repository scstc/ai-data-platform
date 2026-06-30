# 多格式数据治理流程 — 快速参考

> 完整文档见 [data-governance-flow.md](./data-governance-flow.md)

## 一分钟速览

```
异构文件(PDF/CSV/图片/视频) 
    ↓ 
【接入层】landing.py → 统一 jsonl + OBS 归档
    ↓
【治理层】data-juicer 流水线 → 过滤/去重/合规/增强/选择(五类算子)
    ↓
【交付层】构造训练 schema → parquet/jsonl + OBS 媒体 → 训练平台
```

---

## 文件格式处理速查表

| 格式类型 | 具体格式 | 处理方式 | 最终产物 | 说明 |
|---------|---------|---------|---------|------|
| **纯文本** | txt, log, md | 逐行读取 | `{text}` | 直接加载 |
| **文档(文本型)** | pdf(可复制), docx | markitdown → **去格式(plain text)** | `{text, source_file}` | 可选中文字的 PDF |
| **文档(扫描型)** | pdf(不可复制), 图片 | **OCR(Unlimited-OCR)** → 去格式 | `{text, pdf_type: "scanned"}` | 系统自动识别并 OCR |
| **表格** | csv, xlsx | 列映射 | `{text, col1, col2, ...}` | 指定哪列当 text |
| **图片** | jpg, png, gif | OBS 归档 + 路径索引 | `{text?, images: [obs://...]}` | 媒体不内嵌,只存路径 |
| **音频** | mp3, wav, flac | OBS 归档 + 路径索引 | `{text?, audios: [obs://...]}` | 同图片 |
| **视频** | mp4, avi, mov | OBS 归档 + 路径索引 | `{text?, videos: [obs://...]}` | 可切片/自动打标 |
| **数据库** | MySQL(goldendb)、PostgreSQL、专有库 | SELECT → records → land_records | **parquet**(失败兜底 jsonl) | 批量/增量,保留列类型 |
| **API推送** | 外部系统 Webhook | POST → land_push_records 同步落地 | jsonl(归并新版本) | 实时,幂等去重 |
| **S3兼容存储** | OBS, MinIO, AWS S3, OSS | s3fs(只换 endpoint) | 读取/归档媒体 | **统一连接器** |
| **HDFS** | Hadoop 文件系统(WebHDFS) | httpx 调 WebHDFS,无需本地 Hadoop | 原始文件✅本期 / 数仓格式走Hive直连 | 独立连接器(connectors/hdfs.py) |

---

## 核心约束与解法

| 约束 | 原因 | 解决方案 |
|-----|------|---------|
| PDF + CSV 不能混在一个目录 | data-juicer formatter 投票制,只能命中一种 | **预处理统一转 jsonl** |
| **扫描型 PDF 无法直接提取** | markitdown 只能处理文本型 PDF | **自动识别 + OCR(Unlimited-OCR)** |
| **Markdown 格式干扰算子** | markitdown 输出含格式标记,影响语言检测/长度/去重 | **去格式 → plain text**(接入层转换) |
| 媒体文件不能嵌入数据集 | 体积爆炸(TB 级) | **路径引用 + OBS 存储** |
| **多数据源血缘溯源** | 需知道样本来自文件/数据库/API | **注入 source_type + 来源元数据** |
| **API推送可靠性** | 落地失败需推送方感知 | **同步落地 + 幂等键(失败回滚不伪成功)** |
| **MinIO 当成新数据源** | 实际是 S3 兼容,会重复造轮子 | **复用 S3 代码,只换 endpoint_url** |
| **HDFS 协议不同于 S3** | WebHDFS REST,不能复用 S3 代码 | **独立连接器(connectors/hdfs.py,免本地 Hadoop)** |
| **HDFS 上格式两类** | 原始文件 vs 数仓格式(parquet/orc) | 本期只拉原始文件✅;数仓数据走 Hive/Doris 直连(需求3) |
| **数据库落地格式** | 结构化数据,jsonl 丢列类型 | **PG族/GoldenDB→parquet;达梦/巨杉/Hive/Doris→当前 jsonl** |
| 跨文档去重 | 避免重复样本污染训练 | **document_minhash_deduplicator** |

---

## 三个典型场景

### 场景 1:法律文书(5000 份 PDF,含扫描件)
```yaml
接入: PDF → 自动识别(文本型/扫描型)
      文本型 → markitdown → 去格式 → jsonl
      扫描型 → Unlimited-OCR → 去格式 → jsonl
治理: 语言过滤 + 长度卡阈值 + MinHash 去重
产物: judgments_clean.parquet (18500 样本,过滤 6500)
说明: 扫描型占 30%,OCR 耗时约 2 小时(GPU 模式)
```

### 场景 2:电商商品图(10 万张 + 描述)
```yaml
接入: 图片批量上传 → OBS + manifest jsonl
治理: 美学过滤(0.4) + 宽高比 + NSFW + 去重
产物: products.parquet(5MB) + OBS 媒体(12GB)
训练: PyTorch DataLoader 懒加载图片
```

### 场景 3:视频采访(500 段,共 300GB)
```yaml
接入: 视频上传 OBS + manifest jsonl
治理: 时长过滤 + 切 10s 片段 + 关键帧打标 + Ray 分布式
产物: interviews.parquet(200MB, 45000 片段) + OBS(50GB)
执行: Ray 集群 4 台 GPU 机,3 小时
```

---

## 项目集成 Checklist

### 后端(`backend/app/services/`)
- [ ] `landing.py` 增加 **PDF 类型自动识别** + **Unlimited-OCR 集成**
- [ ] `landing.py::land_media_batch` — 媒体批量接入
- [ ] `landing.py::land_csv_with_mapping` — CSV 列映射
- [ ] `job_runner.py::materialize_version_with_media` — manifest 物化
- [ ] `job_runner.py::build_process_yaml` 注入 `image/audio/video_key`

### 前端(`frontend/src/`)
- [ ] 媒体批量上传组件(拖拽 + 描述配对)
- [ ] CSV 上传列映射界面
- [ ] PDF 上传后显示类型标签(文本型/扫描型)
- [ ] 算子市场按数据类型筛选(纯文本/图片/视频)
- [ ] 任务配置页算子自动过滤(根据输入数据集类型)

### 数据库
- [ ] `dataset_version` 表增加 `media_stats` / `semantic_schema` JSONB 列
- [ ] `job` 表增加 `executor_type` / `resource_usage` 列


### 场景 4:数据库采集(用户反馈)
```yaml
接入: PostgreSQL(user_feedback表,5000行) → SELECT → records → land_records
落地: parquet(保留列类型,schema失败兜底jsonl);不做text列映射,全列原样保留
治理: text_keys=content + 语言过滤 + 长度过滤 + 去重
产物: feedback_clean.parquet(4200条,过滤800条)
说明: rating(int)/created_at(timestamp)列类型保留,供分析
```

### 场景 5:API实时推送(监控告警)
```yaml
接入: 监控系统 → POST /ingest/push/{token} → land_push_records 同步落地
归并: 首次建数据集(boundDatasetId),后续推送累积为新版本;幂等键去重
治理: text_keys=content + 按严重程度过滤 + 去重
产物: alerts_clean.parquet(治理层 dj-process 导出)
说明: 同步落地无队列;幂等内存版,生产化需 DB 持久去重
```---

## 交付物标准格式

### 纯文本数据集
```
dataset_v1/
├── corpus-00-of-10.parquet   # 元数据,分片
├── corpus-01-of-10.parquet
├── ...
├── corpus_stats.jsonl        # 质量审计
└── process.yaml              # 可复现凭证
```

### 多模态数据集
```
dataset_v1/
├── corpus-*.parquet          # 元数据(轻量,含 OBS 路径)
├── corpus_stats.jsonl
├── process.yaml
└── [OBS bucket]              # 实际媒体文件
    obs://adp-media/train/
    ├── img_0001.jpg
    ├── img_0002.png
    └── ...
```

---

## FAQ 快查

**Q: PDF + CSV 能在一个数据集里吗?**  
A: 可以,但要先统一转 jsonl。CSV 需指定哪列当 `text`,两者 schema 对齐后合并。

**Q: markitdown 提取的 markdown 怎么处理?**  
A: **推荐去格式**(LLM 训练场景)。markitdown 输出含 `#` / `**` / `[]()` 等格式标记,直接喂 data-juicer 会干扰语言检测/长度/去重。接入层用 `markdown` + `BeautifulSoup` 转纯文本,代码见完整文档 2.2.4 节。

**Q: 扫描型 PDF 怎么处理?**  
A: 系统自动识别 + OCR。推荐 **Unlimited-OCR**(百度开源,长文档一次性解析,准确率 ~92%)。流程:markitdown 尝试提取 → 判断字符数 < 50/页 → 自动调用 OCR → 写入 jsonl。安装:`git clone https://github.com/baidu/Unlimited-OCR.git`

**Q: 如何判断 PDF 是文本型还是扫描型?**  
A: 用阅读器试着复制文字:能复制 = 文本型,不能 = 扫描型。系统会自动判断(提取少于 50 字符/页 → 扫描型)。

**Q: 视频数据集会很慢吗?**  
A: 小规模(< 100 视频)单机 OK;大规模用 Ray 分布式,4 台机器线性提速。

**Q: 媒体文件存哪?**  
A: OBS 对象存储。数据集文件(parquet)只存路径,训练时懒加载。

**Q: 如何保证可复现?**  
A: 每个 Job 记录:输入版本 ID + process.yaml + data-juicer 镜像 tag → 重跑完全一致。

**Q: 怎么溯源到原始文件?**  
A: 接入时注入 `source_file` / `doc_id` / `ingest_batch` 字段,治理过程不删,最终进产物。

**Q: 数据库和文件源能在一个数据集吗?**  
A: 可以!所有源都转 jsonl,schema对齐后合并。例:PDF(产品手册) + DB(用户反馈) → 统一训练集。

**Q: 数据库采集会拖垮生产库吗?**  
A: 五个最佳实践:1)只读账号 2)从库采集 3)分批查询(LIMIT 1000) 4)低峰时段 5)连接池限制。

**Q: API推送会丢数据吗?**  
A: 同步落地(无队列):落库成功才返回新版本 id,失败抛错+回滚(不伪成功),推送方按响应重试。带幂等键(内存 TTL 10min)避免重复落地。生产化需 DB 持久去重。

**Q: MinIO 需要单独写连接器吗?**  
A: 不需要。MinIO/OBS/AWS S3 都是 S3 兼容,同一套 boto3/s3fs 代码,唯一区别是 `endpoint_url`。

**Q: HDFS 是什么?要支持吗?**  
A: Hadoop 分布式文件系统,大数据数仓底层存储。协议不同于 S3,需独立连接器(已实现 `connectors/hdfs.py`,走 WebHDFS,无需本地 Hadoop)。**本期只拉原始文件(csv/word/txt)✅**(按扩展名解析);数仓格式(parquet/orc/Hive 表)**不从 HDFS 拉文件**,走需求3 的 Hive/Doris 直连(引擎读成行)。

**Q: 为什么数据库落地是 parquet 而不是 jsonl?**  
A: 数据库是结构化源,parquet 保留列类型(int/timestamp 不退化成字符串)、列式压缩、下游读取高效。**现状两档**:PG族/GoldenDB 已传 `storage_format="parquet"`;达梦/巨杉/Hive/Doris 暂未传、落默认 jsonl。schema 推断失败兜底回退 jsonl。

---

## 下一步

1. 阅读完整设计文档:[data-governance-flow.md](./data-governance-flow.md)
2. 查看实施计划:文档第七章「下一步行动」
3. 运行示例:
   - 纯文本:见文档 3.1 节
   - 多模态:见文档 3.3 节
   - 视频分布式:见文档 3.4 节

---

**维护**:本文档与 `data-governance-flow.md` 同步更新 | 最后更新:2026-06-30
