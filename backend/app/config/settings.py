"""
全局配置模块
使用 pydantic-settings 管理所有配置项，支持从 .env 文件和环境变量读取
加载优先级：环境变量（系统级别） > .env 文件 > 代码中的默认值
"""

from pathlib import Path

from pydantic_settings import BaseSettings


BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """应用全局配置"""

    # ── 训练配置 ──────────────────────────────────────
    TRAIN_OUTPUT_DIR: str = "runs/train"  # 训练输出目录（模型权重、日志等）
    DATASET_BASE_DIR: str = "datasets"    # 数据集根目录

    # ── 应用基础配置 ───────────────────────────────
    APP_NAME: str = "RSOD Agent Platform"
    APP_VERSION: str = "0.1.0"
    APP_DEBUG: bool = True

    # ── 日志配置 ──────────────────────────────────────
    LOG_LEVEL: str = "INFO"           # 日志级别
    LOG_DIR: str = "logs"             # 日志目录（相对于 backend/）
    LOG_MAX_BYTES: int = 10 * 1024 * 1024  # 单文件最大 10MB
    LOG_BACKUP_COUNT: int = 5         # 保留 5 份历史日志

    # ── 数据库配置 ───────────────────────────────
    DB_HOST: str = "localhost"
    DB_PORT: int = 5432
    DB_NAME: str = "rsod_agent"
    DB_USER: str = "rsod_admin"
    DB_PASSWORD: str = "rsod_admin"

    @property
    def DATABASE_URL(self) -> str:
        """构造 PostgreSQL 连接字符串"""
        return f"postgresql://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"

    # ── Redis 配置 ───────────────────────────────
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379

    @property
    def REDIS_URL(self) -> str:
        """构造 Redis 连接字符串"""
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/0"

    # ── MinIO 配置 ───────────────────────────────
    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin"
    MINIO_BUCKET: str = "rsod-agent-images"
    MINIO_SECURE: bool = False

    # ── JWT 认证配置 ───────────────────────────────
    JWT_SECRET_KEY: str = "your-super-secret-key-change-in-production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    # ── CORS 配置 ───────────────────────────────
    ALLOWED_ORIGINS: str = (
        "http://localhost:3000,http://localhost:5173,http://localhost:8080"
    )

    @property
    def cors_origins_list(self) -> list:
        """将 CORS 配置字符串转为列表"""
        return [origin.strip() for origin in self.ALLOWED_ORIGINS.split(",")]

    class Config:
        env_file = BACKEND_DIR / ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


# 创建全局单例，其他模块直接 import 使用
settings = Settings()
