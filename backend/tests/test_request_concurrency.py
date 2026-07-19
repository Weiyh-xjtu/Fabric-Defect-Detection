"""防止同步 ORM 再次被放回 Web 事件循环。"""

import inspect
import os

from app.api import auth, models, training
from app.training.training_service import (
    shutdown_model_task_executor,
    submit_model_task,
)


def test_sync_database_hot_paths_run_in_fastapi_threadpool() -> None:
    """同步 SQLAlchemy 路径必须声明为 def，由 FastAPI 在线程池执行。"""
    handlers = [
        auth.get_current_user,
        auth.refresh_login_session,
        training.list_training_scenes,
        training.list_training_tasks,
        training.get_training_status,
        training.get_training_metrics,
        training.get_validation_status,
        models.list_model_versions,
        models.get_current_model,
        models.get_model_evaluation_status,
    ]

    assert all(not inspect.iscoroutinefunction(handler) for handler in handlers)


def test_model_tasks_execute_in_a_separate_process() -> None:
    """模型任务进程池必须与运行 API 的当前进程隔离。"""
    try:
        worker_pid = submit_model_task(os.getpid).result(timeout=20)
        assert worker_pid != os.getpid()
    finally:
        shutdown_model_task_executor()
