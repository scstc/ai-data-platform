"""本地模型仓库：根路径配置缓存 + 实时扫描。

设计（与 llm_config 同构）：
- 根路径存 system_settings（key=dj_model_home），模块级缓存供 engine 同步无 I/O
  读取；启动时 / 修改后由 refresh_cache 刷新。
- 模型清单**不落库**——目录即事实源，每次扫描实时比对，避免库与磁盘漂移。
- 期望清单从算子目录推导：params 中含 "hf_" 且含 "model" 的字符串默认值
  （形如 org/name 的 HF repo id）即该算子的本地模型需求；另有一组按 lang
  参数运行期解析的单文件资产（fasttext/kenlm/sentencepiece/spacy），静态列出。

DJ 侧解析规则（data_juicer/utils/model_utils.py check_model_home/check_model）：
HF 模型命中 <root>/<repo_id> 目录、单文件资产命中 <root>/<文件名> 即完全离线。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

MODEL_HOME_KEY = "dj_model_home"

# 模块级根路径缓存；None 表示未配置
_model_home: str | None = None

# api_model / api_or_hf_model 默认值是远端 API 模型名，不属于本地模型需求
_EXCLUDED_PARAMS = {"api_model", "api_or_hf_model"}

# 算子源码内置默认模型：目录里参数默认值为空、回退值硬编码在 DJ 算子源码中,
# 无法从 params 推导，静态维护（repo_id -> 使用算子）
_CODE_DEFAULT_MODELS: dict[str, list[str]] = {
    "openai/clip-vit-base-patch32": [
        "image_pair_similarity_filter",
        "image_text_similarity_filter",
        "text_pair_similarity_filter",
        "video_frames_text_similarity_filter",
    ],
    "Salesforce/blip-itm-base-coco": [
        "image_text_matching_filter",
        "detect_character_attributes_mapper",
        "detect_character_locations_mapper",
    ],
    "google/owlvit-base-patch32": ["phrase_grounding_recall_filter"],
    "shunk031/aesthetics-predictor-v2-sac-logos-ava1-l14-linearMSE": [
        "image_aesthetics_filter",
        "video_aesthetics_filter",
    ],
    "Salesforce/blip2-opt-2.7b": [
        "image_captioning_mapper",
        "image_diffusion_mapper",
        "video_captioning_from_frames_mapper",
    ],
}

# 本地大模型（7B 级生成/VLM，需独立分组展示;与 deploy/download_dj_models.py
# 的 llm 组一致）
_LLM_MODELS = {
    "Qwen/Qwen2.5-7B-Instruct",
    "Qwen/Qwen2-7B-Instruct",
    "Qwen/Qwen2.5-VL-7B-Instruct",
    "Qwen/Qwen3-VL-8B-Instruct",
    "alibaba-pai/pai-qwen1_5-7b-doc2qa",
    "llava-hf/llava-v1.6-vicuna-7b-hf",
}

# 按 lang 参数运行期解析的单文件资产（无法从 params 默认值推导，静态维护;
# 文件名与 deploy/download_dj_models.py 的 text 组一致）
_ASSET_FILES: list[dict[str, str]] = [
    {"id": "lid.176.bin", "note": "fasttext 语言识别（language_id_score_filter 等）"},
    {"id": "en.arpa.bin", "note": "kenlm 英文语言模型（perplexity_filter 等）"},
    {"id": "en.sp.model", "note": "sentencepiece 英文分词"},
    {"id": "zh.arpa.bin", "note": "kenlm 中文语言模型"},
    {"id": "zh.sp.model", "note": "sentencepiece 中文分词"},
    {"id": "en_core_web_md-3.7.0.tar.gz", "note": "spacy 英文（词性/多样性类）"},
    {"id": "zh_core_web_md-3.7.0.tar.gz", "note": "spacy 中文"},
    # 视觉单文件权重(非 HF;DJ check_model 按文件名查 DJEMH 根目录)
    {
        "id": "ram_plus_swin_large_14m.pth",
        "note": "RAM 图像打标（image/video_tagging_from_frames）",
        "group": "vision",
    },
    {
        "id": "yolo11n.pt",
        "note": "YOLO 目标检测（image_detection_yolo_mapper）",
        "group": "vision",
    },
    {
        "id": "FastSAM-x.pt",
        "note": "FastSAM 图像分割（image_segment_mapper）",
        "group": "vision",
    },
    {
        "id": "yolox_l.onnx",
        "note": "DWPose 人体检测（video_whole_body_pose_estimation_mapper）",
        "group": "vision",
    },
    {
        "id": "dw-ll_ucoco_384.onnx",
        "note": "DWPose 姿态估计（video_whole_body_pose_estimation_mapper）",
        "group": "vision",
    },
]


def get_model_home() -> str | None:
    """同步读取模型仓库根路径（无 I/O，供 engine 组装子进程 env）。"""
    return _model_home


def set_model_home_cache(path: str | None) -> None:
    """写入根路径缓存（保存配置后调用）。"""
    global _model_home
    _model_home = path or None


async def refresh_cache(session: AsyncSession) -> None:
    """从 system_settings 读取根路径刷新缓存（启动时调用）。"""
    from app.models.system_setting import SystemSetting  # 延迟导入避免循环

    row = await session.get(SystemSetting, MODEL_HOME_KEY)
    set_model_home_cache(row.value if row else None)


async def save_model_home(session: AsyncSession, path: str) -> None:
    """落库并刷新缓存。空串表示清除配置。"""
    from app.models.system_setting import SystemSetting

    value = path.strip() or None
    row = await session.get(SystemSetting, MODEL_HOME_KEY)
    if row is None:
        row = SystemSetting(key=MODEL_HOME_KEY, value=value)
        session.add(row)
    else:
        row.value = value
    await session.commit()
    set_model_home_cache(value)


# ---------------------------------------------------------------------------
# 期望模型清单（从算子目录推导）与目录扫描
# ---------------------------------------------------------------------------

def _is_local_model_param(name: str) -> bool:
    """参数是否为本地模型参数（hf_model / vggt_model_path / model_name…）。

    只按参数名粗筛（含 model 且非 API 参数），是否真为 HF 模型由默认值形态
    （_looks_like_hf_repo）决定——MoGe/VGGT 等参数名不带 hf_ 前缀。
    """
    return "model" in name and name not in _EXCLUDED_PARAMS


def _looks_like_hf_repo(value: str) -> bool:
    """默认值是否形如 HF repo id（org/name，非文件路径/URL）。"""
    if "://" in value or value.count("/") != 1 or value.startswith((".", "/")):
        return False
    return not value.lower().endswith(
        (".pt", ".pth", ".onnx", ".bin", ".ckpt", ".yaml", ".json", ".model")
    )


def expected_hf_models() -> dict[str, dict[str, Any]]:
    """期望的 HF 模型集合：repo_id -> {used_by, params, modalities}。"""
    from app.services import operator_catalog as oc

    op_modality = {
        op["name"]: set(op.get("modality") or []) for op in oc.all_operators()
    }
    out: dict[str, dict[str, Any]] = {}

    def _add(repo_id: str, op_name: str, param: str | None) -> None:
        item = out.setdefault(
            repo_id, {"used_by": set(), "params": set(), "modalities": set()}
        )
        item["used_by"].add(op_name)
        if param:
            item["params"].add(param)
        item["modalities"] |= op_modality.get(op_name, set())

    for op in oc.all_operators():
        for p in op.get("params") or []:
            name = p.get("name") or ""
            default = p.get("default")
            if not _is_local_model_param(name):
                continue
            # HF repo id 形如 org/name；剥掉目录里 repr 化的引号
            if isinstance(default, str):
                default = default.strip("'\"")
            if not (isinstance(default, str) and _looks_like_hf_repo(default)):
                continue
            _add(default, op["name"], name)
    for repo_id, ops in _CODE_DEFAULT_MODELS.items():
        for op_name in ops:
            _add(repo_id, op_name, None)
    return out


_MEDIA_MODALITIES = {"image", "video", "audio", "multimodal"}


def _group_for(repo_id: str, kind: str, used_by: list[str], modalities: set) -> str:
    """模型分组：asset 基础资产 / llm 本地大模型 / vision 视觉多模态 /
    text 文本 / extra 目录额外模型。"""
    if kind == "file":
        return "asset"
    if repo_id in _LLM_MODELS:
        return "llm"
    if not used_by:
        return "extra"
    if modalities & _MEDIA_MODALITIES:
        return "vision"
    return "text"


def _dir_size(path: Path) -> int:
    try:
        return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    except OSError:
        return 0


def _hf_model_present(root: Path, repo_id: str) -> bool:
    """DJ check_model_home 口径：目录存在即命中；再验有配置或权重文件防空壳。

    HF 下载先落 config 等小文件、权重大分片后到，中断会留 ``*.incomplete``
    （huggingface_hub 的 ``.cache/huggingface/download/`` 残留）——存在即视为
    未就位，避免把下载中/下载失败的模型误判为可用。
    """
    d = root / repo_id
    if not d.is_dir():
        return False
    cache = d / ".cache" / "huggingface"
    if cache.is_dir() and any(cache.rglob("*.incomplete")):
        return False
    if (d / "config.json").exists() or (d / "tokenizer_config.json").exists():
        return True
    # 无 config 的裸权重仓库(如 Ruicheng/moge-2-vitl 仅 model.pt)
    weight_exts = ("*.safetensors", "*.bin", "*.pt", "*.pth", "*.onnx", "*.ckpt")
    return any(any(d.glob(pat)) for pat in weight_exts)


def scan_models() -> dict[str, Any]:
    """扫描配置目录，返回期望/额外模型的就位状态（实时，不落库）。"""
    home = get_model_home()
    root = Path(home) if home else None
    root_ok = bool(root and root.is_dir())

    models: list[dict[str, Any]] = []
    known_ids: set[str] = set()

    for repo_id, meta in sorted(expected_hf_models().items()):
        known_ids.add(repo_id)
        present = root_ok and _hf_model_present(root, repo_id)
        used_by = sorted(meta["used_by"])
        models.append(
            {
                "id": repo_id,
                "kind": "hf",
                "present": present,
                "size_bytes": _dir_size(root / repo_id) if present else 0,
                "used_by": used_by,
                "params": sorted(meta["params"]),
                "note": None,
                "group": _group_for(repo_id, "hf", used_by, meta["modalities"]),
            }
        )

    for asset in _ASSET_FILES:
        f = root / asset["id"] if root_ok else None
        present = bool(f and f.is_file() and f.stat().st_size > 0)
        models.append(
            {
                "id": asset["id"],
                "kind": "file",
                "present": present,
                "size_bytes": f.stat().st_size if present else 0,
                "used_by": [],
                "params": [],
                "note": asset["note"],
                "group": asset.get("group", "asset"),
            }
        )

    # 目录里额外的 HF 模型（非任何算子默认值，编辑器下拉仍可选）
    if root_ok:
        for org_dir in root.iterdir():
            if not org_dir.is_dir() or org_dir.name == "nltk_data":
                continue
            for repo_dir in org_dir.iterdir():
                repo_id = f"{org_dir.name}/{repo_dir.name}"
                if repo_id in known_ids or not repo_dir.is_dir():
                    continue
                if _hf_model_present(root, repo_id):
                    models.append(
                        {
                            "id": repo_id,
                            "kind": "hf",
                            "present": True,
                            "size_bytes": _dir_size(repo_dir),
                            "used_by": [],
                            "params": [],
                            "note": "目录中的额外模型（非算子默认）",
                            "group": _group_for(repo_id, "hf", [], set()),
                        }
                    )

    return {
        "path": home,
        "path_exists": root_ok,
        "models": models,
        "present_count": sum(1 for m in models if m["present"]),
        "total_count": len(models),
    }
