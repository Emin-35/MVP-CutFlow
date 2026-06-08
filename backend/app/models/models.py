"""
SQLAlchemy ORM Models
"""
import enum
from datetime import datetime, timezone, timedelta
from sqlalchemy import (
    Boolean, Column, DateTime, Enum, ForeignKey,
    Integer, Numeric, String, Text, JSON
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.base import Base


# ─────────────────────────────────────────
# ENUMS
# ─────────────────────────────────────────

class UserRole(str, enum.Enum):
    manager    = "manager"
    accounting = "accounting"


class OrderStatus(str, enum.Enum):
    pending_approval = "pending_approval"
    active           = "active"
    on_hold          = "on_hold"
    cancelled        = "cancelled"
    completed        = "completed"
    deleted          = "deleted"
    mismatch_review  = "mismatch_review"
    edit_granted     = "edit_granted"


class InvoiceType(str, enum.Enum):
    initial = "initial"
    final   = "final"


class NotifType(str, enum.Enum):
    approval_needed = "approval_needed"
    approved        = "approved"
    rejected        = "rejected"
    edit_requested  = "edit_requested"


class AuditAction(str, enum.Enum):
    # order
    order_created            = "order_created"
    order_updated            = "order_updated"
    order_approved           = "order_approved"
    order_rejected           = "order_rejected"
    order_cancelled          = "order_cancelled"
    order_completed          = "order_completed"
    order_deleted            = "order_deleted"
    invoice_uploaded         = "invoice_uploaded"
    amount_changed           = "amount_changed"
    ocr_data_edited          = "ocr_data_edited"
    status_changed           = "status_changed"
    metal_request_added      = "metal_request_added"
    production_step_updated  = "production_step_updated"
    # user
    user_created             = "user_created"
    user_updated             = "user_updated"
    user_role_changed        = "user_role_changed"
    user_deactivated         = "user_deactivated"
    user_reactivated         = "user_reactivated"

    @classmethod
    def user_actions(cls) -> list["AuditAction"]:
        return [a for a in cls if a.value.startswith("user_")]

    @classmethod
    def order_actions(cls) -> list["AuditAction"]:
        return [a for a in cls if not a.value.startswith("user_")]


# ─────────────────────────────────────────
# USER
# ─────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id            = Column(Integer, primary_key=True, index=True)
    username      = Column(String(100), unique=True, nullable=False)
    email         = Column(String(255), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)

    role          = Column(Enum(UserRole, name="userrole"), nullable=False)

    is_active     = Column(Boolean, default=True)
    created_at    = Column(DateTime(timezone=True), server_default=func.now())
    updated_at    = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relations
    created_orders  = relationship("Order", back_populates="creator",  foreign_keys="Order.created_by")
    approved_orders = relationship("Order", back_populates="approver", foreign_keys="Order.approved_by")
    notifications   = relationship("Notification", back_populates="recipient")
    audit_logs      = relationship("AuditLog", back_populates="user")


# ─────────────────────────────────────────
# ORDER
# ─────────────────────────────────────────

class Order(Base):
    __tablename__ = "orders"

    id           = Column(Integer, primary_key=True, index=True)
    order_number = Column(String(50), unique=True, nullable=False)

    # Sipariş custom başlığı — kullanıcının verdiği isim
    order_title  = Column(String(255), nullable=False)

    # Müşteri bilgileri
    customer_name    = Column(String(255))          # Opsiyonel
    customer_contact = Column(String(255))          # Eski alan, geriye dönük uyumluluk
    customer_phone   = Column(String(50))           # Hazır alan, şimdilik opsiyonel
    customer_address = Column(Text)                 # Hazır alan, şimdilik opsiyonel

    # Durum
    status           = Column(Enum(OrderStatus, name="orderstatus"), nullable=False, default=OrderStatus.pending_approval)
    rejection_reason = Column(Text)

    # Üretim adımları
    metal_arrived   = Column(Boolean, default=False)
    cutting_started = Column(Boolean, default=False)
    cutting_done    = Column(Boolean, default=False)
    ready_count     = Column(Integer, default=0)
    total_count     = Column(Integer)               # metal_items[].quantity toplamından otomatik

    # Tutarlar
    estimated_amount = Column(Numeric(12, 2))
    final_amount     = Column(Numeric(12, 2))

    # Sahiplik & onay
    created_by  = Column(Integer, ForeignKey("users.id"))
    approved_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    # Tarihler
    created_at   = Column(DateTime(timezone=True), server_default=func.now())
    updated_at   = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    approved_at  = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    # Relations
    creator        = relationship("User", back_populates="created_orders",  foreign_keys=[created_by])
    approver       = relationship("User", back_populates="approved_orders", foreign_keys=[approved_by])
    invoices       = relationship("Invoice",              back_populates="order", cascade="all, delete-orphan")
    order_files    = relationship("OrderFile",            back_populates="order", cascade="all, delete-orphan")
    metal_requests = relationship("MetalRequest",         back_populates="order", cascade="all, delete-orphan")
    notifications  = relationship("Notification",         back_populates="order")
    status_history = relationship("OrderStatusHistory",   back_populates="order", cascade="all, delete-orphan")
    audit_logs     = relationship("AuditLog",             back_populates="order")


# ─────────────────────────────────────────
# ORDER FILES
# ─────────────────────────────────────────

class OrderFile(Base):
    __tablename__ = "order_files"

    id            = Column(Integer, primary_key=True, index=True)
    order_id      = Column(Integer, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False)
    file_path     = Column(String, nullable=False)
    file_type     = Column(String, nullable=False)
    original_name = Column(String)
    uploaded_by   = Column(Integer, ForeignKey("users.id"))
    uploaded_at   = Column(DateTime(timezone=True), server_default=func.now())

    order    = relationship("Order", back_populates="order_files")
    uploader = relationship("User")


# ─────────────────────────────────────────
# TEMP INVOICE FILES (OCR öncesi geçici depo)
# ─────────────────────────────────────────

class TempInvoiceFile(Base):
    """
    Sipariş oluşturulmadan önce yüklenen fatura burada bekler.
    create-order çağrıldığında Invoice'a taşınır, bu kayıt silinir.
    expires_at geçen kayıtlar periyodik temizlik job'ı ile temizlenir.
    """
    __tablename__ = "temp_invoice_files"

    id            = Column(Integer, primary_key=True, index=True)
    token         = Column(String(64), unique=True, nullable=False, index=True)

    file_path     = Column(String, nullable=False)
    file_type     = Column(String, nullable=False)
    original_name = Column(String)
    ocr_raw       = Column(JSON)                    # OCR ham çıktı burada bekler

    uploaded_by   = Column(Integer, ForeignKey("users.id"))
    uploaded_at   = Column(DateTime(timezone=True), server_default=func.now())
    expires_at    = Column(DateTime(timezone=True), nullable=False)  # +2 saat

    uploader = relationship("User")


# ─────────────────────────────────────────
# INVOICE
# ─────────────────────────────────────────

class Invoice(Base):
    __tablename__ = "invoices"

    id            = Column(Integer, primary_key=True, index=True)
    order_id      = Column(Integer, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False)

    type          = Column(Enum(InvoiceType, name="invoicetype"), nullable=False)

    ocr_raw       = Column(JSON)     # OCR ham çıktı — asla değiştirilmez
    edited_data   = Column(JSON)     # Kullanıcının onaylayıp düzenlediği veri

    file_path     = Column(String)
    file_type     = Column(String)
    original_name = Column(String)
    amount        = Column(Numeric(12, 2))

    uploaded_by = Column(Integer, ForeignKey("users.id"))
    uploaded_at = Column(DateTime(timezone=True), server_default=func.now())

    order    = relationship("Order", back_populates="invoices")
    uploader = relationship("User")


# ─────────────────────────────────────────
# METAL REQUEST
# ─────────────────────────────────────────

class MetalRequest(Base):
    """
    Bir siparişe ait metal kalemleri. Sınırsız sayıda eklenebilir.

    Preset A (varsayılan): width=1500, length=3000, thickness=3, material=GLV, quantity=1
    Preset B:              width=1250, length=2500, thickness=3, material=GLV, quantity=1
    Her alan frontend'de editlenebilir.
    """
    __tablename__ = "metal_requests"

    id       = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False)

    # Boyutlar
    width     = Column(Numeric(8, 2),  nullable=False)   # En  (mm)
    length    = Column(Numeric(8, 2),  nullable=False)   # Boy (mm)
    thickness = Column(Numeric(6, 3),  nullable=False)   # Kalınlık (mm)

    # Malzeme
    material  = Column(String(100), nullable=False)      # Örn: GLV

    # Miktar & ağırlık
    quantity  = Column(Integer, nullable=False, default=1)  # Plaka adedi
    kg        = Column(Numeric(10, 3))                      # Ağırlık (kg)
    total     = Column(Numeric(12, 2))                      # Satır toplamı (tutar)

    notes      = Column(Text)
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    order   = relationship("Order", back_populates="metal_requests")
    creator = relationship("User")


# ─────────────────────────────────────────
# NOTIFICATION
# ─────────────────────────────────────────

class Notification(Base):
    __tablename__ = "notifications"

    id           = Column(Integer, primary_key=True, index=True)
    recipient_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    order_id     = Column(Integer, ForeignKey("orders.id", ondelete="SET NULL"), nullable=True)

    type         = Column(Enum(NotifType, name="notiftype"), nullable=False)

    message      = Column(Text)
    is_read      = Column(Boolean, default=False)
    created_at   = Column(DateTime(timezone=True), server_default=func.now())

    recipient = relationship("User", back_populates="notifications")
    order     = relationship("Order", back_populates="notifications")


# ─────────────────────────────────────────
# ORDER STATUS HISTORY
# ─────────────────────────────────────────

class OrderStatusHistory(Base):
    __tablename__ = "order_status_history"

    id         = Column(Integer, primary_key=True, index=True)
    order_id   = Column(Integer, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False)

    old_status = Column(Enum(OrderStatus, name="orderstatus"), nullable=True)
    new_status = Column(Enum(OrderStatus, name="orderstatus"), nullable=False)

    changed_by = Column(Integer, ForeignKey("users.id"))
    note       = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    order           = relationship("Order", back_populates="status_history")
    changed_by_user = relationship("User")


# ─────────────────────────────────────────
# AUDIT LOG
# ─────────────────────────────────────────

class AuditLog(Base):
    __tablename__ = "audit_logs"

    id         = Column(Integer, primary_key=True, index=True)
    user_id    = Column(Integer, ForeignKey("users.id",   ondelete="SET NULL"), nullable=True)
    order_id   = Column(Integer, ForeignKey("orders.id",  ondelete="SET NULL"), nullable=True)

    action     = Column(Enum(AuditAction, name="auditaction"), nullable=False)

    old_value  = Column(JSON)
    new_value  = Column(JSON)
    ip_address = Column(String(45))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user  = relationship("User",  back_populates="audit_logs")
    order = relationship("Order", back_populates="audit_logs")