"""
JWT token üretimi ve doğrulaması (v2)

Değişiklik özeti:
  - ALLOWED_ROLES ve ACCOUNTING_ALLOWED_ROLES kaldırıldı (kafa karışıklığı yaratıyordu)
  - require_staff, require_buyer, require_accountant fonksiyonları eklendi
  - require_manager_or_accountant: manager ve accountant'ın ortak kullandığı endpointler için
  - require_active_order_access: buyer + manager (satın alma ve takip)
  - UserRole: 'accounting' → 'accountant' ile uyumlu hale getirildi

Yetki özeti:
  manager            → require_manager
  accountant         → require_accountant        (manager da erişebilir: require_manager_or_accountant)
  staff              → require_staff             (manager da erişebilir: require_manager_or_staff)
  buyer              → require_buyer             (manager da erişebilir: require_manager_or_buyer)

Kural: Manager her şeyi görebilir/takip edebilir ama bazı aksiyonlar
       yalnızca ilgili role aittir (örn: final fatura yükleme → sadece accountant).
"""
from datetime import datetime, timedelta, timezone
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.base import get_db
from app.models.models import User, UserRole

pwd_context  = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_PREFIX}/auth/login")


# ─────────────────────────────────────────
# YARDIMCI FONKSİYONLAR
# ─────────────────────────────────────────

def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(user_id: int, role: UserRole) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload = {"sub": str(user_id), "role": role.value, "exp": expire}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Geçersiz veya süresi dolmuş token",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ─────────────────────────────────────────
# KİMLİK DOĞRULAMA
# ─────────────────────────────────────────

def get_current_user(
    token: str = Depends(oauth2_scheme),
    db:    Session = Depends(get_db),
) -> User:
    payload = decode_token(token)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token geçersiz: sub bulunamadı")

    user = db.query(User).filter(User.id == int(user_id)).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Kullanıcı bulunamadı veya inaktif")

    # Zincirleme güvenlik: token içindeki rol ile DB'deki rol eşleşmeli
    token_role = payload.get("role")
    if token_role != user.role.value:
        raise HTTPException(
            status_code=401,
            detail="Yetkilendirme bilgileri güncel değil, lütfen tekrar giriş yapın"
        )

    return user


# ─────────────────────────────────────────
# ROL BAĞIMLI YETKİLENDİRME
# ─────────────────────────────────────────

def require_manager(current_user: User = Depends(get_current_user)) -> User:
    """Yalnızca manager."""
    if current_user.role != UserRole.manager:
        raise HTTPException(status_code=403, detail="Bu işlem için müdür yetkisi gerekli")
    return current_user


def require_accountant(current_user: User = Depends(get_current_user)) -> User:
    """
    Accountant veya Manager.
    """
    if current_user.role not in {UserRole.accountant, UserRole.manager}:
        raise HTTPException(status_code=403, detail="Bu işlem için muhasebe veya müdür yetkisi gerekli")
    return current_user

def require_staff(current_user: User = Depends(get_current_user)) -> User:
    """
    Staff veya Manager.
    """
    if current_user.role not in {UserRole.staff, UserRole.manager}:
        raise HTTPException(status_code=403, detail="Bu işlem için personel veya müdür yetkisi gerekli")
    return current_user

def require_buyer(current_user: User = Depends(get_current_user)) -> User:
    """
    Buyer veya Manager.
    """
    if current_user.role not in {UserRole.buyer, UserRole.manager}:
        raise HTTPException(status_code=403, detail="Bu işlem için satın alma veya müdür yetkisi gerekli")
    return current_user