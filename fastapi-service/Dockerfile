FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖（curl 用于 healthcheck）
RUN apt-get update && apt-get install -y \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 先复制依赖文件，利用 Docker 层缓存（依赖不变时跳过 pip install）
COPY requirements.txt .
RUN pip install --no-cache-dir --default-timeout=120 --retries 10 -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
# RUN pip install --no-cache-dir --default-timeout=120 --retries 10 -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
# RUN pip install -default-timeout=120 --retries 10 -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/

# 复制应用代码
COPY . .

# 创建非 root 用户运行应用（安全加固）
RUN useradd -m -u 1000 appuser \
    && mkdir -p /app/logs \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
