"""
app/models/log_models.py

Notification, OrderStatusHistory, AuditLog ORM modelleri.

BİLDİRİM HEDEFLEME (önemli):
  notifications.recipient_id tek bir kullanıcıya bağlıdır.
  Bir olay birden fazla kullanıcıyı ilgilendiriyorsa (örn. 2 accountant),
  bildirim servisi her alıcı için AYRI bir Notification satırı insert eder.
  Böylece her kullanıcı yalnızca kendi recipient_id'sine ait bildirimleri görür;
  roller arası karışma olmaz (accountant, manager bildirimini göremez).
"""
from sqlalchemy import (
    Boolean, Column, DateTime, Enum, ForeignKey,
    Integer, JSON, String, Text
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base import Base
from app.models.enums import AuditAction, NotifType, OrderStatus


class Notification(Base):
    __tablename__ = "notifications"

    id           = Column(Integer, primary_key=True, index=True)
    recipient_id = Column(Integer, ForeignKey("users.id",  ondelete="CASCADE"), nullable=False)
    order_id     = Column(Integer, ForeignKey("orders.id", ondelete="SET NULL"), nullable=True)

    type      = Column(Enum(NotifType, name="notif_type"), nullable=False)
    message   = Column(Text)
    is_read   = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    recipient = relationship("User",  back_populates="notifications")
    order     = relationship("Order", back_populates="notifications")


class OrderStatusHistory(Base):
    __tablename__ = "order_status_history"

    id       = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False)

    old_status = Column(Enum(OrderStatus, name="order_status"), nullable=True)
    new_status = Column(Enum(OrderStatus, name="order_status"), nullable=False)

    changed_by = Column(Integer, ForeignKey("users.id"))
    note       = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    order           = relationship("Order", back_populates="status_history")
    changed_by_user = relationship("User")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id       = Column(Integer, primary_key=True, index=True)
    user_id  = Column(Integer, ForeignKey("users.id",  ondelete="SET NULL"), nullable=True)
    order_id = Column(Integer, ForeignKey("orders.id", ondelete="SET NULL"), nullable=True)

    action     = Column(Enum(AuditAction, name="audit_action"), nullable=False)
    old_value  = Column(JSON)
    new_value  = Column(JSON)
    ip_address = Column(String(45))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user  = relationship("User",  back_populates="audit_logs")
    order = relationship("Order", back_populates="audit_logs")
