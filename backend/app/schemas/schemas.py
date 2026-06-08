"""
Pydantic Schemas — Request / Response modelleri
"""
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, EmailStr, Field, model_validator

from app.models.models import (
    AuditAction, InvoiceType, NotifType,
    OrderStatus, UserRole
)


# ─────────────────────────────────────────
# USER
# ─────────────────────────────────────────

class UserBase(BaseModel):
    username: str
    email: EmailStr

class UserCreate(UserBase):
    password: str
    role: UserRole

# 3. Güncelleme Esnasında (Her şey isteğe bağlıdır)
class UserUpdate(BaseModel):
    username: Optional[str] = None
    email: Optional[EmailStr] = None

class UserRoleUpdate(BaseModel):
    role: UserRole

class UserOut(UserBase):
    id:         int
    role:       UserRole
    is_active:  bool
    created_at: datetime

    class Config:
        from_attributes = True


# ─────────────────────────────────────────
# PASSWORD RESET
# ─────────────────────────────────────────

min_password_len = 1
# Change password için ayrı schema — sadece yeni şifre alanı var - Müdür için
class ChangePasswordRequest(BaseModel):
    new_password: str = Field(..., min_length=min_password_len)

# Self-service password change — kullanıcı kendi şifresini değiştirebilir
class ChangeSelfPasswordRequest(BaseModel):
    old_password:        str
    new_password:        str = Field(..., min_length=min_password_len)
    new_password_confirm: str

    @model_validator(mode="after")
    def passwords_match(self):
        if self.new_password != self.new_password_confirm:
            raise ValueError("Yeni şifreler eşleşmiyor")
        return self


# ─────────────────────────────────────────
# AUTH
# ─────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


class Token(BaseModel):
    access_token: str
    token_type:   str = "bearer"


class TokenPayload(BaseModel):
    sub:  int       # user id
    role: UserRole
    exp:  datetime


# ─────────────────────────────────────────
# METAL REQUEST
# ─────────────────────────────────────────

class MetalRequestCreate(BaseModel):
    """
    Tek bir metal kalemi.
    Frontend'de iki preset sunulur:
      Preset A: width=1500, length=3000, thickness=3, material=GLV, quantity=1
      Preset B: width=1250, length=2500, thickness=3, material=GLV, quantity=1
    Tüm alanlar kullanıcı tarafından editlenebilir.
    Sipariş başına sınırsız kalem eklenebilir.
    """
    width:     Decimal               # En  (mm)
    length:    Decimal               # Boy (mm)
    thickness: Decimal               # Kalınlık (mm)
    material:  str                   # Malzeme tipi — örn: GLV
    quantity:  int      = 1          # Plaka adedi
    kg:        Optional[Decimal] = None
    total:     Optional[Decimal] = None
    notes:     Optional[str]    = Field(None, max_length=300)  # Notlar 300 karakterle sınırlı


class MetalRequestOut(BaseModel):
    id:         int
    order_id:   int
    width:      Decimal
    length:     Decimal
    thickness:  Decimal
    material:   str
    quantity:   int
    kg:         Optional[Decimal]
    total:      Optional[Decimal]
    notes:      Optional[str] = Field(None, max_length=300)  # Notlar 300 karakterle sınırlı
    created_by: int
    created_at: datetime

    class Config:
        from_attributes = True


# ─────────────────────────────────────────
# ORDER
# ─────────────────────────────────────────

class OrderCreate(BaseModel):
    """
    Sipariş oluşturma payload'ı.
    Akış:
      1. POST /orders/upload-invoice-ocr  → invoice_token + ocr_result döner
      2. Kullanıcı OCR verisini düzenler, metal kalemlerini ve müşteri bilgilerini girer
      3. POST /orders/create-order        → bu payload gönderilir
    """
    # Zorunlu alanlar
    order_title:          str                       # Kullanıcının verdiği custom sipariş adı
    invoice_token:        str                       # upload-invoice-ocr'dan dönen UUID token
    edited_invoice_data:  Dict[str, Any]            # Kullanıcının onayladığı/düzenlediği OCR verisi
    estimated_amount:     Decimal                   # Faturadan gelen / kullanıcının onayladığı tutar
    metal_items:          List[MetalRequestCreate]  # En az 1 kalem zorunlu

    # Opsiyonel müşteri bilgileri
    customer_name:    Optional[str] = None
    customer_phone:   Optional[str] = None
    customer_address: Optional[str] = None


class OrderUpdate(BaseModel):
    # Sipariş meta
    order_title:      Optional[str] = None
    # Müşteri
    customer_name:    Optional[str] = None
    customer_phone:   Optional[str] = None
    customer_address: Optional[str] = None
    # Üretim
    total_count:      Optional[int]  = None
    estimated_amount: Optional[Decimal] = None
    metal_arrived:    Optional[bool] = None
    cutting_started:  Optional[bool] = None
    cutting_done:     Optional[bool] = None
    ready_count:      Optional[int]  = None


class OrderApprove(BaseModel):
    approved:         bool
    rejection_reason: Optional[str] = None   # approved=False ise zorunlu


class OrderStatusOut(BaseModel):
    id:           int
    order_number: str
    order_title:  str

    customer_name:    Optional[str]
    customer_contact: Optional[str]   # Geriye dönük uyumluluk
    customer_phone:   Optional[str]
    customer_address: Optional[str]

    status:           OrderStatus
    rejection_reason: Optional[str]

    metal_arrived:   bool
    cutting_started: bool
    cutting_done:    bool
    ready_count:     int
    total_count:     Optional[int]

    estimated_amount: Optional[Decimal]
    final_amount:     Optional[Decimal]

    created_by:  int
    approved_by: Optional[int]

    created_at:   datetime
    updated_at:   datetime
    approved_at:  Optional[datetime]
    completed_at: Optional[datetime]

    class Config:
        from_attributes = True


class OrderListOut(BaseModel):
    """Listede daha az alan döner — performans"""
    id:           int
    order_number: str
    order_title:  str          # Listede custom isim gösterilir

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


# ─────────────────────────────────────────
# INVOICE — OCR UPLOAD (sipariş öncesi)
# ─────────────────────────────────────────

class InvoiceOCRUploadOut(BaseModel):
    """
    POST /orders/upload-invoice-ocr yanıtı.
    Frontend invoice_token'ı saklar ve create-order'a gönderir.
    ocr_result kullanıcıya gösterilir, düzenlenebilir.
    """
    invoice_token: str
    ocr_result:    Dict[str, Any]


# ─────────────────────────────────────────
# INVOICE
# ─────────────────────────────────────────

class InvoiceOCRData(BaseModel):
    """OCR'dan çekilen ve kullanıcının düzenleyebileceği alanlar"""
    customer_name:  Optional[str]           = None
    supplier_name:  Optional[str]           = None
    invoice_date:   Optional[str]           = None
    invoice_number: Optional[str]           = None
    total_amount:   Optional[Decimal]       = None
    tax_amount:     Optional[Decimal]       = None
    line_items:     Optional[List[Dict[str, Any]]] = None
    raw_text:       Optional[str]           = None


class InvoiceCreate(BaseModel):
    order_id:    int
    type:        InvoiceType
    edited_data: InvoiceOCRData
    amount:      Decimal


class InvoiceOut(BaseModel):
    id:            int
    order_id:      int
    type:          InvoiceType
    ocr_raw:       Optional[Dict[str, Any]]   # Ham OCR — salt okunur
    edited_data:   Optional[Dict[str, Any]]   # Kullanıcı onaylı
    file_path:     Optional[str]
    file_type:     Optional[str]
    original_name: Optional[str]
    amount:        Optional[Decimal]
    uploaded_by:   int
    uploaded_at:   datetime

    class Config:
        from_attributes = True


class InvoiceCompareOut(BaseModel):
    """initial vs final fatura karşılaştırması"""
    order_id:       int
    initial_amount: Optional[Decimal]
    final_amount:   Optional[Decimal]
    match:          bool
    difference:     Optional[Decimal]


class FinalInvoiceSubmit(BaseModel):
    """Final faturasını siparişe bağlama ve tamamlama isteği"""
    invoice_token: str
    edited_data: Dict[str, Any]
    final_amount: Decimal


# ─────────────────────────────────────────
# MISMATCH RESOLUTION & EDIT GRANT
# ─────────────────────────────────────────
 
class MismatchResolve(BaseModel):
    """Müdürün tutar uyuşmazlığını çözme isteği — ÜÇ seçenek:"""
    approve:      bool              # True → farkı kabul et, tamamla
    grant_edit:   bool = False      # True → muhasebeye düzenleme izni ver (approve=False ile birlikte)
    manager_note: str = Field(..., max_length=300)  # Müdürün kararıyla ilgili notu — her iki durumda da zorunlu

    # approve=False ve grant_edit=False ise → iptal et
 
 
class EditGrantedInvoiceSubmit(BaseModel):
    """
    Muhasebe edit_granted siparişine yeni fatura yükledikten sonra gönderir.
    Akış:
      1. POST /{order_id}/upload-edit-invoice-ocr  → token + ocr_result
      2. Kullanıcı OCR verisini kontrol eder, tutarı girer
      3. POST /{order_id}/submit-edit-invoice       → bu payload
    """
    invoice_token:       str              # upload-edit-invoice-ocr'dan dönen token
    edited_invoice_data: Dict[str, Any]   # Kullanıcının onayladığı OCR verisi
    new_amount:          Decimal          # Yeni/düzeltilmiş fatura tutarı



# ─────────────────────────────────────────
# NOTIFICATION
# ─────────────────────────────────────────

class NotificationOut(BaseModel):
    id:         int
    order_id:   Optional[int]
    type:       NotifType
    message:    Optional[str]
    is_read:    bool
    created_at: datetime

    class Config:
        from_attributes = True


class NotificationMarkRead(BaseModel):
    notification_ids: List[int]


# ─────────────────────────────────────────
# ORDER STATUS HISTORY
# ─────────────────────────────────────────

class StatusHistoryOut(BaseModel):
    id:         int
    old_status: Optional[OrderStatus]
    new_status: OrderStatus
    changed_by: int
    note:       Optional[str] = Field(None, max_length=300)  # Notlar 300 karakterle sınırlı
    created_at: datetime

    class Config:
        from_attributes = True


# ─────────────────────────────────────────
# AUDIT LOG
# ─────────────────────────────────────────

class AuditLogOut(BaseModel):
    id:         int
    user_id:    Optional[int]
    order_id:   Optional[int]
    action:     AuditAction
    old_value:  Optional[Dict[str, Any]]
    new_value:  Optional[Dict[str, Any]]
    created_at: datetime

    class Config:
        from_attributes = True


# ─────────────────────────────────────────
# PAGINATION
# ─────────────────────────────────────────

class PaginatedOrders(BaseModel):
    total:     int
    page:      int
    page_size: int
    items:     List[OrderListOut]