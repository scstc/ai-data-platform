"""landing.py 缺陷修复回归。

覆盖本轮修的 7 处:
1. (P0) land_media_manifest 续写 draft 时旧 manifest 读取失败 fail-loud
   (不再吞成空清单)。
2. (P0) _target_draft_version 建新 draft 版本号接入 version_alloc 的并发冲突重试。
3. (P0) records_to_jsonl_bytes 自定义类型编码(bytes/datetime/date/Decimal)。
4. (高) add_table_member 同名覆盖成员时清空过期 stats_uri。
5. (中) records 惰性契约:records_to_jsonl_bytes / add_table_member 接受 Iterable[dict]。
6. (中) add_table_member 上传成功但 DB 阶段失败时尽力回收孤儿对象。
7. (低) PDF 页数检测失败置 None,_detect_pdf_type 显式保守回退。
8. (高,终审回修新增) land_media_manifest 无 existing draft 分支同样接入
   with_version_conflict_retry(此前只有 2 的 _target_draft_version 接入,
   这条姊妹分支遗漏)。

3/5(records_to_jsonl_bytes 部分)/7 是纯函数,不连库;其余需要 DB(db_session)。
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path as _P

import pytest

from app.services.connectors.base import IngestError
from app.services.landing import (
    _detect_pdf_type,
    _pdf_page_count,
    add_table_member,
    create_dataset,
    land_media_manifest,
    records_to_jsonl_bytes,
)

# ---------------------------------------------------------------------------
# 3) records_to_jsonl_bytes:自定义 default 编码约定(不连库)
# ---------------------------------------------------------------------------


def test_records_to_jsonl_bytes_encodes_non_json_native_types():
    """`default=str` 会把 bytes 当成随意的 repr/latin1 字符串处理、Decimal 精度
    不可控——这里钉住每种类型的编码约定,下游(导出/DJ 消费/回读)按此约定解析,
    而不是拿到一坨不可预期的 repr 碎片。"""
    records = [
        {
            "raw": b"\xff\x00hi",
            "ts": datetime(2026, 1, 2, 3, 4, 5),
            "d": date(2026, 1, 2),
            "amount": Decimal("19.99"),
        }
    ]
    out = records_to_jsonl_bytes(records).decode("utf-8")
    row = json.loads(out.strip())
    assert row["raw"] == base64.b64encode(b"\xff\x00hi").decode("ascii")
    assert row["ts"] == "2026-01-02T03:04:05"
    assert row["d"] == "2026-01-02"
    assert row["amount"] == "19.99"


# ---------------------------------------------------------------------------
# 5) records 惰性契约:records_to_jsonl_bytes 接受一次性生成器(不连库)
# ---------------------------------------------------------------------------


def test_records_to_jsonl_bytes_accepts_one_shot_generator():
    """底层写 jsonl 的路径要支持 Iterable[dict](生成器)单趟消费,不要求
    `__len__`/可重复迭代——否则未来接入生成器式来源(如流式采集)会直接在
    "判断是否为空" 这一步出错或崩溃。"""

    def gen():
        yield {"n": 1}
        yield {"n": 2}

    assert records_to_jsonl_bytes(gen()) == b'{"n": 1}\n{"n": 2}\n'


def test_records_to_jsonl_bytes_empty_generator_is_empty_bytes():
    assert records_to_jsonl_bytes(iter(())) == b""


# ---------------------------------------------------------------------------
# 7) PDF 页数检测失败:置 None,_detect_pdf_type 显式保守回退(不连库)
# ---------------------------------------------------------------------------


def test_pdf_page_count_returns_none_on_corrupt_content():
    """旧实现失败退化为 1 页:一份实际多页、总字符量不低但"人均"很低的扫描件,
    会被 `总字符 / 1` 算出虚高密度而误判成文本型,漏检真正需要 OCR 的文档。
    修复后失败必须显式为 None,绝不能假装知道页数。"""
    assert _pdf_page_count(b"not a real pdf") is None


def test_detect_pdf_type_unknown_page_count_is_conservative():
    """page_count=None 时必须保守判定为疑似扫描(触发可能的 OCR 兜底),而不是
    延续旧的"当 1 页处理"语义——用一个具体反例钉住:20 页文档共 600 字符
    (人均 30 < 阈值 50,应判定扫描),若被当成 1 页,`600/1=600` 远超阈值,
    会被误判为文本型,静默丢失内容。"""
    text = "x" * 600
    assert _detect_pdf_type(text, None) is True
    # 对照组:页数已知为 1 时行为与旧实现完全一致(未破坏正常路径)
    assert _detect_pdf_type(text, 1) is False


# ---------------------------------------------------------------------------
# 以下需要 DB(db_session);另需内存 MinIO 补丁(add_table_member /
# land_media_manifest 内部都是函数体内延迟 import external_store,补丁打在
# external_store 模块的 upload_object/platform_config/download_to_temp/
# remove_* 上即可对上层 upload_jsonl_member 等包装函数透明生效)。
# ---------------------------------------------------------------------------


def _patch_minio(monkeypatch) -> dict[tuple[str, str], bytes]:
    from app.services import external_store as esmod

    store: dict[tuple[str, str], bytes] = {}

    def _cfg():
        return {"endpoint": "x", "accessKey": "a", "secretKey": "b"}

    async def fake_upload(cfg, bucket, key, data, length, content_type="x"):
        store[(bucket, key)] = data.read()

    async def fake_download(cfg, bucket, key):
        fd, name = tempfile.mkstemp()
        os.close(fd)
        p = _P(name)
        p.write_bytes(store[(bucket, key)])
        return p

    async def fake_remove(cfg, bucket, key):
        store.pop((bucket, key), None)

    async def fake_remove_prefix(cfg, bucket, prefix):
        keys = [k for k in store if k[0] == bucket and k[1].startswith(prefix)]
        for k in keys:
            store.pop(k, None)
        return len(keys)

    monkeypatch.setattr(esmod, "platform_config", _cfg)
    monkeypatch.setattr(esmod, "upload_object", fake_upload)
    monkeypatch.setattr(esmod, "download_to_temp", fake_download)
    monkeypatch.setattr(esmod, "remove_object", fake_remove)
    monkeypatch.setattr(esmod, "remove_prefix", fake_remove_prefix)
    return store


@pytest.mark.asyncio
async def test_land_media_manifest_continuation_fails_loud_on_broken_old_manifest(
    db_session, monkeypatch
):
    """1)(P0) 续写是"旧清单 + 新增行 → 整体覆盖 manifest.jsonl"。旧清单读不出来
    (对象丢失/损坏)绝不能被吞成空清单——那样新清单会覆盖旧对象,已入册的
    媒体成员就静默消失且无迹可查。必须中止本次续写,报错带上旧 manifest 的
    具体位置。"""
    store = _patch_minio(monkeypatch)
    ds = await create_dataset(db_session, name="媒体续写集", data_type="image")

    await land_media_manifest(
        db_session, ds.id, files=[("a.png", b"PNGDATA")], data_type="image"
    )

    from sqlalchemy import select

    from app.models.dataset_version import DatasetVersion

    ver = (
        await db_session.execute(
            select(DatasetVersion).where(DatasetVersion.dataset_id == ds.id)
        )
    ).scalar_one()
    bucket, key = ver.storage_uri.removeprefix("s3://").split("/", 1)
    del store[(bucket, key)]  # 模拟旧 manifest 对象丢失/损坏

    with pytest.raises(IngestError) as exc_info:
        await land_media_manifest(
            db_session, ds.id, files=[("b.png", b"PNGDATA2")], data_type="image"
        )
    assert bucket in str(exc_info.value)
    assert key in str(exc_info.value)


@pytest.mark.asyncio
async def test_land_media_manifest_no_draft_retries_on_concurrent_version_no_collision(
    db_session, monkeypatch
):
    """(P0,终审回修新增) land_media_manifest 无 existing draft 时(首次媒体批量
    入湖)版本号分配同样要走 with_version_conflict_retry——之前这条 else 分支
    仍是裸的"查 max(version_no)+1",与紧邻的 `_target_draft_version` 已接入
    重试形成不一致。两个并发请求各自算出同一个 version_no 时,若无重试兜底,
    后 flush 的一方会在 uq_dataset_version_no 上撞车,IntegrityError 直接冒泡
    (未被 LandingError 包裹),请求 500。这里模拟"另一个并发事务抢先占用了
    本次算好的版本号",验证重试后能正常拿到下一个可用版本号——且已上传的
    文件确实落在与最终 version_no 一致的 S3 前缀里,不是被抢占的那个。"""
    store = _patch_minio(monkeypatch)
    ds = await create_dataset(db_session, name="媒体并发建版本集", data_type="image")

    from app.models.dataset_version import DatasetVersion
    from app.services import version_alloc

    real_next = version_alloc.next_version_no
    collided = {"done": False}

    async def flaky_next(session, dataset_id):
        vno = await real_next(session, dataset_id)
        if not collided["done"] and dataset_id == ds.id:
            collided["done"] = True
            # 模拟另一个并发请求抢先把这个版本号占了
            async with session.begin_nested():
                session.add(
                    DatasetVersion(
                        id="dsv-eeeeee",
                        dataset_id=dataset_id,
                        version_no=vno,
                        storage_uri="pending://race/",
                        format="jsonl",
                        rows=0,
                        size=0,
                        origin="managed",
                        publish_status="draft",
                    )
                )
                await session.flush()
        return vno

    monkeypatch.setattr(version_alloc, "next_version_no", flaky_next)

    ver = await land_media_manifest(
        db_session, ds.id, files=[("a.png", b"PNGDATA")], data_type="image"
    )
    assert collided["done"] is True
    assert ver.version_no == 2  # 1 号被"抢"占,重试后拿到 2,而不是抛异常
    assert any(k[1].startswith(f"{ds.id}/v2/") for k in store)


@pytest.mark.asyncio
async def test_target_draft_version_retries_on_concurrent_version_no_collision(
    db_session, monkeypatch
):
    """2)(P0) 建新 draft 版本号:模拟"另一个并发事务抢先占用了本次算好的版本号"
    ——验证不再是 IntegrityError 直接冒泡把整个任务打挂,而是在 version_alloc
    的 SAVEPOINT 重试下重算后正常拿到下一个可用版本号。"""
    from app.models.dataset_version import DatasetVersion
    from app.services import version_alloc

    ds = await create_dataset(db_session, name="并发建版本集")
    v1, _ = await add_table_member(db_session, ds.id, [{"a": 1}], table_name="t")
    v1.publish_status = "published"
    await db_session.commit()

    real_next = version_alloc.next_version_no
    collided = {"done": False}

    async def flaky_next(session, dataset_id):
        vno = await real_next(session, dataset_id)
        if not collided["done"] and dataset_id == ds.id:
            collided["done"] = True
            # 模拟另一个并发事务抢先把这个版本号占了
            async with session.begin_nested():
                session.add(
                    DatasetVersion(
                        id="dsv-ffffff",
                        dataset_id=dataset_id,
                        version_no=vno,
                        storage_uri="pending://race/",
                        format="jsonl",
                        rows=0,
                        size=0,
                        origin="managed",
                        publish_status="draft",
                    )
                )
                await session.flush()
        return vno

    monkeypatch.setattr(version_alloc, "next_version_no", flaky_next)

    v2, _ = await add_table_member(db_session, ds.id, [{"a": 2}], table_name="t2")
    assert collided["done"] is True
    assert v2.version_no == 3  # 2 号被"抢"占,重试后拿到 3,而不是抛异常


@pytest.mark.asyncio
async def test_add_table_member_override_clears_stale_stats_uri(db_session):
    """4)(高) 同名覆盖成员内容后,若不清空 stats_uri,质量报告页面会继续展示
    "上一次内容"的评估结果——报告和当前实际数据对不上,误导用户以为报告是
    对新内容跑的。"""
    ds = await create_dataset(db_session, name="脏报告集")
    _, m1 = await add_table_member(db_session, ds.id, [{"a": 1}], table_name="t")
    # 模拟质量评估已跑过,按 quality.py 的既有契约回写了 stats_uri
    m1.stats_uri = "s3://adp-datasets/x/quality/job-1/report.json"
    await db_session.commit()

    _, m2 = await add_table_member(db_session, ds.id, [{"a": 2}], table_name="t")
    assert m2.id == m1.id  # 同名覆盖,同一行
    assert m2.stats_uri is None


@pytest.mark.asyncio
async def test_add_table_member_accepts_generator_records(db_session):
    """5)(中) records 传一次性生成器时,行为要与传 list 一致——语义校验/质量
    统计/序列化/行数统计各自都要"看到"完整的那批数据,不能因为生成器提前
    被某一趟消费耗尽,导致后面几趟悄悄拿到 0 行。"""
    ds = await create_dataset(db_session, name="生成器集")

    def gen():
        yield {"a": 1}
        yield {"a": 2}
        yield {"a": 3}

    ver, member = await add_table_member(db_session, ds.id, gen(), table_name="t")
    assert member.rows == 3
    assert ver.rows == 3


@pytest.mark.asyncio
async def test_add_table_member_gcs_orphan_object_on_db_failure_after_upload(
    db_session, monkeypatch
):
    """6)(中) 对象已成功上传到平台 MinIO(拿到 uri),但后续 DB 阶段(这里用
    质量统计炸掉模拟)失败时,必须尽力把刚上传的对象删掉——否则留下一个
    再无任何行引用、任何 UI 都定位不到的孤儿对象,长期占用存储。"""
    store = _patch_minio(monkeypatch)
    ds = await create_dataset(db_session, name="孤儿回收集")

    from app.services import ingest_quality as iq

    def boom(records):
        raise RuntimeError("质量统计炸了(模拟上传成功后的 DB 阶段失败)")

    monkeypatch.setattr(iq, "compute_quality_stats", boom)

    with pytest.raises(RuntimeError, match="质量统计炸了"):
        await add_table_member(db_session, ds.id, [{"a": 1}], table_name="t")

    # 上传已发生,但失败后应被尽力回收:store 里不应残留该成员对象
    assert not any(k[1].endswith("/t.jsonl") for k in store)
