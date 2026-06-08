"""
Orders Endpoints
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.models.models import (
    Order, OrderStatus, OrderStatusHistory,
    Notification, NotifType, User, AuditAction,
)
from app.schemas.schemas import (
    MismatchResolve,
    OrderUpdate, OrderApprove,
    OrderStatusOut, PaginatedOrders
)
from app.core.security import get_current_user, require_manager, require_accounting
from app.services.audit import log_action, _serialize

router = APIRouter(prefix="/orders", tags=["orders"])

# ─────────────────────────────────────────
# SİPARİŞ LİSTESİ
# ─────────────────────────────────────────

@router.get("/list-orders", response_model=PaginatedOrders)
def list_orders(
    status: Optional[OrderStatus] = Query(None),
    search: Optional[str] = Query(None, description="Sipariş adı veya müşteri adı arama"),
    sort_by: Optional[str] = Query("created_at"),
    sort_dir: Optional[str] = Query("desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(Order).filter(Order.status != OrderStatus.deleted)

    if status:
        query = query.filter(Order.status == status)

    if search:
        query = query.filter(
            Order.order_title.ilike(f"%{search}%") |
            Order.customer_name.ilike(f"%{search}%")
        )

    sort_col = {
        "created_at":       Order.created_at,
        "order_title":      Order.order_title,
        "customer_name":    Order.customer_name,
        "estimated_amount": Order.estimated_amount,
    }.get(sort_by, Order.created_at)

    query = query.order_by(sort_col.desc() if sort_dir == "desc" else sort_col.asc())

    total = query.count()
    orders = query.offset((page - 1) * page_size).limit(page_size).all()

    return PaginatedOrders(total=total, page=page, page_size=page_size, items=orders)


# ─────────────────────────────────────────
# DÜZENLENEBİLİR SİPARİŞLER (Muhasebe — edit_granted listesi)
# ─────────────────────────────────────────

@router.get("/list-editable-orders", response_model=PaginatedOrders)
def list_editable_orders(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_accounting),
):
    """
    Sadece muhasebenin görmesi gereken, müdür tarafından düzenleme izni verilmiş
    siparişleri döner. Frontend'de ayrı bir "Düzenlenecek Siparişler" sayfası için.
    """
    query = (
        db.query(Order)
        .filter(Order.status == OrderStatus.edit_granted)
        .order_by(Order.updated_at.desc())
    )
    total = query.count()
    orders = query.offset((page - 1) * page_size).limit(page_size).all()
    return PaginatedOrders(total=total, page=page, page_size=page_size, items=orders)


# ─────────────────────────────────────────
# SİPARİŞ DETAYI
# ─────────────────────────────────────────

@router.get("/{order_id}/get-order", response_model=OrderStatusOut)
def get_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    order = db.query(Order).filter(Order.status != OrderStatus.deleted).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı.")
    return order


# ─────────────────────────────────────────
# SİPARİŞ GÜNCELLE (Müdür — üretim adımları)
# ─────────────────────────────────────────

@router.patch("/{order_id}/update-order", response_model=OrderStatusOut)
def update_order(
    order_id: int,
    payload: OrderUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_manager),
):
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı.")

    changed_fields = payload.model_dump(exclude_none=True)
    if not changed_fields:
        raise HTTPException(status_code=400, detail="Güncellenecek alan belirtilmedi.")

    old_values = {k: _serialize(getattr(order, k)) for k in changed_fields}
    for field, value in changed_fields.items():
        setattr(order, field, value)

    log_action(db, AuditAction.order_updated, request, current_user.id, order.id,
               old_value=old_values,
               new_value=_serialize(changed_fields))
    db.commit()
    db.refresh(order)
    return order


# ─────────────────────────────────────────
# SİPARİŞ ONAYLA / REDDET (Müdür)
# ─────────────────────────────────────────

@router.post("/{order_id}/approve-order", response_model=OrderStatusOut)
def approve_order(
    order_id: int,
    payload: OrderApprove,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_manager),
):
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı.")
    if order.status != OrderStatus.pending_approval:
        raise HTTPException(status_code=400, detail="Sipariş onay bekliyor durumunda değil.")

    old_status = order.status

    if payload.approved:
        order.status      = OrderStatus.active
        order.approved_by = current_user.id
        order.approved_at = datetime.now(timezone.utc)
        notif_type   = NotifType.approved
        notif_msg    = f'"{order.order_title}" ({order.order_number}) onaylandı.'
        audit_action = AuditAction.order_approved
    else:
        if not payload.rejection_reason:
            raise HTTPException(status_code=400, detail="Red sebebi girilmeli.")
        order.status           = OrderStatus.cancelled
        order.rejection_reason = payload.rejection_reason
        notif_type   = NotifType.rejected
        notif_msg    = f'"{order.order_title}" ({order.order_number}) reddedildi: {payload.rejection_reason}'
        audit_action = AuditAction.order_rejected

    db.add(OrderStatusHistory(
        order_id=order.id, old_status=old_status,
        new_status=order.status, changed_by=current_user.id,
    ))
    db.add(Notification(
        recipient_id=order.created_by, order_id=order.id,
        type=notif_type, message=notif_msg,
    ))
    log_action(db, audit_action, request, current_user.id, order.id,
               old_value={"status": old_status},
               new_value={"status": order.status})

    db.commit()
    db.refresh(order)
    return order


# ─────────────────────────────────────────
# TUTAR UYUŞMAZLIĞINI ÇÖZ (Müdür)
# ─────────────────────────────────────────

@router.post("/{order_id}/resolve-mismatch", response_model=OrderStatusOut)
def resolve_mismatch(
    order_id: int,
    payload: MismatchResolve,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_manager),
):
    """
    mismatch_review durumundaki siparişi müdür üç şekilde çözebilir:
      - approve=True              → farkı kabul et, siparişi tamamla (completed)
      - approve=False             → siparişi iptal et (cancelled)
      - approve=False + grant_edit=True → muhasebeye düzenleme izni ver (edit_granted)

    grant_edit=True seçilirse sipariş edit_granted durumuna geçer,
    muhasebe faturayı yeniden yükleyip düzenledikten sonra
    sipariş pending_approval'a döner ve müdür tekrar onaylar.
    """
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order or order.status != OrderStatus.mismatch_review:
        raise HTTPException(status_code=400, detail="Bu sipariş tutar inceleme durumunda değil.")
    if not payload.manager_note.strip():
        raise HTTPException(status_code=400, detail="İşlemi sonuçlandırmak için açıklama girmelisiniz.")

    old_status = order.status

    if payload.approve:
        # Fiyat farkını kabul et → tamamla
        order.status       = OrderStatus.completed
        order.completed_at = datetime.now(timezone.utc)
        action_note  = f"Müdür uyuşmazlığı onayladı, sipariş tamamlandı. Not: {payload.manager_note}"
        audit_action = AuditAction.order_completed
        notif_type   = NotifType.approved
        notif_msg    = f'"{order.order_title}" tutar uyuşmazlığı onaylandı, sipariş tamamlandı.'

    elif payload.grant_edit:
        # Muhasebeye düzenleme izni ver → edit_granted
        order.status = OrderStatus.edit_granted
        action_note  = f"Müdür düzenleme izni verdi. Not: {payload.manager_note}"
        audit_action = AuditAction.status_changed
        notif_type   = NotifType.edit_requested
        notif_msg    = (
            f'"{order.order_title}" ({order.order_number}) siparişi için '
            f"müdür fatura düzenleme izni verdi. Lütfen faturayı yeniden yükleyin."
        )
    else:
        # Reddet → iptal et
        order.status           = OrderStatus.cancelled
        order.rejection_reason = payload.manager_note
        action_note  = f"Müdür uyuşmazlığı reddetti, sipariş iptal edildi. Sebep: {payload.manager_note}"
        audit_action = AuditAction.order_rejected
        notif_type   = NotifType.rejected
        notif_msg    = f'"{order.order_title}" tutar uyuşmazlığı reddedildi, sipariş iptal edildi.'

    db.add(OrderStatusHistory(
        order_id=order.id, old_status=old_status,
        new_status=order.status, changed_by=current_user.id,
        note=action_note,
    ))
    db.add(Notification(
        recipient_id=order.created_by, order_id=order.id,
        type=notif_type, message=notif_msg,
    ))
    log_action(db, audit_action, request, current_user.id, order.id,
               old_value={"status": old_status},
               new_value={"status": order.status, "manager_note": payload.manager_note})

    db.commit()
    db.refresh(order)
    return order


# ─────────────────────────────────────────
# SİPARİŞ SİL (soft delete)
# ─────────────────────────────────────────

@router.delete("/{order_id}/delete-order", status_code=204)
def delete_order(
    order_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_manager),
):
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı.")

    old_status       = order.status
    order.status     = OrderStatus.deleted
    order.updated_at = datetime.now(timezone.utc)

    db.add(OrderStatusHistory(
        order_id=order.id, old_status=old_status,
        new_status=OrderStatus.deleted, changed_by=current_user.id,
        note="Sipariş müdür tarafından silindi (soft-delete).",
    ))
    log_action(db, AuditAction.order_deleted, request, current_user.id, order.id,
               old_value={"order_number": order.order_number, "status": old_status},
               new_value={"status": OrderStatus.deleted})

    db.commit()