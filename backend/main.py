"""
main.py — FastAPI application entry point.
"""

import threading
import time
import os
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.gzip import GZipMiddleware

from config import cors_origins, is_production, validate_production_config
from database import (
    engine,
    SessionLocal,
    Base,
    ensure_schema_compatibility,
    get_db,
    reset_request_username,
    set_request_username,
)
from models import UserSettings
from routers import courses, topics, sessions, quiz, assessments, planner, learning, settings as settings_router
from routers import auth as auth_router
from routers import blackboard as blackboard_router
from routers.calls import router as calls_router, webhook_router as calls_webhook_router
from services.auth_utils import decode_username_from_token, require_auth


def _frontend_dir() -> Path | None:
    raw = os.getenv("STUDYPACE_STATIC_DIR", "").strip()
    if not raw:
        return None
    path = Path(raw)
    if path.exists() and (path / "index.html").exists():
        return path
    return None


# ── Rate limiting ────────────────────────────────────────────────────────────
_rate_store: dict[str, list[float]] = defaultdict(list)
_rate_lock = threading.Lock()
# (path_prefix, max_requests, window_seconds)
_RATE_RULES = [
    ("/api/auth/register", 5, 60),
    ("/api/auth/login", 10, 60),
    ("/api/auth/google", 20, 60),
    ("/api/learning/ai/", 20, 60),
]


class _RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        for prefix, max_req, window in _RATE_RULES:
            if request.url.path.startswith(prefix):
                ip = (request.client.host if request.client else "unknown")
                key = f"{ip}:{prefix}"
                now = time.time()
                with _rate_lock:
                    times = [t for t in _rate_store[key] if now - t < window]
                    if len(times) >= max_req:
                        return JSONResponse(
                            {"detail": "Rate limit exceeded. Please slow down."},
                            status_code=429,
                        )
                    times.append(now)
                    _rate_store[key] = times
                break
        return await call_next(request)


class _WorkspaceMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        username = None
        authorization = request.headers.get("authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() == "bearer" and token:
            try:
                username = decode_username_from_token(token)
            except Exception:
                username = None

        context_token = set_request_username(username)
        try:
            return await call_next(request)
        finally:
            reset_request_username(context_token)


class _CacheHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/assets/"):
            response.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
        elif not path.startswith("/api/") and path not in {"/health", "/ready"}:
            response.headers.setdefault("Cache-Control", "public, max-age=0, must-revalidate")
        return response


# ── Startup ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    ensure_schema_compatibility(engine)
    _ensure_auth_schema()
    _ensure_settings_schema()
    _seed(SessionLocal())
    _start_call_scheduler()
    yield


def _ensure_auth_schema():
    """Backfill local auth columns when upgrading an existing SQLite database."""
    if engine.dialect.name != "sqlite":
        return
    try:
        columns = {column["name"] for column in inspect(engine).get_columns("users")}
        statements = []
        if "first_name" not in columns:
            statements.append("ALTER TABLE users ADD COLUMN first_name VARCHAR(80) NOT NULL DEFAULT ''")
        if "last_name" not in columns:
            statements.append("ALTER TABLE users ADD COLUMN last_name VARCHAR(80) NOT NULL DEFAULT ''")
        if not statements:
            return
        with engine.begin() as connection:
            for statement in statements:
                connection.execute(text(statement))
    except Exception as e:
        print(f"Auth schema migration error (non-fatal): {e}")


def _ensure_settings_schema():
    """Backfill call-reminder columns added after initial schema creation."""
    if engine.dialect.name != "sqlite":
        return
    try:
        columns = {col["name"] for col in inspect(engine).get_columns("user_settings")}
        statements = []
        if "phone_number" not in columns:
            statements.append("ALTER TABLE user_settings ADD COLUMN phone_number VARCHAR(30) NOT NULL DEFAULT ''")
        if "call_reminder_enabled" not in columns:
            statements.append("ALTER TABLE user_settings ADD COLUMN call_reminder_enabled BOOLEAN NOT NULL DEFAULT 0")
        if "call_reminder_hour" not in columns:
            statements.append("ALTER TABLE user_settings ADD COLUMN call_reminder_hour INTEGER NOT NULL DEFAULT 8")
        if not statements:
            return
        with engine.begin() as connection:
            for statement in statements:
                connection.execute(text(statement))
    except Exception as e:
        print(f"Settings schema migration error (non-fatal): {e}")


def _start_call_scheduler():
    """Background thread that fires daily study-reminder calls at the configured hour."""
    from config import twilio_call_time

    _fired_dates: set[str] = set()

    def _loop():
        import datetime as dt
        while True:
            time.sleep(60)
            try:
                now = dt.datetime.now()
                today_key = now.strftime("%Y-%m-%d")
                if today_key in _fired_dates:
                    continue

                target_hhmm = twilio_call_time()  # re-read each cycle so env changes take effect
                try:
                    target_h, target_m = (int(x) for x in target_hhmm.split(":"))
                except ValueError:
                    continue

                if now.hour != target_h or now.minute != target_m:
                    continue

                _fired_dates.add(today_key)

                db = SessionLocal()
                try:
                    s = db.get(UserSettings, 1)
                    if not s or not s.call_reminder_enabled or not s.phone_number:
                        continue
                    from services.twilio_calls import place_outbound_call
                    place_outbound_call(s.phone_number, db)
                except Exception as exc:
                    print(f"Scheduled call error: {exc}")
                finally:
                    db.close()
            except Exception as exc:
                print(f"Call scheduler loop error: {exc}")

    t = threading.Thread(target=_loop, daemon=True, name="call-scheduler")
    t.start()


def _seed(db: Session):
    """Create default settings on first run. Courses are added by the user."""
    try:
        if db.query(UserSettings).count() == 0:
            db.add(UserSettings(id=1, daily_minutes=135, max_course_pct=0.6, streak=0))
            db.commit()
    except Exception as e:
        db.rollback()
        print(f"Seed error (non-fatal): {e}")
    finally:
        db.close()


# ── App ──────────────────────────────────────────────────────────────────────
validate_production_config()

app = FastAPI(
    title="StudyPace API",
    lifespan=lifespan,
    docs_url=None if is_production() else "/docs",
    redoc_url=None if is_production() else "/redoc",
    openapi_url=None if is_production() else "/openapi.json",
)

_allowed_origins = cors_origins()

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(_CacheHeadersMiddleware)
app.add_middleware(_RateLimitMiddleware)
app.add_middleware(_WorkspaceMiddleware)

# ── Public routes (no auth) ──────────────────────────────────────────────────
app.include_router(auth_router.router, prefix="/api/auth", tags=["auth"])


@app.get("/health")
def health():
    return {"status": "ok", "service": "StudyPace API"}


@app.get("/ready")
def ready(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ready", "database": "ok"}


@app.get("/")
def root():
    frontend = _frontend_dir()
    if frontend:
        return FileResponse(frontend / "index.html")
    payload = {"message": "StudyPace API running"}
    if not is_production():
        payload["docs"] = "/docs"
    return payload


# ── Protected routes (require JWT) ──────────────────────────────────────────
_auth = [Depends(require_auth)]

app.include_router(courses.router,          prefix="/api/courses",     tags=["courses"],     dependencies=_auth)
app.include_router(topics.router,           prefix="/api/topics",      tags=["topics"],      dependencies=_auth)
app.include_router(sessions.router,         prefix="/api/sessions",    tags=["sessions"],    dependencies=_auth)
app.include_router(quiz.router,             prefix="/api/quiz",        tags=["quiz"],        dependencies=_auth)
app.include_router(assessments.router,      prefix="/api/assessments", tags=["assessments"], dependencies=_auth)
app.include_router(planner.router,          prefix="/api/plan",        tags=["plan"],        dependencies=_auth)
app.include_router(learning.router,         prefix="/api/learning",    tags=["learning"],    dependencies=_auth)
app.include_router(settings_router.router,  prefix="/api/settings",    tags=["settings"],    dependencies=_auth)
app.include_router(blackboard_router.router, prefix="/api/blackboard",  tags=["blackboard"],  dependencies=_auth)
app.include_router(calls_router,         prefix="/api/calls",  tags=["calls"],   dependencies=_auth)
app.include_router(calls_webhook_router, prefix="/api/calls",  tags=["calls-webhook"])


_frontend = _frontend_dir()
if _frontend:
    assets_dir = _frontend / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="frontend-assets")

    @app.get("/{path:path}", include_in_schema=False)
    def frontend_app(path: str):
        if path.startswith(("api/", "health", "ready")):
            raise HTTPException(404, "Not found")
        candidate = (_frontend / path).resolve()
        if candidate.is_file() and _frontend in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(_frontend / "index.html")
