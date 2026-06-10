from __future__ import annotations

import http.cookiejar
import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ALLOWED_EXTENSIONS = {".pdf", ".pptx", ".ppt", ".docx", ".txt", ".md"}
_TIMEOUT_SHORT = 15
_TIMEOUT_DOWNLOAD = 60


def _make_opener(jar: http.cookiejar.CookieJar | None = None) -> urllib.request.OpenerDirector:
    handlers: list = []
    if jar is not None:
        handlers.append(urllib.request.HTTPCookieProcessor(jar))
    if os.getenv("BB_VERIFY_SSL", "true").lower() in ("0", "false", "no"):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        handlers.append(urllib.request.HTTPSHandler(context=ctx))
    return urllib.request.build_opener(*handlers)


class BlackboardError(Exception):
    pass


class BlackboardLoginError(BlackboardError):
    pass


def login(base_url: str, username: str, password: str) -> str:
    """Log into Blackboard via the web form and return the BbRouter cookie value."""
    base = base_url.rstrip("/")
    jar = http.cookiejar.CookieJar()
    opener = _make_opener(jar)

    data = urllib.parse.urlencode({
        "user_id": username.strip(),
        "password": password,
        "login": "Login",
        "action": "login",
        "new_loc": "",
    }).encode()

    req = urllib.request.Request(
        f"{base}/webapps/login/",
        data=data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "Mozilla/5.0 StudyPace",
        },
    )
    try:
        with opener.open(req, timeout=_TIMEOUT_SHORT) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
    except urllib.error.URLError as exc:
        raise BlackboardLoginError(f"Cannot reach E-Learn: {exc.reason}") from exc

    body_lower = body.lower()
    if any(p in body_lower for p in (
        "invalid username", "invalid password", "login failed",
        "your username or password", "incorrect username",
    )):
        raise BlackboardLoginError("Incorrect username or password.")

    bb_router = next((c.value for c in jar if c.name == "BbRouter"), None)
    if not bb_router:
        raise BlackboardLoginError(
            "Login did not return a session. "
            "KU may use Single Sign-On (SSO) — contact the developer."
        )
    return bb_router


class BlackboardClient:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        raw = token.strip()
        if raw.lower().startswith("bearer "):
            raw = raw[len("bearer "):].strip()
        self._token = raw
        self._use_bearer = raw.startswith("eyJ")

    def _headers(self) -> dict[str, str]:
        if self._use_bearer:
            return {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
        return {"Cookie": f"BbRouter={self._token}", "Accept": "application/json"}

    def _get(self, path: str) -> dict:
        url = f"{self.base_url}{path}"
        opener = _make_opener()
        req = urllib.request.Request(url, headers=self._headers())
        try:
            with opener.open(req, timeout=_TIMEOUT_SHORT) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise BlackboardError("Session expired. Log in again.") from exc
            if exc.code == 403:
                raise BlackboardError("Access denied.") from exc
            raise BlackboardError(f"E-Learn returned HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise BlackboardError(f"Cannot reach E-Learn: {exc.reason}") from exc

    def _download(self, path: str) -> bytes:
        url = f"{self.base_url}{path}"
        jar = http.cookiejar.CookieJar()
        opener = _make_opener(jar)
        req = urllib.request.Request(url, headers=self._headers())
        try:
            with opener.open(req, timeout=_TIMEOUT_DOWNLOAD) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            raise BlackboardError(f"Download failed: HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise BlackboardError(f"Download failed: {exc.reason}") from exc

    def verify(self) -> dict:
        data = self._get("/learn/api/public/v1/users/me")
        given = data.get("name", {}).get("given", "")
        family = data.get("name", {}).get("family", "")
        return {
            "id": data.get("id", ""),
            "name": f"{given} {family}".strip() or data.get("userName", ""),
            "username": data.get("userName", ""),
        }

    def get_courses(self) -> list[dict]:
        data = self._get("/learn/api/public/v1/users/me/courses?expand=course&limit=100")
        courses = []
        for item in data.get("results", []):
            course = item.get("course", {})
            if not course:
                continue
            courses.append({
                "bb_course_id": course.get("id", ""),
                "name": course.get("name", ""),
                "code": course.get("courseId", ""),
            })
        return courses

    def get_attachments(self, bb_course_id: str) -> list[dict]:
        contents = self._get(
            f"/learn/api/public/v1/courses/{bb_course_id}/contents?limit=200"
        )
        attachments = []
        for item in contents.get("results", []):
            content_id = item.get("id", "")
            if not content_id:
                continue
            try:
                att_data = self._get(
                    f"/learn/api/public/v1/courses/{bb_course_id}"
                    f"/contents/{content_id}/attachments"
                )
            except BlackboardError:
                continue
            for att in att_data.get("results", []):
                filename = att.get("fileName", "")
                ext = Path(filename).suffix.lower() if filename else ""
                if ext not in ALLOWED_EXTENSIONS:
                    continue
                attachments.append({
                    "content_id": content_id,
                    "attachment_id": att.get("id", ""),
                    "filename": filename,
                    "content_title": item.get("title", filename),
                    "size_bytes": att.get("size", 0),
                })
        return attachments

    def download_attachment(
        self, bb_course_id: str, content_id: str, attachment_id: str
    ) -> bytes:
        path = (
            f"/learn/api/public/v1/courses/{bb_course_id}"
            f"/contents/{content_id}/attachments/{attachment_id}/download"
        )
        return self._download(path)
