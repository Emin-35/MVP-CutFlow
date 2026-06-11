"""
Uygulama konfigürasyonu — .env dosyasından okunur
"""
from pydantic_settings import BaseSettings
from typing import List

class Settings(BaseSettings):
    APP_NAME: str = "Metal Order System"
    DEBUG: bool = False
    API_V1_PREFIX: str = "/api/routes"

    # SQL loglamasını DEBUG'dan ayırıyoruz ve varsayılan olarak kapatıyoruz
    DB_ECHO: bool = False

    # Varsayılan değerleri sil, .env içinde tanımlanmak ZORUNDA (Fail-Fast)
    DATABASE_URL: str
    SECRET_KEY: str
    
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    RATE_LIMIT_ENABLED: bool = True            # testlerde False yapıp kapatabilirsin
    RATE_LIMIT_STORAGE_URI: str | None = None  # ileride "redis://localhost:6379"

    UPLOAD_DIR: str = "uploads"
    MAX_FILE_SIZE_MB: int = 20
    ALLOWED_EXTENSIONS: List[str] = ["pdf", "jpg", "jpeg", "png", "webp"]

    OCR_LANGUAGE: str = "tr"

    class Config:
        env_file = ".env"
        case_sensitive = True

settings = Settings()
