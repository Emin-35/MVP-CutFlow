"""
app/services/notification_service.py

Bildirim hedefleme servisi (v3).

TASARIM KARARI
──────────────
notifications.recipient_id TEK bir kullanıcıya bağlıdır. Bir olay bir rolü
ilgilendiriyorsa (örn. 2 accountant), o roldeki HER kullanıcı için AYRI bir
Notification satırı insert edilir.

Bildirim gösterimi:  WHERE recipient_id = current_user.id
Bu sayede her kullanıcı yalnızca kendine gelen bildirimleri görür ve roller
arası karışma olmaz (accountant, manager'a giden bildirimi göremez).

Mevcut kullanıcı dağılımı: 1 buyer, 2 accountant, 1 manager, 1 staff.
Kişi sayısı değişse de bu servis otomatik uyum sağlar (rol bazlı sorgu yapar).
"""
from typing import Optional, Sequence

from sqlalchemy.orm import Session

from app.models.enums import NotifType, UserRole
from app.models.user_models import User
from app.models.log_models import Notification


def _active_users_with_role(db: Session, role: UserRole) -> Sequence[User]:
    return (
        db.query(User)
        .filter(User.role == role, User.is_active.is_(True))
        .all()
    )


def notify_user(
    db: Session,
    *,
    recipient_id: int,
    notif_type: NotifType,
    message: Optional[str] = None,
    order_id: Optional[int] = None,
    commit: bool = False,
) -> Notification:
    """Tek bir kullanıcıya bildirim oluşturur."""
    notif = Notification(
        recipient_id=recipient_id,
        order_id=order_id,
        type=notif_type,
        message=message,
    )
    db.add(notif)
    if commit:
        db.commit()
        db.refresh(notif)
    return notif


def notify_role(
    db: Session,
    *,
    role: UserRole,
    notif_type: NotifType,
    message: Optional[str] = None,
    order_id: Optional[int] = None,
    commit: bool = False,
) -> list[Notification]:
    """
    Belirtilen roldeki TÜM aktif kullanıcılara ayrı ayrı bildirim oluşturur.
    Örn. notify_role(db, role=UserRole.accountant, ...) → 2 accountant'a 2 satır.

    NOT: commit=False (varsayılan) — çağıran transaction'ı (sipariş oluşturma vb.)
    tek seferde commit etmelidir. Böylece bildirim, ana işlemle atomik kalır.
    """
    recipients = _active_users_with_role(db, role)
    created: list[Notification] = []
    for user in recipients:
        created.append(
            notify_user(
                db,
                recipient_id=user.id,
                notif_type=notif_type,
                message=message,
                order_id=order_id,
                commit=False,
            )
        )
    if commit:
        db.commit()
    return created


# ─────────────────────────────────────────
# İŞ AKIŞI KISAYOLLARI (hangi olay → hangi rol)
# ─────────────────────────────────────────

def notify_new_order(db: Session, order_id: int, message: str = "Yeni sipariş oluşturuldu") -> None:
    """Staff sipariş oluşturdu → buyer(lar)."""
    notify_role(db, role=UserRole.buyer, notif_type=NotifType.new_order,
                message=message, order_id=order_id)


def notify_extra_metal_requested(db: Session, order_id: int, message: str = "Ekstra metal talebi açıldı") -> None:
    """Staff extra metal talebi açtı → buyer(lar)."""
    notify_role(db, role=UserRole.buyer, notif_type=NotifType.extra_metal_requested,
                message=message, order_id=order_id)


def notify_order_buyed(db: Session, order_id: int, message: str = "Sipariş satın alındı, üretime hazır") -> None:
    """Buyer satın aldı → accountant(lar). (Manager ayrıca dashboard'dan görür.)"""
    notify_role(db, role=UserRole.accountant, notif_type=NotifType.order_buyed,
                message=message, order_id=order_id)


def notify_order_completed(db: Session, order_id: int, message: str = "Sipariş tamamlandı") -> None:
    """Accountant tamamladı → manager(lar)."""
    notify_role(db, role=UserRole.manager, notif_type=NotifType.order_completed,
                message=message, order_id=order_id)


def notify_production_updated(db: Session, order_id: int, message: str = "Üretim güncellendi") -> None:
    """Accountant üretim güncelledi → manager(lar)."""
    notify_role(db, role=UserRole.manager, notif_type=NotifType.production_updated,
                message=message, order_id=order_id)

# NOT: Final fatura tutarı uyuşmazlığı için bildirim fonksiyonu YOK.
#      Uyuşmazlık tamamen frontend'de bir uyarı ekranıyla ele alınır
#      (doğru faturayı yükle / bu fatura ile devam et). Manager'a bildirim gitmez.
