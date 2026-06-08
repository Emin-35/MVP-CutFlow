"""
Auth Endpoints: /login, /me
"""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.models.models import AuditAction, User
from app.core.security import hash_password, verify_password, create_access_token, get_current_user
from app.schemas.schemas import ChangeSelfPasswordRequest, Token, UserOut
from app.services.audit import log_action

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=Token)
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    # FastAPI OAuth2Form sayesinde veriler doğrudan form_data.username ve form_data.password olarak gelir
    user = db.query(User).filter(User.username == form_data.username).first()
    
    if not user or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Kullanıcı adı veya şifre hatalı",
        )
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Hesap inaktif")

    token = create_access_token(user.id, user.role)
    return Token(access_token=token)


@router.get("/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)):
    return current_user


@router.patch("/me/change-password")
def change_my_password(
    payload: ChangeSelfPasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),  # giriş yapmış herkes
):
    # Eski şifreyi doğrula
    if not verify_password(payload.old_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="Mevcut şifre hatalı")

    # Aynı şifreyi tekrar koymasın
    if verify_password(payload.new_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="Yeni şifre eski şifreyle aynı olamaz")

    current_user.password_hash = hash_password(payload.new_password)
    log_action(db, AuditAction.user_updated, request, current_user.id,
               old_value={"user_id": current_user.id},
               new_value={"action": "password_changed_by_self"})
    db.commit()
    return {"message": "Şifre güncellendi"}