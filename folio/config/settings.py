import os
from datetime import timedelta

from dotenv import load_dotenv

load_dotenv()

class BaseConfig:
    """Base configuration shared by all environments."""

    SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "change-me-in-production")
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = False
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)

    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///folio.db")
    STORAGE_ROOT = os.getenv("STORAGE_ROOT", "storage")

    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4")

    RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
    RESEND_FROM_EMAIL = os.getenv("RESEND_FROM_EMAIL", "")

    APP_URL = os.getenv("APP_URL", "http://localhost:5000")
    SUPPORTED_LANGUAGES = ["en", "de"]
    DEFAULT_LANGUAGE = "en"

    RATELIMIT_DEFAULT = "200 per day;50 per hour"
    RATELIMIT_STORAGE_URI = "memory://"


class DevelopmentConfig(BaseConfig):
    DEBUG = True
    ENV = "development"


class ProductionConfig(BaseConfig):
    DEBUG = False
    ENV = "production"
    SESSION_COOKIE_SECURE = True


class TestingConfig(BaseConfig):
    TESTING = True
    DEBUG = False
    ENV = "testing"


CONFIG_MAP = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}


def get_config(config_name: str | None = None):
    env_name = config_name or os.getenv("FLASK_ENV", "development")
    return CONFIG_MAP.get(env_name, DevelopmentConfig)
