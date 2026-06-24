#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
docx_to_jsonl.py — 把 Word(.docx) 文档归一化成 JSONL（一行一条样本）。

用途：上传/治理任务的「格式归一化」前置步骤。
      Word 文档不能直接进 data-juicer 的结构化数据集，先转成 jsonl 再处理/入库。

依赖：python-docx   (pip install python-docx)

基本规则（与 data-juicer TextFormatter 一致）：
  - 逐段抽取正文段落文本，过滤空段
  - 不处理表格、图片、页眉页脚（版式有损，只取文本语料）
  - 输出 JSONL：一行一个 JSON 对象，中文不转义（ensure_ascii=False）

用法：
  # 单个文件，整篇文档 → 一行
  python docx_to_jsonl.py report.docx -o out.jsonl

  # 整个目录批量（递归找 .docx）
  python docx_to_jsonl.py ./docs -o out.jsonl

  # 按非空段落切成多行（一行一段，适合做句子级语料）
  python docx_to_jsonl.py report.docx -o out.jsonl --split-paragraphs

  # 自定义承载正文的字段名（默认 text，对接 data-juicer 的 text_keys）
  python docx_to_jsonl.py report.docx -o out.jsonl --text-key content
"""

import argparse
import glob
import json
import os
import sys

from docx import Document


def extract_paragraphs(docx_path: str) -> list[str]:
    """读取 docx，返回非空段落文本列表。"""
    doc = Document(docx_path)
    return [p.text.strip() for p in doc.paragraphs if p.text.strip()]


def build_samples(docx_path: str, text_key: str, split_paragraphs: bool) -> list[dict]:
    """把一个 docx 转成若干条 jsonl 样本。"""
    paragraphs = extract_paragraphs(docx_path)
    source = os.path.basename(docx_path)

    if split_paragraphs:
        # 一段一行
        return [{text_key: para, "source": source} for para in paragraphs]
    # 整篇一行（段落用 \n 连接）
    return [{text_key: "\n".join(paragraphs), "source": source}]


def iter_docx_files(path: str):
    """支持传单个文件或目录（递归 .docx）。"""
    if os.path.isdir(path):
        yield from sorted(glob.glob(os.path.join(path, "**", "*.docx"), recursive=True))
    elif os.path.isfile(path) and path.lower().endswith(".docx"):
        yield path
    else:
        print(f"[skip] 不是 .docx 也不是目录：{path}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description="Convert .docx to JSONL.")
    ap.add_argument("input", help="单个 .docx 文件或目录")
    ap.add_argument("-o", "--output", required=True, help="输出 jsonl 路径")
    ap.add_argument("--text-key", default="text", help="承载正文的字段名（默认 text）")
    ap.add_argument("--split-paragraphs", action="store_true",
                    help="按非空段落切成多行（默认整篇一行）")
    args = ap.parse_args()

    total = 0
    with open(args.output, "w", encoding="utf-8") as f:
        for docx_path in iter_docx_files(args.input):
            try:
                samples = build_samples(docx_path, args.text_key, args.split_paragraphs)
            except Exception as e:
                print(f"[error] {docx_path}: {e}", file=sys.stderr)
                continue
            for s in samples:
                # ensure_ascii=False：中文原样写出，不变成 \uXXXX
                f.write(json.dumps(s, ensure_ascii=False) + "\n")
                total += 1
            print(f"[ok] {docx_path} -> {len(samples)} 条")

    print(f"\n完成：共 {total} 条样本 -> {args.output}")


if __name__ == "__main__":
    main()
