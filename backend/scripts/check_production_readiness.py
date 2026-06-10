from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

try:
    from dotenv import load_dotenv
    load_dotenv(BACKEND / ".env")
except Exception:
    pass

from config import DEV_SECRET, cors_origins, data_dir, env_name, local_admin_enabled, max_upload_bytes


def main() -> int:
    checks: list[tuple[str, str, str]] = []
    env = env_name()
    prod = env in {"prod", "production"}

    checks.append(("pass" if prod else "warn", "Environment", f"STUDYPACE_ENV={env}."))

    secret = os.getenv("SECRET_KEY", "")
    if secret and secret != DEV_SECRET and len(secret) >= 32:
        checks.append(("pass", "JWT secret", "SECRET_KEY is set and long enough."))
    else:
        checks.append(("fail" if prod else "warn", "JWT secret", "Set a unique SECRET_KEY with at least 32 characters."))

    origins = cors_origins()
    if origins and "*" not in origins and os.getenv("CORS_ORIGINS"):
        checks.append(("pass", "CORS", f"{len(origins)} explicit origin(s) configured."))
    else:
        checks.append(("fail" if prod else "warn", "CORS", "Set CORS_ORIGINS to the exact deployed frontend origin."))

    if local_admin_enabled():
        password = os.getenv("STUDYPACE_PASSWORD", "")
        if prod and (not password or password in {"studypace", "changeme"}):
            checks.append(("fail", "Local admin", "Disable STUDYPACE_LOCAL_ADMIN_ENABLED or set a non-default admin password."))
        else:
            checks.append(("warn", "Local admin", "Local admin fallback is enabled. Disable it for public production."))
    else:
        checks.append(("pass", "Local admin", "Local admin fallback is disabled."))

    db_url = os.getenv("DATABASE_URL", f"sqlite:///{data_dir() / 'studypace_local.db'}")
    if db_url.startswith("sqlite"):
        raw_db_path = db_url.replace("sqlite:///", "", 1)
        db_path = Path(raw_db_path)
        if not db_path.is_absolute():
            db_path = ROOT / raw_db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        writable = os.access(db_path.parent, os.W_OK)
        status = "pass" if writable else "fail"
        checks.append((status, "Database storage", f"SQLite directory is {'writable' if writable else 'not writable'}."))
        checks.append(("warn", "Database mode", "SQLite is okay for a private beta; use Supabase/Postgres for serious multi-user scale."))
    else:
        checks.append(("pass", "Database mode", "External DATABASE_URL is configured. Registered users use isolated Postgres workspaces."))

    r2_vars = ["R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET"]
    configured_r2 = [name for name in r2_vars if os.getenv(name)]
    if len(configured_r2) == len(r2_vars):
        checks.append(("pass", "File storage", "Cloudflare R2 is configured for uploaded PDFs and slide images."))
    elif configured_r2:
        missing = ", ".join(name for name in r2_vars if not os.getenv(name))
        checks.append(("fail" if prod else "warn", "File storage", f"R2 is partially configured. Missing: {missing}."))
    else:
        checks.append(("warn", "File storage", "Using local disk for PDFs/images. Use Cloudflare R2 before a public beta."))

    upload_mb = max_upload_bytes() // (1024 * 1024)
    checks.append(("pass", "Upload limit", f"MAX_UPLOAD_MB effective limit is {upload_mb} MB."))

    gitignore = (ROOT / ".gitignore").read_text(errors="ignore") if (ROOT / ".gitignore").exists() else ""
    for pattern in [".env", "backend/data/source_material/", "backend/data/slide_images/", "backend/data/user_workspaces/"]:
        checks.append((
            "pass" if pattern in gitignore else "fail",
            "Private files",
            f"{pattern} {'is ignored' if pattern in gitignore else 'is not ignored'}.",
        ))

    if os.getenv("ANTHROPIC_API_KEY"):
        checks.append(("pass", "Claude key", "Configured. Rotate it if it was ever pasted into chat or logs."))
    else:
        checks.append(("warn", "Claude key", "Not configured; local/fallback generation will be used."))

    print("StudyPace production readiness")
    print("=" * 32)
    for status, name, message in checks:
        print(f"{status.upper():<5} {name}: {message}")

    return 1 if any(status == "fail" for status, _, _ in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
