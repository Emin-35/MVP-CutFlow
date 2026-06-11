"""
app/schemas/schemas.py  —  GERİYE DÖNÜK UYUMLULUK SHIM'i

Eski kod `from app.schemas.schemas import OrderCreate` şeklinde import ediyordu.
Şemalar artık role/alan bazlı ayrı dosyalara bölündü.
Bu modül her şeyi yeniden export ederek eski import yollarını korur.

Yeni kodda doğrudan:  from app.schemas import OrderCreate
import etmeniz önerilir. Bu shim ileride kaldırılabilir.
"""
from app.schemas import *  # noqa: F401,F403
