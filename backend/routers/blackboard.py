from __future__ import annotations

import json
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import current_workspace_data_dir, get_db
from models import Course, Flashcard, Lecture, LessonQuestion, QuizQuestion, Slide, Topic
from services import ku_ai_client
from services.ku_ai_client import KuAiError
from services.object_storage import content_type_for_filename, object_storage, workspace_object_key
from services.question_generator import build_lesson
from services.slide_parser import SlideExtractionError, extract_slides

router = APIRouter()

_MS_TENANT   = "08fe1c0a-19f5-4f24-a662-fdd5dd460025"
_MS_CLIENT   = "0801541c-a1d6-40ad-a943-19fa62be722f"
_MS_TOKEN_URL = f"https://login.microsoftonline.com/{_MS_TENANT}/oauth2/v2.0/token"
_MAX_FILE_BYTES = 50 * 1024 * 1024


# ── Pydantic bodies ───────────────────────────────────────────────────────────

class OAuthExchangeBody(BaseModel):
    code: str
    redirect_uri: str
    code_verifier: str


class FilesBody(BaseModel):
    bb_token: str


class ImportFile(BaseModel):
    course_id_ext: str          # ku-ai course id
    content_id: str
    filename: str
    download_url: str = ""


class ImportBody(BaseModel):
    bb_token: str
    course_id: int              # StudyPace course id
    files: list[ImportFile]


# ── OAuth exchange ────────────────────────────────────────────────────────────

@router.post("/oauth/exchange")
def oauth_exchange(body: OAuthExchangeBody):
    """Exchange Microsoft auth code (PKCE) for an access token, then verify it works."""
    token = _exchange_ms_code(body.code, body.redirect_uri, body.code_verifier)

    # Verify the token actually works with the KU AI API
    try:
        courses = ku_ai_client.get_courses(token)
    except KuAiError as exc:
        raise HTTPException(400, f"Signed in with Microsoft but could not load courses: {exc}") from exc

    return {"bb_token": token, "courses": courses}


def _exchange_ms_code(code: str, redirect_uri: str, code_verifier: str) -> str:
    data = urllib.parse.urlencode({
        "client_id":     _MS_CLIENT,
        "grant_type":    "authorization_code",
        "code":          code,
        "redirect_uri":  redirect_uri,
        "code_verifier": code_verifier,
    }).encode()

    req = urllib.request.Request(
        _MS_TOKEN_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    opener = _make_opener()
    try:
        with opener.open(req, timeout=15) as resp:
            payload = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        raise HTTPException(400, f"Microsoft token exchange failed: {body}") from exc
    except urllib.error.URLError as exc:
        raise HTTPException(502, f"Cannot reach Microsoft: {exc.reason}") from exc

    token = payload.get("access_token", "")
    if not token:
        raise HTTPException(400, "Microsoft did not return an access token.")
    return token


def _make_opener() -> urllib.request.OpenerDirector:
    handlers: list = []
    if os.getenv("BB_VERIFY_SSL", "true").lower() in ("0", "false", "no"):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        handlers.append(urllib.request.HTTPSHandler(context=ctx))
    return urllib.request.build_opener(*handlers)


# ── Course files ──────────────────────────────────────────────────────────────

@router.post("/courses/{ku_course_id}/files")
def list_files(ku_course_id: str, body: FilesBody):
    try:
        files = ku_ai_client.get_files(body.bb_token, ku_course_id)
    except KuAiError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"files": files}


# ── Import ────────────────────────────────────────────────────────────────────

@router.post("/import")
def import_files(body: ImportBody, db: Session = Depends(get_db)):
    course = db.get(Course, body.course_id)
    if not course:
        raise HTTPException(404, "Course not found")

    results = []
    for f in body.files:
        # Download from KU AI service
        try:
            content = ku_ai_client.download_file(body.bb_token, f.download_url or f"/student/contents/{f.content_id}/download")
        except KuAiError as exc:
            results.append({"filename": f.filename, "status": "error", "message": str(exc)})
            continue

        if len(content) > _MAX_FILE_BYTES:
            results.append({"filename": f.filename, "status": "error", "message": "File too large (>50 MB)"})
            continue

        try:
            slides = extract_slides(f.filename, content)
            generated = build_lesson(slides, Path(f.filename).stem)
        except Exception as exc:
            results.append({"filename": f.filename, "status": "error", "message": f"Could not parse: {exc}"})
            continue

        display_title = _clean_label(generated.title) or _clean_label(Path(f.filename).stem) or Path(f.filename).stem

        topic = Topic(
            course_id=body.course_id,
            name=display_title[:200],
            chapter="Imported from E-Learn",
            estimated_minutes=generated.estimated_minutes,
            weight=1.5,
            order=db.query(Topic).filter(Topic.course_id == body.course_id).count(),
        )
        db.add(topic)
        db.flush()

        lecture = Lecture(
            course_id=body.course_id,
            topic_id=topic.id,
            title=display_title[:240],
            source_filename=f.filename[:260],
            source_type="elearn",
            status="ready",
            summary=generated.summary,
            key_concepts_json=json.dumps(generated.key_concepts),
            learning_objectives_json=json.dumps(generated.learning_objectives),
            estimated_minutes=generated.estimated_minutes,
            local_only=True,
        )
        db.add(lecture)
        db.flush()

        _save_source(lecture, content)

        for extracted in slides:
            db.add(Slide(
                lecture_id=lecture.id,
                slide_number=extracted.slide_number,
                title=extracted.title[:240],
                text=extracted.text,
                content_tags_json=json.dumps(extracted.content_tags),
            ))

        for card in generated.flashcards:
            db.add(Flashcard(lecture_id=lecture.id, topic_id=topic.id,
                             slide_number=card["slide_number"],
                             front=card["front"], back=card["back"]))

        for q in generated.questions:
            opts = q["options"]
            db.add(LessonQuestion(
                lecture_id=lecture.id, topic_id=topic.id,
                slide_number=q["slide_number"], prompt=q["prompt"],
                option_a=opts["a"], option_b=opts["b"],
                option_c=opts["c"], option_d=opts["d"],
                correct=q["correct"], explanation=q["explanation"],
                wrong_explanations_json=json.dumps(q.get("wrong_explanations", {})),
                difficulty=q["difficulty"],
                topic_tag=q.get("topic_tag", "")[:160],
            ))
            if _is_mcq(q):
                db.add(QuizQuestion(
                    topic_id=topic.id,
                    question=f"{q['prompt']} (cite: slide {q['slide_number']})",
                    option_a=opts["a"], option_b=opts["b"],
                    option_c=opts["c"], option_d=opts["d"],
                    correct=q["correct"], explanation=q["explanation"],
                ))

        db.commit()
        results.append({"filename": f.filename, "status": "ok", "lecture_id": lecture.id})

    return {"results": results}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _save_source(lecture: Lecture, content: bytes) -> None:
    filename = Path(lecture.source_filename).name[:260]
    storage = object_storage()
    if storage:
        storage.put_bytes(
            workspace_object_key("source_material", str(lecture.course_id), filename),
            content,
            content_type_for_filename(filename),
        )
        return
    folder = current_workspace_data_dir() / "source_material" / str(lecture.course_id)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / filename).write_bytes(content)


def _clean_label(value: str) -> str:
    if not value:
        return ""
    cleaned = re.sub(r"[_\-]+", " ", value).strip()
    return cleaned if len(cleaned) > 3 else ""


def _is_mcq(q: dict) -> bool:
    return bool(q.get("prompt")) and bool(q.get("options", {}).get("a")) and q.get("correct") in {"a", "b", "c", "d"}
