import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import models  # noqa: F401  (register tables)
from .api import router
from .config import BASE_DIR, settings
from .db import Base, engine
from .errors import AEOPError, Conflict, InvalidTransition, NotFound, PermanentError, PermissionDenied
from .seed import bootstrap_admin
from .worker import Worker

logging.basicConfig(level=logging.INFO, format="%(message)s")
access = logging.getLogger("aeop.access")
STATIC = BASE_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.AUTO_CREATE_TABLES:
        Base.metadata.create_all(engine)
    res = bootstrap_admin()
    if res and res != "env":
        print(f"\n  First run: admin credentials were written to {res}\n")
    app.state.worker = None
    if settings.WORKER_ENABLED:
        app.state.worker = Worker(settings.WORKER_THREADS)
        app.state.worker.start()
    yield
    if app.state.worker:
        app.state.worker.shutdown()


app = FastAPI(title="Autonomous Email Operations Platform", version="1.0.0", lifespan=lifespan,
              docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)
app.include_router(router)

CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
       "frame-ancestors 'none'; base-uri 'self'; form-action 'self'")


@app.middleware("http")
async def mw(request: Request, call_next):
    rid, t0 = uuid.uuid4().hex[:12], time.perf_counter()
    try:
        resp = await call_next(request)
    except Exception:
        access.exception(json.dumps({"rid": rid, "path": request.url.path, "event": "unhandled_error"}))
        resp = JSONResponse({"detail": "Internal server error", "request_id": rid}, status_code=500)
    resp.headers.update({"X-Request-ID": rid, "X-Content-Type-Options": "nosniff", "Referrer-Policy": "same-origin",
                         "X-Frame-Options": "DENY", "Cache-Control": "no-store" if request.url.path.startswith("/api") else "no-cache"})
    if not request.url.path.startswith("/api/docs") and not request.url.path.startswith("/api/openapi"):
        resp.headers["Content-Security-Policy"] = CSP
    if settings.COOKIE_SECURE:
        resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    access.info(json.dumps({"rid": rid, "method": request.method, "path": request.url.path, "status": resp.status_code,
                            "ms": round((time.perf_counter() - t0) * 1000, 1)}))
    return resp


@app.exception_handler(AEOPError)
async def aeop_handler(request, exc: AEOPError):
    code = 403 if isinstance(exc, PermissionDenied) else 404 if isinstance(exc, NotFound) else 409 if isinstance(exc, (Conflict, InvalidTransition)) \
        else 422 if isinstance(exc, PermanentError) else 400
    return JSONResponse({"detail": str(exc)}, status_code=code)


app.mount("/assets", StaticFiles(directory=STATIC), name="assets")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")
