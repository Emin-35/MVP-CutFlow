import os
import uuid
from datetime import datetime, timezone, timedelta
from typing import List, Optional
from pathlib import Path

from fastapi import APIRouter, UploadFile, Depends, HTTPException, Request, File
from sqlalchemy.orm import Session

from app.core.security import get_current_user, require_accounting
from app.db.base import get_db
from app.models.models import AuditAction, Invoice, InvoiceType, MetalRequest, NotifType, Notification, Order, OrderStatus, OrderStatusHistory, TempInvoiceFile, User, UserRole
from app.schemas.schemas import EditGrantedInvoiceSubmit, FinalInvoiceSubmit, InvoiceOCRUploadOut, OrderCreate, OrderStatusOut
from app.core.config import settings
from app.services.audit import log_action
from app.utils.notification_services import send_order_notification_to_managers
from app.utils.order_number import generate_order_number


router = APIRouter(prefix="/order-ocr", tags=["ocr-helper"])

# Geçici fatura dosyaları için TTL
TEMP_INVOICE_TTL_HOURS = 1


# ─────────────────────────────────────────
# FATURA YÜKLE + OCR  (sipariş öncesi)
# ─────────────────────────────────────────

@router.post("/upload-first-invoice-ocr", response_model=InvoiceOCRUploadOut)
def upload_first_invoice_ocr(
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

    if not file.filename:
        raise AttributeError("Dosya adı bulunamadı.")

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
        "_mock": True,
    }

    # 4. Geçici kayıt oluştur
    expires_at = datetime.now(timezone.utc) + timedelta(hours=TEMP_INVOICE_TTL_HOURS)
    db.add(TempInvoiceFile(
        token=token,
        file_path=file_path,
        file_type=ext,
        original_name=Path(file.filename).name,
        ocr_raw=ocr_raw,
        uploaded_by=current_user.id,
        expires_at=expires_at,
    ))
    db.commit()

    return InvoiceOCRUploadOut(invoice_token=token, ocr_result=ocr_raw)


# ─────────────────────────────────────────
# SİPARİŞ OLUŞTUR (ilk fatura yükleme sonrası)
# ─────────────────────────────────────────

@router.post("/submit-first-invoice", response_model=OrderStatusOut, status_code=201)
def submit_first_invoice(
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

    # 8. Müdürlere bildirim
    send_order_notification_to_managers(db, order, payload.order_title, order_number)

    # 9. Temp kaydı sil — işi bitti
    db.delete(temp)

    db.commit()
    db.refresh(order)
    return order


# ─────────────────────────────────────────
# FINAL FATURA YÜKLE + OCR
# ─────────────────────────────────────────

@router.post("/{order_id}/upload-final-invoice-ocr", response_model=InvoiceOCRUploadOut)
def upload_final_invoice_ocr(
    order_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_accounting),
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

    if not file.filename:
        raise AttributeError("Dosya adı bulunamadı.")

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
        "_mock": True,
    }

    expires_at = datetime.now(timezone.utc) + timedelta(hours=TEMP_INVOICE_TTL_HOURS)
    db.add(TempInvoiceFile(
        token=token,
        file_path=file_path,
        file_type=ext,
        original_name=Path(file.filename).name,
        ocr_raw=ocr_raw,
        uploaded_by=current_user.id,
        expires_at=expires_at,
    ))
    db.commit()

    return InvoiceOCRUploadOut(invoice_token=token, ocr_result=ocr_raw)


# ─────────────────────────────────────────
# FINAL FATURA SUBMIT + TUTAR KARŞILAŞTIRMA
# ─────────────────────────────────────────

@router.post("/{order_id}/submit-final-invoice", response_model=OrderStatusOut)
def submit_final_invoice(
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

    if not file.filename:
        raise AttributeError("Dosya adı bulunamadı.")

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
        "_mock": True,
    }

    expires_at = datetime.now(timezone.utc) + timedelta(hours=TEMP_INVOICE_TTL_HOURS)
    db.add(TempInvoiceFile(
        token=token,
        file_path=file_path,
        file_type=ext,
        original_name=Path(file.filename).name,
        ocr_raw=ocr_raw,
        uploaded_by=current_user.id,
        expires_at=expires_at,
    ))
    db.commit()

    return InvoiceOCRUploadOut(invoice_token=token, ocr_result=ocr_raw)

# ─────────────────────────────────────────
# FATURA BİLGİLERİ (Muhasebe — edit_granted sonrası)
# ─────────────────────────────────────────

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
      - Sipariş mismatch_review'a döner → müdür tekrar onaylar.
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