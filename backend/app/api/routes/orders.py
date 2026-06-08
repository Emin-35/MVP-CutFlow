"""
Orders Endpoints
"""
import os
import uuid
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, File
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.models.models import (
    AuditLog, Invoice, InvoiceType, MetalRequest, Order,
    OrderStatus, OrderStatusHistory, Notification, NotifType,
    TempInvoiceFile, User, UserRole, AuditAction,
)
from app.schemas.schemas import (
    FinalInvoiceSubmit,
    InvoiceOCRUploadOut,
    MismatchResolve,
    OrderCreate, OrderUpdate, OrderApprove,
    OrderStatusOut, PaginatedOrders, StatusHistoryOut,
    EditGrantedInvoiceSubmit,  # YENİ
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

@router.post("/first-invoice-ocr", response_model=InvoiceOCRUploadOut)
def first_invoice_ocr(
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
    unique_filename = f"temp_initial_{token}.{ext}"
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
    request: Request,
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
        customer_contact=None,           # Eski alan, geriye dönük uyumluluk
        customer_phone=payload.customer_phone,
        customer_address=payload.customer_address,
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
        db, AuditAction.order_created, request, current_user.id, order.id,
        new_value={
            "order_number":     order_number,
            "order_title":      payload.order_title,
            "status":           initial_status,
            "estimated_amount": str(payload.estimated_amount),
            "metal_items":      len(payload.metal_items),
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

@router.get("/editable-orders", response_model=PaginatedOrders)
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
# SİPARİŞ TAMAMLA (eski endpoint — deprecated, kaldırılabilir) DEPRECATED
# ─────────────────────────────────────────

"""
@router.post("/{order_id}/complete-order", response_model=OrderStatusOut)
def complete_order(
    order_id: int,
    final_amount_input: float,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_accounting),
):
    '''
    DEPRECATED: Bunun yerine upload-final-invoice-ocr + final-invoice-submit akışı kullanılmalı.
    Bu endpoint fatura dosyası olmadan çalışır, yalnızca geriye dönük uyumluluk için bırakıldı.
    '''
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order or order.status != OrderStatus.active:
        raise HTTPException(status_code=400, detail="Sadece aktif siparişler tamamlanabilir.")

    old_status         = order.status
    order.final_amount = final_amount_input
    order.updated_at   = datetime.now(timezone.utc)

    estimated = float(order.estimated_amount or 0)

    if abs(estimated - final_amount_input) > 0.01:
        order.status = OrderStatus.mismatch_review

        db.add(OrderStatusHistory(
            order_id=order.id, old_status=old_status,
            new_status=OrderStatus.mismatch_review, changed_by=current_user.id,
            note=(
                f"Tutar Uyuşmazlığı! İlk Tutar: {estimated} | "
                f"Final Tutar: {final_amount_input}"
            ),
        ))
        managers = db.query(User).filter(
            User.role == UserRole.manager, User.is_active == True
        ).all()
        for manager in managers:
            db.add(Notification(
                recipient_id=manager.id, order_id=order.id,
                type=NotifType.approval_needed,
                message=f'"{order.order_title}" ({order.order_number}) — tutar uyuşmazlığı! Müdür onayı bekleniyor.',
            ))
        log_action(db, AuditAction.amount_changed, current_user.id, order.id,
                   old_value={"estimated_amount": str(estimated)},
                   new_value={"final_amount": str(final_amount_input),
                              "status": OrderStatus.mismatch_review})
        db.commit()
        db.refresh(order)
        return order

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
"""

# ─────────────────────────────────────────
# FINAL FATURA YÜKLE + OCR
# ─────────────────────────────────────────

@router.post("/{order_id}/upload-final-invoice-ocr", response_model=InvoiceOCRUploadOut)
def upload_final_invoice_ocr(
    order_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Final Faturası — Adım 1
    Aktif siparişe ait final faturası yüklenir ve OCR çalıştırılır.
    Dönen token frontend'de saklanır, final-invoice-submit'e gönderilir.
    """
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order or order.status != OrderStatus.active:
        raise HTTPException(status_code=400, detail="Sadece aktif siparişlere final faturası yüklenebilir.")

    ext = file.filename.rsplit(".", 1)[-1].lower()
    if ext not in settings.ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Geçersiz dosya uzantısı.")

    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    token = str(uuid.uuid4())
    unique_filename = f"temp_final_{token}.{ext}"
    file_path = os.path.join(settings.UPLOAD_DIR, unique_filename)

    with open(file_path, "wb") as buffer:
        buffer.write(file.file.read())

    ocr_raw = {
        "detected_text": "FINAL METAL TICARET LTD. STI. TOPLAM TUTAR: 15450.00 TL",
        "parsed_amount": 15450.00,
        "date": "2026-06-07",
    }

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
# FINAL FATURA SUBMIT + TUTAR KARŞILAŞTIRMA
# ─────────────────────────────────────────

@router.post("/{order_id}/final-invoice-submit", response_model=OrderStatusOut)
def final_invoice_submit(
    order_id: int,
    payload: FinalInvoiceSubmit,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_accounting),
):
    """
    Final Faturası — Adım 2
    Token doğrulanır, fatura Invoice tablosuna yazılır.
    Tutar karşılaştırması:
      - Eşleşirse → completed
      - Uyuşmazsa  → mismatch_review (müdüre bildirim)
    """
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order or order.status != OrderStatus.active:
        raise HTTPException(status_code=400, detail="Sadece aktif siparişlere final faturası yüklenebilir.")

    temp = db.query(TempInvoiceFile).filter(
        TempInvoiceFile.token == payload.invoice_token,
        TempInvoiceFile.uploaded_by == current_user.id,
        TempInvoiceFile.expires_at > datetime.now(timezone.utc),
    ).first()
    if not temp:
        raise HTTPException(status_code=400, detail="Geçersiz veya süresi dolmuş fatura token'ı. Tekrar yükleyin.")

    old_status = order.status

    # ── BUG FIX: estimated_amount None ise 0 yerine hata ver ──────────────────
    if order.estimated_amount is None:
        raise HTTPException(
            status_code=400,
            detail="Siparişe ait ilk tutar bulunamadı. Sipariş verilerini kontrol edin.",
        )
    estimated = float(order.estimated_amount)
    final_val = float(payload.final_amount)
    # ──────────────────────────────────────────────────────────────────────────

    order.final_amount = payload.final_amount
    order.updated_at   = datetime.now(timezone.utc)

    db.add(Invoice(
        order_id=order.id,
        type=InvoiceType.final,
        ocr_raw=temp.ocr_raw,
        edited_data=payload.edited_data,
        file_path=temp.file_path,
        file_type=temp.file_type,
        original_name=temp.original_name,
        amount=payload.final_amount,
        uploaded_by=current_user.id,
    ))

    if abs(estimated - final_val) > 0.01:
        # UYUŞMAZLIK → müdür incelemesine al
        order.status = OrderStatus.mismatch_review
        db.add(OrderStatusHistory(
            order_id=order.id, old_status=old_status,
            new_status=OrderStatus.mismatch_review, changed_by=current_user.id,
            note=(
                f"Tutar Uyuşmazlığı! "
                f"İlk Tutar: {estimated:.2f} | "
                f"Final Tutar: {final_val:.2f}"
            ),
        ))
        managers = db.query(User).filter(
            User.role == UserRole.manager, User.is_active == True
        ).all()
        for manager in managers:
            db.add(Notification(
                recipient_id=manager.id, order_id=order.id,
                type=NotifType.approval_needed,
                message=(
                    f'"{order.order_title}" siparişinde fatura uyuşmazlığı! '
                    f'İlk: {estimated:.2f} → Final: {final_val:.2f}. Müdür onayı bekleniyor.'
                ),
            ))
        log_action(db, AuditAction.amount_changed, request, current_user.id, order.id,
                   old_value={"estimated_amount": str(estimated)},
                   new_value={"final_amount": str(final_val), "status": "mismatch_review"})
    else:
        # UYUŞTU → tamamla
        order.status       = OrderStatus.completed
        order.completed_at = datetime.now(timezone.utc)
        db.add(OrderStatusHistory(
            order_id=order.id, old_status=old_status,
            new_status=OrderStatus.completed, changed_by=current_user.id,
            note="Faturalar eşleşti. Sipariş başarıyla tamamlandı.",
        ))
        log_action(db, AuditAction.order_completed, request, current_user.id, order.id,
                   old_value={"status": old_status},
                   new_value={"status": "completed"})

    db.delete(temp)
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
# FATURA DÜZENLE (Muhasebe — edit_granted sonrası)
# ─────────────────────────────────────────

@router.post("/{order_id}/upload-edit-invoice-ocr", response_model=InvoiceOCRUploadOut)
def upload_edit_invoice_ocr(
    order_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_accounting),
):
    """
    Düzenleme izni (edit_granted) verilmiş siparişe yeni fatura yüklenir.
    Adım 1: Dosya OCR'a sokulur, token döner.
    """
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order or order.status != OrderStatus.edit_granted:
        raise HTTPException(
            status_code=400,
            detail="Bu sipariş düzenleme izni durumunda değil.",
        )

    ext = file.filename.rsplit(".", 1)[-1].lower()
    if ext not in settings.ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Geçersiz dosya uzantısı.")

    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    token = str(uuid.uuid4())
    unique_filename = f"temp_edit_{token}.{ext}"
    file_path = os.path.join(settings.UPLOAD_DIR, unique_filename)

    with open(file_path, "wb") as buffer:
        buffer.write(file.file.read())

    # OCR Mock — ileride gerçek motor
    ocr_raw = {
        "detected_text": "DÜZENLEME — METAL TICARET LTD. STI. TOPLAM TUTAR: 15450.00 TL",
        "parsed_amount": 15450.00,
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }

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


@router.post("/{order_id}/submit-edit-invoice", response_model=OrderStatusOut)
def submit_edit_invoice(
    order_id: int,
    payload: EditGrantedInvoiceSubmit,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_accounting),
):
    """
    Düzenleme izni (edit_granted) — Adım 2.
    Muhasebe yeni faturayı ve tutarı onaylar.

    Ne olur:
      - Mevcut initial invoice güncellenir (dosya + tutar + edited_data).
      - estimated_amount güncellenir (muhasebe yeni tutarı girdi).
      - Sipariş pending_approval'a döner → müdür tekrar onaylar.
      - Müdüre bildirim gider.
    """
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order or order.status != OrderStatus.edit_granted:
        raise HTTPException(
            status_code=400,
            detail="Bu sipariş düzenleme izni durumunda değil.",
        )

    temp = db.query(TempInvoiceFile).filter(
        TempInvoiceFile.token == payload.invoice_token,
        TempInvoiceFile.uploaded_by == current_user.id,
        TempInvoiceFile.expires_at > datetime.now(timezone.utc),
    ).first()
    if not temp:
        raise HTTPException(
            status_code=400,
            detail="Geçersiz veya süresi dolmuş fatura token'ı. Tekrar yükleyin.",
        )

    old_status         = order.status
    old_estimated      = order.estimated_amount

    # Mevcut initial faturayı bul ve güncelle
    initial_invoice = (
        db.query(Invoice)
        .filter(Invoice.order_id == order.id, Invoice.type == InvoiceType.initial)
        .order_by(Invoice.uploaded_at.desc())
        .first()
    )
    if initial_invoice:
        initial_invoice.file_path     = temp.file_path
        initial_invoice.file_type     = temp.file_type
        initial_invoice.original_name = temp.original_name
        initial_invoice.ocr_raw       = temp.ocr_raw
        initial_invoice.edited_data   = payload.edited_invoice_data
        initial_invoice.amount        = payload.new_amount
        initial_invoice.uploaded_by   = current_user.id
        initial_invoice.uploaded_at   = datetime.now(timezone.utc)
    else:
        # Nadir durum: initial fatura yoksa yenisini oluştur
        db.add(Invoice(
            order_id=order.id,
            type=InvoiceType.initial,
            ocr_raw=temp.ocr_raw,
            edited_data=payload.edited_invoice_data,
            file_path=temp.file_path,
            file_type=temp.file_type,
            original_name=temp.original_name,
            amount=payload.new_amount,
            uploaded_by=current_user.id,
        ))

    # Order güncelle
    order.estimated_amount = payload.new_amount
    order.final_amount     = None               # final fatura sıfırlanır, akış yeniden başlar
    order.status           = OrderStatus.mismatch_review
    order.updated_at       = datetime.now(timezone.utc)

    db.add(OrderStatusHistory(
        order_id=order.id, old_status=old_status,
        new_status=OrderStatus.mismatch_review, changed_by=current_user.id,
        note=(
            f"Muhasebe faturayı düzeltti. "
            f"Eski Tutar: {old_estimated} → Yeni Tutar: {payload.new_amount}. "
            f"Müdür onayı bekleniyor."
        ),
    ))
    log_action(db, AuditAction.invoice_uploaded, request, current_user.id, order.id,
               old_value={"estimated_amount": str(old_estimated), "status": old_status},
               new_value={"estimated_amount": str(payload.new_amount), "status": "mismatch_review"})

    # Müdürlere bildirim
    managers = db.query(User).filter(
        User.role == UserRole.manager, User.is_active == True
    ).all()
    for manager in managers:
        db.add(Notification(
            recipient_id=manager.id, order_id=order.id,
            type=NotifType.approval_needed,
            message=(
                f'"{order.order_title}" ({order.order_number}) faturası güncellendi. '
                f"Yeni tutar: {payload.new_amount}. Onay bekleniyor."
            ),
        ))

    db.delete(temp)
    db.commit()
    db.refresh(order)
    return order


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