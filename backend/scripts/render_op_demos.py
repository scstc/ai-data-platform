"""跑 DJ 算子处理 DJ 仓 tests/ops/data/ 里的 fixture 资源,输出 before/after 到 frontend/public/operator-demos/{op}/。

仅覆盖可在 CPU 上 1-shot 处理的 image / video / audio 类简单算子;
需要 LLM / GPU / Ray 集群的算子(vllm / ray_* / *_vlm / *_llm / captioning 等)列入 SKIP。

输出:
    frontend/public/operator-demos/{op_name}/before.{ext}
    frontend/public/operator-demos/{op_name}/after.{ext}
    + 后端可读的 demo.jsonl(给人工 / 后续脚本 import 用)

跑:cd backend && ./.venv/Scripts/python.exe scripts/render_op_demos.py
注:用 DJ venv 跑(Python 不能换)。脚本顶部会尝试 sys.path.insert 找 DJ 仓。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

# 让脚本可用 DJ 包(同机 DJ venv)
ROOT = Path(__file__).resolve().parents[2]
DJ_ROOT = ROOT / "data-juicer"
DJ_TESTS_DATA = DJ_ROOT / "tests" / "ops" / "data"
PUBLIC_DEMOS = ROOT / "frontend" / "public" / "operator-demos"

# 算子→fixture 映射表。只列能在 CPU 上 1-shot 跑通且有视觉/听觉差异的算子。
# 每项:type=image|video|audio,fixture=文件名,key=算子的输入字段名,op_args=构造参数
# filter 类(in-place,无 before/after 差异)和需 GPU/cv2 cascade 的算子不进此表。
TARGETS: list[dict[str, Any]] = [
    # === image mappers (CPU,纯图像处理) ===
    {"name": "image_blur_mapper", "type": "image", "fixture": "img1.png",
     "key": "images", "op_args": {"p": 1.0, "blur_type": "gaussian", "radius": 3}},
    # === audio mappers (CPU) ===
    {"name": "audio_add_gaussian_noise_mapper", "type": "audio", "fixture": "audio1.wav",
     "key": "audios", "op_args": {"snr": 20}},
    # === video mappers (CPU,ffmpeg/cv2 不需 cascade) ===
    {"name": "video_resize_resolution_mapper", "type": "video", "fixture": "video1.mp4",
     "key": "videos", "op_args": {"min_width": 160, "max_width": 320,
                                   "min_height": 120, "max_height": 240}},
    {"name": "video_resize_aspect_ratio_mapper", "type": "video", "fixture": "video1.mp4",
     "key": "videos", "op_args": {"min_ratio": 0.5, "max_ratio": 0.8}},
    {"name": "video_split_by_duration_mapper", "type": "video", "fixture": "video3.mp4",
     "key": "videos", "op_args": {"split_duration": 1.0}},
]

# 已跳过的算子(原因写在 SKIP 里,避免每次跑重试)
SKIP = {
    # GPU / 大模型
    "image_captioning_mapper": "需要 BLIP 等视觉模型(GPU)",
    "image_diffusion_mapper": "需要 Stable Diffusion(GPU)",
    "image_tagging_vlm_mapper": "需要 VLM(GPU/vLLM)",
    "image_segment_mapper": "需要 SAM(GPU)",
    "image_sam_3d_body_mapper": "需要 SAM 3D(GPU)",
    "image_mmpose_mapper": "需要 MMPose(GPU)",
    "image_detection_yolo_mapper": "需要 YOLO 模型",
    "image_remove_background_mapper": "需要 rembg 模型",
    "image_tagging_mapper": "需要 HF tagger 模型",
    "video_captioning_from_*_mapper": "需要 captioning 模型",
    "video_depth_estimation_mapper": "需要 depth 模型",
    "video_camera_pose_mapper": "需要 MegaSaM(GPU)",
    "video_hand_reconstruction_*_mapper": "需要 HaWoR/WiLoR(GPU)",
    "video_undistort_mapper": "需要相机内参",
    "video_remove_watermark_mapper": "需要 inpainting 模型",
    "imgdiff_difference_*_mapper": "需要两图对比",
    "mllm_mapper": "需要 MLLM(GPU)",
    "video_resize_*_mapper": "resize 单图不易展示效果",
    "audio_ffmpeg_wrapped_mapper": "需具体 filter 参数",
    # face_blur 类:opencv-python-headless 缺 cascade data(要装 opencv-python)
    "image_face_blur_mapper": "opencv-python-headless 缺 CascadeClassifier",
    "video_face_blur_mapper": "opencv-python-headless 缺 CascadeClassifier",
    # 无视觉差异(in-place)
    "image_aspect_ratio_filter": "filter in-place,无 before/after 差异",
    "image_shape_filter": "filter in-place,无 before/after 差异",
    "image_size_filter": "filter in-place,无 before/after 差异",
    "video_extract_frames_mapper": "只抽帧不做处理,无 before/after",
    "video_split_by_*_mapper": "只切片,无视觉处理",
}


def _ext(fixture: str) -> str:
    return Path(fixture).suffix.lower() or ".bin"


def _to_posix(p: str) -> str:
    return p.replace("\\", "/")


def _pick_output(out_dir: Path) -> Path | None:
    """DJ 把输出存到 save_dir,文件名形如 {name}__dj_hash_...{ext},取最长的作 after。"""
    files = [f for f in out_dir.iterdir() if f.is_file() and not f.name.startswith("before")]
    if not files:
        return None
    return max(files, key=lambda f: f.stat().st_mtime)


def _run_target(target: dict[str, Any]) -> dict[str, Any] | None:
    name = target["name"]
    fixture_name = target["fixture"]
    fixture = DJ_TESTS_DATA / fixture_name
    if not fixture.exists():
        return {"name": name, "status": "skip", "reason": f"fixture 缺失 {fixture}"}

    out_dir = PUBLIC_DEMOS / name
    out_dir.mkdir(parents=True, exist_ok=True)
    # 清旧产物(防多文件混淆)
    for f in out_dir.iterdir():
        if f.is_file():
            f.unlink()
    ext = _ext(fixture_name)
    before = out_dir / f"before{ext}"
    shutil.copy(fixture, before)

    # 动态 import 算子类
    op_module = f"data_juicer.ops.{target['type']}_mapper" if target["type"] in ("image", "video", "audio") and "filter" not in name else None
    if target["type"] == "image" and "filter" in name:
        op_module = "data_juicer.ops.filter"
    elif target["type"] == "image":
        op_module = "data_juicer.ops.mapper"
    elif target["type"] == "video":
        op_module = "data_juicer.ops.mapper"
    elif target["type"] == "audio":
        op_module = "data_juicer.ops.mapper"

    try:
        mod = __import__(op_module, fromlist=["*"])
        cls = getattr(mod, _camel(name))
    except Exception as e:
        return {"name": name, "status": "skip", "reason": f"import 失败: {e}"}

    from data_juicer.core.data import NestedDataset as Dataset

    try:
        op = cls(save_dir=_to_posix(str(out_dir)), **target["op_args"])
        ds = Dataset.from_list([{target["key"]: [_to_posix(str(fixture))]}])
        ds = ds.map(op.process, batch_size=1, num_proc=1)
        res = ds.to_list()
    except Exception as e:
        return {"name": name, "status": "fail", "reason": f"执行失败: {e}"}

    after_path = _pick_output(out_dir)
    if not after_path:
        return {"name": name, "status": "fail", "reason": "未生成 after 文件"}

    # 重命名 after → 简洁名(去掉 DJ 哈希)
    final_after = out_dir / f"after{ext}"
    if final_after.exists():
        final_after.unlink()
    shutil.move(str(after_path), final_after)

    return {
        "name": name,
        "status": "ok",
        "before_url": f"/operator-demos/{name}/before{ext}",
        "after_url": f"/operator-demos/{name}/after{ext}",
        "media_type": "video" if ext in (".mp4", ".webm") else (
            "audio" if ext in (".wav", ".ogg", ".mp3") else "image"
        ),
    }


def _camel(snake: str) -> str:
    """snake_case → CamelCase。"""
    return "".join(p.capitalize() for p in snake.split("_"))


def main() -> None:
    if not DJ_TESTS_DATA.is_dir():
        raise SystemExit(f"DJ 测试数据目录不存在: {DJ_TESTS_DATA}")
    PUBLIC_DEMOS.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    for t in TARGETS:
        r = _run_target(t)
        if r:
            results.append(r)
            status = r["status"]
            extra = r.get("reason") or r.get("after_url", "")
            print(f"  [{status:4s}] {t['name']:35s} {extra}")

    # 写 demo 记录(JSON,供后续 import)
    out = ROOT / "backend" / "scripts" / "_media_demos.json"
    out.write_text(
        json.dumps([r for r in results if r["status"] == "ok"],
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n生成 {sum(1 for r in results if r['status']=='ok')} 个 ok,"
          f"{sum(1 for r in results if r['status']=='skip')} 个 skip,"
          f"{sum(1 for r in results if r['status']=='fail')} 个 fail")
    print(f"资源落地: {PUBLIC_DEMOS}")
    print(f"记录: {out}")


if __name__ == "__main__":
    main()
