import os
from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+psycopg2://deriv_admin:deriv_secret_pass@postgres:5432/deriv_dca"
    REDIS_URL: str = "redis://redis:6379/0"
    JWT_SECRET: str = "change-me-in-production-32-chars-min"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 120
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    ENCRYPTION_KEY: str = ""
    DERIV_APP_ID: str = "1089"
    DERIV_PUBLIC_WS: str = "wss://api.derivws.com/trading/v1/options/ws/public"
    DERIV_AUTH_WS_BASE: str = "wss://ws.derivws.com/websockets/v3"
    ADMIN_EMAIL: str = "admin@roboderiv.local"
    ADMIN_PASSWORD: str = "Admin@123456"
    ADMIN_NAME: str = "Administrador"
    CORS_ORIGINS: str = "http://localhost:8095,http://127.0.0.1:8095"

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
