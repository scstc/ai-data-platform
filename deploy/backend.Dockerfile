# syntax=docker/dockerfile:1
# 后端镜像:FastAPI(应用 venv) + data-juicer 引擎(独立 venv,进程隔离)。
# 两个 venv 共用 3.12 解释器,靠目录隔离规避依赖冲突(DJ 需 numpy<2 等);
# backend 通过子进程调 /opt/dj/.venv/bin/dj-process 执行算子流水线。
# 构建上下文 = 仓库根目录(需同时含 backend/ 与 data-juicer/)。
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    DJ_PROCESS_BIN=/opt/dj/.venv/bin/dj-process

# uv:两段安装共用
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# 系统依赖:编译(lz4/zstandard 等) + 媒体算子运行期(ffmpeg / libGL)
# + 老 .doc 二进制文件解析:antiword(快路径) / libreoffice(兜底 headless 转 .docx)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential git ffmpeg libgl1 libglib2.0-0 \
        antiword unzip \
        libreoffice-core libreoffice-writer \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /tmp/lo-home

# --- data-juicer 引擎 venv(变动少,放前面利于层缓存) ---
# 非 editable 安装:触发 hatch 自定义 build hook 编译 C++/Cython 去重扩展
# (minhash/tokenize),否则 MinHash 等去重算子运行期缺 .so。
# DJ_EXTRAS:本地栈要让 GPU/hf_model 类算子能跑,默认装 [generic] 把 torch/transformers/vllm/cudf
# 等 CUDA 依赖打进镜像(~6 GB,构建 10+ 分钟;生产机不需要,build --build-arg DJ_EXTRAS="" 覆盖)。
# 显式 ARG 默认值,不依赖外部 .env:compose 文件把 env 拼到 ARGS 时若整行丢空,这里兜底。
ARG DJ_EXTRAS="[generic]"
COPY data-juicer/ /opt/dj/
# 大依赖(GPU extra 几 GB)在弱网下偶发连接中断,--mount=cache 让已下完的包跨构建复用
# (RUN 失败不会保留 Docker 层缓存,没有这个 mount 重试等于从零重下);外层再加重试兜底。
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv /opt/dj/.venv --python 3.12 \
    && ( uv pip install --python /opt/dj/.venv/bin/python "/opt/dj${DJ_EXTRAS}" \
         || (echo "uv pip install failed, retry 1/2..." && sleep 5 && uv pip install --python /opt/dj/.venv/bin/python "/opt/dj${DJ_EXTRAS}") \
         || (echo "uv pip install failed, retry 2/2..." && sleep 5 && uv pip install --python /opt/dj/.venv/bin/python "/opt/dj${DJ_EXTRAS}") )

# GPU 化:generic 装的是 torch 2.8.0+cpu(有卡也用不了),换成 cu126(Linux/py3.12)让 NVIDIA 卡可用。
# 直连 download.pytorch.org 卡死、aliyun 403,改用 SJTU 镜像;--no-deps 只替换 torch 三件套、
# 不动 generic 已装好的其它依赖(否则会被连带重装/退版)。视觉模型 tokenizer 另需 sentencepiece+protobuf。
# DJ_EXTRAS 为空(生产 CPU-only)时整段跳过,不下载 GPU wheel、镜像不变大。
ARG TORCH_CU_BASE="https://mirror.sjtu.edu.cn/pytorch-wheels/cu126"
RUN --mount=type=cache,target=/root/.cache/uv \
    if [ -n "${DJ_EXTRAS}" ]; then \
      ( uv pip install --python /opt/dj/.venv/bin/python --no-deps \
          "${TORCH_CU_BASE}/torch-2.8.0%2Bcu126-cp312-cp312-manylinux_2_28_x86_64.whl" \
          "${TORCH_CU_BASE}/torchvision-0.23.0%2Bcu126-cp312-cp312-manylinux_2_28_x86_64.whl" \
          "${TORCH_CU_BASE}/torchaudio-2.8.0%2Bcu126-cp312-cp312-manylinux_2_28_x86_64.whl" \
        || (echo "cu126 torch install failed, retry..." && sleep 5 && uv pip install --python /opt/dj/.venv/bin/python --no-deps \
          "${TORCH_CU_BASE}/torch-2.8.0%2Bcu126-cp312-cp312-manylinux_2_28_x86_64.whl" \
          "${TORCH_CU_BASE}/torchvision-0.23.0%2Bcu126-cp312-cp312-manylinux_2_28_x86_64.whl" \
          "${TORCH_CU_BASE}/torchaudio-2.8.0%2Bcu126-cp312-cp312-manylinux_2_28_x86_64.whl") ) \
      && uv pip install --python /opt/dj/.venv/bin/python \
          --index-url https://mirror.sjtu.edu.cn/pypi/web/simple sentencepiece protobuf ; \
    fi

# 临时热补丁:scstc/data-juicer PR#2 correlation_analysis.py StringDtype 兜底
# 补丁合并到 data-juicer 上游 + 后续 rebuild 镜像后可整段删除。
# 脚本幂等(检测 marker),跑多次安全。
COPY deploy/patch_dj_correlation_stringdtype.py /tmp/patch_dj_correlation_stringdtype.py
RUN /opt/dj/.venv/bin/python /tmp/patch_dj_correlation_stringdtype.py \
    && rm /tmp/patch_dj_correlation_stringdtype.py

# --- 后端应用 venv ---
WORKDIR /app
RUN uv venv /app/.venv --python 3.12
ENV VIRTUAL_ENV=/app/.venv PATH="/app/.venv/bin:${PATH}"
# 先装依赖(pyproject 变动少),再拷源码,加速重建
COPY backend/pyproject.toml /app/pyproject.toml
RUN --mount=type=cache,target=/root/.cache/uv uv pip install -r /app/pyproject.toml
# 后端 venv 补 mammoth(.doc 解析兜底链 soffice→.docx→mammoth 需要)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python /app/.venv/bin/python 'mammoth>=1.8.0'
COPY backend/ /app/

EXPOSE 18003
COPY deploy/backend-entrypoint.sh /usr/local/bin/entrypoint.sh
# Windows checkout(core.autocrlf=true)会把 CRLF 带进这个文件,\r 追加到 shebang 后容器起不来;
# 直接从工作区构建(非 git archive)时 -c core.autocrlf=false 不生效,这里兜底转换。
RUN sed -i 's/\r$//' /usr/local/bin/entrypoint.sh && chmod +x /usr/local/bin/entrypoint.sh
CMD ["/usr/local/bin/entrypoint.sh"]
