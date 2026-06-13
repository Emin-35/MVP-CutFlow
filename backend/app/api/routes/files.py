"""
Dosya Erişim Endpoint'leri — JWT korumalı backend proxy (IDOR koruması)

TASARIM
───────
- Dosyalar ASLA statik mount ile servis edilmez; her indirme bu router'dan geçer.
- Her istekte: JWT doğrulanır (get_current_user) → dosya kaydı DB'den bulunur
  → sahiplik/yetki kontrolü MERKEZİ _authorize_file_access ile yapılır
  → ancak ondan sonra disk okunur.
- Yetkisiz denemeler (silinmiş siparişin dosyası, olmayan id taraması/probing)
  audit log'a 'unauthorized_file_access' olarak IP ile kaydedilir.
- Path containment: DB'deki yol uploads/ kökü dışına çözümleniyorsa
  (path traversal) erişim reddedilir.

YETKİ POLİTİKASI (6-8 kişilik tek firma)
────────────────────────────────────────
- Tüm aktif kullanıcılar, silinmemiş siparişlerin dosyalarını görebilir
  (sipariş detayını da zaten herkes görüyor — list-orders/get-order).
- Soft-delete edilmiş siparişin dosyaları YALNIZCA manager'a açıktır.
- İleride rol bazlı daraltma gerekirse tek nokta: _authorize_file_access.
"""
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.base import get_db
from app.models.models import (
    AuditAction, Invoice, InvoiceType, Order, OrderFile, OrderStatus, User, UserRole,
)
from app.services.audit import log_action
from app.services.storage_backend import get_storage

router = APIRouter(prefix="/files", tags=["files"])


def _sanitize_filename(name: str | None, fallback: str) -> str:
    """Content-Disposition için güvenli dosya adı (header injection/garip karakter yok)."""
    if not name:
        return fallback
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", name).strip("._")
    return safe[:100] or fallback


def _deny(
    db: Session,
    request: Request,
    current_user: User,
    *,
    file_kind: str,
    file_id: int,
    order_id: int | None,
    reason: str,
    status_code: int,
    detail: str,
) -> None:
    """
    Yetkisiz/şüpheli dosya erişim denemesini audit'e yazar ve isteği keser.
    Audit kaydı, isteği reddetmeden ÖNCE commit edilir (rollback'e kurban gitmesin).
    """
    log_action(db, AuditAction.unauthorized_file_access, request, current_user.id, order_id,
               new_value={"file_kind": file_kind, "file_id": file_id, "reason": reason})
    db.commit()
    raise HTTPException(status_code=status_code, detail=detail)


def _authorize_file_access(
    db: Session,
    request: Request,
    current_user: User,
    order: Order | None,
    *,
    file_kind: str,
    file_id: int,
) -> None:
    """
    MERKEZİ YETKİ KONTROLÜ — tüm dosya servis yolları buradan geçer.
    Kural değişiklikleri yalnızca burada yapılır.
    """
    if order is None:
        # Dosya kaydı bir siparişe bağlanamıyor → servis edilmez
        _deny(db, request, current_user,
              file_kind=file_kind, file_id=file_id, order_id=None,
              reason="order_missing", status_code=404, detail="Dosya bulunamadı.")

    if order.status == OrderStatus.deleted and current_user.role != UserRole.manager:
        _deny(db, request, current_user,
              file_kind=file_kind, file_id=file_id, order_id=order.id,
              reason="deleted_order_non_manager", status_code=403,
              detail="Bu dosyaya erişim yetkiniz yok.")


def _serve_file(
    db: Session,
    request: Request,
    current_user: User,
    *,
    file_kind: str,
    file_id: int,
    order: Order | None,
    file_path: str,
    file_type: str | None,
    original_name: str | None,
    download: bool,
) -> StreamingResponse:
    """
    Yetki kontrolü + StorageBackend üzerinden servis (yerel disk veya R2).
    file_path = storage_key. Path traversal koruması LocalStorageBackend içinde;
    R2'de anahtarlar dosya sistemi yolu değildir.
    """
    _authorize_file_access(db, request, current_user, order,
                           file_kind=file_kind, file_id=file_id)

    storage = get_storage()
    if not storage.exists(file_path):
        raise HTTPException(status_code=404, detail="Dosya sunucuda bulunamadı.")

    disposition = "attachment" if download else "inline"
    fallback = file_path.rsplit("/", 1)[-1]
    filename = _sanitize_filename(original_name, fallback)
    return StreamingResponse(
        storage.open_stream(file_path),
        media_type=file_type or "application/octet-stream",
        headers={"Content-Disposition": f'{disposition}; filename="{filename}"'},
    )


# ─────────────────────────────────────────
# SİPARİŞ DOSYASI İNDİR / GÖRÜNTÜLE
# ─────────────────────────────────────────

@router.get("/order-file/{file_id}")
def get_order_file(
    file_id: int,
    request: Request,
    download: bool = False,   # true → indirme; false → tarayıcıda görüntüleme
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Sipariş şema/ek dosyasını servis eder (order_files tablosu)."""
    record = db.query(OrderFile).filter(OrderFile.id == file_id).first()
    if record is None:
        # Olmayan id taraması (probing) da audit'e düşer
        _deny(db, request, current_user,
              file_kind="order_file", file_id=file_id, order_id=None,
              reason="not_found", status_code=404, detail="Dosya bulunamadı.")

    order = db.query(Order).filter(Order.id == record.order_id).first()
    return _serve_file(
        db, request, current_user,
        file_kind="order_file", file_id=file_id, order=order,
        file_path=record.file_path, file_type=record.file_type,
        original_name=record.original_name, download=download,
    )


# ─────────────────────────────────────────
# FATURA DOSYASI İNDİR / GÖRÜNTÜLE
# ─────────────────────────────────────────

@router.get("/invoice/{invoice_id}")
def get_invoice_file(
    invoice_id: int,
    request: Request,
    download: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    YALNIZCA FINAL fatura dosyasını servis eder (en son yüklenen tahsilat faturası).
    Initial (sipariş şeması) dosyası bu uçtan SERVİS EDİLMEZ; o, order-file
    üzerinden erişilir (aynı fiziksel dosya zaten order_files'ta bağlı).
    """
    record = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if record is None:
        _deny(db, request, current_user,
              file_kind="invoice", file_id=invoice_id, order_id=None,
              reason="not_found", status_code=404, detail="Dosya bulunamadı.")

    if record.type != InvoiceType.final:
        # initial fatura → şema dosyasıdır; order-file'dan erişilir
        raise HTTPException(
            status_code=404,
            detail="Bu uç yalnızca final faturayı servis eder. Şema dosyası için order-file kullanın.",
        )

    if not record.file_path:
        raise HTTPException(status_code=404, detail="Bu faturaya bağlı dosya yok.")

    order = db.query(Order).filter(Order.id == record.order_id).first()
    return _serve_file(
        db, request, current_user,
        file_kind="invoice", file_id=invoice_id, order=order,
        file_path=record.file_path, file_type=record.file_type,
        original_name=record.original_name, download=download,
    )
