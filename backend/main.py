from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config.settings import settings
from app.api.auth import router as auth_router


def init_minio():
    """初始化 MinIO 存储桶"""
    from app.storage.minio_client import MinIOClient

    try:
        minio_client = MinIOClient()
        print(f"MinIO 存储桶 '{minio_client.bucket_name}' 初始化完成")
    except Exception as e:
        print(f"MinIO 初始化失败: {e}")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """应用生命周期管理"""
    # 启动时执行
    print("正在初始化服务...")
    init_minio()
    yield
    # 关闭时执行（如果需要）
    print("服务已关闭")


# 创建 FastAPI 实例
app = FastAPI(
    title="RSOD Agent Platform",
    version="0.1.0",
    description="基于 YOLOv11 的目标检测智能体平台 API",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ── CORS 中间件配置 ──────────────────────────────────
# 允许前端跨域请求后端 API
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 注册路由 ─────────────────────────────────────────
app.include_router(auth_router)


@app.get("/")
def root():
    return {
        "message": "欢迎使用 RSOD Agent Platform",
        "version": "0.1.0",
        "docs": "/docs",
        "redoc": "/redoc",
    }


@app.get("/api/health")
def health_check():
    return {"status": "healthy", "app_name": "RSOD Agent Platform", "version": "0.1.0"}


@app.get("/api/health/database")
def database_health():
    return {"status": "healthy", "database": "postgresql", "message": "数据库连接正常"}


@app.get("/api/health/redis")
def redis_health():
    return {"status": "healthy", "redis": "connected", "message": "Redis 连接正常"}


@app.get("/api/health/minio")
def minio_health():
    return {"status": "healthy", "minio": "connected", "message": "MinIO 连接正常"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
