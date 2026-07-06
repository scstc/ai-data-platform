"""湖抽取字段映射单测(纯函数,不依赖 DB)。

验证意图:表格类快照抽取时,用户可把源列映射成任意输出字段集合
(如 {"id": ..., "text": ...}),裁列后只保留输出字段 + meta 血缘,
且模板渲染是纯文本替换——含引号/非法占位符不会崩、不会被求值。
"""

from app.services.lake_extract import (
    _apply_field_mapping_transform,
    _render_template,
)


def test_multi_field_mapping_outputs_and_trims():
    """多输出字段:产出 id+text,丢原始源列,保留 meta 血缘。"""
    records = [
        {
            "order_id": "A001",
            "question": "余额怎么查",
            "answer": "打开APP首页",
            "category": "查询",
            "meta": {"src": "t.tsv", "version": "v1"},
        }
    ]
    mapping = {"id": "{order_id}", "text": "问:{question} 答:{answer}"}

    result = _apply_field_mapping_transform(records, mapping)

    assert result == [
        {
            "id": "A001",
            "text": "问:余额怎么查 答:打开APP首页",
            "meta": {"src": "t.tsv", "version": "v1"},
        }
    ]


def test_missing_column_and_none_render_empty():
    """缺列与 None 值渲染为空串,不产生字面 'None'。"""
    records = [{"a": None, "meta": {}}]
    result = _apply_field_mapping_transform(records, {"text": "[{a}][{b}]"})
    assert result[0]["text"] == "[][]"


def test_empty_mapping_keeps_records():
    """空映射不改动记录(保留全部原始列)。"""
    records = [{"a": 1, "meta": {}}]
    assert _apply_field_mapping_transform(records, {}) is records


def test_template_is_plain_substitution_not_eval():
    """模板是纯文本替换:引号原样保留,非 {\\w+} 占位符不求值不替换。"""
    row = {"q": "it's \"fine\""}
    assert _render_template("say {q}", row) == 'say it\'s "fine"'
    # 含点号/调用的花括号不匹配占位符语法 → 原样保留,绝不执行
    dangerous = '{__import__("os").system("x")} {a.b}'
    assert _render_template(dangerous, row) == dangerous


def test_numeric_values_stringified():
    """数值列渲染为字符串拼进模板。"""
    result = _apply_field_mapping_transform(
        [{"amount": 3.5, "meta": {}}], {"text": "金额{amount}元"}
    )
    assert result[0]["text"] == "金额3.5元"
