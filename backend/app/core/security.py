"""
JWT token üretimi ve doğrulaması
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

# Ekstra roller eklendikçe bu seti güncellemek yeterli olacak, O(1) lookup sağlar
ALLOWED_ROLES = {UserRole.manager, UserRole.accounting}
ACCOUNTING_ALLOWED_ROLES = {UserRole.manager,UserRole.accounting}

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_PREFIX}/auth/login")

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

def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    payload = decode_token(token)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token geçersiz: sub bulunamadı")
        
    user = db.query(User).filter(User.id == int(user_id)).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Kullanıcı bulunamadı veya inaktif")
        
    # Token içindeki rol ile DB'deki rol eşleşiyor mu (Zincirleme Güvenlik Kontrolü)
    token_role = payload.get("role")
    if token_role != user.role.value:
        raise HTTPException(status_code=401, detail="Yetkilendirme bilgileri güncel değil, lütfen tekrar giriş yapın")
        
    return user

def require_manager(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != UserRole.manager:
        raise HTTPException(status_code=403, detail="Bu işlem için müdür yetkisi gerekli")
    return current_user

def require_accounting(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role not in ACCOUNTING_ALLOWED_ROLES: # Set O(1) lookup
        raise HTTPException(status_code=403, detail="Bu işlem için muhasebe veya müdür yetkisi gerekli")
    return current_user