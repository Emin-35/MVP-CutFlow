"""
FastAPI Uygulama Giriş Noktası
"""
from typing import Annotated
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.api.routes import auth, history, order_ocr_helper, orders, notifications, users

from app.utils.cleanup import start_cleanup_scheduler


# Süresi dolmuş temp dosyalarını temizleme işlemini başlat
@asynccontextmanager
async def lifespan(app: FastAPI):
    start_cleanup_scheduler()   # uygulama başlarken bir kez çalışır
    yield

app = FastAPI(
    title=settings.APP_NAME,
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# CORS — local geliştirme için
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Router'lar
PREFIX = settings.API_V1_PREFIX
app.include_router(auth.router,          prefix=PREFIX)
app.include_router(users.router,         prefix=PREFIX)
app.include_router(orders.router,        prefix=PREFIX)
app.include_router(order_ocr_helper.router, prefix=PREFIX)
app.include_router(history.router,       prefix=PREFIX)
app.include_router(notifications.router, prefix=PREFIX)

# Sonraki adımda eklenecekler:
# app.include_router(invoices.router,   prefix=PREFIX)
# app.include_router(ocr.router,        prefix=PREFIX)


@app.get("/health")
async def health():
    return {"status": "ok", "app": settings.APP_NAME}


@app.get("/")
def home():
    return {"message": "Metal Kesim API çalışıyor"}