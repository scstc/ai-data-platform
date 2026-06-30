"""数据类型语义层(L3):`semantic_type` 枚举 + 每类型标准 schema 校验/字段映射。

与 `data_type`(接入/格式功能键,见 datasets 分栏过滤 + 媒体字段解析)**正交**:
`data_type` 不动,`semantic_type` 承载 11 类 LLM 语义维度。设计见 docs/plan/14。

本模块**纯函数、无 DB/IO 依赖**,可纯单测;在 `normalize_to_records` 之后调用,
与文件格式无关(输入已是 list[dict])。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class SemanticType(StrEnum):
    """11 类 LLM 数据语义类型(覆盖需求"数据类型"8 项 + 评估)。"""

    TEXT = "text"  # 文本
    STRUCTURED = "structured"  # 需求#2:结构化(表格/库表/csv)
    UNSTRUCTURED = "unstructured"  # 需求#2:非结构化(文档/日志可解析文本侧)
    MULTIMODAL = "multimodal"  # 需求#1:多模态 + 跨模态
    COT = "cot"  # 需求#3:思维链
    QA = "qa"  # 需求#4:问答对
    PREFERENCE = "preference"  # 需求#5:偏好
    TIMESERIES = "timeseries"  # 需求#6:时序
    GIS = "gis"  # 需求#7:位置
    FUSION = "fusion"  # 需求#8:融合 / 统一语义
    EVAL = "eval"  # 评估数据集(prompt/response;治理整改 G16)


# 展示用中文名(供 GET /semantic-types 与前端下拉)
SEMANTIC_LABELS: dict[SemanticType, str] = {
    SemanticType.TEXT: "文本",
    SemanticType.STRUCTURED: "结构化",
    SemanticType.UNSTRUCTURED: "非结构化",
    SemanticType.MULTIMODAL: "多模态",
    SemanticType.COT: "COT 思维链",
    SemanticType.QA: "QA 问答对",
    SemanticType.PREFERENCE: "偏好",
    SemanticType.TIMESERIES: "时序",
    SemanticType.GIS: "GIS 位置",
    SemanticType.FUSION: "融合",
    SemanticType.EVAL: "评估",
}


@dataclass(frozen=True)
class SemanticSpec:
    """一个语义类型的标准 schema:必填字段 + 别名归一 + 媒体字段。"""

    required: tuple[str, ...] = ()
    # 别名 → 标准名(只 rename 不丢原其它字段;标准名已存在则不覆盖)
    aliases: dict[str, str] = field(default_factory=dict)
    # 多模态媒体字段(标准名)
    media_fields: tuple[str, ...] = ()


SEMANTIC_SCHEMAS: dict[SemanticType, SemanticSpec] = {
    SemanticType.TEXT: SemanticSpec(
        required=("text",),
        aliases={"content": "text", "body": "text", "line": "text"},
    ),
    SemanticType.STRUCTURED: SemanticSpec(),  # 任意非空列即可
    SemanticType.UNSTRUCTURED: SemanticSpec(
        required=("text",),
        aliases={"markdown": "text", "paragraph": "text", "content": "text"},
    ),
    SemanticType.MULTIMODAL: SemanticSpec(
        aliases={
            "image": "images",
            "audio": "audios",
            "video": "videos",
            "caption": "text",
        },
        media_fields=("images", "audios", "videos"),
    ),
    SemanticType.COT: SemanticSpec(
        required=("question", "reasoning", "answer"),
        aliases={
            "input": "question",
            "prompt": "question",
            "chain_of_thought": "reasoning",
            "rationale": "reasoning",
            "thought": "reasoning",
            "cot": "reasoning",
            "output": "answer",
            "response": "answer",
        },
    ),
    SemanticType.QA: SemanticSpec(
        required=("question", "answer"),
        aliases={
            "q": "question",
            "prompt": "question",
            "instruction": "question",
            "a": "answer",
            "response": "answer",
            "output": "answer",
        },
    ),
    SemanticType.PREFERENCE: SemanticSpec(
        required=("prompt", "chosen", "rejected"),
        aliases={
            "question": "prompt",
            "input": "prompt",
            "accepted": "chosen",
            "win": "chosen",
            "loser": "rejected",
            "lose": "rejected",
            "reject": "rejected",
        },
    ),
    SemanticType.TIMESERIES: SemanticSpec(
        required=("timestamp", "value"),
        aliases={
            "time": "timestamp",
            "ts": "timestamp",
            "date": "timestamp",
            "val": "value",
            "metric": "value",
        },
    ),
    SemanticType.GIS: SemanticSpec(
        required=("lat", "lon"),
        aliases={
            "latitude": "lat",
            "y": "lat",
            "longitude": "lon",
            "lng": "lon",
            "x": "lon",
        },
    ),
    SemanticType.FUSION: SemanticSpec(),  # 不强校验
    SemanticType.EVAL: SemanticSpec(
        required=("prompt", "response"),
        aliases={
            "question": "prompt",
            "query": "prompt",
            "instruction": "prompt",
            "answer": "response",
            "reference": "response",
            "output": "response",
        },
    ),
}


@dataclass
class SemanticReport:
    """一次语义校验/归一的结果报告(不落库,随响应回传)。"""

    semantic_type: str | None
    validated: bool  # 是否真正按某枚举校验了(None/未知类型 → False)
    total: int
    bad_rows: int
    column_union: list[str]
    modalities: list[str]  # 多模态:出现过的模态(images/audios/videos/text)
    errors: list[str]  # 抽样错误信息(最多前 5 条)


class SemanticValidationError(ValueError):
    """严格模式下逐行校验失败(由调用方转 422)。"""


def coerce_semantic_type(value: str | None) -> SemanticType | None:
    """把任意字符串收敛为 SemanticType;None/空/未知 → None。"""
    if not value:
        return None
    try:
        return SemanticType(value)
    except ValueError:
        return None


def parse_semantic_type(value: str | None) -> SemanticType | None:
    """写入路径用:None 放行,非法值抛 SemanticValidationError(转 422)。"""
    if not value:
        return None
    try:
        return SemanticType(value)
    except ValueError as exc:
        allowed = ", ".join(t.value for t in SemanticType)
        raise SemanticValidationError(
            f"非法 semanticType={value!r};允许:{allowed}"
        ) from exc


# data_type(接入键)→ semantic_type 的便捷默认映射(展示/落地填充用,不写回 data_type)
_DATA_TYPE_SEMANTIC: dict[str, SemanticType] = {
    "sql": SemanticType.STRUCTURED,
    "csv-tsv": SemanticType.STRUCTURED,
    "image": SemanticType.MULTIMODAL,
    "audio": SemanticType.MULTIMODAL,
    "video": SemanticType.MULTIMODAL,
    "pdf": SemanticType.UNSTRUCTURED,
    "doc": SemanticType.UNSTRUCTURED,
    "docx": SemanticType.UNSTRUCTURED,
    "ppt": SemanticType.UNSTRUCTURED,
    "pptx": SemanticType.UNSTRUCTURED,
    "html": SemanticType.UNSTRUCTURED,
    "log": SemanticType.TEXT,
    "txt": SemanticType.TEXT,
    "json": SemanticType.TEXT,
    "jsonl": SemanticType.TEXT,
}


def infer_semantic_from_data_type(data_type: str | None) -> SemanticType | None:
    """由接入键 data_type 推断默认语义类型(确定性映射);未知 → None。"""
    if not data_type:
        return None
    return _DATA_TYPE_SEMANTIC.get(data_type.lower())


class TrainType(StrEnum):
    """训练用途(治理整改 G1,见 docs/training-dataset-format-spec.md §4)。

    训练平台据此过滤可用数据集;与 SemanticType(语义维度)正交。
    """

    PRETRAIN = "pretrain"
    SFT = "sft"
    DISTILL = "distill"
    DPO = "dpo"
    RLHF = "rlhf"
    EVAL = "eval"
    CUSTOM = "custom"


# semantic_type → 默认训练用途(确定性默认;落地时填充,可被写端点显式覆盖)。
# 只映射语义明确对应训练方式的几类;其余(structured/gis/timeseries/...)→ None。
_SEMANTIC_TO_TRAIN_TYPE: dict[SemanticType, TrainType] = {
    SemanticType.QA: TrainType.SFT,
    SemanticType.COT: TrainType.SFT,
    SemanticType.PREFERENCE: TrainType.DPO,
    SemanticType.TEXT: TrainType.PRETRAIN,
    SemanticType.EVAL: TrainType.EVAL,
}

# 训练用途 → 默认 schema 变体;distill/rlhf/custom 无固定变体 → None。
_TRAIN_TYPE_TO_VARIANT: dict[TrainType, str] = {
    TrainType.PRETRAIN: "text",
    TrainType.SFT: "messages",
    TrainType.DPO: "preference",
    TrainType.RLHF: "prompt_only",
    TrainType.EVAL: "eval",
}


def infer_train_type(semantic_type: str | None) -> str | None:
    """由 semantic_type 推断默认训练用途(确定性);未知/None → None。"""
    st = coerce_semantic_type(semantic_type)
    if st is None:
        return None
    tt = _SEMANTIC_TO_TRAIN_TYPE.get(st)
    return tt.value if tt else None


def default_schema_variant(train_type: str | None) -> str | None:
    """由训练用途取默认 schema 变体;未知/None/无固定变体 → None。"""
    if not train_type:
        return None
    try:
        tt = TrainType(train_type)
    except ValueError:
        return None
    return _TRAIN_TYPE_TO_VARIANT.get(tt)


def parse_train_type(value: str | None) -> str | None:
    """写入路径用:None 放行,非法值抛 SemanticValidationError(转 422)。"""
    if not value:
        return None
    try:
        return TrainType(value).value
    except ValueError as exc:
        allowed = ", ".join(t.value for t in TrainType)
        raise SemanticValidationError(
            f"非法 trainType={value!r};允许:{allowed}"
        ) from exc


def infer_semantic(records: Sequence[dict]) -> SemanticType | None:
    """按样本字段启发式推断语义类型(纯函数);空/无 dict 行 → None。

    优先级:preference > cot > qa > gis > timeseries > multimodal > structured > text。
    """
    sample = next((r for r in records if isinstance(r, dict)), None)
    if sample is None:
        return None
    keys = {str(k).lower() for k in sample}

    def has(*names: str) -> bool:
        return any(n in keys for n in names)

    if has("chosen", "accepted") and has("rejected", "loser", "lose", "reject"):
        return SemanticType.PREFERENCE
    if has("reasoning", "chain_of_thought", "rationale", "thought", "cot"):
        return SemanticType.COT
    if has("question", "q", "prompt", "instruction") and has(
        "answer", "a", "response", "output"
    ):
        return SemanticType.QA
    if has("lat", "latitude") and has("lon", "lng", "longitude"):
        return SemanticType.GIS
    if has("timestamp", "time", "ts", "date") and has("value", "val", "metric"):
        return SemanticType.TIMESERIES
    if has("images", "image", "audios", "audio", "videos", "video"):
        return SemanticType.MULTIMODAL
    if len(keys) > 1:
        return SemanticType.STRUCTURED
    return SemanticType.TEXT


# 多模态子分类(列表"图片/视频/音频/跨模态"子标签 + 筛选用)。
# 媒体字段名(复数,与 SEMANTIC_SCHEMAS[MULTIMODAL].media_fields 一致)→ 子分类 key。
_MEDIA_KIND = {
    "images": "image",
    "audios": "audio",
    "videos": "video",
}


def classify_modalities(modalities: Sequence[str] | None) -> str | None:
    """modalities 集合 → 子分类 key(image|video|audio|cross|None),纯函数。

    语义(用户决策 B):``text`` 计入模态计数 —— 图文配对(images+text)算"跨模态"。
      - ≥2 种(含 text)→ cross
      - 恰好 1 种媒体、无 text → image / video / audio
      - 空 / 仅 text / 无法判定 → None(前端兜底显示"多模态"主标签,无子标签)
    """
    if not modalities:
        return None
    media = {m for m in modalities if m in _MEDIA_KIND}
    has_text = "text" in modalities
    if len(media) + (1 if has_text else 0) >= 2:
        return "cross"
    if len(media) == 1 and not has_text:
        return _MEDIA_KIND[next(iter(media))]
    return None


# classify_modalities 的逆:子分类 key → 代表性 modalities 集合。
# 供列表「数据类型」快速设置多模态子类型时,反写展示版本 modalities(合成代表值,
# 而非来自真实数据);经 classify_modalities 必须能原样还原回该子类型(round-trip)。
_SUBTYPE_TO_MODALITIES: dict[str, list[str]] = {
    "image": ["images"],
    "video": ["videos"],
    "audio": ["audios"],
    "cross": ["images", "text"],  # 图文配对 → 跨模态(≥2 种含 text)
}


def modalities_for_subtype(subtype: str) -> list[str]:
    """子分类 key(image|video|audio|cross)→ modalities 集合;非法 key 抛 KeyError。"""
    return list(_SUBTYPE_TO_MODALITIES[subtype])


def collect_modalities(records: Sequence[dict]) -> list[str]:
    """扫描记录聚合出现过的模态(images/audios/videos/text);**不改记录**(纯读)。

    供 land_records 推断路径(未显式传 semantic_type,原本不改记录)取模态集合:
    复用 multimodal 的别名归一 + _modalities_of,但作用于副本,不动调用方 records。
    """
    spec = SEMANTIC_SCHEMAS[SemanticType.MULTIMODAL]
    found: set[str] = set()
    for r in records:
        if not isinstance(r, dict):
            continue
        found.update(_modalities_of(_normalize_row(r, spec), spec))
    return sorted(found)


def _is_blank(value: object) -> bool:
    """空判定:None / 空串 / 空列表/空字典视为空。"""
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, tuple, dict)):
        return len(value) == 0
    return False


def _normalize_row(row: dict, spec: SemanticSpec) -> dict:
    """别名归一:alias→标准名(标准名已存在则不覆盖,其余字段保留)。"""
    out = dict(row)
    for alias, canon in spec.aliases.items():
        if alias in out and (canon not in out or _is_blank(out.get(canon))):
            out[canon] = out.pop(alias)
    return out


def _validate_row(st: SemanticType, row: dict, spec: SemanticSpec) -> str | None:
    """逐行校验,返回错误信息或 None(通过)。"""
    for req in spec.required:
        if _is_blank(row.get(req)):
            return f"缺少必填字段 {req}"
    if st is SemanticType.STRUCTURED:
        non_blank = [k for k, v in row.items() if not _is_blank(v)]
        if not non_blank:
            return "结构化数据行无任何非空列"
    elif st is SemanticType.MULTIMODAL:
        present = [f for f in spec.media_fields if not _is_blank(row.get(f))]
        if not present and _is_blank(row.get("text")):
            return "多模态数据行无任何媒体/文本字段"
    elif st is SemanticType.GIS:
        try:
            lat, lon = float(row["lat"]), float(row["lon"])
        except (TypeError, ValueError, KeyError):
            return "lat/lon 非数值"
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            return f"经纬度越界 lat={lat} lon={lon}"
    elif st is SemanticType.TIMESERIES:
        if not _timestamp_ok(row.get("timestamp")):
            return "timestamp 不可解析为时间"
        if not _numeric_ok(row.get("value")):
            return "value 非数值"
    return None


def _numeric_ok(value: object) -> bool:
    try:
        float(value)  # type: ignore[arg-type]
        return True
    except (TypeError, ValueError):
        return False


def _timestamp_ok(value: object) -> bool:
    """时间可解析:数值(epoch)或 ISO 字符串(宽松)即可。"""
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, datetime):
        return True
    if isinstance(value, str) and value.strip():
        try:
            datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            return True
        except ValueError:
            return value.strip().isdigit()
    return False


def _modalities_of(row: dict, spec: SemanticSpec) -> list[str]:
    """该行出现的模态(media_fields 命中 + text 命中);跨模态=≥2。"""
    mods = [f for f in spec.media_fields if not _is_blank(row.get(f))]
    if not _is_blank(row.get("text")):
        mods.append("text")
    return mods


def apply_semantic_spec(
    records: list[dict],
    semantic_type: str | None,
    *,
    strict: bool = False,
) -> tuple[list[dict], SemanticReport]:
    """按 semantic_type 归一别名 + 校验必填(纯函数)。

    - semantic_type 为 None / 未知 → **不校验、不改记录**(向后兼容)。
    - 命中枚举 → 逐行 alias 归一(只 rename 不丢)→ 校验;失败:
      - 非严格(默认):bad_rows 计数进 report,**不阻断**;
      - 严格:bad_rows>0 抛 SemanticValidationError(由调用方转 422)。
    """
    st = coerce_semantic_type(semantic_type)
    if st is None:
        cols = _column_union(records)
        return records, SemanticReport(
            semantic_type=semantic_type,
            validated=False,
            total=len(records),
            bad_rows=0,
            column_union=cols,
            modalities=[],
            errors=[],
        )

    spec = SEMANTIC_SCHEMAS[st]
    out: list[dict] = []
    bad = 0
    errors: list[str] = []
    modalities: set[str] = set()
    for i, row in enumerate(records):
        if not isinstance(row, dict):
            bad += 1
            if len(errors) < 5:
                errors.append(f"第{i}行不是对象,无法按 {st.value} 校验")
            out.append(row)
            continue
        norm = _normalize_row(row, spec)
        if st is SemanticType.MULTIMODAL:
            mods = _modalities_of(norm, spec)
            modalities.update(mods)
            norm.setdefault("modalities", mods)
        err = _validate_row(st, norm, spec)
        if err is not None:
            bad += 1
            if len(errors) < 5:
                errors.append(f"第{i}行:{err}")
        out.append(norm)

    if strict and bad > 0:
        raise SemanticValidationError(
            f"{st.value} 严格校验失败:{bad}/{len(records)} 行不合规;"
            f"示例:{'; '.join(errors)}"
        )

    return out, SemanticReport(
        semantic_type=st.value,
        validated=True,
        total=len(records),
        bad_rows=bad,
        column_union=_column_union(out),
        modalities=sorted(modalities),
        errors=errors,
    )


def _column_union(records: Sequence[dict]) -> list[str]:
    """所有行字段的并集(保持首次出现顺序)。"""
    seen: dict[str, None] = {}
    for row in records:
        if isinstance(row, dict):
            for k in row:
                seen.setdefault(str(k), None)
    return list(seen)


def semantic_type_catalog() -> list[dict]:
    """GET /semantic-types 的数据:每类型 key/label/required/mediaFields。"""
    return [
        {
            "key": st.value,
            "label": SEMANTIC_LABELS[st],
            "required": list(SEMANTIC_SCHEMAS[st].required),
            "mediaFields": list(SEMANTIC_SCHEMAS[st].media_fields),
        }
        for st in SemanticType
    ]
