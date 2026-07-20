#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════
# FIRESIGHT 服务器本地一键部署脚本（首次部署 / 全量重装）
#
# 与 remote-deploy.sh 的区别：
#   - remote-deploy.sh 由 GitHub Actions 调用，只做增量更新，
#     且要求系统依赖已装好；
#   - 本脚本在服务器上手动运行，从零完成：系统依赖 → 防火墙 →
#     基础设施容器 → 后端 venv/迁移/systemd → 前端构建 → Caddy 上线。
#
# 用法（先把仓库 clone 到服务器，在仓库根目录执行）：
#   sudo bash deploy/local-deploy.sh
#
# 敏感信息（域名、密码、API Key 等）不写死在脚本里：
#   运行时交互式输入，自动填充到 .env / backend/.env / Caddyfile。
#   也可通过环境变量预设跳过对应提问，例如：
#   sudo DOMAIN=example.com QWEN_API_KEY=sk-xxx bash deploy/local-deploy.sh
#
# 幂等，可重复执行；已完成的步骤会自动跳过或安全覆盖。
# ═══════════════════════════════════════════════════════
set -Eeuo pipefail

log()  { printf '\n\033[1;32m[deploy]\033[0m %s\n' "$*"; }
warn() { printf '\n\033[1;33m[warn]\033[0m %s\n' "$*"; }
die()  { printf '\n\033[1;31m[error]\033[0m %s\n' "$*" >&2; exit 1; }

# ── 0. 前置检查 ─────────────────────────────────────
[[ "$(id -u)" -eq 0 ]] || die "请用 root 或 sudo 运行本脚本"
command -v apt-get >/dev/null 2>&1 || die "本脚本仅支持 Ubuntu/Debian（依赖 apt）"

# 脚本位于 deploy/ 下，仓库根目录即为部署目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(dirname "$SCRIPT_DIR")"
WEB_ROOT="${WEB_ROOT:-/var/www/firesight}"
RUN_USER="${RUN_USER:-firesight}"

[[ -f "$APP_DIR/docker-compose.yml" && -d "$APP_DIR/backend" ]] \
  || die "未在仓库根目录找到项目文件，请确认已完整 clone 仓库（当前推断根目录: $APP_DIR）"

log "部署目录: $APP_DIR"

# ── 1. 交互式收集配置（占位符在这里填充） ─────────────
# ask VAR "提示" "默认值"        —— 普通输入
# ask_secret VAR "提示" [默认值] —— 隐藏输入（密码/Key）
ask() {
  local var="$1" prompt="$2" default="${3:-}" value
  if [[ -n "${!var:-}" ]]; then return 0; fi   # 环境变量已预设则跳过
  while true; do
    if [[ -n "$default" ]]; then
      read -r -p "$prompt [$default]: " value
      value="${value:-$default}"
    else
      read -r -p "$prompt: " value
    fi
    [[ -n "$value" ]] && break
    echo "  该项为必填。"
  done
  printf -v "$var" '%s' "$value"
}

ask_secret() {
  local var="$1" prompt="$2" default="${3:-}" value
  if [[ -n "${!var:-}" ]]; then return 0; fi
  while true; do
    if [[ -n "$default" ]]; then
      read -r -s -p "$prompt [回车使用默认/自动生成]: " value; echo
      value="${value:-$default}"
    else
      read -r -s -p "$prompt: " value; echo
    fi
    [[ -n "$value" ]] && break
    echo "  该项为必填。"
  done
  printf -v "$var" '%s' "$value"
}

log "收集部署配置（可 Ctrl+C 中止，重跑不影响已完成步骤）"

ask        DOMAIN            "站点域名（不带 https://，例如 firesight.example.com）"
ask        SERVER_IP         "服务器公网 IP（用于 DNS 解析校验，留空跳过）" "skip"
ask        POSTGRES_DB       "PostgreSQL 数据库名" "rsod_agent"
ask        POSTGRES_USER     "PostgreSQL 用户名" "rsod_admin"
ask_secret POSTGRES_PASSWORD "PostgreSQL 密码（强密码）"
ask        MINIO_ROOT_USER   "MinIO 用户名" "minioadmin"
ask_secret MINIO_ROOT_PASSWORD "MinIO 密码（强密码，至少 8 位）"
ask        MINIO_BUCKET      "MinIO Bucket 名" "rsod-images"
ask_secret JWT_SECRET_KEY    "JWT 密钥" "$(openssl rand -hex 32)"
ask_secret QWEN_API_KEY      "聊天 LLM API Key（OpenAI 兼容 / Qwen）"
ask        QWEN_BASE_URL     "聊天 LLM Base URL" "https://dashscope.aliyuncs.com/compatible-mode/v1"
ask        QWEN_MODEL        "聊天 LLM 模型名" "qwen-plus"
ask_secret EMBEDDING_API_KEY "Embedding API Key（回车复用聊天 Key）" "$QWEN_API_KEY"
ask        EMBEDDING_BASE_URL "Embedding Base URL" "$QWEN_BASE_URL"
ask        EMBEDDING_MODEL   "Embedding 模型名" "text-embedding-v3"
ask        EMBEDDING_DIM     "Embedding 维度" "1024"

# DNS 校验（仅提示，不中断）
if [[ "$SERVER_IP" != "skip" ]]; then
  resolved="$(getent ahostsv4 "$DOMAIN" 2>/dev/null | awk 'NR==1{print $1}' || true)"
  if [[ "$resolved" == "$SERVER_IP" ]]; then
    log "DNS 校验通过：$DOMAIN → $SERVER_IP"
  else
    warn "DNS 校验未通过：$DOMAIN 当前解析为 '${resolved:-无}'，期望 $SERVER_IP"
    warn "Caddy 签发 HTTPS 证书要求 DNS 已生效，请确认 A 记录后再继续"
    read -r -p "仍然继续部署？(y/N): " go
    [[ "$go" == "y" || "$go" == "Y" ]] || die "已中止，请先配置 DNS"
  fi
fi

# ── 2. 安装系统依赖 ─────────────────────────────────
log "安装系统依赖（python3.10 / ffmpeg / opencv 运行库等）"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git curl ffmpeg libgl1 libglib2.0-0 ca-certificates \
  gnupg apt-transport-https debian-keyring debian-archive-keyring ufw

# python3.10：仓库无此包时走 deadsnakes PPA（Ubuntu 24.04 等）
if ! command -v python3.10 >/dev/null 2>&1; then
  if ! apt-get install -y -qq python3.10 python3.10-venv 2>/dev/null; then
    log "系统源无 python3.10，添加 deadsnakes PPA"
    apt-get install -y -qq software-properties-common
    add-apt-repository -y ppa:deadsnakes/ppa
    apt-get update -qq
    apt-get install -y -qq python3.10 python3.10-venv
  fi
else
  apt-get install -y -qq python3.10-venv 2>/dev/null || true
fi

# Docker + Compose 插件
if ! command -v docker >/dev/null 2>&1; then
  log "安装 Docker"
  curl -fsSL https://get.docker.com | sh
fi
docker compose version >/dev/null 2>&1 || die "docker compose 插件不可用，请检查 Docker 安装"

# Caddy（官方源）
if ! command -v caddy >/dev/null 2>&1; then
  log "安装 Caddy"
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -qq
  apt-get install -y -qq caddy
fi

# Node 20+（前端构建）
need_node=1
if command -v node >/dev/null 2>&1; then
  node_major="$(node -v | sed 's/^v\([0-9]*\).*/\1/')"
  [[ "$node_major" -ge 20 ]] && need_node=0
fi
if [[ "$need_node" == "1" ]]; then
  log "安装 Node.js 20"
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
  apt-get install -y -qq nodejs
fi

# ── 3. 防火墙 ───────────────────────────────────────
log "配置防火墙（放行 SSH/80/443，数据库等仅绑 127.0.0.1 不对外）"
ufw allow OpenSSH >/dev/null
ufw allow 80/tcp  >/dev/null
ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null
ufw status | sed 's/^/  /'

# ── 4. 基础设施（PostgreSQL / Redis / MinIO） ─────────
log "写入基础设施 .env 并启动容器"
umask 077
cat > "$APP_DIR/.env" <<EOF
POSTGRES_DB=$POSTGRES_DB
POSTGRES_USER=$POSTGRES_USER
POSTGRES_PASSWORD=$POSTGRES_PASSWORD
MINIO_ROOT_USER=$MINIO_ROOT_USER
MINIO_ROOT_PASSWORD=$MINIO_ROOT_PASSWORD
EOF

docker compose \
  -f "$APP_DIR/docker-compose.yml" \
  -f "$APP_DIR/deploy/docker-compose.prod.yml" \
  --project-directory "$APP_DIR" up -d

log "等待基础设施容器健康"
for _ in $(seq 1 30); do
  unhealthy="$(docker compose --project-directory "$APP_DIR" ps --format '{{.Name}} {{.Health}}' 2>/dev/null \
    | grep -cv 'healthy' || true)"
  [[ "$unhealthy" == "0" ]] && break
  sleep 3
done
docker compose --project-directory "$APP_DIR" ps

# ── 5. 后端：.env / venv / 依赖 / 迁移 ────────────────
log "写入 backend/.env"
cat > "$APP_DIR/backend/.env" <<EOF
DB_HOST=localhost
DB_PORT=5432
DB_NAME=$POSTGRES_DB
DB_USER=$POSTGRES_USER
DB_PASSWORD=$POSTGRES_PASSWORD

REDIS_HOST=localhost
REDIS_PORT=6379

MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=$MINIO_ROOT_USER
MINIO_SECRET_KEY=$MINIO_ROOT_PASSWORD
MINIO_BUCKET=$MINIO_BUCKET
MINIO_SECURE=false

JWT_SECRET_KEY=$JWT_SECRET_KEY
REFRESH_COOKIE_SECURE=true

APP_DEBUG=false
LOG_LEVEL=INFO
MODEL_TASK_PROCESS_ISOLATION=true

ALLOWED_ORIGINS=https://$DOMAIN

QWEN_API_KEY=$QWEN_API_KEY
QWEN_BASE_URL=$QWEN_BASE_URL
QWEN_MODEL=$QWEN_MODEL

EMBEDDING_API_KEY=$EMBEDDING_API_KEY
EMBEDDING_BASE_URL=$EMBEDDING_BASE_URL
EMBEDDING_MODEL=$EMBEDDING_MODEL
EMBEDDING_DIM=$EMBEDDING_DIM
EOF

log "创建 venv 并安装后端依赖（首次需数分钟）"
cd "$APP_DIR/backend"
if [[ ! -x .venv/bin/python ]]; then
  python3.10 -m venv .venv
  ./.venv/bin/pip install --upgrade pip -q
  # 无 GPU 服务器必须先装 CPU 版 torch，否则 ultralytics 会拉超大的 CUDA 包
  ./.venv/bin/pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
fi
./.venv/bin/pip install -q -r requirements.txt

log "执行数据库迁移（alembic upgrade head）"
./.venv/bin/alembic upgrade head

# ── 6. systemd 托管后端 ─────────────────────────────
log "创建运行用户并安装 systemd 服务"
id -u "$RUN_USER" >/dev/null 2>&1 || useradd -r -s /usr/sbin/nologin "$RUN_USER"
chown -R "$RUN_USER:$RUN_USER" "$APP_DIR"

# 按实际部署路径/用户渲染 service 文件（仓库模板默认 /opt/firesight）
sed -e "s|/opt/firesight|$APP_DIR|g" \
    -e "s|^User=.*|User=$RUN_USER|" \
    -e "s|^Group=.*|Group=$RUN_USER|" \
    "$APP_DIR/deploy/firesight-backend.service" > /etc/systemd/system/firesight-backend.service

systemctl daemon-reload
systemctl enable firesight-backend >/dev/null 2>&1
systemctl restart firesight-backend

# ── 7. 前端构建与发布 ───────────────────────────────
log "构建前端（npm install + build，首次需数分钟）"
cd "$APP_DIR/frontend"
# 前端同源部署走 /api 相对路径
printf 'VITE_API_BASE_URL=/api\nVITE_APP_TITLE=RSOD Agent Platform\n' > .env
npm install --no-audit --no-fund
npm run build

log "发布前端静态资源到 $WEB_ROOT"
mkdir -p "$WEB_ROOT"
rm -rf "${WEB_ROOT:?}"/*
cp -r dist/* "$WEB_ROOT/"

# ── 8. Caddy：填充域名占位符并上线 ────────────────────
log "渲染 Caddyfile（域名: $DOMAIN）并重载 Caddy"
mkdir -p /var/log/caddy
sed "s|<你的域名>|$DOMAIN|g" "$APP_DIR/deploy/Caddyfile" > /etc/caddy/Caddyfile
caddy validate --config /etc/caddy/Caddyfile >/dev/null
systemctl enable caddy >/dev/null 2>&1
systemctl restart caddy

# ── 9. 健康检查 ─────────────────────────────────────
log "等待后端就绪"
ok=0
for _ in $(seq 1 24); do
  if curl -fsS --max-time 5 http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
    ok=1; break
  fi
  sleep 5
done
[[ "$ok" == "1" ]] || die "后端健康检查失败，请查看: journalctl -u firesight-backend -e"

if curl -fsS --max-time 15 "https://$DOMAIN/api/health" >/dev/null 2>&1; then
  log "HTTPS 站点验证通过"
else
  warn "后端正常，但 https://$DOMAIN 暂不可达（DNS 未生效 / 证书签发中 / 防火墙）"
  warn "证书签发日志: journalctl -u caddy -f"
fi

# ── 10. 输出首次管理员凭据提示 ───────────────────────
log "部署完成！站点: https://$DOMAIN"
echo
echo "══════════════════ 后续操作 ══════════════════"
echo "1. 首次启动会创建一次性管理员账号，凭据打印在后端日志中："
echo "     journalctl -u firesight-backend | grep -iA3 -m1 '管理员\|admin'"
echo "   请立即登录并修改密码。忘记密码可执行："
echo "     cd $APP_DIR/backend && sudo -u $RUN_USER ./.venv/bin/python -m app.cli recover-admin"
echo "2. 敏感配置已写入（权限 600，勿提交到 git）："
echo "     $APP_DIR/.env            （基础设施密码）"
echo "     $APP_DIR/backend/.env    （后端全部配置）"
echo "3. 常用运维命令："
echo "     systemctl status firesight-backend   # 后端状态"
echo "     journalctl -u firesight-backend -f   # 后端日志"
echo "     docker compose --project-directory $APP_DIR ps   # 基础设施"
echo "     journalctl -u caddy -f               # Caddy/证书日志"
echo "4. 日常更新可继续使用 GitHub Actions 的 Deploy workflow。"
echo "══════════════════════════════════════════════"
