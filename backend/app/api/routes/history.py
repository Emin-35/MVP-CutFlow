from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.security import get_current_user, require_manager
from app.db.base import get_db
from app.models.models import AuditAction, AuditLog, Order, OrderStatus, OrderStatusHistory, User
from app.schemas.schemas import PaginatedOrders, StatusHistoryOut


router = APIRouter(prefix="/history", tags=["history"])

# ─────────────────────────────────────────
# GLOBAL SİPARİŞ GEÇMİŞİ
# ─────────────────────────────────────────

@router.get("/global-order-history")
def get_global_order_history(
    action_filter: Optional[AuditAction] = Query(None),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_manager),
):
    query = db.query(AuditLog).filter(
        AuditLog.action.in_(AuditAction.order_actions())
    )
    if action_filter:
        if action_filter not in AuditAction.order_actions():
            raise HTTPException(400, "Bu filtre bir sipariş aksiyonu değil.")
        query = query.filter(AuditLog.action == action_filter)

    return query.order_by(AuditLog.created_at.desc()).limit(limit).all()


# ─────────────────────────────────────────
# SİLİNMİŞ SİPARİŞLER
# ─────────────────────────────────────────

@router.get("/get-deleted-orders", response_model=PaginatedOrders)
def get_deleted_orders(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_manager),
):
    query = db.query(Order).filter(Order.status == OrderStatus.deleted)
    total = query.count()
    orders = query.offset((page - 1) * page_size).limit(page_size).all()
    return PaginatedOrders(total=total, page=page, page_size=page_size, items=orders)


# ─────────────────────────────────────────
# SİPARİŞ DURUM GEÇMİŞİ
# ─────────────────────────────────────────

@router.get("/{order_id}/specific-order-history", response_model=List[StatusHistoryOut])
def specific_order_history(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not db.query(Order).filter(Order.id == order_id).first():
        raise HTTPException(404, "Bu ID'ye sahip bir sipariş bulunamadı.")
    
    return (
        db.query(OrderStatusHistory)
        .filter(OrderStatusHistory.order_id == order_id)
        .order_by(OrderStatusHistory.created_at)
        .all()
    )


# ─────────────────────────────────────────
# BELİRLİ KULLANICI GEÇMİŞİ
# ─────────────────────────────────────────

@router.get("/{user_id}/specific-user-history")
def specific_user_history(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_manager),
):
    if not db.query(User).filter(User.id == user_id).first():
        raise HTTPException(404, "Bu ID'ye sahip bir kullanıcı bulunamadı.")
    
    return db.query(AuditLog).filter(
        AuditLog.user_id == user_id
    ).order_by(AuditLog.created_at.desc()).all()
