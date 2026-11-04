# ClubOS NEW 自托管镜像
# 构建上下文为项目根（clubos-new/），通过 ../.dockerignore 排除体积内容
FROM python:3.11-slim

WORKDIR /app

# 先装依赖（利用层缓存）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码（.dockerignore 已排除 venv/static/uploads 等）
COPY . .

# 数据持久化目录（由 compose 挂载卷）
RUN mkdir -p /data
ENV CLUBOS_DB_PATH=/data/clubos.db

EXPOSE 8000

# 注意：不要在此绑定 0.0.0.0 之外的外部端口，由反代（Caddy/nginx）转发
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port 8000"]
