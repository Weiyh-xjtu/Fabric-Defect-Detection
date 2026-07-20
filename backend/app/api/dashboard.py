"""数据看板统计 API。"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.permissions import require_permission
from app.core.rbac import DASHBOARD_READ_ANY
from app.database.session import get_db
from app.entity.db_models import User
from app.services.dashboard_service import dashboard_service

router = APIRouter(prefix="/api/dashboard", tags=["数据看板"])


def _validate_range(start_date: date | None, end_date: date | None) -> None:
    """校验自定义时间段，起始日期不得晚于结束日期。"""
    if start_date and end_date and start_date > end_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="开始日期不能晚于结束日期",
        )


@router.get("/statistics", summary="汇总统计")
async def get_statistics(
    days: int = Query(30, ge=1, le=365),
    start_date: date | None = Query(None, description="自定义起始日期（含），优先于 days"),
    end_date: date | None = Query(None, description="自定义结束日期（含），优先于 days"),
    class_name: list[str] | None = Query(None, description="按缺陷类别过滤，可多值"),
    scene_id: int | None = Query(None, ge=1, description="按检测场景隔离统计"),
    user_id: int | None = Query(None, ge=1, description="按检测员工过滤"),
    _current_user: User = Depends(require_permission(DASHBOARD_READ_ANY)),
    db: Session = Depends(get_db),
) -> dict:
    """获取任务、图片、目标、耗时及环比统计，支持时间段、缺陷类别与场景过滤。"""
    _validate_range(start_date, end_date)
    return dashboard_service.get_statistics(
        db, user_id, days, start_date, end_date, class_name, scene_id
    )


@router.get("/trend", summary="每日检测趋势")
async def get_trend(
    days: int = Query(30, ge=1, le=365),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    class_name: list[str] | None = Query(None, description="按缺陷类别过滤，可多值"),
    scene_id: int | None = Query(None, ge=1, description="按检测场景隔离统计"),
    user_id: int | None = Query(None, ge=1, description="按检测员工过滤"),
    _current_user: User = Depends(require_permission(DASHBOARD_READ_ANY)),
    db: Session = Depends(get_db),
) -> dict:
    """获取时间段内的每日任务、图片和目标数量，支持缺陷类别与场景过滤。"""
    _validate_range(start_date, end_date)
    return dashboard_service.get_trend(
        db, user_id, days, start_date, end_date, class_name, scene_id
    )


@router.get("/defect-trend", summary="缺陷类别趋势对比")
async def get_defect_trend(
    days: int = Query(30, ge=1, le=365),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    class_name: list[str] | None = Query(None, description="指定缺陷类别，可多值；留空取 Top N"),
    top_n: int = Query(8, ge=1, le=30, description="未指定类别时展示目标数最多的前 N 类"),
    scene_id: int | None = Query(None, ge=1, description="按检测场景隔离统计"),
    user_id: int | None = Query(None, ge=1, description="按检测员工过滤"),
    _current_user: User = Depends(require_permission(DASHBOARD_READ_ANY)),
    db: Session = Depends(get_db),
) -> dict:
    """获取按缺陷类别拆分的每日趋势，用于多折线对比。"""
    _validate_range(start_date, end_date)
    return dashboard_service.get_defect_trend(
        db, user_id, days, start_date, end_date, class_name, top_n, scene_id
    )


@router.get("/defect-options", summary="可选缺陷类别")
async def get_defect_options(
    days: int = Query(30, ge=1, le=365),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    scene_id: int | None = Query(None, ge=1, description="按检测场景隔离统计"),
    user_id: int | None = Query(None, ge=1, description="按检测员工过滤"),
    _current_user: User = Depends(require_permission(DASHBOARD_READ_ANY)),
    db: Session = Depends(get_db),
) -> dict:
    """获取时间段内实际出现过的缺陷类别，用于前端筛选下拉。"""
    _validate_range(start_date, end_date)
    return dashboard_service.get_defect_options(
        db, user_id, days, start_date, end_date, scene_id
    )


@router.get("/scene-options", summary="可选检测场景")
async def get_scene_options(
    _current_user: User = Depends(require_permission(DASHBOARD_READ_ANY)),
    db: Session = Depends(get_db),
) -> dict:
    """获取启用的检测场景，用于看板场景筛选下拉。"""
    return dashboard_service.get_scene_options(db)


@router.get("/employee-options", summary="可选检测员工")
async def get_employee_options(
    _current_user: User = Depends(require_permission(DASHBOARD_READ_ANY)),
    db: Session = Depends(get_db),
) -> dict:
    """获取产生过检测记录的员工，用于看板员工筛选下拉。"""
    return dashboard_service.get_employee_options(db)


@router.get("/class-dist", summary="类别分布统计")
async def get_class_distribution(
    days: int = Query(30, ge=1, le=365),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    class_name: list[str] | None = Query(None, description="按缺陷类别过滤，可多值"),
    scene_id: int | None = Query(None, ge=1, description="按检测场景隔离统计"),
    user_id: int | None = Query(None, ge=1, description="按检测员工过滤"),
    _current_user: User = Depends(require_permission(DASHBOARD_READ_ANY)),
    db: Session = Depends(get_db),
) -> dict:
    """获取检测目标类别分布。"""
    _validate_range(start_date, end_date)
    return dashboard_service.get_class_distribution(
        db, user_id, days, start_date, end_date, class_name, scene_id
    )


@router.get("/scene-dist", summary="场景分布统计")
async def get_scene_distribution(
    days: int = Query(30, ge=1, le=365),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    scene_id: int | None = Query(None, ge=1, description="按检测场景隔离统计"),
    user_id: int | None = Query(None, ge=1, description="按检测员工过滤"),
    _current_user: User = Depends(require_permission(DASHBOARD_READ_ANY)),
    db: Session = Depends(get_db),
) -> dict:
    """获取检测任务的场景分布。"""
    _validate_range(start_date, end_date)
    return dashboard_service.get_scene_distribution(
        db, user_id, days, start_date, end_date, scene_id
    )


@router.get("/type-dist", summary="任务类型分布统计")
async def get_type_distribution(
    days: int = Query(30, ge=1, le=365),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    scene_id: int | None = Query(None, ge=1, description="按检测场景隔离统计"),
    user_id: int | None = Query(None, ge=1, description="按检测员工过滤"),
    _current_user: User = Depends(require_permission(DASHBOARD_READ_ANY)),
    db: Session = Depends(get_db),
) -> dict:
    """获取单图、批量、ZIP、视频、摄像头等类型分布。"""
    _validate_range(start_date, end_date)
    return dashboard_service.get_type_distribution(
        db, user_id, days, start_date, end_date, scene_id
    )
