FROM python:3.11-slim-bookworm

WORKDIR /app

# 国内网络：apt 与 pip 均走阿里云镜像，并清空 Docker 注入的宿主机 127.0.0.1 死代理。
# bookworm-slim 用 deb822 格式的 debian.sources，同时兼容旧版 sources.list。
RUN set -eux; \
    unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; \
    for f in /etc/apt/sources.list.d/debian.sources /etc/apt/sources.list; do \
        if [ -f "$f" ]; then \
            sed -i 's|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org|mirrors.aliyun.com|g' "$f"; \
        fi; \
    done

# 简历 OCR 系统依赖：poppler-utils 供 pdf2image 转图，tesseract 供 pytesseract 识别（含中文简体语言包）
RUN unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; \
    apt-get update && apt-get install -y --no-install-recommends \
        poppler-utils \
        tesseract-ocr \
        tesseract-ocr-chi-sim \
    && rm -rf /var/lib/apt/lists/*

ENV PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
    PIP_TRUSTED_HOST=mirrors.aliyun.com \
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY requirements.txt .
RUN unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; \
    pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

EXPOSE 8000

# 生产模式：不挂源码卷、不开 --reload；表结构由 ORM 首次启动自动建表并幂等种子
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
