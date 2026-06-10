"""
Staff Endpoints (v3) — YENİ DOSYA

Staff iş akışı (sipariş oluşturma order_ocr_helper.py'de):
  - Aktif bir sipariş için EKSTRA metal talebi açar → buyer'a bildirim gider
  - Talep aynı order_id altına bağlanır; siparişin başka hiçbir alanı değişmez,
    yalnızca üstüne ekstra malzeme eklenir.

Yetki: require_staff (staff + manager).
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.security import require_staff
from app.db.base import get_db
from app.models.models import (
    AuditAction, ExtraMetalRequest, ExtraMetalStatus,
    Order, OrderStatus, User,
)
from app.schemas.schemas import ExtraMetalRequestCreate, ExtraMetalRequestOut
from app.services.audit import log_action
from app.services.notification_service import notify_extra_metal_requested

router = APIRouter(prefix="/staff", tags=["staff"])

# ─────────────────────────────────────────
# EKSTRA METAL TALEBİ AÇ  (staff → buyer)
# ─────────────────────────────────────────

@router.post("/{order_id}/request-extra-metal", response_model=ExtraMetalRequestOut, status_code=201)
def request_extra_metal(
    order_id: int,
    payload: ExtraMetalRequestCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    """
    Staff, aktif siparişe ekstra metal talebi açar.
    Sadece active (üretimi süren) siparişlere talep açılabilir.
    Talep pending_approval ile oluşur, buyer(lar)a bildirim gider.
    """
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı.")
    if order.status != OrderStatus.active:
        raise HTTPException(
            status_code=400,
            detail="Ekstra metal talebi yalnızca aktif (satın alınmış) siparişlere açılabilir.",
        )

    extra = ExtraMetalRequest(
        order_id=order.id,
        width=payload.width,
        length=payload.length,
        thickness=payload.thickness,
        material=payload.material,
        quantity=payload.quantity,
        kg=payload.kg,
        estimated_cost=payload.estimated_cost,   # schema validator'ı hesaplar
        reason=payload.reason,
        status=ExtraMetalStatus.pending_approval,
        created_by=current_user.id,
    )
    db.add(extra)
    db.flush()  # extra.id

    log_action(db, AuditAction.extra_metal_requested, request, current_user.id, order.id,
               new_value={
                   "extra_metal_request_id": extra.id,
                   "material": payload.material,
                   "quantity": payload.quantity,
                   "estimated_cost": str(payload.estimated_cost),
               })

    notify_extra_metal_requested(
        db, order.id,
        message=f'"{order.order_title}" ({order.order_number}) için ekstra metal talebi açıldı.',
    )

    db.commit()
    db.refresh(extra)
    return extra
