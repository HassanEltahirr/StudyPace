from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request
from pathlib import Path

_BASE = "https://prod-api.ku-ai-instructor.azzammourad.org"
_TIMEOUT = 20
ALLOWED_EXTENSIONS = {".pdf", ".pptx", ".ppt", ".docx", ".txt", ".md"}


class KuAiError(Exception):
    pass


def _make_opener() -> urllib.request.OpenerDirector:
    handlers: list = []
    if os.getenv("BB_VERIFY_SSL", "true").lower() in ("0", "false", "no"):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        handlers.append(urllib.request.HTTPSHandler(context=ctx))
    return urllib.request.build_opener(*handlers)


def _get(path: str, token: str) -> dict | list:
    req = urllib.request.Request(
        f"{_BASE}{path}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    try:
        with _make_opener().open(req, timeout=_TIMEOUT) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise KuAiError("Token rejected by KU AI — try signing in again.") from exc
        raise KuAiError(f"KU AI API returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise KuAiError(f"Cannot reach KU AI: {exc.reason}") from exc


def get_courses(token: str) -> list[dict]:
    data = _get("/student/courses", token)
    courses = data if isinstance(data, list) else data.get("results", data.get("courses", []))
    return [
        {
            "bb_course_id": str(c.get("id", "")),
            "name": c.get("name", c.get("title", "")),
            "code": c.get("code", c.get("course_code", "")),
        }
        for c in courses
        if c.get("id")
    ]


def get_files(token: str, course_id: str) -> list[dict]:
    data = _get(f"/student/contents/{course_id}", token)
    items = data if isinstance(data, list) else data.get("results", data.get("contents", []))
    files = []
    for item in items:
        filename = item.get("file_name", item.get("filename", item.get("name", "")))
        ext = Path(filename).suffix.lower() if filename else ""
        if ext not in ALLOWED_EXTENSIONS:
            continue
        files.append({
            "content_id": str(item.get("id", "")),
            "attachment_id": str(item.get("id", "")),
            "filename": filename,
            "content_title": item.get("title", item.get("name", filename)),
            "size_bytes": item.get("size", item.get("file_size", 0)),
            "download_url": item.get("file_url", item.get("url", "")),
        })
    return files


def download_file(token: str, download_url: str) -> bytes:
    # Use the full URL directly if provided, else construct from base
    url = download_url if download_url.startswith("http") else f"{_BASE}{download_url}"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with _make_opener().open(req, timeout=60) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise KuAiError(f"Download failed: HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise KuAiError(f"Download failed: {exc.reason}") from exc
