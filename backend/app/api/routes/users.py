"""
Users Endpoints — kullanıcı yönetimi (sadece müdür erişebilir)
"""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Query, Session
from typing import List, Optional

from app.db.base import get_db
from app.models.models import AuditLog, User, AuditAction, NotifType
from app.schemas.schemas import UserCreate, UserUpdate, UserOut, ChangePasswordRequest, UserRoleUpdate
from app.core.security import require_manager, get_current_user, hash_password
from app.services.audit import log_action
from app.services.notification_service import notify_actor_and_managers

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/list-users", response_model=List[UserOut])
def list_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_manager),
):
    """Tüm kullanıcıları listele — sadece müdür"""
    return db.query(User).filter(User.is_active == True).order_by(User.created_at).all()


@router.get("/list-inactive-users", response_model=List[UserOut])
def list_inactive_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_manager),
):
    """Tüm inaktif kullanıcıları listele — sadece müdür"""
    return db.query(User).filter(User.is_active == False).order_by(User.created_at).all()


# FRONTEND KISMINDA BU KULLANICI INAKTIF, AKTİF ETMEK ISTER MİSİNİZ DİYE SORMALI 

@router.post("/create-user", response_model=UserOut, status_code=201)
def create_user(
    payload: UserCreate,
    request: Request,
    confirm_reactivate: bool = False,  # Frontend'den gelecek onay flag'i
    db: Session = Depends(get_db),
    current_user: User = Depends(require_manager),
):
    """Yeni kullanıcı oluştur veya inaktif kullanıcıyı onay ile canlandır"""
    
    # Tüm kullanıcılar içinde ara
    existing_user_by_username = db.query(User).filter(User.username == payload.username).first()
    existing_user_by_email = db.query(User).filter(User.email == payload.email).first()

    target_user = existing_user_by_username or existing_user_by_email

    if target_user:
        # DURUM A: Kullanıcı zaten AKTİF ise -> Doğrudan Hata Dön
        if target_user.is_active:
            if existing_user_by_username:
                raise HTTPException(status_code=400, detail="Bu kullanıcı adı zaten aktif olarak kullanılıyor.")
            if existing_user_by_email:
                raise HTTPException(status_code=400, detail="Bu e-posta adresi zaten aktif olarak kullanılıyor.")
        
        # DURUM B: Kullanıcı var ve İNAKTİF (Soft Deleted)
        if not target_user.is_active:
            
            # Eğer frontend henüz onay vermediyse (Müdüre pop-up göstermek için bilgileri dönüyoruz)
            if not confirm_reactivate:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "reason": "user_inactive",
                        "message": "Bu bilgilere sahip inaktif bir kullanıcı bulundu. Aynı kullanıcıyı tekrardan aktive etmek istiyor musunuz?",
                        "user_details": {
                            "id": target_user.id,
                            "username": target_user.username,
                            "email": target_user.email,
                            "role": target_user.role
                        }
                    }
                )
            
            # Eğer müdür pop-up'ta "Evet" dediyse (confirm_reactivate == True gelmiştir)
            old_value = {"username": target_user.username, "role": target_user.role, "is_active": target_user.is_active}
            
            target_user.username = payload.username
            target_user.email = payload.email
            target_user.password_hash = hash_password(payload.password)
            target_user.role = payload.role
            target_user.first_name = payload.first_name
            target_user.last_name = payload.last_name
            target_user.phone = payload.phone
            target_user.is_active = True  # Canlandır
            
            db.flush()
            log_action(db, AuditAction.user_updated, request, current_user.id,
                       old_value=old_value,
                       new_value={"username": target_user.username, "role": target_user.role, "is_active": True})
            notify_actor_and_managers(
                db,
                actor_id=current_user.id,
                notif_type=NotifType.user_reactivated,
                actor_message=f'"{target_user.username}" kullanıcısını yeniden aktive ettiniz.',
                manager_message=f'{current_user.username}, "{target_user.username}" kullanıcısını yeniden aktive etti.',
            )
            db.commit()
            db.refresh(target_user)
            return target_user

    # DURUM C: Sistemde hiç yoksa sıfırdan oluştur
    user = User(
        username=payload.username,
        email=payload.email,
        password_hash=hash_password(payload.password),
        role=payload.role,
        first_name=payload.first_name,
        last_name=payload.last_name,
        phone=payload.phone,
        is_active=True
    )
    db.add(user)
    db.flush()
    log_action(db, AuditAction.user_created, request, current_user.id,
               new_value={"username": user.username, "role": user.role})
    notify_actor_and_managers(
        db,
        actor_id=current_user.id,
        notif_type=NotifType.user_created,
        actor_message=f'"{user.username}" ({user.role}) kullanıcısını oluşturdunuz.',
        manager_message=f'{current_user.username}, "{user.username}" ({user.role}) kullanıcısını oluşturdu.',
    )
    db.commit()
    db.refresh(user)
    return user


@router.get("/{user_id}/get-user", response_model=UserOut)
def get_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_manager),
):
    """Spesifik bir kullanıcı bilgilerini gör — sadece müdür"""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Kullanıcı bulunamadı")
    return user


@router.patch("/{user_id}/update-user", response_model=UserOut)
def update_user(
    user_id: int,
    payload: UserUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_manager),
):
    """Kullanıcı bilgilerini güncelle — sadece müdür"""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Kullanıcı bulunamadı")

    # Sadece gönderilen (None olmayan) alanları işle
    changed_fields = payload.model_dump(exclude_none=True)
    if not changed_fields:
        raise HTTPException(status_code=400, detail="Güncellenecek alan belirtilmedi")
 
    # Çakışma kontrolü — sadece gerçekten değişen değerler için.
    # NOT: Kısmi güncelleme desteklenir; örn. yalnızca telefon/isim gönderilebilir.
    new_username = changed_fields.get("username")
    new_email = changed_fields.get("email")

    if new_username and new_username != user.username:
        if db.query(User).filter(User.username == new_username).first():
            raise HTTPException(status_code=400, detail="Bu kullanıcı adı zaten kullanılıyor")
 
    if new_email and new_email != user.email:
        if db.query(User).filter(User.email == new_email).first():
            raise HTTPException(status_code=400, detail="Bu e-posta zaten kullanılıyor")
        
    # Sadece değiştirilen alanların eski halini logla
    old_values = {k: getattr(user, k) for k in changed_fields}
 
    for field, value in changed_fields.items():
        setattr(user, field, value)
 
    log_action(db, AuditAction.user_updated, request, current_user.id,
               old_value=old_values, new_value=changed_fields)

    notify_actor_and_managers(
        db,
        actor_id=current_user.id,
        notif_type=NotifType.user_updated,
        actor_message=f'"{user.username}" kullanıcısının bilgilerini güncellediniz.',
        manager_message=f'{current_user.username}, "{user.username}" kullanıcısının bilgilerini güncelledi.',
    )

    db.commit()
    db.refresh(user)
    return user


@router.patch("/{user_id}/change-role", response_model=UserOut)
def update_user_role(
    user_id: int,
    payload: UserRoleUpdate,  # Sadece rol bilgisi body'den gelir, güvenli
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_manager),  # Sadece müdür erişebilir!
):
    """Kullanıcının rolünü değiştir — Sadece Müdür yetkisiyle"""
    
    # 1. Rolü değiştirilmek istenen kullanıcıyı bul
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Kullanıcı bulunamadı")
    
    # 2. Müdürün kendi rolünü yanlışlıkla değiştirmesini engelle (Opsiyonel Güvenlik Önlemi)
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Kendi rolünüzü bu endpoint üzerinden değiştiremezsiniz")

    # 3. Denetim günlüğü (Audit Log) için eski ve yeni değerleri hazırla
    old_role = {"role": user.role}
    new_role = {"role": payload.role}

    # 4. Güncellemeyi yap
    user.role = payload.role
    
    # 5. Güvenli Audit Log çağrısı (Sıralamaya dikkat ederek, isme göre atama ile)
    log_action(
        db=db,
        action=AuditAction.user_role_changed,  # Eğer modellerinde daha spesifik bir enum yoksa user_role_changed uygundur
        request=request,
        user_id=current_user.id,
        old_value={"username": user.username, "previous_role": old_role["role"]},
        new_value={"username": user.username, "updated_role": new_role["role"]}
    )

    notify_actor_and_managers(
        db,
        actor_id=current_user.id,
        notif_type=NotifType.user_role_changed,
        actor_message=f'"{user.username}" kullanıcısının rolünü "{new_role["role"]}" olarak değiştirdiniz.',
        manager_message=f'{current_user.username}, "{user.username}" kullanıcısının rolünü "{old_role["role"]}" → "{new_role["role"]}" olarak değiştirdi.',
    )

    db.commit()
    db.refresh(user)  # Güncel veriyi veritabanından çek
    return user


@router.patch("/{user_id}/global-change-password")
def global_change_password(
    user_id: int,
    payload: ChangePasswordRequest, # şifre sadece body'den gelir, güvenli
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_manager),  # sadece manager
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Kullanıcı bulunamadı")
    
    user.password_hash = hash_password(payload.new_password)

    log_action(db, AuditAction.user_updated, request, current_user.id,
               old_value={"user_id": user.id},
               new_value={"action": "password_changed_by_manager"})

    notify_actor_and_managers(
        db,
        actor_id=current_user.id,
        notif_type=NotifType.password_changed,
        actor_message=f'"{user.username}" kullanıcısının şifresini sıfırladınız.',
        manager_message=f'{current_user.username}, "{user.username}" kullanıcısının şifresini sıfırladı.',
    )
    db.commit()
    return {"message": "Şifre güncellendi"}


@router.delete("/{user_id}/delete-user")
def delete_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_manager),
):
    """Kullanıcıyı inaktif et (hard delete yok — veri bütünlüğü için) — sadece müdür"""
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Kendinizi silemezsiniz")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Kullanıcı bulunamadı")
    
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Kullanıcı zaten inaktif")

    old_status = {"is_active": True}
    user.is_active = False
    
    log_action(db, AuditAction.user_deactivated, request, current_user.id,
               old_value={"username": user.username}, new_value={"is_active": False})

    notify_actor_and_managers(
        db,
        actor_id=current_user.id,
        notif_type=NotifType.user_deactivated,
        actor_message=f'"{user.username}" kullanıcısını inaktif ettiniz.',
        manager_message=f'{current_user.username}, "{user.username}" kullanıcısını inaktif etti.',
    )

    db.commit()
    return {"message": f"{user.username} inaktif edildi"}


@router.post("/{user_id}/activate-user")
def activate_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_manager),
):
    """Kullanıcıyı aktif et - sadece müdür"""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Kullanıcı bulunamadı")
    
    if user.is_active:
        raise HTTPException(status_code=400, detail="Kullanıcı zaten aktif")

    old_status = {"is_active": False}
    user.is_active = True
    
    log_action(db, AuditAction.user_reactivated, request, current_user.id,
               old_value={"username": user.username}, new_value={"is_active": True})

    notify_actor_and_managers(
        db,
        actor_id=current_user.id,
        notif_type=NotifType.user_reactivated,
        actor_message=f'"{user.username}" kullanıcısını aktif ettiniz.',
        manager_message=f'{current_user.username}, "{user.username}" kullanıcısını aktif etti.',
    )

    db.commit()
    return {"message": f"{user.username} aktif edildi"}
