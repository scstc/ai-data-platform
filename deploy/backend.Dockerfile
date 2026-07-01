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
COPY data-juicer/ /opt/dj/
RUN uv venv /opt/dj/.venv --python 3.12 \
    && uv pip install --python /opt/dj/.venv/bin/python /opt/dj

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
RUN uv pip install -r /app/pyproject.toml
# 后端 venv 补 mammoth(.doc 解析兜底链 soffice→.docx→mammoth 需要)
RUN uv pip install --python /app/.venv/bin/python 'mammoth>=1.8.0'
COPY backend/ /app/

EXPOSE 18003
COPY deploy/backend-entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh
CMD ["/usr/local/bin/entrypoint.sh"]
