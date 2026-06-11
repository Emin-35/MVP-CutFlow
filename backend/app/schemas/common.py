"""
app/schemas/common.py

Auth, token ve pagination şemaları.
"""
from datetime import datetime
from typing import List
from pydantic import BaseModel

from app.models.enums import UserRole
# OrderListOut PaginatedOrders içinde lazım — circular import'tan kaçınmak için
# burada string forward-ref kullanmıyoruz; order_schemas zaten common'a bağımlı değil,
# bu yüzden order_schemas'tan import güvenli.
from app.schemas.order_schemas import OrderListOut


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
    sub:  int
    role: UserRole
    exp:  datetime


# ─────────────────────────────────────────
# PAGINATION
# ─────────────────────────────────────────

class PaginatedOrders(BaseModel):
    total:     int
    page:      int
    page_size: int
    items:     List[OrderListOut]
