# FIRESIGHT 生产部署指南

目标服务器：`<服务器公网IP>`（Ubuntu/Debian，无 GPU）
域名：`<你的域名>`

架构：
- PostgreSQL + Redis + MinIO 用 Docker Compose，端口只绑定 `127.0.0.1`
- 后端 FastAPI 用 venv + systemd 原生运行，监听 `127.0.0.1:8000`
- 前端 Vite 构建产物由 Caddy 托管，Caddy 反代 `/api` 并自动签发 HTTPS

```text
浏览器 ──HTTPS──▶ Caddy :443
                  ├─ /api、/docs、WebSocket ─▶ 127.0.0.1:8000 (uvicorn)
                  └─ 其余静态资源 ───────────▶ /var/www/firesight
后端 ─▶ 127.0.0.1: 5432 / 6379 / 9000（compose 容器）
```

---

## 0. 部署前：DNS 与防火墙

1. 在域名服务商把 `<你的域名>` 的 **A 记录**指向 `<服务器公网IP>`。
   验证（本地）：`nslookup <你的域名>` 应返回该 IP。
2. 服务器只放行必要端口。DigitalOcean 用 `ufw`：

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp      # Let's Encrypt HTTP-01 挑战必须开
sudo ufw allow 443/tcp     # HTTPS
sudo ufw enable
```

数据库/Redis/MinIO **不要**开放到公网，它们只绑定在 `127.0.0.1`。

---

## 1. 安装系统依赖

```bash
sudo apt update
sudo apt install -y python3.10 python3.10-venv python3-pip git ffmpeg \
    libgl1 libglib2.0-0    # opencv 运行所需的系统库

# Docker + Compose 插件
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER   # 重新登录后生效

# Caddy（官方源）
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy
```

> 若服务器 Python 不是 3.10，可用 `deadsnakes` PPA 装 `python3.10`。项目要求 3.10.20，次要版本一致即可。

---

## 2. 拉取代码

```bash
sudo mkdir -p /opt/firesight
sudo chown $USER:$USER /opt/firesight
git clone <你的仓库地址> /opt/firesight
cd /opt/firesight
git checkout main   # 或你要部署的分支
```

后续文档假设项目根目录是 `/opt/firesight`。

---

## 3. 启动基础设施（Docker）

```bash
cd /opt/firesight

# 基础设施密码
cp deploy/infra.env.prod.example .env
nano .env        # 填入强密码（POSTGRES_PASSWORD / MINIO_ROOT_PASSWORD 等）

docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml up -d
docker compose ps    # 三个容器应为 healthy
```

生产覆盖文件把端口改成只绑 `127.0.0.1`，并从 `.env` 读取密码。

---

## 4. 配置并启动后端

```bash
cd /opt/firesight/backend

# 生产环境变量
cp ../deploy/backend.env.prod.example .env
nano .env
#   - DB_PASSWORD / MINIO_SECRET_KEY 要与根目录 .env 里的一致
#   - JWT_SECRET_KEY 用 `openssl rand -hex 32` 生成
#   - 填入真实 QWEN_API_KEY / EMBEDDING_API_KEY

# 虚拟环境
python3.10 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip

# 先装 CPU 版 PyTorch（无 GPU 服务器必须，否则会拉超大的 CUDA 包甚至失败）
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu

# 再装其余依赖（ultralytics 会复用已装好的 CPU torch）
pip install -r requirements.txt

# 数据库迁移（首次且仅需一次）
alembic upgrade head
```

用 systemd 托管后端：

```bash
# 建一个专用运行用户（可选但推荐）
sudo useradd -r -s /usr/sbin/nologin firesight || true
sudo chown -R firesight:firesight /opt/firesight

sudo cp /opt/firesight/deploy/firesight-backend.service /etc/systemd/system/
# 按实际情况检查 service 里的 User / WorkingDirectory / ExecStart 路径
sudo systemctl daemon-reload
sudo systemctl enable --now firesight-backend
sudo systemctl status firesight-backend
journalctl -u firesight-backend -f    # 看启动日志，首次会打印 bootstrap 管理员凭据
```

> 首次启动时后端会创建一次性管理员账号并把用户名/密码打印到日志（`journalctl`），**请立刻记下并登录后修改**。

---

## 5. 构建并部署前端

前端 `.env` 已用相对路径 `VITE_API_BASE_URL=/api`，同源部署无需改动。
构建可以在本地或服务器上做（服务器需装 Node 20+）。以服务器为例：

```bash
# 安装 Node 20（nvm 或 nodesource 均可）
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo bash -
sudo apt install -y nodejs

cd /opt/firesight/frontend
npm ci
npm run build      # 产出 frontend/dist

# 部署到 Caddy 的站点目录
sudo mkdir -p /var/www/firesight
sudo cp -r dist/* /var/www/firesight/
sudo mkdir -p /var/log/caddy
```

---

## 6. 配置 Caddy 并上线

```bash
sudo cp /opt/firesight/deploy/Caddyfile /etc/caddy/Caddyfile
sudo systemctl restart caddy
sudo systemctl status caddy
journalctl -u caddy -f    # 观察证书签发是否成功
```

Caddy 会自动向 Let's Encrypt 申请证书（需要 80/443 可达 + DNS 已生效）。

打开 `https://<你的域名>` 验证：
- 页面能加载，`/api/health`（或登录）正常
- 地址栏是 HTTPS（摄像头功能依赖安全上下文）
- 用日志里的 bootstrap 管理员登录

---

## 7. 更新发布

```bash
cd /opt/firesight
git pull

# 后端
cd backend && source .venv/bin/activate
pip install -r requirements.txt        # 依赖有变化时
alembic upgrade head                   # 有新迁移时
sudo systemctl restart firesight-backend

# 前端
cd ../frontend && npm ci && npm run build
sudo rm -rf /var/www/firesight/* && sudo cp -r dist/* /var/www/firesight/
```

---

## 一键部署（GitHub Actions）

首次按上文手动部署一次后，后续更新可以全自动：push 到 `development` 分支
（或在 Actions 页面手动触发 Deploy workflow）即完成"前端构建 → 代码同步 →
依赖/迁移 → 重启后端 → 更新 Caddy → 健康检查"。

敏感信息全部放在仓库 **Settings → Secrets and variables → Actions**，
仓库文件中不出现真实 IP/域名/密钥。

### 所需 Secrets

| Secret | 必填 | 说明 |
|---|---|---|
| `SSH_PRIVATE_KEY` | ✅ | 部署专用 SSH 私钥（见下文生成方法） |
| `SERVER_HOST` | ✅ | 服务器公网 IP |
| `DOMAIN` | ✅ | 站点域名（不带 https://） |
| `JWT_SECRET_KEY` | ✅ | `openssl rand -hex 32` 生成 |
| `SERVER_USER` | | SSH 用户，默认 `root` |
| `SERVER_PORT` | | SSH 端口，默认 `22` |
| `POSTGRES_PASSWORD` | | 默认 `rsod_admin`；⚠️ 数据卷初始化后修改此值不会改数据库真实密码 |
| `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` | | 默认 `minioadmin` |
| `QWEN_API_KEY` / `QWEN_BASE_URL` / `QWEN_MODEL` | | 聊天 LLM（OpenAI 兼容） |
| `EMBEDDING_API_KEY` / `EMBEDDING_BASE_URL` / `EMBEDDING_MODEL` / `EMBEDDING_DIM` | | 知识库向量化 |

### 生成部署密钥

本地执行：

```bash
ssh-keygen -t ed25519 -f firesight_deploy -N "" -C "github-actions-deploy"
# 公钥追加到服务器
ssh root@<服务器IP> "cat >> ~/.ssh/authorized_keys" < firesight_deploy.pub
# 私钥全文（含 BEGIN/END 行）粘贴到 Secret SSH_PRIVATE_KEY
cat firesight_deploy
```

### 行为说明

- 前端在 CI 免费机器上构建，不占服务器资源；产物打包 scp 到服务器解压。
- 服务器代码用 `git reset --hard origin/<分支>` 同步：仓库内文件的服务器
  本地改动会被覆盖（`.env`、datasets、runs 等未跟踪文件不受影响）。
- 检测到训练任务 `running`/`stopping` 时部署会自动中止，避免重启后端
  杀掉训练；确要部署可手动触发并勾选 `force`。
- Caddyfile 里的 `<你的域名>` 占位符在部署时由 `DOMAIN` Secret 替换。
- 密钥文件在服务器上落盘于 /tmp，脚本退出时即删除。

---

## 备份要点

- PostgreSQL 数据卷、MinIO 数据卷、`backend/runs/train`（训练权重）要定期备份。
- `docker compose` 卷：`postgres_data`、`minio_data`。
- PG 逻辑备份示例：
  `docker exec rsod-agent-postgres pg_dump -U rsod_admin rsod_agent > backup.sql`

## 常见问题

- **证书签发失败**：确认 DNS 已指向服务器、80 端口对公网开放、`journalctl -u caddy` 看具体错误。
- **后端起不来**：`journalctl -u firesight-backend -e`，多为 `.env` 密码与容器不一致或 `alembic upgrade` 没跑。
- **torch 装成了 CUDA 版/占用巨大**：先装 CPU 版 torch 再装 requirements（见第 4 步顺序）。
- **上传大文件 413**：Caddyfile 里已设 `max_size 2GB`，可按需调大。

