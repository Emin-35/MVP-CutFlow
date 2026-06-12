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
from pydantic import BaseModel, Field, computed_field

from app.models.enums import ExtraMetalStatus, OrderStatus
from app.schemas.metal_schemas import MetalRequestCreate, MetalRequestOut, ExtraMetalRequestOut
from app.schemas.invoice_schemas import InvoiceOut
from app.schemas.production_schemas import ProductionEventOut

# Sipariş toplamına dahil edilen ekstra metal durumları (buyer onayladı/satın aldı)
_COUNTED_EXTRA_STATUSES = {ExtraMetalStatus.approved, ExtraMetalStatus.purchased}


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

    # Aynı yüklemede gelen ek fotoğrafların token'ları (order_files'a bağlanır)
    extra_file_tokens: List[str] = []
    # NOT: customer_name serbest metindir — müşteri bir firma olabilir
    #      (rakam/sembol içerebilir), kişi-adı regex'i UYGULANMAZ.


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


class OrderRevisionRequest(BaseModel):
    """
    Buyer, pending_approval siparişte staff'tan düzenleme ister (red ETMEZ).
    Not zorunlu: staff neyi düzeltmesi gerektiğini bilmeli.
    """
    note: str = Field(..., min_length=3, max_length=1000)


class OrderContentUpdate(BaseModel):
    """
    Staff, buyer'ın revizyon notu üzerine pending_approval siparişini düzenler.
    metal_items gönderilirse TÜM liste yenisiyle DEĞİŞTİRİLİR (replace-all):
    frontend, tablodaki satırların son hâlini komple gönderir.
    """
    order_title:      Optional[str]     = None
    customer_name:    Optional[str]     = None
    customer_phone:   Optional[str]     = None
    customer_address: Optional[str]     = None
    estimated_amount: Optional[Decimal] = None
    metal_items:      Optional[List[MetalRequestCreate]] = None


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
    buyer_note:       Optional[str] = None   # Buyer'ın düzenleme isteği notu

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

    # ── Toplulaştırılmış değerler (base = ilk sipariş, extra = onaylı/satın alınmış
    #    ekstra metaller, final = ikisinin toplamı) ──
    # estimated_amount alanı BASE (ilk fatura) tutarını taşır; aşağıdakiler türetilir.
    @computed_field  # type: ignore[misc]
    @property
    def extra_total(self) -> Decimal:
        """Onaylı/satın alınmış ekstra metallerin ağırlık/alan (total) toplamı."""
        return sum(
            (e.total for e in self.extra_metal_requests
             if e.total is not None and e.status in _COUNTED_EXTRA_STATUSES),
            Decimal("0"),
        )

    @computed_field  # type: ignore[misc]
    @property
    def base_total(self) -> Decimal:
        """İlk siparişteki metal kalemlerinin (metal_requests) total toplamı."""
        return sum((m.total for m in self.metal_requests if m.total is not None), Decimal("0"))

    @computed_field  # type: ignore[misc]
    @property
    def final_total(self) -> Decimal:
        """base_total + extra_total."""
        return self.base_total + self.extra_total

    @computed_field  # type: ignore[misc]
    @property
    def extra_estimated_amount(self) -> Decimal:
        """Onaylı/satın alınmış ekstra metallerin FİYAT (estimated_amount) toplamı."""
        return sum(
            (e.estimated_amount for e in self.extra_metal_requests
             if e.estimated_amount is not None and e.status in _COUNTED_EXTRA_STATUSES),
            Decimal("0"),
        )

    @computed_field  # type: ignore[misc]
    @property
    def final_estimated_amount(self) -> Decimal:
        """estimated_amount (base) + extra_estimated_amount.
        Final fatura bu tutarla karşılaştırılır (ekstra metaller dahil)."""
        base = self.estimated_amount if self.estimated_amount is not None else Decimal("0")
        return base + self.extra_estimated_amount

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
