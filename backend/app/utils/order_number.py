"""
Sipariş numarası üretici — ORD-2026-001 formatı (Sequence ile Kilitlemesiz & Güvenli Versiyon)
"""
from datetime import datetime
from sqlalchemy import text
from sqlalchemy.orm import Session


def generate_order_number(db: Session) -> str:
    year = datetime.now().year
    
    # LOCK TABLE tamamen kaldırıldı! 
    # PostgreSQL sequence kullanarak atomik (güvenli) bir şekilde sıradaki sayıyı alıyoruz.
    result = db.execute(text("SELECT nextval('order_number_seq')"))
    next_id = result.scalar()
    
    # Çıktı formatı: ORD-2026-001, ORD-2026-002...
    return f"ORD-{year}-{next_id:03d}"