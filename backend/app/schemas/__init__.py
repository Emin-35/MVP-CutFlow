"""
app/schemas/__init__.py

Tüm Pydantic şemalarını tek noktadan re-export eder.

Yeni kodda tercih edilen import:
  from app.schemas import OrderCreate, UserOut

Import sırası circular import'u önleyecek şekilde düzenlenmiştir:
  metal/invoice/production → order → common (PaginatedOrders OrderListOut'a bağlı)
"""
# Bağımsız / yaprak modüller önce
from app.schemas.user_schemas import (  # noqa: F401
    UserBase, UserCreate, UserUpdate, UserRoleUpdate, UserOut,
    ChangePasswordRequest, ChangeSelfPasswordRequest, min_password_len,
)
from app.schemas.metal_schemas import (  # noqa: F401
    MetalRequestCreate, MetalRequestOut,
    ExtraMetalRequestCreate, ExtraMetalRequestOut, ExtraMetalApprove,
)
from app.schemas.invoice_schemas import (  # noqa: F401
    InvoiceOCRUploadOut, InvoiceOCRData, InvoiceCreate, InvoiceOut,
    InvoiceCompareOut, FinalInvoiceSubmit,
)
from app.schemas.production_schemas import (  # noqa: F401
    ProductionEventCreate, ProductionEventOut,
)
# order_schemas, yukarıdakilere bağımlı
from app.schemas.order_schemas import (  # noqa: F401
    OrderCreate, OrderBuy, OrderUpdate,
    OrderStatusOut, OrderListOut, OrderFileOut,
)
# buyer ve notification
from app.schemas.buyer_schemas import (  # noqa: F401
    PurchasePageItemOut, BatchExtraMetalAction,
)
from app.schemas.notification_schemas import (  # noqa: F401
    NotificationOut, NotificationMarkRead, StatusHistoryOut, AuditLogOut,
)
# common, OrderListOut'a bağımlı → en son
from app.schemas.common import (  # noqa: F401
    LoginRequest, Token, TokenPayload, PaginatedOrders,
)

__all__ = [
    # user
    "UserBase", "UserCreate", "UserUpdate", "UserRoleUpdate", "UserOut",
    "ChangePasswordRequest", "ChangeSelfPasswordRequest", "min_password_len",
    # metal
    "MetalRequestCreate", "MetalRequestOut",
    "ExtraMetalRequestCreate", "ExtraMetalRequestOut", "ExtraMetalApprove",
    # invoice
    "InvoiceOCRUploadOut", "InvoiceOCRData", "InvoiceCreate", "InvoiceOut",
    "InvoiceCompareOut", "FinalInvoiceSubmit",
    # production
    "ProductionEventCreate", "ProductionEventOut",
    # order
    "OrderCreate", "OrderBuy", "OrderUpdate",
    "OrderStatusOut", "OrderListOut", "OrderFileOut",
    # buyer
    "PurchasePageItemOut", "BatchExtraMetalAction",
    # notification
    "NotificationOut", "NotificationMarkRead", "StatusHistoryOut", "AuditLogOut",
    # common
    "LoginRequest", "Token", "TokenPayload", "PaginatedOrders",
]
