"""Central configuration. Everything is read from environment variables / .env."""
import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _bool(v, default=False):
    if v is None or v == "":
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


DATA_DIR = Path(os.getenv("AEOP_DATA_DIR") or (BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)


def _load_secret() -> str:
    s = os.getenv("SECRET_KEY")
    if s:
        return s
    if os.getenv("AEOP_ENV", "development") == "production":
        raise RuntimeError("SECRET_KEY must be set in production")
    f = DATA_DIR / ".secret_key"
    if f.exists():
        return f.read_text().strip()
    s = secrets.token_urlsafe(48)
    f.write_text(s)
    try:
        os.chmod(f, 0o600)
    except OSError:
        pass
    return s


class Settings:
    def __init__(self):
        e = os.getenv
        self.ENV = e("AEOP_ENV", "development")
        self.PRODUCTION = self.ENV == "production"
        self.HOST = e("HOST", "127.0.0.1")
        self.PORT = int(e("PORT", "8000"))
        self.BASE_URL = e("BASE_URL", f"http://localhost:{self.PORT}").rstrip("/")
        self.DATABASE_URL = e("DATABASE_URL") or f"sqlite:///{(DATA_DIR / 'aeop.db').as_posix()}"
        self.AUTO_CREATE_TABLES = _bool(e("AEOP_AUTO_CREATE_TABLES"), not self.PRODUCTION)
        self.SECRET_KEY = _load_secret()
        self.COOKIE_SECURE = _bool(e("COOKIE_SECURE"), self.PRODUCTION)
        self.ALLOW_SIGNUP = _bool(e("ALLOW_SIGNUP"), not self.PRODUCTION)
        self.ENABLE_DEMO = _bool(e("AEOP_ENABLE_DEMO"), True)
        self.ADMIN_EMAIL = e("ADMIN_EMAIL", "admin@aeop.local")
        self.ADMIN_PASSWORD = e("ADMIN_PASSWORD", "")
        self.SESSION_HOURS = int(e("SESSION_HOURS", "8"))
        # LLM
        self.LLM_PROVIDER = e("LLM_PROVIDER", "gemini" if e("GEMINI_API_KEY") else "none")
        self.GEMINI_API_KEY = e("GEMINI_API_KEY", "")
        self.GEMINI_MODEL = e("GEMINI_MODEL", "gemini-2.5-flash")
        self.LLM_TIMEOUT = float(e("LLM_TIMEOUT", "30"))
        self.LLM_PRICE_IN_PER_1K = float(e("LLM_PRICE_IN_PER_1K", "0"))
        self.LLM_PRICE_OUT_PER_1K = float(e("LLM_PRICE_OUT_PER_1K", "0"))
        # Email providers
        self.GOOGLE_CLIENT_ID = e("GOOGLE_CLIENT_ID", "")
        self.GOOGLE_CLIENT_SECRET = e("GOOGLE_CLIENT_SECRET", "")
        self.GMAIL_PUBSUB_TOPIC = e("GMAIL_PUBSUB_TOPIC", "")
        self.MS_CLIENT_ID = e("MS_CLIENT_ID", "")
        self.MS_CLIENT_SECRET = e("MS_CLIENT_SECRET", "")
        self.MS_TENANT = e("MS_TENANT", "common")
        self.WEBHOOK_SECRET = e("WEBHOOK_SECRET", "")
        # Worker
        self.WORKER_ENABLED = _bool(e("WORKER_ENABLED"), True)
        self.WORKER_THREADS = int(e("WORKER_THREADS", "2"))
        self.ATTACH_MAX_MB = int(e("ATTACH_MAX_MB", "10"))


settings = Settings()
