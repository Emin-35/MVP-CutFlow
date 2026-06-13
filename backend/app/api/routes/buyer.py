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
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.security import require_buyer
from app.db.base import get_db
from app.models.models import (
    AuditAction, ExtraMetalRequest, ExtraMetalStatus, Notification, NotifType,
    Order, OrderStatus, OrderStatusHistory, User,
)
from app.models.file_models import FileAsset
from app.services.storage_backend import get_storage
from app.schemas.schemas import (
    ExtraMetalDecision, OrderBuy, OrderRevisionRequest,
    OrderStatusOut, PurchasePageItemOut,
)
from app.services.audit import log_action, _serialize
from app.services.notification_service import (
    notify_actor_and_managers, notify_all_active_users, notify_user,
)

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
    Buyer siparişi satın alır → active, veya reddeder → HARD DELETE.

    RED = KALICI SİLME: Satın alınmamış sipariş reddedilince DB'de boşta
    kalmaz; sipariş ve tüm bağlı kayıtlar (fatura, dosyalar, metal kalemleri,
    durum geçmişi) kalıcı olarak silinir, fiziksel dosyalar da temizlenir.
    İz audit log'da kalır (order_number new_value içinde, order_id=NULL).

    NOT: Reddetmek yerine düzeltme istemek için request-changes kullanılır.
    """
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı.")
    if order.status != OrderStatus.pending_approval:
        raise HTTPException(status_code=400, detail="Sipariş satın alınmayı bekleyen durumda değil.")

    old_status = order.status

    # ── RED → HARD DELETE ─────────────────────────────────────────
    if not payload.approved:
        if not payload.rejection_reason:
            raise HTTPException(status_code=400, detail="Red sebebi girilmeli.")

        order_number = order.order_number
        order_title  = order.order_title
        creator_id   = order.created_by
        reason       = payload.rejection_reason
        file_paths   = {f.file_path for f in order.order_files}
        file_paths  |= {inv.file_path for inv in order.invoices if inv.file_path}

        # Siparişe bağlı eski bildirimleri ve dosya metadata kayıtlarını sil
        db.query(Notification).filter(Notification.order_id == order.id).delete(
            synchronize_session=False
        )
        db.query(FileAsset).filter(FileAsset.order_id == order.id).delete(
            synchronize_session=False
        )

        # Audit izi: sipariş silineceği için order_id=None; kimlik new/old_value'da
        log_action(db, AuditAction.order_rejected, request, current_user.id, None,
                   old_value={"order_number": order_number, "order_title": order_title,
                              "status": _serialize(old_status)},
                   new_value={"hard_deleted": True, "reason": reason})

        notify_actor_and_managers(
            db,
            actor_id=current_user.id,
            notif_type=NotifType.rejected,
            actor_message=f'"{order_title}" ({order_number}) siparişini reddettiniz; sipariş kalıcı olarak silindi.',
            manager_message=f'{current_user.username}, "{order_title}" ({order_number}) siparişini reddetti (kalıcı silindi). Sebep: {reason}',
            order_id=None,   # sipariş siliniyor → FK bağlanamaz
        )
        if creator_id and creator_id != current_user.id:
            notify_user(
                db,
                recipient_id=creator_id,
                notif_type=NotifType.rejected,
                message=f'"{order_title}" ({order_number}) siparişiniz reddedildi ve silindi. Sebep: {reason}',
                order_id=None,
            )

        db.delete(order)   # cascade: invoices, order_files, metal_requests, status_history
        db.commit()

        # Fiziksel dosyalar commit SONRASI StorageBackend ile best-effort silinir
        # (yerel disk veya R2). DB hata verirse buraya gelinmez (veri kaybı olmaz);
        # dosya silinemezse retention/temizlik job'ına kalır (orphan tolere edilir).
        storage = get_storage()
        for key in file_paths:
            storage.delete(key)

        return JSONResponse({
            "deleted": True,
            "order_number": order_number,
            "message": f'"{order_title}" ({order_number}) reddedildi ve kalıcı olarak silindi.',
        })

    # ── ONAY → ACTIVE ─────────────────────────────────────────────
    order.status     = OrderStatus.active
    order.bought_by  = current_user.id
    order.bought_at  = datetime.now(timezone.utc)
    order.updated_at = datetime.now(timezone.utc)
    order.buyer_note = None   # varsa eski düzenleme notu temizlenir

    db.add(OrderStatusHistory(
        order_id=order.id, old_status=old_status,
        new_status=order.status, changed_by=current_user.id,
        note="Buyer satın aldı, sipariş aktif.",
    ))
    log_action(db, AuditAction.order_buyed, request, current_user.id, order.id,
               old_value={"status": _serialize(old_status)},
               new_value={"status": _serialize(order.status)})

    # Sipariş artık aktif → herkes ilgilenebilir.
    # Aktöre (buyer) onay; siparişi oluşturan staff'a hedefli "onaylandı";
    # diğer TÜM aktif kullanıcılara genel bilgilendirme.
    exclude = {current_user.id}

    notify_user(
        db,
        recipient_id=current_user.id,
        notif_type=NotifType.order_buyed,
        message=f'"{order.order_title}" ({order.order_number}) siparişini satın aldınız, sipariş aktif (üretime hazır).',
        order_id=order.id,
    )
    # Siparişi oluşturan staff (varsa ve aktörün kendisi değilse) — hedefli bildirim
    if order.created_by and order.created_by != current_user.id:
        notify_user(
            db,
            recipient_id=order.created_by,
            notif_type=NotifType.order_buyed,
            message=f'Oluşturduğunuz "{order.order_title}" ({order.order_number}) siparişi onaylanıp satın alındı, sipariş aktif.',
            order_id=order.id,
        )
        exclude.add(order.created_by)

    notify_all_active_users(
        db,
        notif_type=NotifType.order_buyed,
        message=f'{current_user.username}, "{order.order_title}" ({order.order_number}) siparişini satın aldı. Sipariş aktif — ilgilenebilirsiniz.',
        order_id=order.id,
        exclude_ids=exclude,   # aktör + oluşturan staff yukarıda ayrı bilgilendirildi
    )

    db.commit()
    db.refresh(order)
    return order


# ─────────────────────────────────────────
# DÜZENLEME İSTE  (buyer → staff, red ETMEDEN)
# ─────────────────────────────────────────

@router.post("/{order_id}/request-changes", response_model=OrderStatusOut)
def request_order_changes(
    order_id: int,
    payload: OrderRevisionRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_buyer),
):
    """
    Buyer, satın almadan ÖNCE staff'tan düzenleme ister (reddetmek yerine).
    Örn: "ekstra metal eklenmeli" veya "yanlış şema yüklenmiş".

    Not zorunludur ve orders.buyer_note'a yazılır; sipariş pending_approval'da
    KALIR. Staff, update-order-content ile düzenleyince not temizlenir ve
    buyer'a tekrar inceleme bildirimi gider.
    """
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı.")
    if order.status != OrderStatus.pending_approval:
        raise HTTPException(
            status_code=400,
            detail="Yalnızca satın alma onayı bekleyen siparişler için düzenleme istenebilir.",
        )

    order.buyer_note = payload.note
    order.updated_at = datetime.now(timezone.utc)

    log_action(db, AuditAction.order_updated, request, current_user.id, order.id,
               new_value={"buyer_note": payload.note, "revision_requested": True})

    notify_actor_and_managers(
        db,
        actor_id=current_user.id,
        notif_type=NotifType.order_revision_requested,
        actor_message=f'"{order.order_title}" ({order.order_number}) için düzenleme talebiniz personele iletildi.',
        manager_message=f'{current_user.username}, "{order.order_title}" ({order.order_number}) için düzenleme istedi. Not: {payload.note}',
        order_id=order.id,
    )
    if order.created_by and order.created_by != current_user.id:
        notify_user(
            db,
            recipient_id=order.created_by,
            notif_type=NotifType.order_revision_requested,
            message=f'"{order.order_title}" ({order.order_number}) için satın alma düzenleme istedi: {payload.note}',
            order_id=order.id,
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
# EKSTRA METAL — KARAR (onay / red / satın alındı) — TEK ENDPOINT
# ─────────────────────────────────────────

@router.post("/extra-metal/decide")
def decide_extra_metal(
    payload: ExtraMetalDecision,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_buyer),
):
    """
    Bir veya birden fazla ekstra metal talebini tek seferde karara bağlar.
    (Tekil ve toplu için AYRI endpoint yoktur; request_ids 1 eleman da olabilir.)

      action_type=approved  → pending_approval → approved
      action_type=rejected  → pending_approval → rejected
      action_type=purchased → approved → purchased (arşiv)

    NOT: Siparişin estimated_amount/total alanı MUTASYONA UĞRATILMAZ. Onaylanan
    ekstra metallerin fiyatı (estimated_amount) ve ağırlığı (total), sipariş
    detayında computed alanlarla base+extra+final olarak gösterilir; final fatura
    karşılaştırması da base + onaylı/satın alınmış ekstralar üzerinden yapılır.

    Uygun olmayan durumdaki talepler sessizce atlanır (skipped sayılır).
    """
    if payload.action_type not in (
        ExtraMetalStatus.approved, ExtraMetalStatus.rejected, ExtraMetalStatus.purchased
    ):
        raise HTTPException(
            status_code=400,
            detail="action_type yalnızca 'approved', 'rejected' veya 'purchased' olabilir.",
        )

    reqs = db.query(ExtraMetalRequest).filter(
        ExtraMetalRequest.id.in_(payload.request_ids)
    ).all()
    if not reqs:
        raise HTTPException(status_code=404, detail="Belirtilen taleplerden hiçbiri bulunamadı.")

    updated = 0
    total_approved_price = Decimal("0")   # onaylanan ekstraların fiyat toplamı (bilgilendirme için)
    staff_orders: dict[int, set[int]] = {}   # talebi açan staff_id → etkilenen order_id'ler

    for req in reqs:
        if payload.action_type == ExtraMetalStatus.approved:
            if req.status != ExtraMetalStatus.pending_approval:
                continue
            req.status      = ExtraMetalStatus.approved
            req.approved_by = current_user.id
            req.approved_at = datetime.now(timezone.utc)
            if req.estimated_amount is not None:
                total_approved_price += req.estimated_amount

            log_action(db, AuditAction.extra_metal_approved, request, current_user.id, req.order_id,
                       old_value={"status": _serialize(ExtraMetalStatus.pending_approval)},
                       new_value={"status": _serialize(req.status),
                                  "price": str(req.estimated_amount)})

        elif payload.action_type == ExtraMetalStatus.rejected:
            if req.status != ExtraMetalStatus.pending_approval:
                continue
            req.status = ExtraMetalStatus.rejected
            log_action(db, AuditAction.order_rejected, request, current_user.id, req.order_id,
                       old_value={"status": _serialize(ExtraMetalStatus.pending_approval)},
                       new_value={"status": _serialize(req.status)})

        else:  # purchased
            if req.status != ExtraMetalStatus.approved:
                continue
            req.status = ExtraMetalStatus.purchased
            log_action(db, AuditAction.extra_metal_approved, request, current_user.id, req.order_id,
                       new_value={"status": _serialize(req.status), "purchased": True})

        if payload.buyer_note:
            req.buyer_note = payload.buyer_note
        if req.created_by:
            staff_orders.setdefault(req.created_by, set()).add(req.order_id)
        updated += 1

    if updated:
        note_suffix = f" — Not: {payload.buyer_note}" if payload.buyer_note else ""
        cost_suffix = (
            f" Onaylanan ekstra tutar: {total_approved_price:.2f}"
            if total_approved_price > 0 else ""
        )
        action_label = {
            ExtraMetalStatus.approved:  "onayladınız",
            ExtraMetalStatus.rejected:  "reddettiniz",
            ExtraMetalStatus.purchased: "satın alındı olarak işaretlediniz",
        }[payload.action_type]
        notif_type = (
            NotifType.extra_metal_rejected
            if payload.action_type == ExtraMetalStatus.rejected
            else NotifType.extra_metal_approved
        )
        notify_actor_and_managers(
            db,
            actor_id=current_user.id,
            notif_type=notif_type,
            actor_message=f"{updated} ekstra metal talebini {action_label}.{note_suffix}{cost_suffix}",
            manager_message=f"{current_user.username}, {updated} ekstra metal talebini işledi ({payload.action_type.value}).{note_suffix}{cost_suffix}",
            order_id=None,
        )

        # Talebi açan staff'a da hedefli bildirim (aktör buyer ise tekrar bildirme)
        staff_label = {
            ExtraMetalStatus.approved:  "onaylandı",
            ExtraMetalStatus.rejected:  "reddedildi",
            ExtraMetalStatus.purchased: "satın alındı",
        }[payload.action_type]
        for staff_id, order_ids in staff_orders.items():
            if staff_id == current_user.id:
                continue
            linked_order = next(iter(order_ids)) if len(order_ids) == 1 else None
            notify_user(
                db,
                recipient_id=staff_id,
                notif_type=notif_type,
                message=f"Açtığınız ekstra metal talebi/talepleri {staff_label}.{note_suffix}",
                order_id=linked_order,
            )

    db.commit()
    return {
        "requested": len(payload.request_ids),
        "updated": updated,
        "skipped": len(payload.request_ids) - updated,
        "action": payload.action_type.value,
        "approved_extra_price": str(total_approved_price),
    }
