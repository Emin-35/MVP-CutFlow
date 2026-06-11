"""
app/schemas/notification_schemas.py

Bildirim, sipariş durum geçmişi ve audit log şemaları.
"""
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, computed_field

from app.models.enums import AuditAction, NotifCategory, NotifType, OrderStatus


# ─────────────────────────────────────────
# NOTIFICATION
# ─────────────────────────────────────────

class NotificationOut(BaseModel):
    id:         int
    order_id:   Optional[int]
    type:       NotifType
    message:    Optional[str]
    is_read:    bool
    created_at: datetime

    @computed_field  # type: ignore[misc]
    @property
    def category(self) -> NotifCategory:
        """Frontend'in gruplama/sıralama için kullandığı üst kategori (türetilir)."""
        return self.type.category

    class Config:
        from_attributes = True


class NotificationMarkRead(BaseModel):
    notification_ids: List[int]


# ─────────────────────────────────────────
# ORDER STATUS HISTORY
# ─────────────────────────────────────────

class StatusHistoryOut(BaseModel):
    id:         int
    old_status: Optional[OrderStatus]
    new_status: OrderStatus
    changed_by: int
    note:       Optional[str] = Field(None, max_length=300)
    created_at: datetime

    class Config:
        from_attributes = True


# ─────────────────────────────────────────
# AUDIT LOG
# ─────────────────────────────────────────

class AuditLogOut(BaseModel):
    id:         int
    user_id:    Optional[int]
    order_id:   Optional[int]
    action:     AuditAction
    old_value:  Optional[Dict[str, Any]]
    new_value:  Optional[Dict[str, Any]]
    created_at: datetime

    class Config:
        from_attributes = True
