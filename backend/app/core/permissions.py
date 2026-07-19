"""Reusable FastAPI permission dependencies."""

from collections.abc import Callable

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.core.rbac import get_user_permission_codes
from app.database.session import get_db
from app.entity.db_models import User


def _forbidden() -> HTTPException:
    return HTTPException(status_code=403, detail="没有权限执行此操作")


def require_permission(code: str) -> Callable:
    def dependency(
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> User:
        if not current_user.is_superuser and code not in get_user_permission_codes(db, current_user):
            raise _forbidden()
        return current_user

    return dependency


def require_any_permission(*codes: str) -> Callable:
    required = set(codes)

    def dependency(
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> User:
        if current_user.is_superuser:
            return current_user
        if not required.intersection(get_user_permission_codes(db, current_user)):
            raise _forbidden()
        return current_user

    return dependency
