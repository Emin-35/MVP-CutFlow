"""
Notifications Endpoints
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.models.models import Notification, User
from app.models.enums import NotifCategory, types_in_category
from app.schemas.schemas import NotificationOut, NotificationMarkRead
from app.core.security import get_current_user

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=List[NotificationOut])
def get_notifications(
    unread_only: bool = False,
    category: Optional[NotifCategory] = Query(
        None, description="Kategoriye göre filtre: order / user / settings"
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(Notification).filter(Notification.recipient_id == current_user.id)
    if unread_only:
        query = query.filter(Notification.is_read.is_(False))
    if category:
        # Kategori DB'de kolon değil; ilgili notif_type listesine çevrilir.
        query = query.filter(Notification.type.in_(types_in_category(category)))
    return query.order_by(Notification.created_at.desc()).all()


@router.post("/mark-read")
def mark_read(
    payload: NotificationMarkRead,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    db.query(Notification).filter(
        Notification.id.in_(payload.notification_ids),
        Notification.recipient_id == current_user.id,
    ).update({"is_read": True}, synchronize_session="fetch")
    db.commit()
    return {"updated": len(payload.notification_ids)}
