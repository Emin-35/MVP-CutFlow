"""
app/schemas/metal_schemas.py

Metal kalemi ve ekstra metal talebi şemaları.

NOT: ExtraMetalStatus enum'u eskiden bu dosyada (schemas.py içinde) ayrıca
     tanımlıydı. Artık tek kaynak app.models.enums.ExtraMetalStatus.
     Eski yerel tanım aşağıda yorum satırına alındı (silinmedi).
"""
from datetime import datetime
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, Field, model_validator

from app.models.enums import ExtraMetalStatus


# ─────────────────────────────────────────
# METAL REQUEST (sipariş oluşturma)
# ─────────────────────────────────────────

class MetalRequestCreate(BaseModel):
    """
    Sipariş oluşturulurken girilecek bir metal kalemi.

    total hesaplama:
      total = width * length * thickness * quantity * 8 / 1_000_000
      (width, length, thickness mm cinsinden)

    Preset A: width=1500, length=3000, thickness=3, material=GLV, quantity=1
    Preset B: width=1250, length=2500, thickness=3, material=GLV, quantity=1
    """
    width:     Decimal = Field(..., gt=0)
    length:    Decimal = Field(..., gt=0)
    thickness: Decimal = Field(..., gt=0)
    material:  str
    quantity:  int     = Field(1, gt=0)
    kg:        Optional[Decimal] = None
    total:     Optional[Decimal] = None
    notes:     Optional[str]     = Field(None, max_length=300)

    @model_validator(mode="after")
    def calculate_and_verify_total(self):
        # Frontend hesaplasa bile backend tekrar doğrular ve ezer
        calculated = (self.width * self.length * self.thickness * self.quantity * 8) / 1000000
        self.total = Decimal(str(round(float(calculated), 2)))
        return self


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
    notes:      Optional[str]
    created_by: int
    created_at: datetime

    class Config:
        from_attributes = True


# ─────────────────────────────────────────
# EXTRA METAL REQUEST (staff → buyer)
# ─────────────────────────────────────────

class ExtraMetalRequestCreate(BaseModel):
    """
    Staff, aktif bir siparişe ekstra metal talebi açar.
    Buyer bildirim alır ve onaylayabilir.

    NOT: Burada hesaplanan değer modelde 'estimated_cost' kolonuna yazılır.
    """
    width:     Decimal = Field(..., gt=0)
    length:    Decimal = Field(..., gt=0)
    thickness: Decimal = Field(..., gt=0)
    material:  str
    quantity:  int            = Field(1, gt=0)
    kg:        Optional[Decimal] = None
    estimated_cost:     Optional[Decimal] = None    # estimated_cost olarak kaydedilir
    reason:    Optional[str]     = Field(None, max_length=500)

    @model_validator(mode="after")
    def calculate_and_verify_total(self):
        calculated = (self.width * self.length * self.thickness * self.quantity * 8) / 1000000
        self.estimated_cost = Decimal(str(round(float(calculated), 2)))
        return self


class ExtraMetalRequestOut(BaseModel):
    id:          int
    order_id:    int
    width:       Decimal
    length:      Decimal
    thickness:   Decimal
    material:    str
    quantity:    int
    kg:          Optional[Decimal]
    estimated_cost: Optional[Decimal]   # modeldeki kolon adıyla hizalı
    reason:      Optional[str]
    buyer_note:  Optional[str]
    status:      ExtraMetalStatus
    approved_by: Optional[int]
    approved_at: Optional[datetime]
    created_by:  int
    created_at:  datetime

    class Config:
        from_attributes = True


class ExtraMetalApprove(BaseModel):
    """Buyer ekstra metal talebini onaylar veya reddeder."""
    approved: bool
    note:     Optional[str] = Field(None, max_length=300)
