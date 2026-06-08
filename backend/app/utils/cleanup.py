"""
Süresi dolmuş temp fatura dosyalarını temizler.
Uygulama başlarken arka planda periyodik çalışır.
"""
import os
import logging
import threading
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.db.base import SessionLocal
from app.models.models import TempInvoiceFile

logger = logging.getLogger(__name__)
CLEANUP_INTERVAL_SECONDS = 60 * 30  # 30 dakikada bir


def _run_cleanup():
    db: Session = SessionLocal()
    try:
        expired = db.query(TempInvoiceFile).filter(
            TempInvoiceFile.expires_at < datetime.now(timezone.utc)
        ).all()

        for record in expired:
            # Önce diskten sil
            if record.file_path and os.path.exists(record.file_path):
                try:
                    os.remove(record.file_path)
                except OSError as e:
                    logger.warning(f"Dosya silinemedi: {record.file_path} — {e}")
            # Sonra DB'den sil
            db.delete(record)

        if expired:
            db.commit()
            logger.info(f"Cleanup: {len(expired)} süresi dolmuş temp kayıt silindi.")

    except Exception as e:
        db.rollback()
        logger.error(f"Cleanup hatası: {e}")
    finally:
        db.close()


def _cleanup_loop():
    while True:
        _run_cleanup()
        threading.Event().wait(CLEANUP_INTERVAL_SECONDS)


def start_cleanup_scheduler():
    """main.py'den bir kez çağrılır."""
    thread = threading.Thread(target=_cleanup_loop, daemon=True)
    thread.start()
    logger.info("Temp dosya cleanup scheduler başlatıldı.")