#!/usr/bin/env python3
"""预下载 data-juicer 算子所需模型到单一目录,供离线环境使用。

在有外网的机器上运行,产出目录整体拷贝到离线机后,给 dj-process 所在进程设:

    DATA_JUICER_EXTERNAL_MODELS_HOME=/data/dj-models   # 本脚本的输出目录
    NLTK_DATA=/data/dj-models/nltk_data                # punkt 分句模型
    HF_HUB_OFFLINE=1                                   # 禁止运行期回源 HF Hub

DJ 侧对应逻辑见 data_juicer/utils/model_utils.py:
- HF 模型: check_model_home() 命中 <DJEMH>/<repo_id> 目录即离线加载
- 单文件资产(fasttext/kenlm/sentencepiece/spacy): check_model() 优先查 <DJEMH>/<文件名>

用法:
    pip install "huggingface_hub[hf_transfer]"
    python download_dj_models.py --dest ./dj-models                 # 默认 text+vision
    python download_dj_models.py --dest ./dj-models --groups all    # 全量(数百 GB)
    python download_dj_models.py --list                             # 只看清单
"""

import argparse
import os
import sys
import urllib.request
from pathlib import Path

# 国内直连 HF 需关闭 xet 通道(会挂死)。不用 hf_transfer:出错即失败无重试,
# 大文件断线场景下不如 huggingface_hub 自带的重试/续传稳。
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.pop("HF_HUB_ENABLE_HF_TRANSFER", None)

OSS_BASE = "https://dail-wlcb.oss-cn-wulanchabu.aliyuncs.com/data_juicer/models"

# 组 -> (HF repo 列表, OSS 单文件列表)
GROUPS: dict[str, dict] = {
    # 文本清洗/质量过滤:语言识别、困惑度、分词、分句、词性 (~4GB)
    "text": {
        "hf": [
            "Qwen/Qwen2.5-0.5B",  # llm_perplexity_filter / instruction_following_difficulty_filter
        ],
        "oss": [
            "lid.176.bin",  # fasttext 语言识别 (language_id_score_filter)
            "en.arpa.bin", "en.sp.model",  # kenlm + sentencepiece (perplexity_filter 等)
            "zh.arpa.bin", "zh.sp.model",
            "en_core_web_md-3.7.0.tar.gz",  # spacy (词性/多样性类)
            "zh_core_web_md-3.7.0.tar.gz",
        ],
        "nltk": ["punkt", "punkt_tab"],  # 分句 (language_id / text_action 等)
    },
    # 图像/视频质量过滤小模型 (~5GB)
    "vision": {
        "hf": [
            "openai/clip-vit-base-patch32",  # image_text_similarity_filter 等 8 个算子
            "Salesforce/blip-itm-base-coco",  # image_text_matching_filter 等 8 个算子
            "Falconsai/nsfw_image_detection",  # image/video_nsfw_filter
            "amrul-hzz/watermark_detector",  # image/video_watermark_filter
            "google/owlvit-base-patch32",  # phrase_grounding_recall_filter
            "shunk031/aesthetics-predictor-v2-sac-logos-ava1-l14-linearMSE",  # 美学评分
            "facebook/sam2.1-hiera-tiny",  # video_object_segmenting_mapper
        ],
        "oss": [],
        "nltk": [],
    },
    # query 分析类 mapper(意图/主题/情感,含中译英前置) (~2GB)
    "query": {
        "hf": [
            "Helsinki-NLP/opus-mt-zh-en",
            "bespin-global/klue-roberta-small-3i4k-intent-classification",
            "dstefa/roberta-base_topic_classification_nyt_news",
            "mrm8488/distilroberta-finetuned-financial-news-sentiment-analysis",
        ],
        "oss": [],
        "nltk": [],
    },
    # 本地 LLM/VLM 生成类 mapper,7B 级,每个 ~15GB (平台默认走 API,通常不需要)
    "llm": {
        "hf": [
            "Qwen/Qwen2.5-7B-Instruct",  # optimize_qa / generate_qa_from_examples 等
            "Qwen/Qwen2-7B-Instruct",  # sentence_augmentation_mapper
            "Qwen/Qwen2.5-VL-7B-Instruct",  # image_tagging_vlm_mapper
            "Qwen/Qwen3-VL-8B-Instruct",  # video_captioning_from_vlm_mapper
            "alibaba-pai/pai-qwen1_5-7b-doc2qa",  # generate_qa_from_text_mapper
            "llava-hf/llava-v1.6-vicuna-7b-hf",  # mllm_mapper
            "Salesforce/blip2-opt-2.7b",  # image/video captioning
        ],
        "oss": [],
        "nltk": [],
    },
    # 重型视觉专用权重(检测/位姿/深度/打标) (~15GB)
    "vision-heavy": {
        "hf": [
            "facebook/VGGT-1B",  # vggt_mapper
            "Ruicheng/moge-2-vitl",  # video_camera_calibration_static_moge_mapper
        ],
        "oss": [
            "ram_plus_swin_large_14m.pth",  # image/video_tagging_from_frames
        ],
        "urls": [  # 非 OSS 直链(BACKUP_MODEL_LINKS)
            "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n.pt",
            "https://github.com/ultralytics/assets/releases/download/v8.2.0/FastSAM-x.pt",
            "https://huggingface.co/yzd-v/DWPose/resolve/main/yolox_l.onnx",
            "https://huggingface.co/yzd-v/DWPose/resolve/main/dw-ll_ucoco_384.onnx",
        ],
        "nltk": [],
    },
}
DEFAULT_GROUPS = ["text", "vision", "query"]


def download_file(url: str, dest: Path, retries: int = 3) -> None:
    """带断点续传的下载:.part 文件存在则从其末尾 Range 续传。"""
    import shutil

    if dest.exists() and dest.stat().st_size > 0:
        print(f"  [skip] {dest.name} 已存在")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(1, retries + 1):
        existing = tmp.stat().st_size if tmp.exists() else 0
        req = urllib.request.Request(url)  # noqa: S310
        if existing:
            req.add_header("Range", f"bytes={existing}-")
        print(f"  [get ] {dest.name}" + (f" 续传自 {existing // 2**20}MB" if existing else ""))
        try:
            with urllib.request.urlopen(req) as resp:  # noqa: S310
                # 服务端不支持 Range 时返回 200 全量,须从头写
                mode = "ab" if existing and resp.status == 206 else "wb"
                with open(tmp, mode) as f:
                    shutil.copyfileobj(resp, f, length=1 << 20)
            tmp.rename(dest)
            return
        except Exception as e:  # noqa: BLE001
            if attempt == retries:
                raise
            print(f"  [retry {attempt}/{retries}] {dest.name}: {e}", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dest", default="./dj-models", help="输出目录(拷贝到离线机的整体)")
    ap.add_argument(
        "--groups", default=",".join(DEFAULT_GROUPS),
        help=f"逗号分隔: {','.join(GROUPS)} 或 all (默认 {','.join(DEFAULT_GROUPS)})",
    )
    ap.add_argument("--list", action="store_true", help="只打印清单不下载")
    args = ap.parse_args()

    names = list(GROUPS) if args.groups == "all" else args.groups.split(",")
    unknown = [g for g in names if g not in GROUPS]
    if unknown:
        ap.error(f"未知组: {unknown},可选 {list(GROUPS)}")

    if args.list:
        for g in names:
            spec = GROUPS[g]
            print(f"[{g}]")
            for r in spec["hf"]:
                print(f"  HF   {r}")
            for f in spec["oss"]:
                print(f"  OSS  {f}")
            for u in spec.get("urls", []):
                print(f"  URL  {u}")
            for n in spec["nltk"]:
                print(f"  NLTK {n}")
        return 0

    dest = Path(args.dest).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    failed: list[str] = []

    from huggingface_hub import snapshot_download

    for g in names:
        spec = GROUPS[g]
        print(f"== 组 {g} ==")
        # HF 模型:目录名必须等于 repo_id,check_model_home 才能命中
        for repo in spec["hf"]:
            target = dest / repo
            print(f"  [hf  ] {repo}")
            try:
                snapshot_download(repo_id=repo, local_dir=str(target))
            except Exception as e:  # noqa: BLE001
                print(f"  [FAIL] {repo}: {e}", file=sys.stderr)
                failed.append(repo)
        # OSS 单文件:放目录根,check_model 按文件名查
        for fname in spec["oss"]:
            try:
                download_file(f"{OSS_BASE}/{fname}", dest / fname)
            except Exception as e:  # noqa: BLE001
                print(f"  [FAIL] {fname}: {e}", file=sys.stderr)
                failed.append(fname)
        for url in spec.get("urls", []):
            fname = url.rsplit("/", 1)[-1]
            try:
                download_file(url, dest / fname)
            except Exception as e:  # noqa: BLE001
                print(f"  [FAIL] {fname}: {e}", file=sys.stderr)
                failed.append(fname)
        # nltk 资源灌到 <dest>/nltk_data,离线机设 NLTK_DATA 指过来
        if spec["nltk"]:
            import nltk

            for pkg in spec["nltk"]:
                print(f"  [nltk] {pkg}")
                try:
                    nltk.download(pkg, download_dir=str(dest / "nltk_data"), quiet=True)
                except Exception as e:  # noqa: BLE001
                    print(f"  [FAIL] nltk {pkg}: {e}", file=sys.stderr)
                    failed.append(f"nltk:{pkg}")

    print()
    if failed:
        print(f"完成但有 {len(failed)} 项失败,请重跑补齐: {failed}", file=sys.stderr)
        return 1
    print(f"全部完成 -> {dest}")
    print("离线机环境变量:")
    print(f"  DATA_JUICER_EXTERNAL_MODELS_HOME={dest}")
    print(f"  NLTK_DATA={dest / 'nltk_data'}")
    print("  HF_HUB_OFFLINE=1")
    return 0


if __name__ == "__main__":
    sys.exit(main())
