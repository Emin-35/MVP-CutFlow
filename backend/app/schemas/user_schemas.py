"""
app/schemas/user_schemas.py

Kullanıcı ve şifre şemaları.
Bu endpointler yalnızca manager tarafından yönetilir.
"""
import re
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, Field, computed_field, field_validator, model_validator

from app.models.enums import UserRole
from app.schemas.validators import validate_person_name


# ─────────────────────────────────────────
# TELEFON DOĞRULAMA (Türkiye)
# ─────────────────────────────────────────

def normalize_tr_phone(value: Optional[str]) -> Optional[str]:
    """
    Türkiye telefon numarasını doğrular ve standart '0XXXXXXXXXX' (11 hane)
    formatına normalleştirir. Boş/None ise None döner.

    Kabul edilen girişler (boşluk, tire, parantez, nokta serbest):
      +90 532 123 45 67 / 0090 532 ... / 0532 123 45 67 / 532 123 45 67
    Kural: ülke/0 öneki temizlendikten sonra 10 hane kalmalı ve ilk hane
    2-5 arası olmalı (sabit hat 2/3/4, mobil 5).
    """
    if value is None:
        return None
    digits = re.sub(r"[\s\-().]", "", value.strip())
    if digits == "":
        return None

    if digits.startswith("+90"):
        digits = digits[3:]
    elif digits.startswith("0090"):
        digits = digits[4:]
    elif len(digits) == 12 and digits.startswith("90"):
        digits = digits[2:]
    elif digits.startswith("0"):
        digits = digits[1:]

    if not digits.isdigit():
        raise ValueError("Telefon numarası yalnızca rakam ve +, -, boşluk, parantez içerebilir.")
    if len(digits) != 10 or digits[0] not in "2345":
        raise ValueError(
            "Geçersiz Türkiye telefon numarası. Örn: 0532 123 45 67 veya +90 532 123 45 67"
        )
    return "0" + digits


# ─────────────────────────────────────────
# USER
# ─────────────────────────────────────────

class UserBase(BaseModel):
    username:   str
    email:      EmailStr
    # Opsiyonel kişisel bilgiler — şimdilik zorunlu değil
    first_name: Optional[str] = Field(None, max_length=100)
    last_name:  Optional[str] = Field(None, max_length=100)
    phone:      Optional[str] = Field(None, max_length=30)


class UserCreate(UserBase):
    password: str
    role: UserRole

    @field_validator("phone")
    @classmethod
    def _check_phone(cls, v: Optional[str]) -> Optional[str]:
        return normalize_tr_phone(v)

    @field_validator("first_name", "last_name")
    @classmethod
    def _check_name(cls, v: Optional[str]) -> Optional[str]:
        return validate_person_name(v, field_label="Ad/Soyad")


class UserUpdate(BaseModel):
    """Kullanıcı bilgilerini günceller (kısmi — yalnızca gönderilen alanlar)."""
    username:   Optional[str]      = None
    email:      Optional[EmailStr] = None
    first_name: Optional[str]      = Field(None, max_length=100)
    last_name:  Optional[str]      = Field(None, max_length=100)
    phone:      Optional[str]      = Field(None, max_length=30)

    @field_validator("phone")
    @classmethod
    def _check_phone(cls, v: Optional[str]) -> Optional[str]:
        return normalize_tr_phone(v)

    @field_validator("first_name", "last_name")
    @classmethod
    def _check_name(cls, v: Optional[str]) -> Optional[str]:
        return validate_person_name(v, field_label="Ad/Soyad")


class UserRoleUpdate(BaseModel):
    role: UserRole


class UserOut(UserBase):
    id:         int
    role:       UserRole
    is_active:  bool
    created_at: datetime

    @computed_field  # type: ignore[misc]
    @property
    def name(self) -> str:
        """Görüntülenecek ad: 'Ad Soyad' (varsa), yoksa username'e düşer.
        Frontend 'Merhaba {user.name}' için doğrudan kullanabilir."""
        full = f"{self.first_name or ''} {self.last_name or ''}".strip()
        return full or self.username

    class Config:
        from_attributes = True


# ─────────────────────────────────────────
# PASSWORD
# ─────────────────────────────────────────

min_password_len = 1


class ChangePasswordRequest(BaseModel):
    """Manager bir kullanıcının şifresini sıfırlar (global_change_password)."""
    new_password: str = Field(..., min_length=min_password_len)


class ChangeSelfPasswordRequest(BaseModel):
    """Kullanıcı kendi şifresini değiştirir."""
    old_password:         str
    new_password:         str = Field(..., min_length=min_password_len)
    new_password_confirm: str

    @model_validator(mode="after")
    def passwords_match(self):
        if self.new_password != self.new_password_confirm:
            raise ValueError("Yeni şifreler eşleşmiyor")
        return self
