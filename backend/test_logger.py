from app.core.logger import get_logger

logger = get_logger(__name__)

x = 42
username = "testuser"
e = Exception("connection refused")
free = 128

logger.debug("调试信息：变量 x = %s", x)
logger.info("用户 %s 登录成功", username)
logger.warning("Redis 连接超时，正在重试...")
logger.error("数据库查询失败: %s", str(e))
logger.critical("磁盘空间不足！剩余 %d MB", free)
