"""Single entry point:  python main.py   ->  starts API + background worker + dashboard on http://127.0.0.1:8000"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _ensure_deps():
    try:
        import fastapi, uvicorn, sqlalchemy, jwt, cryptography, httpx, dotenv, langgraph, pypdf  # noqa: F401
        import multipart, email_validator, tzdata  # noqa: F401
    except ImportError:
        print("Installing dependencies from requirements.txt (first run only)...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")])


if __name__ == "__main__":
    _ensure_deps()
    import uvicorn
    from app.config import settings
    print(f"\n  Autonomous Email Operations Platform -> http://{settings.HOST}:{settings.PORT}   (API docs: /api/docs)\n")
    uvicorn.run("app.main:app", host=settings.HOST, port=settings.PORT, reload=False, log_level="warning")
