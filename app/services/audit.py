"""
Audit Log Servisi — her kritik işlemde çağrılır
"""
from decimal import Decimal
from typing import Any, Dict, Optional
from sqlalchemy.orm import Session

from app.models.models import AuditLog, AuditAction


def _serialize(obj: Any) -> Any:
    """Decimal ve diğer JSON serialize edilemeyen tipleri dönüştür"""
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_serialize(i) for i in obj]
    return obj


def log_action(
    db: Session,
    action: AuditAction,
    user_id: Optional[int] = None,
    order_id: Optional[int] = None,
    old_value: Optional[Dict[str, Any]] = None,
    new_value: Optional[Dict[str, Any]] = None,
    ip_address: Optional[str] = None,
) -> None:
    entry = AuditLog(
        user_id=user_id,
        order_id=order_id,
        action=action,
        old_value=_serialize(old_value) if old_value else None,
        new_value=_serialize(new_value) if new_value else None,
        ip_address=ip_address,
    )
    db.add(entry)
    db.flush()
