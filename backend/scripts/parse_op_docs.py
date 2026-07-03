"""从 data-juicer/docs/operators/**/*.md 解析 example / effect_demo,输出 JSON。

效果演示节结构(以 chinese_convert_mapper.md 为例):
    ## 📊 Effect demonstration 效果演示
    ### test_s2t
    ```python
    ChineseConvertMapper('s2t')   ← example
    ```
    #### 📥 input data 输入数据
    <pre>...sample 1 text...</pre><pre>...sample 2 text...</pre>
    #### 📤 output data 输出数据
    <pre>...sample 1 text...</pre><pre>...sample 2 text...</pre>

输出:backend/scripts/_op_demos.json(供 import_effect_demos.py 消费)。

含图片/视频 markdown(`![alt](url)`)或 HTML(`<img src=url>` / `<video src=url>`)时,
下载到 frontend/public/operator-demos/{op_name}/{sha1}.{ext},effect_demo 内 url
替换为 /operator-demos/{op_name}/{sha1}.{ext}——前端静态服务稳定可读。
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import urllib.request
from pathlib import Path

# 路径
ROOT = Path(__file__).resolve().parents[2]  # ai-data-platform/
DJ_DOCS = ROOT / "data-juicer" / "docs" / "operators"
PUBLIC_DEMOS = ROOT / "frontend" / "public" / "operator-demos"
OUT_JSON = Path(__file__).resolve().parent / "_op_demos.json"

# 解析 pattern
SECTION_HEADER = re.compile(r"^##\s*📊\s*Effect demonstration", re.M)
SUBSECTION = re.compile(r"^###\s+(.+)$", re.M)
PY_FENCE = re.compile(r"```python\s*\n(.+?)\n```", re.S)
PRE_BLOCK = re.compile(r"<pre[^>]*>(.+?)</pre>", re.S)
INPUT_HEADER = re.compile(r"####\s*📥\s*input data", re.I)
OUTPUT_HEADER = re.compile(r"####\s*📤\s*output data", re.I)

# media: ![alt](url) 或 <img src="url"> / <video src="url">
MD_MEDIA = re.compile(r"!\[([^\]]*)\]\((https?://[^)\s]+)\)")
HTML_MEDIA = re.compile(
    r'<(?:img|video)[^>]*?\bsrc=["\'](https?://[^"\']+)["\']',
    re.I,
)
MEDIA_EXTS = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
}


def _local_media_path(url: str, op_name: str) -> str:
    """下载到 public/operator-demos/{op}/{sha1}.{ext},返回 web 路径(以 / 开头)。"""
    ext_match = re.search(r"\.(png|jpg|jpeg|gif|webp|mp4|webm)(?:\?|$)", url, re.I)
    ext = "." + ext_match.group(1).lower() if ext_match else ".bin"
    digest = hashlib.sha1(url.encode()).hexdigest()[:12]
    target_dir = PUBLIC_DEMOS / op_name
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{digest}{ext}"
    if not target.exists():
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                target.write_bytes(r.read())
        except Exception as e:
            print(f"  ⚠️ 下载失败 {url}: {e}", file=sys.stderr)
            return url
    return f"/operator-demos/{op_name}/{target.name}"


def _inline_media(html: str, op_name: str) -> tuple[str, list[str]]:
    """把 html 内的 media URL 替换为本地路径,返回(替换后 html, 本地路径列表)。"""
    urls: list[str] = []
    for m in HTML_MEDIA.finditer(html):
        url = m.group(1)
        local = _local_media_path(url, op_name)
        urls.append(local)
        html = html.replace(url, local)
    for m in MD_MEDIA.finditer(html):
        url = m.group(2)
        local = _local_media_path(url, op_name)
        urls.append(local)
        html = html.replace(url, local)
    return html, urls


def _strip_html(s: str) -> str:
    """HTML 实体反转义 + 标签剥除。"""
    s = re.sub(r"<[^>]+>", "", s)
    s = (
        s.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#x27;", "'")
        .replace("&#39;", "'")
        .replace("&nbsp;", " ")
    )
    return s.strip()


def _extract_pre_texts(section: str) -> list[str]:
    """从一个 ## 子节文本里抽所有 <pre>...</pre> 的纯文本。"""
    return [_strip_html(p) for p in PRE_BLOCK.findall(section)]


def parse_one(md_path: Path) -> dict | None:
    op_name = md_path.stem
    text = md_path.read_text(encoding="utf-8")

    # 找 Effect demonstration 节
    sec_match = SECTION_HEADER.search(text)
    if not sec_match:
        return None
    start = sec_match.end()
    # 切到下一个 ## 标题前
    rest = text[start:]
    nxt = re.search(r"^##\s", rest, re.M)
    section = rest[: nxt.start()] if nxt else rest

    # 找第一个 python 代码块 = example
    py_match = PY_FENCE.search(section)
    if not py_match:
        return None
    example = py_match.group(1).strip()

    # 把整个 section 的 media 一次性本地化(主要在 input/output 块里)
    section_local, media_urls = _inline_media(section, op_name)

    # 找 ### test_xxx 子节 → 每子节一组 input/output 对
    demos: list[dict] = []
    parts = SUBSECTION.split(section_local)
    # parts[0] 是第一个 ### 之前的杂质文本(可能是 ## 标题到第一个 ### 之间)
    # 之后 [title, body, title, body, ...]
    for i in range(1, len(parts), 2):
        body = parts[i + 1] if i + 1 < len(parts) else ""
        # 在子节 body 里分 input / output
        in_m = INPUT_HEADER.search(body)
        out_m = OUTPUT_HEADER.search(body)
        if not in_m or not out_m:
            continue
        in_start = in_m.end()
        out_start = out_m.end()
        in_end = out_m.start()
        inputs = _extract_pre_texts(body[in_start:in_end])
        outputs = _extract_pre_texts(body[out_start:])
        for before, after in zip(inputs, outputs):
            demos.append({"before": before, "after": after})

    return {
        "name": op_name,
        "example": example,
        "demos": demos,
        "media_local_urls": media_urls,
    }


def main() -> None:
    if not DJ_DOCS.is_dir():
        raise SystemExit(f"DJ docs 目录不存在: {DJ_DOCS}")
    rows: list[dict] = []
    for md in sorted(DJ_DOCS.rglob("*.md")):
        parsed = parse_one(md)
        if parsed:
            rows.append(parsed)
    OUT_JSON.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    with_demos = sum(1 for r in rows if r["demos"])
    with_media = sum(1 for r in rows if r["media_local_urls"])
    print(
        f"解析 {len(rows)} 个算子 .md → {OUT_JSON.name}\n"
        f"  有 demos: {with_demos}\n"
        f"  含 media(已本地化): {with_media}"
    )


if __name__ == "__main__":
    main()
