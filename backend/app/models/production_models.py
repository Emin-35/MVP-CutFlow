"""
app/models/production_models.py

ProductionEvent ve ExtraMetalRequest ORM modelleri.
"""
from sqlalchemy import (
    Column, DateTime, Enum, ForeignKey,
    Integer, Numeric, String, Text
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base import Base
from app.models.enums import ExtraMetalStatus, ProductionEventType


class ExtraMetalRequest(Base):
    """
    Staff, aktif bir sipariş için ekstra metal talebi açar.
    Buyer bildirim alır, onaylarsa satın alır.
    Tüm işlemler aynı order_id altında loglanır; aktif siparişin başka
    hiçbir alanı değişmez, yalnızca üstüne ekstra malzeme eklenir.
    """
    __tablename__ = "extra_metal_requests"

    id       = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False)

    width          = Column(Numeric(8, 2),  nullable=False)
    length         = Column(Numeric(8, 2),  nullable=False)
    thickness      = Column(Numeric(6, 3),  nullable=False)
    material       = Column(String(100),    nullable=False)
    quantity       = Column(Integer,        nullable=False, default=1)
    kg             = Column(Numeric(10, 3))
    estimated_cost = Column(Numeric(12, 2))   # schemas tarafında 'total' formülüyle hesaplanır

    reason     = Column(Text)   # Staff'ın açtığı talep nedeni
    buyer_note = Column(Text)   # Buyer'ın süreç esnasında ekleyeceği satın alma/operasyon notu

    status      = Column(Enum(ExtraMetalStatus, name="extra_metal_status"),
                         default=ExtraMetalStatus.pending_approval, nullable=False)
    approved_by = Column(Integer, ForeignKey("users.id"), nullable=True)   # buyer
    approved_at = Column(DateTime(timezone=True), nullable=True)

    created_by = Column(Integer, ForeignKey("users.id"))   # staff
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    order     = relationship("Order", back_populates="extra_metal_requests")
    requester = relationship("User",  back_populates="extra_metal_created",  foreign_keys=[created_by])
    approver  = relationship("User",  back_populates="extra_metal_approved", foreign_keys=[approved_by])


class ProductionEvent(Base):
    """
    Üretim sürecindeki olayları loglar (accountant kaydeder).
    cutting_stopped birden fazla kez olabilir (makine arızası, iş kazası vb.).
    Manager dashboard bu tabloyu kronolojik sırada okur.
    """
    __tablename__ = "production_events"

    id       = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False)

    event_type  = Column(Enum(ProductionEventType, name="production_event_type"), nullable=False)
    note        = Column(Text)      # cutting_stopped → sebep; diğerleri için opsiyonel
    ready_count = Column(Integer)   # Yalnızca ready_count_updated olayında dolu gelir

    created_by = Column(Integer, ForeignKey("users.id"))   # accountant
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    order   = relationship("Order", back_populates="production_events")
    creator = relationship("User",  back_populates="production_events")
