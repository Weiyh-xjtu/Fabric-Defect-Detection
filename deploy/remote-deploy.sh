#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════
# FIRESIGHT 服务器端部署脚本（由 GitHub Actions 的 Deploy workflow 调用）
# 用法: bash remote-deploy.sh <secrets.env 路径>
#
# secrets.env 由 CI 从 GitHub Secrets 生成并 scp 到服务器，
# 内容为 KEY=VALUE（bash printf %q 转义），本脚本退出时自动删除。
#
# 幂等，可重复执行。代码会强制同步到 origin/$DEPLOY_BRANCH：
# 服务器上对仓库内文件的手工改动会被覆盖；
# .env、datasets/、runs/ 等未跟踪文件不受影响。
# ═══════════════════════════════════════════════════════
set -Eeuo pipefail

SECRETS_FILE="${1:?用法: remote-deploy.sh <secrets.env 路径>}"
APP_DIR="${APP_DIR:-/opt/firesight}"
WEB_ROOT="${WEB_ROOT:-/var/www/firesight}"
DIST_TARBALL="${DIST_TARBALL:-/tmp/firesight-dist.tar.gz}"

cleanup() { rm -f "$SECRETS_FILE"; }
trap cleanup EXIT

# shellcheck disable=SC1090
source "$SECRETS_FILE"

log() { printf '\n[deploy] %s\n' "$*"; }

# ── 必填项与默认值 ───────────────────────────────────
: "${DOMAIN:?缺少 DOMAIN（GitHub Secrets 未配置）}"
: "${JWT_SECRET_KEY:?缺少 JWT_SECRET_KEY（GitHub Secrets 未配置）}"
DEPLOY_BRANCH="${DEPLOY_BRANCH:-development}"
FORCE_DEPLOY="${FORCE_DEPLOY:-0}"
POSTGRES_DB="${POSTGRES_DB:-rsod_agent}"
POSTGRES_USER="${POSTGRES_USER:-rsod_admin}"
# 注意：Postgres 数据卷首次初始化后密码即固化在库里，
# 之后改这个值不会改数据库实际密码，只会造成两边不一致。
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-rsod_admin}"
MINIO_ROOT_USER="${MINIO_ROOT_USER:-minioadmin}"
MINIO_ROOT_PASSWORD="${MINIO_ROOT_PASSWORD:-minioadmin}"
MINIO_BUCKET="${MINIO_BUCKET:-rsod-images}"
QWEN_API_KEY="${QWEN_API_KEY:-}"
QWEN_BASE_URL="${QWEN_BASE_URL:-https://dashscope.aliyuncs.com/compatible-mode/v1}"
QWEN_MODEL="${QWEN_MODEL:-qwen-plus}"
EMBEDDING_API_KEY="${EMBEDDING_API_KEY:-}"
EMBEDDING_BASE_URL="${EMBEDDING_BASE_URL:-}"
EMBEDDING_MODEL="${EMBEDDING_MODEL:-text-embedding-v3}"
EMBEDDING_DIM="${EMBEDDING_DIM:-1024}"

SUDO=""
[[ "$(id -u)" -ne 0 ]] && SUDO="sudo -n"

# ── 前置检查（首次安装请按 deploy/DEPLOY.md 手动完成） ──
command -v docker >/dev/null 2>&1 || { log "缺少 docker"; exit 1; }
command -v caddy >/dev/null 2>&1 || { log "缺少 caddy"; exit 1; }
command -v python3.10 >/dev/null 2>&1 || { log "缺少 python3.10"; exit 1; }
[[ -d "$APP_DIR/.git" ]] || { log "$APP_DIR 不是 git 仓库，请先 clone"; exit 1; }

# ── 训练保护：重启后端会杀掉跑在后端进程内的训练任务 ────
if [[ "$FORCE_DEPLOY" != "1" ]]; then
  RUNNING=$($SUDO docker exec rsod-agent-postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc \
    "SELECT count(*) FROM training_tasks WHERE status IN ('running','stopping');" 2>/dev/null || echo "?")
  if [[ "$RUNNING" == "?" ]]; then
    log "警告：无法查询训练状态（数据库容器未运行或无权限），继续部署"
  elif [[ "$RUNNING" != "0" ]]; then
    log "检测到 ${RUNNING} 个训练任务进行中；部署会重启后端并杀掉训练，已中止。"
    log "等训练结束再部署，或手动触发 workflow 时勾选 force。"
    exit 1
  fi
fi

# ── 1. 同步代码 ─────────────────────────────────────
log "同步代码到 origin/${DEPLOY_BRANCH}"
git -C "$APP_DIR" fetch origin "$DEPLOY_BRANCH"
git -C "$APP_DIR" checkout "$DEPLOY_BRANCH" 2>/dev/null \
  || git -C "$APP_DIR" checkout -b "$DEPLOY_BRANCH" "origin/$DEPLOY_BRANCH"
# reset --hard 兼容远程历史被改写的情况，普通 pull 会失败
git -C "$APP_DIR" reset --hard "origin/$DEPLOY_BRANCH"

# ── 2. 基础设施（PostgreSQL / Redis / MinIO） ─────────
log "写入基础设施 .env 并更新容器"
umask 077
cat > "$APP_DIR/.env" <<EOF
POSTGRES_DB=$POSTGRES_DB
POSTGRES_USER=$POSTGRES_USER
POSTGRES_PASSWORD=$POSTGRES_PASSWORD
MINIO_ROOT_USER=$MINIO_ROOT_USER
MINIO_ROOT_PASSWORD=$MINIO_ROOT_PASSWORD
EOF
$SUDO docker compose \
  -f "$APP_DIR/docker-compose.yml" \
  -f "$APP_DIR/deploy/docker-compose.prod.yml" \
  --project-directory "$APP_DIR" up -d

# ── 3. 后端配置、依赖、迁移、重启 ─────────────────────
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

log "安装后端依赖并执行数据库迁移"
cd "$APP_DIR/backend"
if [[ ! -x .venv/bin/python ]]; then
  python3.10 -m venv .venv
  ./.venv/bin/pip install --upgrade pip
  # 必须先装 CPU 版 torch，否则 requirements 里的 ultralytics 会拉 CUDA 版
  ./.venv/bin/pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
fi
./.venv/bin/pip install -q -r requirements.txt
./.venv/bin/alembic upgrade head

log "重启后端服务"
$SUDO systemctl restart firesight-backend

# ── 4. 前端静态资源（CI 已构建好，直接解包） ───────────
if [[ -f "$DIST_TARBALL" ]]; then
  log "部署前端静态资源"
  $SUDO mkdir -p "$WEB_ROOT"
  $SUDO rm -rf "${WEB_ROOT:?}"/*
  $SUDO tar -xzf "$DIST_TARBALL" -C "$WEB_ROOT"
  # umask 077 会让解包出的文件仅 root 可读，反代（www-data）读不了会 500
  $SUDO chmod -R a+rX "$WEB_ROOT"
  rm -f "$DIST_TARBALL"
else
  log "未发现前端构建包（$DIST_TARBALL），跳过前端更新"
fi

# ── 5. Caddy（域名从 Secrets 注入，仓库内只有占位符） ───
log "渲染 Caddyfile 并 reload"
$SUDO mkdir -p /var/log/caddy
sed "s|<你的域名>|$DOMAIN|g" "$APP_DIR/deploy/Caddyfile" | $SUDO tee /etc/caddy/Caddyfile >/dev/null
$SUDO systemctl reload caddy

# ── 6. 健康检查 ─────────────────────────────────────
log "等待后端就绪"
ok=0
for _ in $(seq 1 12); do
  if curl -fsS --max-time 5 http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
    ok=1
    break
  fi
  sleep 5
done
[[ "$ok" == "1" ]] || { log "后端健康检查失败，请查看 journalctl -u firesight-backend"; exit 1; }

if curl -fsS --max-time 10 "https://$DOMAIN/api/health" >/dev/null 2>&1; then
  log "部署完成：https://$DOMAIN 正常"
else
  log "后端正常，但 https://$DOMAIN 暂不可达（DNS/证书/防火墙），请手动确认"
fi
