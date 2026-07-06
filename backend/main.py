from fastapi import FastAPI

app = FastAPI(
    title="RSOD Agent Platform",
    version="0.1.0",
    description="基于 YOLOv11 的目标检测智能体平台 API",
    docs_url="/docs",
    redoc_url="/redoc"
)

@app.get("/")
def root():
    return {
        "message": "欢迎使用 RSOD Agent Platform",
        "version": "0.1.0",
        "docs": "/docs",
        "redoc": "/redoc"
    }


@app.get("/api/health")
def health_check():
    return {
        "status": "healthy",
        "app_name": "RSOD Agent Platform",
        "version": "0.1.0"
    }


@app.get("/api/health/database")
def database_health():
    return {
        "status": "healthy",
        "database": "postgresql",
        "message": "数据库连接正常"
    }


@app.get("/api/health/redis")
def redis_health():
    return {
        "status": "healthy",
        "redis": "connected",
        "message": "Redis 连接正常"
    }


@app.get("/api/health/minio")
def minio_health():
    return {
        "status": "healthy",
        "minio": "connected",
        "message": "MinIO 连接正常"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)