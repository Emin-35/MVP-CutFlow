"""
app/models/models.py  —  GERİYE DÖNÜK UYUMLULUK SHIM'i

Eski kod `from app.models.models import User, UserRole` şeklinde import ediyordu.
Modeller artık ayrı dosyalara bölündü (enums, user_models, order_models, ...).
Bu modül her şeyi yeniden export ederek eski import yollarını korur.

Yeni kodda doğrudan:  from app.models import User
veya alt modülden:     from app.models.user_models import User
import etmeniz önerilir. Bu shim ileride kaldırılabilir.
"""
from app.models import (  # noqa: F401
    AuditAction,
    AuditLog,
    ExtraMetalRequest,
    ExtraMetalStatus,
    Invoice,
    InvoiceType,
    MetalRequest,
    NotifCategory,
    NotifType,
    NOTIF_TYPE_CATEGORY,
    Notification,
    Order,
    OrderFile,
    OrderStatus,
    OrderStatusHistory,
    ProductionEvent,
    ProductionEventType,
    TempInvoiceFile,
    User,
    UserRole,
)
