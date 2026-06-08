# app/services/notification_service.py (veya utils.py)

from sqlalchemy.orm import Session
from app.models.models import Notification, User, NotifType
from fastapi import Request
from app.services.audit import get_client_ip, log_action, AuditAction  # audit.py'den gerekli fonksiyonları import edin

def send_order_notification_to_managers(db: Session, order, order_title: str, order_number: str):
    """Tüm müdürlere sipariş onayı bildirimi gönderir"""
    
    # Tüm aktif müdürleri veritabanından çekelim
    # (Eğer managers listesini fonksiyonu çağırdığınız yerde zaten çekiyorsanız, parametre olarak da paslayabilirsiniz)
    managers = db.query(User).filter(User.role == "manager", User.is_active == True).all()
    
    for manager in managers:
        db.add(Notification(
            recipient_id=manager.id,
            order_id=order.id,
            type=NotifType.approval_needed,
            message=f'"{order_title}" ({order_number}) onay bekliyor.',
        ))
    
    # db.commit() veya db.flush() işlemini burada yapmıyoruz.
    # Ana endpoint kendi işlemi başarılı olunca topluca commit edecek