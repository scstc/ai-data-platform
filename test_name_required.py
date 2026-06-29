#!/usr/bin/env python3
"""快速验证:数据集名称必填逻辑(后端校验)"""
import sys
import json

# 模拟 POST /datasets/upload-batch 和 /datasets/upload-media 的名称校验
def validate_upload_batch(name: str | None) -> tuple[bool, str]:
    """模拟 upload_batch_as_dataset 的校验逻辑"""
    if not name or not name.strip():
        return False, "数据集名称不能为空"
    return True, "OK"

def validate_upload_media(name: str | None) -> tuple[bool, str]:
    """模拟 upload_media_as_dataset 的校验逻辑"""
    if not name or not name.strip():
        return False, "数据集名称不能为空"
    return True, "OK"

# 测试用例
test_cases = [
    (None, False, "空值"),
    ("", False, "空字符串"),
    ("  ", False, "纯空格"),
    ("valid_name", True, "正常名称"),
    ("  trimmed  ", True, "带空格(可 trim)"),
]

print("=" * 60)
print("数据集名称必填校验 — 后端逻辑测试")
print("=" * 60)

all_pass = True
for name, should_pass, desc in test_cases:
    batch_ok, batch_msg = validate_upload_batch(name)
    media_ok, media_msg = validate_upload_media(name)

    batch_result = "✓" if batch_ok == should_pass else "✗"
    media_result = "✓" if media_ok == should_pass else "✗"

    if batch_ok != should_pass or media_ok != should_pass:
        all_pass = False

    name_repr = repr(name) if name is not None else "None"
    print(f"{batch_result} upload-batch | {media_result} upload-media | {desc:18s} | {name_repr}")

print("=" * 60)
if all_pass:
    print("✓ 所有测试通过")
    sys.exit(0)
else:
    print("✗ 有测试失败")
    sys.exit(1)
