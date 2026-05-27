FROM python:3.12-slim

WORKDIR /app

# 시스템 의존성 (Playwright Chromium)
RUN apt-get update && apt-get install -y \
    wget curl gnupg ca-certificates \
    libglib2.0-0 libnss3 libnspr4 libdbus-1-3 \
    libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 \
    libxrandr2 libgbm1 libasound2 \
    fontconfig fonts-dejavu-core fonts-liberation fonts-noto-cjk \
    libreoffice-writer \
    && rm -rf /var/lib/apt/lists/*

# Python 의존성
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright 브라우저 설치
RUN playwright install chromium

COPY . .

ENV PYTHONUNBUFFERED=1
ENV OUTPUT_DIR=/data

CMD ["python", "main.py", "--loop"]
