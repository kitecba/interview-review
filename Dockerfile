# 多阶段构建：Node 编译前端 → Python 运行后端。
# 产出的镜像自包含：前端静态文件由 FastAPI 同源托管，不需要 Nginx。

# ---------- 阶段一：构建前端 ----------
FROM node:22-alpine AS frontend-build

WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
# 先装依赖再拷源码，利用 Docker 层缓存 —— 改业务代码时不用重装依赖
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---------- 阶段二：后端运行时 ----------
FROM python:3.13-slim

WORKDIR /app

# imageio-ffmpeg 自带 ffmpeg 二进制，不需要 apt 装
COPY backend/requirements.txt ./backend/
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/app ./backend/app
COPY --from=frontend-build /build/dist ./static

ENV PYTHONUNBUFFERED=1 \
    # 数据（SQLite、上传的音频）写到挂载卷，容器重建不丢
    DATA_DIR=/data \
    STATIC_DIR=/app/static \
    # playwright 等无关依赖在容器里没有，imageio-ffmpeg 不要去找系统 ffmpeg
    IMAGEIO_NO_DOWNLOAD=1

# SQLite + 上传音频都在 /data
VOLUME /data

EXPOSE 8000

# --workers 1 是硬约束：任务队列和进度推送是进程内状态
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--app-dir", "backend"]
