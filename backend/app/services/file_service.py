"""
app/services/file_service.py

Merkezi yükleme servisi + retention (saklama süresi) yardımcıları.

store_upload:
  - MIME/uzantı doğrula (whitelist), boyut kontrol et, adı sanitize et
  - StorageBackend ile fiziksel kaydet (yerel disk veya R2)
  - FileAsset metadata satırı oluştur
  - DİSTRİBUTED ROLLBACK: DB tarafında hata olursa storage'a yazılan dosya
    geri silinir (orphan bırakılmaz)

purge_expired_files:
  - retention_until süresi geçmiş, henüz silinmemiş dosyaları
    soft (deleted_at) veya hard (storage + DB) siler. Cron'a bağlı değildir;
    ileride bir scheduled task çağırır.
"""
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.core.config import config_settings
from app.models.enums import UserRole
from app.models.file_models import FileAsset
from app.services.storage_backend import get_storage
from app.utils.storage import build_storage_path, UnsupportedFileTypeError


def sanitize_filename(name: Optional[str], fallback: str = "dosya") -> str:
    """Orijinal dosya adını güvenli metadata'ya çevirir (path/sembol enjeksiyonu yok)."""
    if not name:
        return fallback
    base = Path(name).name                       # klasör bileşenlerini at
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", base).strip("._")
    return safe[:255] or fallback


def store_upload(
    db: Session,
    *,
    file: UploadFile,
    role: UserRole,
    uploaded_by: int,
    order_id: Optional[int] = None,
    kind: Optional[str] = None,
    retention_until: Optional[datetime] = None,
    commit: bool = False,
) -> FileAsset:
    """
    Bir UploadFile'ı doğrular, storage'a yazar ve FileAsset kaydı oluşturur.
    Hata durumunda storage'a yazılan dosya geri alınır (orphan bırakmaz).
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="Dosya adı bulunamadı.")

    contents = file.file.read()
    max_bytes = config_settings.MAX_FILE_SIZE_MB * 1024 * 1024
    if len(contents) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"Dosya boyutu en fazla {config_settings.MAX_FILE_SIZE_MB} MB olabilir.",
        )

    try:
        storage_key, _ = build_storage_path(
            role=role,
            content_type=file.content_type,
            original_name=file.filename,
        )
    except UnsupportedFileTypeError:
        raise HTTPException(status_code=400, detail="Geçersiz dosya tipi. PDF veya resim yükleyin.")

    storage = get_storage()
    storage.save(storage_key, contents, file.content_type)

    asset = FileAsset(
        file_name=sanitize_filename(file.filename),
        storage_key=storage_key,
        content_type=file.content_type or "application/octet-stream",
        file_size=len(contents),
        kind=kind,
        order_id=order_id,
        uploaded_by=uploaded_by,
        retention_until=retention_until,
    )
    db.add(asset)
    try:
        db.flush()              # FK / unique hataları burada yakalanır
        if commit:
            db.commit()
            db.refresh(asset)
    except Exception:
        db.rollback()
        storage.delete(storage_key)   # DİSTRİBUTED ROLLBACK: storage'ı da geri al
        raise HTTPException(
            status_code=500,
            detail="Dosya kaydı oluşturulamadı, lütfen tekrar deneyin.",
        )
    return asset


def purge_expired_files(db: Session, *, mode: str = "soft", now: Optional[datetime] = None) -> int:
    """
    retention_until süresi geçmiş, henüz silinmemiş FileAsset'leri temizler.
      mode="soft" → deleted_at damgalanır (kayıt ve dosya kalır)
      mode="hard" → storage'dan dosya + DB satırı kalıcı silinir

    Dönüş: işlenen dosya sayısı. (Cron'a bağlı değildir; çağıran tetikler.)
    """
    if mode not in ("soft", "hard"):
        raise ValueError("mode yalnızca 'soft' veya 'hard' olabilir.")

    moment = now or datetime.now(timezone.utc)
    q = db.query(FileAsset).filter(
        FileAsset.retention_until.isnot(None),
        FileAsset.retention_until < moment,
        FileAsset.deleted_at.is_(None),
    )
    rows = q.all()
    storage = get_storage()
    count = 0
    for asset in rows:
        if mode == "soft":
            asset.deleted_at = moment
        else:
            storage.delete(asset.storage_key)
            db.delete(asset)
        count += 1
    db.commit()
    return count
