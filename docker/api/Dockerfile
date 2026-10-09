FROM python:3.11-slim-bookworm

# 镜像源改由 build-arg 决定，默认保留国内源——本项目作者在内地，本地构建走它最快。
# CI（GitHub Runner 在海外）必须覆盖成上游源，否则会绕道国内镜像：实测同一个
# docker build 在 CI 上要 30~45 分钟，而其中绝大部分时间花在跨境下载上。
ARG APT_MIRROR=mirrors.aliyun.com
ARG PIP_INDEX=https://mirrors.aliyun.com/pypi/simple/
ARG PIP_HOST=mirrors.aliyun.com

WORKDIR /app

# apt 与 pip 均走上面指定的镜像，并清空 Docker 注入的宿主机 127.0.0.1 死代理。
# bookworm-slim 用 deb822 格式的 debian.sources，同时兼容旧版 sources.list。
# 走上游时传 APT_MIRROR=deb.debian.org：sed 变成原地替换，不改变任何字节。
# 约束：APT_MIRROR 必须是非空、且不含 sed 分隔符 | 的主机名。
RUN set -eux; \
    unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; \
    for f in /etc/apt/sources.list.d/debian.sources /etc/apt/sources.list; do \
        if [ -f "$f" ]; then \
            sed -i "s|deb.debian.org|${APT_MIRROR}|g; s|security.debian.org|${APT_MIRROR}|g" "$f"; \
        fi; \
    done

# 简历 OCR 系统依赖：poppler-utils 供 pdf2image 转图，tesseract 供 pytesseract 识别（含中文简体语言包）
RUN unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; \
    apt-get update && apt-get install -y --no-install-recommends \
        poppler-utils \
        tesseract-ocr \
        tesseract-ocr-chi-sim \
    && rm -rf /var/lib/apt/lists/*

ENV PIP_INDEX_URL=${PIP_INDEX} \
    PIP_TRUSTED_HOST=${PIP_HOST} \
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY requirements.txt .
RUN unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; \
    pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

EXPOSE 8000

# 生产模式：不挂源码卷、不开 --reload；表结构由 ORM 首次启动自动建表并幂等种子
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
