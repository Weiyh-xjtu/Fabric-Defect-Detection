from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config.settings import settings
from app.core.exceptions import register_exception_handlers
from app.middleware.request_logger import RequestLogMiddleware
from app.api.auth import router as auth_router
from app.api.health import router as health_router
from app.api.training import router as training_router  # 【新增】导入训练路由
from app.api.chat import router as chat_router
from app.api.dashboard import router as dashboard_router
from app.api.detection import router as detection_router
from app.api.history import router as history_router
from app.middleware.rate_limiter import RateLimiterMiddleware
from app.api.user import router as user_router
from app.api.knowledge import router as knowledge_router

def init_minio():
    """初始化 MinIO 存储桶"""
    from app.storage.minio_client import MinIOClient

    try:
        minio_client = MinIOClient()
        print(f"MinIO 存储桶 '{minio_client.bucket_name}' 初始化完成")
    except Exception as e:
        print(f"MinIO 初始化失败: {e}")


def recover_training_history():
    """启动时从磁盘产物恢复训练历史，抵御数据库重建导致的记录丢失。"""
    from app.database.session import SessionLocal
    from app.training.training_service import training_service

    db = SessionLocal()
    try:
        result = training_service.rescan_tasks(db)
        if result["recovered"]:
            print(
                f"训练历史恢复完成：恢复 {result['recovered']} 条，"
                f"跳过 {result['skipped']} 条，失败 {result['failed']} 条"
            )
    except Exception as e:
        print(f"训练历史恢复失败: {e}")
    finally:
        db.close()


def init_rbac():
    """幂等初始化系统角色和权限。"""
    from app.core.rbac import initialize_rbac
    from app.database.session import SessionLocal

    db = SessionLocal()
    try:
        initialize_rbac(db)
        print("RBAC 角色与权限初始化完成")
    except Exception as e:
        db.rollback()
        print(f"RBAC 初始化失败: {e}")
    finally:
        db.close()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """应用生命周期管理"""
    # 启动时执行
    print("正在初始化服务...")
    init_rbac()
    init_minio()
    recover_training_history()
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

# ── 注册全局异常处理器 ─────────────────────────────────
register_exception_handlers(app)

# ── 注册中间件（注意顺序）──────────────────────────────
# 中间件执行顺序：后添加的先执行（洋葱模型）
# 1. CORS 中间件（最先执行，处理跨域）
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 2. 请求日志中间件（在 CORS 之后执行）
app.add_middleware(RequestLogMiddleware)

# 速率限制中间件
app.add_middleware(RateLimiterMiddleware)

# ── 注册路由 ─────────────────────────────────────────
app.include_router(auth_router)
app.include_router(health_router)
app.include_router(training_router)  # 【新增】注册训练路由
app.include_router(chat_router)  # Day 8 新增
app.include_router(detection_router) # Day 8 新增
app.include_router(dashboard_router)   # 【Day10 新增】
app.include_router(history_router)     # 【Day10 新增】
app.include_router(user_router)        # 【Day10 新增】
app.include_router(knowledge_router)   # 【Day11 知识库检索】

@app.get("/")
def root():
    return {
        "message": "欢迎使用 RSOD Agent Platform",
        "version": "0.1.0",
        "docs": "/docs",
        "redoc": "/redoc",
    }


if __name__ == "__main__":
    from pathlib import Path

    import uvicorn

    backend_dir = Path(__file__).resolve().parent
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_dirs=[str(backend_dir / "app")],
        reload_excludes=["logs/*", "*.log", "runs/*", "datasets/*"],
    )
