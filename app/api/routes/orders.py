"""
Orders Endpoints
"""
import os
import uuid
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.models.models import (
    AuditLog, Invoice, InvoiceType, MetalRequest, Order,
    OrderStatus, OrderStatusHistory, Notification, NotifType,
    TempInvoiceFile, User, UserRole, AuditAction,
)
from app.schemas.schemas import (
    InvoiceOCRUploadOut,
    OrderCreate, OrderUpdate, OrderApprove,
    OrderStatusOut, PaginatedOrders, StatusHistoryOut,
)
from app.core.config import settings
from app.core.security import get_current_user, require_manager, require_accounting
from app.services.audit import log_action, _serialize
from app.utils.order_number import generate_order_number

router = APIRouter(prefix="/orders", tags=["orders"])

# Geçici fatura dosyaları için TTL
TEMP_INVOICE_TTL_HOURS = 2


# ─────────────────────────────────────────
# FATURA YÜKLE + OCR  (sipariş öncesi)
# ─────────────────────────────────────────

@router.post("/upload-invoice-ocr", response_model=InvoiceOCRUploadOut)
def upload_invoice_ocr(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Adım 1 — Sipariş oluşturulmadan fatura yüklenir ve OCR çalıştırılır.
    Dönen `invoice_token` frontend tarafından saklanır ve
    create-order isteğinde gönderilir.
    Token 2 saat geçerlidir; süresi dolan kayıtlar periyodik job ile temizlenir.
    """
    # 1. Uzantı kontrolü
    ext = file.filename.rsplit(".", 1)[-1].lower()
    if ext not in settings.ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Geçersiz dosya uzantısı.")

    # 2. Diske kaydet
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    token = str(uuid.uuid4())
    unique_filename = f"temp_{token}.{ext}"
    file_path = os.path.join(settings.UPLOAD_DIR, unique_filename)

    with open(file_path, "wb") as buffer:
        buffer.write(file.file.read())

    # 3. OCR — şimdilik mock; ileride gerçek OCR motoru buraya gelecek
    ocr_raw = {
        "detected_text": "METAL TICARET LTD. STI. TOPLAM TUTAR: 15450.00 TL",
        "parsed_amount": 15450.00,
        "date": "2026-06-06",
    }

    # 4. Geçici kayıt oluştur
    expires_at = datetime.now(timezone.utc) + timedelta(hours=TEMP_INVOICE_TTL_HOURS)
    db.add(TempInvoiceFile(
        token=token,
        file_path=file_path,
        file_type=ext,
        original_name=file.filename,
        ocr_raw=ocr_raw,
        uploaded_by=current_user.id,
        expires_at=expires_at,
    ))
    db.commit()

    return InvoiceOCRUploadOut(invoice_token=token, ocr_result=ocr_raw)


# ─────────────────────────────────────────
# SİPARİŞ OLUŞTUR
# ─────────────────────────────────────────

@router.post("/create-order", response_model=OrderStatusOut, status_code=201)
def create_order(
    payload: OrderCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Adım 2 — Tüm veriler tek transaction'da kaydedilir:
      - Order (order_title, müşteri bilgileri, tutar)
      - Invoice (OCR ham + kullanıcı onaylı veri, temp dosya kalıcıya taşınır)
      - MetalRequest × N (sınırsız kalem)
    Oluşturulan sipariş muhasebe kullanıcısı için pending_approval,
    müdür ise direkt active statüsünde açılır.
    """
    # 1. Token doğrulama — temp dosya var mı, süre dolmamış mı, sahibi mi?
    temp = db.query(TempInvoiceFile).filter(
        TempInvoiceFile.token == payload.invoice_token,
        TempInvoiceFile.uploaded_by == current_user.id,
        TempInvoiceFile.expires_at > datetime.now(timezone.utc),
    ).first()
    if not temp:
        raise HTTPException(
            status_code=400,
            detail="Geçersiz veya süresi dolmuş fatura token'ı. Lütfen faturayı tekrar yükleyin.",
        )

    # 2. Metal kalemi kontrolü
    if not payload.metal_items:
        raise HTTPException(status_code=400, detail="En az bir metal kalemi girilmeli.")

    # 3. Sipariş durumu
    initial_status = (
        OrderStatus.active
        if current_user.role == UserRole.manager
        else OrderStatus.pending_approval
    )

    # 4. Order
    order_number = generate_order_number(db)
    order = Order(
        order_number=order_number,
        order_title=payload.order_title,
        customer_name=payload.customer_name,
        customer_contact=None,                      # Eski alan, geriye dönük uyumluluk
        customer_phone=None,
        customer_address=None,
        estimated_amount=payload.estimated_amount,
        total_count=sum(item.quantity for item in payload.metal_items),
        status=initial_status,
        created_by=current_user.id,
    )
    db.add(order)
    db.flush()  # order.id üretilsin, foreign key'ler için gerekli

    # 5. Invoice — temp'ten kalıcıya
    db.add(Invoice(
        order_id=order.id,
        type=InvoiceType.initial,
        ocr_raw=temp.ocr_raw,                       # Ham OCR — asla değiştirilmez
        edited_data=payload.edited_invoice_data,    # Kullanıcının onayladığı veri
        file_path=temp.file_path,
        file_type=temp.file_type,
        original_name=temp.original_name,
        amount=payload.estimated_amount,
        uploaded_by=current_user.id,
    ))

    # 6. Metal kalemleri — sınırsız sayıda
    for item in payload.metal_items:
        db.add(MetalRequest(
            order_id=order.id,
            width=item.width,
            length=item.length,
            thickness=item.thickness,
            material=item.material,
            quantity=item.quantity,
            kg=item.kg,
            total=item.total,
            notes=item.notes,
            created_by=current_user.id,
        ))

    # 7. Durum geçmişi + audit log
    db.add(OrderStatusHistory(
        order_id=order.id,
        old_status=None,
        new_status=initial_status,
        changed_by=current_user.id,
    ))
    log_action(
        db, AuditAction.order_created, current_user.id, order.id,
        new_value={
            "order_number": order_number,
            "order_title":  payload.order_title,
            "status":       initial_status,
            "metal_items":  len(payload.metal_items),
        },
    )

    # 8. Müdürlere bildirim (müdür kendi siparişini oluşturuyorsa bildirim yok)
    if current_user.role != UserRole.manager:
        managers = db.query(User).filter(
            User.role == UserRole.manager,
            User.is_active == True,
        ).all()
        for manager in managers:
            db.add(Notification(
                recipient_id=manager.id,
                order_id=order.id,
                type=NotifType.approval_needed,
                message=f'"{payload.order_title}" ({order_number}) onay bekliyor.',
            ))

    # 9. Temp kaydı sil — işi bitti
    db.delete(temp)

    db.commit()
    db.refresh(order)
    return order


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
        # order_title ve customer_name üzerinde arama
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
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı.")
    return order


# ─────────────────────────────────────────
# SİPARİŞ GÜNCELLE
# ─────────────────────────────────────────

@router.patch("/{order_id}/update-order", response_model=OrderStatusOut)
def update_order(
    order_id: int,
    payload: OrderUpdate,
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

    log_action(db, AuditAction.order_updated, current_user.id, order.id,
               old_value=old_values,
               new_value=_serialize(changed_fields))
    db.commit()
    db.refresh(order)
    return order


# ─────────────────────────────────────────
# SİPARİŞ ONAYLA / REDDET
# ─────────────────────────────────────────

@router.post("/{order_id}/approve-order", response_model=OrderStatusOut)
def approve_order(
    order_id: int,
    payload: OrderApprove,
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
    log_action(db, audit_action, current_user.id, order.id,
               old_value={"status": old_status},
               new_value={"status": order.status})

    db.commit()
    db.refresh(order)
    return order


# ─────────────────────────────────────────
# SİPARİŞ TAMAMLA
# ─────────────────────────────────────────

@router.post("/{order_id}/complete-order", response_model=OrderStatusOut)
def complete_order(
    order_id: int,
    final_amount_input: float,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_accounting),
):
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order or order.status != OrderStatus.active:
        raise HTTPException(status_code=400, detail="Sadece aktif siparişler tamamlanabilir.")

    old_status         = order.status
    order.final_amount = final_amount_input
    order.updated_at   = datetime.now(timezone.utc)

    # Tutar uyuşmazlığı kontrolü (float hassasiyet payı: 0.01)
    if abs(float(order.estimated_amount) - final_amount_input) > 0.01:
        order.status = OrderStatus.mismatch_review

        db.add(OrderStatusHistory(
            order_id=order.id, old_status=old_status,
            new_status=OrderStatus.mismatch_review, changed_by=current_user.id,
            note=(
                f"UYARI: İlk tutar ({order.estimated_amount}) ile "
                f"son tutar ({final_amount_input}) uyuşmuyor. Müdür onayı bekleniyor."
            ),
        ))
        db.add(Notification(
            recipient_id=current_user.id,
            order_id=order.id,
            type=NotifType.approval_needed,
            message=(
                f'"{order.order_title}" ({order.order_number}) — '
                f"tutar uyuşmazlığı var! Kontrol edin."
            ),
        ))
        log_action(db, AuditAction.amount_changed, current_user.id, order.id,
                   old_value={"estimated_amount": str(order.estimated_amount)},
                   new_value={"final_amount": str(final_amount_input),
                              "status": OrderStatus.mismatch_review})
        db.commit()
        db.refresh(order)
        return order

    # Tutarlar uyuşuyor — normal tamamlama
    order.status       = OrderStatus.completed
    order.completed_at = datetime.now(timezone.utc)

    db.add(OrderStatusHistory(
        order_id=order.id, old_status=old_status,
        new_status=OrderStatus.completed, changed_by=current_user.id,
    ))
    log_action(db, AuditAction.order_completed, current_user.id, order.id,
               old_value={"status": old_status},
               new_value={"status": OrderStatus.completed})

    db.commit()
    db.refresh(order)
    return order


# ─────────────────────────────────────────
# TUTAR UYUŞMAZLIĞINI MÜDÜR ONAYLA
# ─────────────────────────────────────────

@router.post("/{order_id}/force-complete-mismatch", response_model=OrderStatusOut)
def force_complete_mismatch(
    order_id: int,
    manager_note: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_manager),
):
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order or order.status != OrderStatus.mismatch_review:
        raise HTTPException(status_code=400, detail="Bu sipariş tutar inceleme durumunda değil.")
    if not manager_note.strip():
        raise HTTPException(status_code=400, detail="Uyuşmazlığı onaylamak için açıklama girilmeli.")

    old_status         = order.status
    order.status       = OrderStatus.completed
    order.completed_at = datetime.now(timezone.utc)

    db.add(OrderStatusHistory(
        order_id=order.id, old_status=old_status,
        new_status=OrderStatus.completed, changed_by=current_user.id,
        note=f"Müdür uyuşmazlığı manuel onayladı. Not: {manager_note}",
    ))
    log_action(db, AuditAction.order_completed, current_user.id, order.id,
               old_value={"status": old_status},
               new_value={"status": OrderStatus.completed, "manager_note": manager_note})

    db.commit()
    db.refresh(order)
    return order


# ─────────────────────────────────────────
# FATURA İŞLE (final fatura — sipariş sonrası)
# ─────────────────────────────────────────

@router.post("/{order_id}/process-invoice")
def process_invoice(
    order_id: int,
    invoice_type: InvoiceType,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Sipariş oluştuktan SONRA fatura yükleme (genellikle final fatura için).
    İlk (initial) fatura create-order akışında upload-invoice-ocr ile kaydedilir.
    """
    ext = file.filename.rsplit(".", 1)[-1].lower()
    if ext not in settings.ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Geçersiz dosya uzantısı.")

    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    unique_filename = f"{uuid.uuid4()}.{ext}"
    file_path = os.path.join(settings.UPLOAD_DIR, unique_filename)

    with open(file_path, "wb") as buffer:
        buffer.write(file.file.read())

    # OCR mock — ileride gerçek motor
    ocr_raw_mock = {
        "detected_text": "METAL TICARET LTD. STI. TOPLAM TUTAR: 15450.00 TL",
        "parsed_amount": 15450.00,
        "date": "2026-06-06",
    }

    invoice = Invoice(
        order_id=order_id,
        type=invoice_type,
        ocr_raw=ocr_raw_mock,
        edited_data=ocr_raw_mock,
        file_path=file_path,
        file_type=ext,
        original_name=file.filename,
        amount=ocr_raw_mock["parsed_amount"],
        uploaded_by=current_user.id,
    )
    db.add(invoice)
    db.commit()
    db.refresh(invoice)

    return {
        "message":    "Fatura başarıyla yüklendi ve OCR işleminden geçirildi.",
        "invoice_id": invoice.id,
        "ocr_result": invoice.edited_data,
    }


# ─────────────────────────────────────────
# GLOBAL SİPARİŞ GEÇMİŞİ (audit)
# ─────────────────────────────────────────

@router.get("/global-order-history", tags=["audit", "orders"])
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
# SİPARİŞ DURUM GEÇMİŞİ
# ─────────────────────────────────────────

@router.get("/{order_id}/specific-order-history", response_model=List[StatusHistoryOut])
def order_history(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return (
        db.query(OrderStatusHistory)
        .filter(OrderStatusHistory.order_id == order_id)
        .order_by(OrderStatusHistory.created_at)
        .all()
    )


# ─────────────────────────────────────────
# SİPARİŞ SİL (soft delete)
# ─────────────────────────────────────────

@router.delete("/{order_id}/delete-order", status_code=204)
def delete_order(
    order_id: int,
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
    log_action(db, AuditAction.order_deleted, current_user.id, order.id,
               old_value={"order_number": order.order_number, "status": old_status},
               new_value={"status": OrderStatus.deleted})

    db.commit()