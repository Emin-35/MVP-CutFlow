"""
main.py — RATE LIMITER ENTEGRASYONU (referans)

Mevcut main.py'ye eklenecek/değişecek SATIRLAR aşağıda işaretlendi.
Tüm dosyayı kopyalamana gerek yok; sadece [+ EKLE] ve [~ DEĞİŞTİR]
işaretli kısımları kendi main.py'ne taşı.
"""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# [+ EKLE] rate limit importları
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from app.core.rate_limit import limiter

from app.core.config import config_settings
from app.api.routes import (
    auth, files, history, order_ocr_helper, orders, notifications, users,
    buyer, staff, production,
)

from app.utils.cleanup import start_cleanup_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_cleanup_scheduler()
    yield


app = FastAPI(
    title=config_settings.APP_NAME,
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ─────────────────────────────────────────
# RATE LIMITER KURULUMU
# ─────────────────────────────────────────
# 1) limiter'ı app.state'e bağla (decorator'lar app.state.limiter'ı okur)
app.state.limiter = limiter
# 2) 429 için exception handler kaydet (JSON {"error": "..."} döner)
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
# 3) (OPSİYONEL) middleware — yalnızca default_limits kullanacaksan gerekli.
#    Biz endpoint bazlı @limiter.limit kullandığımız için ŞART DEĞİL.
#    Global bir taban limit istersen rate_limit.py'de default_limits doldur
#    ve aşağıyı aç:
# app.add_middleware(SlowAPIMiddleware)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Router'lar
PREFIX = config_settings.API_V1_PREFIX
app.include_router(auth.router,             prefix=PREFIX)
app.include_router(users.router,            prefix=PREFIX)
app.include_router(orders.router,           prefix=PREFIX)
app.include_router(order_ocr_helper.router, prefix=PREFIX)
app.include_router(buyer.router,            prefix=PREFIX)
app.include_router(staff.router,            prefix=PREFIX)
app.include_router(production.router,       prefix=PREFIX)
app.include_router(history.router,          prefix=PREFIX)
app.include_router(notifications.router,    prefix=PREFIX)
app.include_router(files.router,            prefix=PREFIX)   # JWT korumalı dosya servisi


@app.get("/health")
async def health():
    return {"status": "ok", "app": config_settings.APP_NAME}


@app.get("/")
def home():
    return {"message": "Metal Kesim API çalışıyor"}