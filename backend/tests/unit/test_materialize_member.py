"""engine.materialized_member 生命周期纯单测。

不依赖 DB / 真实 S3:monkeypatch cached_bytes + platform_config。

背景(治理整改):旧版 _materialize_member 对 s3:// 成员用
NamedTemporaryFile(delete=False) 落地后从不清理,六类任务(蒸馏/合成/评估/
审核/增强/加工)每跑一次遗留一份数据副本,磁盘长期沉积。改为 async
contextmanager 后,正常退出与异常退出都必须清理该临时文件;而本地路径
(managed 存储)是版本的真实数据文件,绝不能被当作临时产物删除。
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


def _make_member(storage_uri: str, fmt: str = "jsonl"):
    from app.models.dataset_version_table import DatasetVersionTable

    return DatasetVersionTable(
        id="dvt-x",
        dataset_version_id="dsv-x",
        table_name="data",
        storage_uri=storage_uri,
        format=fmt,
    )


async def test_materialized_member_s3_cleans_up_on_success(monkeypatch):
    """s3 成员:退出 async with 后,下载生成的临时文件必须被删除——

    否则每次加工/蒸馏/评估任务都会在磁盘上永久遗留一份数据副本(原始缺陷)。
    """
    from app.services import engine

    async def fake_cached_bytes(cfg, bucket, key):
        return b'{"text": "hello"}\n'

    monkeypatch.setattr(engine, "cached_bytes", fake_cached_bytes)
    monkeypatch.setattr(
        engine,
        "platform_config",
        lambda: {"endpoint": "e", "accessKey": "a", "secretKey": "s"},
    )

    member = _make_member("s3://uploads/dset-x/v1/data.jsonl")
    captured_path = None
    async with engine.materialized_member(session=None, member=member) as path:
        captured_path = path
        assert path.exists()
        assert path.read_bytes() == b'{"text": "hello"}\n'

    # 业务意图:临时文件必须在退出时被清理,不能沉积
    assert not captured_path.exists()


async def test_materialized_member_s3_cleans_up_on_exception(monkeypatch):
    """s3 成员:调用方在 async with 块内抛异常时,临时文件仍必须被清理——

    这正是原缺陷的核心场景:六类任务里 dj-process 失败/校验失败等异常路径
    此前会跳过清理,让临时文件永久遗留。
    """
    from app.services import engine

    async def fake_cached_bytes(cfg, bucket, key):
        return b"data"

    monkeypatch.setattr(engine, "cached_bytes", fake_cached_bytes)
    monkeypatch.setattr(
        engine,
        "platform_config",
        lambda: {"endpoint": "e", "accessKey": "a", "secretKey": "s"},
    )

    member = _make_member("s3://uploads/dset-x/v1/data.jsonl")
    captured_path = None

    class _Boom(RuntimeError):
        pass

    with pytest.raises(_Boom):
        async with engine.materialized_member(session=None, member=member) as path:
            captured_path = path
            assert path.exists()
            raise _Boom("dj-process 失败")

    assert not captured_path.exists()


async def test_materialized_member_local_path_not_deleted(tmp_path):
    """本地路径(managed 存储):这是版本的真实数据文件,退出 async with 后

    必须原样保留——它不是本函数创建的临时产物,误删会破坏受管数据集。
    """
    from app.services import engine

    real_file = tmp_path / "data.jsonl"
    real_file.write_text('{"text": "x"}\n', encoding="utf-8")
    member = _make_member(str(real_file))

    async with engine.materialized_member(session=None, member=member) as path:
        assert path == real_file
        assert path.exists()

    assert real_file.exists()


async def test_materialized_member_cleanup_failure_only_warns(monkeypatch, caplog):
    """临时文件清理失败(如已被外部删除/权限问题)只 log warning,

    不得让清理异常掩盖或替代调用方本身的执行结果——任务的成功/失败判定
    不应被一次磁盘清理失败拖垮。
    """
    from pathlib import Path

    from app.services import engine

    async def fake_cached_bytes(cfg, bucket, key):
        return b"data"

    def fake_unlink(self, missing_ok=False):
        raise OSError("permission denied")

    monkeypatch.setattr(engine, "cached_bytes", fake_cached_bytes)
    monkeypatch.setattr(
        engine,
        "platform_config",
        lambda: {"endpoint": "e", "accessKey": "a", "secretKey": "s"},
    )
    monkeypatch.setattr(Path, "unlink", fake_unlink)

    member = _make_member("s3://uploads/dset-x/v1/data.jsonl")
    with caplog.at_level("WARNING"):
        async with engine.materialized_member(session=None, member=member) as path:
            assert path.exists()
    assert "清理物化成员临时文件失败" in caplog.text
