# RSOD Web Platform — 部署教程

> 本文档覆盖从零开始的完整部署流程：云服务器准备 → Docker 容器化 → GitHub Actions CI/CD 自动化 → 日常运维。

---

## 目录

- [一、部署架构概览](#一部署架构概览)
- [二、云服务器准备](#二云服务器准备)
- [三、GitHub 仓库配置](#三github-仓库配置)
- [四、CI/CD 工作流详解](#四cicd-工作流详解)
- [五、Docker 配置详解](#五docker-配置详解)
- [六、首次部署（手动）](#六首次部署手动)
- [七、日常运维命令](#七日常运维命令)
- [八、服务器迁移指南](#八服务器迁移指南)
- [九、常见问题排查](#九常见问题排查)
- [附录 A：文件清单](#附录-a文件清单)
- [附录 B：环境变量参考](#附录-b环境变量参考)

---

## 一、部署架构概览

### 1.1 设计目标

| 目标 | 说明 |
|------|------|
| **全自动 CI/CD** | `git push` → 自动构建镜像 → 自动部署到服务器，零手动操作 |
| **环境隔离** | 前端、后端、数据库、缓存、对象存储全部容器化，开箱即用 |
| **镜像体积优化** | 前端 ~20MB（多阶段构建），后端 CPU-only PyTorch（节省 ~4GB） |
| **安全凭证隔离** | 敏感信息通过 GitHub Secrets + 服务器环境变量注入，不进代码仓库 |

### 1.2 架构图

```
开发者 ─── git push ──→ GitHub
                         │
                    GitHub Actions
                    ┌────┴─────────────────────────┐
                    │  ci.yml         语法/构建检查  │
                    │  build-frontend.yml → ghcr.io │
                    │  build-backend.yml  → ghcr.io │
                    │  deploy.yml     SCP + SSH 部署 │
                    └────┬─────────────────────────┘
                         │ SCP docker-compose.server.yml
                         │ SSH docker compose pull & up
                         ▼
                    ┌─ 云服务器 ─────────────────────┐
                    │                                │
                    │  Nginx（前端, :80）             │
                    │    └→ /api/ 反向代理 → 后端    │
                    │  FastAPI + YOLO（后端, :8000）  │
                    │  PostgreSQL（:5432）            │
                    │  Redis（:6379）                 │
                    │  MinIO（API :9000, UI :9001）   │
                    │                                │
                    │  数据持久化 → /data/            │
                    └────────────────────────────────┘
```

### 1.3 参考链接

- 项目仓库：https://github.com/wxber12/rsod-web-platform/
- Workflow 目录：https://github.com/wxber12/rsod-web-platform/tree/main/.github/workflows
- 服务器编排文件：https://github.com/wxber12/rsod-web-platform/blob/main/docker-compose.server.yml

---

## 二、云服务器准备

### 2.1 获取云服务器

- **Google Cloud**（免费）：https://zhuanlan.zhihu.com/p/711917614
- **阿里云 ECS**：https://help.aliyun.com/zh/ecs/user-guide/deploy-applications

> **建议配置**：≥2 核 CPU、≥4GB 内存、≥30GB 磁盘。YOLO 模型推理需要一定的内存空间。

### 2.2 安装 Docker

SSH 登录服务器后执行：

```bash
# 一键安装 Docker（含 Docker Compose 插件）
curl -fsSL https://get.docker.com | sh

# 将当前用户加入 docker 组（免 sudo）
sudo usermod -aG docker $USER
```

**退出 SSH 重新登录**使组权限生效，然后验证：

```bash
docker --version
docker compose version
```

### 2.3 创建项目目录与数据目录

```bash
# 项目根目录
sudo mkdir -p /opt/rsod-web-platform
sudo chown $(whoami):$(whoami) /opt/rsod-web-platform

# 数据持久化目录（Docker 卷挂载点）
sudo mkdir -p /data/postgres /data/redis /data/minio
sudo chown -R $(whoami):$(whoami) /data
```

### 2.4 创建路径标记文件

后端通过 `.rsod_platform` 标记文件识别项目根目录。此文件通过 Docker 卷挂载到容器内：

```bash
touch /opt/rsod-web-platform/.rsod_platform
```

### 2.5 创建 `.env` 文件

`.env` 文件存储敏感配置，**不进 Git 仓库、不进 Docker 镜像**：

```bash
cat > /opt/rsod-web-platform/.env << 'EOF'
DEEPSEEK_API_KEY=你的DeepSeek密钥
EOF
```

### 2.6 开放防火墙端口

确保以下端口在云平台安全组 / 防火墙中放行：

| 端口 | 服务 | 是否必须 |
|------|------|----------|
| 80   | 前端 Nginx | ✅ 必须 |
| 8000 | 后端 FastAPI | 可选（Nginx 已反代） |
| 9000 | MinIO API | ✅ 必须（图片公网访问） |
| 9001 | MinIO 控制台 | 可选（管理用） |
| 22   | SSH | ✅ 必须 |

---

## 三、GitHub 仓库配置

### 3.1 配置 Secrets

路径：仓库 → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

| Secret 名称 | 示例值(需替换为实际配置) | 说明 |
|---|---|---|
| `SERVER_HOST` | `8.229.104.133` | 服务器公网 IP |
| `SERVER_USERNAME` | `root` | SSH 用户名（需有 docker 权限） |
| `SERVER_PASSWORD` | `********` | SSH 密码 |
| `SERVER_PORT` | `22` | SSH 端口 |

### 3.2 开启 Actions 写权限

路径：仓库 → **Settings** → **Actions** → **General** → **Workflow permissions**

选择 **Read and write permissions** 并保存。

> ⚠️ 不开启此项会导致推送 Docker 镜像到 GHCR 时报 403 权限错误。

---

## 四、CI/CD 工作流详解

推荐将该prompt提交给Coding Agents，生成需要的文件

```
你是一个资深的 DevOps 工程师与全栈开发专家。现在我需要你根据以下架构规范和具体要求，为我的 "RSOD Web Platform" 项目编写完整的 Docker 与 CI/CD 部署配置文件。

【项目架构】
- 前端：Vue 3 + Nginx (暴露端口 80)
- 后端：FastAPI + PyTorch (暴露端口 8000)
- 数据库：PostgreSQL (5432)
- 缓存：Redis (6379)
- 对象存储：MinIO (API:9000, UI:9001)

【需要生成的 7 个核心文件】
1. frontend/Dockerfile
2. frontend/nginx.conf
3. backend/Dockerfile
4. docker-compose.server.yml
5. .github/workflows/ci.yml
6. .github/workflows/build-frontend.yml
7. .github/workflows/build-backend.yml
8. .github/workflows/deploy.yml

【严格技术要求】
1. 前端构建：
   - 采用多阶段构建（Node + Nginx alpine）。
   - Nginx 配置需支持 SPA 路由回退，且将 `/api/` 代理至后端服务。
2. 后端构建：
   - 基础镜像使用 python:3.11-slim。
   - 必须使用 PyTorch CPU 版本以减小镜像体积（指定 --index-url https://download.pytorch.org/whl/cpu）。
3. 容器编排 (docker-compose.server.yml)：
   - 镜像统一使用 GitHub Container Registry (ghcr.io/<your-username>/rsod-xxx:latest)。
   - 所有服务配置 restart: unless-stopped，配置相应的 volume 持久化到宿主机的 /data 目录下。
   - 后端需配置 env_file 指向宿主机 .env，并配置 depends_on 确保 db 和 minio 就绪后再启动。
   - MinIO 环境变量需包含 MINIO_PUBLIC_ENDPOINT 设置。
4. CI/CD 工作流 (GitHub Actions)：
   - ci.yml: push/PR 时触发，前端执行 npm build，后端执行语法检查 (python -m compileall)。
   - build-*.yml: 目录有变更时触发，登录 GHCR 并使用 docker build 推送镜像，注意镜像名必须全小写。
   - deploy.yml: build 成功后触发，使用 appleboy/scp-action 推送 docker-compose.server.yml 到服务器，并用 appleboy/ssh-action 登录服务器执行 docker compose pull & up -d & prune 清理。

请一步步输出上述所有文件的标准代码，要求代码包含详细的注释，符合生产环境安全最佳实践。
```



### 4.1 整体流水线

```
git push to main
       │
       ├──→ ci.yml（始终执行，语法 + 构建检查）
       │
       ├──→ build-frontend.yml（仅 frontend/ 有变更时触发）
       │         └──→ 构建镜像 → 推送到 ghcr.io
       │
       ├──→ build-backend.yml（仅 backend/ 有变更时触发）
       │         └──→ 构建镜像 → 推送到 ghcr.io
       │
       └──→ deploy.yml（build 成功后自动触发）
                 ├──→ SCP 推送 docker-compose.server.yml
                 └──→ SSH 执行 pull + 重启
```

### 4.2 CI 构建检查 — `ci.yml`

**触发条件**：push 或 PR 到 `main` / `develop` 分支

| Job | 操作 | 说明 |
|-----|------|------|
| Frontend Build | `npm ci` + `npm run build` | 验证前端构建不报错 |
| Backend Syntax | `python -m compileall -q .` | 只检查语法，不安装依赖（避免 PyTorch 拖慢 CI） |

### 4.3 构建镜像 — `build-frontend.yml` / `build-backend.yml`

**触发条件**：push 到 `main` 且对应目录（`frontend/` 或 `backend/`）有文件变更

**流程**：`checkout` → `Login to GHCR` → `docker build & push`

- 镜像推送到 `ghcr.io/<owner>/rsod-frontend:latest` 和 `ghcr.io/<owner>/rsod-backend:latest`
- 同时打上 `:<commit-sha>` 标签用于回滚
- 后端镜像含 PyTorch CPU 版本，构建时间约 5–10 分钟

### 4.4 部署到服务器 — `deploy.yml`

**触发条件**：
- `workflow_run`：上述 build workflow 完成后自动触发
- `workflow_dispatch`：支持手动触发（备用）

**部署步骤**：

```
1. SCP 推送 docker-compose.server.yml 到服务器 /opt/rsod-web-platform/
2. SSH 到服务器执行：
   ├── docker rmi 旧镜像（强制清除缓存）
   ├── docker compose pull（拉取最新镜像）
   ├── docker compose up -d（重启容器）
   └── docker image prune -f（清理悬空镜像）
```

> ⚠️ 如果 build workflow 因路径过滤被跳过（无代码变更），deploy 不会自动触发。此时可手动 `workflow_dispatch`。

---

## 五、Docker 配置详解

### 5.1 前端 Dockerfile — `frontend/Dockerfile`

采用**多阶段构建**：

| 阶段 | 基础镜像 | 操作 |
|------|----------|------|
| 构建阶段 | `node:20-alpine` | `npm install` + `npm run build` |
| 运行阶段 | `nginx:alpine` | 仅拷贝构建产物 `dist/` 和 `nginx.conf` |

最终镜像约 **~20MB**。构建时通过 `VITE_API_BASE_URL=/api` 让前端 API 请求走 Nginx 反代。

### 5.2 后端 Dockerfile — `backend/Dockerfile`

基础镜像 `python:3.11-slim`，关键优化：

```dockerfile
# ★ 先装 CPU-only PyTorch（~200MB），再装其他依赖
RUN pip install --no-cache-dir torch torchvision \
    --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir -r requirements.txt
```

- 默认 `pip install torch` 会下载 CUDA 版本（~2GB+），CPU 版本节省约 **4GB** 镜像体积
- 模型文件 `.pt` 在构建时 `COPY` 进镜像
- 安装了 `libgl1` 和 `libglib2.0-0` 以支持 OpenCV

### 5.3 Nginx 反向代理 — `frontend/nginx.conf`

```
请求路径          →  转发目标
────────────────────────────────
/                →  SPA 静态文件（try_files → index.html）
/api/*           →  http://backend:8000（API 请求）
/runs/*          →  http://backend:8000（检测结果图片）
/static/*        →  http://backend:8000（上传文件）
```

> 容器间通过 Docker Compose **服务名**通信（如 `backend:8000`），不是 `localhost`。

### 5.4 服务器编排 — `docker-compose.server.yml`

| 服务 | 镜像 | 端口 | 数据卷 |
|------|------|------|--------|
| `postgres` | `postgres:15` | 内部 5432 | `/data/postgres` |
| `redis` | `redis:7` | 内部 6379 | `/data/redis` |
| `minio` | `minio/minio:latest` | 9000, 9001 | `/data/minio` |
| `backend` | `ghcr.io/<owner>/rsod-backend:latest` | 8000 | `.rsod_platform` 只读挂载 |
| `frontend` | `ghcr.io/<owner>/rsod-frontend:latest` | 80 | — |

**核心要点**：

- 所有服务均设置 `restart: unless-stopped`（服务器重启后自动恢复）
- `backend` 通过 `env_file: .env` 注入敏感配置（如 DeepSeek API Key）
- `backend` 的 `depends_on` 配合健康检查，确保 PostgreSQL 和 MinIO 就绪后再启动
- `MINIO_PUBLIC_ENDPOINT` 必须设为**服务器公网 IP**（详见 [9.4](#94-图片无法显示broken-image)）

---

## 六、首次部署（手动）

完成第二、三节的准备工作后，在服务器上执行：

```bash
# 1. 进入项目目录
cd /opt/rsod-web-platform

# 2. 获取 compose 文件（二选一）
#    方式 A：从 GitHub 下载
curl -o docker-compose.server.yml \
  https://raw.githubusercontent.com/wxber12(替换为自己的仓库用户名)/rsod-web-platform/main/docker-compose.server.yml

#    方式 B：从本地 SCP 上传
#    scp docker-compose.server.yml root@<server-ip>:/opt/rsod-web-platform/

# 3. 拉取镜像并启动
docker compose -f docker-compose.server.yml pull
docker compose -f docker-compose.server.yml up -d

# 4. 验证
docker ps
```

应看到 5 个容器全部 `Up`：

```
rsod-frontend   (80)
rsod-backend    (8000)
rsod-postgres   (5432)
rsod-redis      (6379)
rsod-minio      (9000, 9001)
```

首次部署后，后续所有更新由 GitHub Actions 自动完成。

---

## 七、日常运维命令

### 7.1 查看状态

```bash
# 查看所有容器运行状态
docker ps

# 查看某个服务的日志（-f 实时跟踪）
docker logs rsod-backend
docker logs -f rsod-frontend

# 进入容器内部调试
docker exec -it rsod-backend bash
```

### 7.2 重启服务

```bash
# 重启单个服务
docker compose -f docker-compose.server.yml restart backend

# 重新拉取并启动所有服务
cd /opt/rsod-web-platform
docker rmi ghcr.io/wxber12/rsod-frontend:latest || true
docker rmi ghcr.io/wxber12/rsod-backend:latest || true
docker compose -f docker-compose.server.yml pull
docker compose -f docker-compose.server.yml up -d
docker image prune -f
```

### 7.3 磁盘与资源管理

```bash
# 查看服务器磁盘使用
df -h

# 查看 Docker 磁盘占用
docker system df

# 清理所有未使用的 Docker 资源（镜像、容器、网络）
docker system prune -a
```

### 7.4 环境变量检查

```bash
# 验证 .env 变量是否注入成功
docker exec rsod-backend env | grep DEEPSEEK
docker exec rsod-backend env | grep MINIO
```

---

## 八、服务器迁移指南

将平台迁移到新服务器的完整步骤：

### 8.1 新服务器准备

按照 [第二节](#二云服务器准备) 完成以下操作：

1. 安装 Docker
2. 创建 `/opt/rsod-web-platform` 和 `/data/{postgres,redis,minio}` 目录
3. 创建 `.rsod_platform` 标记文件
4. 创建 `.env` 文件
5. 开放防火墙端口

### 8.2 更新 GitHub Secrets

修改仓库 Secrets 中的 `SERVER_HOST` 为新服务器的公网 IP。如果 SSH 用户名、密码、端口有变化，一并更新。

### 8.3 更新 `docker-compose.server.yml`

将 `MINIO_PUBLIC_ENDPOINT` 的 IP 改为新服务器的公网 IP：

```yaml
MINIO_PUBLIC_ENDPOINT: <新服务器IP>:9000
```

提交并推送到 GitHub，或直接在新服务器上手动修改此文件。

### 8.4 启动服务

```bash
cd /opt/rsod-web-platform
docker compose -f docker-compose.server.yml pull
docker compose -f docker-compose.server.yml up -d
```

### 8.5 数据迁移（可选）

如需保留旧服务器的检测历史和用户数据：

```bash
# 在旧服务器上备份
docker exec rsod-postgres pg_dump -U my_user my_db > backup.sql
scp backup.sql root@<新服务器IP>:/tmp/

# 在新服务器上恢复
docker cp /tmp/backup.sql rsod-postgres:/tmp/
docker exec rsod-postgres psql -U my_user -d my_db -f /tmp/backup.sql
```

---

## 九、常见问题排查

> 💡 **通用建议**：遇到任何问题，将本节对应条目 + 出错命令 / 截图一起粘贴给 AI，可快速定位。

### 9.1 GHCR 镜像名必须全小写

```yaml
# ❌ GitHub 用户名含大写
image: ghcr.io/YourName/rsod-frontend:latest

# ✅ 必须全小写
image: ghcr.io/yourname/rsod-frontend:latest
```

Workflow 中使用 `${{ github.repository_owner }}` 可自动获取全小写的仓库所有者名。

### 9.2 容器内不能用 localhost

Docker 中每个容器的 `localhost` 是独立隔离的，容器间通信必须使用 **Compose 服务名**：

```python
# ❌ 错误
MINIO_ENDPOINT = "localhost:9000"

# ✅ 正确
MINIO_ENDPOINT = "minio:9000"
```

### 9.3 GitHub Actions 权限不足（GHCR 推送 403）

**方法一**：在 workflow 文件中声明权限

```yaml
permissions:
  contents: read
  packages: write
```

**方法二**：在仓库 Settings → Actions → General → Workflow permissions 开启 **Read and write permissions**

### 9.4 图片无法显示（Broken Image）

**根因**：后端生成的图片 URL 使用了 Docker 内部域名 `minio:9000`，浏览器无法解析。

**解决**：在 `docker-compose.server.yml` 中设置 `MINIO_PUBLIC_ENDPOINT` 为服务器公网 IP：

```yaml
environment:
  MINIO_ENDPOINT: minio:9000              # 容器内部通信
  MINIO_PUBLIC_ENDPOINT: <公网IP>:9000     # 浏览器访问地址
```

### 9.5 Docker 镜像缓存导致不更新

`docker compose pull` 拉取 `:latest` 标签时，本地已有同名镜像可能命中缓存。解决方法：**先删旧镜像再拉取**。

```bash
docker rmi ghcr.io/wxber12/rsod-frontend:latest || true
docker rmi ghcr.io/wxber12/rsod-backend:latest || true
docker compose -f docker-compose.server.yml pull
docker compose -f docker-compose.server.yml up -d
docker image prune -f
```

### 9.6 `.env` 修改后不生效

`.env` 文件修改后，已运行的容器不会自动读取新变量，必须**重启容器**：

```bash
docker compose -f docker-compose.server.yml restart backend
```

### 9.7 `appleboy/ssh-action` SSH 握手失败

某些服务器的 SSH 版本与该 Action 不兼容。改用原生 `sshpass`：

```yaml
- run: sudo apt-get install -y sshpass
- run: sshpass -p "$PASSWORD" scp -o StrictHostKeyChecking=no ...
- run: sshpass -p "$PASSWORD" ssh -o StrictHostKeyChecking=no ...
```

### 9.8 `workflow_run` 触发机制

- 如果 build workflow 因路径过滤被跳过（代码无变更），deploy **不会触发**
- `deploy.yml` 的 `if` 条件判断 `conclusion == 'success'`，构建失败不会触发部署
- 支持 `workflow_dispatch` 手动触发作为备用

### 9.9 Vue Router / Vite `base` 路径

| 场景 | `vite.config.js` 的 `base` |
|------|---------------------------|
| 服务器根目录部署 | 不设置（默认 `/`） |
| GitHub Pages 子目录 | `base: '/rsod-web-platform/'` |

服务器部署时 **不要** 设置 `base`，否则会导致 Nginx MIME type 错误。

### 9.10 后端 500 错误 — `'NoneType' object is not iterable`

**可能原因**：使用了图像分类模型（如 `best_plant_village.pt`）进行"目标检测"。

- **分类模型**只返回 `probs`（全局概率），不返回 `boxes`（检测框）
- 后端代码默认遍历 `boxes`，当 `boxes` 为 `None` 时崩溃

**解决**：在前端切换回正确的目标检测模型（如 `best_rsod`）。

---

## 附录 A：文件清单

| 文件 | 用途 |
|------|------|
| `.github/workflows/ci.yml` | CI 检查（前端构建 + 后端语法） |
| `.github/workflows/build-frontend.yml` | 构建前端 Docker 镜像 → GHCR |
| `.github/workflows/build-backend.yml` | 构建后端 Docker 镜像 → GHCR |
| `.github/workflows/deploy.yml` | SCP 推送 compose 文件 + SSH 部署 |
| `frontend/Dockerfile` | 前端多阶段构建（Node → Nginx） |
| `frontend/nginx.conf` | Nginx 反向代理配置 |
| `backend/Dockerfile` | 后端构建（Python + PyTorch CPU） |
| `backend/.dockerignore` | 排除 `.env`、缓存等不进镜像 |
| `docker-compose.server.yml` | **服务器部署用**（GHCR 镜像 + 环境变量） |
| `docker-compose.yml` | 本地开发用（仅基础设施） |
| `docker-compose.prod.yml` | 生产参考配置 |

## 附录 B：环境变量参考

### 服务器 `.env` 文件

| 变量 | 说明 |
|------|------|
| `DEEPSEEK_API_KEY` | DeepSeek AI 诊断建议接口密钥 |

### `docker-compose.server.yml` 中的环境变量

| 变量 | 值 | 说明 |
|------|-----|------|
| `DB_HOST` | `postgres` | 数据库服务名 |
| `DB_NAME` | `my_db` | 数据库名 |
| `DB_USER` | `my_user` | 数据库用户 |
| `DB_PASSWORD` | `123456` | 数据库密码 |
| `DB_PORT` | `5432` | 数据库端口 |
| `MINIO_ENDPOINT` | `minio:9000` | MinIO 容器内部通信地址 |
| `MINIO_ACCESS_KEY` | `minioadmin` | MinIO 访问密钥 |
| `MINIO_SECRET_KEY` | `minioadmin` | MinIO 密钥 |
| `MINIO_PUBLIC_ENDPOINT` | `<公网IP>:9000` | 浏览器访问 MinIO 的地址 |

### GitHub Secrets

| Secret | 说明 |
|--------|------|
| `SERVER_HOST` | 服务器公网 IP |
| `SERVER_USERNAME` | SSH 用户名 |
| `SERVER_PASSWORD` | SSH 密码 |
| `SERVER_PORT` | SSH 端口 |