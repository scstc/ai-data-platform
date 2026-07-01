"""自定义算子上传:静态校验上传的 data-juicer 算子源码(AST,不执行)。

data-juicer 本身通过 ``load_custom_operators``(见 data-juicer/data_juicer/config/
config.py)动态加载算子文件——用 importlib 执行整个模块,使 ``@OPERATORS.
register_module`` 装饰器生效注册进全局 ``OPERATORS`` 注册表,过程无沙箱。本模块只
做上传时的结构性静态校验(单类继承合法基类 + 装饰器齐全 + import/属性黑名单),
**不是安全沙箱**——放行的代码在任务真正执行时仍以 dj-process 子进程权限跑,黑名单
只挡常见误用/明显恶意模式,挡不住刻意绕过。生产环境如需强隔离,应在此基础上叠加
容器级隔离(见 docs)。
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass

# data-juicer 支持在 process 流水线里链式编排的算子基类(排除 Formatter——数据集
# 加载阶段的概念,不参与 process 链,与本功能的"上传一个可编排算子"场景不符)。
_ALLOWED_BASE_CLASSES = {"Mapper", "Filter", "Deduplicator", "Selector"}

_CATEGORY_BY_BASE = {
    "Mapper": "mapper",
    "Filter": "filter",
    "Deduplicator": "deduplicator",
    "Selector": "selector",
}

# import 黑名单(按根模块名):明显越权/逃逸能力,静态挡掉常见误用。
_DENIED_IMPORT_ROOTS = {
    "os",
    "sys",
    "subprocess",
    "socket",
    "shutil",
    "ctypes",
    "pickle",
    "multiprocessing",
    "importlib",
    "pty",
    "pdb",
    "code",
    "resource",
    "signal",
}

# 危险内建调用
_DENIED_CALL_NAMES = {"eval", "exec", "compile", "__import__", "open"}

# 常见沙箱逃逸 gadget 属性名(其余 dunder 如 __init__/__call__/__class__ 放行)
_DENIED_ATTRS = {
    "__subclasses__",
    "__bases__",
    "__globals__",
    "__builtins__",
    "__loader__",
    "__spec__",
    "__mro__",
}

MAX_SOURCE_BYTES = 256 * 1024


class CustomOperatorError(ValueError):
    """上传的算子源码未通过静态校验。"""


@dataclass
class CustomOperatorInfo:
    op_name: str
    category: str
    class_name: str


def _base_name(node: ast.expr) -> str | None:
    """取基类表达式的末段标识符(``Mapper`` 或 ``base_op.Mapper`` 均取 ``Mapper``)。"""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _register_module_name(decorator: ast.expr) -> str | None:
    """从 ``@OPERATORS.register_module("xxx")`` 装饰器提取算子名字符串字面量。"""
    if not isinstance(decorator, ast.Call):
        return None
    func = decorator.func
    is_attr_call = isinstance(func, ast.Attribute) and func.attr == "register_module"
    is_name_call = isinstance(func, ast.Name) and func.id == "register_module"
    is_register_call = is_attr_call or is_name_call
    if not is_register_call:
        return None
    if decorator.args:
        arg = decorator.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
    for kw in decorator.keywords:
        if kw.arg == "name" and isinstance(kw.value, ast.Constant) and isinstance(
            kw.value.value, str
        ):
            return kw.value.value
    return None


def _check_denylist(tree: ast.Module) -> None:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in _DENIED_IMPORT_ROOTS:
                    raise CustomOperatorError(f"禁止导入模块:{alias.name}")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in _DENIED_IMPORT_ROOTS:
                raise CustomOperatorError(f"禁止导入模块:{node.module}")
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else None
            if name in _DENIED_CALL_NAMES:
                raise CustomOperatorError(f"禁止调用:{name}")
        elif isinstance(node, ast.Attribute):
            if node.attr in _DENIED_ATTRS:
                raise CustomOperatorError(f"禁止访问属性:{node.attr}")


def parse_custom_operator(source: str) -> CustomOperatorInfo:
    """静态解析并校验一个自定义算子源文件,返回注册名 / 类别 / 类名。

    要求:恰好一个类继承 Mapper/Filter/Deduplicator/Selector 之一,且该类带
    ``@OPERATORS.register_module("op_name")`` 装饰器(字符串字面量算子名)。
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise CustomOperatorError(f"Python 语法错误:{exc}") from exc

    _check_denylist(tree)

    candidates: list[tuple[ast.ClassDef, str]] = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        base_names = {b for b in (_base_name(base) for base in node.bases) if b}
        matched = base_names & _ALLOWED_BASE_CLASSES
        if matched:
            candidates.append((node, next(iter(matched))))

    if not candidates:
        raise CustomOperatorError(
            "未找到继承 Mapper/Filter/Deduplicator/Selector 的算子类"
        )
    if len(candidates) > 1:
        raise CustomOperatorError("一个文件只能定义一个算子类")

    cls_node, base = candidates[0]
    op_name: str | None = None
    for decorator in cls_node.decorator_list:
        op_name = _register_module_name(decorator)
        if op_name:
            break
    if not op_name:
        raise CustomOperatorError(
            '算子类缺少 @OPERATORS.register_module("算子名") 装饰器'
        )

    return CustomOperatorInfo(
        op_name=op_name,
        category=_CATEGORY_BY_BASE[base],
        class_name=cls_node.name,
    )


_ALLOWED_PARAM_TYPES = {"str", "int", "float", "bool"}
MAX_PARAMS = 30


def parse_params_json(raw: str) -> list[dict[str, str]]:
    """解析上传表单里的算子参数表(JSON 字符串)→ 校验后的参数列表。

    形状须与内置算子的 ``params`` 列一致(供 ``operator_catalog._ui_field`` 按
    ``type`` 里的 "bool"/"int"/"float" 子串生成表单控件),每项 ``name`` 必填、
    ``type`` 限定四选一,多余键忽略。
    """
    try:
        items = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CustomOperatorError(f"参数表不是合法 JSON:{exc}") from exc
    if not isinstance(items, list):
        raise CustomOperatorError("参数表须是数组")
    if len(items) > MAX_PARAMS:
        raise CustomOperatorError(f"参数最多 {MAX_PARAMS} 个")

    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise CustomOperatorError(f"第 {i + 1} 个参数不是对象")
        name = str(item.get("name") or "").strip()
        if not name:
            raise CustomOperatorError(f"第 {i + 1} 个参数缺少 name")
        if name in seen:
            raise CustomOperatorError(f"参数名重复:{name}")
        seen.add(name)
        ptype = str(item.get("type") or "str").strip()
        if ptype not in _ALLOWED_PARAM_TYPES:
            raise CustomOperatorError(
                f"参数「{name}」类型非法:{ptype}(须为 str/int/float/bool 之一)"
            )
        result.append(
            {
                "name": name,
                "type": ptype,
                "default": str(item.get("default") or ""),
                "desc": str(item.get("desc") or ""),
            }
        )
    return result
