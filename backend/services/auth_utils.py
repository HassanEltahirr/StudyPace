from __future__ import annotations

import hmac
import hashlib
import secrets
from datetime import datetime, timedelta

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from config import DEV_SECRET, is_production, secret_key

_security = HTTPBearer(auto_error=False)
_ALGORITHM = "HS256"
_TOKEN_DAYS = 7
_HASH_ALGORITHM = "pbkdf2_sha256"
_HASH_ITERATIONS = 310_000


def _secret() -> str:
    secret = secret_key()
    if is_production() and secret == DEV_SECRET:
        raise RuntimeError("SECRET_KEY must be set in production.")
    return secret


def create_token(username: str) -> str:
    expire = datetime.utcnow() + timedelta(days=_TOKEN_DAYS)
    return jwt.encode({"sub": username, "exp": expire}, _secret(), algorithm=_ALGORITHM)


def decode_username_from_token(token: str) -> str:
    payload = jwt.decode(token, _secret(), algorithms=[_ALGORITHM])
    username: str = payload.get("sub", "")
    if not username:
        raise JWTError("Missing subject")
    return username


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _HASH_ITERATIONS)
    return f"{_HASH_ALGORITHM}${_HASH_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations, salt_hex, digest_hex = stored_hash.split("$", 3)
        if algorithm != _HASH_ALGORITHM:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode(),
            bytes.fromhex(salt_hex),
            int(iterations),
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (TypeError, ValueError):
        return False


def require_auth(
    creds: HTTPAuthorizationCredentials | None = Depends(_security),
) -> str:
    if not creds:
        raise HTTPException(401, "Authentication required")
    try:
        return decode_username_from_token(creds.credentials)
    except JWTError:
        raise HTTPException(401, "Invalid or expired token")
