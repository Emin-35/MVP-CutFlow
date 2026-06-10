"""
Order OCR Helper Endpoints (v3)

Değişiklik özeti:
  - edit_granted / mismatch_review akışı TAMAMEN KALDIRILDI
    (upload-edit-invoice-ocr ve submit-edit-invoice endpoint'leri silindi)
  - Final fatura tutar uyuşmazlığı: backend 409 + karşılaştırma detayı döner,
    karar frontend'de verilir:
        "Doğru faturayı yükle"   → yeni dosya ile akış baştan
        "Bu fatura ile devam et" → aynı token + force_complete=True ile tekrar submit
    Manager'a bildirim GİTMEZ, manager onayı YOKTUR.
  - Sipariş oluşturma: status her zaman pending_approval → buyer satın alır
    (eski 'manager ise direkt active' kuralı kaldırıldı)
  - Bildirimler: send_order_notification_to_managers yerine
    app.services.notification_service (rol bazlı, kişi başı satır)
  - Dosya yolu: app.utils.storage → uploads/<rol>/<yıl-ay>/<uuid>.<uzantı>
  - require_accounting → require_accountant (security v2 ile uyum)
  - submit_final_invoice SADECE accountant (manager hariç) — siparişi
    tamamlama yetkisi yalnızca muhasebededir.
"""
from datetime import datetime, timezone, timedelta
from pathlib import Path

from fastapi import APIRouter, UploadFile, Depends, HTTPException, Request, File
from sqlalchemy.orm import Session

from app.core.security import get_current_user, require_staff, require_accountant
from app.db.base import get_db
from app.models.models import (
    AuditAction, Invoice, InvoiceType, MetalRequest, Order, OrderFile,
    OrderStatus, OrderStatusHistory, TempInvoiceFile, User, UserRole,
)
from app.schemas.schemas import (
    FinalInvoiceSubmit, InvoiceCompareOut, InvoiceOCRUploadOut,
    OrderCreate, OrderStatusOut,
)
from app.services.audit import log_action
from app.services.notification_service import notify_new_order
from app.utils.order_number import generate_order_number
from app.utils.storage import build_storage_path, ensure_dir, UnsupportedFileTypeError

router = APIRouter(prefix="/order-ocr", tags=["ocr-helper"])

# Geçici fatura dosyaları için TTL
TEMP_INVOICE_TTL_HOURS = 2


# ─────────────────────────────────────────
# ORTAK: dosya kaydet + temp kayıt oluştur
# ─────────────────────────────────────────

def _save_temp_upload(db: Session, file: UploadFile, current_user: User) -> tuple[str, dict]:
    """
    Dosyayı güvenli yola yazar, mock OCR çalıştırır, TempInvoiceFile oluşturur.
    Dönüş: (token, ocr_raw)
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="Dosya adı bulunamadı.")

    try:
        rel_path, token = build_storage_path(
            role=current_user.role,
            content_type=file.content_type,
            original_name=file.filename,
        )
    except UnsupportedFileTypeError:
        raise HTTPException(status_code=400, detail="Geçersiz dosya tipi. PDF veya resim yükleyin.")

    abs_path = ensure_dir(rel_path)
    with open(abs_path, "wb") as buffer:
        buffer.write(file.file.read())

    # OCR — şimdilik mock; ileride PaddleOCR/PyMuPDF buraya bağlanacak
    ocr_raw = {
        "detected_text": "METAL TICARET LTD. STI. TOPLAM TUTAR: 15450.00 TL",
        "parsed_amount": 15450.00,
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "_mock": True,
    }

    expires_at = datetime.now(timezone.utc) + timedelta(hours=TEMP_INVOICE_TTL_HOURS)
    db.add(TempInvoiceFile(
        token=token,
        file_path=rel_path,                       # DB'ye göreli yol yazılır
        file_type=file.content_type or "application/octet-stream",
        original_name=Path(file.filename).name,   # sadece metadata
        ocr_raw=ocr_raw,
        uploaded_by=current_user.id,
        expires_at=expires_at,
    ))
    db.commit()
    return token, ocr_raw


def _get_valid_temp(db: Session, token: str, user_id: int) -> TempInvoiceFile:
    temp = db.query(TempInvoiceFile).filter(
        TempInvoiceFile.token == token,
        TempInvoiceFile.uploaded_by == user_id,
        TempInvoiceFile.expires_at > datetime.now(timezone.utc),
    ).first()
    if not temp:
        raise HTTPException(
            status_code=400,
            detail="Geçersiz veya süresi dolmuş fatura token'ı. Lütfen faturayı tekrar yükleyin.",
        )
    return temp


# ─────────────────────────────────────────
# STAFF — Adım 1: İLK FATURA YÜKLE + OCR
# ─────────────────────────────────────────

@router.post("/upload-first-invoice-ocr", response_model=InvoiceOCRUploadOut)
def upload_first_invoice_ocr(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    """
    Sipariş oluşturulmadan şema (PDF/resim) yüklenir, OCR çalıştırılır.
    Dönen invoice_token frontend'de saklanır, submit-first-invoice'a gönderilir.
    Token 2 saat geçerlidir. (staff veya manager)
    """
    token, ocr_raw = _save_temp_upload(db, file, current_user)
    return InvoiceOCRUploadOut(invoice_token=token, ocr_result=ocr_raw)


# ─────────────────────────────────────────
# STAFF — Adım 2: SİPARİŞ OLUŞTUR
# ─────────────────────────────────────────

@router.post("/submit-first-invoice", response_model=OrderStatusOut, status_code=201)
def submit_first_invoice(
    payload: OrderCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    """
    Tüm veriler tek transaction'da kaydedilir:
      - Order, Invoice (initial), OrderFile (şema dosyası), MetalRequest × N
    Sipariş HER ZAMAN pending_approval ile açılır → buyer satın alınca active olur.
    Buyer rolündeki tüm aktif kullanıcılara bildirim gider.
    """
    temp = _get_valid_temp(db, payload.invoice_token, current_user.id)

    if not payload.metal_items:
        raise HTTPException(status_code=400, detail="En az bir metal kalemi girilmeli.")

    initial_status = OrderStatus.pending_approval

    order_number = generate_order_number(db)
    order = Order(
        order_number=order_number,
        order_title=payload.order_title,
        customer_name=payload.customer_name,
        customer_contact=None,
        customer_phone=payload.customer_phone,
        customer_address=payload.customer_address,
        estimated_amount=payload.estimated_amount,
        total_count=sum(item.quantity for item in payload.metal_items),
        status=initial_status,
        created_by=current_user.id,
    )
    db.add(order)
    db.flush()  # order.id üretilsin

    # Invoice — temp'ten kalıcıya
    db.add(Invoice(
        order_id=order.id,
        type=InvoiceType.initial,
        ocr_raw=temp.ocr_raw,                     # Ham OCR — asla değiştirilmez
        edited_data=payload.edited_invoice_data,  # Staff'ın düzenleyip onayladığı veri
        file_path=temp.file_path,
        file_type=temp.file_type,
        original_name=temp.original_name,
        amount=payload.estimated_amount,
        uploaded_by=current_user.id,
    ))

    # Şema dosyası order_files'a da bağlanır (sipariş dosyaları sekmesi için)
    db.add(OrderFile(
        order_id=order.id,
        file_path=temp.file_path,
        file_type=temp.file_type,
        original_name=temp.original_name,
        uploaded_by=current_user.id,
    ))

    # Metal kalemleri — sınırsız
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

    db.add(OrderStatusHistory(
        order_id=order.id, old_status=None,
        new_status=initial_status, changed_by=current_user.id,
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

    # Buyer(lar)a bildirim — eski send_order_notification_to_managers KALDIRILDI
    notify_new_order(
        db, order.id,
        message=f'Yeni sipariş: "{order.order_title}" ({order_number}). Satın alma bekleniyor.',
    )

    db.delete(temp)
    db.commit()
    db.refresh(order)
    return order


# ─────────────────────────────────────────
# ACCOUNTANT — Adım 1: FINAL FATURA YÜKLE + OCR
# ─────────────────────────────────────────

@router.post("/{order_id}/upload-final-invoice-ocr", response_model=InvoiceOCRUploadOut)
def upload_final_invoice_ocr(
    order_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_accountant),
):
    """
    Aktif siparişe final faturası yüklenir, OCR çalıştırılır.
    Dönen token submit-final-invoice'a gönderilir.
    """
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order or order.status != OrderStatus.active:
        raise HTTPException(status_code=400, detail="Sadece aktif siparişlere final faturası yüklenebilir.")

    token, ocr_raw = _save_temp_upload(db, file, current_user)
    return InvoiceOCRUploadOut(invoice_token=token, ocr_result=ocr_raw)


# ─────────────────────────────────────────
# ACCOUNTANT — Adım 2: FINAL FATURA SUBMIT + TUTAR KARŞILAŞTIRMA
# ─────────────────────────────────────────

@router.post("/{order_id}/submit-final-invoice", response_model=OrderStatusOut)
def submit_final_invoice(
    order_id: int,
    payload: FinalInvoiceSubmit,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Token doğrulanır, tutar karşılaştırılır.

    SADECE accountant kullanabilir (manager DAHİL DEĞİL) — siparişi tamamlama
    yetkisi yalnızca muhasebededir.

    Akış:
      - Tutar eşleşiyor            → fatura kaydedilir, status → completed
      - Tutar uyuşmuyor + force_complete=False
            → HİÇBİR ŞEY kaydedilmez, 409 + karşılaştırma detayı döner.
              Frontend uyarı ekranı gösterir:
                "Doğru faturayı yükle"   → accountant yeni dosya yükler (Adım 1'e döner)
                "Bu fatura ile devam et" → AYNI token ile force_complete=True gönderilir
              (Temp dosya silinmez, token geçerli kalır.)
      - Tutar uyuşmuyor + force_complete=True
            → fatura kaydedilir, status → completed,
              audit log'a final_invoice_edited + amount_changed bilgisi yazılır.

    Manager'a bildirim gitmez, manager onayı yoktur.
    """
    # Sıkı rol kontrolü: yalnızca accountant
    if current_user.role != UserRole.accountant:
        raise HTTPException(
            status_code=403,
            detail="Siparişi tamamlama (final fatura) yetkisi yalnızca muhasebeye aittir.",
        )

    order = db.query(Order).filter(Order.id == order_id).first()
    if not order or order.status != OrderStatus.active:
        raise HTTPException(status_code=400, detail="Sadece aktif siparişlere final faturası yüklenebilir.")

    temp = _get_valid_temp(db, payload.invoice_token, current_user.id)

    # estimated_amount None ise 0 ile karşılaştırma bug'ına düşme
    if order.estimated_amount is None:
        raise HTTPException(
            status_code=400,
            detail="Siparişe ait ilk tutar bulunamadı. Sipariş verilerini kontrol edin.",
        )

    estimated = float(order.estimated_amount)
    final_val = float(payload.final_amount)
    amounts_match = abs(estimated - final_val) <= 0.01

    # ── UYUŞMAZLIK + force yok → kaydetme, frontend'e karar bırak ──
    if not amounts_match and not payload.force_complete:
        compare = InvoiceCompareOut(
            order_id=order.id,
            initial_amount=order.estimated_amount,
            final_amount=payload.final_amount,
            match=False,
            difference=payload.final_amount - order.estimated_amount,
        )
        # Temp dosya SİLİNMEZ — "bu fatura ile devam et" aynı token'ı kullanır
        raise HTTPException(
            status_code=409,
            detail={
                "reason": "amount_mismatch",
                "message": (
                    "Fatura tutarı ilk şemadaki tutar ile uyuşmuyor. "
                    "Lütfen doğru faturayı yükleyin veya bu fatura ile devam edin."
                ),
                "comparison": compare.model_dump(mode="json"),
            },
        )

    # ── Eşleşti veya force_complete=True → kaydet ve tamamla ──
    old_status = order.status

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

    order.final_amount = payload.final_amount
    order.status       = OrderStatus.completed
    order.completed_by = current_user.id
    order.completed_at = datetime.now(timezone.utc)
    order.updated_at   = datetime.now(timezone.utc)

    history_note = (
        "Faturalar eşleşti. Sipariş tamamlandı."
        if amounts_match else
        f"Tutar farkına rağmen muhasebe onayıyla tamamlandı. "
        f"İlk: {estimated:.2f} → Final: {final_val:.2f}"
    )
    db.add(OrderStatusHistory(
        order_id=order.id, old_status=old_status,
        new_status=OrderStatus.completed, changed_by=current_user.id,
        note=history_note,
    ))

    log_action(db, AuditAction.order_completed, request, current_user.id, order.id,
               old_value={"status": old_status},
               new_value={"status": "completed", "final_amount": str(final_val),
                          "forced": (not amounts_match)})
    if not amounts_match:
        # Fark kabul edildi — ayrıca tutar değişikliği audit'i
        log_action(db, AuditAction.amount_changed, request, current_user.id, order.id,
                   old_value={"estimated_amount": str(estimated)},
                   new_value={"final_amount": str(final_val), "accepted_by_accountant": True})

    # Manager'a yalnızca 'tamamlandı' bilgisi gider (onay değil, bilgilendirme)
    from app.services.notification_service import notify_order_completed
    notify_order_completed(
        db, order.id,
        message=f'"{order.order_title}" ({order.order_number}) tamamlandı. Final tutar: {final_val:.2f}',
    )

    db.delete(temp)
    db.commit()
    db.refresh(order)
    return order


# ─────────────────────────────────────────
# KALDIRILAN ENDPOINT'LER (edit_granted akışı — artık yok)
# ─────────────────────────────────────────
# @router.post("/{order_id}/upload-edit-invoice-ocr", ...)   → SİLİNDİ
# @router.post("/{order_id}/submit-edit-invoice", ...)        → SİLİNDİ
# Uyuşmazlık çözümü artık tamamen frontend'de:
#   doğru faturayı yükle / force_complete=True ile devam et.
