FROM python:3.12-slim-bookworm

WORKDIR /app
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# 시스템 의존성 (Playwright Chromium)
RUN apt-get update && apt-get install -y \
    wget curl gnupg ca-certificates \
    libglib2.0-0 libnss3 libnspr4 libdbus-1-3 \
    libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 \
    libxrandr2 libgbm1 libasound2 \
    fontconfig fonts-dejavu-core fonts-liberation fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

ARG RHWP_VERSION=v0.7.10
RUN set -eux; \
    archive="rhwp-${RHWP_VERSION}-linux-x86_64.tar.gz"; \
    curl -L "https://github.com/edwardkim/rhwp/releases/download/${RHWP_VERSION}/${archive}" -o "/tmp/${archive}"; \
    curl -L "https://github.com/edwardkim/rhwp/releases/download/${RHWP_VERSION}/SHA256SUMS.txt" -o /tmp/SHA256SUMS.txt; \
    cd /tmp; \
    grep " ${archive}$" SHA256SUMS.txt | sha256sum -c -; \
    mkdir -p /tmp/rhwp \
    && tar -xzf "/tmp/${archive}" -C /tmp/rhwp \
    && find /tmp/rhwp -type f -name rhwp -exec install -m 0755 {} /usr/local/bin/rhwp \; \
    && test -x /usr/local/bin/rhwp \
    && rm -rf /tmp/rhwp "/tmp/${archive}" /tmp/SHA256SUMS.txt

# Python 의존성
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright 브라우저 설치
RUN playwright install chromium

RUN groupadd --system app \
    && useradd --system --gid app --create-home app \
    && mkdir -p /data /ms-playwright \
    && chown -R app:app /app /data /ms-playwright

COPY --chown=app:app . .

ENV PYTHONUNBUFFERED=1
ENV OUTPUT_DIR=/data

USER app

CMD ["python", "main.py", "--loop"]
