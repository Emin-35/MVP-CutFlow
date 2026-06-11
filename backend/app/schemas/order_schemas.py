"""
app/schemas/order_schemas.py

Sipariş şemaları.

DÜZELTME (v3): OrderStatusOut eskiden InvoiceOut ve ProductionEventOut'a
kendisinden ÖNCE tanımlanmadan referans veriyordu → NameError riski.
Bu tipler artık ayrı modüllerden EN ÜSTTE import edilerek sorun giderildi.
"""
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional
from pydantic import BaseModel

from app.models.enums import OrderStatus
from app.schemas.metal_schemas import MetalRequestCreate, MetalRequestOut, ExtraMetalRequestOut
from app.schemas.invoice_schemas import InvoiceOut
from app.schemas.production_schemas import ProductionEventOut


# ─────────────────────────────────────────
# ORDER — CREATE / BUY / UPDATE
# ─────────────────────────────────────────

class OrderCreate(BaseModel):
    """
    Staff sipariş oluşturma payload'ı.
    Akış:
      1. POST /orders/upload-invoice-ocr → invoice_token + ocr_result döner
      2. Staff OCR verisini düzenler, metal kalemlerini girer
      3. POST /orders/create-order       → bu payload gönderilir
    """
    order_title:         str
    invoice_token:       str
    edited_invoice_data: Dict[str, Any]
    estimated_amount:    Decimal
    metal_items:         List[MetalRequestCreate]   # En az 1 kalem zorunlu

    customer_name:    Optional[str] = None
    customer_phone:   Optional[str] = None
    customer_address: Optional[str] = None


class OrderBuy(BaseModel):
    """
    Buyer siparişi satın alır → status: pending_approval → active.
    Reddederse rejection_reason zorunlu.
    """
    approved:         bool
    rejection_reason: Optional[str] = None


class OrderUpdate(BaseModel):
    """
    Accountant ve manager'ın güncelleyebildiği alanlar.
    Üretim adımları (metal_arrived vb.) production_events'e taşındı — burada yok.
    """
    order_title:      Optional[str]     = None
    customer_name:    Optional[str]     = None
    customer_phone:   Optional[str]     = None
    customer_address: Optional[str]     = None
    total_count:      Optional[int]     = None
    estimated_amount: Optional[Decimal] = None
    ready_count:      Optional[int]     = None
    note:             Optional[str]     = None   # Üretim notu


# ─────────────────────────────────────────
# ORDER — OUTPUT
# ─────────────────────────────────────────

class OrderStatusOut(BaseModel):
    id:           int
    order_number: str
    order_title:  str

    customer_name:    Optional[str]
    customer_contact: Optional[str]   # Geriye dönük, ileride müşteri portalı için
    customer_phone:   Optional[str]
    customer_address: Optional[str]

    status:           OrderStatus
    rejection_reason: Optional[str]

    ready_count: int
    total_count: Optional[int]
    note:        Optional[str]

    estimated_amount: Optional[Decimal]
    final_amount:     Optional[Decimal]

    created_by:   int
    bought_by:    Optional[int]
    completed_by: Optional[int]

    created_at:   datetime
    updated_at:   datetime
    bought_at:    Optional[datetime]
    completed_at: Optional[datetime]

    # İlişkili tablolar — "her detaya erişebilme" kısmı
    metal_requests:       List[MetalRequestOut]      = []
    extra_metal_requests: List[ExtraMetalRequestOut] = []
    invoices:             List[InvoiceOut]           = []
    production_events:    List[ProductionEventOut]   = []

    class Config:
        from_attributes = True


class OrderListOut(BaseModel):
    id:           int
    order_number: str
    order_title:  str
    customer_name: Optional[str]
    status:        OrderStatus
    estimated_amount: Optional[Decimal]
    final_amount:     Optional[Decimal]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ─────────────────────────────────────────
# ORDER FILE
# ─────────────────────────────────────────

class OrderFileOut(BaseModel):
    id:            int
    order_id:      int
    file_path:     str
    file_type:     str
    original_name: Optional[str]
    uploaded_by:   int
    uploaded_at:   datetime

    class Config:
        from_attributes = True
