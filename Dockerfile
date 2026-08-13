# ================================
# 阶段一：构建前端
# ================================
FROM node:18-slim AS frontend-builder

WORKDIR /build

# 复制前端依赖文件并安装
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --registry=https://registry.npmmirror.com

# 复制前端代码并构建
COPY frontend/ ./

# 接收构建参数
ARG VITE_AMAP_WEB_JS_KEY
ARG VITE_GOOGLE_MAPS_BROWSER_KEY
ARG VITE_PUBLIC_DEMO_MODE=true

# 设置构建时环境变量：API 使用相对路径(同源部署)
ENV VITE_API_BASE_URL=""
ENV VITE_AMAP_WEB_JS_KEY=${VITE_AMAP_WEB_JS_KEY:-your_amap_web_js_api_key_here}
ENV VITE_GOOGLE_MAPS_BROWSER_KEY=${VITE_GOOGLE_MAPS_BROWSER_KEY:-}
ENV VITE_PUBLIC_DEMO_MODE=${VITE_PUBLIC_DEMO_MODE}

# Use the committed production build contract, including TypeScript checking.
RUN npm run build


# ================================
# 阶段二：构建最终镜像
# ================================
FROM python:3.10-slim

WORKDIR /app

# 安装系统依赖及 Node.js(用于执行小红书签名引擎)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc curl nodejs npm \
    && rm -rf /var/lib/apt/lists/*

# 安装 uv 包管理器
RUN pip install --no-cache-dir uv -i https://mirrors.aliyun.com/pypi/simple/

# 复制后端依赖并使用 uv 安装
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/

# 安装 gunicorn + uvicorn worker
RUN uv pip install --system --no-cache gunicorn "uvicorn[standard]" -i https://mirrors.aliyun.com/pypi/simple/

# 复制后端代码并安装 Node.js 依赖
COPY backend/ ./backend/
RUN cd backend && npm install --omit=dev --registry=https://registry.npmmirror.com

# 从阶段一复制前端构建产物
COPY --from=frontend-builder /build/dist ./frontend/dist

# 复制启动脚本
COPY start.sh ./start.sh
RUN chmod +x ./start.sh

# Default container port; Render supplies PORT at runtime.
EXPOSE 7860

CMD ["./start.sh"]
