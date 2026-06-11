"""
app/models/__init__.py

Tüm model ve enum'ları tek noktadan re-export eder.
Mevcut kodun `from app.models.models import User` şeklindeki import'larını
kırmamak için bir uyumluluk modülü (models.py) de korunmuştur.

Yeni kodda tercih edilen import:
  from app.models import User, Order, OrderStatus
"""
from app.models.enums import (  # noqa: F401
    AuditAction,
    ExtraMetalStatus,
    InvoiceType,
    NotifCategory,
    NotifType,
    NOTIF_TYPE_CATEGORY,
    OrderStatus,
    ProductionEventType,
    UserRole,
    types_in_category,
)
from app.models.user_models import User  # noqa: F401
from app.models.order_models import Order, OrderFile, TempInvoiceFile  # noqa: F401
from app.models.invoice_models import Invoice, MetalRequest  # noqa: F401
from app.models.production_models import ExtraMetalRequest, ProductionEvent  # noqa: F401
from app.models.log_models import AuditLog, Notification, OrderStatusHistory  # noqa: F401

__all__ = [
    # enums
    "UserRole", "OrderStatus", "InvoiceType", "ExtraMetalStatus",
    "ProductionEventType", "NotifType", "NotifCategory", "AuditAction",
    "NOTIF_TYPE_CATEGORY", "types_in_category",
    # models
    "User", "Order", "OrderFile", "TempInvoiceFile",
    "Invoice", "MetalRequest", "ExtraMetalRequest", "ProductionEvent",
    "Notification", "OrderStatusHistory", "AuditLog",
]
