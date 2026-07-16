"""检测历史记录 API。"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.permissions import require_any_permission, require_permission
from app.core.rbac import (
    HISTORY_DELETE_ANY,
    HISTORY_READ_ANY,
    HISTORY_READ_OWN,
    get_user_permission_codes,
)
from app.core.logger import get_logger
from app.database.session import get_db
from app.entity.db_models import User
from app.services.history_service import history_service

logger = get_logger(__name__)
router = APIRouter(prefix="/api/history", tags=["检测历史"])


@router.get("/tasks", summary="检测任务分页列表")
async def list_detection_tasks(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    task_type: str | None = Query(None),
    status: str | None = Query(None),
    scene_id: int | None = Query(None),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    keyword: str | None = Query(None, max_length=100),
    current_user: User = Depends(
        require_any_permission(HISTORY_READ_OWN, HISTORY_READ_ANY)
    ),
    db: Session = Depends(get_db),
) -> dict:
    """分页查询当前用户任务，支持类型、状态、场景、日期和关键词筛选。"""
    if start_date and end_date and start_date > end_date:
        raise HTTPException(status_code=422, detail="起始日期不能晚于结束日期")
    return history_service.list_tasks(
        db=db,
        user_id=(
            None
            if HISTORY_READ_ANY in get_user_permission_codes(db, current_user)
            else current_user.id
        ),
        page=page,
        page_size=page_size,
        task_type=task_type,
        status=status,
        scene_id=scene_id,
        start_date=start_date,
        end_date=end_date,
        keyword=keyword,
    )


@router.get("/tasks/{task_id}", summary="检测任务详情")
async def get_detection_task_detail(
    task_id: int,
    current_user: User = Depends(
        require_any_permission(HISTORY_READ_OWN, HISTORY_READ_ANY)
    ),
    db: Session = Depends(get_db),
) -> dict:
    """获取当前用户的一条任务及其检测结果。"""
    permissions = get_user_permission_codes(db, current_user)
    result = history_service.get_task_detail(
        db, None if HISTORY_READ_ANY in permissions else current_user.id, task_id
    )
    if result is None:
        raise HTTPException(status_code=404, detail="任务不存在或无权访问")
    return result


@router.delete("/tasks/{task_id}", summary="删除检测任务")
async def delete_detection_task(
    task_id: int,
    current_user: User = Depends(require_permission(HISTORY_DELETE_ANY)),
    db: Session = Depends(get_db),
) -> dict:
    """删除当前用户的一条任务及其关联结果。"""
    if not history_service.delete_task(db, None, task_id):
        raise HTTPException(status_code=404, detail="任务不存在或无权访问")
    logger.info("用户 %s 删除检测任务 #%d", current_user.username, task_id)
    return {"message": f"任务 #{task_id} 已删除", "task_id": task_id}


@router.get("/summary", summary="历史记录快速统计")
async def get_history_summary(
    current_user: User = Depends(
        require_any_permission(HISTORY_READ_OWN, HISTORY_READ_ANY)
    ),
    db: Session = Depends(get_db),
) -> dict:
    """获取当前用户历史记录摘要。"""
    permissions = get_user_permission_codes(db, current_user)
    return history_service.get_summary(
        db, None if HISTORY_READ_ANY in permissions else current_user.id
    )


@router.get("/scenes", summary="获取所有检测场景列表")
async def list_scenes(
    _current_user: User = Depends(
        require_any_permission(HISTORY_READ_OWN, HISTORY_READ_ANY)
    ),
    db: Session = Depends(get_db),
) -> dict:
    """获取历史筛选下拉框所需的启用场景。"""
    return {"scenes": history_service.list_scenes(db)}
