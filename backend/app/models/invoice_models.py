"""
app/models/invoice_models.py

Invoice ve MetalRequest ORM modelleri.

Fatura dosya yolu standardı:
  initial (staff): uploads/staff/<yıl-ay>/<uuid>.<uzantı>
  final (accountant): uploads/accountant/<yıl-ay>/<uuid>.<uzantı>
"""
from sqlalchemy import (
    Column, DateTime, Enum, ForeignKey,
    Integer, JSON, Numeric, String, Text
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base import Base
from app.models.enums import InvoiceType


class Invoice(Base):
    """
    initial → sipariş şeması (staff yükler, create-order akışında)
    final   → tahsilat faturası (accountant yükler, order_completed akışında)

    NOT: final fatura tutarı, siparişin estimated_amount değeri ile karşılaştırılır.
         Tutar uyuşmazsa BACKEND özel bir status'a geçmez ve manager'a bildirim
         göndermez. Karşılaştırma sonucu (bkz. InvoiceCompareOut) frontend'e döner;
         frontend bir uyarı ekranı gösterir:
           - "Doğru faturayı yükle"  → accountant final faturayı tekrar yükler
           - "Bu fatura ile devam et" → sipariş 'completed' olur
         (edit_granted / mismatch_review akışı YOK.)
    """
    __tablename__ = "invoices"

    id            = Column(Integer, primary_key=True, index=True)
    order_id      = Column(Integer, ForeignKey("orders.id"), nullable=False)
    type          = Column(Enum(InvoiceType, name="invoice_type"), nullable=False)

    ocr_raw       = Column(JSON)    # OCR ham çıktı — asla değiştirilmez
    edited_data   = Column(JSON)    # Kullanıcının onaylayıp düzenlediği veri

    file_path     = Column(String)  # uploads/<rol>/<yıl-ay>/<uuid>.<uzantı>
    file_type     = Column(String)
    original_name = Column(String)
    amount        = Column(Numeric(12, 2))

    uploaded_by = Column(Integer, ForeignKey("users.id"))
    uploaded_at = Column(DateTime(timezone=True), server_default=func.now())

    order    = relationship("Order", back_populates="invoices")
    uploader = relationship("User")


class MetalRequest(Base):
    """
    Sipariş oluşturulurken girilen metal kalemleri. Sınırsız sayıda eklenebilir.

    total hesaplama:
      total = width(mm) * length(mm) * thickness(mm) * quantity * 8 / 1_000_000

    Preset A: width=1500, length=3000, thickness=3, material=GLV, quantity=1
    Preset B: width=1250, length=2500, thickness=3, material=GLV, quantity=1
    """
    __tablename__ = "metal_requests"

    id       = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False)

    width     = Column(Numeric(8, 2),  nullable=False)
    length    = Column(Numeric(8, 2),  nullable=False)
    thickness = Column(Numeric(6, 3),  nullable=False)
    material  = Column(String(100),    nullable=False)
    quantity  = Column(Integer,        nullable=False, default=1)
    kg        = Column(Numeric(10, 3))
    total     = Column(Numeric(12, 2))
    notes     = Column(Text)

    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    order   = relationship("Order", back_populates="metal_requests")
    creator = relationship("User")
