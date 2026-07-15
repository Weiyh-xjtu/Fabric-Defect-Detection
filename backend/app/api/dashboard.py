"""数据看板统计 API。"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.database.session import get_db
from app.entity.db_models import User
from app.services.dashboard_service import dashboard_service

router = APIRouter(prefix="/api/dashboard", tags=["数据看板"])


@router.get("/statistics", summary="汇总统计")
async def get_statistics(
    days: int = Query(30, ge=1, le=365),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取任务、图片、目标、耗时及环比统计。"""
    return dashboard_service.get_statistics(db, current_user.id, days)


@router.get("/trend", summary="每日检测趋势")
async def get_trend(
    days: int = Query(30, ge=1, le=365),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取最近 N 天的每日任务、图片和目标数量。"""
    return dashboard_service.get_trend(db, current_user.id, days)


@router.get("/class-dist", summary="类别分布统计")
async def get_class_distribution(
    days: int = Query(30, ge=1, le=365),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取检测目标类别分布。"""
    return dashboard_service.get_class_distribution(db, current_user.id, days)


@router.get("/scene-dist", summary="场景分布统计")
async def get_scene_distribution(
    days: int = Query(30, ge=1, le=365),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取检测任务的场景分布。"""
    return dashboard_service.get_scene_distribution(db, current_user.id, days)


@router.get("/type-dist", summary="任务类型分布统计")
async def get_type_distribution(
    days: int = Query(30, ge=1, le=365),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取单图、批量、ZIP、视频、摄像头等类型分布。"""
    return dashboard_service.get_type_distribution(db, current_user.id, days)
