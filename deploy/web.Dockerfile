# FIRESIGHT Web 镜像：构建前端产物 + Caddy 托管与反向代理
# 构建上下文为仓库根目录（需要 frontend/ 与 deploy/Caddyfile.docker）
# ── 阶段 1：构建前端 ─────────────────────────────────
FROM node:20-slim AS build

WORKDIR /build
COPY frontend/package.json ./
# package-lock.json 未入库，用 npm install 而非 npm ci
RUN npm install --no-audit --no-fund

COPY frontend/ ./
# 同源部署走 /api 相对路径，覆盖任何本地 .env
RUN printf 'VITE_API_BASE_URL=/api\n' > .env && npm run build

# ── 阶段 2：Caddy 运行时 ────────────────────────────
FROM caddy:2-alpine

COPY deploy/Caddyfile.docker /etc/caddy/Caddyfile
COPY --from=build /build/dist /srv/firesight
