"""数据集构造层执行引擎(治理整改 G2/G3):原始列 → 训练 schema。

方式A:纯 Python 确定性列映射(不调 LLM/算子,Rule 5:确定性转换不用 model)。
产物 origin='managed' 新版本,打 train_type/schema_variant 元数据(G1)。
与 make/augment(LLM 合成/改写)区别:本模块不进 data-juicer 子进程。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.dataset import Dataset
from app.models.dataset_version import DatasetVersion
from app.models.job_input import JobInput
from app.schemas.construct import ConstructGoal, ConstructReport, FieldSource
from app.services.engine import _new_version_id, _read_jsonl_head, materialized_version
from app.services.external_store import upload_file_to_datasets
from app.services.landing import (
    MANIFEST_FORMAT,
    parquet_bytes_to_records,
    records_to_jsonl_bytes,
)


class ConstructError(RuntimeError):
    """构造执行失败(无可用输入 / 全部行不合规 / 不支持的输入)。"""


def _is_blank(v: Any) -> bool:
    """空判定(对齐 semantic_registry 语义):None/空串/纯空白/空容器为空。"""
    if v is None:
        return True
    if isinstance(v, str):
        return v.strip() == ""
    if isinstance(v, (list, dict, tuple)):
        return len(v) == 0
    return False


def _resolve(src: FieldSource, row: dict) -> tuple[str | None, str | None]:
    """按取值来源从源行解析一个字段值。返回 (值, 错误信息)。

    优先级 column > template > const。column 缺列/None、template 缺占位符 → 错误。
    """
    if src.column is not None:
        if src.column not in row or row[src.column] is None:
            return None, f"列 {src.column!r} 缺失或为空"
        return str(row[src.column]), None
    if src.template is not None:
        try:
            return src.template.format(**row), None
        except (KeyError, IndexError) as exc:
            return None, f"模板字段缺失:{exc}"
    if src.const is not None:
        return src.const, None
    return None, "FieldSource 未指定 column/template/const"


def build_training_records(
    records: list[dict], goal: ConstructGoal
) -> tuple[list[dict], int, list[str]]:
    """把原始记录逐行构造成目标训练 schema。

    返回 (合规记录, 不合规行数, 错误抽样最多5条)。不合规行被剔除(不写入产物),
    必填字段为空即视为不合规(Fail loud:不静默落脏样本)。
    """
    good: list[dict] = []
    bad = 0
    errors: list[str] = []
    variant = goal.schema_variant

    def fail(idx: int, msg: str) -> None:
        nonlocal bad
        bad += 1
        if len(errors) < 5:
            errors.append(f"第{idx}行:{msg}")

    for idx, row in enumerate(records):
        if not isinstance(row, dict):
            fail(idx, "非对象行")
            continue
        out, err = _build_one(row, goal, variant)
        if err is not None:
            fail(idx, err)
            continue
        good.append(out)
    return good, bad, errors


def _build_one(
    row: dict, goal: ConstructGoal, variant: str
) -> tuple[dict | None, str | None]:
    """构造单行;必填字段为空返回错误。"""
    fm = goal.field_mapping
    if variant == "messages":
        turns: list[dict] = []
        for turn in goal.messages:
            content, err = _resolve(turn.content, row)
            if err is not None or _is_blank(content):
                return None, f"{turn.role} content {err or '为空'}"
            turns.append({"role": turn.role, "content": content})
        return {"messages": turns}, None

    # 列映射类:逐字段解析(记录首个解析错误,便于定位是缺列还是模板出错)
    resolved: dict[str, str | None] = {}
    first_err: str | None = None
    for field_name, src in fm.items():
        val, err = _resolve(src, row)
        resolved[field_name] = None if err else val
        if err and first_err is None:
            first_err = f"{field_name}: {err}"

    def _need(*keys: str) -> str | None:
        """必填字段校验;有空字段时返回错误(优先报解析错误)。"""
        if any(_is_blank(resolved.get(k)) for k in keys):
            return first_err or f"{'/'.join(keys)} 必填非空"
        return None

    if variant == "text":
        if (e := _need("text")) is not None:
            return None, e
        return {"text": resolved["text"]}, None
    if variant == "alpaca":
        if (e := _need("instruction", "output")) is not None:
            return None, e
        return {
            "instruction": resolved["instruction"],
            "input": resolved.get("input") or "",
            "output": resolved["output"],
        }, None
    if variant == "preference":
        if (e := _need("prompt", "chosen", "rejected")) is not None:
            return None, e
        return {
            "prompt": resolved["prompt"],
            "chosen": resolved["chosen"],
            "rejected": resolved["rejected"],
        }, None
    if variant == "prompt_only":
        if (e := _need("prompt")) is not None:
            return None, e
        return {"prompt": resolved["prompt"]}, None
    if variant == "eval":
        if (e := _need("prompt", "response")) is not None:
            return None, e
        out = {"prompt": resolved["prompt"], "response": resolved["response"]}
        if not _is_blank(resolved.get("category")):
            out["category"] = resolved["category"]
        return out, None
    return None, f"未知 schemaVariant={variant}"


async def run_construct_job(
    session: AsyncSession,
    *,
    job_id: str,
    input_version: DatasetVersion,
    goal: ConstructGoal,
    output_dataset_id: str | None = None,
) -> tuple[DatasetVersion, str, str, ConstructReport]:
    """对输入版本跑确定性构造 → 写新版本(打 train_type/schema_variant)。

    返回 (新版本, ''(无 DJ recipe), 日志路径, 报告)。失败抛 ConstructError。
    """
    if input_version.format == MANIFEST_FORMAT:
        raise ConstructError(
            "多模态 manifest 输入暂不支持构造(占位符注入见规范 §8.5 后续)"
        )

    dataset_id = output_dataset_id or input_version.dataset_id
    target_ds = await session.get(Dataset, dataset_id)
    if target_ds is None:
        raise ConstructError(f"输出数据集不存在:{dataset_id}")

    max_vno = await session.scalar(
        select(func.max(DatasetVersion.version_no)).where(
            DatasetVersion.dataset_id == dataset_id
        )
    )
    new_vno = (max_vno or 0) + 1
    out_dir = Path(settings.datasets_dir) / dataset_id / f"v{new_vno}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "data.jsonl"
    log_path = out_dir / "run.log"
    report_path = out_dir / "report.json"

    started = time.time()

    async with materialized_version(input_version, session) as input_path:
        if not input_path.exists():
            raise ConstructError(f"输入版本数据文件不存在:{input_version.storage_uri}")
        if input_version.format == "parquet":
            records = parquet_bytes_to_records(input_path.read_bytes(), limit=0)
        else:
            records = _read_jsonl_head(input_path, 0)

    input_count = len(records)
    good, bad, errors = build_training_records(records, goal)

    warnings: list[str] = []
    if goal.schema_variant == "eval" and len(good) < 300:
        warnings.append(
            f"评估集仅 {len(good)} 条(<300);评估模块发布前需补足(规范 §3.7 硬指标)"
        )

    # Fail loud:全部行不合规 → 不写空/脏版本,直接失败
    if not good:
        log_path.write_text(
            f"构造失败:{input_count} 行无一合规\n" + "\n".join(errors),
            encoding="utf-8",
        )
        raise ConstructError(
            f"构造产出为 0 条(输入 {input_count} 行全部不合规);" + "; ".join(errors)
        )

    out_path.write_bytes(records_to_jsonl_bytes(good))
    storage_uri = await upload_file_to_datasets(dataset_id, new_vno, out_path)

    version = DatasetVersion(
        id=_new_version_id(),
        dataset_id=dataset_id,
        version_no=new_vno,
        storage_uri=storage_uri,
        format="jsonl",
        rows=len(good),
        size=out_path.stat().st_size,
        origin="managed",
        produced_by_job_id=job_id,
        train_type=goal.train_type,
        schema_variant=goal.schema_variant,
        note=(
            f"构造产出(来自 v{input_version.version_no},"
            f"{goal.train_type}/{goal.schema_variant})"
        ),
    )
    session.add(version)
    session.add(JobInput(job_id=job_id, dataset_version_id=input_version.id))
    await session.commit()
    await session.refresh(version)

    log_path.write_text(
        f"构造完成:输入 {input_count} 行 → 合规 {len(good)} / 不合规 {bad}\n"
        + "\n".join(errors),
        encoding="utf-8",
    )
    report = ConstructReport(
        job_id=job_id,
        input_version_id=input_version.id,
        output_version_id=version.id,
        train_type=goal.train_type,
        schema_variant=goal.schema_variant,
        input_count=input_count,
        output_count=len(good),
        bad_rows=bad,
        elapsed_seconds=round(time.time() - started, 2),
        warnings=warnings,
        errors=errors,
        raw={"goal": goal.model_dump(mode="json")},
    )
    report_path.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return version, "", str(log_path), report
