"""
Buyer Endpoints (v3) — YENİ DOSYA

Buyer iş akışı:
  - Yeni siparişi görüntüler, satın alır → status: pending_approval → active
    (audit: order_buyed, status → active; accountant'a bildirim gider)
  - "Satın Alınacaklar" sayfası: onay bekleyen / onaylanmış ekstra metal talepleri,
    her satır hangi siparişe (order_title) ait olduğunu gösterir
  - Ekstra metal talebini tekil veya toplu (batch) onaylar/satın alır
  - Sadece buyer ve manager erişebilir

Yetki: require_buyer (buyer + manager).
       Manager her şeyi GÖREBİLİR; satın alma aksiyonunu da yapabilir
       (require_buyer manager'ı kapsıyor).
"""
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.core.security import require_buyer
from app.db.base import get_db
from app.models.models import (
    AuditAction, ExtraMetalRequest, ExtraMetalStatus,
    Order, OrderStatus, OrderStatusHistory, User,
)
from app.schemas.schemas import (
    BatchExtraMetalAction, ExtraMetalApprove, OrderBuy, OrderStatusOut,
    PurchasePageItemOut,
)
from app.services.audit import log_action, _serialize
from app.services.notification_service import notify_order_buyed

router = APIRouter(prefix="/buyer", tags=["buyer"])


# ─────────────────────────────────────────
# SATIN AL / REDDET  (pending_approval → active)
# ─────────────────────────────────────────

@router.post("/{order_id}/buy-order", response_model=OrderStatusOut)
def buy_order(
    order_id: int,
    payload: OrderBuy,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_buyer),
):
    """
    Buyer siparişi satın alır → active, veya reddeder → cancelled.
    Onaylanınca accountant(lar)a bildirim gider (üretimi başlatması için).
    """
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı.")
    if order.status != OrderStatus.pending_approval:
        raise HTTPException(status_code=400, detail="Sipariş satın alınmayı bekleyen durumda değil.")

    old_status = order.status

    if payload.approved:
        order.status     = OrderStatus.active
        order.bought_by  = current_user.id
        order.bought_at  = datetime.now(timezone.utc)
        order.updated_at = datetime.now(timezone.utc)
        audit_action = AuditAction.order_buyed
        history_note = "Buyer satın aldı, sipariş aktif."
    else:
        if not payload.rejection_reason:
            raise HTTPException(status_code=400, detail="Red sebebi girilmeli.")
        order.status           = OrderStatus.cancelled
        order.rejection_reason = payload.rejection_reason
        order.updated_at       = datetime.now(timezone.utc)
        audit_action = AuditAction.order_rejected
        history_note = f"Buyer reddetti: {payload.rejection_reason}"

    db.add(OrderStatusHistory(
        order_id=order.id, old_status=old_status,
        new_status=order.status, changed_by=current_user.id,
        note=history_note,
    ))
    log_action(db, audit_action, request, current_user.id, order.id,
               old_value={"status": _serialize(old_status)},
               new_value={"status": _serialize(order.status)})

    if payload.approved:
        notify_order_buyed(
            db, order.id,
            message=f'"{order.order_title}" ({order.order_number}) satın alındı, üretime hazır.',
        )

    db.commit()
    db.refresh(order)
    return order


# ─────────────────────────────────────────
# SATIN ALINACAKLAR SAYFASI
# ─────────────────────────────────────────

@router.get("/purchase-list", response_model=List[PurchasePageItemOut])
def purchase_list(
    status: Optional[ExtraMetalStatus] = Query(
        None, description="Filtre: pending_approval / approved / purchased / rejected"
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_buyer),
):
    """
    Sadece buyer ve manager görür. Ekstra metal taleplerini siparişle birlikte listeler.
    Varsayılan: tüm aktif (pending_approval + approved) talepler.
    Her satır order_title/order_number gibi sipariş bilgilerini de içerir.
    """
    query = db.query(ExtraMetalRequest).join(Order, ExtraMetalRequest.order_id == Order.id)

    if status:
        query = query.filter(ExtraMetalRequest.status == status)
    else:
        query = query.filter(ExtraMetalRequest.status.in_([
            ExtraMetalStatus.pending_approval, ExtraMetalStatus.approved,
        ]))

    rows = query.order_by(ExtraMetalRequest.created_at.desc()).all()
    return [PurchasePageItemOut.from_model(r) for r in rows]


# ─────────────────────────────────────────
# EKSTRA METAL — TEKİL ONAY / RED
# ─────────────────────────────────────────

@router.post("/extra-metal/{request_id}/approve", response_model=PurchasePageItemOut)
def approve_extra_metal(
    request_id: int,
    payload: ExtraMetalApprove,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_buyer),
):
    """
    Buyer tek bir ekstra metal talebini onaylar (approved) veya reddeder (rejected).
    Onay → "Satın Alınacaklar" listesinde satın almaya hazır hâle gelir.
    """
    req = db.query(ExtraMetalRequest).filter(ExtraMetalRequest.id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Ekstra metal talebi bulunamadı.")
    if req.status != ExtraMetalStatus.pending_approval:
        raise HTTPException(status_code=400, detail="Bu talep onay bekleyen durumda değil.")

    old_status = req.status

    if payload.approved:
        req.status      = ExtraMetalStatus.approved
        req.approved_by = current_user.id
        req.approved_at = datetime.now(timezone.utc)
        audit_action = AuditAction.extra_metal_approved
    else:
        req.status = ExtraMetalStatus.rejected
        audit_action = AuditAction.order_rejected

    if payload.note:
        req.buyer_note = payload.note

    log_action(db, audit_action, request, current_user.id, req.order_id,
               old_value={"status": _serialize(old_status)},
               new_value={"status": _serialize(req.status), "buyer_note": req.buyer_note})

    db.commit()
    db.refresh(req)
    return PurchasePageItemOut.from_model(req)


# ─────────────────────────────────────────
# EKSTRA METAL — TOPLU (BATCH) İŞLEM
# ─────────────────────────────────────────

@router.post("/extra-metal/batch")
def batch_extra_metal(
    payload: BatchExtraMetalAction,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_buyer),
):
    """
    Birden fazla ekstra metal talebini toplu onaylar/satın alır ve ortak not ekler.
      action_type=approved  → pending_approval olanları onaylar
      action_type=purchased → approved olanları satın alındı işaretler (arşivler)
    """
    if payload.action_type not in (ExtraMetalStatus.approved, ExtraMetalStatus.purchased):
        raise HTTPException(status_code=400, detail="action_type yalnızca 'approved' veya 'purchased' olabilir.")

    reqs = db.query(ExtraMetalRequest).filter(
        ExtraMetalRequest.id.in_(payload.request_ids)
    ).all()
    if not reqs:
        raise HTTPException(status_code=404, detail="Belirtilen taleplerden hiçbiri bulunamadı.")

    updated = 0
    for req in reqs:
        if payload.action_type == ExtraMetalStatus.approved:
            if req.status != ExtraMetalStatus.pending_approval:
                continue
            req.status      = ExtraMetalStatus.approved
            req.approved_by = current_user.id
            req.approved_at = datetime.now(timezone.utc)
            audit_action = AuditAction.extra_metal_approved
        else:  # purchased
            if req.status != ExtraMetalStatus.approved:
                continue
            req.status = ExtraMetalStatus.purchased
            audit_action = AuditAction.extra_metal_approved

        if payload.buyer_note:
            req.buyer_note = payload.buyer_note

        log_action(db, audit_action, request, current_user.id, req.order_id,
                   new_value={"status": _serialize(req.status), "batch": True})
        updated += 1

    db.commit()
    return {"requested": len(payload.request_ids), "updated": updated,
            "action": payload.action_type.value}
