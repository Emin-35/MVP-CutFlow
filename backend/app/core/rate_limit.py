"""
app/core/rate_limit.py

SlowAPI tabanlı rate limiting kurulumu.

İki key fonksiyonu sağlar:
  - ip_key_func    → IP bazlı (login gibi kimlik doğrulanmamış endpoint'ler)
  - user_key_func  → JWT'deki kullanıcı id'si bazlı (korumalı endpoint'ler)

Backend: in-memory (varsayılan). 5-10 kullanıcı + tek process için yeterli.
Birden fazla uvicorn worker'a veya çok sunucuya geçersen storage_uri'yi
Redis'e çevir (örn. "redis://localhost:6379"); sayaçlar process'ler arası paylaşılır.

NOT: SlowAPI'nin çalışması için limit uygulanan endpoint fonksiyonunun
     imzasında `request: Request` (ve gerekiyorsa `response: Response`)
     açıkça bulunmalıdır. Senin route'larında zaten request var.
"""
from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import settings
from app.core.security import decode_token


# ─────────────────────────────────────────
# KEY FONKSİYONLARI
# ─────────────────────────────────────────

def ip_key_func(request: Request) -> str:
    """
    IP bazlı anahtar. Reverse proxy (Nginx/Cloudflare) arkasında
    x-forwarded-for / x-real-ip başlıklarını dikkate alır.
    audit.get_client_ip ile aynı mantık — tek yerden türetmek istersen
    oradan da import edebilirsin.
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    xri = request.headers.get("x-real-ip")
    if xri:
        return xri
    return get_remote_address(request)


def user_key_func(request: Request) -> str:
    """
    Kullanıcı bazlı anahtar. Authorization: Bearer <token> başlığından
    kullanıcı id'sini çözer. Token yok/geçersizse IP'ye düşer
    (böylece anonim istekler de limitlenir, sızıntı olmaz).

    Avantaj: aynı IP arkasındaki farklı kullanıcılar birbirinin limitini
    tüketmez; tek kullanıcı çok IP'den de toplam limitle sınırlı kalır.
    """
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        try:
            payload = decode_token(token)
            sub = payload.get("sub")
            if sub:
                return f"user:{sub}"
        except Exception:
            # Geçersiz/expired token → IP'ye düş
            pass
    return f"ip:{ip_key_func(request)}"


# ─────────────────────────────────────────
# LIMITER ÖRNEKLERİ
# ─────────────────────────────────────────
# Varsayılan key IP'dir. Endpoint bazında @limiter.limit(..., key_func=...)
# ile kullanıcı bazlıya geçebilirsin. İki ayrı limiter yerine TEK limiter
# kullanıp key_func'ı decorator'da override etmek en temiz yöntem.

# storage_uri verilmezse SlowAPI in-memory kullanır.
_storage_uri = getattr(settings, "RATE_LIMIT_STORAGE_URI", None)  # ileride Redis için

limiter = Limiter(
    key_func=ip_key_func,                 # varsayılan: IP bazlı
    storage_uri=_storage_uri,             # None → in-memory
    default_limits=[],                    # global varsayılan yok; endpoint bazlı koyacağız
    # headers_enabled=True yaparsan X-RateLimit-* başlıkları yanıta eklenir,
    # ANCAK o zaman limit uygulanan her endpoint'in imzasında `response: Response`
    # de bulunmak ZORUNDA (yoksa SlowAPI hata verir). Sürtünmeyi azaltmak için
    # varsayılan kapalı; istersen aç ve endpoint'lere response parametresi ekle.
    headers_enabled=False,
    enabled=getattr(settings, "RATE_LIMIT_ENABLED", True),  # testte kapatmak için
)