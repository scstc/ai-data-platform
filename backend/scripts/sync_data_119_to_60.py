"""
一次性数据迁移:把 10.60.1.119 的业务数据同步到 10.60.1.60。

行为:
  1. 在 60 上 TRUNCATE <业务表> RESTART IDENTITY CASCADE(顺序按 pg 内部)
  2. 按"父→子"应用层顺序从 119 SELECT,executemany 到 60
  3. audit_logs / alembic_version 完全不动
  4. 完成后比对两边行数

约束:
  - 跨库,需要 119 + 60 同时可达
  - adp 平台未用 DB 外键(0 FK),所以 truncate CASCADE 仍安全
  - RESTART IDENTITY 重置 sequence,避免后续 INSERT 撞主键
"""
import asyncio
import asyncpg
from typing import Sequence

SRC = "postgresql://adp:adp_dev_pw@10.60.1.119:55433/adp"
DST = "postgresql://adp:adp_dev_pw@10.60.1.60:55433/adp"

# 业务表(全部非 audit_logs/alembic_version)。
# 灌入顺序:先"被引用多的父表",后"引用多的子表"——按应用层关系排,虽然没 DB FK 也稳。
INSERT_ORDER: Sequence[str] = (
    "users",
    "categories",          # self-ref parent_id,后插子节点也行
    "tags",                # self-ref
    "llm_providers",
    "llm_models",          # -> llm_providers
    "datasources",         # -> users
    "datasets",            # -> users, categories
    "dataset_versions",    # -> datasets
    "dataset_tags",        # -> datasets, tags
    "ingest_tasks",        # -> datasets
    "upload_records",      # -> users
    "jobs",                # -> users
    "job_inputs",          # -> jobs, datasets
    "llm_usage",           # -> llm_models
    "review_findings",     # -> jobs
)


async def copy_table(src: asyncpg.Connection, dst: asyncpg.Connection, table: str) -> int:
    """SELECT * FROM src,executemany 到 dst。返回写入行数。"""
    rows = await src.fetch(f'SELECT * FROM "{table}"')
    if not rows:
        return 0
    cols = list(rows[0].keys())
    placeholders = ", ".join(f"${i+1}" for i in range(len(cols)))
    col_list = ", ".join(f'"{c}"' for c in cols)
    sql = f'INSERT INTO "{table}" ({col_list}) VALUES ({placeholders})'
    # asyncpg 不直接支持 executemany 跨 batch 优化,但几百行无所谓
    await dst.executemany(sql, [tuple(r[c] for c in cols) for r in rows])
    return len(rows)


async def main() -> None:
    src = await asyncpg.connect(SRC)
    dst = await asyncpg.connect(DST)

    # 1. Truncate 60 业务表
    table_list = ", ".join(f'"{t}"' for t in INSERT_ORDER)
    print(f"[truncate] {len(INSERT_ORDER)} tables on 60 ...")
    await dst.execute(f"TRUNCATE {table_list} RESTART IDENTITY CASCADE")
    print("  ok")

    # 2. 按顺序 copy
    print("[copy] 119 -> 60:")
    for t in INSERT_ORDER:
        n = await copy_table(src, dst, t)
        print(f"  {t:25s} {n:5d} rows")

    # 3. 验证:逐表行数对比
    print("[verify] row count diff:")
    for t in INSERT_ORDER:
        a = await src.fetchval(f'SELECT count(*) FROM "{t}"')
        b = await dst.fetchval(f'SELECT count(*) FROM "{t}"')
        flag = "OK" if a == b else "MISMATCH"
        print(f"  {t:25s} 119={a:5d}  60={b:5d}  {flag}")

    # 4. 确认 audit_logs 没动
    al_src = await src.fetchval("SELECT count(*) FROM audit_logs")
    al_dst = await dst.fetchval("SELECT count(*) FROM audit_logs")
    print(f"[verify] audit_logs 119={al_src}  60={al_dst}  (60 should be >= 191 +0)")

    await src.close()
    await dst.close()


if __name__ == "__main__":
    asyncio.run(main())
