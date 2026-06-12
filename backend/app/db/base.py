"""
Database bağlantısı — SQLAlchemy sync engine
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.core.config import config_settings

engine = create_engine(
    config_settings.DATABASE_URL,
    echo=config_settings.DB_ECHO,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

Base = declarative_base()

def get_db():
    """FastAPI dependency — her request'e ayrı session"""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
