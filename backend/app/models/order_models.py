"""
app/models/order_models.py

Order, OrderFile, TempInvoiceFile ORM modelleri.

Dosya yolu standardı (v3):
  uploads/<rol>/<yıl-ay>/<uuid>.<uzantı>
  Örnek: uploads/staff/2024-03/a1b2c3d4.pdf
         uploads/accountant/2024-03/e5f6g7h8.jpg
  Bu format storage servisinde (utils/storage.py) oluşturulur;
  burada yalnızca ham path string saklanır.
"""
from sqlalchemy import (
    Column, DateTime, Enum, ForeignKey,
    Integer, JSON, Numeric, String, Text
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base import Base
from app.models.enums import OrderStatus


class Order(Base):
    __tablename__ = "orders"

    id           = Column(Integer, primary_key=True, index=True)
    order_number = Column(String(50), unique=True, nullable=False)   # ORD-2024-001
    order_title  = Column(String(255), nullable=False)

    # ── Müşteri bilgileri ─────────────────────────────────────────
    customer_name    = Column(String(255))
    # Aşağıdakiler şimdilik boş; ileride müşteri portalı açılırsa kullanılır
    customer_contact = Column(String(255))
    customer_phone   = Column(String(50))
    customer_address = Column(Text)

    # ── Durum ─────────────────────────────────────────────────────
    status           = Column(Enum(OrderStatus, name="order_status"), nullable=False, default=OrderStatus.pending_approval)
    rejection_reason = Column(Text)

    # ── Üretim takibi ─────────────────────────────────────────────
    # Detaylı olaylar (cutting_started vb.) → production_events tablosu
    ready_count  = Column(Integer, default=0)   # Anlık sevkiyata hazır ürün sayısı
    total_count  = Column(Integer)              # metal_requests[].quantity toplamı
    note         = Column(Text)                 # Accountant üretim notu (makine arızası vb.)

    # ── Tutarlar ──────────────────────────────────────────────────
    estimated_amount = Column(Numeric(12, 2))   # initial invoice tutarı (şema)
    final_amount     = Column(Numeric(12, 2))   # final invoice tutarı (fatura)

    # ── Sahiplik ──────────────────────────────────────────────────
    created_by   = Column(Integer, ForeignKey("users.id"))                    # staff
    bought_by    = Column(Integer, ForeignKey("users.id"), nullable=True)     # buyer
    completed_by = Column(Integer, ForeignKey("users.id"), nullable=True)     # accountant

    # ── Tarihler ──────────────────────────────────────────────────
    created_at   = Column(DateTime(timezone=True), server_default=func.now())
    updated_at   = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    bought_at    = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    # ── Relations ─────────────────────────────────────────────────
    creator   = relationship("User", back_populates="created_orders",  foreign_keys=[created_by])
    buyer     = relationship("User", back_populates="bought_orders",   foreign_keys=[bought_by])
    completer = relationship("User", back_populates="completed_orders", foreign_keys=[completed_by])

    invoices             = relationship("Invoice",          back_populates="order", cascade="all, delete-orphan", lazy="selectin")
    order_files          = relationship("OrderFile",        back_populates="order", cascade="all, delete-orphan")
    metal_requests       = relationship("MetalRequest",     back_populates="order", cascade="all, delete-orphan", lazy="selectin")
    extra_metal_requests = relationship("ExtraMetalRequest",back_populates="order", cascade="all, delete-orphan", lazy="selectin")
    production_events    = relationship("ProductionEvent",  back_populates="order", cascade="all, delete-orphan", lazy="selectin")
    notifications        = relationship("Notification",     back_populates="order")
    status_history       = relationship("OrderStatusHistory", back_populates="order", cascade="all, delete-orphan")
    audit_logs           = relationship("AuditLog",         back_populates="order")


class OrderFile(Base):
    """
    Sipariş şema dosyası (PDF / resim).
    Yol: uploads/staff/<yıl-ay>/<uuid>.<uzantı>
    """
    __tablename__ = "order_files"

    id            = Column(Integer, primary_key=True, index=True)
    order_id      = Column(Integer, ForeignKey("orders.id"), nullable=False)
    file_path     = Column(String, nullable=False)   # uploads/staff/2024-03/abc.pdf
    file_type     = Column(String, nullable=False)   # application/pdf | image/jpeg | image/png
    original_name = Column(String)
    uploaded_by   = Column(Integer, ForeignKey("users.id"))
    uploaded_at   = Column(DateTime(timezone=True), server_default=func.now())

    order    = relationship("Order", back_populates="order_files")
    uploader = relationship("User")


class TempInvoiceFile(Base):
    """
    Sipariş oluşturulmadan önce yüklenen geçici fatura/şema dosyası.

    Akış:
      1. POST /orders/upload-invoice-ocr → dosya buraya yazılır, token döner
      2. POST /orders/create-order       → token ile bu kayıt okunur, Invoice + OrderFile'a taşınır
      3. Bu kayıt silinir.

    expires_at geçen kayıtlar periyodik temizlik job'ı ile temizlenir (2 saatlik TTL).

    Dosya yolu: uploads/staff/<yıl-ay>/<uuid>.<uzantı>
    """
    __tablename__ = "temp_invoice_files"

    id            = Column(Integer, primary_key=True, index=True)
    token         = Column(String(64), unique=True, nullable=False, index=True)
    file_path     = Column(String, nullable=False)
    file_type     = Column(String, nullable=False)
    original_name = Column(String)
    ocr_raw       = Column(JSON)

    uploaded_by = Column(Integer, ForeignKey("users.id"))
    uploaded_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at  = Column(DateTime(timezone=True), nullable=False)

    uploader = relationship("User")
