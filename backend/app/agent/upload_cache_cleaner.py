"""Periodic cleanup of the shared ``rsod_uploads`` attachment cache.

聊天附件（``chat.py``）、带会话的快捷检测（``detection.py``）以及历史还原
（``attachment_store.py`` 的 ``restored_*``）都会往系统临时目录下的
``rsod_uploads`` 落文件。这些文件是纯缓存——用户原图在 MinIO 有永久副本，
删掉后可按需重新下载还原——但此前没有任何删除逻辑，会无限期堆积。

清理策略：删除 mtime 早于保留窗口的文件。保留窗口默认取
``ConversationMemory.ttl`` 的 2 倍（见 settings.UPLOAD_CACHE_RETENTION_SECONDS），
确保不会删掉对话记忆仍在引用、但用户尚未“再检测一次”的文件。
"""

import asyncio
import os
import tempfile
import time

from app.config.settings import settings
from app.core.logger import get_logger

logger = get_logger(__name__)

# 与 chat.py / detection.py / attachment_store.py 保持一致的缓存目录。
UPLOAD_CACHE_DIR = os.path.join(tempfile.gettempdir(), "rsod_uploads")


def cleanup_upload_cache(retention_seconds: int | None = None) -> int:
    """删除 ``rsod_uploads`` 中 mtime 早于保留窗口的文件，返回删除数量。

    Args:
        retention_seconds: 保留窗口（秒）；为 None 时取配置默认值。

    Returns:
        成功删除的文件数量。
    """
    if retention_seconds is None:
        retention_seconds = settings.UPLOAD_CACHE_RETENTION_SECONDS

    if not os.path.isdir(UPLOAD_CACHE_DIR):
        return 0

    cutoff = time.time() - retention_seconds
    removed = 0
    try:
        entries = os.scandir(UPLOAD_CACHE_DIR)
    except OSError as exc:
        logger.warning("扫描上传缓存目录失败: %s", exc)
        return 0

    with entries:
        for entry in entries:
            try:
                if not entry.is_file():
                    continue
                if entry.stat().st_mtime >= cutoff:
                    continue
                os.unlink(entry.path)
                removed += 1
            except OSError as exc:
                # 文件可能正被并发使用或已被其他流程删除，跳过即可。
                logger.debug("删除缓存文件失败 %s: %s", entry.path, exc)

    if removed:
        logger.info("上传缓存清理完成：删除 %d 个过期文件", removed)
    return removed


async def upload_cache_cleanup_loop() -> None:
    """后台周期任务：按配置间隔重复清理，直到被取消。"""
    interval = max(60, settings.UPLOAD_CACHE_CLEANUP_INTERVAL_SECONDS)
    try:
        while True:
            await asyncio.sleep(interval)
            try:
                cleanup_upload_cache()
            except Exception as exc:  # 单次失败不应终止循环
                logger.warning("周期性上传缓存清理异常: %s", exc)
    except asyncio.CancelledError:
        raise
