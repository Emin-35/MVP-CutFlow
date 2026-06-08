"""
Audit Log Servisi — her kritik işlemde çağrılır (IP Yakalama Entegre Edilmiş Güvenli Versiyon)
"""
from decimal import Decimal
from typing import Any, Dict, Optional
from sqlalchemy.orm import Session
from fastapi import Request

from app.models.models import AuditLog, AuditAction


def _serialize(obj: Any) -> Any:
    """Decimal ve diğer JSON serialize edilemeyen tipleri dönüştür"""
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_serialize(i) for i in obj]
    return obj


def get_client_ip(request: Request) -> str:
    """
    İstemcinin gerçek IP adresini döndürür.
    Önündeki Nginx/Cloudflare gibi tersine proxy (Reverse Proxy) yapılarını destekler.
    """
    if not request:
        return "unknown"
        
    # 1. Standart Proxy başlığı (Nginx/Haproxy vb. yapılandırıldıysa)
    x_forwarded_for = request.headers.get("x-forwarded-for")
    if x_forwarded_for:
        # İlk sıradaki IP, gerçek kullanıcının IP'sidir.
        return x_forwarded_for.split(",")[0].strip()
    
    # 2. Alternatif yaygın proxy başlığı
    x_real_ip = request.headers.get("x-real-ip")
    if x_real_ip:
        return x_real_ip

    # 3. Eğer önünde hiçbir proxy yoksa (Localhost / Geliştirme ortamı)
    if request.client and request.client.host:
        return request.client.host

    return "unknown"


def log_action(
    db: Session,
    action: AuditAction,
    request: Optional[Request] = None,  # ip_address yerine doğrudan Request nesnesini alıyoruz
    user_id: Optional[int] = None,
    order_id: Optional[int] = None,
    old_value: Optional[Dict[str, Any]] = None,
    new_value: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Kritik sistem işlemlerini veritabanına loglar.
    IP adresi, gelen Request nesnesinden otomatik olarak çözümlenir.
    """
    # IP adresini request nesnesini kullanarak otomatik tespit ediyoruz
    extracted_ip = get_client_ip(request) if request else "unknown"

    entry = AuditLog(
        user_id=user_id,
        order_id=order_id,
        action=action,
        old_value=_serialize(old_value) if old_value else None,
        new_value=_serialize(new_value) if new_value else None,
        ip_address=extracted_ip,  # Otomatik çözülen güvenli IP'yi atıyoruz
    )
    db.add(entry)
    db.flush()  # Transaction'ı bitirmez ama ID'nin oluşmasını sağlar