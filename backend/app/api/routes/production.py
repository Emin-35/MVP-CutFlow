"""
Production Endpoints (v3) — YENİ DOSYA

Accountant üretim sürecini yönetir:
  - Üretim olayı kaydeder (metal_arrived, cutting_started, cutting_stopped,
    cutting_started_again, cutting_done, ready_count_updated)
  - ready_count_updated olayı orders.ready_count alanını da günceller
  - Her olay manager'a "production_updated" bildirimi gönderir
  - Üretim olaylarını listeler (sipariş detayı / manager dashboard için)

NOT: order_completed olayı burada DEĞİL — sipariş tamamlama final fatura
     akışında (order_ocr_helper.submit_final_invoice) gerçekleşir.

Yetki: require_accountant (accountant + manager). Ancak kayıt aksiyonu için
       siparişin 'active' olması gerekir.
"""
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.security import get_current_user, require_accountant
from app.db.base import get_db
from app.models.models import (
    AuditAction, Order, OrderStatus, ProductionEvent,
    ProductionEventType, User,
)
from app.schemas.schemas import ProductionEventCreate, ProductionEventOut
from app.services.audit import log_action
from app.services.notification_service import notify_production_updated

router = APIRouter(prefix="/production", tags=["production"])


# ─────────────────────────────────────────
# ÜRETİM OLAYI EKLE  (accountant)
# ─────────────────────────────────────────

@router.post("/{order_id}/add-event", response_model=ProductionEventOut, status_code=201)
def add_production_event(
    order_id: int,
    payload: ProductionEventCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_accountant),
):
    """
    Accountant üretim olayı kaydeder. Sipariş 'active' olmalıdır.
    ready_count_updated olayında orders.ready_count senkronlanır.
    Manager'a production_updated bildirimi gider.
    """
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı.")
    if order.status != OrderStatus.active:
        raise HTTPException(
            status_code=400,
            detail="Üretim olayları yalnızca aktif siparişlere eklenebilir.",
        )
    # order_completed olayı bu endpoint üzerinden eklenemez
    if payload.event_type == ProductionEventType.order_completed:
        raise HTTPException(
            status_code=400,
            detail="Sipariş tamamlama, final fatura akışı üzerinden yapılır.",
        )

    event = ProductionEvent(
        order_id=order.id,
        event_type=payload.event_type,
        note=payload.note,
        ready_count=payload.ready_count,
        created_by=current_user.id,
    )
    db.add(event)

    # ready_count_updated → orders.ready_count senkronla
    audit_action = AuditAction.production_step_updated
    if payload.event_type == ProductionEventType.ready_count_updated:
        order.ready_count = payload.ready_count
        order.updated_at  = datetime.now(timezone.utc)
    elif payload.note:
        # cutting_stopped vb. notlu olay
        audit_action = AuditAction.production_note_added

    db.flush()  # event.id

    log_action(db, audit_action, request, current_user.id, order.id,
               new_value={
                   "event_type": payload.event_type.value,
                   "note": payload.note,
                   "ready_count": payload.ready_count,
               })

    notify_production_updated(
        db, order.id,
        message=f'"{order.order_title}" üretim güncellemesi: {payload.event_type.value}',
    )

    db.commit()
    db.refresh(event)
    return event


# ─────────────────────────────────────────
# ÜRETİM OLAYLARINI LİSTELE
# ─────────────────────────────────────────

@router.get("/{order_id}/events", response_model=List[ProductionEventOut])
def list_production_events(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Siparişe ait üretim olaylarını kronolojik sırada döner.
    Tüm roller görebilir (manager dashboard, accountant takip, vb.).
    """
    if not db.query(Order).filter(Order.id == order_id).first():
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı.")

    return (
        db.query(ProductionEvent)
        .filter(ProductionEvent.order_id == order_id)
        .order_by(ProductionEvent.created_at)
        .all()
    )
