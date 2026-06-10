"""
database.py — SQLAlchemy engine + session setup.

WHY SQLAlchemy: it's the standard Python ORM. We use the newer 2.0-style
`Session` (not the legacy scoped_session) and let FastAPI manage lifetimes
via dependency injection — each request gets its own session, auto-closed
on response, preventing connection leaks.
"""

import os
import re
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import NoSuchTableError
from sqlalchemy.pool import NullPool
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from config import data_dir

DEFAULT_DATA_DIR = data_dir()
DEFAULT_DB_PATH = DEFAULT_DATA_DIR / "studypace_local.db"
USER_WORKSPACE_DIR = DEFAULT_DATA_DIR / "user_workspaces"
DEFAULT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
USER_WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DEFAULT_DB_PATH}")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
_REQUEST_USERNAME: ContextVar[str | None] = ContextVar("studypace_request_username", default=None)
IS_SQLITE = DATABASE_URL.startswith("sqlite")
IS_POSTGRES = DATABASE_URL.startswith(("postgresql://", "postgresql+"))
_SCHEMA_LOCKS: dict[str, threading.Lock] = {}
_SCHEMA_LOCKS_GUARD = threading.Lock()
_READY_POSTGRES_SCHEMAS: set[str] = set()


def _base_engine_kwargs(url: str) -> dict:
    kwargs = {"echo": False}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        kwargs["poolclass"] = NullPool
    else:
        kwargs["pool_pre_ping"] = True
    return kwargs


engine_kwargs = _base_engine_kwargs(DATABASE_URL)
if IS_SQLITE:
    engine_kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    engine_kwargs["poolclass"] = NullPool

engine = create_engine(DATABASE_URL, **engine_kwargs)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    """All models inherit from this — gives us metadata and the ORM registry."""
    pass


def set_request_username(username: str | None):
    normalized = (username or "").strip().lower() or None
    return _REQUEST_USERNAME.set(normalized)


def reset_request_username(token) -> None:
    _REQUEST_USERNAME.reset(token)


def current_request_username() -> str | None:
    return _REQUEST_USERNAME.get()


def _default_workspace_username() -> str:
    return os.getenv("STUDYPACE_USERNAME", "admin").strip().lower() or "admin"


def is_default_workspace_username(username: str | None) -> bool:
    normalized = (username or "").strip().lower()
    return not normalized or normalized == _default_workspace_username()


def _engine_kwargs_for(url: str) -> dict:
    kwargs = _base_engine_kwargs(url)
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        kwargs["poolclass"] = NullPool
    return kwargs


def _safe_workspace_name(username: str) -> str:
    safe = re.sub(r"[^a-z0-9_.-]+", "_", username.lower()).strip("._-")
    return safe or "user"


def workspace_storage_name(username: str | None) -> str:
    if is_default_workspace_username(username):
        return _safe_workspace_name(_default_workspace_username())
    return _safe_workspace_name(username or "user")


def _workspace_schema_name(username: str) -> str:
    safe = re.sub(r"[^a-z0-9_]+", "_", username.lower()).strip("_")
    return f"sp_{safe or 'user'}"


def _quote_ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _qualified_table(table: str, schema: str | None = None) -> str:
    if IS_POSTGRES and schema:
        return f"{_quote_ident(schema)}.{_quote_ident(table)}"
    return _quote_ident(table)


def _columns_for(connection, table: str, schema: str | None = None) -> set[str]:
    try:
        return {
            column["name"]
            for column in inspect(connection).get_columns(
                table,
                schema=schema if IS_POSTGRES and schema else None,
            )
        }
    except NoSuchTableError:
        return set()


def ensure_schema_compatibility(bind=engine, schema: str | None = None) -> None:
    """Backfill columns added after early beta schemas were created."""
    owns_connection = isinstance(bind, Engine)
    context = bind.begin() if owns_connection else None
    connection = None
    exc_info = (None, None, None)
    try:
        connection = context.__enter__() if context else bind
        dialect = connection.dialect.name
        if dialect == "postgresql":
            statements = [
                f"ALTER TABLE IF EXISTS {_qualified_table('users', schema)} ADD COLUMN IF NOT EXISTS email VARCHAR(255)",
                f"ALTER TABLE IF EXISTS {_qualified_table('users', schema)} ADD COLUMN IF NOT EXISTS first_name VARCHAR(80) NOT NULL DEFAULT ''",
                f"ALTER TABLE IF EXISTS {_qualified_table('users', schema)} ADD COLUMN IF NOT EXISTS last_name VARCHAR(80) NOT NULL DEFAULT ''",
                f"ALTER TABLE IF EXISTS {_qualified_table('users', schema)} ADD COLUMN IF NOT EXISTS password_reset_token_hash VARCHAR(128) NOT NULL DEFAULT ''",
                f"ALTER TABLE IF EXISTS {_qualified_table('users', schema)} ADD COLUMN IF NOT EXISTS password_reset_expires_at TIMESTAMP",
                f"ALTER TABLE IF EXISTS {_qualified_table('user_settings', schema)} ADD COLUMN IF NOT EXISTS phone_number VARCHAR(30) NOT NULL DEFAULT ''",
                f"ALTER TABLE IF EXISTS {_qualified_table('user_settings', schema)} ADD COLUMN IF NOT EXISTS call_reminder_enabled BOOLEAN NOT NULL DEFAULT false",
                f"ALTER TABLE IF EXISTS {_qualified_table('user_settings', schema)} ADD COLUMN IF NOT EXISTS call_reminder_hour INTEGER NOT NULL DEFAULT 8",
                f"ALTER TABLE IF EXISTS {_qualified_table('lectures', schema)} ADD COLUMN IF NOT EXISTS ai_summary TEXT NOT NULL DEFAULT ''",
                f"ALTER TABLE IF EXISTS {_qualified_table('lectures', schema)} ADD COLUMN IF NOT EXISTS video_recs_json TEXT NOT NULL DEFAULT ''",
            ]
            for statement in statements:
                connection.execute(text(statement))
            index_name = _quote_ident(f"ix_{schema or 'public'}_users_email_unique")
            connection.execute(
                text(
                    f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name} "
                    f"ON {_qualified_table('users', schema)} (email) "
                    "WHERE email IS NOT NULL AND email <> ''"
                )
            )
            return

        bool_default = "false" if dialect == "postgresql" else "0"
        migrations = {
            "users": [
                ("email", "VARCHAR(255)"),
                ("first_name", "VARCHAR(80) NOT NULL DEFAULT ''"),
                ("last_name", "VARCHAR(80) NOT NULL DEFAULT ''"),
                ("password_reset_token_hash", "VARCHAR(128) NOT NULL DEFAULT ''"),
                ("password_reset_expires_at", "DATETIME"),
            ],
            "user_settings": [
                ("phone_number", "VARCHAR(30) NOT NULL DEFAULT ''"),
                ("call_reminder_enabled", f"BOOLEAN NOT NULL DEFAULT {bool_default}"),
                ("call_reminder_hour", "INTEGER NOT NULL DEFAULT 8"),
            ],
            "lectures": [
                ("ai_summary", "TEXT NOT NULL DEFAULT ''"),
                ("video_recs_json", "TEXT NOT NULL DEFAULT ''"),
            ],
        }

        for table, expected_columns in migrations.items():
            columns = _columns_for(connection, table, schema)
            if not columns:
                continue
            qualified = _qualified_table(table, schema)
            for column_name, column_definition in expected_columns:
                if column_name not in columns:
                    connection.execute(
                        text(f"ALTER TABLE {qualified} ADD COLUMN {column_name} {column_definition}")
                    )
        if "email" in _columns_for(connection, "users", schema):
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email_unique "
                    "ON users(email) WHERE email IS NOT NULL AND email <> ''"
                )
            )
    except Exception as exc:
        exc_info = (type(exc), exc, exc.__traceback__)
        raise
    finally:
        if context:
            context.__exit__(*exc_info)


def _lock_for_schema(schema: str) -> threading.Lock:
    with _SCHEMA_LOCKS_GUARD:
        lock = _SCHEMA_LOCKS.get(schema)
        if lock is None:
            lock = threading.Lock()
            _SCHEMA_LOCKS[schema] = lock
        return lock


def workspace_root_for_username(username: str | None) -> Path:
    if is_default_workspace_username(username):
        return DEFAULT_DATA_DIR
    return USER_WORKSPACE_DIR / _safe_workspace_name(username or "user")


def current_workspace_data_dir() -> Path:
    root = workspace_root_for_username(current_request_username())
    root.mkdir(parents=True, exist_ok=True)
    return root


@lru_cache(maxsize=128)
def _ensure_postgres_workspace_schema(schema: str) -> None:
    if not IS_POSTGRES or schema == "public":
        return
    if schema in _READY_POSTGRES_SCHEMAS:
        return
    with _lock_for_schema(schema):
        if schema in _READY_POSTGRES_SCHEMAS:
            return
        quoted = _quote_ident(schema)
        with engine.begin() as connection:
            connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {quoted}"))
            existing_courses_table = connection.execute(
                text("SELECT to_regclass(:table_name)"),
                {"table_name": f"{schema}.courses"},
            ).scalar()
            if existing_courses_table:
                ensure_schema_compatibility(connection, schema)
            else:
                connection.execute(text(f"SET search_path TO {quoted}"))
                Base.metadata.create_all(bind=connection, checkfirst=True)
                ensure_schema_compatibility(connection, schema)
                connection.execute(text("RESET search_path"))
        _READY_POSTGRES_SCHEMAS.add(schema)


def _postgres_workspace_url(schema: str) -> str:
    separator = "&" if "?" in DATABASE_URL else "?"
    return f"{DATABASE_URL}{separator}options=-csearch_path%3D{schema}"


@lru_cache(maxsize=128)
def _sessionmaker_for_workspace(username: str):
    if is_default_workspace_username(username):
        return SessionLocal

    if IS_POSTGRES:
        schema = _workspace_schema_name(username)
        _ensure_postgres_workspace_schema(schema)
        workspace_engine = create_engine(_postgres_workspace_url(schema), **_base_engine_kwargs(DATABASE_URL))
        return sessionmaker(autocommit=False, autoflush=False, bind=workspace_engine)

    db_path = workspace_root_for_username(username) / "studypace_local.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    url = f"sqlite:///{db_path}"
    workspace_engine = create_engine(url, **_engine_kwargs_for(url))
    Base.metadata.create_all(bind=workspace_engine, checkfirst=True)
    ensure_schema_compatibility(workspace_engine)
    return sessionmaker(autocommit=False, autoflush=False, bind=workspace_engine)


@contextmanager
def workspace_session():
    """
    Open a DB session for the current workspace.

    Postgres uses one shared connection pool and sets search_path at the start
    of every request. That avoids a separate engine per user and avoids a costly
    reset round-trip after read-heavy requests.
    """
    username = current_request_username() or ""
    schema = None
    if IS_POSTGRES:
        schema = "public" if is_default_workspace_username(username) else _workspace_schema_name(username)
        if schema != "public":
            _ensure_postgres_workspace_schema(schema)
        session_factory = SessionLocal
    else:
        session_factory = _sessionmaker_for_workspace(username)

    db = session_factory()
    if IS_POSTGRES and schema:
        db.execute(text(f"SET search_path TO {_quote_ident(schema)}"))
    try:
        yield db
    finally:
        db.close()


def get_db():
    """
    FastAPI dependency: yields a DB session for the duration of a request,
    then closes it. Use with `db: Session = Depends(get_db)` in route handlers.
    """
    with workspace_session() as db:
        yield db


def prime_workspace(username: str | None) -> None:
    """Create and warm the user's workspace before the first app screen needs it."""
    if not username:
        return
    if IS_POSTGRES:
        schema = "public" if is_default_workspace_username(username) else _workspace_schema_name(username)
        if schema != "public":
            _ensure_postgres_workspace_schema(schema)
        db = SessionLocal()
        try:
            db.execute(text(f"SET search_path TO {_quote_ident(schema)}"))
            db.execute(text("SELECT 1"))
        finally:
            db.close()
        return
    session_factory = _sessionmaker_for_workspace(username)
    db = session_factory()
    try:
        db.execute(text("SELECT 1"))
    finally:
        db.close()
