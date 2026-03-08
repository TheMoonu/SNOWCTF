# ============ 第一阶段：构建阶段（编译 Python 包） ============
FROM python:3.11-slim AS builder

ARG debian_host=mirrors.ustc.edu.cn
ARG pip_index_url=https://mirrors.tencent.com/pypi/simple
ARG pip_trusted_host=mirrors.tencent.com

RUN sed -i "s/deb.debian.org/${debian_host}/g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends \
       libpq-dev \
       build-essential \
       pkg-config \
       python3-dev \
       libffi-dev \
       libssl-dev \
    && rm -rf /var/lib/apt/lists/*


RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
    --index-url $pip_index_url --trusted-host $pip_trusted_host --root-user-action=ignore

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --prefix=/install \
    -r /tmp/requirements.txt \
    supervisor gunicorn \
    --index-url $pip_index_url --trusted-host $pip_trusted_host --root-user-action=ignore


FROM python:3.11-slim

ARG debian_host=mirrors.ustc.edu.cn
ENV PYTHONUNBUFFERED=1
WORKDIR /opt/cloud/secsnow

RUN sed -i "s/deb.debian.org/${debian_host}/g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends \
       libpq5 \
       libssl3t64 \
       ca-certificates \
       fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/* \
    && apt-get clean \
    && cp /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && echo "Asia/Shanghai" > /etc/timezone

COPY --from=builder /install /usr/local

COPY . .

RUN set -ex \
    && mkdir -p log static media whoosh_index \
    && find /opt/cloud/secsnow -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true \
    && find /opt/cloud/secsnow -type f \( -name "*.pyc" -o -name "*.pyo" \) -delete \
    && find /usr/local -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true \
    && find /usr/local -type f \( -name "*.pyc" -o -name "*.pyo" \) -delete \
    && find /opt/cloud/secsnow -type d \( -name "*.egg-info" -o -name ".git" -o -name ".pytest_cache" -o -name ".mypy_cache" \) -exec rm -rf {} + 2>/dev/null || true \
    && rm -rf /opt/cloud/secsnow/.coverage /opt/cloud/secsnow/htmlcov \
    && find /opt/cloud/secsnow -type f -name "test_*.py" -delete 2>/dev/null || true \
    && find /opt/cloud/secsnow -type d -name "tests" -exec rm -rf {} + 2>/dev/null || true \
    && rm -rf /opt/cloud/secsnow/docs 2>/dev/null || true \
    && find /opt/cloud/secsnow -name "*.py" -exec chmod 644 {} + \
    && chmod 600 /opt/cloud/secsnow/secsnow/settings.py 2>/dev/null || true \
    && chmod -R 755 log whoosh_index \
    && chmod -R 755 static media \
    && chown -R www-data:www-data /opt/cloud/secsnow


# 切换到非root用户
USER root

# 默认命令（Web 服务）
CMD ["supervisord", "-n", "-c", "supervisord.conf"]