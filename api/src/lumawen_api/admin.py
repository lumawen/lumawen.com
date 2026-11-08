"""Admin-only API. The dashboard's admin tabs (users, mailing list, messages) build on this."""

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from .auth import DB, AdminUser, UserOut
from .models import User

router = APIRouter(prefix="/admin", tags=["admin"])


class UserPage(BaseModel):
    total: int
    users: list[UserOut]


@router.get("/users", response_model=UserPage)
def list_users(
    _: AdminUser,
    db: DB,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> UserPage:
    total = db.scalar(select(func.count()).select_from(User))
    users = db.scalars(
        select(User).order_by(User.created_at.desc()).limit(limit).offset(offset)
    ).all()
    return UserPage(total=total, users=[UserOut.of(u) for u in users])
