"""
Staff Endpoints (v3)

Staff iş akışı (sipariş oluşturma order_ocr_helper.py'de):
  - Aktif bir sipariş için EKSTRA metal talebi açar (tekil veya batch)
    → buyer'a bildirim gider
  - Buyer düzenleme istediyse (orders.buyer_note) pending_approval siparişin
    içeriğini günceller (metal kalemleri replace-all) → buyer tekrar inceler

Yetki: require_staff (staff + manager).
"""
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.security import require_staff
from app.db.base import get_db
from app.models.models import (
    AuditAction, ExtraMetalRequest, ExtraMetalStatus, MetalRequest, NotifType,
    Order, OrderStatus, ProductionEvent, ProductionEventType, User, UserRole,
)
from app.schemas.schemas import (
    ExtraMetalBatchCreate, ExtraMetalRequestOut,
    OrderContentUpdate, OrderStatusOut,
)
from app.schemas.metal_schemas import describe_manual_totals
from app.services.audit import log_action, _serialize
from app.services.notification_service import (
    notify_actor_and_managers, notify_extra_metal_requested, notify_role,
)

router = APIRouter(prefix="/staff", tags=["staff"])

# ─────────────────────────────────────────
# EKSTRA METAL TALEBİ AÇ  (staff → buyer) — tekil VEYA çoklu, TEK endpoint
# ─────────────────────────────────────────

@router.post("/{order_id}/request-extra-metal",
             response_model=List[ExtraMetalRequestOut], status_code=201)
def request_extra_metal(
    order_id: int,
    payload: ExtraMetalBatchCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    """
    Staff, aktif siparişe bir veya birden fazla ekstra metal talebi açar.
    (Tekil/çoklu için AYRI endpoint yoktur; items 1 eleman da olabilir.)
    Her kalem ayrı ExtraMetalRequest satırı olur (buyer tekil/toplu karar verebilir);
    talepler pending_approval ile oluşur, bildirimler tek mesajda özetlenir.
    """
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı.")
    # on_hold = kesim durduruldu — tam da ekstra metal gerekebilecek durum,
    # bu yüzden aktif İLE BİRLİKTE kabul edilir
    if order.status not in (OrderStatus.active, OrderStatus.on_hold):
        raise HTTPException(
            status_code=400,
            detail="Ekstra metal talebi yalnızca aktif veya durdurulmuş (satın alınmış) siparişlere açılabilir.",
        )

    # Kesim tamamlandıysa üretim döngüsü kapalı — yeni metal gelişi kaydedilemeyeceği
    # için ekstra metal talebi de açılamaz (tutarlılık kuralı).
    cutting_done_exists = db.query(ProductionEvent.id).filter(
        ProductionEvent.order_id == order.id,
        ProductionEvent.event_type == ProductionEventType.cutting_done,
    ).first()
    if cutting_done_exists:
        raise HTTPException(
            status_code=400,
            detail="Kesim tamamlanmış siparişe ekstra metal talebi açılamaz.",
        )

    created: list[ExtraMetalRequest] = []
    for item in payload.items:
        extra = ExtraMetalRequest(
            order_id=order.id,
            width=item.width,
            length=item.length,
            thickness=item.thickness,
            material=item.material,
            quantity=item.quantity,
            kg=item.kg,
            total=item.total,                       # schema validator'ı otomatik hesaplar
            estimated_amount=item.estimated_amount, # elle girilen fiyat
            reason=item.reason,
            status=ExtraMetalStatus.pending_approval,
            created_by=current_user.id,
        )
        db.add(extra)
        created.append(extra)

    db.flush()  # id'ler üretilsin

    materials = ", ".join(f"{i.material}×{i.quantity}" for i in payload.items)

    # Elle değiştirilen otomatik ağırlıklar → audit + tüm bildirimlere eklenir
    manual_totals = describe_manual_totals(payload.items)
    override_note = (
        f" DİKKAT: {len(manual_totals)} kalemde otomatik ağırlık elle değiştirildi — "
        + "; ".join(manual_totals)
    ) if manual_totals else ""

    log_action(db, AuditAction.extra_metal_requested, request, current_user.id, order.id,
               new_value={
                   "batch": True,
                   "count": len(created),
                   "request_ids": [e.id for e in created],
                   "materials": materials,
                   "manual_totals": manual_totals or None,
               })

    notify_extra_metal_requested(
        db, order.id,
        message=f'"{order.order_title}" ({order.order_number}) için {len(created)} kalem ekstra metal talebi açıldı: {materials}.{override_note}',
    )
    notify_actor_and_managers(
        db,
        actor_id=current_user.id,
        notif_type=NotifType.extra_metal_requested,
        actor_message=f'"{order.order_title}" ({order.order_number}) için {len(created)} kalem ekstra metal talebiniz açıldı, onay bekleniyor.{override_note}',
        manager_message=f'{current_user.username}, "{order.order_title}" ({order.order_number}) için {len(created)} kalem ekstra metal talebi açtı: {materials}.{override_note}',
        order_id=order.id,
    )

    db.commit()
    for e in created:
        db.refresh(e)
    return created


# ─────────────────────────────────────────
# SİPARİŞ İÇERİĞİNİ DÜZENLE  (buyer'ın revizyon notu üzerine)
# ─────────────────────────────────────────

@router.patch("/{order_id}/update-order-content", response_model=OrderStatusOut)
def update_order_content(
    order_id: int,
    payload: OrderContentUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    """
    Staff, pending_approval (henüz satın alınmamış) siparişin içeriğini düzenler.
    Tipik akış: buyer request-changes ile not düşer → staff burayı çağırır.

    metal_items gönderilirse liste KOMPLE değiştirilir (replace-all):
    frontend, metal tablosunun son hâlini (eski + yeni satırlar) gönderir.
    total_count otomatik yeniden hesaplanır, buyer_note temizlenir,
    buyer'a "tekrar inceleyin" bildirimi gider.

    Yalnızca siparişi oluşturan staff (veya manager) düzenleyebilir.
    """
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı.")
    if order.status != OrderStatus.pending_approval:
        raise HTTPException(
            status_code=400,
            detail="Yalnızca satın alma onayı bekleyen siparişler düzenlenebilir.",
        )
    if current_user.role != UserRole.manager and order.created_by != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="Bu siparişi yalnızca oluşturan personel veya müdür düzenleyebilir.",
        )

    changed: dict = {}
    old_values: dict = {}

    for field in ("order_title", "customer_name", "customer_phone",
                  "customer_address", "estimated_amount"):
        value = getattr(payload, field)
        if value is not None:
            old_values[field] = _serialize(getattr(order, field))
            setattr(order, field, value)
            changed[field] = _serialize(value)

    if payload.metal_items is not None:
        if not payload.metal_items:
            raise HTTPException(status_code=400, detail="En az bir metal kalemi girilmeli.")
        old_values["metal_items"] = len(order.metal_requests)
        # replace-all: delete-orphan cascade eski kalemleri siler
        order.metal_requests = [
            MetalRequest(
                width=item.width,
                length=item.length,
                thickness=item.thickness,
                material=item.material,
                quantity=item.quantity,
                kg=item.kg,
                total=item.total,
                notes=item.notes,
                created_by=current_user.id,
            )
            for item in payload.metal_items
        ]
        # NOT: total_count artık ÜRETİM HEDEFİ — metal kalemleriyle ilgisi yok,
        #      burada yeniden hesaplanmaz (accountant/staff ayrıca yönetir).
        changed["metal_items"] = len(payload.metal_items)

    if not changed:
        raise HTTPException(status_code=400, detail="Güncellenecek alan belirtilmedi.")

    resolved_note = order.buyer_note
    order.buyer_note = None          # düzenleme yapıldı → buyer notu kapandı
    order.updated_at = datetime.now(timezone.utc)

    # Elle değiştirilen otomatik ağırlıklar → audit + tüm bildirimlere eklenir
    manual_totals = describe_manual_totals(payload.metal_items or [])
    override_note = (
        f" DİKKAT: {len(manual_totals)} kalemde otomatik ağırlık elle değiştirildi — "
        + "; ".join(manual_totals)
    ) if manual_totals else ""

    log_action(db, AuditAction.order_updated, request, current_user.id, order.id,
               old_value=old_values,
               new_value={**changed, "resolved_buyer_note": resolved_note,
                          "manual_totals": manual_totals or None})

    fields_label = ", ".join(changed.keys())
    notify_actor_and_managers(
        db,
        actor_id=current_user.id,
        notif_type=NotifType.order_updated,
        actor_message=f'"{order.order_title}" ({order.order_number}) siparişini düzenlediniz. Değişen: {fields_label}.{override_note}',
        manager_message=f'{current_user.username}, "{order.order_title}" ({order.order_number}) siparişini düzenledi. Değişen: {fields_label}.{override_note}',
        order_id=order.id,
    )
    # Buyer(lar) yeniden incelemeli
    notify_role(
        db,
        role=UserRole.buyer,
        notif_type=NotifType.order_updated,
        message=f'"{order.order_title}" ({order.order_number}) düzenlendi, lütfen tekrar inceleyin. Değişen: {fields_label}.{override_note}',
        order_id=order.id,
    )

    db.commit()
    db.refresh(order)
    return order
