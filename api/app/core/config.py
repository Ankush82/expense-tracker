from pydantic import PostgresDsn, RedisDsn, field_validator
from pydantic_settings import BaseSettings
from typing import Any, Optional

class Settings(BaseSettings):
    # API
    API_V1_STR: str = "/api/v1"
    VERSION: str = "0.1.0"
    # Security
    SECRET_KEY: str
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 8  # 8 days
    # Database
    POSTGRES_SERVER: str
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str
    POSTGRES_DB: str
    DATABASE_URI: Optional[PostgresDsn] = None

    @field_validator("DATABASE_URI", mode="before")
    @classmethod
    def assemble_db_connection(cls, v: Optional[str], info) -> Any:
        if isinstance(v, str):
            return v
        values = info.data
        return PostgresDsn.build(
            scheme="postgresql",
            username=values.get("POSTGRES_USER"),
            password=values.get("POSTGRES_PASSWORD"),
            host=values.get("POSTGRES_SERVER"),
            path=f"/{values.get('POSTGRES_DB') or ''}",
        )

    # Redis
    REDIS_HOST: str
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: Optional[str] = None
    REDIS_DB: int = 0
    REDIS_URI: Optional[RedisDsn] = None

    @field_validator("REDIS_URI", mode="before")
    @classmethod
    def assemble_redis_connection(cls, v: Optional[str], info) -> Any:
        if isinstance(v, str):
            return v
        values = info.data
        return RedisDsn.build(
            scheme="redis",
            host=values.get("REDIS_HOST"),
            port=values.get("REDIS_PORT"),
            password=values.get("REDIS_PASSWORD"),
            path=f"/{values.get('REDIS_DB')}",
        )

    # Mailhog (for local development)
    MAILHOG_HOST: str = "mailhog"
    MAILHOG_PORT: int = 1025
    MAILHOG_USER: Optional[str] = None
    MAILHOG_PASSWORD: Optional[str] = None

    class Config:
        case_sensitive = True
        env_file = ".env"
        env_file_encoding = 'utf-8'
        # .env is shared with the web service via docker-compose's
        # env_file: (VITE_API_URL etc. are frontend-only) -- ignore keys
        # this Settings class doesn't itself declare, rather than crash
        # on every var meant for a different service.
        extra = "ignore"

settings = Settings()