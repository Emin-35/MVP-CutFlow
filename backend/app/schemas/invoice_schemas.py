"""
app/schemas/invoice_schemas.py

Fatura ve OCR şemaları.
"""
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.models.enums import InvoiceType


# ─────────────────────────────────────────
# INVOICE — DOSYA YÜKLEME + TARAMA (iki ayrı adım)
# ─────────────────────────────────────────
# Akış:
#   1) Her dosya TEK TEK upload-...-file endpoint'ine yüklenir → file_token döner
#      (frontend 1-3 dosya için endpoint'i ayrı ayrı çağırır)
#   2) Kullanıcı "Tara" butonuna basınca scan-files çağrılır → tüm dosyalar
#      OCR'lanır, birleşik (merged) ön-doldurma verisi döner
#   3) submit'e birincil token (invoice_token) + kalanlar (extra_file_tokens) gönderilir

class InvoiceFileUploadOut(BaseModel):
    """Tek dosya yükleme yanıtı. file_token tarama ve submit'te kullanılır."""
    file_token:    str
    original_name: Optional[str] = None


class InvoiceScanRequest(BaseModel):
    """Yüklenmiş dosyaların token'ları — hepsi taranır, sonuçlar birleştirilir."""
    file_tokens: List[str] = Field(..., min_length=1, max_length=10)


class InvoiceFileScanResult(BaseModel):
    file_token: str
    ocr_result: Dict[str, Any]


class InvoiceScanOut(BaseModel):
    """
    results: dosya başına ham OCR çıktısı
    merged:  tüm dosyalardan birleştirilmiş ön-doldurma verisi
             (frontend form sütunlarını bununla doldurur)
    """
    results: List[InvoiceFileScanResult]
    merged:  Dict[str, Any]


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
    extra_file_tokens: List[str] = []   # ek fotoğraflar → order_files'a eklenir
