# 数据治理系统整改方案

> 版本:v1.0 | 日期:2026-06-30 | 状态:整改方案(待评审)
> 分支:`feature/governance-remediation-plan`
>
> 依据三份目标文档 —— [数据治理流程](./data-governance-flow.md)、[流程速查](./data-governance-flow-summary.md)、[训练数据集格式规范](./training-dataset-format-spec.md) —— 对当前系统(`ai-data-platform` 后端/前端 + `data-juicer` fork)做**逐文件核实的差距分析**,给出按优先级排期的整改路线。
>
> **方法论(诚实声明)**:本方案的每条结论均引用真实 `file:symbol` 证据,由 6+1 路并行代码调研 + 主调研对核心模块(`engine.py`/`landing.py`/`dataset(_version).py`/`capabilities.py`/`connectors`)的亲读交叉验证得出。凡文档与代码不符处一律记入第三章「文档勘误」,不掩盖、不粉饰(对齐 CLAUDE.md Rule 12 Fail loud)。data-juicer 算子真实性已对照 `data_juicer/ops/` 源码与平台 `operators_catalog.json`(212 算子)双重核实。

---

## 一、结论速览

**一句话**:接入与治理的「**数据进得来、洗得干净**」闭环已相当成熟(连接器族、DJ 子进程编排、算子市场、内容安全审核、质量审计、发布门都已落地);真正的缺口集中在**下游**——把治理后数据**构造成训练 schema**、标注**训练用途元数据**、以及**评估数据集 + 裁判员**这条线,几乎是 0 基础。这恰好是《训练数据集格式规范》第五/七章点名的 P0。

| 维度 | 状态 | 说明 |
|------|------|------|
| 数据接入(文件/库/API/S3/HDFS) | 🟢 成熟 | 连接器 REGISTRY 9 库 + s3/hdfs/api 全注册;诚实失败;parquet 两档兜底 |
| 治理编排(DJ 子进程 + 算子市场) | 🟢 成熟 | `engine.build_config`+`_run_dj`;212 算子入库;能力门控 `runnable` |
| 合成/增强/蒸馏引擎 | 🟢 已有 | `make`/`augment`/`distillation` 共享 engine,产 `origin=synthetic` 版本 |
| 内容安全 / 质量审计 / 发布门 | 🟢 已有 | `review`/`quality`/`scan_verdict`/`publish_status` |
| **数据集构造层(原始列→训练 schema)** | 🔴 缺失 | spec §5 点名缺口;无 messages/alpaca/eval 构造 |
| **训练用途元数据(train_type/schema/record_count)** | 🔴 缺失 | 模型层无字段;训练平台无法按用途过滤 |
| **评估数据集 + ≥300 校验 + 裁判员** | 🔴 缺失 | 需求硬指标;后端 0 实现 |
| 扫描型 PDF OCR / markdown 去格式 | 🟡 未实现 | 文档已自标「需补充」,但 summary/FAQ 呈现为已具备 |
| Ray 分布式 / 分片 / 媒体 key 注入 | 🟡 半接 | 能力已探测,但 `build_config` 不生成对应 YAML 键 |
| 逐条血缘(source_file/doc_id) | 🟡 仅版本级 | 记录级来源字段未注入 |

---

## 二、当前真实能力基线(已实现,勿重复造)

整改前先固化"已经有什么",避免把成熟模块当缺口重做。以下均有代码证据。

### 2.1 接入层(`landing.py` / `connectors/`)
- `normalize_to_records`(landing.py:317)受理 `txt/log/csv/tsv/json/jsonl/xlsx/xls` + 文档 `pdf/doc/docx/ppt/pptx/html` + `geojson`;文档经 `_doc_to_records`(:245)markitdown 提取后按空行切段落,`.doc` 走 antiword→soffice→mammoth 双桥。
- `land_records`(:415)统一出口:默认 `storage_format="jsonl"`,显式传 `"parquet"` 时 `records_to_parquet_bytes`,**schema 推断失败兜底回退 jsonl**(D2 红线已守);产物统一上平台 MinIO,失败 rollback 不伪成功。
- 语义层 `semantic_registry.apply_semantic_spec`(:375):10 类 `SemanticType`(text/structured/unstructured/multimodal/cot/qa/preference/timeseries/gis/fusion),各带 required + 别名归一。`PREFERENCE.required=(prompt,chosen,rejected)` 已与 DPO 对齐。
- 多模态 manifest 真实存在:`_DATA_TYPE_TO_MEDIA_FIELD={image:images,audio:audios,video:videos}`(:68)、`MANIFEST_FORMAT`(:80);媒体批量经 `datasets.upload_media_as_dataset`(:365)落 manifest 版本。

### 2.2 连接器(`connectors/__init__.py:REGISTRY`)
- 12 条注册全在:`database` 9 个 db_kind(pg/hologres/kingbase/gaussdb→`PgConnector`,goldendb→`MysqlConnector`,dameng/sequoiadb/hive/doris→proprietary)+ `(s3/hdfs/api,None)`,与文档 §2.3.1 表逐行一致。
- 契约三方法 `probe/list_tables/run_ingest` 齐全;驱动缺失诚实 `ConnectorNotReady` 不伪成功。
- PG/MySQL 落地显式 `storage_format="parquet"`(pg.py:196 / mysql.py:231);API 推送 `land_push_records` 同步落地、内存幂等 TTL 600s、`boundDatasetId` 归并,**无 Redis 队列**(与文档一致);HDFS 走 WebHDFS REST 纯 httpx。

### 2.3 治理编排(`engine.py` / `operator_catalog.py` / `capabilities.py`)
- `build_config`(engine.py:125)序列化算子链为 DJ 配置 → `_run_dj`(:201)以**子进程**跑 `dj_process_bin --config`(py3.11 venv 隔离),进程组管理 + 超时整组杀 + `terminate_job`。
- 算子市场 DB 驱动:`Operator` 模型 + `operators_catalog.json` 212 算子(8 类:mapper 123/filter 57/dedup 10/formatter 8/selector 5/aggregator 4/grouper 3/pipeline 2);`effective_runnable`(:344)按 `resource_class × Capabilities` 实时映射 ready/needs_api/needs_compute/needs_media。
- `capabilities.py` 探测 **DJ venv**(非后端)的 cuda/ray/vllm + llm,`*_force` 可覆盖,异常不抛。
- 合成/增强/蒸馏:`make`/`augment`/`distillation` 三 service 复用 `build_config`+`_run_dj`,产 `origin=synthetic` 新版本。

### 2.4 质量 / 审核 / 交付
- 质量:`quality.run_quality_job` 起 `dj-analyze` 子进程逐条 `compute_stats`,产 `data_stats.jsonl` 回写 `stats_uri`;`quality_report` 纯 Python 聚合(count/mean/分位/直方图)。
- 内容安全:`review.scan_version` 四路并集(自定义词/正则 + 内置敏感词 + PII + LLM moderate);`review_runner` 写 `ReviewFinding` + 产打标版本 + 自动三态 `scan_verdict`。
- 采集质量:`ingest_quality` 纯函数算 null_rate/schema drift,落 `quality_stats`/`schema_snapshot`/`quality_verdict`。
- 发布门:`datasets.publish_version` 校验 `scan_verdict=='passed'` 才发布;`export_version_to_s3` 导出成员文件。

---

## 三、文档勘误(目标文档与代码不符 / 算子不存在)

整改前必须先修文档,否则照搬会被"未知算子"校验拒绝或链路描述误导后续开发。

### 3.1 data-juicer 算子勘误(照搬必失败)
逐一对照 `data_juicer/ops/` 源码 + 平台 catalog 核实:

| 文档引用 | 真相 | 处置 |
|---------|------|------|
| `video_captioning_mapper`(§2 框图) | 泛称**不存在**;真实 5 变体 `video_captioning_from_frames/_from_video/_from_audio/_from_summarizer/_from_vlm_mapper`(§3.4 用的 `_from_frames` 正确) | 框图改真名 |
| `obs_download_file_mapper` / `obs_upload_file_mapper`(§3.4) | **不存在**;OBS 即 S3 兼容,用 `s3_download_file_mapper`/`s3_upload_file_mapper`(接受 `endpoint_url`)或 `download_file_mapper` | 替换 |
| `columns_selector`(§3.2) | **不存在**;Selector 是"按值选行"非"选列"(只有 `topk/frequency/random/range/tags_specified_field_selector`)。列保留应在接入层或自研列投影 mapper | 删除/改路径 |
| `python_lambda_filter`(§3.6) | **不存在**;只有 `python_lambda_mapper`(mapper 非 filter)。当 filter 用语义不符 | 改 `general_field_filter` 或先 mapper 打标再 filter |
| `document_deduplicator:{method:simhash}`(§3.6) | `document_deduplicator` 是 **MD5 精确哈希、无 method 参数**;simhash 用独立的 `document_simhash_deduplicator` | 改算子名 |

✅ **核实存在**(可放心引用):`language_id_score_filter`、`text_length_filter`、`perplexity_filter`、`image_aesthetics_filter`、`image_aspect_ratio_filter`(注意是宽高比非分辨率)、`image_nsfw_filter`、`document_minhash_deduplicator`、`image_deduplicator`、`document_simhash_deduplicator`、`remove_specific_chars_mapper`、`image_face_blur_mapper`、`video_split_by_duration_mapper`、`video_duration_filter`、`video_resolution_filter`、`generate_qa_from_text_mapper`(合成式蒸馏可用)、`python_lambda_mapper`(构造层方式 B 可用)、`pair_preference_mapper`、`pii_redaction_mapper`。

### 3.2 实现现状勘误(示意代码 ≠ 真实代码)
- **§2.1.2-2.1.4 的 `_detect_pdf_type` / `_ocr_pdf_unlimited` 是未实现的示意代码**:全 backend grep 无此函数,亦无 Unlimited-OCR/PyMuPDF/任何 OCR 依赖。§4.1.1 已诚实标"需补充",但 §2.1/summary 速查表/FAQ 把"扫描型 PDF→OCR"呈现为既有能力 → 需统一标"规划/未实现"。
- **§2.2.4 的 `_markdown_to_plain_text`(markdown+BeautifulSoup)不存在**:`markdown`/`beautifulsoup4` 不在 backend 依赖;真实 `_doc_to_records` 保留 markitdown 段落原文,不去格式。
- **§2.5.1/§2.5.2 的 `boto3`/`s3fs` + `S3_PRESETS` 字典是虚构**:真实用 `minio` Python SDK(`external_store.py:36`),且 OBS 有专门适配分支(虚拟主机寻址/region 解析,:112-141),**并非"只换 endpoint"**。
- **§2.3.3 称 proprietary 四连接器"未传 storage_format → 落 jsonl"不成立**:它们 `run_ingest` 调 `land_records(name=, source_uri=, job_id=)` 的**参数名与现签名全错**(真实是 keyword-only 的 `dataset_name`/`produced_by_job_id`,无 `name`/`source_uri`/`job_id`)→ 真激活会抛 `TypeError`,不会落任何 jsonl(见 §4 G-CONN)。
- **§2.4.4 "API 推送经 land_records"链路描述不符**:`land_push_records` 自己 `json.dumps` 直写 `data.jsonl` 并手工建 `DatasetVersion`,不调 `land_records`(结果 jsonl 对,链路不对)。
- **summary checklist 引用的 `job_runner.build_process_yaml` / `materialize_version_with_media` 两函数不存在**:真实分工是 `engine.build_config`(生成 YAML)+ `external_store.materialized_version`(物化输入)+ `persist_manifest_output`(回写媒体)。
- ✅ **spec §8.5 关于 `_DATA_TYPE_TO_MEDIA_FIELD` + manifest "已实现"属实**(landing.py:68/80),是文档中少数与代码一致的实现声明 —— 但其示例 `{images:[...],"text":"...","source_file":"..."}` 与真实 manifest 行(`{images:[member_key],text:<__dj__image>,__member:{...}}`,**无 source_file、text 是 DJ 占位 token、路径是 MinIO 相对 key 而非 obs://**)不符。

---

## 四、差距清单(按严重级,带代码证据 + 工作量)

> 严重级:**P0** 阻塞核心闭环 / 需求硬指标;**P1** 重要、有 workaround;**P2/P3** 增强。
> 工作量:**S**=1-2 天,**M**=3-5 天,**L**=1-2 周。

### P0 —— 训练数据下游闭环(规范第五/七章点名,当前 0 基础)

| ID | 缺口 | 证据 | 工作量 |
|----|------|------|--------|
| **G1** 训练用途元数据缺失 | `Dataset`/`DatasetVersion` 无 `train_type`/`schema`/`record_count` 字段(只有 `data_type`/`semantic_type`/`modalities`/`rows`);全仓 grep `train_type`/`record_count`/`construct` 零命中。训练平台无法按用途过滤 | M |
| **G2** 数据集构造层缺失 | 原始列(`{id,content,rating}`)→ 训练 schema 的方式 A(接入字段映射)/方式 B(治理 mapper)均未落地;`semantic_registry` 只归一到"语义"schema(QA=question/answer),不产训练 schema | L |
| **G3** SFT(messages)构造器缺失 | 规范 P0 最高频格式;无 `{messages:[{role,content}]}` / `{instruction,input,output}` 产出与 role 合法性校验;最接近的 `SemanticType.QA` 是 `{question,answer}` | M |
| **G4** 评估数据集 + ≥300 校验缺失 | **需求硬指标**;`main.py` 22 路由无 eval;无 `{prompt,response}` schema、无 `record_count>=300` Fail-loud 校验、无内置银行业评估集 | L |
| **G5** 裁判员(LLM-as-judge)缺失 | 规范 §3.7 三角色 `prompt/response/completion` 对比打分;grep `judge/裁判/completion` 仅命中 LLM API 字段,无打分逻辑 | L |

### P1 —— 交付完整性 + 流程已设计未接线

| ID | 缺口 | 证据 | 工作量 |
|----|------|------|--------|
| **G6** Ray executor 未接线 | `capabilities._detect_ray` 已探测,但 `build_config` 从不写 `executor_type:ray`/`ray_address`;即便装了 ray 也只跑单机(`np`)。**探测了却用不上** | M |
| **G7** 媒体 key 注入缺失 | `build_config` 不写 `image_key/audio_key/video_key`;manifest 加工仅靠 DJ 默认键名巧合对齐,无法配自定义媒体字段 | S |
| **G8** 交付三件套未打包 | `export_version_to_s3` 只拷成员文件;`stats`(独立 `stats_uri`)不随导出走;`dataset_card.md` 全仓无生成 | M |
| **G9** `dataset_card.md` 生成缺失 | grep `dataset_card` 全仓零命中;`publish/export` 均不产说明卡 | M |
| **G-CONN** proprietary 四连接器激活即崩 | `Dameng/Sequoia/Hive/Doris.run_ingest` 调 `land_records(name=,source_uri=,job_id=)` 参数名全错 → 真库现场激活抛 `TypeError`(被未装驱动的 `ConnectorNotReady` 掩盖;Doris 因 asyncmy 已装,连真库即暴露)。文档承诺"装驱动即激活"不成立 | S |
| **G10** 扫描型 PDF 静默落空 | `_doc_to_records` 对 pdf 仅 markitdown,提取为空时返回 `[]` → 扫描件落 0 行,**违反 D2 红线 + Fail loud**(无 OCR、无告警、无 pdf_type 标记) | S(止血)/L(OCR) |
| **G11** `export_stats` 不保证产出 | `build_config` 不注入 `export_stats:true`;`run_process_job` 仅在 `data_stats.jsonl` 已存在时设 `stats_uri`,DJ 默认不必产 stats → 承诺的审计件可能缺失 | S |

### P2/P3 —— 增强 / 一致性

| ID | 缺口 | 证据 | 级别/量 |
|----|------|------|--------|
| **G12** markdown 去格式未实现 | 无 `_markdown_to_plain_text`;markitdown 的 `#/**/[]()` 标记进 text,干扰语言检测/长度/去重 | P2/M |
| **G13** `.md` 扩展名不受理 | `LANDABLE_FORMATS` 无 md,上传 `.md` 直接 400(文档却列为支持的纯文本) | P2/S |
| **G14** 逐条血缘缺失 | `land_records` 不注入 `source_file/doc_id/ingest_batch`;跨文件合并后无法回溯单条样本来源 | P2/M |
| **G15** 算子目录静态快照 | 212 算子是构建期快照;DJ 版本漂移不自动反映,且用作提交校验依据 | P2/M |
| **G16** 评估 `prompt/response` 语义类型缺失 | `SemanticType` 无 eval(QA=question/answer);与评估字段不对齐 | P2/S |
| **G17** 前端 train_type/构造/eval UI 缺失 | `pages` 无 train_type 选择器、无列→训练字段映射 UI、无 eval 模块/≥300 校验;`qa.tsx` 自述"静态演示";算子市场 modality 仅展示不筛选 | P1-P2 各项 |
| **G18** 可复现凭证不全 | `Job` 无 `dj_version`/`image_tag`/`executor_type`(规范 Q9 复现要素) | P2/S |
| **G19** 媒体"拷贝进 MinIO"非"OBS 零拷贝" | 媒体字节复制进平台 MinIO,`source_kind='object_store'`,manifest 存相对 key 非 `obs://` URI | P3/M |

---

## 五、整改路线(分阶段)

设计原则:**最小改动撬动最大闭环**。复用已成熟的 `engine`/`make`/`semantic_registry`/`Job` 机制,不另起炉灶;先打通规范双 P0(SFT messages + 评估集),再补交付与分布式。

### 阶段 0 —— 止血与对齐(0.5 周,先做)✅ 已实施(2026-06-30,`feature/governance-remediation-plan`)
低风险、防止"假成功",优先合入:

1. **G-CONN(S)✅**:已修 `proprietary.py` 四处 `land_records` 调用 —— `name=`→`dataset_name=`、`job_id=`→`produced_by_job_id=`、删 `source_uri=`(溯源信息放 `note=`)、补 `semantic_type="structured"`/`source_kind="database"`/`storage_format="parquet"` 对齐 PG/MySQL。**已加签名漂移单测** `tests/unit/test_connectors.py::test_doris_run_ingest_landing_kwargs_match_signature`(桩 `land_records` 真实签名,kwarg 漂移即 TypeError)。
2. **G10 止血(S)✅**:`_doc_to_records` 对 pdf/文档提取为空时不再返回 `[]`,改抛 `ParseError`(pdf 文案点名"疑似扫描型/需 OCR"),守 D2 + Fail loud。完整 OCR 留阶段 4。单测 `tests/unit/test_landing_remediation.py`。
3. **G13(S)✅**:`md/markdown` 已加入 `LANDABLE_FORMATS`(逐行 `{text}`)+ `uploads.py::ALLOWED_EXTENSIONS`;`datasets.py` 错误文案从 `LANDABLE_FORMATS` 动态生成,自动同步。
4. **G14(S)✅**(原列在阶段4,提前并入):`land_upload` 经新增 `_stamp_lineage` 注入逐条 `source_file`/`doc_id`(内容 sha256)/`ingest_batch`,不覆盖上游已有值。单测覆盖。
5. **文档勘误(S)✅**:已修 `data-governance-flow.md` 的算子名(`video_captioning_mapper`→`_from_frames`、`obs_*_file_mapper`→`s3_*_file_mapper`、`columns_selector`→注明不存在改接入层、`python_lambda_filter`→`general_field_filter`、`document_deduplicator:{simhash}`→`document_simhash_deduplicator`)。

> **已知**:`tests/test_uploads.py` 3 条用例 `KeyError:'total'` 失败,经 stash base 复跑确认为**先前就存在**的 `/api/v1/uploads` 列表响应封装 bug,与本次改动无关,另行处理。


### 阶段 1 —— 训练用途元数据(G1,1 周)
**这是构造层、评估、训练平台过滤的公共地基,必须先行。**

### 阶段 1 —— 训练用途元数据(G1,1 周)✅ 已实施
**这是构造层、评估、训练平台过滤的公共地基,必须先行。**

- **迁移**:`DatasetVersion` 增 `train_type`(pretrain/sft/distill/dpo/rlhf/eval/custom,nullable)、`schema_variant`(text/alpaca/messages/preference/prompt_only/eval,nullable);`record_count` 复用现有 `rows`(语义等价,避免冗余列,在 API 层映射输出)。放 `DatasetVersion` 而非 `Dataset`:训练用途是**版本级**属性(同数据集不同版本可不同用途)。
- **默认值**:用现有 `semantic_type` 推断(qa→sft、preference→dpo、text→pretrain),存量行留空。
- **暴露**:`datasets` 列表/详情 API 输出 `train_type`/`schema`/`record_count`,供训练平台按 `train_type` 过滤。
- ⚠️ 注意 `DatasetVersion` 不可变(写入即定格),元数据须在落地/构造时一次写定。
- **DB 安全**:迁移走 alembic,新列全 nullable + 有 server_default,可回退(对齐 `/adp-deploy` "部署前确认迁移可回退")。

> **实施(2026-06-30)**:迁移 `0031`(列)已应用到 `adp_gov`;`semantic_registry` 加 `TrainType` 枚举 + `infer_train_type`/`default_schema_variant`/`parse_train_type`;`land_records` 落地写定默认;`DatasetVersionRead` 暴露 `trainType`/`schemaVariant` + `recordCount`(computed_field 钉 alias);`list_datasets` 加 `?trainType=` 版本级子查询过滤。单测 `tests/unit/test_train_type_defaults.py`(13 例)。

### 阶段 2 —— 数据集构造层 + 双 P0(G2/G3/G4/G5,2-3 周)✅ 已实施
规范第五章核心。两条构造路径都落,但**先方式 A(覆盖 80% 简单场景)**:

**2a. 方式 A:接入/采集时字段映射(M)✅**
- 构造任务 `job.type='construct'`:`ConstructGoal{train_type, schema_variant, field_mapping/messages}`,确定性列映射(纯 Python,Rule 5 不用 LLM)。
- 落地前 schema 校验(role∈{system,user,assistant} 由 `MessageTurnSpec` Literal 兜;SFT output/eval prompt-response 等必填非空逐行剔除;全不合规 → `ConstructError` Fail loud)。
- 服务 `services/construct.py`(`build_training_records`/`run_construct_job`)+ 路由 `api/v1/construct.py`(8 端点,镜像 make)+ `job_runner` 分派。单测 `tests/unit/test_construct.py`(7 例)。

**2b. 方式 B:治理层构造算子(M)** —— 算子 `python_lambda_mapper`/`generate_qa_from_text_mapper`/`pair_preference_mapper` 已在 catalog,可经现有 process/synthesis job 跑;构造层方式A已覆盖主路径,方式B 仅需前端归类(后续)。

**2c. 评估数据集模块(G4/G5,L)✅** —— 需求硬指标,独立 router:
- `services/eval_dataset.py`:`land_eval_dataset` 落地 `{prompt, response, category?}`,`validate_eval_records` 强制 `record_count>=settings.eval_min_records(=300)` 的 **Fail-loud 校验**(<300 → `EvalValidationError` → 422)。
- **裁判员**:`AIProvider.judge_answers`(`OpenAICompatProvider` LLM 打分 + `HeuristicProvider` Jaccard 兜底降级);`services/judge_runner.py` 对 `reference` vs `completion` 打分 → `EvalResult` 逐条 + `job.eval_report` 汇总(avgScore/passRate/byCategory/scoreBuckets)。
- `SemanticType` 增 `eval`(prompt/response,别名归一)(G16);`Job.eval_report` 列 + `EvalResult` 表(迁移 `0032`,已应用到 `adp_gov`)。
- 路由 `api/v1/evaluation.py`(上传/起裁判/列表/报告/逐条结果)。单测 `tests/unit/test_eval_judge.py`(9 例)+ 端到端 smoke 验证(启发式裁判跑通,2 行 → avgScore/passRate/eval_results 落库)。
- ⏳ **未做**:内置通用+银行业评估集(≥300 条/集)需业务侧供数据;`completion` 来源(跑被测模型生成回答)留扩展点,当前需上游 join 进待评版本。

> **实施(2026-06-30)**:迁移 `0031`+`0032` 已应用到 `adp_gov`;96 项新单测全过,ruff 干净;app 装配全通过(construct 8 + eval 6 路由注册)。

### 阶段 3 —— 交付完整性 + 分布式(G6/G7/G8/G9/G11/G18)✅ 已实施(2026-06-30)
- **G7+G6(S+M)✅**:`build_config` 增可选 `executor_type/ray_address`(切 'ray'/'ray_partitioned' 才写,经 DJ config 真实 choices 核实)+ `image_key/audio_key/video_key`(仅 manifest 输入注入)。`JobCreate` 暴露 `use_ray/image_key/audio_key/video_key`;`_start_job` 用 `capabilities.ray` 门控(未就绪 → 400,诚实失败)。⏳ Ray 产物是**目录**(ray_exporter `os.makedirs`),`run_process_job` 现按单文件读 rows——本期接线 config + 门控,Ray 落版本的目录聚合读留后续。
- **G11(已澄清,不改 build_config)✅**:**核实纠正**——`export_stats` 不是 DJ 的 jsonargparse 配置键(只是 Exporter 构造参数,default 模式恒 True),注入 YAML 会让 dj-process 崩。default executor 恒产 `data_stats.jsonl`(前提:流水线含 Filter 算子产 stats 列);`run_process_job` 现有 `stats_uri=... if stats_path.exists()` 已是正确的"有则记"。`build_config` **绝不注入 export_stats**(已加单测守此结论)。
- **G8+G9(M)✅**:新增独立 `services/export_delivery.py` + `api/v1/export.py`(`job.type='export'`,8 端点):治理后版本 → `train.parquet`(可按条数分片 `export_shard_size`)/`train.jsonl` + `train_stats.jsonl`(stats_uri 优先,quality_stats 兜底,皆空则 warning)+ `dataset_card.md`(名称/train_type/schema/条数/血缘来源/算子链/已知局限,局限由 `collect_limitations` 确定性推导)+ 打包落 S3。`collect_lineage` BFS 上游(去环+MAX_DEPTH);**不产新 DatasetVersion**(交付物非数据版本),只记 JobInput 血缘边。
- **G18(S)✅**:`Job` 加 `dj_version`/`image_tag`/`executor_type`(迁移 `0033`,已应用 adp_gov);`capabilities.get_dj_version()` 探测 DJ venv 包版本(哨兵缓存,缺失安全 None);`_run_job` 执行时一次写定;`JobRead` 暴露。`settings.image_tag`(env IMAGE_TAG)。

> **实施验证**:迁移 `0033` 已应用到 adp_gov(head=0033);新增 `test_export_delivery.py`+`test_phase3_wiring.py`,107 项单测全过、ruff 干净;export 端到端 smoke 验证(7 条→3 parquet 分片+card,stats 兜底 warning,血缘边写入)。app 装配 export 6 路由注册。

### 阶段 4 —— 接入增强 + 前端(部分实施 2026-06-30)
> G14(逐条血缘)已在阶段0提前完成。
- **G12(M)✅**:`landing._markdown_to_plain_text`(纯函数,标准库 re)去除 markitdown 输出的 `#/**/[]()`/表格分隔/列表前缀等标记;接入 `_doc_to_records` 文档分支(空值 Fail-loud 校验之后)。markitdown 确实发射 markdown 标记(已实证)。单测 `test_phase4_ingest.py`。
- **G10(框架 ✅ / OCR 本体承诺级)**:`_detect_pdf_type`(纯函数,字符密度阈值)+ `_pdf_page_count`(pypdf,失败退化为 1)+ `_ocr_pdf`(HTTP 客户端调独立 OCR service,懒加载+超时+诚实失败)。`_doc_to_records` pdf 分支:空/低密度 → OCR(`ocr_enabled` 开启时)否则 Fail-loud。**`settings.ocr_enabled` 默认 False → 零行为变化**(扫描件仍抛错,回归单测守此)。⏳ OCR 引擎本体(Unlimited-OCR/paddleocr ~1GB+GPU)是独立 service,本机不跑,真实准确率/性能需现场 GPU 验证。
- **G15(✅,本机实证)**:`capabilities.probe_dj_operator_names()`(子进程读 DJ `OPERATORS.list()`,本机实测返回 209 算子)+ `operator_catalog.detect_operator_drift()`(DB 快照 vs DJ 真实集 diff,排除 formatter/pipeline)+ `GET /operators/catalog/drift` 端点 + `scripts/check_operator_drift.py`(CI 用)。DJ venv 不可探测 → `status=unavailable` 降级不误报。
- **G17 前端(契约层 ✅ / 页面承诺级)**:`typings.d.ts` 契约对齐——`DatasetVersion` 加 `trainType/schemaVariant/recordCount`、`SemanticType` 加 `eval`、新增 `TrainType/SchemaVariant` 联合类型 + `ConstructJobCreate/JudgeJobCreate/ExportJobCreate` 等全套请求/报告类型、`DatasetListParams.trainType`;`api.ts` 加 construct/eval(上传+裁判+逐条结果)/export 的请求函数。**`npm run tsc` 通过**(我的契约改动零新增错误;唯一 2 个错误在未触碰的 `UploadModal.tsx`,经 stash-base 确认先存)。⏳ 新增任务页面 UI(构造/评估/交付编辑器+列表)未做——无法跑 dev server 验证且会大幅扩散未提交 diff,留待 UI 联调阶段(已备好契约与 API,镜像 `distillation`/`make` 页面模板即可)。
- **G19(承诺级,本期不落)**:真 OBS 零拷贝需可达 OBS 桶现场验证,涉及上传引用模式/manifest schema/物化凭证/presign/GC 红线 6 个面(M 级)。设计已在 spec 备好(`parse_storage_uri` 隔离 + manifest `__member` 扩展 + per-member 物化),待 OBS 桶就绪现场补,避免无法验证时声称完成。

> **实施验证**:119 项后端单测全过(含回归),ruff 干净;新增依赖 `pypdf>=4.0.0`;前端 tsc 通过。无新迁移(阶段4 不动表结构)。
- **G15(M)**:算子目录改运行时动态扫描 DJ 安装(或 CI 校验快照与实际算子集不漂移)。
- **G19(M)**:如需真 OBS 零拷贝(引用既有 OBS 桶而非复制进 MinIO),改 manifest 存 `obs://` URI。

---

## 六、依赖关系与排期建议

```
阶段0 止血(0.5w) ──┐
                    ├─→ 阶段1 元数据 train_type(1w) ──→ 阶段2 构造层+双P0(2-3w) ──→ 阶段3 交付+分布式(1-2w)
G14 逐条血缘 ───────┘         │                                                          │
(可与阶段0并行)              └────────────── 训练平台按 train_type 过滤 ────────────────┘
                                              阶段4 接入增强+前端(按需,可与2/3并行)
```

- **关键路径**:阶段 1(元数据)是阶段 2/3 的前置;评估集(G4/G5)与 SFT 构造(G3)可在阶段 2 内并行(不同 router)。
- **前端(G17)**:每项后端能力就绪后跟进对应 UI,可与后端阶段 2/3 并行推进。
- **总工期**:核心闭环(阶段 0-3)约 **5-7 周**;接入增强与前端完整化(阶段 4)按资源叠加。

## 七、风险与验证

| 风险 | 应对 |
|------|------|
| `DatasetVersion` 不可变,元数据写错无法改 | 落地/构造时一次写定;校验前置;留 `note` 记录 |
| alembic 迁移生产回退 | 新列全 nullable + server_default;部署前演练 downgrade(对齐 `/adp-deploy`) |
| 构造层 schema 校验遗漏致脏数据进训练 | Fail loud:role 合法性 / 必填非空 / eval≥300 落地前硬校验,违反即拒绝 + 明确报错 |
| DJ 算子版本漂移(G15) | 短期 CI 校验快照,中期改运行时扫描 |
| 裁判员 LLM 成本/可用性 | 复用 `llm_config` 用量统计;provider 失败降级(参考 `review.py` moderate 降级模式) |

**验证(对齐 CLAUDE.md 后端约定)**:
- 每阶段补 `backend/tests/test_<module>.py`(Rule 9:测意图非仅行为);构造层/评估校验须有"低于阈值即拒绝"的反例测试。
- 改完跑冒烟:`cd backend && uv run pytest tests/test_files.py tests/test_datasets_preview.py tests/test_uploads.py tests/test_dataset_acl.py tests/test_rbac.py -q`。
- 后端 `ruff check .` + 前端 `biome + tsc` 都过;改 OpenAPI 契约后 `npm run openapi` 重生客户端。

## 八、立即可做(本次整改第一刀建议)

按"风险最低、闭环最关键、撬动最大"排序,建议从**阶段 0 + 阶段 1** 起步:

1. 修 `proprietary.py` 签名 bug(G-CONN)+ 补漂移单测 —— 潜伏崩溃,优先。
2. PDF 扫描件止血(G10)+ `.md` 受理(G13)+ 逐条血缘(G14)—— 守 D2 红线,低成本。
3. `DatasetVersion` 加 `train_type`/`schema_variant` 元数据迁移(G1)—— 解锁下游一切。
4. 三份文档按第三章批量勘误 —— 让文档与代码一致,后续开发不踩坑。

> 本方案随实现推进更新;每完成一个阶段在本表勾除并记录实际 commit。

---

**关联文档**:[数据治理流程](./data-governance-flow.md) · [流程速查](./data-governance-flow-summary.md) · [训练数据集格式规范](./training-dataset-format-spec.md) · 实现规划 `plan/`




