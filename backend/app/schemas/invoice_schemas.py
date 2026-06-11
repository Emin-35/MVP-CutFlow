"""
app/schemas/invoice_schemas.py

Fatura ve OCR şemaları.
"""
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional
from pydantic import BaseModel

from app.models.enums import InvoiceType


# ─────────────────────────────────────────
# INVOICE — OCR UPLOAD
# ─────────────────────────────────────────

class InvoiceOCRUploadOut(BaseModel):
    """
    POST /orders/upload-invoice-ocr yanıtı.
    invoice_token create-order'a gönderilir.
    ocr_result kullanıcıya gösterilir ve düzenlenebilir.
    """
    invoice_token: str
    ocr_result:    Dict[str, Any]


# ─────────────────────────────────────────
# INVOICE — OCR DATA
# ─────────────────────────────────────────

class InvoiceOCRData(BaseModel):
    customer_name:  Optional[str]                  = None
    supplier_name:  Optional[str]                  = None
    invoice_date:   Optional[str]                  = None
    invoice_number: Optional[str]                  = None
    total_amount:   Optional[Decimal]              = None
    tax_amount:     Optional[Decimal]              = None
    line_items:     Optional[List[Dict[str, Any]]] = None
    raw_text:       Optional[str]                  = None


# ─────────────────────────────────────────
# INVOICE
# ─────────────────────────────────────────

class InvoiceCreate(BaseModel):
    order_id:    int
    type:        InvoiceType
    edited_data: InvoiceOCRData
    amount:      Decimal


class InvoiceOut(BaseModel):
    id:            int
    order_id:      int
    type:          InvoiceType
    ocr_raw:       Optional[Dict[str, Any]]
    edited_data:   Optional[Dict[str, Any]]
    file_path:     Optional[str]
    file_type:     Optional[str]
    original_name: Optional[str]
    amount:        Optional[Decimal]
    uploaded_by:   int
    uploaded_at:   datetime

    class Config:
        from_attributes = True


class InvoiceCompareOut(BaseModel):
    """initial vs final fatura karşılaştırması (mismatch kontrolü)."""
    order_id:       int
    initial_amount: Optional[Decimal]
    final_amount:   Optional[Decimal]
    match:          bool
    difference:     Optional[Decimal]


class FinalInvoiceSubmit(BaseModel):
    """
    Accountant final faturasını yükler ve tamamlama isteği gönderir.

    Tutar initial (estimated_amount) ile uyuşmazsa backend ÖZEL bir status'a
    geçmez ve manager onayı beklemez. Endpoint karşılaştırma sonucunu
    (InvoiceCompareOut.match) döner; frontend uyarı ekranı gösterir:
      - "Doğru faturayı yükle"  → bu endpoint yeni faturayla tekrar çağrılır
      - "Bu fatura ile devam et" → force_complete=True ile çağrılır, status → completed
    """
    invoice_token: str
    edited_data:   Dict[str, Any]
    final_amount:  Decimal
    force_complete: bool = False   # frontend "bu fatura ile devam et" derse True
