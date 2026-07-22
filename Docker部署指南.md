# FIRESIGHT 全 Docker 部署指南

一条命令把 **前端 + 后端 + 数据库 + 缓存 + 对象存储** 全部跑起来，适合快速体验和验收。
（开发模式、云服务器脚本部署见 [README.md](README.md) 与 [deploy/DEPLOY.md](deploy/DEPLOY.md)）

## 0、为什么有两个 compose 文件（都需要保留）

| 文件 | 里面有什么 | 给谁用 |
|---|---|---|
| `docker-compose.yml` | 仅基础设施：PostgreSQL / Redis / MinIO（端口暴露到本机） | ① 日常开发：中间件进容器，前后端在本机热重载；② 云服务器脚本部署：`deploy/local-deploy.sh`、`remote-deploy.sh`、`auto-update.sh` 都基于它（叠加 `deploy/docker-compose.prod.yml` 使用） |
| `docker-compose.full.yml` | 全部五个服务（含前后端镜像构建） | 本指南：一条命令完整跑起来，用于体验、演示、验收 |

两套栈容器名不同（`rsod-agent-*` vs `firesight-*`）、互相独立，但因为在同一目录，**必须按本指南带 `-p firesight` 启动全容器栈**，避免数据卷串到一起。删掉任何一个文件都会破坏对应的使用场景。

## 1、架构总览

| 容器 | 镜像 | 作用 | 对外端口 |
|---|---|---|---|
| firesight-web | 本地构建（Caddy） | 前端静态页面 + 反向代理 `/api` | **80 / 443** |
| firesight-backend | 本地构建（Python 3.x） | FastAPI 后端 + YOLO 推理/训练 | 无（仅内网） |
| firesight-postgres | pgvector/pgvector:pg15 | 业务数据 + 向量知识库 | 无（仅内网） |
| firesight-redis | redis:7-alpine | 缓存 / 会话 | 无（仅内网） |
| firesight-minio | minio/minio | 图片与数据集对象存储 | 无（仅内网） |

浏览器只访问 80/443，其余服务都收在内部网络里，后端启动时自动执行数据库迁移（alembic）。

## 2、前置要求

- Docker Desktop（Windows 需启用 WSL2 后端）或 Linux 原生 Docker + Compose v2
- 空闲的 **80 端口**（443 可选，仅配域名时需要）
- 磁盘 ≥ 10 GB：后端镜像含 PyTorch/YOLO，**首次构建需下载约 2~3 GB 依赖，耗时 5~15 分钟属正常**
- 无需本地安装 Python / Node，全部在容器内构建

## 3、配置环境变量

在仓库根目录执行：

```bash
cp deploy/full.env.example .env
```

编辑 `.env`，各项说明：

| 变量 | 是否必填 | 说明 |
|---|---|---|
| `POSTGRES_PASSWORD` | **必填** | 数据库密码，不填 compose 直接拒绝启动 |
| `MINIO_ROOT_PASSWORD` | **必填** | MinIO 密码（≥8 位） |
| `JWT_SECRET_KEY` | **必填** | 登录令牌签名密钥，用 `openssl rand -hex 32` 生成 |
| `DOMAIN` | 本地留空 | 有公网域名才填；**本地体验请置空**（走 HTTP 80 端口） |
| `REFRESH_COOKIE_SECURE` | 本地改 `false` | 无 HTTPS 时必须为 `false`，否则登录态无法保持 |
| `QWEN_API_KEY` | 可选 | AI 对话 / 智能体功能的大模型 Key（阿里云 DashScope），不填则对话功能不可用，检测/训练不受影响 |
| `EMBEDDING_API_KEY` | 可选 | 知识库 RAG 的向量化 Key，不填则知识库检索不可用 |

本地最小可用示例：

```ini
DOMAIN=
REFRESH_COOKIE_SECURE=false
POSTGRES_DB=rsod_agent
POSTGRES_USER=rsod_admin
POSTGRES_PASSWORD=firesight_pg_2026
MINIO_ROOT_USER=minioadmin
MINIO_ROOT_PASSWORD=firesight_minio_2026
MINIO_BUCKET=rsod-images
JWT_SECRET_KEY=<openssl rand -hex 32 的输出>
QWEN_API_KEY=sk-xxxx        # 没有可留空
EMBEDDING_API_KEY=          # 没有可留空
```

## 4、启动

**首次启动或遇到数据库密码认证失败时**，必须加 `-v` 删除旧数据卷，让 postgres 用 `.env` 里的新密码初始化：

```bash
docker compose -p firesight -f docker-compose.full.yml down -v
docker compose -p firesight -f docker-compose.full.yml up -d --build
```

**后续重启**（已有数据，不想清空）：

```bash
docker compose -p firesight -f docker-compose.full.yml restart
```

> **说明**：
> - `-p firesight` 指定独立项目名，避免和开发三件套（`docker-compose.yml`）的卷/服务名冲突
> - `-v` 会删除 postgres/redis/minio 的数据卷，**首次部署或改过 .env 密码时必须加**，否则会报 `password authentication failed`
> - 后续所有命令都要带 `-p firesight -f docker-compose.full.yml`

首次构建时间较长（下载 PyTorch 等），可以另开终端观察进度：

```bash
docker compose -p firesight -f docker-compose.full.yml logs -f backend
```

看到 `Application startup complete` 说明后端启动成功。

## 5、获取管理员账号（重要）

系统**首次启动时自动创建管理员**，用户名和临时密码只在后端日志里打印一次：

```bash
docker compose -p firesight -f docker-compose.full.yml logs backend | grep -B2 -A4 "临时密码"
```

会看到类似输出：

```
管理员账号：admin_xxxxxx
临时密码：xxxxxxxxxxxx
请立即登录并修改密码；该临时密码不会再次显示。
```

用它登录后立刻在「设置」里改密码。如果密码丢了，可重置：

```bash
docker exec -it firesight-backend python -m app.cli recover-admin
```

普通账号直接在登录页「注册」入口自助注册即可。

## 6、验证部署成功

```bash
# 1. 五个容器全部 Up (healthy)
docker compose -p firesight -f docker-compose.full.yml ps

# 2. 后端健康检查返回 JSON
curl http://localhost/api/health
```

然后浏览器打开 **http://localhost**（Windows + WSL 用户直接在 Windows 浏览器开即可，端口自动转发）：

1. 出现登录页 → 前端 OK
2. 用上一节的管理员账号登录成功 → 后端 + 数据库 OK
3. 「检测」页上传一张图片能出结果 → YOLO 推理 + MinIO OK

> 交付包已内置布匹瑕疵数据集（`backend/datasets/fdd_dataset/yolo_dataset/`，2692 张图、4 类瑕疵：划痕/破洞/污渍/织线异常）。因其挂载进了后端容器，登录后在「数据集管理」页可直接看到，「训练」页可直接发起训练验证完整流程。

## 7、数据持久化与常用命令

数据存放：数据库/缓存/对象存储在 docker 卷（`firesight_postgres_data` 等）；数据集、训练产物、日志直接落在宿主机 `backend/datasets`、`backend/runs`、`backend/logs` 目录，重建容器不丢失。

```bash
# 查看后端日志
docker compose -p firesight -f docker-compose.full.yml logs -f backend

# 改了代码后重建并重启（增量）
docker compose -p firesight -f docker-compose.full.yml up -d --build

# 停止（保留数据）
docker compose -p firesight -f docker-compose.full.yml down

# 彻底清空（连数据卷一起删，慎用！）
docker compose -p firesight -f docker-compose.full.yml down -v
```

## 8、常见问题

| 现象 | 原因与处理 |
|---|---|
| `must set POSTGRES_PASSWORD` 等报错直接退出 | `.env` 没建或必填项没填，回到第三节 |
| 80 端口被占用 | Windows 常见占用者是 IIS / 其他 web 服务；改掉占用方，或把 compose 里 `"80:80"` 改成 `"8080:80"` 后访问 `http://localhost:8080` |
| backend 一直 `health: starting` | 首次启动要加载 YOLO，给了 60 秒宽限期，耐心等；超过 2 分钟看 `logs backend` |
| 登录后一刷新就掉线 | 本地 HTTP 部署忘了把 `REFRESH_COOKIE_SECURE` 改成 `false` |
| AI 对话报错 / 无响应 | 没填 `QWEN_API_KEY`，属预期行为，不影响检测功能 |
| WSL 提示 `检测到 localhost 代理配置，但未镜像到 WSL` | 无害警告（Windows 开了代理软件所致），可忽略；如需消除可在 `.wslconfig` 启用镜像网络 |
| 基础镜像（python/node/caddy）拉取极慢 | Docker Desktop → Settings → Docker Engine 配置 `registry-mirrors` 镜像加速器后重跑。注意：加速器只管 Docker Hub，**管不到容器内 pip/npm 的下载**，那类超时见下一节 |
| 构建到 `RUN pip install torch` 时报 `Read timed out` | 从 pytorch 官源下载超时，国内网络最常见的失败点，处理方法见下面 8.1 |

### 8.1 torch / pip 下载超时的处理

后端镜像构建时要从 `download.pytorch.org` 下载约 200MB 的 CPU 版 torch，国内直连很容易超时。把 `backend/Dockerfile` 中的两条 pip 命令改成国内镜像源：

```dockerfile
# 原来：
# RUN pip install --no-cache-dir torch torchvision \
#     --index-url https://download.pytorch.org/whl/cpu
# 改为（阿里云 torch CPU 轮子镜像 + 清华 PyPI 镜像）：
RUN pip install --no-cache-dir torch torchvision \
    -f https://mirrors.aliyun.com/pytorch-wheels/cpu/ \
    -i https://pypi.tuna.tsinghua.edu.cn/simple

# 下面 requirements 那条同理加清华源：
RUN pip install --no-cache-dir -r requirements.txt \
    -i https://pypi.tuna.tsinghua.edu.cn/simple
```

改完重新执行 `docker compose -p firesight -f docker-compose.full.yml up -d --build` 即可——前面已完成的层（apt 依赖等）有缓存，会直接跳到失败的那一步继续。

> 同理，`web` 镜像的 `npm install` 慢的话，可在 `deploy/web.Dockerfile` 的 `npm install` 前加一行：
> `RUN npm config set registry https://registry.npmmirror.com`
