"""
Orders Endpoints (v3)

Değişiklik özeti:
  - KALDIRILDI: approve-order (manager onayı) — onay artık buyer'da (buyer.py)
  - KALDIRILDI: resolve-mismatch (edit_granted/mismatch_review akışı yok)
  - KALDIRILDI: list-editable-orders (edit_granted listesi yok)
  - import'lardan MismatchResolve, OrderApprove çıkarıldı
  - update-order yetkisi: manager (üretim alanlarını accountant production.py'den günceller)
  - list-orders, get-order, delete-order korundu
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.models.models import (
    Order, OrderStatus, OrderStatusHistory, User, AuditAction,
)
from app.schemas.schemas import (
    OrderUpdate, OrderStatusOut, PaginatedOrders,
)
from app.core.security import get_current_user, require_manager
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
# SİPARİŞ DETAYI
# ─────────────────────────────────────────

@router.get("/{order_id}/get-order", response_model=OrderStatusOut)
def get_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    order = (
        db.query(Order)
        .filter(Order.status != OrderStatus.deleted, Order.id == order_id)
        .first()
    )
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı.")
    return order


# ─────────────────────────────────────────
# SİPARİŞ GÜNCELLE (Müdür — genel alanlar)
# ─────────────────────────────────────────

@router.patch("/{order_id}/update-order", response_model=OrderStatusOut)
def update_order(
    order_id: int,
    payload: OrderUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_manager),
):
    """
    Müdür sipariş üst bilgilerini günceller (başlık, müşteri, tutar, total_count, not).
    NOT: Üretim olayları (ready_count, cutting vb.) accountant tarafından
         production.py üzerinden yönetilir.
    """
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
# SİPARİŞ SİL (soft delete — Müdür)
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
               old_value={"order_number": order.order_number, "status": _serialize(old_status)},
               new_value={"status": _serialize(OrderStatus.deleted)})

    db.commit()


# ─────────────────────────────────────────
# KALDIRILAN ENDPOINT'LER
# ─────────────────────────────────────────
# @router.get("/list-editable-orders", ...)        → SİLİNDİ (edit_granted yok)
# @router.post("/{order_id}/approve-order", ...)    → SİLİNDİ (onay buyer'da: buyer.py)
# @router.post("/{order_id}/resolve-mismatch", ...) → SİLİNDİ (uyuşmazlık frontend'de)
