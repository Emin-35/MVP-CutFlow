"""
Order OCR Helper Endpoints (v4 — yükleme ve tarama AYRILDI)

AKIŞ
────
  Adım 1  upload-first-invoice-file / {id}/upload-final-invoice-file
          → TEK dosya yükler, file_token döner. OCR çalışmaz.
            Çok dosyalı şemada (2-3 fotoğraf) endpoint dosya başına tekrar çağrılır.
  Adım 1.5  scan-files  ("Tara" butonu)
          → Token listesi alır, HER dosyayı OCR'lar, dosya başına sonuç +
            birleşik (merged) ön-doldurma verisi döner. Bilgi birden fazla
            resme yayılmışsa hepsinden toplanır.
  Adım 2  submit-first-invoice / {id}/submit-final-invoice
          → invoice_token (birincil dosya) + extra_file_tokens (kalanlar).
            Birincil dosya Invoice olur, ekler order_files'a bağlanır.

Diğer kurallar (v3'ten devam):
  - Final fatura tutar uyuşmazlığı: 409 + karşılaştırma detayı, karar frontend'de
    ("doğru faturayı yükle" / force_complete=True ile devam). Manager onayı YOK.
  - Sipariş her zaman pending_approval ile açılır → buyer satın alır.
  - Dosya yolu: app.utils.storage → uploads/<rol>/<yıl-ay>/<uuid>.<uzantı>
  - submit_final_invoice SADECE accountant (manager hariç).
"""
from datetime import datetime, timezone, timedelta
from pathlib import Path

from fastapi import APIRouter, UploadFile, Depends, HTTPException, Request, File
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.security import get_current_user, require_staff, require_accountant
from app.core.rate_limit import limiter, user_key_func
from app.core.config import config_settings

from app.db.base import get_db
from app.models.models import (
    AuditAction, ExtraMetalRequest, ExtraMetalStatus, Invoice, InvoiceType,
    MetalRequest, NotifType, Order, OrderFile,
    OrderStatus, OrderStatusHistory, TempInvoiceFile, User, UserRole,
)
from app.models.file_models import FileAsset
from app.schemas.schemas import (
    FinalInvoiceSubmit, InvoiceCompareOut,
    InvoiceFileUploadOut, InvoiceScanRequest, InvoiceFileScanResult, InvoiceScanOut,
    OrderCreate, OrderStatusOut,
)
from app.schemas.metal_schemas import describe_manual_totals
from app.services.audit import log_action
from app.services.notification_service import (
    notify_actor_and_managers, notify_new_order, notify_user,
)
from app.services.ocr_service import get_ocr, OCRError
from app.services.storage_backend import get_storage
from app.utils.order_number import generate_order_number
from app.utils.storage import build_storage_path, UnsupportedFileTypeError


router = APIRouter(prefix="/order-ocr", tags=["ocr-helper"])

# Geçici fatura dosyaları için TTL
TEMP_INVOICE_TTL_HOURS = 1

# Sipariş başına en fazla dosya (birincil şema + ekler). Frontend de aynı limiti
# uygular (NewOrderPage); burası otoritedir.
MAX_ORDER_FILES = 5


# ─────────────────────────────────────────
# ORTAK: dosya kaydet + temp kayıt oluştur
# ─────────────────────────────────────────

def _save_temp_file(db: Session, file: UploadFile, current_user: User) -> TempInvoiceFile:
    """
    TEK dosyayı güvenli yola yazar ve TempInvoiceFile kaydı oluşturur.
    OCR BURADA ÇALIŞMAZ — tarama ayrı bir adımdır (POST /order-ocr/scan-files).
    Frontend birden fazla dosya için bu endpoint'i dosya başına ayrı çağırır.

    DISTRIBUTED ROLLBACK: DB kaydı başarısız olursa diske yazılan dosya
    geri silinir — sistemde orphan dosya kalmaz.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="Dosya adı bulunamadı.")

    contents = file.file.read()
    max_bytes = config_settings.MAX_FILE_SIZE_MB * 1024 * 1024   # MB → byte
    if len(contents) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"Dosya boyutu en fazla {config_settings.MAX_FILE_SIZE_MB} MB olabilir.",
        )

    try:
        storage_key, token = build_storage_path(
            role=current_user.role,
            content_type=file.content_type,
            original_name=file.filename,
        )
    except UnsupportedFileTypeError:
        raise HTTPException(status_code=400, detail="Geçersiz dosya tipi. PDF veya resim yükleyin.")

    # Fiziksel kayıt StorageBackend üzerinden (yerel disk veya R2 — config'e göre)
    storage = get_storage()
    storage.save(storage_key, contents, file.content_type)

    temp = TempInvoiceFile(
        token=token,
        file_path=storage_key,                    # storage anahtarı (R2 object key / yerel yol)
        file_type=file.content_type or "application/octet-stream",
        original_name=Path(file.filename).name,   # sadece metadata
        ocr_raw=None,                             # tarama scan-files ile yapılır
        uploaded_by=current_user.id,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=TEMP_INVOICE_TTL_HOURS),
    )
    db.add(temp)
    try:
        db.commit()
    except Exception:
        db.rollback()
        storage.delete(storage_key)   # DİSTRİBUTED ROLLBACK: storage'a yazılanı geri al
        raise HTTPException(
            status_code=500,
            detail="Dosya kaydedilirken bir hata oluştu, lütfen tekrar deneyin.",
        )
    return temp


def _run_ocr(temp: TempInvoiceFile) -> dict:
    """
    Tek dosya üzerinde OCR çalıştırır (sağlayıcı seçimi: OCR_PROVIDER).
    Dosya StorageBackend'den okunur — local diskte de R2'de de aynı şekilde çalışır.
    """
    storage = get_storage()
    try:
        contents = b"".join(storage.open_stream(temp.file_path))
    except FileNotFoundError:
        raise HTTPException(
            status_code=400,
            detail=f"'{temp.original_name}' dosyası depoda bulunamadı. Lütfen tekrar yükleyin.",
        )

    try:
        ocr = get_ocr().scan(contents, temp.file_type, temp.original_name)
    except OCRError as exc:
        # Yapılandırma eksik / Vision çağrısı başarısız — kullanıcıya net mesaj
        raise HTTPException(status_code=502, detail=f"OCR taraması başarısız: {exc}")

    ocr["source_file"] = temp.original_name
    return ocr


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
# STAFF — Adım 1: ŞEMA/FATURA DOSYASI YÜKLE (dosya başına bir istek)
# ─────────────────────────────────────────

@router.post("/upload-first-invoice-file", response_model=InvoiceFileUploadOut)
@limiter.limit("20/minute", key_func=user_key_func)   # kullanıcı başına 20/dk (çoklu dosya için)
def upload_first_invoice_file(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    """
    TEK dosya yükler (PDF veya fotoğraf), file_token döner. OCR burada ÇALIŞMAZ.

    Akış (çok dosyalı şema için):
      1. Kullanıcı her dosyayı ayrı ayrı bu endpoint'e yükler (1-3 dosya)
      2. "Tara" butonuyla POST /order-ocr/scan-files çağrılır (tüm token'lar) →
         birleşik OCR verisi form sütunlarına ön-doldurulur
      3. submit-first-invoice'a invoice_token (birincil dosya) +
         extra_file_tokens (kalanlar) gönderilir
    """
    temp = _save_temp_file(db, file, current_user)
    return InvoiceFileUploadOut(file_token=temp.token, original_name=temp.original_name)


# ─────────────────────────────────────────
# ORTAK — Adım 1.5: YÜKLENEN DOSYALARI TARA (OCR)
# ─────────────────────────────────────────

@router.post("/scan-files", response_model=InvoiceScanOut)
@limiter.limit("10/minute", key_func=user_key_func)
def scan_files(
    payload: InvoiceScanRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Yüklenmiş dosyaları (token listesi) OCR ile tarar.

    - Her dosya AYRI taranır → results[] içinde dosya başına ham çıktı
    - merged: tüm dosyalardan birleştirilmiş ön-doldurma verisi —
      bilgiler 2-3 resme yayılmışsa hepsinden toplanır, frontend form
      sütunlarını bununla doldurur (kullanıcı düzenleyebilir)
    - OCR sonuçları temp kayda da yazılır; submit'te Invoice.ocr_raw'a taşınır
    - Yalnızca kendi yüklediğin, süresi dolmamış token'lar taranabilir
    """
    tokens = list(dict.fromkeys(payload.file_tokens))   # mükerrer token ayıkla

    results: list[InvoiceFileScanResult] = []
    texts: list[str] = []
    merged_amount = None
    merged_date = None
    any_mock = False

    for token in tokens:
        temp = _get_valid_temp(db, token, current_user.id)
        ocr = _run_ocr(temp)
        temp.ocr_raw = ocr   # submit'te Invoice.ocr_raw'a taşınır
        results.append(InvoiceFileScanResult(file_token=token, ocr_result=ocr))

        if ocr.get("detected_text"):
            texts.append(ocr["detected_text"])
        if merged_amount is None and ocr.get("parsed_amount") is not None:
            merged_amount = ocr["parsed_amount"]   # ilk tutar bulunan dosya kazanır
        if merged_date is None and ocr.get("date"):
            merged_date = ocr["date"]              # ilk tarih bulunan dosya kazanır
        any_mock = any_mock or bool(ocr.get("_mock"))

    db.commit()

    merged = {
        "detected_text": "\n".join(texts),
        "parsed_amount": merged_amount,
        "date": merged_date or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "scanned_files": len(results),
        "_mock": any_mock,   # tek dosya bile mock taranmışsa frontend uyarabilsin
    }
    return InvoiceScanOut(results=results, merged=merged)


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

    # ── Dosya sayısı ve TOPLAM boyut limiti (frontend de uygular; burası otorite) ──
    extra_tokens = [t for t in dict.fromkeys(payload.extra_file_tokens) if t != payload.invoice_token]
    if 1 + len(extra_tokens) > MAX_ORDER_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"Sipariş başına en fazla {MAX_ORDER_FILES} dosya yüklenebilir.",
        )
    extra_temps = [_get_valid_temp(db, t, current_user.id) for t in extra_tokens]

    storage = get_storage()
    try:
        total_bytes = sum(storage.size(t.file_path) for t in [temp, *extra_temps])
    except FileNotFoundError:
        raise HTTPException(
            status_code=400,
            detail="Yüklenen dosyalardan biri depoda bulunamadı. Lütfen dosyaları tekrar yükleyin.",
        )
    max_total_bytes = config_settings.MAX_FILE_SIZE_MB * 1024 * 1024
    if total_bytes > max_total_bytes:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Dosyaların TOPLAM boyutu en fazla {config_settings.MAX_FILE_SIZE_MB} MB olabilir "
                f"(şu an {total_bytes / (1024 * 1024):.1f} MB)."
            ),
        )

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
        # Üretim hedefi: staff girdiyse o; girilmediyse None (accountant
        # target_count_updated olayıyla belirler). Metal adedi toplamı DEĞİL.
        total_count=payload.total_count,
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
    # Merkezi dosya kaydı (retention/lifecycle için)
    db.add(FileAsset(
        storage_key=temp.file_path, file_name=temp.original_name,
        content_type=temp.file_type, kind="order_schema",
        order_id=order.id, uploaded_by=current_user.id,
    ))

    # Ek fotoğraflar (varsa) — yalnızca order_files'a bağlanır, Invoice oluşmaz
    # (extra_temps yukarıda, limit kontrolü sırasında doğrulanıp çekildi)
    for extra_temp in extra_temps:
        db.add(OrderFile(
            order_id=order.id,
            file_path=extra_temp.file_path,
            file_type=extra_temp.file_type,
            original_name=extra_temp.original_name,
            uploaded_by=current_user.id,
        ))
        db.add(FileAsset(
            storage_key=extra_temp.file_path, file_name=extra_temp.original_name,
            content_type=extra_temp.file_type, kind="order_schema_extra",
            order_id=order.id, uploaded_by=current_user.id,
        ))
        db.delete(extra_temp)

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

    # Elle değiştirilen otomatik ağırlıklar → audit + tüm bildirimlere eklenir
    manual_totals = describe_manual_totals(payload.metal_items)
    override_note = (
        f" DİKKAT: {len(manual_totals)} kalemde otomatik ağırlık elle değiştirildi — "
        + "; ".join(manual_totals)
    ) if manual_totals else ""

    log_action(
        db, AuditAction.order_created, request, current_user.id, order.id,
        new_value={
            "order_number":     order_number,
            "order_title":      payload.order_title,
            "status":           initial_status,
            "estimated_amount": str(payload.estimated_amount),
            "metal_items":      len(payload.metal_items),
            "manual_totals":    manual_totals or None,
        },
    )

    # Buyer(lar)a bildirim — eski send_order_notification_to_managers KALDIRILDI
    notify_new_order(
        db, order.id,
        message=f'Yeni sipariş: "{order.order_title}" ({order_number}). Satın alma bekleniyor.{override_note}',
    )

    # Aktör (staff) onayı + müdür(ler)e bilgilendirme
    notify_actor_and_managers(
        db,
        actor_id=current_user.id,
        notif_type=NotifType.order_created,
        actor_message=f'"{order.order_title}" ({order_number}) siparişiniz oluşturuldu. Satın alma onayı bekleniyor.{override_note}',
        manager_message=f'{current_user.username} yeni sipariş oluşturdu: "{order.order_title}" ({order_number}).{override_note}',
        order_id=order.id,
    )

    db.delete(temp)
    db.commit()
    db.refresh(order)
    return order


# ─────────────────────────────────────────
# ACCOUNTANT — Adım 3: FINAL FATURA YÜKLE + OCR
# ─────────────────────────────────────────

@router.post("/{order_id}/upload-final-invoice-file", response_model=InvoiceFileUploadOut)
@limiter.limit("20/minute", key_func=user_key_func)
def upload_final_invoice_file(
    request: Request,
    order_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_accountant),
):
    """
    Final fatura dosyasını yükler (TEK dosya — genelde tek PDF/resim yeterli).
    OCR için scan-files, tamamlama için submit-final-invoice çağrılır.
    Birden fazla dosya gerekirse bu endpoint dosya başına tekrar çağrılır;
    ek token'lar submit'te extra_file_tokens olarak gönderilir.
    """
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order or order.status != OrderStatus.active:
        raise HTTPException(status_code=400, detail="Sadece aktif siparişlere final faturası yüklenebilir.")
    temp = _save_temp_file(db, file, current_user)
    return InvoiceFileUploadOut(file_token=temp.token, original_name=temp.original_name)


# ─────────────────────────────────────────
# ACCOUNTANT — Adım 4: FINAL FATURA SUBMIT + TUTAR KARŞILAŞTIRMA
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

    # Beklenen tutar = ilk (base) tutar + onaylı/satın alınmış EKSTRA metallerin fiyatı.
    # Final fatura ekstra metalleri de içerdiği için karşılaştırma buna göre yapılır.
    extras_total = db.query(
        func.coalesce(func.sum(ExtraMetalRequest.estimated_amount), 0)
    ).filter(
        ExtraMetalRequest.order_id == order.id,
        ExtraMetalRequest.status.in_([ExtraMetalStatus.approved, ExtraMetalStatus.purchased]),
    ).scalar()

    expected_amount = order.estimated_amount + extras_total   # Decimal
    estimated = float(expected_amount)
    final_val = float(payload.final_amount)
    amounts_match = abs(estimated - final_val) <= 0.01

    # ── UYUŞMAZLIK + force yok → kaydetme, frontend'e karar bırak ──
    if not amounts_match and not payload.force_complete:
        compare = InvoiceCompareOut(
            order_id=order.id,
            initial_amount=expected_amount,   # base + onaylı ekstralar
            final_amount=payload.final_amount,
            match=False,
            difference=payload.final_amount - expected_amount,
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
    db.add(FileAsset(
        storage_key=temp.file_path, file_name=temp.original_name,
        content_type=temp.file_type, kind="invoice_final",
        order_id=order.id, uploaded_by=current_user.id,
    ))

    # Ek fotoğraflar (varsa) — order_files'a bağlanır
    # (409 mismatch yolunda buraya gelinmez; token'lar geçerli kalır, retry'da işlenir)
    extra_tokens = [t for t in dict.fromkeys(payload.extra_file_tokens) if t != payload.invoice_token]
    for extra_token in extra_tokens:
        extra_temp = _get_valid_temp(db, extra_token, current_user.id)
        db.add(OrderFile(
            order_id=order.id,
            file_path=extra_temp.file_path,
            file_type=extra_temp.file_type,
            original_name=extra_temp.original_name,
            uploaded_by=current_user.id,
        ))
        db.add(FileAsset(
            storage_key=extra_temp.file_path, file_name=extra_temp.original_name,
            content_type=extra_temp.file_type, kind="invoice_final_extra",
            order_id=order.id, uploaded_by=current_user.id,
        ))
        db.delete(extra_temp)

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
    # NOT: notify_order_completed zaten müdür(ler)e gider; burada yalnızca
    #      aktöre (accountant) onay bildirimi ekliyoruz, müdürü mükerrer bildirmemek için.
    from app.services.notification_service import notify_order_completed
    notify_order_completed(
        db, order.id,
        message=f'"{order.order_title}" ({order.order_number}) tamamlandı. Final tutar: {final_val:.2f}',
    )
    notify_user(
        db,
        recipient_id=current_user.id,
        notif_type=NotifType.order_completed,
        message=f'"{order.order_title}" ({order.order_number}) final faturasını yüklediniz, sipariş tamamlandı. Final tutar: {final_val:.2f}',
        order_id=order.id,
    )

    db.delete(temp)
    db.commit()
    db.refresh(order)
    return order


# ──────────────────────────────────────────────────────────────────
# PARAMETRE SIRASI UYARISI (FastAPI)
# ──────────────────────────────────────────────────────────────────
# Python'da varsayılansız parametre (order_id: int), varsayılanlı olandan
# (file: UploadFile = File(...)) ÖNCE gelmeli. `request: Request`
# varsayılansızdır, bu yüzden onu da diğer varsayılansızların yanına,
# en başa koymak en güvenlisidir. Yukarıdaki örneklerde request en başta.