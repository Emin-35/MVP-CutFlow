"""
app/utils/storage.py

Dosya isimlendirme güvenliği (v3).

ESKİ DAVRANIŞ: token UUID'si doğrudan dosya adı olarak kullanılıyordu
(örn. uploads/a1b2c3d4.pdf). Bu hem klasörü düzleştiriyor hem de
orijinal dosya adından gelen güvensiz karakter riski taşıyordu.

YENİ YAPI:
    uploads/<rol>/<yıl-ay>/<uuid>.<uzantı>
    Örnek: uploads/staff/2024-03/a1b2c3d4e5f6.pdf
           uploads/accountant/2024-03/9f8e7d6c5b4a.jpg

- <rol>     : yükleyen kullanıcının rolü (staff, accountant, ...)
- <yıl-ay>  : yükleme zamanına göre (UTC) klasör — listeleme/temizlik kolaylığı
- <uuid>    : çakışmasız, tahmin edilemez dosya adı (orijinal ad asla path'e girmez)
- <uzantı>  : yalnızca izinli content-type'lardan türetilir

Orijinal dosya adı yalnızca metadata olarak DB'de original_name alanında tutulur,
asla diskteki path'in parçası olmaz.
"""
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.models.enums import UserRole

# Proje kök dizinine göre upload taban klasörü.
# Gerekirse settings.UPLOAD_DIR ile override edilebilir.
UPLOAD_ROOT = Path("uploads")

# İzinli content-type → uzantı eşlemesi (whitelist)
_CONTENT_TYPE_EXT = {
    "application/pdf": "pdf",
    "image/jpeg":      "jpg",
    "image/jpg":       "jpg",
    "image/png":       "png",
    "image/webp":      "webp",
}

# Orijinal dosya adından uzantı düşmek için izinli uzantılar (fallback)
_ALLOWED_EXT = {"pdf", "jpg", "jpeg", "png", "webp"}


class UnsupportedFileTypeError(ValueError):
    """İzin verilmeyen dosya tipi yüklenmeye çalışıldı."""


def _resolve_extension(content_type: Optional[str], original_name: Optional[str]) -> str:
    """
    Önce content-type whitelist'inden, olmazsa orijinal ad uzantısından uzantı belirler.
    Hiçbiri uymuyorsa hata fırlatır (sessizce .bin yazmıyoruz).
    """
    if content_type:
        ext = _CONTENT_TYPE_EXT.get(content_type.lower().strip())
        if ext:
            return ext

    if original_name and "." in original_name:
        candidate = original_name.rsplit(".", 1)[-1].lower().strip()
        if candidate in _ALLOWED_EXT:
            return "jpg" if candidate == "jpeg" else candidate

    raise UnsupportedFileTypeError(
        f"Desteklenmeyen dosya tipi: content_type={content_type!r}, name={original_name!r}"
    )


def build_storage_path(
    role: UserRole,
    content_type: Optional[str],
    original_name: Optional[str] = None,
    *,
    now: Optional[datetime] = None,
) -> tuple[str, str]:
    """
    Yeni bir güvenli depolama yolu üretir.

    Dönüş: (relative_path, file_uuid)
      relative_path → DB'ye yazılacak göreli yol (uploads/staff/2024-03/<uuid>.pdf)
      file_uuid     → üretilen uuid (token ile ilişkilendirmek isterseniz)

    Klasörü fiziksel olarak oluşturmaz; ensure_dir() ile yazmadan önce çağırın.
    """
    ext = _resolve_extension(content_type, original_name)

    role_value = role.value if isinstance(role, UserRole) else str(role)
    moment = now or datetime.now(timezone.utc)
    year_month = moment.strftime("%Y-%m")

    file_uuid = uuid.uuid4().hex
    rel_path = UPLOAD_ROOT / role_value / year_month / f"{file_uuid}.{ext}"
    return str(rel_path), file_uuid


def ensure_dir(relative_path: str) -> Path:
    """
    Verilen göreli dosya yolunun klasörünü oluşturur ve mutlak Path döner.
    Dosyayı yazmadan hemen önce çağrılmalıdır.
    """
    abs_path = Path(relative_path).resolve()
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    return abs_path
