"""
Veritabanı tablolarını oluşturur ve ilk kullanıcıları ekler.
Sıfırdan kurulum için kullan.

Kullanım:
    cd backend
    python seed.py
"""
from app.db.base import SessionLocal, engine, Base

# Tüm modellerin import edilmesi zorunlu — Base.metadata bunları tanısın
from app.models.models import (
    User, Order, OrderFile, TempInvoiceFile,
    Invoice, MetalRequest, Notification,
    OrderStatusHistory, AuditLog
)
from app.core.security import hash_password
from app.models.models import UserRole


def create_tables():
    print("Tablolar oluşturuluyor...")
    Base.metadata.create_all(bind=engine)
    print("[OK] Tüm tablolar oluşturuldu.\n")


def seed():
    db = SessionLocal()
    try:
        users_to_create = [
            {
                "username": "manager",
                "email": "manager@abc.com",
                "password": "manager123",
                "role": UserRole.manager,
            },
            {
                "username": "accountant",
                "email": "accountant@abc.com",
                "password": "accountant123",
                "role": UserRole.accounting,
            },
        ]

        for u in users_to_create:
            exists = db.query(User).filter(User.username == u["username"]).first()
            if exists:
                print(f"[SKIP] {u['username']} zaten mevcut")
                continue

            user = User(
                username=u["username"],
                email=u["email"],
                password_hash=hash_password(u["password"]),
                role=u["role"],
            )
            db.add(user)
            print(f"[OK] {u['username']} ({u['role'].value}) oluşturuldu")

        db.commit()
        print("\nSeed tamamlandı.")
        print("  Müdür    → username: manager     / şifre: manager123")
        print("  Muhasebe → username: accountant  / şifre: accountant123")

    finally:
        db.close()


if __name__ == "__main__":
    create_tables()
    seed()