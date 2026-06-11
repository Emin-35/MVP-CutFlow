"""
app/schemas/production_schemas.py

Üretim olayı şemaları (accountant kaydeder, manager görür).
"""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, model_validator

from app.models.enums import ProductionEventType


class ProductionEventCreate(BaseModel):
    """
    Accountant üretim olayı kaydeder.
    event_type=cutting_stopped     → note zorunlu (sebep açıklaması)
    event_type=ready_count_updated → ready_count zorunlu
    """
    event_type:  ProductionEventType
    note:        Optional[str] = Field(None, max_length=500)
    ready_count: Optional[int] = None

    @model_validator(mode="after")
    def validate_event_fields(self):
        if self.event_type == ProductionEventType.cutting_stopped and not self.note:
            raise ValueError("cutting_stopped olayı için sebep notu zorunludur")
        if self.event_type == ProductionEventType.ready_count_updated and self.ready_count is None:
            raise ValueError("ready_count_updated olayı için ready_count değeri zorunludur")
        return self


class ProductionEventOut(BaseModel):
    id:          int
    order_id:    int
    event_type:  ProductionEventType
    note:        Optional[str]
    ready_count: Optional[int]
    created_by:  int
    created_at:  datetime

    class Config:
        from_attributes = True
