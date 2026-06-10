import hmac
import os
import re
import threading

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from config import local_admin_enabled
from database import SessionLocal, prime_workspace
from models import User
from services.auth_utils import create_token, hash_password, require_auth, verify_password
from services.simple_cache import get_cached, set_cached

router = APIRouter()


class LoginBody(BaseModel):
    username: str
    password: str


class RegisterBody(BaseModel):
    username: str
    password: str
    first_name: str
    last_name: str


class GoogleLoginBody(BaseModel):
    credential: str


USERNAME_RE = re.compile(r"^[a-z0-9_.@-]{3,80}$")


def get_auth_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post("/login")
def login(body: LoginBody, db: Session = Depends(get_auth_db)):
    username = _normalize_username(body.username)
    password = body.password or ""
    user = db.query(User).filter(User.username == username).first()
    if user and verify_password(password, user.password_hash):
        _warm_workspace(user.username)
        return {"access_token": create_token(user.username), "token_type": "bearer"}

    if not local_admin_enabled():
        raise HTTPException(401, "Invalid credentials")

    admin_user = os.getenv("STUDYPACE_USERNAME", "admin")
    admin_pass = os.getenv("STUDYPACE_PASSWORD", "studypace")
    user_ok = hmac.compare_digest((body.username or "").strip().encode(), admin_user.encode())
    pass_ok = hmac.compare_digest(password.encode(), admin_pass.encode())
    if not (user_ok and pass_ok):
        raise HTTPException(401, "Invalid credentials")
    _warm_workspace(admin_user)
    return {"access_token": create_token(admin_user), "token_type": "bearer"}


@router.post("/register")
def register(body: RegisterBody, db: Session = Depends(get_auth_db)):
    username = _normalize_username(body.username)
    password = body.password or ""
    first_name = _normalize_person_name(body.first_name, "First name")
    last_name = _normalize_person_name(body.last_name, "Last name")
    if len(username) < 3:
        raise HTTPException(422, "Username must be at least 3 characters.")
    if not USERNAME_RE.fullmatch(username):
        raise HTTPException(422, "Username can use letters, numbers, dots, underscores, hyphens, or @.")
    if len(password) < 8:
        raise HTTPException(422, "Password must be at least 8 characters.")
    if username == _reserved_local_username():
        raise HTTPException(409, "That username is reserved for the local admin account.")
    if db.query(User).filter(User.username == username).first():
        raise HTTPException(409, "That username is already taken.")

    user = User(
        username=username,
        first_name=first_name,
        last_name=last_name,
        password_hash=hash_password(password),
    )
    db.add(user)
    db.commit()
    # Use the local string, not user.username: after commit the ORM instance is
    # expired and reading the attribute issues a refresh query, which 500s when
    # the session's connection has gone stale (seen in production logs).
    _warm_workspace(username)
    return {"access_token": create_token(username), "token_type": "bearer"}


@router.get("/config")
def auth_config():
    client_id = _google_client_id()
    return {
        "google_enabled": bool(client_id),
        "google_client_id": client_id,
    }


@router.post("/google")
def google_login(body: GoogleLoginBody):
    client_id = _google_client_id()
    if not client_id:
        raise HTTPException(503, "Google sign-in is not configured.")

    credential = body.credential.strip()
    if not credential:
        raise HTTPException(422, "Missing Google credential.")

    profile = _verify_google_credential(credential, client_id)
    email = str(profile.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(401, "Google did not return an email address.")
    if not profile.get("email_verified"):
        raise HTTPException(401, "Google email is not verified.")
    if not _google_user_allowed(email, profile):
        raise HTTPException(403, "This Google account is not allowed to use StudyPace.")

    return {
        "access_token": create_token(email),
        "token_type": "bearer",
        "user": {
            "email": email,
            "name": profile.get("name") or email,
            "picture": profile.get("picture") or "",
        },
    }


@router.get("/me")
def me(username: str = Depends(require_auth)):
    key = ("auth:me", username)
    cached = get_cached(key)
    if cached is not None:
        return cached
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
    finally:
        db.close()
    if not user:
        return {"username": username, "first_name": "", "last_name": ""}
    payload = {
        "username": user.username,
        "first_name": user.first_name or "",
        "last_name": user.last_name or "",
    }
    set_cached(key, payload, ttl_seconds=300)
    return payload


def _google_client_id() -> str:
    return os.getenv("GOOGLE_CLIENT_ID", "").strip()


def _normalize_username(value: str) -> str:
    return (value or "").strip().lower()


def _normalize_person_name(value: str, label: str) -> str:
    cleaned = re.sub(r"\s+", " ", value or "").strip()
    if not cleaned:
        raise HTTPException(422, f"{label} is required.")
    if len(cleaned) > 80:
        raise HTTPException(422, f"{label} must be 80 characters or fewer.")
    if any(ord(ch) < 32 for ch in cleaned):
        raise HTTPException(422, f"{label} has invalid characters.")
    return cleaned


def _reserved_local_username() -> str:
    return os.getenv("STUDYPACE_USERNAME", "admin").strip().lower() or "admin"


def _warm_workspace(username: str) -> None:
    def run() -> None:
        try:
            prime_workspace(username)
        except Exception as exc:
            print(f"Workspace warmup failed for {username}: {exc}")

    threading.Thread(target=run, daemon=True).start()


def _verify_google_credential(credential: str, client_id: str) -> dict:
    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token
    except ImportError as exc:
        raise HTTPException(503, "Install google-auth to enable Google sign-in.") from exc

    try:
        return id_token.verify_oauth2_token(credential, google_requests.Request(), client_id)
    except ValueError as exc:
        raise HTTPException(401, "Invalid Google sign-in token.") from exc


def _google_user_allowed(email: str, profile: dict) -> bool:
    allowed_emails = {
        item.strip().lower()
        for item in os.getenv("GOOGLE_ALLOWED_EMAILS", "").split(",")
        if item.strip()
    }
    if allowed_emails and email not in allowed_emails:
        return False

    allowed_domain = (
        os.getenv("GOOGLE_ALLOWED_DOMAIN", "")
        or os.getenv("GOOGLE_HOSTED_DOMAIN", "")
    ).strip().lower().lstrip("@")
    if allowed_domain:
        email_domain = email.rsplit("@", 1)[-1]
        hosted_domain = str(profile.get("hd") or "").strip().lower()
        return email_domain == allowed_domain and (not hosted_domain or hosted_domain == allowed_domain)

    return True
