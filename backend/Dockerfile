# FIRESIGHT 后端镜像（CPU 推理/训练）
# 构建（在仓库根目录）：docker compose -f docker-compose.full.yml build backend
FROM python:3.10-slim

# opencv / 视频转码所需系统库
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend

# 必须先装 CPU 版 torch，否则 requirements 里的 ultralytics 会拉超大的 CUDA 包
RUN pip install --no-cache-dir torch torchvision \
    --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# 用 sh 调起，避免 Windows 检出丢失可执行位
ENTRYPOINT ["sh", "docker-entrypoint.sh"]
