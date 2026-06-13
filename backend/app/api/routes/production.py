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
    AuditAction, ExtraMetalRequest, ExtraMetalStatus, MetalRequest, NotifType,
    Order, OrderStatus, OrderStatusHistory, ProductionEvent, ProductionEventType, User,
)
from app.schemas.schemas import ProductionEventCreate, ProductionEventOut
from app.services.audit import log_action
from app.services.notification_service import notify_production_updated, notify_user

router = APIRouter(prefix="/production", tags=["production"])

# Bildirim mesajlarında ham enum değeri yerine kullanıcıların anlayacağı Türkçe etiketler
EVENT_LABELS_TR: dict[ProductionEventType, str] = {
    ProductionEventType.metal_arrived:         "Metal geldi",
    ProductionEventType.cutting_started:       "Kesim başladı",
    ProductionEventType.cutting_stopped:       "Kesim durduruldu",
    ProductionEventType.cutting_started_again: "Kesim yeniden başladı",
    ProductionEventType.cutting_done:          "Kesim tamamlandı (üretim döngüsü kapandı)",
    ProductionEventType.ready_count_updated:   "Üretilen ürün adedi güncellendi",
    ProductionEventType.target_count_updated:  "Üretim hedefi güncellendi",
}


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
    # on_hold = kesim durduruldu — yeniden başlatma/metal gelişi vb. olaylar
    # eklenebilsin diye aktif İLE BİRLİKTE kabul edilir
    if order.status not in (OrderStatus.active, OrderStatus.on_hold):
        raise HTTPException(
            status_code=400,
            detail="Üretim olayları yalnızca aktif veya durdurulmuş siparişlere eklenebilir.",
        )
    # NOT: order_completed artık ProductionEventType'ta yok — sipariş tamamlama
    #      yalnızca final fatura akışıyla (submit-final-invoice) yapılır.

    # ── DURUM MAKİNESİ — mevcut olaylardan üretim durumunu çıkar ──────────
    existing = (
        db.query(ProductionEvent)
        .filter(ProductionEvent.order_id == order.id)
        .order_by(ProductionEvent.created_at.asc(), ProductionEvent.id.asc())
        .all()
    )

    # Kesim tamamlandıysa üretim döngüsü KAPALI — hiçbir olay eklenemez
    if any(e.event_type == ProductionEventType.cutting_done for e in existing):
        raise HTTPException(
            status_code=400,
            detail="Üretim döngüsü tamamlandı (kesim bitti) — bu siparişe yeni üretim olayı eklenemez.",
        )

    cutting_events = [e for e in existing if e.event_type in (
        ProductionEventType.cutting_started,
        ProductionEventType.cutting_stopped,
        ProductionEventType.cutting_started_again,
    )]
    cutting_started_once = any(
        e.event_type == ProductionEventType.cutting_started for e in cutting_events
    )
    cutting_running = bool(cutting_events) and cutting_events[-1].event_type in (
        ProductionEventType.cutting_started,
        ProductionEventType.cutting_started_again,
    )
    arrived_metal_ids = {e.metal_request_id for e in existing if e.metal_request_id}
    arrived_extra_ids = {e.extra_metal_request_id for e in existing if e.extra_metal_request_id}
    any_metal_arrived = bool(arrived_metal_ids or arrived_extra_ids)

    metal_label = None   # bildirim mesajı için "GLV 1500×3000×3 (2 adet)" gibi

    if payload.event_type == ProductionEventType.metal_arrived:
        if payload.metal_request_id is not None:
            metal = db.query(MetalRequest).filter(
                MetalRequest.id == payload.metal_request_id,
                MetalRequest.order_id == order.id,
            ).first()
            if not metal:
                raise HTTPException(status_code=404, detail="Metal kalemi bu siparişte bulunamadı.")
            if payload.metal_request_id in arrived_metal_ids:
                raise HTTPException(status_code=400, detail="Bu metal kalemi zaten 'geldi' olarak işaretlenmiş.")
            metal_label = f"{metal.material} {metal.width}×{metal.length}×{metal.thickness} ({metal.quantity} adet)"
        else:
            extra = db.query(ExtraMetalRequest).filter(
                ExtraMetalRequest.id == payload.extra_metal_request_id,
                ExtraMetalRequest.order_id == order.id,
            ).first()
            if not extra:
                raise HTTPException(status_code=404, detail="Ekstra metal talebi bu siparişte bulunamadı.")
            if extra.status not in (ExtraMetalStatus.approved, ExtraMetalStatus.purchased):
                raise HTTPException(
                    status_code=400,
                    detail="Yalnızca onaylanmış/satın alınmış ekstra metaller 'geldi' olarak işaretlenebilir.",
                )
            if payload.extra_metal_request_id in arrived_extra_ids:
                raise HTTPException(status_code=400, detail="Bu ekstra metal zaten 'geldi' olarak işaretlenmiş.")
            metal_label = (
                f"{extra.material} {extra.width}×{extra.length}×{extra.thickness} "
                f"({extra.quantity} adet, EKSTRA)"
            )

    elif payload.event_type == ProductionEventType.cutting_started:
        if cutting_started_once:
            raise HTTPException(
                status_code=400,
                detail="Kesim zaten başlatılmış. Durdurma/yeniden başlatma için ilgili olayları kullanın.",
            )
        if not any_metal_arrived:
            raise HTTPException(
                status_code=400,
                detail="En az bir metal gelmeden kesim başlatılamaz (önce 'Metal Geldi' kaydedin).",
            )

    elif payload.event_type == ProductionEventType.cutting_stopped:
        if not cutting_running:
            raise HTTPException(status_code=400, detail="Kesim şu an çalışmıyor — durdurulamaz.")

    elif payload.event_type == ProductionEventType.cutting_started_again:
        if not cutting_started_once or cutting_running:
            raise HTTPException(
                status_code=400,
                detail="Yeniden başlatma yalnızca durdurulmuş kesim için kullanılabilir.",
            )

    elif payload.event_type == ProductionEventType.cutting_done:
        if not cutting_started_once:
            raise HTTPException(status_code=400, detail="Kesim hiç başlatılmadı — tamamlanamaz.")

    event = ProductionEvent(
        order_id=order.id,
        event_type=payload.event_type,
        note=payload.note,
        ready_count=payload.ready_count,
        target_count=payload.target_count,
        metal_request_id=payload.metal_request_id,
        extra_metal_request_id=payload.extra_metal_request_id,
        created_by=current_user.id,
    )
    db.add(event)

    # Sayısal alanları orders'a senkronla
    audit_action = AuditAction.production_step_updated
    if payload.event_type == ProductionEventType.ready_count_updated:
        order.ready_count = payload.ready_count
        order.updated_at  = datetime.now(timezone.utc)
    elif payload.event_type == ProductionEventType.target_count_updated:
        # Üretim hedefi (ürün adedi) — metal sac adetleriyle ilgisizdir
        order.total_count = payload.target_count
        order.updated_at  = datetime.now(timezone.utc)
    elif payload.note:
        # cutting_stopped vb. notlu olay
        audit_action = AuditAction.production_note_added

    # ── SİPARİŞ DURUMU GEÇİŞLERİ ────────────────────────────────────────
    # Kesim durunca sipariş listede "Durduruldu" (on_hold) görünür;
    # yeniden başlayınca / kesim tamamlanınca "Aktif"e döner.
    def _set_status(new_status: OrderStatus, history_note: str) -> None:
        old = order.status
        if old == new_status:
            return
        order.status     = new_status
        order.updated_at = datetime.now(timezone.utc)
        db.add(OrderStatusHistory(
            order_id=order.id, old_status=old, new_status=new_status,
            changed_by=current_user.id, note=history_note,
        ))

    if payload.event_type == ProductionEventType.cutting_stopped:
        _set_status(OrderStatus.on_hold,
                    f"Kesim durduruldu — {payload.note}" if payload.note else "Kesim durduruldu.")
    elif payload.event_type == ProductionEventType.cutting_started_again:
        _set_status(OrderStatus.active, "Kesim yeniden başladı.")
    elif payload.event_type == ProductionEventType.cutting_done:
        # Durdurulmuş halde tamamlanırsa da aktife normalize edilir
        # (final fatura akışı 'active' bekler)
        _set_status(OrderStatus.active, "Kesim tamamlandı — üretim döngüsü kapandı.")

    db.flush()  # event.id

    log_action(db, audit_action, request, current_user.id, order.id,
               new_value={
                   "event_type": payload.event_type.value,
                   "note": payload.note,
                   "ready_count": payload.ready_count,
                   "target_count": payload.target_count,
                   "metal_request_id": payload.metal_request_id,
                   "extra_metal_request_id": payload.extra_metal_request_id,
               })

    # Olay açıklaması — varsa üretilen adet/hedef/metal ve girilen not da mesaja eklenir
    detail = EVENT_LABELS_TR.get(payload.event_type, payload.event_type.value)
    if payload.event_type == ProductionEventType.ready_count_updated and payload.ready_count is not None:
        detail += f" (üretilen: {payload.ready_count})"
    if payload.event_type == ProductionEventType.target_count_updated and payload.target_count is not None:
        detail += f" (yeni hedef: {payload.target_count})"
    if metal_label:
        detail += f" — {metal_label}"
    if payload.note:
        detail += f" — Not: {payload.note}"

    notify_production_updated(
        db, order.id,
        message=f'"{order.order_title}" üretim güncellemesi: {detail}',
    )
    # Aktöre (accountant) onay bildirimi — müdür zaten yukarıda bilgilendirildi.
    notify_user(
        db,
        recipient_id=current_user.id,
        notif_type=NotifType.production_updated,
        message=f'"{order.order_title}" için üretim olayı kaydettiniz: {detail}',
        order_id=order.id,
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
