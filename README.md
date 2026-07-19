# FIRESIGHT 布匹表面缺陷智能检测平台

基于 YOLOv11、FastAPI、Vue 3 和 LangChain/LangGraph 构建的织物表面缺陷检测平台。项目覆盖数据集管理、模型训练与评估、模型版本管理、图片/批量/视频/摄像头检测、检测历史、数据看板、用户权限、知识库和智能对话等完整流程。

当前应用版本：`0.1.0`。

## 主要功能

- 数据集 ZIP 上传、结构识别、自动划分、类别中英文配置和数据体检。
- YOLOv11 训练任务创建、指标轮询、停止训练、验证、结果下载和模型导出。
- 模型版本激活、归档、备份、恢复、测试和评估。
- 单图、多图、ZIP、视频、本机摄像头和 IP Webcam 检测。
- 检测任务、缺陷类别、场景、趋势和任务类型统计。
- 基于 JWT、Refresh Cookie 和 RBAC 的认证授权。
- 支持 PDF、Markdown、TXT 的知识库管理和 pgvector 检索。
- 多 Agent 智能对话，可调用检测、统计、知识检索和管理工具。
- 聊天附件、头像和检测结果通过同源文件代理访问，远程浏览器不需要直接连接 MinIO。

## 系统架构

```text
浏览器
  │
  │ http://<host>:5173
  ▼
Vue 3 / Vite
  │
  │ /api、WebSocket
  ▼
FastAPI :8000
  ├─ PostgreSQL 15 + pgvector :5432
  ├─ Redis 7 :6379
  ├─ MinIO :9000
  ├─ Ultralytics YOLOv11
  └─ Qwen / OpenAI-compatible LLM
```

浏览器使用相对地址 `/api` 请求后端。头像、聊天附件和检测图片使用 `/api/files/{token}` 访问，由 FastAPI 从 MinIO 流式转发，因此远程使用时无需向浏览器开放 MinIO 端口。

## 技术栈

| 层级 | 技术 |
| --- | --- |
| 后端 | Python 3.10.20、FastAPI、SQLAlchemy 2、Alembic、Pydantic 2 |
| 检测与训练 | Ultralytics YOLOv11、PyTorch、OpenCV、Pillow |
| 智能体 | LangChain、LangGraph、OpenAI-compatible API |
| 前端 | Vue 3、Vite、Element Plus、Pinia、Axios、ECharts |
| 数据库 | PostgreSQL 15、pgvector |
| 缓存 | Redis 7 |
| 对象存储 | MinIO |
| 测试 | pytest、pytest-asyncio、Vitest |

## 项目目录

```text
Fabric-Defect-Detection/
├─ backend/
│  ├─ main.py                    # FastAPI 入口
│  ├─ app/api/                   # API 路由
│  ├─ app/services/              # 检测和业务服务
│  ├─ app/agent/                 # Agent、工具和会话记忆
│  ├─ app/training/              # 训练、验证、导出和历史恢复
│  ├─ app/entity/                # SQLAlchemy 模型与 Pydantic Schema
│  ├─ app/storage/               # Redis、MinIO 客户端
│  ├─ app/rag/                   # 文档加载和知识检索
│  ├─ alembic/                   # 数据库迁移
│  ├─ datasets/                  # 归一化后的训练数据集
│  ├─ runs/train/                # 训练输出、指标和权重
│  ├─ knowledge_base/            # 知识库文档
│  ├─ tests/                     # 后端测试
│  └─ .env.example               # 后端配置模板
├─ frontend/
│  ├─ src/views/                 # 页面
│  ├─ src/components/            # 公共组件
│  ├─ src/stores/                # Pinia 状态
│  ├─ src/utils/request.js       # Axios 客户端，baseURL 为 /api
│  ├─ tests/                     # 前端测试
│  └─ .env.example               # 前端配置模板
├─ docs/                         # 实现记录和背景文档
├─ docker-compose.yml            # PostgreSQL、Redis、MinIO
└─ README.md
```


## 环境要求

- Windows 或 Linux。
- Python `3.10.20`
- Node.js `^20.19.0` 或 `>=22.12.0`。
- npm。
- Docker 和 Docker Compose，可用远程 PostgreSQL、Redis、MinIO。
- 建议安装 FFmpeg，用于将检测视频转码为兼容性更好的 H.264。
- 使用 GPU 训练时，需要后端运行环境安装与显卡驱动匹配的 CUDA/PyTorch 版本。

## 端口说明

| 服务 | 默认地址 | 用途 |
| --- | --- | --- |
| 前端 | `http://localhost:5173` | Web 页面 |
| 后端 | `http://localhost:8000` | FastAPI |
| OpenAPI | `http://localhost:8000/docs` | API 调试 |
| PostgreSQL | `localhost:5432` | 业务数据库和 pgvector |
| Redis | `localhost:6379` | 限流、会话记忆、异步状态 |
| MinIO API | `localhost:9000` | 后端对象存储访问 |
| MinIO Console | `http://localhost:9001` | MinIO 管理界面 |

## 快速启动

下面以 Windows PowerShell 为例。后端命令必须从 `backend/` 目录运行，否则 `datasets`、`runs/train` 和 `logs` 等相对路径可能指向错误位置。

### 1. 启动基础设施

`docker-compose.yml` 只负责 PostgreSQL、Redis 和 MinIO，不包含前端和后端应用。

```powershell
cd D:\path\to\Fabric-Defect-Detection
docker compose up -d
docker compose ps
```

开发用 Compose 默认配置为：

```text
PostgreSQL database: rsod_agent
PostgreSQL user:     rsod_admin
PostgreSQL password: rsod_admin
MinIO user:          minioadmin
MinIO password:      minioadmin
```

这些默认值仅适合本地开发，生产环境必须修改密码。

如果基础设施运行在远程主机，不需要执行 `docker compose up`，直接在后端环境变量中配置远程地址即可。

### 2. 配置后端

```powershell
Copy-Item backend\.env.example backend\.env
```

至少检查以下配置：

```env
# PostgreSQL
DB_HOST=localhost
DB_PORT=5432
DB_NAME=rsod_agent
DB_USER=rsod_admin
DB_PASSWORD=rsod_admin

# Redis
REDIS_HOST=localhost
REDIS_PORT=6379

# MinIO
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET=rsod-images
MINIO_SECURE=false

# 认证
JWT_SECRET_KEY=replace-with-a-long-random-secret

# 允许直接访问后端的前端来源
ALLOWED_ORIGINS=http://localhost:5173
```

数据库连接由 `DB_HOST`、`DB_PORT`、`DB_NAME`、`DB_USER`、`DB_PASSWORD` 构造。当前实现不会使用 `DATABASE_URL` 覆盖这些字段，只修改 `DATABASE_URL` 不会生效。

如果后端也运行在 Docker 网络中，通常应配置：

```env
DB_HOST=postgres
REDIS_HOST=redis
MINIO_ENDPOINT=minio:9000
```

MinIO 地址只需要后端可达，浏览器不会直接使用该地址。

### 3. 配置聊天模型和 Embedding

当前聊天模型选择顺序是：有效的 Qwen 配置优先，否则使用 OpenAI 配置。

Qwen 示例：

```env
QWEN_API_KEY=your-real-api-key
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_MODEL=qwen-plus
```

OpenAI-compatible 示例：

```env
OPENAI_API_KEY=your-real-api-key
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o-mini
```

知识库向量化可使用独立配置：

```env
EMBEDDING_API_KEY=your-embedding-api-key
EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
EMBEDDING_MODEL=text-embedding-v3
EMBEDDING_DIM=1024
```

Embedding 返回维度必须与 `EMBEDDING_DIM` 一致。未配置或向量服务不可用时，知识检索会降级为本地词法检索。

`USE_LOCAL_LLM`、`OLLAMA_BASE_URL` 和 `OLLAMA_MODEL` 已预留，但当前聊天 Agent 主路径实际使用 Qwen/OpenAI-compatible 客户端。

### 4. 安装后端依赖并迁移数据库

```powershell
cd backend

# 激活虚拟环境 这里以conda作示例
# 除非明确指出，本文档中所有命令均在虚拟环境中执行
conda create -n myenv python=3.10.20 -y
conda activate myenv

python -m pip install --upgrade pip
pip install -r requirements.txt

# 数据库迁移，第一次运行项目时必须且只需执行一次
alembic upgrade head
```

不要跳过 Alembic。应用启动不会自动创建或升级数据库表。

### 5. 启动后端

```powershell
cd backend

python main.py
```

后端监听 `0.0.0.0:8000`，并启用开发热重载。启动过程中会：

1. 初始化系统角色和权限。
2. 在系统没有管理员时创建一次性管理员账号。
3. 初始化 MinIO Bucket。
4. 扫描 `runs/train` 恢复可识别的训练历史。

首次创建的管理员用户名和密码只会显示在交互式终端中，请立即保存并登录后修改密码。

如果忘记管理员密码，可在交互式终端运行：

```powershell
cd backend

python -m app.cli recover-admin
```

存在多个管理员时需要指定用户名：

```powershell
python -m app.cli recover-admin --username your_admin_name
```

### 6. 配置并启动前端

```powershell
Copy-Item frontend\.env.example frontend\.env
```

同一主机运行前后端时，推荐：

```env
VITE_API_BASE_URL=http://localhost:8000
VITE_APP_TITLE=RSOD Agent Platform
```

`VITE_API_BASE_URL` 是 Vite 开发服务器访问后端的地址，不是远程浏览器直接访问的地址。因此即使用户通过 `http://服务器IP:5173` 打开页面，只要 Vite 和后端在同一主机，仍可使用 `http://localhost:8000`。

`VITE_MINIO_URL` 当前不参与头像或附件加载；文件统一通过 `/api/files/{token}` 由后端代理。

启动前端：

```powershell
cd frontend
npm ci
npm run dev
```

访问：

- 本机：`http://localhost:5173`
- 局域网/远程：`http://<服务器IP>:5173`

远程访问只开放 5173 时，Vite 会代理 `/api` 和 WebSocket 到后端。请确认主机防火墙允许 5173 入站。

## 首次使用流程

新初始化的数据库没有检测场景和模型。推荐按以下顺序配置：

### 1. 登录管理员账号

使用后端首次启动时输出的一次性管理员账号登录。进入“系统设置”修改个人信息、头像和密码。

### 2. 创建数据集和检测场景

进入“模型训练”页面的数据集管理区域，上传 YOLO ZIP 数据集。支持以下结构：

标准结构：

```text
dataset.zip
├─ images/
│  ├─ train/
│  ├─ val/
│  └─ test/             # 可选
├─ labels/
│  ├─ train/
│  ├─ val/
│  └─ test/             # 可选
└─ data.yaml            # 推荐提供
```

Roboflow 结构：

```text
dataset.zip
├─ train/images + train/labels
├─ valid/images + valid/labels
├─ test/images + test/labels    # 可选
└─ data.yaml
```

平铺结构：

```text
dataset.zip
├─ image_001.jpg
├─ image_001.txt
├─ image_002.jpg
└─ image_002.txt
```

平铺数据会自动按约 `8:1:1` 划分。上传包最大为 2GB，上传暂存记录保留 6 小时。

上传确认时可以：

- 直接登记为检测场景；
- 仅上传数据集，稍后修改数据集名或英文类别名后再登记。

场景标识必须以小写字母开头，只能包含小写字母、数字和下划线，长度为 2～50 个字符。登记为场景后，数据集名和英文类别名将锁定。

归一化后的目录为：

```text
backend/datasets/{scene_name}/yolo_dataset/
```

建议在训练前先执行“数据体检”。

也可以直接将数据集解压放到后端datasets目录下，通过 模型训练-数据集管理 页面直接进行登记即可。

### 3. 启动训练

在“模型训练”页面选择已登记场景，配置：

- 基础模型，如 `yolo11n`、`yolo11s`、`yolo11m`；
- epochs；
- 图像尺寸；
- batch size；
- 训练设备，如 `cpu` 或 GPU 编号 `0`；
- 优化器、初始学习率和数据增强。

训练页面每 5 秒轮询任务状态和指标。训练产物保存在：

```text
backend/runs/train/{task_uuid}/
```

### 4. 验证、导出并激活模型

训练完成后：

1. 对 `train`、`val` 或 `test` 划分执行模型验证。
2. 检查 mAP、Precision、Recall 和各类别指标。
3. 导出模型版本，可选择备份到 MinIO。
4. 将模型设为默认，或在“模型管理”页面激活为全局模型。

检测、统计和智能对话中的检测工具都使用当前全局模型。没有全局可用模型时，检测接口会提示“尚未配置可用的全局检测模型”。

### 5. 开始检测

可从“智能对话”或“检测工作台”使用以下模式：

| 模式 | 说明 |
| --- | --- |
| 单图 | 上传一张图片并返回标注图和缺陷统计 |
| 批量图片 | 一次最多上传 20 张图片 |
| ZIP | 上传一个图片 ZIP，执行批量检测 |
| 视频 | 后台处理并轮询进度，返回标注视频和关键帧 |
| 本机摄像头 | 浏览器采集画面，通过 WebSocket 实时检测 |
| IP Webcam | 后端从经过安全校验的摄像头 URL 拉取视频帧 |

聊天附件限制：

- 单张图片最大 10MB；
- ZIP 最大 100MB；
- 视频最大 50MB；
- 同一条消息不能混合图片、ZIP 和视频；
- ZIP 或视频一次只能上传一个。

远程通过普通 HTTP 访问时，浏览器可能禁止本机摄像头权限。生产或跨主机使用摄像头时建议启用 HTTPS。

## 页面使用说明

| 页面 | 功能 |
| --- | --- |
| 智能对话 | 文本问答、附件检测、快捷检测、会话历史、历史附件复检 |
| 检测工作台 | 本机摄像头和 IP Webcam 实时检测 |
| 模型训练 | 数据集管理、训练、指标、验证、导出和结果下载 |
| 模型管理 | 全局模型切换、归档、备份、恢复、测试和评估 |
| 历史记录 | 检测任务查询、筛选、详情和删除 |
| 知识库 | 上传、删除 PDF/MD/TXT 文档并重建向量索引 |
| 数据看板 | 趋势、类别、场景和任务类型统计，支持 CSV 导出 |
| 用户管理 | 用户、角色、启禁状态和权限管理 |
| 系统设置 | 个人资料、头像和密码修改 |

## 用户角色与权限

系统启动时会幂等初始化以下角色：

| 角色 | 默认权限 |
| --- | --- |
| 普通质检人员 `quality_inspector` | 智能对话、知识检索、执行检测、查看本人历史 |
| 生产管理人员 `production_manager` | 智能对话、知识检索、查看全部历史、查看数据看板 |
| 系统管理员 `system_admin` | 用户、数据集、训练、模型、知识库和系统管理等全部权限 |

新注册且没有角色的用户会在 RBAC 初始化时补充为普通质检人员。管理员可以在“用户管理”页面调整角色和启用状态。

## 知识库使用

知识库文档保存在：

```text
backend/knowledge_base/
```

支持扩展名：

- `.pdf`
- `.md`
- `.txt`

上传或删除文档后，系统会在后台安排向量索引重建。检索优先使用 PostgreSQL pgvector；Embedding、数据库或索引不可用时，会自动降级为本地词法检索，并在结果中保留降级原因。

## 私有文件访问

数据库保存的是 MinIO 永久对象名，而不是浏览器可见的 `localhost:9000` URL。后端返回如下同源地址：

```text
/api/files/{signed_token}
```

文件令牌默认有效 7 天，可配置：

```env
FILE_ACCESS_TOKEN_EXPIRE_MINUTES=10080
```

令牌过期不会删除 MinIO 文件。重新加载用户信息、聊天历史或检测历史时，后端会生成新的链接。文件代理支持 HTTP Range，视频可以正常拖动和续播。

## API 概览

| 前缀 | 功能 |
| --- | --- |
| `/api/auth` | 注册、登录、刷新、退出、当前用户 |
| `/api/user` | 用户、角色、头像、资料和密码 |
| `/api/datasets` | 数据集上传、登记、名称配置和体检 |
| `/api/training` | 训练、状态、指标、验证、导出和下载 |
| `/api/models` | 模型版本管理、备份、恢复、测试和评估 |
| `/api/detection` | 单图、批量、ZIP、视频和摄像头检测 |
| `/api/chat` | 会话、附件上传和 SSE 流式对话 |
| `/api/history` | 检测历史和场景列表 |
| `/api/dashboard` | 数据统计和趋势 |
| `/api/knowledge` | 知识库文件、索引和检索 |
| `/api/files` | 私有 MinIO 文件流式代理 |
| `/api/health` | 基础健康检查 |

完整接口和请求 Schema 请查看：

```text
http://localhost:8000/docs
```

## 健康检查

```powershell
Invoke-RestMethod http://localhost:8000/api/health
```

拥有系统健康权限时可以查看详细依赖状态：

```text
GET /api/health/detail
```

Redis 不可用时会回退到后端进程内存，适合单进程开发，但无法在多个后端进程之间共享限流、会话记忆和视频任务状态。

## 测试与构建

后端测试：

```powershell
cd backend

python -m pytest -q
```

前端测试：

```powershell
cd frontend
npm run test:run
```

前端生产构建：

```powershell
cd frontend
npm run build
```

构建输出位于 `frontend/dist/`，该目录是生成产物，不应手工编辑。


## 数据库迁移

SQLAlchemy 模型位于：

```text
backend/app/entity/db_models.py
```

修改模型后应生成并检查 Alembic 迁移：

```powershell
cd backend

alembic revision --autogenerate -m "describe change"
alembic upgrade head
```

不要依赖应用运行时自动建表。

## 停止服务

前端和后端终端中按 `Ctrl+C`。

停止基础设施：

```powershell
docker compose stop
```

停止并删除容器但保留命名卷：

```powershell
docker compose down
```

除非确认不再需要数据库、MinIO 和 Redis 数据，否则不要使用 `docker compose down -v`。

## 常见问题

### 后端无法连接 PostgreSQL

- 确认容器或远程数据库已经启动。
- 核对 `DB_HOST`、`DB_PORT`、`DB_NAME`、`DB_USER`、`DB_PASSWORD`。
- 不要只修改 `DATABASE_URL`。
- 从 Docker 容器连接其他 Compose 服务时使用服务名 `postgres`，不要使用 `localhost`。

### 浏览器能打开页面，但 API 请求失败

- 确认后端监听 8000。
- 检查 `frontend/.env` 中的 `VITE_API_BASE_URL`。
- 修改前端 `.env` 后需要重启 Vite。
- 远程只开放 5173 时，Vite 所在主机必须能够访问后端 8000。

### 头像、附件或标注图无法加载

- 新版本应返回 `/api/files/...`，不应返回 `http://localhost:9000/...`。
- 确认后端能够访问 `MINIO_ENDPOINT`。
- 确认 Vite 或生产反向代理会把 `/api/files` 转发到 FastAPI。
- 文件令牌过期时，刷新页面以重新获取链接。

### 没有场景或检测提示没有模型

新系统不会自动创建场景和模型。请按“上传数据集并登记场景 → 训练 → 验证/导出 → 激活全局模型”的顺序初始化。

### 训练找不到 data.yaml

- 必须从 `backend/` 目录启动后端。
- 检查文件是否位于 `backend/datasets/{scene_name}/yolo_dataset/data.yaml`。
- 确认数据集已经完成上传确认，而不是仍停留在暂存阶段。

### 知识库向量索引构建失败

- 检查 PostgreSQL 和 pgvector。
- 检查 Embedding API Key、Base URL、模型和维度。
- 即使向量构建失败，本地词法检索仍可工作。

### 摄像头无法授权

除 `localhost` 外，大多数浏览器要求在 HTTPS 安全上下文中使用摄像头。远程部署请配置 HTTPS。

### 日志中出现 Pydantic 或 SQLAlchemy 警告

- Pydantic class-based `Config` 和 `model_` 命名空间警告目前不阻止运行。
- 启动查询后的 SQLAlchemy `ROLLBACK` 通常是会话清理。
- `APP_DEBUG=true` 时 SQL 输出较多，并可能因为 engine echo 和应用日志看起来重复。

## 生产部署建议

- 不要使用 Vite 开发服务器和 Uvicorn `reload` 作为正式生产服务。
- 使用 Nginx/Caddy 提供 `frontend/dist`，并反向代理 `/api` 和 WebSocket。
- 为站点启用 HTTPS，生产环境设置 `REFRESH_COOKIE_SECURE=true`。
- 将 `JWT_SECRET_KEY`、数据库密码、MinIO 密钥和 API Key 替换为高强度秘密。
- 精确配置 `ALLOWED_ORIGINS`，不要使用不受控的通配来源。
- PostgreSQL、Redis 和 MinIO 应仅在内网开放；浏览器文件访问统一经过 `/api/files`。
- 为 PostgreSQL、MinIO 数据和 `runs/train` 制定备份策略。
- 多后端进程部署时必须使用真实 Redis，不应依赖内存回退。

## 开发约定

- 所有新增 Python 函数应添加类型标注。
- API Schema 字段应保持与前端兼容，或同步修改前端。
- 前端 API 调用使用 `frontend/src/utils/request.js`，通常不要重复添加 `/api` 前缀。
- Bug 修复应增加聚焦测试，并运行完整相关测试套件。
- 不要将真实 `.env`、密钥、密码、模型权重和大体积训练产物提交到版本控制。
- 不要把 `frontend/node_modules`、`frontend/dist`、Python 缓存和训练输出当作源代码修改。

## License

