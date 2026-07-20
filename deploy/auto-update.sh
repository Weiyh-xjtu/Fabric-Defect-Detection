#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════
# FIRESIGHT 服务器端自动更新脚本
# 由 systemd timer（firesight-autoupdate.timer）每 5 分钟调用：
# 检测跟踪分支有新提交时自动拉取并增量部署。
#
# 依赖 /etc/firesight-deploy.conf（由 local-deploy.sh 生成）：
#   APP_DIR / WEB_ROOT / RUN_USER / DEPLOY_BRANCH / PROXY_MODE
#
# 手动触发一次：sudo bash /opt/firesight/deploy/auto-update.sh
# 停用自动更新：sudo systemctl disable --now firesight-autoupdate.timer
# ═══════════════════════════════════════════════════════
set -Eeuo pipefail

CONF="/etc/firesight-deploy.conf"
[[ -f "$CONF" ]] || { echo "[auto-update] 缺少 $CONF，跳过"; exit 0; }
# shellcheck disable=SC1090
source "$CONF"

log() { printf '[auto-update] %s\n' "$*"; }

# 防止上一轮更新未结束时 timer 再次触发
LOCK="/var/lock/firesight-autoupdate.lock"
exec 9>"$LOCK"
flock -n 9 || { log "另一个更新正在进行，跳过"; exit 0; }

cd "$APP_DIR"

# ── 1. 检查是否有新提交 ─────────────────────────────
git fetch origin "$DEPLOY_BRANCH" --quiet
LOCAL="$(git rev-parse HEAD)"
REMOTE="$(git rev-parse "origin/$DEPLOY_BRANCH")"
if [[ "$LOCAL" == "$REMOTE" ]]; then
  exit 0   # 无新提交，静默退出（timer 高频调用，不刷日志）
fi
log "发现新提交：${LOCAL:0:8} → ${REMOTE:0:8}（分支 $DEPLOY_BRANCH）"

# ── 2. 训练保护：重启后端会杀掉进行中的训练任务 ───────
POSTGRES_USER="$(grep -oP '^POSTGRES_USER=\K.*' "$APP_DIR/.env" 2>/dev/null || echo rsod_admin)"
POSTGRES_DB="$(grep -oP '^POSTGRES_DB=\K.*' "$APP_DIR/.env" 2>/dev/null || echo rsod_agent)"
RUNNING="$(docker exec rsod-agent-postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc \
  "SELECT count(*) FROM training_tasks WHERE status IN ('running','stopping');" 2>/dev/null || echo "?")"
if [[ "$RUNNING" != "?" && "$RUNNING" != "0" ]]; then
  log "检测到 ${RUNNING} 个训练任务进行中，本轮跳过（下轮 timer 会重试）"
  exit 0
fi

# ── 3. 同步代码 ─────────────────────────────────────
CHANGED="$(git diff --name-only "$LOCAL" "$REMOTE")"
git checkout "$DEPLOY_BRANCH" --quiet 2>/dev/null \
  || git checkout -b "$DEPLOY_BRANCH" "origin/$DEPLOY_BRANCH" --quiet
git reset --hard "origin/$DEPLOY_BRANCH" --quiet
chown -R "$RUN_USER:$RUN_USER" "$APP_DIR"

# ── 4. 后端有变更：依赖 / 迁移 / 重启 ─────────────────
if grep -q '^backend/' <<<"$CHANGED"; then
  cd "$APP_DIR/backend"
  if grep -q '^backend/requirements.txt$' <<<"$CHANGED"; then
    log "requirements.txt 有变更，更新依赖"
    ./.venv/bin/pip install -q -r requirements.txt
  fi
  log "执行数据库迁移并重启后端"
  ./.venv/bin/alembic upgrade head
  systemctl restart firesight-backend
else
  log "后端无变更，跳过重启"
fi

# ── 5. 前端有变更：重新构建并发布 ────────────────────
if grep -q '^frontend/' <<<"$CHANGED"; then
  log "重新构建前端"
  cd "$APP_DIR/frontend"
  npm install --no-audit --no-fund --silent
  npm run build --silent
  rm -rf "${WEB_ROOT:?}"/*
  cp -r dist/* "$WEB_ROOT/"
else
  log "前端无变更，跳过构建"
fi

# 注意：deploy/（Caddyfile、nginx 配置、service 文件）的变更不自动应用，
# 反代/服务配置改动风险高，请登录服务器手动处理。
if grep -qE '^deploy/|^docker-compose' <<<"$CHANGED"; then
  log "警告：deploy/ 或 compose 文件有变更，未自动应用，请手动检查"
fi

# ── 6. 健康检查 ─────────────────────────────────────
ok=0
for _ in $(seq 1 12); do
  if curl -fsS --max-time 5 http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
    ok=1; break
  fi
  sleep 5
done
if [[ "$ok" == "1" ]]; then
  log "更新完成：$(git log -1 --oneline)"
else
  log "错误：更新后健康检查失败！请查看 journalctl -u firesight-backend -e"
  exit 1
fi
