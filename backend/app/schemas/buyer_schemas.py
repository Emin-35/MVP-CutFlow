"""
app/schemas/buyer_schemas.py

"Satın Alınacaklar" sayfası şemaları.
Sadece buyer ve manager görür. Her satır hangi siparişe (order_title) ait
olduğunu da gösterir.

DÜZELTME (v3): from_model'in tip ipucu ExtraMetalRequestCreate idi (yanlış —
o bir request/input şeması). Artık ORM modeli ExtraMetalRequest bekliyor.
"""
from datetime import datetime
from decimal import Decimal
from typing import List, Optional, TYPE_CHECKING
from pydantic import BaseModel, Field

from app.models.enums import ExtraMetalStatus, OrderStatus

if TYPE_CHECKING:
    # Yalnızca tip denetimi için; runtime circular import'u önler
    from app.models.production_models import ExtraMetalRequest


class PurchasePageItemOut(BaseModel):
    """
    Satın Alınacaklar ekranında listelenecek detaylı veri modeli.
    Talebe ait metal detayları ile siparişe ait üst bilgileri bir arada sunar.
    """
    request_id:     int
    width:          Decimal
    length:         Decimal
    thickness:      Decimal
    material:       str
    quantity:       int
    kg:             Optional[Decimal]
    total:            Optional[Decimal]   # otomatik hesaplanan ağırlık/alan
    estimated_amount: Optional[Decimal]   # elle girilen fiyat
    reason:         Optional[str]
    buyer_note:     Optional[str]
    status:         ExtraMetalStatus
    created_at:     datetime

    # Sipariş ilişki detayları (birleştirilmiş alanlar)
    order_id:      int
    order_number:  str
    order_title:   str
    customer_name: Optional[str]
    order_status:  OrderStatus

    @classmethod
    def from_model(cls, request: "ExtraMetalRequest") -> "PurchasePageItemOut":
        """ExtraMetalRequest ORM nesnesini bu çıktı şemasına dönüştürür."""
        return cls(
            request_id=request.id,
            width=request.width,
            length=request.length,
            thickness=request.thickness,
            material=request.material,
            quantity=request.quantity,
            kg=request.kg,
            total=request.total,
            estimated_amount=request.estimated_amount,
            reason=request.reason,
            buyer_note=request.buyer_note,
            status=request.status,
            created_at=request.created_at,
            order_id=request.order.id,
            order_number=request.order.order_number,
            order_title=request.order.order_title,
            customer_name=request.order.customer_name,
            order_status=request.order.status,
        )


class ExtraMetalDecision(BaseModel):
    """
    Buyer'ın ekstra metal talep(ler)ini karara bağlaması. TEK ortak endpoint:
    request_ids tek eleman da olabilir (tekil işlem), birden fazla da (toplu).

    action_type:
      approved  → pending_approval → approved  (tutar siparişin estimated_amount'ına EKLENİR)
      rejected  → pending_approval → rejected
      purchased → approved → purchased (arşiv; tutar zaten onayda eklendi, tekrar eklenmez)
    """
    request_ids: List[int] = Field(..., min_length=1, description="İşlem yapılacak talep ID listesi (1+)")
    action_type: ExtraMetalStatus = Field(..., description="approved | rejected | purchased")
    buyer_note:  Optional[str] = Field(None, max_length=500, description="Seçimlere eklenecek ortak not")
