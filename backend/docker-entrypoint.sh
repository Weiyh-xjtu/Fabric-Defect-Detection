#!/bin/sh
# FIRESIGHT 后端容器入口：等待数据库就绪并执行迁移，再启动应用
set -e

echo "[entrypoint] 执行数据库迁移（alembic upgrade head）"
ok=0
i=1
while [ "$i" -le 10 ]; do
  if alembic upgrade head; then
    ok=1
    break
  fi
  echo "[entrypoint] 迁移失败（数据库可能未就绪），${i}/10 次重试，3 秒后再试"
  sleep 3
  i=$((i + 1))
done
if [ "$ok" != "1" ]; then
  echo "[entrypoint] 数据库迁移始终失败，退出" >&2
  exit 1
fi

# workers=1：YOLO/训练依赖进程内状态与内存模型，多 worker 会各自加载模型且状态不共享
echo "[entrypoint] 启动 uvicorn"
exec uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
