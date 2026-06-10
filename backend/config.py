from __future__ import annotations

import os
from pathlib import Path


DEV_SECRET = "dev-only-secret-change-in-production-must-be-32chars!"
DEFAULT_DATA_DIR = Path(__file__).resolve().parent / "data"
DEFAULT_CORS_ORIGINS = "http://localhost:5173,http://localhost:3000"
ALLOWED_UPLOAD_EXTENSIONS = {".pdf", ".ppt", ".pptx", ".docx", ".txt", ".md"}


def env_name() -> str:
    return (os.getenv("STUDYPACE_ENV") or os.getenv("ENV") or "development").strip().lower()


def is_production() -> bool:
    return env_name() in {"prod", "production"}


def bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def data_dir() -> Path:
    return Path(os.getenv("STUDYPACE_DATA_DIR", str(DEFAULT_DATA_DIR))).expanduser()


def secret_key() -> str:
    return os.getenv("SECRET_KEY", DEV_SECRET)


def local_admin_enabled() -> bool:
    default = not is_production()
    return bool_env("STUDYPACE_LOCAL_ADMIN_ENABLED", default)


def cors_origins() -> list[str]:
    return [
        origin.strip()
        for origin in os.getenv("CORS_ORIGINS", DEFAULT_CORS_ORIGINS).split(",")
        if origin.strip()
    ]


def max_upload_bytes() -> int:
    try:
        mb = int(os.getenv("MAX_UPLOAD_MB", "50"))
    except (TypeError, ValueError):
        mb = 50
    return max(1, min(mb, 200)) * 1024 * 1024


def twilio_account_sid() -> str | None:
    return os.getenv("TWILIO_ACCOUNT_SID") or None


def twilio_auth_token() -> str | None:
    return os.getenv("TWILIO_AUTH_TOKEN") or None


def twilio_from_number() -> str | None:
    return os.getenv("TWILIO_FROM_NUMBER") or None


def twilio_ar_voice() -> str:
    return os.getenv("TWILIO_AR_VOICE", "Polly.Hala-Neural")


def twilio_call_time() -> str:
    return os.getenv("TWILIO_CALL_TIME", "08:00")


def twilio_webhook_base_url() -> str | None:
    """Public base URL reachable by Twilio (e.g. https://xxx.ngrok-free.app)."""
    return (os.getenv("TWILIO_WEBHOOK_BASE_URL") or "").rstrip("/") or None


def elevenlabs_api_key() -> str | None:
    return os.getenv("ELEVENLABS_API_KEY") or None


def elevenlabs_voice_id() -> str | None:
    return os.getenv("ELEVENLABS_VOICE_ID") or None


def twilio_configured() -> bool:
    return bool(twilio_account_sid() and twilio_auth_token() and twilio_from_number())


def validate_production_config() -> None:
    if not is_production():
        return

    problems: list[str] = []
    secret = os.getenv("SECRET_KEY", "")
    if not secret or secret == DEV_SECRET or len(secret) < 32:
        problems.append("set SECRET_KEY to a unique random value with at least 32 characters")

    origins = cors_origins()
    if not os.getenv("CORS_ORIGINS") or "*" in origins:
        problems.append("set CORS_ORIGINS to your exact production frontend origin")

    if local_admin_enabled():
        admin_password = os.getenv("STUDYPACE_PASSWORD", "")
        if not admin_password or admin_password == "studypace" or admin_password == "changeme":
            problems.append("disable STUDYPACE_LOCAL_ADMIN_ENABLED or set a non-default STUDYPACE_PASSWORD")

    if problems:
        raise RuntimeError("Unsafe production configuration: " + "; ".join(problems) + ".")
