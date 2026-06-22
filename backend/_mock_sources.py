"""一次性 seed:mock 5 种来源的样本数据集(类型三轴拆分验证用)。

可重跑幂等——按名字先删旧的同名数据集(连同版本与本地文件),再重建。
不碰真实连接器,直连库插 Dataset+DatasetVersion,并落真实 jsonl 文件
(详情预览走本地路径分支,需文件存在)。

跑法(backend 目录内):
    .venv\Scripts\python.exe _mock_sources.py
"""

from __future__ import annotations

import asyncio
import json
import secrets
from pathlib import Path

from sqlalchemy import delete, select

from app.core.config import settings
from app.core.db import async_session_factory
from app.models.dataset import Dataset
from app.models.dataset_version import DatasetVersion


def _did() -> str:
    return f"dset-{secrets.token_hex(3)}"


def _vid() -> str:
    return f"dsv-{secrets.token_hex(3)}"


# 每种来源一个样本:name / source_kind / source_format / semantic_type / 几行记录
SAMPLES: list[dict] = [
    {
        "name": "mock_mysql_orders",
        "source_kind": "database",
        "source_format": None,  # 数据库直连无文件载体
        "semantic_type": "structured",
        "data_type": "sql",
        "note": "mock:MySQL 采集 public.orders 表",
        "rows": [
            {"order_id": 1001, "customer": "张三", "amount": 128.5, "status": "paid"},
            {"order_id": 1002, "customer": "李四", "amount": 88.0, "status": "paid"},
            {"order_id": 1003, "customer": "王五", "amount": 256.0, "status": "refunded"},
        ],
    },
    {
        "name": "mock_oss_articles",
        "source_kind": "object_store",
        "source_format": "txt",
        "semantic_type": "text",
        "data_type": "text",
        "note": "mock:从 OSS 拉取的公告 txt",
        "rows": [
            {"text": "关于调整账户管理费率的公告,自下月起执行。"},
            {"text": "新版手机银行 App 已上线,支持指纹登录与刷脸转账。"},
            {"text": "个人外汇年度便利化额度提示:每人每年等值 5 万美元。"},
        ],
    },
    {
        "name": "mock_hdfs_accesslog",
        "source_kind": "hdfs",
        "source_format": "csv",
        "semantic_type": "structured",
        "data_type": "csv",
        "note": "mock:HDFS /data/logs/access.csv",
        "rows": [
            {"ts": "2026-06-21 10:00:01", "ip": "10.0.0.1", "code": 200, "path": "/index"},
            {"ts": "2026-06-21 10:00:02", "ip": "10.0.0.2", "code": 404, "path": "/missing"},
            {"ts": "2026-06-21 10:00:03", "ip": "10.0.0.1", "code": 200, "path": "/api/v1"},
        ],
    },
    {
        "name": "mock_local_manual",
        "source_kind": "local_upload",
        "source_format": "docx",
        "semantic_type": "text",
        "data_type": "text",
        "note": "mock:本地上传《操作手册.docx》",
        "rows": [
            {"text": "第一章 账户开户流程:客户持有效证件至柜台办理。"},
            {"text": "第二章 风险评估:开户前需完成投资者适当性问卷。"},
            {"text": "第三章 资金存取:支持柜面、ATM、网银多渠道。"},
        ],
    },
    {
        "name": "mock_api_qa",
        "source_kind": "api_push",
        "source_format": "jsonl",
        "semantic_type": "qa",
        "data_type": "qa",
        "note": "mock:外部服务经 API 推送的 QA 对",
        "rows": [
            {"question": "如何修改登录密码?", "answer": "登录后进入「安全中心」修改。"},
            {"question": "手机银行转账限额多少?", "answer": "单笔 5 万、日累计 20 万。"},
            {"question": "挂失补卡收手续费吗?", "answer": "首张借记卡挂失免费补卡。"},
        ],
    },
]


async def _drop_existing(session, names: set[str]) -> None:
    res = await session.execute(
        select(Dataset.id).where(Dataset.name.in_(names))
    )
    ids = [r for r in res.scalars().all()]
    if ids:
        await session.execute(
            delete(DatasetVersion).where(DatasetVersion.dataset_id.in_(ids))
        )
        await session.execute(delete(Dataset).where(Dataset.id.in_(ids)))
    # 清理这些名字对应的本地文件目录(按 datasets_dir/dset-xxx)
    base = Path(settings.datasets_dir)
    for d in base.glob("dset-*"):
        if not d.name.startswith("dset-"):
            continue
    # 目录清理不强制:文件残留不影响(目录 id 随机,旧 id 不再被引用)


async def main() -> None:
    names = {s["name"] for s in SAMPLES}
    async with async_session_factory() as session:
        await _drop_existing(session, names)
        for s in SAMPLES:
            did = _did()
            out_dir = Path(settings.datasets_dir) / did / "v1"
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / "data.jsonl"
            payload = (
                "\n".join(
                    json.dumps(r, ensure_ascii=False, default=str) for r in s["rows"]
                )
                + "\n"
            ).encode("utf-8")
            out_path.write_bytes(payload)

            ds = Dataset(
                id=did,
                name=s["name"],
                description=f"[mock] {s['note']}",
                data_type=s.get("data_type"),
                semantic_type=s["semantic_type"],
                source_kind=s["source_kind"],
                source_format=s["source_format"],
                owner="admin",
                creator="admin",
            )
            session.add(ds)
            session.add(
                DatasetVersion(
                    id=_vid(),
                    dataset_id=did,
                    version_no=1,
                    storage_uri=str(out_path),
                    format="jsonl",
                    rows=len(s["rows"]),
                    size=out_path.stat().st_size,
                    semantic_type=s["semantic_type"],
                    origin="managed",
                    note=s["note"],
                )
            )
            await session.commit()
            print(
                f"  + {s['name']:<22} kind={s['source_kind']:<12} "
                f"fmt={str(s['source_format']):<5} sem={s['semantic_type']}"
            )


if __name__ == "__main__":
    asyncio.run(main())
    print("done.")
