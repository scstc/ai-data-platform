"""merge_records 纯函数单测:数据合并 merge 模式的按行拼接语义。

业务意图(需求示例):主数据 + 扩展行为两个 jsonl 按行号对齐,text 拼成
「主句。扩展句。」——每个片段以分隔符收尾,而非仅居中分隔。
"""

from app.services.make import merge_records


def test_merge_two_files_matches_requirement_example():
    """需求原例:两文件各 1 行,拼接后每段以「。」收尾。"""
    main = [{"text": "用户编号1001，VIP会员，已做手机号脱敏处理，2025年5月注册账号"}]
    ext = [{"text": "该用户发起七天无理由退货，商品无质量问题，未购买运费险"}]
    merged, warnings = merge_records(
        [("main.jsonl", main), ("ext.jsonl", ext)], "text", "。"
    )
    assert merged == [
        {
            "text": "用户编号1001，VIP会员，已做手机号脱敏处理，2025年5月注册账号。"
            "该用户发起七天无理由退货，商品无质量问题，未购买运费险。"
        }
    ]
    assert warnings == []


def test_output_keeps_primary_extra_fields_and_row_count():
    """产物 = 主文件的演进:行数、其余字段都沿用主文件,扩展文件只贡献拼接片段。"""
    main = [
        {"id": 1, "text": "主句A"},
        {"id": 2, "text": "主句B"},
    ]
    ext = [{"id": 99, "text": "扩展A", "extra": "x"}]
    merged, warnings = merge_records(
        [("main", main), ("ext", ext)], "text", "。"
    )
    assert [r["id"] for r in merged] == [1, 2]
    assert "extra" not in merged[0]
    assert merged[0]["text"] == "主句A。扩展A。"
    # 扩展文件短缺的行:主行原文保留(仍以分隔符收尾),并显式告警而非静默
    assert merged[1]["text"] == "主句B。"
    assert any("少 1 行" in w for w in warnings)


def test_extension_longer_than_primary_warns_dropped():
    main = [{"text": "A"}]
    ext = [{"text": "B"}, {"text": "C"}, {"text": "D"}]
    merged, warnings = merge_records([("main", main), ("ext", ext)], "text", "。")
    assert len(merged) == 1
    assert any("多 2 行" in w and "丢弃" in w for w in warnings)


def test_fragment_trailing_separator_not_doubled():
    """片段本身已带句号时不重复,避免「A。。B。」。"""
    main = [{"text": "主句。"}]
    ext = [{"text": "扩展。"}]
    merged, _ = merge_records([("main", main), ("ext", ext)], "text", "。")
    assert merged[0]["text"] == "主句。扩展。"


def test_non_terminal_separator_joins_without_tail():
    """空格等非句末分隔符只居中分隔,不补尾。"""
    main = [{"text": "hello"}]
    ext = [{"text": "world"}]
    merged, _ = merge_records([("main", main), ("ext", ext)], "text", " ")
    assert merged[0]["text"] == "hello world"


def test_missing_field_row_skipped_not_crash():
    """个别行缺字段/为 null:跳过该片段,不让整个任务崩。"""
    main = [{"text": "A"}, {"other": 1}]
    ext = [{"text": None}, {"text": "B"}]
    merged, _ = merge_records([("main", main), ("ext", ext)], "text", "。")
    assert merged[0]["text"] == "A。"
    assert merged[1]["text"] == "B。"


def test_key_field_matches_out_of_order_rows():
    """按 id 匹配:扩展文件顺序打乱,仍按 id 值对上正确的行,而非位置。"""
    main = [
        {"id": 1, "text": "主句A"},
        {"id": 2, "text": "主句B"},
    ]
    ext = [
        {"id": 2, "text": "扩展B"},  # 顺序颠倒
        {"id": 1, "text": "扩展A"},
    ]
    merged, warnings = merge_records(
        [("main", main), ("ext", ext)], "text", "。", key_field="id"
    )
    assert merged[0]["text"] == "主句A。扩展A。"
    assert merged[1]["text"] == "主句B。扩展B。"
    assert warnings == []


def test_key_field_missing_match_skips_fragment_and_warns():
    """扩展文件里找不到匹配 id 的行:该片段跳过,主文件片段仍保留,并 warning。"""
    main = [{"id": 1, "text": "A"}, {"id": 2, "text": "B"}]
    ext = [{"id": 1, "text": "扩展A"}]  # 没有 id=2 的行
    merged, warnings = merge_records(
        [("main", main), ("ext", ext)], "text", "。", key_field="id"
    )
    assert merged[0]["text"] == "A。扩展A。"
    assert merged[1]["text"] == "B。"
    assert any("未找到 1 行匹配" in w for w in warnings)


def test_key_field_missing_in_primary_row_warns():
    """主文件行本身缺键字段:该行不参与匹配(只保留自身片段),并 warning。"""
    main = [{"text": "A"}, {"id": 2, "text": "B"}]
    ext = [{"id": 2, "text": "扩展B"}]
    merged, warnings = merge_records(
        [("main", main), ("ext", ext)], "text", "。", key_field="id"
    )
    assert merged[0]["text"] == "A。"
    assert merged[1]["text"] == "B。扩展B。"
    assert any("缺少键字段" in w for w in warnings)


def test_key_field_duplicate_in_extension_warns_and_uses_first():
    """扩展文件里同一 id 出现两次:取首次出现的行参与匹配,并 warning。"""
    main = [{"id": 1, "text": "A"}]
    ext = [{"id": 1, "text": "第一条"}, {"id": 1, "text": "第二条"}]
    merged, warnings = merge_records(
        [("main", main), ("ext", ext)], "text", "。", key_field="id"
    )
    assert merged[0]["text"] == "A。第一条。"
    assert any("重复" in w for w in warnings)
