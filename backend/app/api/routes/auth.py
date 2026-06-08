"""
Auth Endpoints: /login, /me
"""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.db.base import get_db
from app.models.models import AuditAction, User
from app.core.security import hash_password, verify_password, create_access_token, get_current_user
from app.schemas.schemas import ChangeSelfPasswordRequest, Token, UserOut, UserUpdate
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


@router.patch("/me/change-user-password")
def change_my_password(
    payload: ChangeSelfPasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),  # giriş yapmış herkes
):
    
    if not payload.old_password:
        raise HTTPException(status_code=400, detail="Mevcut şifre zorunlu")

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


@router.patch("/me/change-user-settings")
def change_my_settings(
    payload: UserUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Değişiklik takibi için eski değerleri saklayalım
    old_value = {}
    new_value = {}
    
    # 1. E-posta Değişiklik Kontrolü
    if payload.email and payload.email != current_user.email:
        # Veritabanında bu e-posta adresiyle başka biri var mı?
        email_exists = db.query(User).filter(User.email == payload.email).first()
        if email_exists:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Bu e-posta adresi zaten başka bir kullanıcı tarafından kullanılıyor."
            )
        
        old_value["email"] = current_user.email
        current_user.email = payload.email
        new_value["email"] = current_user.email

    # 2. İsim Değişiklik Kontrolü
    if payload.username and payload.username != current_user.username:
        old_value["username"] = current_user.username
        current_user.username = payload.username
        new_value["username"] = current_user.username

    # Eğer hiçbir alan değiştirilmediyse veritabanını ve log mekanizmasını boşuna yormayalım
    if not old_value:
        return {"message": "Herhangi bir değişiklik yapılmadı"}

    try:
        # Veritabanına kaydetmeyi dene
        db.commit()
        db.refresh(current_user)
        
    except IntegrityError:
        # Beklenmedik bir veritabanı kısıtlaması (constraint) hatası oluşursa
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Veritabanı güncellenirken bir çakışma oluştu. Lütfen bilgileri kontrol edin."
        )
    except Exception as e:
        # Genel sistem hataları için guard-rail
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Sunucu taraflı bir hata oluştu. Ayarlar güncellenemedi."
        )

    # 3. Loglama (Sadece commit başarılı olduysa çalışır)
    try:
        log_action(
            db, 
            AuditAction.user_updated, 
            request, 
            current_user.id,
            old_value=old_value,
            new_value=new_value
        )
        db.commit() # Log kaydını veritabanına yazmak için ikinci commit (eğer log_action otomatik commit etmiyorsa)
    except Exception:
        # Loglama hatası ana akışı (kullanıcının ayarlarının kaydedilmesini) bozmasın diye pass geçilebilir 
        # veya sistem loglarına (logger.error) yazdırılabilir.
        pass

    return {"message": "Kullanıcı ayarları başarıyla güncellendi"}