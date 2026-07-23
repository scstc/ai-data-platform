"""list 型算子参数的字符串反解析(_coerce_list_params)。

动态表单把 list 型参数渲染成文本框,用户填 '["a","b"]' 提交后是字符串;
不还原成列表时 DJ 侧 `in` 判断退化为子串匹配,过滤结果静默出错。
纯函数,不依赖 DB / dj-process。
"""

from __future__ import annotations

from app.services.engine import _coerce_list_param, _coerce_list_params

_LIST_T = "<class 'list'>"
_UNION_T = "typing.Union[str, typing.List[str]]"


def test_json_array_string_becomes_list() -> None:
    assert _coerce_list_param('["电子银行","账户管理"]', _LIST_T) == [
        "电子银行",
        "账户管理",
    ]


def test_python_literal_single_quotes_accepted() -> None:
    assert _coerce_list_param("['a', 'b']", _LIST_T) == ["a", "b"]


def test_comma_and_fullwidth_comma_fallback() -> None:
    assert _coerce_list_param("电子银行,账户管理", _LIST_T) == ["电子银行", "账户管理"]
    assert _coerce_list_param("电子银行，账户管理", _LIST_T) == ["电子银行", "账户管理"]


def test_bare_value_wrapped_for_pure_list_type() -> None:
    assert _coerce_list_param("电子银行", _LIST_T) == ["电子银行"]


def test_union_str_type_keeps_bare_string() -> None:
    # Union[str, List[str]]:裸字符串本身合法,不能替用户做主
    assert _coerce_list_param("some_value", _UNION_T) is None
    # 但显式写成数组仍应还原
    assert _coerce_list_param('["a","b"]', _UNION_T) == ["a", "b"]


def test_broken_bracket_syntax_passes_through() -> None:
    assert _coerce_list_param('["a", missing_quote]', _LIST_T) is None


def test_blank_string_untouched() -> None:
    assert _coerce_list_param("   ", _LIST_T) is None


def test_coerce_params_in_place_only_list_typed() -> None:
    params = {
        "target_value": '["电子银行","账户管理"]',
        "field_key": "category",
        "already_list": ["x"],
    }
    meta_params = [
        {"name": "target_value", "type": _LIST_T},
        {"name": "field_key", "type": "<class 'str'>"},
        {"name": "already_list", "type": _LIST_T},
    ]
    _coerce_list_params(params, meta_params)
    assert params["target_value"] == ["电子银行", "账户管理"]
    assert params["field_key"] == "category"  # str 型不动
    assert params["already_list"] == ["x"]  # 已是列表不动


def test_coerce_params_tolerates_malformed_meta() -> None:
    params = {"k": "[1,2]"}
    _coerce_list_params(params, [None, {"type": _LIST_T}, {"name": "k"}])
    assert params["k"] == "[1,2]"  # meta 缺 type/name 时保持原样
