"""采集质量纯函数模块(切片 B / Task 2)。

供 land_records / rerun / generate-dataset 在落地后做结构化质量检查,
所有函数无副作用、无 I/O,便于单测。与 app/services/quality.py(LLM 质量
引擎,dj-analyze 子进程)是不同关注点,本模块**不导入**它,也不导入 DB。

契约:
- stats 列字段为 snake_case(name/type/null_rate)——与 Python 侧一致。
- policy dict 使用 snake_case 键(max_null_rate / block_on_schema_drift),
  与 QualityPolicy.model_dump() 一致;缺键视为未配置(.get 容错,适配 JSONB)。
- drift 的 added/removed 为列名字符串列表,type_changed 为 {name,from,to} 列表。
"""

from __future__ import annotations

from typing import Any

# Python 类型 → 展示标签(与 preview._TYPE_LABELS 等价,本模块独立维护避免耦合)。
# 注意:bool 是 int 子类,但 type(True) == bool,dict 按 key 精确匹配,故 bool
# 与 int 天然区分;dict 字面量里的书写顺序不影响 .get(type(v)) 的结果。
_TYPE_LABELS: dict[type, str] = {
    bool: "boolean",
    int: "integer",
    float: "float",
    str: "text",
}


def compute_quality_stats(records: list[dict]) -> dict:
    """由结构化记录计算质量统计。

    - rows = 记录数;空列表 → {"rows": 0, "columns": []}。
    - columns: 列序 = 跨记录首见序;type 由该列首个非空值推断
      (bool/int/float/str → boolean/integer/float/text),全空列兜底 text。
    - null_rate = (缺失 key / None / 空串) 计数 / 总行数。

    返回 ``{"rows": int, "columns": [{"name","type","null_rate"}]}``。
    """
    rows = len(records)
    if rows == 0:
        return {"rows": 0, "columns": []}

    # 1) 收集列首见序(不按字典序,保持源数据结构)。
    order: list[str] = []
    seen: set[str] = set()
    for r in records:
        for k in r:
            if k not in seen:
                seen.add(k)
                order.append(k)

    # 2) 各列推断类型 + 空值计数(单次遍历,首非空值定型)。
    columns: list[dict[str, Any]] = []
    for k in order:
        col_type: str | None = None
        null_count = 0
        for r in records:
            if k not in r or r[k] is None:
                null_count += 1
                continue
            v = r[k]
            # 空串视为 null(仅 str);零值(0/0.0/False)不计入。
            if isinstance(v, str) and v == "":
                null_count += 1
                continue
            if col_type is None:
                col_type = _TYPE_LABELS.get(type(v), "text")
        if col_type is None:
            col_type = "text"  # 全空列兜底
        columns.append(
            {
                "name": k,
                "type": col_type,
                "null_rate": null_count / rows,
            }
        )
    return {"rows": rows, "columns": columns}


def schema_snapshot(stats: dict) -> list[dict]:
    """由 compute_quality_stats 结果投影出可持久化用于漂移比对的精简快照。

    丢弃 null_rate(每次重算),只保留 name+type(列序保留)。
    """
    return [{"name": c["name"], "type": c["type"]} for c in stats.get("columns", [])]


def drift_diff(prev: list[dict] | None, curr: list[dict]) -> dict:
    """对比两个 schema 快照,返回漂移明细。

    - prev=None(首版)→ 三个桶全空(无漂移基线)。
    - 按列名比较:added = curr 独有列名;removed = prev 独有列名;
      type_changed = 同名但类型不同的 ``[{"name","from","to"}]``。
    """
    if prev is None:
        return {"added": [], "removed": [], "type_changed": []}

    prev_by_name: dict[str, Any] = {c["name"]: c.get("type") for c in prev}
    curr_by_name: dict[str, Any] = {c["name"]: c.get("type") for c in curr}

    added = [n for n in curr_by_name if n not in prev_by_name]
    removed = [n for n in prev_by_name if n not in curr_by_name]
    type_changed = [
        {"name": n, "from": prev_by_name[n], "to": curr_by_name[n]}
        for n in curr_by_name
        if n in prev_by_name and prev_by_name[n] != curr_by_name[n]
    ]
    return {"added": added, "removed": removed, "type_changed": type_changed}


def evaluate_policy(
    policy: dict | None,
    stats: dict,
    drift: dict | None,
) -> tuple[str, str | None]:
    """按任务级质量策略评估 verdict 与原因。

    分支顺序(短路,首个违例即返回):
    1. policy 为 None / {} → ``("skipped", None)``。
    2. 任一列 ``null_rate > max_null_rate``(严格 >, == 通过)
       → ``("failed", "{name} 空值率 {rate:.2f} 超阈值 {max:.2f}")``。
    3. ``block_on_schema_drift=True`` 且 drift 任一桶非空
       → ``("failed", "schema 漂移: +a -r ~t")``。
    4. 否则 → ``("passed", None)``。

    policy 使用 snake_case 键;缺键经 .get 容错视为未配置(适配 JSONB 来源)。
    """
    if not policy:
        return ("skipped", None)

    # 1) null 率阈值检查(严格 >, == 阈值视为合规)。
    max_null_rate = policy.get("max_null_rate")
    if max_null_rate is not None:
        for col in stats.get("columns", []):
            if col["null_rate"] > max_null_rate:
                return (
                    "failed",
                    f"{col['name']} 空值率 {col['null_rate']:.2f} "
                    f"超阈值 {max_null_rate:.2f}",
                )

    # 2) schema 漂移阻断。
    if policy.get("block_on_schema_drift") and drift:
        added = drift.get("added", [])
        removed = drift.get("removed", [])
        type_changed = drift.get("type_changed", [])
        if added or removed or type_changed:
            parts: list[str] = []
            if added:
                parts.append(f"+{','.join(added)}")
            if removed:
                parts.append(f"-{','.join(removed)}")
            if type_changed:
                parts.append(f"~{','.join(t['name'] for t in type_changed)}")
            return ("failed", f"schema 漂移: {' '.join(parts)}")

    return ("passed", None)
