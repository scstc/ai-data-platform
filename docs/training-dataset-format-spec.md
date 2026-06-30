# 训练数据集格式规范

> 版本:v1.0 | 日期:2026-06-30 | 状态:设计文档
>
> 本文档定义**数据治理平台**产出的数据集,交付给**训练/推理平台**时的格式契约。
> 是两个平台之间的接口标准:治理侧按此规范产出,训练侧按此规范消费。

## 一、定位:为什么需要这份规范

数据治理平台的完整链路:

```
【数据接入层】多源(数据库/HDFS/OBS/PDF/API) → 统一落地 jsonl/parquet
        ↓
【数据治理层】data-juicer 五类算子:Filter 过滤 / Deduplicator 去重 / Mapper 变换+增强 / Selector 选择(质量门禁 + 合规脱敏)
        ↓
【数据集构造层】★ 本文档规范的部分:原始数据 → 训练 schema
        ↓
【训练/推理平台】按训练方式选数据集 → 训练 / 评估
```

**核心认知**:数据集的最终 schema **由它要服务的训练方式决定**,不是由数据来源决定。

- 同一份银行客服对话,做 SFT 是 `{messages}`,做 DPO 是 `{prompt, chosen, rejected}`,做评估是 `{prompt, response}`
- 接入层只负责"把数据弄进来、洗干净",**构造成哪种训练格式是本层的事**

## 二、训练方式与数据集格式对照总表

训练平台支持 8 种训练方式,各自需要不同的数据集 schema:

| 训练方式 | 数据集 schema | 文件格式 | 本文档章节 |
|---------|--------------|---------|-----------|
| **1. 继续预训练** | `{text}` | jsonl / parquet | 3.1 |
| **2. SFT 微调**(LoRA/QLoRA/全参) | `{instruction, input, output}` 或 `{messages}` | jsonl | 3.2 |
| **3. 蒸馏** | 模型蒸馏:同 SFT(output 来自教师);数据蒸馏:Selector 选子集 / Mapper 合成 | jsonl | 3.3 |
| **4. DPO 训练** | `{prompt, chosen, rejected}` | jsonl | 3.4 |
| **5. RFT / RLHF** | `{prompt}` + 奖励信号 | jsonl | 3.5 |
| **6. NoteBook 自定义** | 任意(用户自理) | 任意 | 3.6 |
| **7. 离线作业训练** | 取决于具体作业 | 任意 | 3.6 |
| **评估数据集** | `{prompt, response}`,**每集 ≥300 条** | jsonl | 3.7 |

> **格式选择原则**:
> - **JSONL 为主**:中小规模、SFT/对话/DPO/评估,所有微调框架(LLaMA-Factory / OpenAI / HF)的标准
> - **Parquet 备选**:仅 TB 级预训练语料,需列式存储 + 分片

> **多模态是"变体"不是"第 9 种"**:上面每种训练方式都有**纯文本**和**多模态**两个 schema 变体。多模态变体在文本基础上多了 `images`/`audios`/`videos` 路径字段 + 文本里的 `<image>` 等占位符。详见第八章。

---

## 三、各训练方式的数据集 schema

### 3.1 继续预训练(Continue Pretrain)

**用途**:在通用模型上继续注入领域语料(如银行知识、法规文档)。

**schema**:只需一个文本字段。

```jsonl
{"text": "中华人民共和国商业银行法 第一章 总则 第一条 为了保护..."}
{"text": "信用卡逾期还款将影响个人征信记录,具体规则如下..."}
```

**字段说明**:

| 字段 | 类型 | 必填 | 说明 |
|-----|------|------|------|
| `text` | string | ✅ | 一段完整文本(一篇文档 / 一个段落) |

**来源对应**:
- PDF/Word(去格式后)→ 天然就是这种格式
- 数据库文本列 → 取单列拼成 `text`

**注意**:预训练不需要"问答对",纯文本即可。单条 `text` 长度建议不超过模型上下文窗口。

---

### 3.2 SFT 指令微调 ⭐ 最常用

**用途**:教模型按指令回答(银行客服问答、业务咨询)。

**两种 schema,二选一**(训练平台需都支持):

#### 格式 A:Alpaca 风格(instruction/input/output)

```jsonl
{"instruction": "解释什么是年化利率", "input": "", "output": "年化利率是指..."}
{"instruction": "根据以下信息判断是否可以贷款", "input": "月收入8000,征信良好", "output": "符合基本贷款条件..."}
```

| 字段 | 类型 | 必填 | 说明 |
|-----|------|------|------|
| `instruction` | string | ✅ | 任务指令 |
| `input` | string | ❌ | 补充输入(无则空字符串) |
| `output` | string | ✅ | 期望回答 |

#### 格式 B:对话风格(messages)—— 推荐

```jsonl
{"messages": [{"role": "system", "content": "你是银行智能客服"}, {"role": "user", "content": "信用卡怎么挂失?"}, {"role": "assistant", "content": "您可以通过以下方式挂失..."}]}
```

| 字段 | 类型 | 必填 | 说明 |
|-----|------|------|------|
| `messages` | array | ✅ | 对话轮次列表 |
| `messages[].role` | string | ✅ | `system` / `user` / `assistant` |
| `messages[].content` | string | ✅ | 该轮内容 |

**多轮对话**:`messages` 数组里多组 user/assistant 交替即可。

**选型建议**:
- 单轮问答 → 格式 A 或 B 都行
- 多轮对话、带 system prompt → **用格式 B**(更通用,OpenAI/LLaMA-Factory 默认)

---

### 3.3 蒸馏(Distillation)—— 区分两种

**"蒸馏"有两个完全不同的含义,别混淆**:

| | 模型蒸馏(Model/Knowledge Distillation) | 数据蒸馏(Data Distillation) |
|---|---|---|
| **蒸馏对象** | 模型(大→小) | 数据集(大→小) |
| **产出** | 更小的学生模型 | 更小而精的高质量数据集 |
| **属于** | 一种训练方式 | 治理层/构造层的数据处理 |

#### 3.3.1 模型蒸馏(训练方式)

**用途**:用大模型(教师)的输出训练小模型(学生)。

**schema**:与 SFT 完全相同,区别在 `output` 由教师模型生成,而非人工标注。

```jsonl
{"messages": [{"role": "user", "content": "解释复利"}, {"role": "assistant", "content": "<教师模型生成的高质量回答>"}]}
```

**数据构造特殊点**:
- `output`/`assistant` 内容来自调用教师模型 API 批量生成
- 可附带教师模型的 logits/概率分布(soft label)做软蒸馏,但 schema 上仍以文本为主
- 治理时需对教师输出做质量过滤(去掉教师答错的)

#### 3.3.2 数据蒸馏(治理层/构造层)

**用途**:把大数据集浓缩成小而精的子集,降低训练成本、提升数据质量。**不是训练方式,是数据处理目标**,用 data-juicer 算子实现。

**三种形态**:

| 形态 | 做法 | 对应算子 | 产出 |
|-----|------|---------|------|
| **A. 筛选式**(选代表性子集) | 从大集里选最有价值的一小撮,去冗余/低质 | **Selector**(top-k、按分布采样) | 原集的高质量子集 |
| **B. 合成式**(大模型生成浓缩数据) | 用强模型生成高质量样本,替代海量噪声真实数据 | **Mapper**(LLM 调用生成) | 全新的合成数据集 |
| **C. 教师生成式**(为模型蒸馏备数据) | 教师模型批量生成 output → 喂学生模型 | **Mapper** | 模型蒸馏用的训练集(见 3.3.1) |

**形态 A 示例(Selector 筛选)**:
```
100 万条原始语料
  → data-juicer Selector(按质量分选 top-k / 按分布采样)
  → 5 万条代表性子集(训练效果接近甚至更好)
```

**形态 B 示例(Mapper 合成)**:
```yaml
# data-juicer process.yaml — 用 LLM 生成高质量银行问答
process:
  - generate_qa_from_text_mapper:   # 或自定义 LLM 调用 mapper
      hf_or_api_model: <教师模型>
      # 从领域文档生成问答对,替代爬取的噪声数据
```

**schema**:数据蒸馏的产出 schema **取决于目标训练方式**——筛选/合成出来的数据,最终还是 `{text}` / `{messages}` / `{prompt,chosen,rejected}` 之一(看你蒸馏出来是给预训练还是 SFT 用)。

**关键认知**:数据蒸馏不是独立 schema,而是**用 Selector/Mapper 算子产出更优数据集的治理手段**;它和模型蒸馏(3.3.1)的交集是形态 C——教师模型生成的数据既是"数据蒸馏的产出",又是"模型蒸馏的输入"。

---

### 3.4 DPO 训练(偏好对齐)

**用途**:让模型学会"什么是好回答、什么是差回答"。

**schema**:一个 prompt + 一对优劣回答。

```jsonl
{"prompt": "客户情绪激动地投诉,如何回应?", "chosen": "我非常理解您的心情,先为给您带来的不便道歉...", "rejected": "这不是我们的问题。"}
```

| 字段 | 类型 | 必填 | 说明 |
|-----|------|------|------|
| `prompt` | string | ✅ | 问题/指令 |
| `chosen` | string | ✅ | 更优的回答(偏好) |
| `rejected` | string | ✅ | 更差的回答 |

**数据来源**:
- 人工标注偏好对
- 或同一 prompt 让不同模型/不同温度生成,人工/规则判优劣

---

### 3.5 RFT / RLHF 训练

**用途**:基于奖励信号的强化学习对齐。

**schema**:prompt 集 + 奖励来源(奖励模型或规则)。

```jsonl
{"prompt": "请推荐适合保守型投资者的理财产品"}
{"prompt": "解释什么是基金定投"}
```

| 字段 | 类型 | 必填 | 说明 |
|-----|------|------|------|
| `prompt` | string | ✅ | 待生成的问题 |
| `reward_model` | — | — | 奖励来自独立奖励模型或规则,通常不在数据集里,而在训练配置中指定 |

**说明**:RLHF 的数据集主要提供 prompt,模型在线生成回答后由奖励模型打分。奖励模型本身的训练数据则类似 DPO 的偏好对。

---

### 3.6 NoteBook / 离线作业(自定义)

**用途**:用户在 NoteBook 里自由处理数据,或提交自定义离线训练作业。

**schema**:**不约束**。用户自行管理数据格式。

**平台职责**:提供数据集的原始访问(parquet/jsonl 下载或挂载路径),不强制 schema。

---

### 3.7 评估数据集 ⚠️ 硬性要求

**用途**:评测模型效果(模型评估模块用)。

> **硬指标**(需求明确):内置通用场景 / 银行业评估数据集,**每个数据集不少于 300 条**。

**schema**:prompt + 参考答案。

```jsonl
{"prompt": "信用卡年费如何减免?", "response": "信用卡年费减免通常有以下方式:1.刷卡达标..."}
{"prompt": "什么是LPR利率?", "response": "LPR(贷款市场报价利率)是指..."}
```

| 字段 | 类型 | 必填 | 说明 |
|-----|------|------|------|
| `prompt` | string | ✅ | 评测问题 |
| `response` | string | ✅ | 参考答案(标准答案) |
| `category` | string | ❌ | 评估维度/类别(便于分维度统计) |

**评估流程中的三个角色**(对应评估详情页):
- `prompt`:输入问题
- `response`:**参考回答**(数据集里的标准答案)
- `completion`:**模型回答**(评估时模型生成,不在数据集里)
- 裁判员(大模型/人工)对比 `response` 与 `completion` 打分

**与训练集的区别**:
- 评估集**不参与训练**,只用于评测
- 必须 ≥300 条(统计显著性)
- 字段是 `prompt + response`,不是 `instruction/output`

---

## 四、数据集元数据规范(训练平台选数据集的依据)

每个数据集落地时,**必须标记训练用途**,训练平台据此过滤可用数据集。

```json
{
  "dataset_id": 123,
  "name": "银行客服SFT数据集",
  "train_type": "sft",          // pretrain / sft / distill / dpo / rlhf / eval / custom
  "schema": "messages",          // 该 train_type 的具体 schema 变体
  "format": "jsonl",             // jsonl / parquet
  "record_count": 5000,          // 样本条数(评估集需 ≥300)
  "source_kind": "database",     // 血缘:来源类型
  "created_at": "2026-06-30T10:00:00Z"
}
```

| 字段 | 取值 | 作用 |
|-----|------|------|
| `train_type` | `pretrain`/`sft`/`distill`/`dpo`/`rlhf`/`eval`/`custom` | 训练平台按此过滤:选 SFT 训练只显示 `sft` 数据集 |
| `schema` | `text`/`alpaca`/`messages`/`preference`/`prompt_only`/`eval` | 标明具体字段结构,供下游校验 |
| `record_count` | int | 训练前预检(如评估集 < 300 → 告警) |

**关键约束**:训练平台选数据集时,**按 `train_type` 与训练方式匹配**——避免把 DPO 数据集喂给 SFT 训练导致格式不符。

---

## 五、数据集构造层(当前缺口)

### 5.1 现状与缺口

数据接入 + 治理已实现"多源 → 统一 parquet/jsonl",但**落地的还是原始结构**(如数据库表的原列名),**不是训练 schema**:

```
数据库 user_feedback 表
  ↓ 接入层已实现
parquet: {id, content, rating, created_at}   ← 原始表结构
  ↓ ⚠️ 缺这一步:构造成训练格式
SFT jsonl: {"instruction": "...", "output": content}
```

**这一步(原始列 → 训练字段)是本规范要补的"数据集构造层"。**

### 5.2 两种构造方式

#### 方式 A:接入时映射(简单场景)

前端配采集任务时,用户指定字段映射:

```yaml
采集任务:
  表: user_feedback
  训练用途: sft
  schema: alpaca
  字段映射:
    instruction: "固定模板:请分析以下用户反馈的情感"
    input: content        # content 列 → input
    output: rating        # rating 列 → output(需转文本)
```

落地时直接构造成 `{instruction, input, output}`。

#### 方式 B:治理层构造(灵活场景)

先原样落地,再用 data-juicer 的 mapper 算子构造训练字段:

```yaml
# data-juicer process.yaml
process:
  # 用 mapper 拼接 prompt 模板 + 字段映射
  - python_lambda_mapper:
      lambda_fn: |
        lambda row: {
          "messages": [
            {"role": "user", "content": f"分析这条反馈:{row['content']}"},
            {"role": "assistant", "content": f"情感评分:{row['rating']}"}
          ]
        }
```

**选型**:
- 简单字段映射(列 → 字段)→ 方式 A
- 需要拼模板、改写、多列组合 → 方式 B

### 5.3 构造层的质量要求

构造成训练格式后,落地前应做校验(Fail loud):
- **schema 完整性**:必填字段不能缺(如 SFT 的 `output` 不能为空)
- **评估集条数**:`train_type=eval` 时校验 `record_count >= 300`
- **格式合法性**:`messages` 的 role 只能是 system/user/assistant

---

## 六、交付规范

### 6.1 交付物三件套

```
dataset_v1/
├── train.jsonl           # 训练数据(对应 train_type 的 schema)
├── train_stats.jsonl     # 质量审计(data-juicer 算出的指标)
└── dataset_card.md       # 数据集说明卡
```

**dataset_card.md** 应包含:
- 数据集名称、train_type、schema、条数
- 字段含义说明
- 数据来源(血缘:哪个库/表/文件)
- 治理流程(用了哪些算子)
- 已知局限(如 OCR 数据可能有识别错误)

### 6.2 格式选择决策表

| 场景 | 格式 | schema |
|-----|------|--------|
| 领域预训练,TB 级语料 | parquet + 分片 | `{text}` |
| SFT 微调,几千~几百万条 | jsonl | `{messages}`(推荐)或 `{instruction,input,output}` |
| DPO 对齐 | jsonl | `{prompt, chosen, rejected}` |
| 模型评估 | jsonl | `{prompt, response}`,≥300 条 |
| 推理测试 | jsonl | 与训练同 schema,便于对比 |

### 6.3 落地实施 Checklist

- [ ] 数据集落地时写入 `train_type` / `schema` / `record_count` 元数据
- [ ] 训练平台选数据集按 `train_type` 过滤
- [ ] 实现"数据集构造层":接入时字段映射(方式 A)或治理层 mapper(方式 B)
- [ ] 评估集校验 ≥300 条
- [ ] 优先支持 **SFT(messages)** 和 **评估(prompt/response)** 两种最高频格式
- [ ] 交付三件套:train + stats + card

---

## 七、优先级建议

按使用频率和需求硬指标,建议落地顺序:

| 优先级 | 格式 | 理由 |
|-------|------|------|
| **P0** | SFT(`messages`) | 80% 微调场景,银行客服/问答首选 |
| **P0** | 评估(`prompt/response`) | 需求硬指标(≥300 条),评估模块依赖 |
| **P1** | 预训练(`text`) | 文档场景天然支持,补领域知识 |
| **P2** | DPO(`prompt/chosen/rejected`) | 做对齐才需要 |
| **P3** | RLHF / 蒸馏 | 高阶能力,按需 |

---

## 八、多模态训练数据集(图片 / 音频 / 视频)

### 8.1 核心原则:媒体走路径,不嵌入数据集

与纯文本最大的区别:

| | 纯文本训练 | 多模态训练 |
|---|---|---|
| **数据集里存什么** | 全部内容(text 字段) | **文本 + 媒体路径**(`images:["obs://..."]`) |
| **媒体本体在哪** | —— | **对象存储(OBS/S3)**,不进 jsonl |
| **jsonl 体积** | 与内容成正比 | 很轻(只有文本+路径) |
| **训练时** | 直接读 | 按路径从 OBS **懒加载**媒体 |

**三个关键约定**:
1. **路径字段**:`images` / `audios` / `videos`(数组,与 data-juicer、主流框架一致)
2. **占位符**:文本里用 `<image>` / `<audio>` / `<video>` 标记媒体插入位置(不同框架占位符可能不同,如 data-juicer 用 `<__dj__image>`)
3. **路径稳定**:训练可能跑数周,期间 OBS 路径不可变(用版本号锁定或内容寻址)

### 8.2 各场景的多模态 schema

#### 图文理解 / 图文问答(VQA,多模态 SFT)

```jsonl
{"messages": [{"role": "user", "content": "<image>这张合同有什么风险?"}, {"role": "assistant", "content": "图中合同第3条约定的违约金过高..."}], "images": ["obs://adp-media/contracts/img_001.jpg"]}
```

#### 图文对(caption,看图说话 / 对齐预训练)

```jsonl
{"text": "一份盖章的银行贷款合同", "images": ["obs://adp-media/img_002.jpg"]}
```

#### 音频 / 语音(ASR、语音问答)

```jsonl
{"audios": ["obs://adp-media/calls/call_001.wav"], "text": "客户来电咨询信用卡额度调整"}
```

#### 视频理解

```jsonl
{"messages": [{"role": "user", "content": "<video>柜员操作是否规范?"}, {"role": "assistant", "content": "视频中柜员未核验证件..."}], "videos": ["obs://adp-media/teller/clip_001.mp4"]}
```

#### 文生图 / 文生视频(扩散模型)

```jsonl
{"text": "盖章的银行合同特写,正式风格", "images": ["obs://adp-media/gen/img_010.jpg"]}
```

### 8.3 字段说明

| 字段 | 类型 | 说明 |
|-----|------|------|
| `images` / `audios` / `videos` | array[string] | 媒体路径列表(本地相对路径 或 `obs://` / `s3://` URI) |
| `text` / `messages` | string / array | 文本部分,含 `<image>` 等占位符标记媒体位置 |

**占位符与媒体数量对应**:文本里有几个 `<image>`,`images` 数组就应有几个路径,顺序一一对应。

### 8.4 多模态不是第 9 种训练方式,是已有方式的变体

每种训练方式都有多模态版本:

| 训练方式 | 纯文本 schema | 多模态 schema |
|---------|--------------|--------------|
| 预训练 | `{text}` | `{text, images:[...]}` |
| SFT | `{messages}` | `{messages(含<image>), images:[...]}` |
| DPO | `{prompt, chosen, rejected}` | 同上 + `images:[...]` |
| 评估 | `{prompt, response}` | 同上 + 媒体路径(≥300 条要求不变) |

### 8.5 与现有系统的对接

你们接入层已有 **manifest 格式**(`landing.py`)即多模态数据集的雏形:

```python
# landing.py 现有约定(已实现)
# _DATA_TYPE_TO_MEDIA_FIELD = {"image": "images", "audio": "audios", "video": "videos"}
# MANIFEST_FORMAT 的 jsonl 每行引用 OBS 媒体路径
{"images": ["obs://adp-media/img_001.jpg"], "text": "...", "source_file": "..."}
```

**字段命名与本规范、data-juicer、训练框架三方一致** ✅。从"接入的 manifest"到"训练的多模态数据集",差的还是**构造层**:
- 给文本插入 `<image>` 占位符
- 按训练方式组织成 `messages` / `text` / `prompt-chosen-rejected`
- 校验占位符数量与媒体路径数量一致

### 8.6 多模态交付注意

| 项 | 要求 |
|---|------|
| **交付物** | jsonl(轻量,含路径)+ **OBS 媒体目录**(实际文件)+ stats + card |
| **路径可达性** | 训练平台需有 OBS 读权限;路径在训练周期内不可变 |
| **媒体配套** | dataset_card 须注明媒体存储位置、访问方式、总大小 |
| **占位符约定** | card 须注明用的占位符(`<image>` 还是框架特定的),供训练侧对齐 |

---

**文档维护**:本文档随训练平台需求演进更新,当前对应 `ai-data-platform@dev` 分支。
关联文档:[数据治理流程](./data-governance-flow.md)(数据如何接入+治理,本文档承接其下游的"构造+交付";多模态接入/OBS 归档见其 3.3/3.4 场景)。



