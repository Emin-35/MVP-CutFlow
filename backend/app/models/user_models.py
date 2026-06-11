"""
app/models/user_models.py

User ORM modeli.
"""
from sqlalchemy import Boolean, Column, DateTime, Enum, Integer, String
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base import Base
from app.models.enums import UserRole


class User(Base):
    __tablename__ = "users"

    id            = Column(Integer, primary_key=True, index=True)
    username      = Column(String(100), unique=True, nullable=False)
    email         = Column(String(255), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    role          = Column(Enum(UserRole, name="user_role"), nullable=False)

    # ── Kişisel bilgiler (opsiyonel — şimdilik zorunlu değil) ──────
    first_name    = Column(String(100))
    last_name     = Column(String(100))
    phone         = Column(String(30))

    is_active     = Column(Boolean, default=True)
    created_at    = Column(DateTime(timezone=True), server_default=func.now())
    updated_at    = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # ── Relations ─────────────────────────────────────────────────
    created_orders       = relationship("Order", back_populates="creator",   foreign_keys="Order.created_by")
    bought_orders        = relationship("Order", back_populates="buyer",     foreign_keys="Order.bought_by")
    completed_orders     = relationship("Order", back_populates="completer", foreign_keys="Order.completed_by")
    notifications        = relationship("Notification",      back_populates="recipient")
    audit_logs           = relationship("AuditLog",          back_populates="user")
    production_events    = relationship("ProductionEvent",   back_populates="creator")
    extra_metal_created  = relationship("ExtraMetalRequest", back_populates="requester", foreign_keys="ExtraMetalRequest.created_by")
    extra_metal_approved = relationship("ExtraMetalRequest", back_populates="approver",  foreign_keys="ExtraMetalRequest.approved_by")
