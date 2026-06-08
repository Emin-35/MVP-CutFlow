"""
Sipariş numarası üretici — ORD-2024-001 formatı (Race Condition Önlenmiş Güvenli Versiyon)
"""
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import func, text

from app.models.models import Order


def generate_order_number(db: Session) -> str:
    year = datetime.now().year
    
    # 1. Tablo seviyesinde kilit: Bu işlem bitene kadar başka kimse orders tablosuna INSERT yapamaz.
    # Bu kilit, transaction (db.commit veya db.rollback) bitene kadar geçerlidir.
    db.execute(text("LOCK TABLE orders IN EXCLUSIVE MODE"))
    
    # 2. Kilit alındığı için artık güvenle eşzamanlılık riski olmadan sayım yapabiliriz
    count = db.query(func.count(Order.id)).filter(
        Order.order_number.like(f"ORD-{year}-%")
    ).scalar() or 0
    
    return f"ORD-{year}-{count + 1:03d}"
