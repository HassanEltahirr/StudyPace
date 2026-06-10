from __future__ import annotations

import base64
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
import json
import math
import re
import shutil
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session, joinedload, selectinload

from config import ALLOWED_UPLOAD_EXTENSIONS, max_upload_bytes
from database import (
    current_request_username,
    current_workspace_data_dir,
    get_db,
    reset_request_username,
    set_request_username,
    workspace_session,
)
from models import (
    AnswerAttempt,
    Assessment,
    AssessmentType,
    Badge,
    CalendarBlock,
    Course,
    Flashcard,
    GradeItem,
    Lecture,
    LessonQuestion,
    QuizAttempt,
    QuizQuestion,
    Slide,
    StudySession,
    Syllabus,
    Topic,
    UserSettings,
)
from scheduler import generate_daily_plan
from schemas import (
    CalendarBlockOut,
    CourseWithLessons,
    GradeItemOut,
    LearningOverview,
    LectureUpload,
    LessonQuizAnswer,
    LessonOut,
    LessonQuizSubmission,
    LessonQuizResult,
)
from services.gemini_ai import gemini_available, generate_deck_summary, generate_practice_questions
from services.local_ai import LocalAI
from services.object_storage import content_type_for_filename, object_storage, workspace_object_key
from services.question_generator import GeneratedLesson, build_lesson
from services.simple_cache import clear_workspace_cache, get_cached, set_cached, workspace_cache_key
from services.slide_parser import ExtractedSlide, SlideExtractionError, extract_slides
from services.syllabus_parser import parse_syllabus
from services.youtube_recs import recommend_videos, youtube_available

try:
    import fitz
except Exception:  # pragma: no cover - optional runtime dependency
    fitz = None

router = APIRouter()

OLLAMA_FAST_BUDGET_SECONDS = 0.6
OLLAMA_SUMMARY_BUDGET_SECONDS = 0.9
OLLAMA_GENERAL_BUDGET_SECONDS = 3.0
_OLLAMA_EXECUTOR = ThreadPoolExecutor(max_workers=1)
_OLLAMA_LOCK = threading.Lock()
_OLLAMA_WARMUP_LOCK = threading.Lock()
_OLLAMA_SKIP_UNTIL = 0.0
_OLLAMA_WARMED_UNTIL = 0.0


@router.get("/overview", response_model=LearningOverview)
def overview():
    key = workspace_cache_key("learning:overview")
    cached = get_cached(key)
    if cached is not None:
        return cached
    with workspace_session() as db:
        settings = _settings(db)
        lecture_filter = Lecture.source_type.notin_(["demo", "sync"])
        stats = db.execute(select(
            select(func.count(Lecture.id)).where(lecture_filter).scalar_subquery().label("lectures_count"),
            select(func.coalesce(func.sum(case((Lecture.mastery_score >= 0.8, 1), else_=0)), 0)).where(lecture_filter).scalar_subquery().label("mastered_count"),
            select(func.coalesce(func.avg(Lecture.mastery_score), 0.0)).where(lecture_filter).scalar_subquery().label("avg_mastery"),
            select(func.count(AnswerAttempt.id)).where(AnswerAttempt.is_correct == True).scalar_subquery().label("correct_count"),  # noqa: E712
            select(func.count(Course.id)).scalar_subquery().label("courses_count"),
            select(func.count(Syllabus.id)).scalar_subquery().label("syllabi_count"),
            select(func.count(GradeItem.id)).scalar_subquery().label("grade_items_count"),
            select(func.count(LessonQuestion.id)).scalar_subquery().label("questions_count"),
            select(func.count(Lecture.id)).where(lecture_filter, Lecture.mastery_score < 0.8).scalar_subquery().label("review_count"),
        )).one()

        lecture_count = int(stats.lectures_count or 0)
        mastered_count = int(stats.mastered_count or 0)
        correct = int(stats.correct_count or 0)
        xp = correct * 10 + mastered_count * 25
        level = max(1, xp // 120 + 1)
        mastery = round(float(stats.avg_mastery or 0.0), 2) if lecture_count else 0.0

        payload = LearningOverview(
            xp=xp,
            level=level,
            streak=settings.streak,
            mastery_score=mastery,
            courses_count=int(stats.courses_count or 0),
            lectures_count=lecture_count,
            syllabi_count=int(stats.syllabi_count or 0),
            grade_items_count=int(stats.grade_items_count or 0),
            questions_count=int(stats.questions_count or 0),
            review_count=int(stats.review_count or 0),
            today_tasks=[],
            weak_topics=[],
            upcoming_deadlines=[],
            badges=[],
            privacy_mode="local-first: files stay on this machine unless hosted mode is enabled",
            ai_status={"available": False, "provider": "ollama", "offline_ready": False},
        ).model_dump(mode="json")
    set_cached(key, payload, ttl_seconds=60)
    return payload


@router.get("/courses/{course_id}", response_model=CourseWithLessons)
def course_detail(course_id: int):
    key = workspace_cache_key("learning:course_detail", int(course_id))
    cached = get_cached(key)
    if cached is not None:
        return cached
    with workspace_session() as db:
        course = db.get(Course, course_id)
        if not course:
            raise HTTPException(404, "Course not found")
        topics = _schedulable_topics(db, course_id)
        lectures = (
            db.query(Lecture)
            .filter(Lecture.course_id == course_id, Lecture.source_type.notin_(["demo", "sync"]))
            .order_by(Lecture.created_at.desc())
            .all()
        )
        lectures = _unique_source_lectures(lectures)
        lecture_ids = [lecture.id for lecture in lectures]
        slide_counts = _count_by_lecture(db, Slide, lecture_ids)
        question_counts = _count_by_lecture(db, LessonQuestion, lecture_ids)
        flashcard_counts = _count_by_lecture(db, Flashcard, lecture_ids)
        syllabi = db.query(Syllabus).filter(Syllabus.course_id == course_id).order_by(Syllabus.created_at.desc()).all()
        grade_items = db.query(GradeItem).filter(GradeItem.course_id == course_id).order_by(GradeItem.weight_pct.desc()).all()
        payload = CourseWithLessons.model_validate({
            "course": course,
            "topics": topics,
            "lectures": [
                _lesson_item(
                    lecture,
                    slide_count=slide_counts.get(lecture.id, 0),
                    question_count=question_counts.get(lecture.id, 0),
                    flashcard_count=flashcard_counts.get(lecture.id, 0),
                )
                for lecture in lectures
            ],
            "syllabi": [_syllabus(s) for s in syllabi],
            "grade_items": grade_items,
        }).model_dump(mode="json")
    set_cached(key, payload, ttl_seconds=120)
    return payload


@router.patch("/grade-items/{item_id}", response_model=GradeItemOut)
def update_grade_item(item_id: int, payload: dict, db: Session = Depends(get_db)):
    item = db.get(GradeItem, item_id)
    if not item:
        raise HTTPException(404, "Grade item not found")

    raw = payload.get("current_score")
    if raw in (None, ""):
        item.current_score = None
    else:
        try:
            item.current_score = max(0.0, min(100.0, float(raw)))
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, "current_score must be a raw mark out of 100") from exc

    db.commit()
    clear_workspace_cache()
    db.refresh(item)
    return item


@router.post("/courses/{course_id}/syllabus", status_code=201)
def upload_syllabus(course_id: int, payload: LectureUpload, db: Session = Depends(get_db)):
    course = db.get(Course, course_id)
    if not course:
        raise HTTPException(404, "Course not found")

    try:
        content = _decode_upload(payload.content_base64)
        _validate_upload(payload.filename, content)
        parsed = parse_syllabus(payload.filename, content)
    except SlideExtractionError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(422, "Syllabus extraction failed. Upload a readable PDF, PPTX, DOCX, TXT, or Markdown syllabus.") from exc

    syllabus = Syllabus(
        course_id=course_id,
        source_filename=payload.filename[:260],
        status="ready",
        raw_text=parsed.raw_text,
        summary=parsed.summary,
        extracted_dates_json=json.dumps(parsed.dates),
        grading_weights_json=json.dumps(parsed.weights),
    )
    db.add(syllabus)
    db.flush()

    weighted_dates = {item.get("due_date") for item in parsed.weights if item.get("due_date")}

    for item in parsed.weights:
        grade_item = GradeItem(
            course_id=course_id,
            syllabus_id=syllabus.id,
            title=item["title"][:220],
            category=item["category"],
            weight_pct=item["weight_pct"],
            due_date=date.fromisoformat(item["due_date"]) if item.get("due_date") else None,
        )
        db.add(grade_item)
        if grade_item.due_date and grade_item.category in {"exam", "quiz", "assignment"}:
            _upsert_assessment(db, course_id, grade_item.title, grade_item.due_date, grade_item.category)

    for event in parsed.dates:
        if event["date"] in weighted_dates:
            continue
        title = event["label"][:200]
        category = "exam" if "exam" in title.lower() or "final" in title.lower() else "assignment"
        _upsert_assessment(db, course_id, title, date.fromisoformat(event["date"]), category)

    _award_badge(db, "syllabus_uploaded", "Syllabus parsed", "Extracted dates and grading weights from a local syllabus.")
    db.commit()
    clear_workspace_cache()
    db.refresh(syllabus)
    return _syllabus(syllabus)


@router.post("/courses/{course_id}/upload", response_model=LessonOut, status_code=201)
def upload_lecture(course_id: int, payload: LectureUpload, db: Session = Depends(get_db)):
    course = db.get(Course, course_id)
    if not course:
        raise HTTPException(404, "Course not found")

    try:
        content = _decode_upload(payload.content_base64)
        _validate_upload(payload.filename, content)
        slides = extract_slides(payload.filename, content)
        text_slides = _text_extractable_slides(slides)
        generated = (
            build_lesson(text_slides, Path(payload.filename).stem)
            if text_slides
            else _visual_only_lesson(slides, Path(payload.filename).stem)
        )
        display_title = _clean_display_label(generated.title) or _clean_display_label(Path(payload.filename).stem) or Path(payload.filename).stem
    except SlideExtractionError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(422, "Upload failed. Confirm this is a readable PDF, PPTX, DOCX, TXT, or Markdown file.") from exc

    topic = Topic(
        course_id=course_id,
        name=display_title[:200],
        chapter="Uploaded lecture",
        estimated_minutes=generated.estimated_minutes,
        weight=1.5,
        order=db.query(Topic).filter(Topic.course_id == course_id).count(),
    )
    db.add(topic)
    db.flush()

    lecture = Lecture(
        course_id=course_id,
        topic_id=topic.id,
        title=display_title[:240],
        source_filename=payload.filename[:260],
        source_type=Path(payload.filename).suffix.lower().replace(".", "") or "text",
        status="ready",
        summary=generated.summary,
        key_concepts_json=json.dumps(generated.key_concepts),
        learning_objectives_json=json.dumps(generated.learning_objectives),
        estimated_minutes=generated.estimated_minutes,
        local_only=True,
    )
    db.add(lecture)
    db.flush()
    _store_source_material(lecture, content)

    for extracted in slides:
        db.add(Slide(
            lecture_id=lecture.id,
            slide_number=extracted.slide_number,
            title=extracted.title[:240],
            text=extracted.text,
            content_tags_json=json.dumps(extracted.content_tags),
        ))

    for card in generated.flashcards:
        db.add(Flashcard(
            lecture_id=lecture.id,
            topic_id=topic.id,
            slide_number=card["slide_number"],
            front=card["front"],
            back=card["back"],
        ))

    for generated_question in generated.questions:
        options = generated_question["options"]
        lesson_q = LessonQuestion(
            lecture_id=lecture.id,
            topic_id=topic.id,
            slide_number=generated_question["slide_number"],
            prompt=generated_question["prompt"],
            option_a=options["a"],
            option_b=options["b"],
            option_c=options["c"],
            option_d=options["d"],
            correct=generated_question["correct"],
            explanation=generated_question["explanation"],
            wrong_explanations_json=json.dumps(generated_question["wrong_explanations"]),
            difficulty=generated_question["difficulty"],
            topic_tag=generated_question["topic_tag"][:160],
        )
        db.add(lesson_q)
        if _generated_question_is_mcq(generated_question):
            db.add(QuizQuestion(
                topic_id=topic.id,
                question=f"{generated_question['prompt']} (cite: slide {generated_question['slide_number']})",
                option_a=options["a"],
                option_b=options["b"],
                option_c=options["c"],
                option_d=options["d"],
                correct=generated_question["correct"],
                explanation=generated_question["explanation"],
            ))

    _award_badge(db, "first_upload", "First upload", "Created a lesson from local slide content.")

    lecture_id = lecture.id
    db.commit()
    clear_workspace_cache()
    db.expire_all()
    _generate_ai_summary_in_background(lecture_id)
    lecture = db.query(Lecture).filter(Lecture.id == lecture_id).first()
    return _lesson_detail(lecture)


@router.get("/lectures/{lecture_id}", response_model=LessonOut)
def lecture_detail(lecture_id: int, db: Session = Depends(get_db)):
    lecture = _get_lecture(db, lecture_id)
    return _lesson_detail(lecture)


@router.get("/lectures/{lecture_id}/ai-summary")
def lecture_ai_summary(lecture_id: int, db: Session = Depends(get_db)):
    """Deck summary shown before the student starts studying.

    Generated once in the background and cached in the DB; this endpoint only
    kicks off generation when the stored summary is missing (e.g. decks
    uploaded before summaries existed).
    """
    lecture = _get_lecture(db, lecture_id)
    summary = (lecture.ai_summary or "").strip()
    if summary:
        return {"status": "ready", "summary": summary}
    if not gemini_available():
        return {"status": "unavailable", "summary": ""}
    _generate_ai_summary_in_background(lecture.id)
    return {"status": "pending", "summary": ""}


@router.post("/lectures/{lecture_id}/practice-exam")
def lecture_practice_exam(lecture_id: int, payload: dict, db: Session = Depends(get_db)):
    """Generate Gulf-exam-style practice questions on demand. Never pregenerated."""
    lecture = _get_lecture(db, lecture_id)
    if not gemini_available():
        raise HTTPException(503, "Practice questions need GEMINI_API_KEY configured on the server.")

    difficulty = str(payload.get("difficulty") or "medium").lower().strip()
    if difficulty not in {"easy", "medium", "hard"}:
        difficulty = "medium"

    try:
        count = int(payload.get("count") or 5)
    except (TypeError, ValueError):
        count = 5
    count = max(1, min(count, 15))

    slides = sorted(lecture.slides, key=lambda s: s.slide_number)
    slide_texts = [(s.slide_number, s.title or "", s.text or "") for s in slides if (s.text or "").strip()]
    questions = generate_practice_questions(
        _lecture_display_title(lecture),
        slide_texts,
        difficulty=difficulty,
        count=count,
    )
    if not questions:
        raise HTTPException(502, "Could not generate practice questions right now. Try again in a moment.")
    return {"lecture_id": lecture.id, "difficulty": difficulty, "questions": questions}


@router.get("/lectures/{lecture_id}/videos")
def lecture_videos(lecture_id: int, db: Session = Depends(get_db)):
    """Up to 3 lecture videos for a finished deck, cached in the DB to protect quota."""
    lecture = _get_lecture(db, lecture_id)
    cached = _json_load(lecture.video_recs_json, [])
    if cached:
        return {"videos": cached}
    if not youtube_available():
        return {"videos": []}

    fallback_topics = _clean_display_list(_json_load(lecture.key_concepts_json, []))
    videos = recommend_videos(_lecture_display_title(lecture), fallback_topics)
    if videos:
        lecture.video_recs_json = json.dumps(videos)
        db.commit()
        clear_workspace_cache()
    return {"videos": videos}


_AI_SUMMARY_PENDING_LOCK = threading.Lock()
_AI_SUMMARY_PENDING: set[tuple[str, int]] = set()


def _generate_ai_summary_in_background(lecture_id: int) -> None:
    if not gemini_available():
        return
    username = current_request_username()
    pending_key = (username or "public", lecture_id)
    with _AI_SUMMARY_PENDING_LOCK:
        if pending_key in _AI_SUMMARY_PENDING:
            return
        _AI_SUMMARY_PENDING.add(pending_key)

    def run() -> None:
        token = set_request_username(username)
        try:
            with workspace_session() as db:
                lecture = db.get(Lecture, lecture_id)
                if not lecture or (lecture.ai_summary or "").strip():
                    return
                slides = sorted(lecture.slides, key=lambda s: s.slide_number)
                slide_texts = [(s.slide_number, s.title or "", s.text or "") for s in slides if (s.text or "").strip()]
                summary = generate_deck_summary(_lecture_display_title(lecture), slide_texts)
                if not summary:
                    return
                lecture.ai_summary = summary.strip()
                db.commit()
                clear_workspace_cache()
        except Exception as exc:
            print(f"AI summary generation failed for lecture {lecture_id}: {exc}")
        finally:
            with _AI_SUMMARY_PENDING_LOCK:
                _AI_SUMMARY_PENDING.discard(pending_key)
            reset_request_username(token)

    threading.Thread(target=run, daemon=True).start()


@router.post("/lectures/{lecture_id}/complete", response_model=LessonOut)
def complete_lecture_slides(lecture_id: int, db: Session = Depends(get_db)):
    lecture = _get_lecture(db, lecture_id)
    lecture.mastery_score = round(max(lecture.mastery_score or 0.0, 0.8), 2)

    if lecture.topic_id:
        today = date.today()
        session = (
            db.query(StudySession)
            .filter(
                StudySession.topic_id == lecture.topic_id,
                StudySession.date == today,
                StudySession.completed == True,  # noqa: E712
            )
            .first()
        )
        if session:
            session.planned_minutes = max(session.planned_minutes or 0, lecture.estimated_minutes or 15)
            session.actual_minutes = max(session.actual_minutes or 0, min(lecture.estimated_minutes or 15, 60))
            session.notes = session.notes or f"Finished slides: {_lecture_display_title(lecture)}"
        else:
            db.add(StudySession(
                topic_id=lecture.topic_id,
                date=today,
                planned_minutes=lecture.estimated_minutes or 15,
                actual_minutes=min(lecture.estimated_minutes or 15, 60),
                completed=True,
                notes=f"Finished slides: {_lecture_display_title(lecture)}",
            ))

    _update_streak(db)
    _award_badge(db, "slides_finished", "Slides finished", "Finished a slide deck from the study plan.")
    db.flush()
    response = _lesson_detail(lecture)
    db.commit()
    clear_workspace_cache()
    return response


@router.get("/slides/{slide_id}/image")
def slide_image(slide_id: int, db: Session = Depends(get_db)):
    slide = (
        db.query(Slide)
        .options(joinedload(Slide.lecture))
        .filter(Slide.id == slide_id)
        .first()
    )
    if not slide:
        raise HTTPException(404, "Slide not found")

    source = _source_material_path(slide.lecture)
    if source:
        image_path = _render_slide_image(source, slide.lecture_id, slide.slide_number)
        return FileResponse(image_path, media_type="image/png")

    source_key = _source_material_object_key(slide.lecture)
    storage = object_storage()
    if not source_key or not storage or not storage.exists(source_key):
        raise HTTPException(404, "Slide image source is not available.")

    image_key = _slide_image_object_key(slide.lecture_id, slide.slide_number)
    if storage.exists(image_key):
        return Response(storage.get_bytes(image_key), media_type="image/png")

    source_bytes = storage.get_bytes(source_key)
    image_bytes = _render_slide_image_bytes(source_bytes, slide.lecture.source_filename, slide.slide_number)
    storage.put_bytes(image_key, image_bytes, "image/png")
    return Response(image_bytes, media_type="image/png")


@router.get("/lectures/{lecture_id}/questions")
def lecture_questions(lecture_id: int, db: Session = Depends(get_db)):
    lecture = _get_lecture(db, lecture_id)
    return [_question(q, include_answer=False) for q in sorted(lecture.questions, key=_question_sort_key)]


@router.delete("/lectures/{lecture_id}", status_code=204)
def delete_lecture(lecture_id: int, db: Session = Depends(get_db)):
    lecture = _get_lecture(db, lecture_id)
    topic_id = lecture.topic_id
    source_key = _source_material_object_key(lecture)
    source_path = _source_material_path(lecture)
    slide_image_dir = _slide_image_dir() / str(lecture.id)
    slide_image_keys = [_slide_image_object_key(lecture.id, slide.slide_number) for slide in lecture.slides]
    source_is_shared = bool(
        lecture.source_filename and db.query(Lecture)
        .filter(
            Lecture.id != lecture.id,
            Lecture.course_id == lecture.course_id,
            Lecture.source_filename == lecture.source_filename,
        )
        .first()
    )

    if topic_id:
        db.query(CalendarBlock).filter(
            or_(CalendarBlock.lecture_id == lecture.id, CalendarBlock.topic_id == topic_id)
        ).delete(synchronize_session=False)
    else:
        db.query(CalendarBlock).filter(CalendarBlock.lecture_id == lecture.id).delete(synchronize_session=False)

    db.delete(lecture)
    db.flush()

    if topic_id and not db.query(Lecture.id).filter(Lecture.topic_id == topic_id).first():
        topic = db.get(Topic, topic_id)
        if topic:
            db.query(StudySession).filter(StudySession.topic_id == topic_id).delete(synchronize_session=False)
            db.query(QuizAttempt).filter(QuizAttempt.topic_id == topic_id).delete(synchronize_session=False)
            db.query(QuizQuestion).filter(QuizQuestion.topic_id == topic_id).delete(synchronize_session=False)
            db.delete(topic)

    db.commit()
    clear_workspace_cache()

    _delete_stored_lecture_files(
        source_key=source_key if not source_is_shared else "",
        source_path=source_path if not source_is_shared else None,
        slide_image_dir=slide_image_dir,
        slide_image_keys=slide_image_keys,
    )
    return Response(status_code=204)


@router.post("/lectures/{lecture_id}/quiz", response_model=LessonQuizResult)
def submit_lesson_quiz(lecture_id: int, payload: LessonQuizSubmission, db: Session = Depends(get_db)):
    lecture = _get_lecture(db, lecture_id)
    question_map = {q.id: q for q in lecture.questions if _is_generated_lesson_mcq(q)}
    if not question_map:
        raise HTTPException(404, "No generated MCQ questions found for this lesson.")

    submitted_ids = [a.question_id for a in payload.answers]
    unrecognised = [qid for qid in submitted_ids if qid not in question_map]
    if unrecognised and not any(qid in question_map for qid in submitted_ids):
        raise HTTPException(400, f"None of the submitted question IDs belong to the MCQ set for this lecture. "
                                 f"Unrecognised IDs: {unrecognised}")

    per_question = []
    correct_count = 0
    for answer in payload.answers:
        question = question_map.get(answer.question_id)
        if not question:
            continue
        if not answer.selected:
            raise HTTPException(422, f"Answer for question {answer.question_id} is missing 'selected' (expected 'a', 'b', 'c', or 'd').")
        selected = answer.selected.lower()
        is_correct = selected == question.correct.lower()
        if is_correct:
            correct_count += 1
        wrong_explanations = _json_load(question.wrong_explanations_json, {})
        explanation = question.explanation if is_correct else wrong_explanations.get(selected, question.explanation)
        db.add(AnswerAttempt(
            lecture_id=lecture.id,
            question_id=question.id,
            selected=selected,
            is_correct=is_correct,
            explanation=explanation,
        ))
        per_question.append({
            **_question(question, include_answer=True),
            "selected": selected,
            "is_correct": is_correct,
            "feedback": explanation,
        })

    total = len(per_question)
    score = correct_count / total if total else 0.0
    lecture.mastery_score = round(max(lecture.mastery_score, score), 2)

    if lecture.topic_id:
        db.add(QuizAttempt(
            topic_id=lecture.topic_id,
            score=score,
            questions_total=total,
            questions_correct=correct_count,
        ))
        db.add(StudySession(
            topic_id=lecture.topic_id,
            date=date.today(),
            planned_minutes=lecture.estimated_minutes,
            actual_minutes=min(lecture.estimated_minutes, 45),
            completed=True,
            notes=f"Lesson quiz: {lecture.title}",
        ))

    _update_streak(db)
    if score >= 0.8:
        _award_badge(db, "mastery_checkpoint", "Mastery checkpoint", "Reached at least 80% on a lesson quiz.")
    if correct_count >= 5:
        _award_badge(db, "active_recall", "Active recall", "Answered five generated questions correctly.")

    xp = correct_count * 10 + (25 if score >= 0.8 else 0)
    lecture_id = lecture.id
    db.commit()
    clear_workspace_cache()
    db.expire_all()
    lecture = db.query(Lecture).filter(Lecture.id == lecture_id).first()

    return LessonQuizResult(
        lecture_id=lecture.id,
        score=round(score, 2),
        correct=correct_count,
        total=total,
        mastery_score=lecture.mastery_score,
        xp_earned=xp,
        unlocked_next=lecture.mastery_score >= 0.8,
        per_question=per_question,
    )


@router.post("/lectures/{lecture_id}/questions/{question_id}/check")
def check_lesson_question(lecture_id: int, question_id: int, payload: LessonQuizAnswer, db: Session = Depends(get_db)):
    lecture = _get_lecture(db, lecture_id)
    question = (
        db.query(LessonQuestion)
        .filter(LessonQuestion.lecture_id == lecture.id, LessonQuestion.id == question_id)
        .first()
    )
    if not question:
        raise HTTPException(404, "Question not found for this lesson.")
    if payload.question_id != question_id:
        raise HTTPException(400, "Question id does not match the check request.")

    if not _is_generated_lesson_mcq(question):
        return _check_written_lesson_question(db, lecture, question, payload.response or "")

    selected = (payload.selected or "").lower().strip()
    if selected not in {"a", "b", "c", "d"}:
        raise HTTPException(400, "Select answer A, B, C, or D before checking.")

    is_correct = selected == question.correct.lower()
    wrong_explanations = _json_load(question.wrong_explanations_json, {})
    explanation = question.explanation if is_correct else wrong_explanations.get(selected, question.explanation)
    db.add(AnswerAttempt(
        lecture_id=lecture.id,
        question_id=question.id,
        selected=selected,
        is_correct=is_correct,
        explanation=explanation,
    ))
    db.commit()
    clear_workspace_cache()

    return {
        **_question(question, include_answer=True),
        "selected": selected,
        "is_correct": is_correct,
        "feedback": explanation,
    }


def _check_written_lesson_question(db: Session, lecture: Lecture, question: LessonQuestion, response: str) -> dict:
    answer = (response or "").strip()
    wrong_explanations = _json_load(question.wrong_explanations_json, {})
    slide = (
        db.query(Slide)
        .filter(Slide.lecture_id == lecture.id, Slide.slide_number == question.slide_number)
        .first()
    )
    model_answer = _plain_written_model_answer(question.explanation, question, slide)

    expected_text = " ".join([
        model_answer,
        str(wrong_explanations.get("rubric", "")),
        slide.text if slide else "",
    ])
    expected_keywords = _written_keywords(expected_text)[:12]
    answer_keywords = set(_written_keywords(answer))
    matched = [keyword for keyword in expected_keywords if keyword in answer_keywords]
    missing = [keyword for keyword in expected_keywords if keyword not in answer_keywords][:5]
    word_count = len(re.findall(r"\b[a-zA-Z0-9]+\b", answer))
    required_matches = max(2, min(4, math.ceil(len(expected_keywords) * 0.35))) if expected_keywords else 2
    is_correct = word_count >= 8 and len(matched) >= required_matches

    if not answer:
        feedback = "Write your own answer first. Even a rough attempt is better than opening the answer guide early."
    elif word_count < 8:
        feedback = "This is too short to check fairly yet. Write a full sentence or two, then submit again."
    elif is_correct:
        feedback = "Good answer. You included the main ideas and gave enough reasoning to count this as correct."
    else:
        feedback = "Not quite yet. Add the missing idea(s), then connect them back to why the slide says they matter."

    attempt_explanation = json.dumps({
        "feedback": feedback,
        "matched": matched,
        "missing": missing,
        "model_answer": model_answer,
    })
    db.add(AnswerAttempt(
        lecture_id=lecture.id,
        question_id=question.id,
        selected="w",
        is_correct=is_correct,
        explanation=attempt_explanation,
    ))
    if is_correct:
        correct_attempts = (
            db.query(AnswerAttempt)
            .filter(AnswerAttempt.lecture_id == lecture.id, AnswerAttempt.is_correct.is_(True))
            .count()
        ) + 1
        question_total = max(1, db.query(LessonQuestion).filter(LessonQuestion.lecture_id == lecture.id).count())
        lecture.mastery_score = max(lecture.mastery_score or 0.0, min(1.0, correct_attempts / question_total))
    db.commit()
    clear_workspace_cache()

    return {
        **_question(question, include_answer=True),
        "selected": "written",
        "response": answer,
        "is_correct": is_correct,
        "feedback": feedback,
        "model_answer": model_answer,
        "matched_keywords": matched,
        "missing_keywords": missing,
        "rubric": wrong_explanations.get("rubric", ""),
        "common_errors": wrong_explanations.get("common_errors", ""),
    }


def _plain_written_model_answer(value: str, question: LessonQuestion | None = None, slide: Slide | None = None) -> str:
    raw = value or ""
    was_coverage_check = bool(re.search(r"\bcoverage check for slide\s+\d+\s*:\s*", raw, flags=re.IGNORECASE))
    cleaned = re.sub(r"^#{1,4}\s*model answer\s*", "", raw, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"\bcoverage check for slide\s+\d+\s*:\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[◼■▪●]+", "", cleaned)
    cleaned = re.sub(r"\*\*([^*]+)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    if cleaned and (was_coverage_check or not _looks_like_natural_answer(cleaned)):
        prompt = (question.prompt if question else "the concept").strip().rstrip(".")
        key_point = re.split(r"\bA strong answer should\b", cleaned, flags=re.IGNORECASE)[0].strip(" .")
        source_label = f"slide {slide.slide_number}" if slide else "the source"
        if key_point:
            if question and re.search(r"\bbsts?\b|binary search tree", question.prompt, flags=re.IGNORECASE):
                return (
                    f"From {source_label}, two good applications to name are {key_point}. "
                    "BSTs are useful here because their ordered tree structure lets you search, insert, or delete by narrowing where the value can be instead of scanning every item."
                )
            return (
                f"The key point from {source_label} is {key_point}. "
                f"A complete answer connects that idea directly to the task: {prompt}, and explains why it matters instead of only repeating the slide words."
            )

    return cleaned or "A strong answer should use the source slide, name the key idea, and explain why it matters."


def _looks_like_natural_answer(value: str) -> bool:
    words = re.findall(r"\b[a-zA-Z0-9]+\b", value or "")
    if len(words) < 9:
        return False
    sentence_count = len(re.findall(r"[.!?]", value or ""))
    return sentence_count > 0


_WRITTEN_STOPWORDS = {
    "about", "address", "after", "again", "answer", "because", "before", "being",
    "between", "check", "common", "complete", "concept", "copying", "correct", "course", "does",
    "explain", "from", "give", "good", "grounded", "have", "here", "idea", "important", "instead",
    "just", "keyword", "lecture", "main", "make", "matter", "matters", "mention",
    "model", "name", "need", "needs", "coverage", "only", "question", "reasoning",
    "rubric", "should", "show", "slide", "source", "stay",
    "repeating", "strong", "student", "task", "term", "that", "their", "them", "then",
    "there", "these", "this", "through", "used", "what", "word", "words", "your",
    "when", "where", "which", "while", "with", "would",
}


def _written_keywords(value: str) -> list[str]:
    seen: set[str] = set()
    keywords: list[str] = []
    for raw in re.findall(r"\b[a-zA-Z][a-zA-Z0-9-]{3,}\b", (value or "").lower()):
        word = raw.replace("-", "")
        if word.endswith("ies") and len(word) > 5:
            word = f"{word[:-3]}y"
        elif word.endswith("s") and len(word) > 5:
            word = word[:-1]
        if word in _WRITTEN_STOPWORDS or len(word) < 4 or word in seen:
            continue
        seen.add(word)
        keywords.append(word)
    return keywords


@router.get("/review")
def review_queue(db: Session = Depends(get_db)):
    return _review_items(db, limit=100)


@router.post("/calendar/regenerate", status_code=204)
def regenerate_plan():
    clear_workspace_cache()


@router.get("/calendar", response_model=list[CalendarBlockOut])
def calendar(
    days: int | None = None,
    assessment_id: int | None = None,
    course_id: int | None = None,
    assessment_type: str | None = None,
    assessment_date: date | None = None,
    lecture_start: int | None = None,
    lecture_end: int | None = None,
    passes: int | None = 1,
):
    start = date.today()
    calendar_cache_key = workspace_cache_key(
        "learning:calendar",
        days or "",
        assessment_id or "",
        course_id or "",
        assessment_type or "",
        assessment_date.isoformat() if assessment_date else "",
        lecture_start or "",
        lecture_end or "",
        passes or 1,
        start.isoformat(),
    )
    cached_calendar = get_cached(calendar_cache_key)
    if cached_calendar is not None:
        return cached_calendar

    with workspace_session() as db:
        return _calendar_uncached(
            db=db,
            cache_key=calendar_cache_key,
            start=start,
            days=days,
            assessment_id=assessment_id,
            course_id=course_id,
            assessment_type=assessment_type,
            assessment_date=assessment_date,
            lecture_start=lecture_start,
            lecture_end=lecture_end,
            passes=passes,
        )


def _calendar_uncached(
    *,
    db: Session,
    cache_key: tuple,
    start: date,
    days: int | None,
    assessment_id: int | None,
    course_id: int | None,
    assessment_type: str | None,
    assessment_date: date | None,
    lecture_start: int | None,
    lecture_end: int | None,
    passes: int | None,
) -> list[dict]:
    target_assessment = db.get(Assessment, assessment_id) if assessment_id else None
    if assessment_id and not target_assessment:
        raise HTTPException(404, "Assessment not found")
    target_course_id = target_assessment.course_id if target_assessment else course_id

    if days is None:
        if not target_assessment:
            target_assessment = (
                db.query(Assessment)
                .filter(Assessment.date >= start)
                .order_by(Assessment.date)
                .first()
            )
        days = (target_assessment.date - start).days + 1 if target_assessment else 14
    days = max(1, min(days, 180))
    end = start + timedelta(days=days - 1)

    blocks: list[CalendarBlockOut] = []
    stored = (
        db.query(CalendarBlock)
        .options(joinedload(CalendarBlock.topic), joinedload(CalendarBlock.lecture))
        .filter(CalendarBlock.date >= start, CalendarBlock.date <= end)
        .order_by(CalendarBlock.date, CalendarBlock.id)
        .all()
    )
    for block in stored:
        if target_course_id and not _calendar_block_matches_course(block, target_course_id):
            continue
        blocks.append(CalendarBlockOut(
            id=block.id,
            lecture_id=block.lecture_id,
            topic_id=block.topic_id,
            course_name=block.topic.course.name if block.topic and block.topic.course else "",
            course_color=block.topic.course.color if block.topic and block.topic.course else "",
            date=block.date,
            title=_clean_display_label(block.title) or block.title,
            planned_minutes=block.planned_minutes,
            status=block.status,
            priority=block.priority,
        ))

    assessment_query = (
        db.query(Assessment)
        .filter(Assessment.date >= start, Assessment.date <= end)
    )
    if target_assessment:
        assessment_query = assessment_query.filter(Assessment.course_id == target_assessment.course_id)
    elif course_id:
        assessment_query = assessment_query.filter(Assessment.course_id == course_id)
    assessments = assessment_query.order_by(Assessment.date).all()
    for assessment in assessments:
        blocks.append(_assessment_marker(assessment, start))

    topics = _schedulable_topics(
        db,
        target_course_id,
        lecture_start=lecture_start,
        lecture_end=lecture_end,
    )
    if not topics:
        return _cached_calendar_payload(cache_key, blocks)

    topic_ids = [topic.id for topic in topics]
    course_ids = sorted({topic.course_id for topic in topics if topic.course_id})
    attempts = db.query(QuizAttempt).filter(QuizAttempt.topic_id.in_(topic_ids)).all()
    all_assessments_query = db.query(Assessment)
    if target_course_id:
        all_assessments_query = all_assessments_query.filter(Assessment.course_id == target_course_id)
    elif course_ids:
        all_assessments_query = all_assessments_query.filter(Assessment.course_id.in_(course_ids))
    all_assessments = all_assessments_query.all()
    settings = _settings(db)
    lecture_lookup = _lecture_lookup_for_topics(db, topics)
    assessment_label = _plan_assessment_label(assessment_type)
    if not assessment_label:
        assessment_label = _assessment_label(target_assessment) if target_assessment else ""
    sessions = db.query(StudySession).filter(StudySession.topic_id.in_(topic_ids)).all()
    blocks.extend(_coverage_timeline_blocks(
        topics=topics,
        sessions=sessions,
        attempts=attempts,
        assessments=all_assessments,
        settings=settings,
        start=start,
        days=days,
        target_assessment=target_assessment,
        target_assessment_date=assessment_date,
        assessment_label=assessment_label,
        lecture_lookup=lecture_lookup,
        repeat_passes=_clamp_study_passes(passes),
    ))
    return _cached_calendar_payload(cache_key, blocks)


def _cached_calendar_payload(cache_key: tuple, blocks: list[CalendarBlockOut]) -> list[dict]:
    payload = [
        block.model_dump(mode="json")
        for block in sorted(blocks, key=lambda b: (b.date, b.status, b.title))
    ]
    set_cached(cache_key, payload, ttl_seconds=120)
    return payload


def _calendar_block_matches_course(block: CalendarBlock, course_id: int) -> bool:
    if block.topic and block.topic.course_id == course_id:
        return True
    if block.lecture and block.lecture.course_id == course_id:
        return True
    return False


def _assessment_marker(assessment: Assessment, start: date) -> CalendarBlockOut:
    label = _assessment_label(assessment)
    course = assessment.course
    return CalendarBlockOut(
        assessment_id=assessment.id,
        course_name=course.name if course else "",
        course_color=course.color if course else "",
        assessment_title=assessment.title,
        assessment_type=label,
        days_until_assessment=0,
        date=assessment.date,
        title=assessment.title,
        planned_minutes=0,
        status="assessment",
        priority=label,
    )


def _assessment_label(assessment: Assessment | None) -> str:
    if not assessment:
        return "study"
    title = assessment.title.lower()
    kind = assessment.type.value if hasattr(assessment.type, "value") else str(assessment.type)
    if "final" in title:
        return "final"
    if "midterm" in title or "mid-term" in title:
        return "midterm"
    if "quiz" in title or kind == "quiz":
        return "quiz"
    if kind == "exam":
        return "exam"
    return kind or "assessment"


def _calendar_priority(item: dict, assessment: Assessment | None) -> str:
    if item["avg_quiz_score"] is not None and item["avg_quiz_score"] < 0.6:
        return "weak"
    return _assessment_label(assessment) if assessment else "normal"


def _coverage_timeline_blocks(
    *,
    topics: list[Topic],
    sessions: list[StudySession],
    attempts: list[QuizAttempt],
    assessments: list[Assessment],
    settings: UserSettings,
    start: date,
    days: int,
    target_assessment: Assessment | None,
    target_assessment_date: date | None,
    assessment_label: str,
    lecture_lookup: dict[int, Lecture],
    repeat_passes: int = 1,
) -> list[CalendarBlockOut]:
    if not topics:
        return []

    end = start + timedelta(days=days - 1)
    assessment_date = target_assessment.date if target_assessment else target_assessment_date
    protected_review_date = assessment_date - timedelta(days=1) if assessment_date else None
    study_dates = [
        plan_date
        for offset in range(days)
        if (
            (plan_date := start + timedelta(days=offset))
            and (not assessment_date or plan_date < assessment_date)
            and (not protected_review_date or plan_date != protected_review_date)
        )
    ]
    if not study_dates and not assessment_date:
        study_dates = [start]

    slots_per_day = _study_slots_per_day(settings.daily_minutes)
    total_slots = len(study_dates) * slots_per_day
    ordered_topics = _balanced_topic_order(topics, assessments, start)
    strengths = _topic_strengths(ordered_topics, attempts, lecture_lookup)
    last_seen = _last_studied_dates(sessions)
    completed_today_topics = [
        topic for topic in ordered_topics
        if last_seen.get(topic.id) == start
    ]

    completed_topic_ids = {
        topic.id for topic in ordered_topics
        if topic.id in last_seen or (strengths.get(topic.id) is not None and strengths[topic.id] >= 0.8)
    }
    remaining_topics = [topic for topic in ordered_topics if topic.id not in completed_topic_ids]

    repeat_passes = _clamp_study_passes(repeat_passes)

    # The student-facing plan is slide-first. A higher pass count repeats the
    # same selected slide decks as review passes before the protected review day.
    entries: list[dict] = []
    if repeat_passes <= 1:
        for topic in remaining_topics:
            entries.append(_timeline_entry(topic, "learn", "new", _learn_minutes(topic)))
    else:
        for topic in ordered_topics:
            entries.append(_timeline_entry(topic, "pass 1", "pass 1", _learn_minutes(topic), 1, repeat_passes))
        for pass_number in range(2, repeat_passes + 1):
            for topic in ordered_topics:
                entries.append(_timeline_entry(
                    topic,
                    f"pass {pass_number}",
                    f"pass {pass_number}",
                    _repeat_pass_minutes(topic, pass_number),
                    pass_number,
                    repeat_passes,
                ))

    completed_today_entries = []
    if repeat_passes <= 1:
        completed_today_entries = [
            _timeline_entry(topic, "learn", "done", _learn_minutes(topic))
            for topic in completed_today_topics
        ]
    occupied_counts = {start: len(completed_today_entries)} if completed_today_entries else {}
    # Coverage-first planning: every selected slide deck should appear before
    # the protected review day. The UI can scroll a heavy day; silently dropping
    # slide decks makes the plan feel untrustworthy.
    scheduled_entries = entries
    entries_by_date = (
        _spread_pass_timeline_entries(scheduled_entries, study_dates, slots_per_day, occupied_counts)
        if repeat_passes > 1
        else _spread_timeline_entries(scheduled_entries, study_dates, slots_per_day, occupied_counts)
    )
    if completed_today_entries:
        entries_by_date[start] = completed_today_entries + entries_by_date.get(start, [])
    blocks: list[CalendarBlockOut] = []

    for plan_date in study_dates:
        for entry in entries_by_date.get(plan_date, []):
            topic = entry["topic"]
            lecture = lecture_lookup.get(topic.id)
            minutes = max(15, min(entry["minutes"], 70))
            blocks.append(CalendarBlockOut(
                date=plan_date,
                lecture_id=lecture.id if lecture else None,
                topic_id=topic.id,
                course_name=topic.course.name if topic.course else "",
                course_color=topic.course.color if topic.course else "#007aff",
                title=_topic_display_title(topic, lecture),
                planned_minutes=minutes,
                status=_timeline_status(entry["phase"], assessment_label),
                priority=entry["priority"],
                pass_number=entry.get("pass_number"),
                pass_total=entry.get("pass_total"),
                assessment_id=target_assessment.id if target_assessment else None,
                assessment_title=target_assessment.title if target_assessment else None,
                assessment_type=assessment_label or None,
                days_until_assessment=(assessment_date - plan_date).days if assessment_date else None,
            ))

    if protected_review_date and start <= protected_review_date <= end:
        blocks.append(CalendarBlockOut(
            date=protected_review_date,
            title="Review selected slides",
            planned_minutes=max(30, settings.daily_minutes or 60),
            status="review",
            priority="review",
            assessment_id=target_assessment.id if target_assessment else None,
            assessment_title=target_assessment.title if target_assessment else None,
            assessment_type=assessment_label or None,
            days_until_assessment=(assessment_date - protected_review_date).days if assessment_date else None,
        ))
    return [block for block in blocks if block.date <= end]


def _spread_timeline_entries(
    entries: list[dict],
    study_dates: list[date],
    slots_per_day: int,
    occupied_counts: dict[date, int] | None = None,
) -> dict[date, list[dict]]:
    buckets: dict[date, list[dict]] = {study_date: [] for study_date in study_dates}
    occupied_counts = occupied_counts or {}
    if not entries or not study_dates:
        return buckets

    if len(entries) <= len(study_dates):
        for index, entry in enumerate(entries):
            date_index = 0 if len(entries) == 1 else round(index * (len(study_dates) - 1) / (len(entries) - 1))
            date_index = _nearest_available_study_date(buckets, study_dates, date_index, slots_per_day, occupied_counts)
            buckets[study_dates[date_index]].append(entry)
        return buckets

    for index, entry in enumerate(entries):
        preferred_index = min(len(study_dates) - 1, math.floor(index * len(study_dates) / len(entries)))
        date_index = _nearest_available_study_date(buckets, study_dates, preferred_index, slots_per_day, occupied_counts)
        buckets[study_dates[date_index]].append(entry)
    return buckets


def _spread_pass_timeline_entries(
    entries: list[dict],
    study_dates: list[date],
    slots_per_day: int,
    occupied_counts: dict[date, int] | None = None,
) -> dict[date, list[dict]]:
    buckets: dict[date, list[dict]] = {study_date: [] for study_date in study_dates}
    occupied_counts = occupied_counts or {}
    if not entries or not study_dates:
        return buckets

    pass_numbers = sorted({
        int(entry["pass_number"])
        for entry in entries
        if entry.get("pass_number")
    })
    if len(pass_numbers) <= 1:
        return _spread_timeline_entries(entries, study_dates, slots_per_day, occupied_counts)

    pass_total = max(pass_numbers)
    for pass_number in pass_numbers:
        pass_entries = [entry for entry in entries if int(entry.get("pass_number") or 0) == pass_number]
        pass_dates = _dates_for_study_pass(study_dates, pass_number, pass_total)
        pass_occupied_counts = {
            study_date: occupied_counts.get(study_date, 0) + len(buckets.get(study_date, []))
            for study_date in pass_dates
        }
        pass_buckets = _spread_timeline_entries(
            pass_entries,
            pass_dates,
            slots_per_day,
            pass_occupied_counts,
        )
        for study_date, date_entries in pass_buckets.items():
            buckets[study_date].extend(date_entries)

    return buckets


def _dates_for_study_pass(study_dates: list[date], pass_number: int, pass_total: int) -> list[date]:
    if not study_dates:
        return []
    if pass_total <= 1 or len(study_dates) == 1:
        return study_dates

    count = len(study_dates)
    start_index = round((pass_number - 1) * count / pass_total)
    end_index = round(pass_number * count / pass_total)
    start_index = max(0, min(count - 1, start_index))
    end_index = max(start_index + 1, min(count, end_index))
    return study_dates[start_index:end_index] or [study_dates[min(start_index, count - 1)]]


def _nearest_available_study_date(
    buckets: dict[date, list[dict]],
    study_dates: list[date],
    preferred_index: int,
    slots_per_day: int,
    occupied_counts: dict[date, int] | None = None,
) -> int:
    occupied_counts = occupied_counts or {}
    if len(buckets[study_dates[preferred_index]]) + occupied_counts.get(study_dates[preferred_index], 0) < slots_per_day:
        return preferred_index

    for distance in range(1, len(study_dates)):
        forward = preferred_index + distance
        if (
            forward < len(study_dates)
            and len(buckets[study_dates[forward]]) + occupied_counts.get(study_dates[forward], 0) < slots_per_day
        ):
            return forward
        backward = preferred_index - distance
        if (
            backward >= 0
            and len(buckets[study_dates[backward]]) + occupied_counts.get(study_dates[backward], 0) < slots_per_day
        ):
            return backward
    return preferred_index


def _study_slots_per_day(daily_minutes: int) -> int:
    return max(1, min(18, math.ceil(daily_minutes / 60)))


def _learn_minutes(topic: Topic) -> int:
    return max(30, min(topic.estimated_minutes or 45, 60))


def _repeat_pass_minutes(topic: Topic, pass_number: int) -> int:
    base = _learn_minutes(topic)
    limit = 45 if pass_number == 2 else 35
    return max(25, min(base, limit))


def _practice_minutes(settings: UserSettings) -> int:
    return 25 if settings.daily_minutes < 90 else 35


def _review_minutes(phase: str, settings: UserSettings) -> int:
    if phase == "weak":
        return 35 if settings.daily_minutes >= 90 else 25
    return 25


def _timeline_entry(
    topic: Topic,
    phase: str,
    priority: str,
    minutes: int,
    pass_number: int | None = None,
    pass_total: int | None = None,
) -> dict:
    return {
        "topic": topic,
        "phase": phase,
        "priority": priority or "normal",
        "minutes": minutes,
        "pass_number": pass_number,
        "pass_total": pass_total,
    }


def _timeline_status(phase: str, assessment_label: str) -> str:
    suffix = f" {assessment_label} prep" if assessment_label else " prep"
    return f"{phase}{suffix}"


def _clamp_study_passes(value: int | None) -> int:
    try:
        parsed = int(value or 1)
    except (TypeError, ValueError):
        parsed = 1
    return max(1, min(parsed, 3))


def _balanced_topic_order(topics: list[Topic], assessments: list[Assessment], start: date) -> list[Topic]:
    grouped: dict[int, list[Topic]] = defaultdict(list)
    for topic in sorted(topics, key=lambda item: (item.course_id, item.order, item.id)):
        grouped[topic.course_id].append(topic)

    course_dates: dict[int, int] = {}
    for assessment in assessments:
        if assessment.date < start:
            continue
        days = (assessment.date - start).days
        course_dates[assessment.course_id] = min(days, course_dates.get(assessment.course_id, days))

    course_ids = sorted(grouped, key=lambda course_id: (course_dates.get(course_id, 9999), course_id))
    ordered: list[Topic] = []
    while any(grouped.values()):
        for course_id in course_ids:
            if grouped[course_id]:
                ordered.append(grouped[course_id].pop(0))
    return ordered


def _topic_strengths(topics: list[Topic], attempts: list[QuizAttempt], lecture_lookup: dict[int, Lecture]) -> dict[int, float | None]:
    by_topic: dict[int, list[QuizAttempt]] = defaultdict(list)
    for attempt in attempts:
        by_topic[attempt.topic_id].append(attempt)

    strengths: dict[int, float | None] = {}
    for topic in topics:
        topic_attempts = sorted(by_topic.get(topic.id, []), key=lambda item: item.timestamp, reverse=True)
        if topic_attempts:
            recent = topic_attempts[:5]
            strengths[topic.id] = sum(item.score for item in recent) / len(recent)
            continue
        lecture = lecture_lookup.get(topic.id)
        strengths[topic.id] = lecture.mastery_score if lecture and lecture.mastery_score > 0 else None
    return strengths


def _last_studied_dates(sessions: list[StudySession]) -> dict[int, date]:
    result: dict[int, date] = {}
    for session in sessions:
        if not session.completed:
            continue
        current = result.get(session.topic_id)
        if current is None or session.date > current:
            result[session.topic_id] = session.date
    return result


def _dedupe_topics(topics: list[Topic]) -> list[Topic]:
    seen: set[int] = set()
    unique: list[Topic] = []
    for topic in topics:
        if topic.id in seen:
            continue
        seen.add(topic.id)
        unique.append(topic)
    return unique


def _weighted_review_cycle(topics: list[Topic], strengths: dict[int, float | None]) -> list[Topic]:
    weighted: list[Topic] = []
    for topic in topics:
        strength = strengths.get(topic.id)
        repeats = 3 if strength is not None and strength < 0.6 else 2 if strength is None or strength < 0.8 else 1
        weighted.extend([topic] * repeats)
    return weighted


@router.get("/instructor")
def instructor_dashboard(db: Session = Depends(get_db)):
    courses = db.query(Course).all()
    lectures = db.query(Lecture).all()
    questions = db.query(LessonQuestion).all()
    attempts = db.query(AnswerAttempt).all()

    missed: dict[int, int] = {}
    for attempt in attempts:
        if not attempt.is_correct:
            missed[attempt.question_id] = missed.get(attempt.question_id, 0) + 1

    most_missed = []
    for question_id, count in sorted(missed.items(), key=lambda item: item[1], reverse=True)[:8]:
        q = db.get(LessonQuestion, question_id)
        if q:
            most_missed.append({
                "question": q.prompt,
                "slide_number": q.slide_number,
                "topic": q.topic_tag,
                "misses": count,
            })

    weak = _weak_topics(db)
    falling_behind = [
        {"student": "Local student", "reason": f"{len(weak)} weak topics and {len(_review_items(db, 50))} review items"}
    ] if weak else []

    return {
        "class_progress": {
            "courses": len(courses),
            "lectures": len(lectures),
            "completion": round(sum(l.mastery_score for l in lectures) / len(lectures), 2) if lectures else 0,
        },
        "students_falling_behind": falling_behind,
        "most_missed_questions": most_missed,
        "weak_topics_by_cohort": weak,
        "lecture_completion": [
            {"lecture": l.title, "mastery": l.mastery_score, "status": "complete" if l.mastery_score >= 0.8 else "in progress"}
            for l in lectures
        ],
        "mastery_distribution": {
            "high": sum(1 for l in lectures if l.mastery_score >= 0.8),
            "medium": sum(1 for l in lectures if 0.5 <= l.mastery_score < 0.8),
            "low": sum(1 for l in lectures if l.mastery_score < 0.5),
        },
        "export_csv_url": "/api/learning/instructor/export.csv",
    }


@router.get("/instructor/export.csv")
def instructor_export(db: Session = Depends(get_db)):
    rows = ["lecture,mastery_score,questions,attempts"]
    for lecture in db.query(Lecture).all():
        rows.append(f'"{lecture.title}",{lecture.mastery_score},{len(lecture.questions)},{len(lecture.attempts)}')
    return Response("\n".join(rows), media_type="text/csv")


@router.get("/ai/status")
def ai_status():
    status = LocalAI().status()
    if status.get("available"):
        _warm_ollama_in_background()
    return status


@router.get("/connectors/blackboard")
def blackboard_connector_status():
    return {
        "provider": "Blackboard Learn",
        "status": "needs_ku_approval",
        "automatic_import_ready": False,
        "summary": "Direct sync can remove manual syllabus and slide uploads once KU enables a registered Blackboard REST, LTI, or OAuth integration.",
        "can_import": [
            "course roster and course list",
            "syllabus files and course content",
            "lecture PDFs, PPTX files, and attachments",
            "calendar items, due dates, and gradebook items",
        ],
        "requirements": [
            "Register StudyPace as a Blackboard developer application.",
            "Have KU Blackboard admin approve the REST or LTI integration.",
            "Use OAuth/LTI tokens so students never share passwords with StudyPace.",
            "Store synced content locally unless hosted mode is enabled.",
        ],
        "fallback": "Until the connector is approved, students can download Blackboard files and import them locally.",
    }


@router.post("/connectors/ku-ai-instructor/sync")
def ku_ai_instructor_sync(payload: dict, db: Session = Depends(get_db)):
    student_id = str(payload.get("student_id", "")).strip()
    password = str(payload.get("password", ""))
    if not student_id or not password:
        raise HTTPException(422, "Student ID and password are required for a sync attempt.")

    result = _sync_local_course_pack(db, source="KU AI Instructor")
    _award_badge(db, "source_connected", "Source connected", "Synced course material from a connected university source.")
    db.commit()

    return {
        "status": "synced",
        "source": "KU AI Instructor",
        "student_id": _mask_student_id(student_id),
        "password_stored": False,
        "synced_at": datetime.utcnow(),
        **result,
    }


@router.get("/retention")
def retention_dashboard(db: Session = Depends(get_db)):
    lectures = db.query(Lecture).options(selectinload(Lecture.attempts)).all()
    now = datetime.utcnow()
    retention_items = []

    for lecture in lectures:
        attempts = sorted(lecture.attempts, key=lambda attempt: attempt.created_at, reverse=True)
        attempts_count = len(attempts)
        if attempts:
            days_since = max(0, (now - attempts[0].created_at).days)
            recent_accuracy = sum(1 for attempt in attempts[:8] if attempt.is_correct) / min(8, attempts_count)
            strength = max(lecture.mastery_score, recent_accuracy)
        else:
            days_since = 999
            strength = lecture.mastery_score or 0.25

        half_life = 2 + (strength * 14) + min(attempts_count, 6) * 1.5
        retention = strength * 100 * math.pow(0.5, min(days_since, 30) / half_life)
        if not attempts:
            retention = 35 if lecture.mastery_score <= 0 else min(max(retention, 35), 65)
        retention_items.append({
            "lecture_id": lecture.id,
            "title": lecture.title,
            "retention": round(max(0, min(100, retention))),
            "days_since": days_since if days_since < 999 else None,
            "due": retention < 70,
            "reason": "Review now" if retention < 55 else "Refresh soon" if retention < 70 else "Looks stable",
        })

    review_count = len(_review_items(db, limit=100))
    recent_attempts = (
        db.query(AnswerAttempt)
        .filter(AnswerAttempt.created_at >= now - timedelta(days=7))
        .count()
    )
    settings = _settings(db)
    avg_retention = round(sum(item["retention"] for item in retention_items) / len(retention_items)) if retention_items else 0
    engagement = min(100, settings.streak * 8 + recent_attempts * 6 + max(0, 30 - review_count * 4) + (20 if retention_items else 0))

    recommendations = []
    due = [item for item in retention_items if item["due"]]
    if due:
        recommendations.append(f"Review {due[0]['title']} before learning something new.")
    if recent_attempts < 3:
        recommendations.append("Do a short quiz today; retrieval practice is the highest-signal retention check.")
    if review_count > 0:
        recommendations.append("Clear mistakes in Review to raise your retention estimate.")
    if not recommendations:
        recommendations.append("Keep the streak alive with one focused recall block.")

    return {
        "retention_score": avg_retention,
        "engagement_score": round(engagement),
        "recent_attempts_7d": recent_attempts,
        "review_debt": review_count,
        "items": sorted(retention_items, key=lambda item: item["retention"])[:6],
        "recommendations": recommendations,
    }


@router.post("/ai/explain")
def ai_explain(payload: dict, db: Session = Depends(get_db)):
    lecture_id = payload.get("lecture_id")
    question = payload.get("question", "")
    allow_general = bool(payload.get("allow_general", False))
    try:
        lecture = _get_lecture(db, int(lecture_id)) if lecture_id else None
    except (TypeError, ValueError):
        raise HTTPException(422, "lecture_id must be an integer")
    wants_summary = _asks_for_lecture_summary(question)
    context = _lecture_ai_context(lecture, comprehensive=wants_summary) if lecture else ""
    ai = LocalAI()
    answer = (
        ai.summarize_lecture(question, context, allow_general=allow_general)
        if wants_summary
        else ai.explain(question, context, allow_general=allow_general)
    )
    return {"answer": answer}


@router.post("/ai/chat")
def ai_chat(payload: dict, db: Session = Depends(get_db)):
    message = str(payload.get("message", "")).strip()
    if not message:
        raise HTTPException(422, "Message is required.")

    history = payload.get("history", [])
    if not isinstance(history, list):
        history = []

    lecture_id = _optional_int(payload.get("lecture_id"))
    course_id = _optional_int(payload.get("course_id"))
    explicit_general = bool(payload.get("allow_general", False)) or _asks_for_general_help(message)
    model_message = _strip_general_prefix(_strip_deep_model_prefix(message))
    if _is_quick_greeting(model_message.lower().strip()):
        instant = _instant_greeting(db, course_id)
        return {
            "answer": instant["answer"],
            "sources": [],
            "ai_status": _instant_tutor_status(),
            "local_context_used": False,
            "allow_general": True,
            "mode": "instant_greeting",
        }

    instant_casual = _instant_casual_general_answer(model_message)
    if instant_casual:
        return {
            "answer": instant_casual["answer"],
            "sources": [],
            "ai_status": _general_tutor_status(available=True, instant=True),
            "local_context_used": False,
            "allow_general": True,
            "mode": "instant_general",
        }

    material_intent = False if explicit_general else _should_answer_from_course_material(db, model_message, lecture_id, course_id)
    allow_general = explicit_general or not material_intent

    if allow_general:
        instant_general = _instant_general_answer(model_message)
        if instant_general:
            return {
                "answer": instant_general["answer"],
                "sources": [],
                "ai_status": _general_tutor_status(available=True, instant=True),
                "local_context_used": False,
                "allow_general": True,
                "mode": "instant_general",
            }

        ai = LocalAI()
        answer = _try_ollama_with_fast_budget(
            lambda: ai.general_chat(model_message, history=history),
            budget_seconds=OLLAMA_GENERAL_BUDGET_SECONDS,
            skip_seconds=2,
        )
        if answer is None or _ollama_missed_fast_budget(answer):
            return {
                "answer": _instant_general_timeout_answer(model_message),
                "sources": [],
                "ai_status": _general_tutor_status(available=False),
                "local_context_used": False,
                "allow_general": True,
                "mode": "general_timeout",
            }
        return {
            "answer": answer,
            "sources": [],
            "ai_status": _general_tutor_status(available=True),
            "local_context_used": False,
            "allow_general": True,
            "mode": "general_fast",
        }

    instant = _instant_tutor_answer(db, model_message, lecture_id=lecture_id, course_id=course_id)
    if instant:
        return {
            "answer": instant["answer"],
            "sources": instant.get("sources", []),
            "ai_status": _instant_tutor_status(),
            "local_context_used": instant.get("local_context_used", True),
            "allow_general": allow_general,
            "mode": "instant",
        }

    context, sources = _build_ai_chat_context(db, model_message, lecture_id=lecture_id, course_id=course_id)
    ai = LocalAI()
    history_for_answer = [] if _has_explicit_material_reference(db, model_message) else history
    wants_summary = _asks_for_lecture_summary(model_message)
    answer = _try_ollama_with_fast_budget(
        lambda: (
            ai.summarize_lecture(model_message, context, allow_general=allow_general)
            if wants_summary
            else ai.chat(model_message, context, history=history_for_answer, allow_general=allow_general)
        ),
        budget_seconds=OLLAMA_SUMMARY_BUDGET_SECONDS if wants_summary else OLLAMA_FAST_BUDGET_SECONDS,
    )
    if answer is None:
        return {
            "answer": "Ollama is still warming up, so I kept the chat fast instead of making you wait. Try a course-specific question, or try the Ollama answer again in a moment.",
            "sources": [],
            "ai_status": _ollama_fast_status(available=False),
            "local_context_used": bool(context.strip()),
            "allow_general": allow_general,
            "mode": "ollama_timeout",
        }
    return {
        "answer": answer,
        "sources": sources,
        "ai_status": _ollama_fast_status(available=True),
        "local_context_used": bool(context.strip()),
        "allow_general": allow_general,
        "mode": "ollama_fast",
    }


def _instant_tutor_status() -> dict:
    return {
        "available": True,
        "provider": "local",
        "model": "instant local tutor",
        "preferred_model": "instant",
        "installed_models": [],
        "offline_ready": True,
    }


def _ollama_fast_status(available: bool) -> dict:
    return {
        "available": available,
        "provider": "ollama",
        "model": "fast budget",
        "preferred_model": "fast budget",
        "installed_models": [],
        "offline_ready": available,
    }


def _general_tutor_status(available: bool, instant: bool = False) -> dict:
    return {
        "available": available,
        "provider": "local" if instant else "ollama",
        "model": "instant general tutor" if instant else "fast general tutor",
        "preferred_model": "instant" if instant else "fast general",
        "installed_models": [],
        "offline_ready": available or instant,
    }


def _warm_ollama_in_background() -> None:
    global _OLLAMA_WARMED_UNTIL

    now = time.monotonic()
    if now < _OLLAMA_WARMED_UNTIL:
        return
    if not _OLLAMA_WARMUP_LOCK.acquire(blocking=False):
        return
    _OLLAMA_WARMED_UNTIL = now + 300

    def warm() -> None:
        try:
            LocalAI().warmup()
        finally:
            _OLLAMA_WARMUP_LOCK.release()

    threading.Thread(target=warm, daemon=True).start()


def _instant_context_for_ollama(instant: dict) -> str:
    answer = str(instant.get("answer", "")).strip()
    source_lines = []
    for source in instant.get("sources", [])[:5]:
        source_lines.append(
            f"- {source.get('course', 'Course')}, {source.get('lecture', 'Lecture')}, "
            f"slide {source.get('slide_number')}: {source.get('title', '')}"
        )
    if source_lines:
        return answer + "\n\nSources:\n" + "\n".join(source_lines)
    return answer


def _try_ollama_with_fast_budget(work, budget_seconds: float, skip_seconds: float = 20) -> str | None:
    global _OLLAMA_SKIP_UNTIL

    now = time.monotonic()
    if now < _OLLAMA_SKIP_UNTIL:
        return None
    if not _OLLAMA_LOCK.acquire(blocking=False):
        return None

    def guarded_work():
        try:
            return work()
        finally:
            _OLLAMA_LOCK.release()

    future = _OLLAMA_EXECUTOR.submit(guarded_work)
    try:
        return future.result(timeout=budget_seconds)
    except FutureTimeout:
        _OLLAMA_SKIP_UNTIL = time.monotonic() + skip_seconds
        return None
    except Exception:
        return None


def _instant_tutor_answer(
    db: Session,
    message: str,
    lecture_id: int | None = None,
    course_id: int | None = None,
) -> dict | None:
    lower = message.lower().strip()
    if _is_quick_greeting(lower):
        return _instant_greeting(db, course_id)
    if _asks_for_tutor_status(message):
        return _instant_tutor_status_answer()
    if "fallback" in lower:
        return {
            "answer": (
                "Fallback means I answered from the indexed course data without waiting for the local language model. "
                "It is faster and usually better for study-plan, weak-topic, grade, and course-status questions. "
                "General questions are allowed too; if a question does not match your course material, I answer it in general mode."
            ),
            "sources": [],
        }
    if _asks_for_study_state(message) or any(word in lower for word in ["weak", "deadline", "exam", "quiz", "grade"]):
        return _instant_study_state_answer(db, message, course_id)
    if _asks_for_lecture_summary(message):
        return _instant_lecture_summary_answer(db, message, lecture_id, course_id)
    if _should_include_slide_context(message, lecture_id, course_id):
        material = _instant_material_answer(db, message, lecture_id, course_id)
        if material:
            return material
        if _looks_like_unmatched_general_question(message):
            return _instant_general_boundary_answer(message)
    if _asks_for_general_help(message):
        return None
    return {
        "answer": (
            "I am fastest with your slides, topics, grades, and study plan. "
            "Ask me things like `what should I study next`, `explain lecture 4`, `summarize chapter 2`, or `what are my weak topics`. "
            "You can also ask normal general questions; I will stop trying to force those into your slides."
        ),
        "sources": [],
        "local_context_used": False,
    }


def _instant_general_boundary_answer(message: str) -> dict:
    return {
        "answer": (
            f"`{message.strip()}` does not look like something I can answer from your indexed course material.\n\n"
            "I will not fake a course answer from unrelated slides. Ask it as a normal general question, or mention a lecture, slide, topic, weak area, assessment, grade, or study plan if you want the answer grounded in your course."
        ),
        "sources": [],
        "local_context_used": False,
    }


def _instant_general_answer(message: str) -> dict | None:
    casual = _instant_casual_general_answer(message)
    if casual:
        return casual

    normalized = re.sub(r"[^a-z0-9\s]+", " ", message.lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if normalized in {"what is life", "define life", "what does life mean"}:
        return {
            "answer": (
                "Life can mean two things. Biologically, it is the condition of an organism that can grow, use energy, respond, reproduce, and maintain itself. "
                "Philosophically, life is the lived experience of being conscious, making choices, building relationships, and giving meaning to what happens."
            )
        }
    if normalized in {"what is the meaning of life", "meaning of life", "what is lifes meaning"}:
        return {
            "answer": (
                "There is no single proven answer. A practical answer is that meaning is built from what you choose to care about: people, growth, service, faith, curiosity, creativity, and the responsibilities you accept."
            )
        }
    if normalized in {"who are you", "what are you", "what is study pace", "what is studypace"}:
        return {
            "answer": (
                "I am StudyPace's tutor. I can answer normal general questions, and when you ask about your course I use your uploaded syllabus, slides, grades, weak topics, and study plan first."
            )
        }
    if normalized in {"what is love", "define love"}:
        return {
            "answer": (
                "Love is a deep form of care and attachment. It can be romantic, family-based, friendly, or moral, but in every form it usually means valuing someone enough that their wellbeing matters to you."
            )
        }
    if normalized in {"what is time", "define time"}:
        return {
            "answer": (
                "Time is how we measure change and order events. In daily life it tells us what happened before or after; in physics it is a dimension used to describe motion and cause-and-effect."
            )
        }
    if normalized in {"what is photosynthesis", "define photosynthesis"}:
        return {
            "answer": (
                "Photosynthesis is the process plants, algae, and some bacteria use to turn light energy, water, and carbon dioxide into glucose. Oxygen is released as a byproduct."
            )
        }
    if normalized in {"what is gravity", "define gravity"}:
        return {
            "answer": (
                "Gravity is the force of attraction between objects with mass. On Earth, it pulls objects toward the ground; in space, it helps planets, moons, and stars hold their orbits."
            )
        }
    if normalized in {"what is an atom", "what is atom", "define atom"}:
        return {
            "answer": (
                "An atom is the basic unit of ordinary matter. It has a nucleus made of protons and neutrons, with electrons around it."
            )
        }
    if normalized in {"what is energy", "define energy"}:
        return {
            "answer": (
                "Energy is the ability to do work or cause change. It can appear as motion, heat, light, chemical energy, electrical energy, and other forms."
            )
        }
    if normalized in {"what is artificial intelligence", "what is ai", "define ai", "define artificial intelligence"}:
        return {
            "answer": (
                "Artificial intelligence is software that performs tasks we usually associate with human intelligence, such as understanding language, recognizing patterns, making predictions, or solving problems."
            )
        }
    if normalized in {"teach me", "teach me something", "help me learn"}:
        return {
            "answer": (
                "Tell me the topic and I will teach it step by step. For example: `teach me merge sort`, `teach me Dijkstra`, or `teach me today’s weakest topic`."
            )
        }
    return None


def _instant_casual_general_answer(message: str) -> dict | None:
    normalized = re.sub(r"[^a-z0-9\s]+", " ", message.lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if normalized in {
        "how is it going",
        "hows it going",
        "how are things",
        "how are you doing",
        "how you doing",
        "whats up",
        "what is up",
        "how goes it",
    }:
        return {
            "answer": "Going well. I am here and ready. Ask me a normal question, or ask about your slides, plan, weak topics, grades, or a lecture."
        }
    return None


def _instant_general_timeout_answer(message: str) -> str:
    return (
        "I can answer general questions too, and I will not force this into your slides. "
        "The local general model missed the fast budget, so I kept the chat responsive instead of making you wait. "
        "Ask it again in a shorter form, or try once more after the model has warmed up."
    )


def _looks_like_unmatched_general_question(message: str) -> bool:
    lower = message.lower().strip()
    return (
        "?" in lower
        or re.match(r"^(?:what|why|how|who|where|when|is|are|can|could|should|do|does|did)\b", lower) is not None
        or lower.startswith(("tell me ", "define ", "meaning of "))
    )


def _instant_tutor_status_answer() -> dict:
    return {
        "answer": (
            "Yes. For normal tutor chat I am working offline-first from your local StudyPace data: indexed slides, lecture summaries, practice questions, grades, weak topics, and study plan.\n\n"
            "What happens by mode:\n"
            "- Course questions: instant local answer from your indexed database and slides.\n"
            "- General questions: answered normally without forcing random slide matches.\n"
            "- Claude: not used for normal chat. Claude is only for generating lecture summaries/questions during upload or batch regeneration, and it needs Anthropic API credits/network.\n\n"
            "So this chat should not search random slides for questions like `what is life`; it should answer in general mode."
        ),
        "sources": [],
        "local_context_used": False,
    }


def _asks_for_tutor_status(message: str) -> bool:
    lower = re.sub(r"[^a-z0-9\s:]+", " ", message.lower())
    lower = re.sub(r"\s+", " ", lower).strip()
    status_phrases = [
        "are you working offline",
        "are you offline",
        "working offline",
        "work offline",
        "offline mode",
        "offline first",
        "are you local",
        "working locally",
        "work locally",
        "local tutor",
        "do you need internet",
        "using internet",
        "use internet",
        "using claude",
        "use claude",
        "using ollama",
        "use ollama",
        "what model are you using",
        "which model are you using",
        "how are you answering",
        "where are your answers coming from",
        "where do your answers come from",
    ]
    return any(phrase in lower for phrase in status_phrases)


def _is_quick_greeting(lower: str) -> bool:
    normalized = re.sub(r"[^a-z ]+", "", lower).strip()
    if normalized in {
        "hi", "hello", "hey", "yo", "sup", "salam", "good morning", "good afternoon", "good evening",
        "hey how are you", "how are you", "how r u",
    }:
        return True

    tokens = normalized.split()
    if not tokens or len(tokens) > 6:
        return False
    greeting_words = {"hi", "hello", "hey", "yo", "sup", "salam"}
    friendly_fillers = {
        "there", "man", "bro", "dude", "mate", "friend", "fam", "boss",
        "how", "are", "you", "ya", "doing",
    }
    return tokens[0] in greeting_words and all(token in friendly_fillers for token in tokens[1:])


def _instant_greeting(db: Session, course_id: int | None) -> dict:
    course = db.get(Course, course_id) if course_id else db.query(Course).order_by(Course.id).first()
    course_line = f" I am looking at {course.name}." if course else ""
    weak = _weak_topics(db)
    weak_line = f" Your weakest topic right now is {weak[0]['topic']}." if weak else ""
    return {
        "answer": (
            f"Hey, I am ready.{course_line}{weak_line}\n\n"
            "Fast commands:\n"
            "- what should I study next\n"
            "- explain lecture 4\n"
            "- summarize this lecture\n"
            "- what are my weak topics"
        ),
        "sources": [],
    }


def _instant_study_state_answer(db: Session, message: str, course_id: int | None) -> dict:
    today = date.today()
    course = db.get(Course, course_id) if course_id else None
    weak = _weak_topics(db)
    upcoming = _upcoming_deadlines(db)
    grades_query = db.query(GradeItem).join(Course)
    if course_id:
        grades_query = grades_query.filter(GradeItem.course_id == course_id)
    grades = grades_query.order_by(Course.name, GradeItem.weight_pct.desc()).limit(6).all()

    lines = []
    if course:
        lines.append(f"Course: {course.name}")
        if course.exam_date:
            days = (course.exam_date - today).days
            lines.append(f"Exam date: {course.exam_date} ({max(days, 0)} days left)")

    if "grade" in message.lower() or "gpa" in message.lower() or "cgpa" in message.lower():
        if grades:
            lines.append("Grade weights:")
            lines.extend(
                f"- {item.title}: {item.weight_pct:g}%"
                + (f", current {item.current_score:g}%" if item.current_score is not None else "")
                + (f", due {item.due_date}" if item.due_date else "")
                for item in grades
            )
        else:
            lines.append("I do not have grade weights for this selection yet.")

    quick_targets = _quick_study_targets(db, course_id)
    if quick_targets:
        lines.append("Do next:")
        lines.extend(
            f"- {item['topic_name']} ({short_course_name(item['course_name'])}): {item['planned_minutes']} min"
            for item in quick_targets[:4]
        )
    else:
        plan = _plan_for(db, today)
        if plan.get("items"):
            lines.append("Do next:")
            lines.extend(
                f"- {item['topic_name']} ({short_course_name(item['course_name'])}): {item['planned_minutes']} min"
                for item in plan["items"][:3]
            )
        else:
            lines.append("No study blocks are scheduled for today.")

    if weak:
        lines.append("Weak topics:")
        lines.extend(
            f"- {item['topic']}: {round(item['avg_score'] * 100)}% across {item['attempts']} attempt(s)"
            for item in weak[:3]
        )

    if upcoming:
        lines.append("Upcoming:")
        lines.extend(f"- {item['title']} on {item['date']} ({item['type']})" for item in upcoming[:3])

    return {"answer": "\n".join(lines), "sources": []}


def _quick_study_targets(db: Session, course_id: int | None, limit: int = 4) -> list[dict]:
    query = (
        db.query(Lecture)
        .options(joinedload(Lecture.course), joinedload(Lecture.topic))
        .filter(Lecture.topic_id.isnot(None), Lecture.source_type.notin_(["demo", "sync"]))
    )
    if course_id:
        query = query.filter(Lecture.course_id == course_id)
    lectures = sorted(query.limit(80).all(), key=_lecture_plan_sort_key)
    seen_topics: set[int] = set()
    targets = []
    for lecture in lectures:
        if not lecture.topic_id or lecture.topic_id in seen_topics:
            continue
        seen_topics.add(lecture.topic_id)
        topic = lecture.topic
        course = lecture.course
        targets.append({
            "topic_id": lecture.topic_id,
            "topic_name": topic.name if topic else lecture.title,
            "course_name": course.name if course else "Course",
            "planned_minutes": min(max(lecture.estimated_minutes or (topic.estimated_minutes if topic else 30), 15), 60),
        })
        if len(targets) >= limit:
            break
    return targets


def _instant_lecture_summary_answer(
    db: Session,
    message: str,
    lecture_id: int | None,
    course_id: int | None,
) -> dict:
    scope = _resolve_ai_scope(db, message, lecture_id=lecture_id, course_id=course_id)
    if scope["lecture_id"]:
        lecture = _get_lecture(db, scope["lecture_id"])
        return {
            "answer": _fast_lecture_briefing(lecture),
            "sources": _lecture_sources(lecture),
        }

    lectures_query = (
        db.query(Lecture)
        .options(joinedload(Lecture.course))
        .order_by(Lecture.created_at.asc(), Lecture.id.asc())
    )
    if scope["course_id"]:
        lectures_query = lectures_query.filter(Lecture.course_id == scope["course_id"])
    lectures = lectures_query.limit(8).all()
    if not lectures:
        return {
            "answer": "I do not see a lecture to summarize yet. Upload slides or choose a course lecture first.",
            "sources": [],
        }
    return {
        "answer": (
            "I can summarize instantly, but I need the lecture target. Try one of these:\n"
            + "\n".join(f"- summarize lecture {idx + 1}: {lecture.title}" for idx, lecture in enumerate(lectures[:6]))
        ),
        "sources": [],
    }


def _fast_lecture_briefing(lecture: Lecture) -> str:
    concepts = _clean_display_list(_json_load(lecture.key_concepts_json, []))
    objectives = _json_load(lecture.learning_objectives_json, [])
    slides = sorted(lecture.slides, key=lambda item: item.slide_number)
    parts = [
        f"{lecture.title}",
        _clip(lecture.summary, 700) or "No stored summary is available yet.",
    ]
    if concepts:
        parts.append("Key concepts:\n" + "\n".join(f"- {item}" for item in concepts[:8]))
    if objectives:
        parts.append("Learning goals:\n" + "\n".join(f"- {item}" for item in objectives[:5]))
    if slides:
        sample = slides[:5] if len(slides) <= 8 else slides[:3] + slides[-2:]
        parts.append("Slide map:\n" + "\n".join(
            f"- Slide {slide.slide_number}: {_slide_display_title(slide)} - {_clip(_clean_display_text(slide.text), 160)}"
            for slide in sample
        ))
    return "\n\n".join(part for part in parts if part)


def _instant_material_answer(
    db: Session,
    message: str,
    lecture_id: int | None,
    course_id: int | None,
) -> dict | None:
    scope = _resolve_ai_scope(db, message, lecture_id=lecture_id, course_id=course_id)
    lectures_query = (
        db.query(Lecture)
        .options(joinedload(Lecture.course), selectinload(Lecture.slides))
        .order_by(Lecture.created_at.asc(), Lecture.id.asc())
    )
    if scope["lecture_id"]:
        lectures_query = lectures_query.filter(Lecture.id == scope["lecture_id"])
    elif scope["course_id"]:
        lectures_query = lectures_query.filter(Lecture.course_id == scope["course_id"])
    lectures = lectures_query.limit(18).all()
    if not lectures:
        return None

    ranked = _rank_lecture_slides(lectures, message, scope=scope)[:4]
    useful = [item for item in ranked if item["score"] > 0 and item.get("strong_match")]
    if not useful:
        return None

    lines = ["Fast answer from your indexed slides:"]
    sources = []
    for item in useful[:3]:
        lecture = item["lecture"]
        slide = item["slide"]
        course_name = lecture.course.name if lecture.course else "Course"
        lecture_title = _lecture_display_title(lecture)
        title = _slide_display_title(slide)
        lines.append(f"- {course_name}, {lecture_title}, slide {slide.slide_number}: {title}. {_clip(_clean_display_text(slide.text), 260)}")
        sources.append({
            "type": "slide",
            "course": course_name,
            "lecture_id": lecture.id,
            "lecture": lecture_title,
            "slide_number": slide.slide_number,
            "title": title,
        })
    lines.append("If your question is not about the course, I answer it in general mode instead of forcing slide matches.")
    return {"answer": "\n".join(lines), "sources": sources}


def _asks_for_deep_model(message: str) -> bool:
    lower = message.lower()
    return any(phrase in lower for phrase in [
        "deep model",
        "use ollama",
        "slow answer",
        "generated answer",
        "think deeply",
        "full ai",
    ])


def _strip_deep_model_prefix(message: str) -> str:
    return re.sub(
        r"^\s*(?:deep model|use ollama|slow answer|generated answer|think deeply|full ai)\s*:?\s*",
        "",
        message,
        flags=re.IGNORECASE,
    ).strip() or message


def _strip_general_prefix(message: str) -> str:
    return re.sub(
        r"^\s*(?:use\s+general|general|outside\s+(?:the\s+)?slides|outside\s+course(?:\s+material)?)\s*:?\s*",
        "",
        message,
        flags=re.IGNORECASE,
    ).strip() or message


def _ollama_missed_fast_budget(answer: str) -> bool:
    lower = (answer or "").lower()
    return lower.startswith("ollama is busy") or lower.startswith("ollama is not reachable")


def short_course_name(name: str = "") -> str:
    return name.split(" - ")[0].replace("COSC", "COSC ")


def _build_ai_chat_context(db: Session, message: str, lecture_id: int | None = None, course_id: int | None = None) -> tuple[str, list[dict]]:
    sections: list[str] = []
    sources: list[dict] = []
    today = date.today()
    scope = _resolve_ai_scope(db, message, lecture_id=lecture_id, course_id=course_id)
    resolved_course_id = scope["course_id"]
    resolved_lecture_id = scope["lecture_id"]
    specific_material_target = (
        bool(resolved_lecture_id or resolved_course_id)
        and _should_include_slide_context(message, resolved_lecture_id, resolved_course_id)
        and not _asks_for_study_state(message)
    )

    if scope["notes"]:
        sections.append("Resolved student reference:\n" + "\n".join(f"- {note}" for note in scope["notes"]))

    courses_query = db.query(Course).order_by(Course.name)
    if resolved_course_id:
        courses_query = courses_query.filter(Course.id == resolved_course_id)
    courses = courses_query.limit(12).all()
    if courses:
        sections.append("Courses:\n" + "\n".join(
            f"- {course.name}; exam date: {course.exam_date or 'not set'}; total hours: {course.total_hours:g}"
            for course in courses
        ))

    if not specific_material_target:
        try:
            plan = _plan_for(db, today)
            if plan["items"]:
                sections.append("Today's study plan:\n" + "\n".join(
                    f"- {item['topic_name']} ({item['course_name']}): {item['planned_minutes']} min, priority {item['priority_score']}"
                    for item in plan["items"][:5]
                ))
        except Exception:
            pass

        weak = _weak_topics(db)
        if weak:
            sections.append("Weak topics:\n" + "\n".join(
                f"- {item['topic']}: average quiz score {round(item['avg_score'] * 100)}% across {item['attempts']} attempt(s)"
                for item in weak[:5]
            ))

        review = _review_items(db, limit=5)
        if review:
            sections.append("Recent mistakes:\n" + "\n".join(
                f"- Slide {item['slide_number']}, {item['topic']}: {item['question']} Feedback: {item['feedback']}"
                for item in review
            ))

        upcoming = _upcoming_deadlines(db)
        if upcoming:
            sections.append("Upcoming assessments:\n" + "\n".join(
                f"- {item['title']} on {item['date']} ({item['type']})"
                for item in upcoming[:6]
            ))

        grades_query = db.query(GradeItem).join(Course)
        if resolved_course_id:
            grades_query = grades_query.filter(GradeItem.course_id == resolved_course_id)
        grades = grades_query.order_by(Course.name, GradeItem.weight_pct.desc()).limit(16).all()
        if grades:
            sections.append("Grade weights:\n" + "\n".join(
                f"- {item.course.name}: {item.title} is {item.weight_pct:g}%"
                + (f", due {item.due_date}" if item.due_date else "")
                + (f", current score {item.current_score:g}%" if item.current_score is not None else "")
                for item in grades
            ))

    lecture_query = (
        db.query(Lecture)
        .options(joinedload(Lecture.course), selectinload(Lecture.slides), selectinload(Lecture.questions))
        .order_by(Lecture.created_at.asc(), Lecture.id.asc())
    )
    if resolved_lecture_id:
        lecture_query = lecture_query.filter(Lecture.id == resolved_lecture_id)
    elif resolved_course_id:
        lecture_query = lecture_query.filter(Lecture.course_id == resolved_course_id)
    lectures = lecture_query.limit(50).all()

    if lectures:
        if resolved_lecture_id and _asks_for_lecture_summary(message):
            lecture = lectures[0]
            sections.append(_lecture_ai_context(lecture, comprehensive=True))
            sources.extend(_lecture_sources(lecture))
        else:
            sections.append("Available matching lectures:\n" + "\n".join(
                f"- {idx + 1}. {lecture.title} ({lecture.course.name if lecture.course else 'Course'}; file {lecture.source_filename})"
                for idx, lecture in enumerate(lectures[:12])
            ))

    ranked_slides = []
    if not (resolved_lecture_id and _asks_for_lecture_summary(message)) and _should_include_slide_context(message, resolved_lecture_id, resolved_course_id):
        ranked_slides = _rank_lecture_slides(lectures, message, scope=scope)
    for item in ranked_slides[:10]:
        lecture = item["lecture"]
        slide = item["slide"]
        course_name = lecture.course.name if lecture.course else "Course"
        lecture_title = _lecture_display_title(lecture)
        title = _slide_display_title(slide)
        sections.append(
            f"Course material source:\n"
            f"Course: {course_name}\n"
            f"Lecture: {lecture_title}\n"
            f"Slide {slide.slide_number}: {title}\n"
            f"Lecture summary: {_clip(lecture.summary, 420)}\n"
            f"Slide text: {_clip(_clean_display_text(slide.text), 760)}"
        )
        sources.append({
            "type": "slide",
            "course": course_name,
            "lecture_id": lecture.id,
            "lecture": lecture_title,
            "slide_number": slide.slide_number,
            "title": title,
        })

    if lectures:
        selected = lectures[0] if resolved_lecture_id else None
        if selected:
            practice = sorted(selected.questions, key=_question_sort_key)[:8]
            if practice:
                sections.append("Practice prompts for current lecture:\n" + "\n".join(
                    f"- {q.topic_tag}; slide {q.slide_number}; {_question_type(q)}: {q.prompt}"
                    for q in practice
                ))

    if not sections:
        sections.append("No local course material has been synced yet. Ask the student to sync a source or upload slides first.")

    return "\n\n".join(sections), sources


def _resolve_ai_scope(db: Session, message: str, lecture_id: int | None = None, course_id: int | None = None) -> dict:
    notes: list[str] = []
    resolved_course_id = course_id
    resolved_lecture_id = lecture_id
    lower = message.lower()

    matched_course = db.get(Course, course_id) if course_id else _match_course_from_message(db, message)
    if matched_course:
        resolved_course_id = matched_course.id
        notes.append(f"Course interpreted as {matched_course.name}.")

    if not resolved_lecture_id:
        lecture_candidates_query = (
            db.query(Lecture)
            .options(joinedload(Lecture.course), joinedload(Lecture.topic))
            .order_by(Lecture.created_at.asc(), Lecture.id.asc())
        )
        if resolved_course_id:
            lecture_candidates_query = lecture_candidates_query.filter(Lecture.course_id == resolved_course_id)
        lecture_candidates = lecture_candidates_query.limit(120).all()
        matched_lecture = _match_lecture_from_message(lecture_candidates, lower)
        if matched_lecture:
            resolved_lecture_id = matched_lecture.id
            if not resolved_course_id:
                resolved_course_id = matched_lecture.course_id
            course_name = matched_lecture.course.name if matched_lecture.course else "course"
            notes.append(f"Lecture interpreted as {matched_lecture.title} in {course_name}.")

    return {
        "course_id": resolved_course_id,
        "lecture_id": resolved_lecture_id,
        "notes": notes,
        "mentioned_lecture_number": _mentioned_lecture_number(lower),
        "mentioned_chapter_part": _mentioned_chapter_part(lower),
    }


def _match_course_from_message(db: Session, message: str) -> Course | None:
    lower = message.lower()
    best: tuple[int, Course] | None = None
    for course in db.query(Course).order_by(Course.name).all():
        aliases = _course_aliases(course)
        score = 0
        for alias in aliases:
            if alias and _phrase_in_message(alias, lower):
                score = max(score, len(alias.split()) * 4 + min(len(alias), 24))
        if score and (best is None or score > best[0]):
            best = (score, course)
    return best[1] if best else None


def _has_explicit_material_reference(db: Session, message: str) -> bool:
    lower = message.lower()
    if re.search(r"\b(?:lecture|lec|lesson|slide|chapter|ch|part)\s*(?:\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)\b", lower):
        return True
    return _match_course_from_message(db, message) is not None and any(
        cue in lower for cue in ["lecture", "lec", "lesson", "slide", "chapter", "topic", "explain", "problem", "question"]
    )


def _should_answer_from_course_material(
    db: Session,
    message: str,
    lecture_id: int | None = None,
    course_id: int | None = None,
) -> bool:
    lower = message.lower().strip()
    if not lower:
        return False
    if _instant_casual_general_answer(message):
        return False
    if _is_quick_greeting(lower) or _asks_for_tutor_status(message):
        return True
    if _asks_for_study_state(message) or _asks_for_lecture_summary(message):
        return True
    if _has_explicit_material_reference(db, message):
        return True
    if any(cue in lower for cue in [
        "my slides",
        "your slides",
        "uploaded slides",
        "course material",
        "from the slides",
        "according to the slides",
        "in this course",
        "for this course",
    ]):
        return True

    tokens = _ai_tokens(message)
    if not tokens:
        return False
    if len(tokens) == 1 and next(iter(tokens)) in _broad_general_single_tokens():
        return False

    scope = _resolve_ai_scope(db, message, lecture_id=lecture_id, course_id=course_id)
    lectures_query = (
        db.query(Lecture)
        .options(joinedload(Lecture.course), joinedload(Lecture.topic), selectinload(Lecture.slides))
        .order_by(Lecture.created_at.asc(), Lecture.id.asc())
    )
    if scope["lecture_id"]:
        lectures_query = lectures_query.filter(Lecture.id == scope["lecture_id"])
    elif scope["course_id"]:
        lectures_query = lectures_query.filter(Lecture.course_id == scope["course_id"])
    lectures = lectures_query.limit(60).all()
    if not lectures:
        return False

    ranked = _rank_lecture_slides(lectures, message, scope=scope)
    best = next((item for item in ranked if item["score"] > 0 and item.get("strong_match")), None)
    if not best:
        return False
    if len(tokens) == 1:
        return _single_token_has_material_anchor(next(iter(tokens)), best)
    return best["score"] >= 2 or _token_overlap_in_material_anchor(tokens, best) >= 2


def _broad_general_single_tokens() -> set[str]:
    return {
        "beauty",
        "death",
        "faith",
        "friendship",
        "god",
        "happiness",
        "hope",
        "life",
        "love",
        "meaning",
        "money",
        "purpose",
        "success",
        "time",
        "truth",
    }


def _single_token_has_material_anchor(token: str, ranked_item: dict) -> bool:
    return _token_overlap_in_material_anchor({token}, ranked_item) > 0


def _token_overlap_in_material_anchor(tokens: set[str], ranked_item: dict) -> int:
    lecture = ranked_item["lecture"]
    slide = ranked_item["slide"]
    tags = ", ".join(str(item) for item in _json_load(slide.content_tags_json, []))
    anchor = " ".join([
        lecture.course.name if lecture.course else "",
        lecture.title or "",
        lecture.source_filename or "",
        lecture.topic.name if lecture.topic else "",
        lecture.topic.chapter if lecture.topic else "",
        slide.title or "",
        tags,
    ])
    words = set(re.findall(r"[a-z0-9]+", anchor.lower()))
    overlap = 0
    for token in tokens:
        variants = {token}
        if not token.endswith("s"):
            variants.add(f"{token}s")
        if token.endswith("ies"):
            variants.add(f"{token[:-3]}y")
        elif token.endswith("s") and len(token) > 4:
            variants.add(token[:-1])
        if words.intersection(variants):
            overlap += 1
    return overlap


def _course_aliases(course: Course) -> set[str]:
    raw = (course.name or "").lower()
    cleaned = re.sub(r"\([^)]*\)", " ", raw)
    pieces = [piece.strip() for piece in re.split(r"[-:|]", cleaned) if piece.strip()]
    aliases = {cleaned.strip(), raw.strip(), *pieces}
    aliases.update(re.findall(r"\b[a-z]{2,}\s*\d{3}\b", raw))
    aliases.update(re.findall(r"\b[a-z]{2,}\d{3}\b", raw))
    replacements = {
        "automata, computability, and complexity": "automata",
        "automata computability and complexity": "automata",
        "introduction to ai": "intro to ai",
        "introduction to artificial intelligence": "intro to ai",
        "operating systems": "os",
        "data structures": "data structures",
    }
    for long_name, short_name in replacements.items():
        if long_name in cleaned:
            aliases.add(short_name)
    for piece in list(aliases):
        aliases.add(piece.replace(" ", ""))
    return {alias.strip() for alias in aliases if len(alias.strip()) >= 2}


def _match_lecture_from_message(lectures: list[Lecture], lower_message: str) -> Lecture | None:
    chapter_part = _mentioned_chapter_part(lower_message)
    if chapter_part:
        exact = [
            lecture for lecture in lectures
            if _lecture_matches_chapter_part(lecture, chapter_part[0], chapter_part[1])
        ]
        if exact:
            exact.sort(key=lambda lecture: (
                _chapter_part_match_score(lecture, chapter_part[0], chapter_part[1], lower_message),
                lecture.created_at,
                lecture.id,
            ), reverse=True)
            return exact[0]

    lecture_number = _mentioned_lecture_number(lower_message)
    if lecture_number:
        numbered = [lecture for lecture in lectures if _lecture_matches_number(lecture, lecture_number)]
        if numbered:
            return numbered[0]
        index = lecture_number - 1
        if 0 <= index < len(lectures):
            return lectures[index]

    tokens = _ai_tokens(lower_message)
    best: tuple[int, Lecture] | None = None
    for lecture in lectures:
        haystack = " ".join([
            lecture.title or "",
            lecture.source_filename or "",
            lecture.summary or "",
            lecture.topic.name if lecture.topic else "",
            lecture.course.name if lecture.course else "",
        ])
        score = _ai_relevance_score(tokens, haystack)
        title = (lecture.title or "").lower()
        filename = (lecture.source_filename or "").lower()
        if title and title in lower_message:
            score += 12
        if filename and filename in lower_message:
            score += 8
        if score and (best is None or score > best[0]):
            best = (score, lecture)
    return best[1] if best and best[0] >= 2 else None


def _mentioned_lecture_number(lower_message: str) -> int | None:
    patterns = [
        r"\b(?:lecture|lec|lesson)\s*(\d{1,2})\b",
        r"\b(?:chapter|ch)\s*(\d{1,2}(?:\.\d+)?)\b",
        r"\bpart\s*(\d{1,2})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, lower_message)
        if match:
            try:
                return max(1, int(float(match.group(1))))
            except ValueError:
                return None
    words = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
        "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    }
    match = re.search(r"\b(?:lecture|lec|lesson|chapter|ch)\s+([a-z]+)\b", lower_message)
    return words.get(match.group(1)) if match else None


def _mentioned_chapter_part(lower_message: str) -> tuple[int, int] | None:
    patterns = [
        r"\b(?:chapter|ch)\s*(\d{1,2})\s*(?:\.|,?\s*part\s*|,?\s*pt\s*)(\d{1,2})\b",
        r"\b(?:chapter|ch)\s*(\d{1,2})\s*,\s*(?:part|pt)\s*(\d{1,2})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, lower_message)
        if match:
            return int(match.group(1)), int(match.group(2))
    return None


def _lecture_matches_number(lecture: Lecture, number: int) -> bool:
    value = _lecture_reference_text(lecture)
    variants = [
        rf"\blecture\s*{number}\b",
        rf"\blec\s*{number}\b",
        rf"\blesson\s*{number}\b",
        rf"\bchapter\s*{number}\b",
        rf"\bch\s*{number}\b",
        rf"\b{number}\b",
    ]
    return any(re.search(pattern, value) for pattern in variants)


def _lecture_matches_chapter_part(lecture: Lecture, chapter: int, part: int) -> bool:
    value = _lecture_reference_text(lecture)
    variants = [
        rf"\bchapter\s*0*{chapter}\s*,?\s*part\s*0*{part}\b",
        rf"\bch\s*0*{chapter}\s*,?\s*part\s*0*{part}\b",
        rf"\bchapter\s*0*{chapter}\.0*{part}\b",
        rf"\bch\s*0*{chapter}\.0*{part}\b",
    ]
    return any(re.search(pattern, value) for pattern in variants)


def _chapter_part_match_score(lecture: Lecture, chapter: int, part: int, lower_message: str = "") -> int:
    fields = [
        (lecture.source_filename or "").lower(),
        (lecture.title or "").lower(),
        (lecture.topic.chapter if lecture.topic else "").lower(),
        (lecture.topic.name if lecture.topic else "").lower(),
    ]
    score = 0
    asks_for_part = re.search(r"\b(?:part|pt)\b", lower_message) is not None
    asks_for_decimal = re.search(rf"\b(?:chapter|ch)\s*0*{chapter}\.0*{part}\b", lower_message) is not None
    for index, field in enumerate(fields):
        if not field:
            continue
        weight = 8 - index
        if re.search(rf"\bchapter\s*0*{chapter}\s*,?\s*part\s*0*{part}\b", field):
            score += weight + 8
            if asks_for_part:
                score += 24
        if re.search(rf"\bch\s*0*{chapter}\.0*{part}\b", field):
            score += weight + 6
            if asks_for_decimal:
                score += 24
        if re.search(rf"\bchapter\s*0*{chapter}\.0*{part}\b", field):
            score += weight + 6
            if asks_for_decimal:
                score += 24
    return score


def _lecture_reference_text(lecture: Lecture) -> str:
    return " ".join([
        lecture.title or "",
        lecture.source_filename or "",
        lecture.topic.chapter if lecture.topic else "",
        lecture.topic.name if lecture.topic else "",
    ]).lower()


def _phrase_in_message(phrase: str, lower_message: str) -> bool:
    phrase = phrase.lower().strip()
    if not phrase:
        return False
    if " " not in phrase:
        return re.search(rf"\b{re.escape(phrase)}\b", lower_message) is not None
    compact_message = re.sub(r"[^a-z0-9]+", " ", lower_message)
    return phrase in compact_message


def _rank_lecture_slides(lectures: list[Lecture], message: str, scope: dict | None = None) -> list[dict]:
    tokens = _ai_tokens(message)
    lower_message = message.lower()
    mentioned_lecture_number = (scope or {}).get("mentioned_lecture_number")
    mentioned_chapter_part = (scope or {}).get("mentioned_chapter_part")
    ranked: list[dict] = []
    for lecture in lectures:
        for slide in lecture.slides:
            slide_text = " ".join([
                slide.title or "",
                slide.text or "",
            ])
            lecture_text = " ".join([
                lecture.course.name if lecture.course else "",
                lecture.title or "",
                lecture.source_filename or "",
                lecture.topic.chapter if lecture.topic else "",
                lecture.topic.name if lecture.topic else "",
            ])
            summary_text = lecture.summary or ""
            slide_score = _ai_relevance_score(tokens, slide_text)
            lecture_score = _ai_relevance_score(tokens, lecture_text)
            summary_score = _ai_relevance_score(tokens, summary_text)
            score = (slide_score * 8) + (lecture_score * 4) + min(summary_score, 1)
            strong_match = slide_score > 0 or lecture_score > 0
            if lecture.title.lower() in lower_message:
                score += 3
                strong_match = True
            if lecture.course and any(_phrase_in_message(alias, lower_message) for alias in _course_aliases(lecture.course)):
                score += 4
            if mentioned_lecture_number and _lecture_matches_number(lecture, mentioned_lecture_number):
                score += 6
                strong_match = True
            if mentioned_chapter_part and _lecture_matches_chapter_part(lecture, mentioned_chapter_part[0], mentioned_chapter_part[1]):
                score += 12 + _chapter_part_match_score(
                    lecture,
                    mentioned_chapter_part[0],
                    mentioned_chapter_part[1],
                    lower_message,
                )
                strong_match = True
            slide_match = re.search(r"\bslide\s*(\d{1,2})\b", lower_message)
            if slide_match and int(slide_match.group(1)) == slide.slide_number:
                score += 8
                strong_match = True
            ranked.append({"score": score, "strong_match": strong_match, "lecture": lecture, "slide": slide})
    ranked.sort(
        key=lambda item: (
            item["score"],
            item["strong_match"],
            item["lecture"].created_at,
            -item["slide"].slide_number,
        ),
        reverse=True,
    )
    if tokens and any(item["score"] > 0 for item in ranked):
        return [item for item in ranked if item["score"] > 0]
    return ranked


def _ai_tokens(value: str) -> set[str]:
    stop = {
        "about", "after", "again", "answer", "before", "course", "explain", "from", "help",
        "lecture", "lesson", "please", "should", "slides", "study", "that", "this", "what",
        "when", "where", "which", "with", "would",
    }
    return {token for token in re.findall(r"[a-z0-9]+", value.lower()) if len(token) > 2 and token not in stop}


def _ai_relevance_score(tokens: set[str], value: str) -> int:
    words = set(re.findall(r"[a-z0-9]+", value.lower()))
    score = 0
    for token in tokens:
        variants = {token}
        if not token.endswith("s"):
            variants.add(f"{token}s")
        if token.endswith("ies"):
            variants.add(f"{token[:-3]}y")
        elif token.endswith("s") and len(token) > 4:
            variants.add(token[:-1])
        if words.intersection(variants):
            score += 1
    return score


def _clip(value: str, limit: int) -> str:
    value = " ".join((value or "").split())
    return value if len(value) <= limit else value[:limit - 3].rstrip() + "..."


def _optional_int(value) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _asks_for_general_help(message: str) -> bool:
    lower = message.lower()
    return any(phrase in lower for phrase in [
        "general explanation",
        "outside the slides",
        "outside course material",
        "use general",
        "in general",
    ])


def _asks_for_study_state(message: str) -> bool:
    lower = message.lower()
    return any(phrase in lower for phrase in [
        "what should i study",
        "study next",
        "next study",
        "plan",
        "schedule",
        "ready",
        "readiness",
        "grade",
        "cgpa",
        "gpa",
        "assessment",
        "deadline",
        "exam countdown",
        "weak topic",
    ])


def _asks_for_lecture_summary(message: str) -> bool:
    lower = message.lower()
    return any(phrase in lower for phrase in [
        "summarize",
        "summary",
        "study briefing",
        "study guide",
        "complete overview",
        "cover everything",
        "cover good ground",
        "everything required",
        "required to be learned",
        "what do i need to learn",
        "what should i know",
        "explain lesson",
        "explain this lesson",
        "explain lecture",
        "explain this lecture",
        "teach me this lecture",
        "teach me this lesson",
    ])


def _lecture_ai_context(lecture: Lecture, comprehensive: bool = False) -> str:
    slides = sorted(lecture.slides, key=lambda item: item.slide_number)
    key_concepts = _json_load(lecture.key_concepts_json, [])
    objectives = _json_load(lecture.learning_objectives_json, [])
    course_name = lecture.course.name if lecture.course else "Course"
    sections = [
        f"Course: {course_name}",
        f"Lecture: {lecture.title}",
        f"File: {lecture.source_filename}",
        f"Slide count: {len(slides)}",
        f"Stored summary: {_clip(lecture.summary, 1200)}",
    ]
    if key_concepts:
        sections.append("Key concepts: " + "; ".join(str(item) for item in _clean_display_list(key_concepts)[:18]))
    if objectives:
        sections.append("Learning objectives: " + "; ".join(str(item) for item in objectives[:12]))

    slide_limit = len(slides) if comprehensive else min(10, len(slides))
    slide_text_limit = 520 if comprehensive else 360
    slide_blocks = []
    for slide in slides[:slide_limit]:
        tags = ", ".join(_json_load(slide.content_tags_json, []))
        tag_text = f" Tags: {tags}." if tags else ""
        slide_blocks.append(
            f"Slide {slide.slide_number}: {_slide_display_title(slide)}.{tag_text} Text: {_clip(_clean_display_text(slide.text), slide_text_limit)}"
        )
    if slide_blocks:
        sections.append("Slides in order:\n" + "\n".join(slide_blocks))

    questions = sorted(lecture.questions, key=_question_sort_key)
    if questions:
        sections.append("Practice prompts:\n" + "\n".join(
            f"- Slide {question.slide_number}; {question.topic_tag}; {_question_type(question)}: {_clip(question.prompt, 220)}"
            for question in questions[:12]
        ))

    return "\n\n".join(sections)


def _lecture_sources(lecture: Lecture) -> list[dict]:
    course_name = lecture.course.name if lecture.course else "Course"
    slides = sorted(lecture.slides, key=lambda item: item.slide_number)
    if len(slides) <= 6:
        selected = slides
    else:
        selected = slides[:3] + slides[-3:]
    return [
        {
            "type": "slide",
            "course": course_name,
            "lecture_id": lecture.id,
            "lecture": _lecture_display_title(lecture),
            "slide_number": slide.slide_number,
            "title": _slide_display_title(slide),
        }
        for slide in selected
    ]


def _should_include_slide_context(message: str, lecture_id: int | None, course_id: int | None) -> bool:
    if lecture_id or course_id:
        return True
    lower = message.lower()
    planning_cues = ["study next", "what should i study", "plan", "schedule", "ready", "grade", "assessment", "deadline"]
    material_cues = ["explain", "slide", "lecture", "topic", "question", "quiz", "problem", "concept"]
    if any(cue in lower for cue in material_cues):
        return True
    return not any(cue in lower for cue in planning_cues)


def ensure_demo_data(db: Session) -> None:
    if db.query(Lecture).count() > 0:
        return
    course = db.query(Course).order_by(Course.id).first()
    if not course:
        return

    slides = [
        ExtractedSlide(
            1,
            "Memory Management",
            "Memory management is the operating system activity that tracks each byte of memory and decides which process gets memory. It supports protection, relocation, and efficient allocation.",
            ["definition"],
        ),
        ExtractedSlide(
            2,
            "Paging",
            "Paging divides logical memory into fixed-size pages and physical memory into frames. A page table maps pages to frames so a process can run without contiguous physical memory.",
            ["definition", "formula"],
        ),
        ExtractedSlide(
            3,
            "Page Faults",
            "A page fault occurs when a process references a page that is not currently in physical memory. The operating system must load the needed page before the instruction can continue.",
            ["example"],
        ),
    ]
    generated = build_lesson(slides, "Memory Management")
    topic = Topic(
        course_id=course.id,
        name="Memory Management Slide Lesson",
        chapter="Uploaded lecture demo",
        estimated_minutes=generated.estimated_minutes,
        weight=2.0,
        order=db.query(Topic).filter(Topic.course_id == course.id).count(),
    )
    db.add(topic)
    db.flush()
    lecture = Lecture(
        course_id=course.id,
        topic_id=topic.id,
        title="Memory Management Slide Lesson",
        source_filename="demo-memory-management.txt",
        source_type="demo",
        status="ready",
        summary=generated.summary,
        key_concepts_json=json.dumps(generated.key_concepts),
        learning_objectives_json=json.dumps(generated.learning_objectives),
        estimated_minutes=generated.estimated_minutes,
        local_only=True,
    )
    db.add(lecture)
    db.flush()
    for extracted in slides:
        db.add(Slide(
            lecture_id=lecture.id,
            slide_number=extracted.slide_number,
            title=extracted.title,
            text=extracted.text,
            content_tags_json=json.dumps(extracted.content_tags),
        ))
    for card in generated.flashcards:
        db.add(Flashcard(lecture_id=lecture.id, topic_id=topic.id, **card))
    for generated_question in generated.questions:
        options = generated_question["options"]
        db.add(LessonQuestion(
            lecture_id=lecture.id,
            topic_id=topic.id,
            slide_number=generated_question["slide_number"],
            prompt=generated_question["prompt"],
            option_a=options["a"],
            option_b=options["b"],
            option_c=options["c"],
            option_d=options["d"],
            correct=generated_question["correct"],
            explanation=generated_question["explanation"],
            wrong_explanations_json=json.dumps(generated_question["wrong_explanations"]),
            difficulty=generated_question["difficulty"],
            topic_tag=generated_question["topic_tag"],
        ))
        if _generated_question_is_mcq(generated_question):
            db.add(QuizQuestion(
                topic_id=topic.id,
                question=f"{generated_question['prompt']} (cite: slide {generated_question['slide_number']})",
                option_a=options["a"],
                option_b=options["b"],
                option_c=options["c"],
                option_d=options["d"],
                correct=generated_question["correct"],
                explanation=generated_question["explanation"],
            ))
    db.commit()


def _sync_local_course_pack(db: Session, source: str) -> dict:
    courses = db.query(Course).order_by(Course.id).all()
    created_syllabi = 0
    created_lectures = 0
    created_practice_questions = 0

    for course in courses:
        if not db.query(Syllabus).filter(Syllabus.course_id == course.id).first():
            syllabus = _create_synced_syllabus(db, course, source)
            created_syllabi += 1
            _create_synced_grade_items(db, course, syllabus)

        existing_lecture_count = db.query(Lecture).filter(Lecture.course_id == course.id).count()
        if existing_lecture_count < 3:
            needed = 3 - existing_lecture_count
            topics = (
                db.query(Topic)
                .filter(Topic.course_id == course.id)
                .order_by(Topic.order)
                .limit(needed)
                .all()
            )
            for topic in topics:
                if _create_synced_lesson(db, course, topic, source):
                    created_lectures += 1

        lectures = (
            db.query(Lecture)
            .options(selectinload(Lecture.slides), selectinload(Lecture.questions))
            .filter(Lecture.course_id == course.id)
            .all()
        )
        for lecture in lectures:
            created_practice_questions += _backfill_coverage_questions(db, lecture)

    return {
        "courses_count": db.query(Course).count(),
        "lectures_count": db.query(Lecture).count(),
        "syllabi_count": db.query(Syllabus).count(),
        "grade_items_count": db.query(GradeItem).count(),
        "created_syllabi": created_syllabi,
        "created_lectures": created_lectures,
        "created_practice_questions": created_practice_questions,
    }


def _backfill_coverage_questions(db: Session, lecture: Lecture) -> int:
    if any(_question_type(question) != "generated_mcq" for question in lecture.questions):
        return 0
    if not lecture.slides:
        return 0

    slides = [
        ExtractedSlide(
            slide_number=slide.slide_number,
            title=slide.title,
            text=slide.text,
            content_tags=_json_load(slide.content_tags_json, []),
        )
        for slide in sorted(lecture.slides, key=lambda item: item.slide_number)
    ]
    generated = build_lesson(slides, lecture.title)
    existing_prompts = {question.prompt for question in lecture.questions}
    created = 0

    for generated_question in generated.questions:
        if _generated_question_is_mcq(generated_question):
            continue
        if generated_question["prompt"] in existing_prompts:
            continue
        options = generated_question["options"]
        db.add(LessonQuestion(
            lecture_id=lecture.id,
            topic_id=lecture.topic_id,
            slide_number=generated_question["slide_number"],
            prompt=generated_question["prompt"],
            option_a=options["a"],
            option_b=options["b"],
            option_c=options["c"],
            option_d=options["d"],
            correct=generated_question["correct"],
            explanation=generated_question["explanation"],
            wrong_explanations_json=json.dumps(generated_question["wrong_explanations"]),
            difficulty=generated_question["difficulty"],
            topic_tag=generated_question["topic_tag"][:160],
        ))
        existing_prompts.add(generated_question["prompt"])
        created += 1
    return created


def _create_synced_syllabus(db: Session, course: Course, source: str) -> Syllabus:
    final_date = course.exam_date or (date.today() + timedelta(days=60))
    midterm_date = final_date - timedelta(days=26)
    quiz_date = max(date.today() + timedelta(days=10), midterm_date - timedelta(days=12))
    weights = [
        {"title": "Final Exam", "category": "exam", "weight_pct": 40.0, "due_date": final_date.isoformat()},
        {"title": "Midterm Exam", "category": "exam", "weight_pct": 25.0, "due_date": midterm_date.isoformat()},
        {"title": "Quizzes", "category": "quiz", "weight_pct": 15.0, "due_date": quiz_date.isoformat()},
        {"title": "Assignments", "category": "assignment", "weight_pct": 20.0, "due_date": None},
    ]
    events = [
        {"label": "Quiz checkpoint", "date": quiz_date.isoformat()},
        {"label": "Midterm Exam", "date": midterm_date.isoformat()},
        {"label": "Final Exam", "date": final_date.isoformat()},
    ]
    raw_text = "\n".join([
        f"{course.name} synced syllabus",
        f"Source: {source}",
        f"Quiz checkpoint: {quiz_date.isoformat()}",
        f"Midterm Exam: {midterm_date.isoformat()}",
        f"Final Exam: {final_date.isoformat()}",
        "Grade weights: Final 40%, Midterm 25%, Quizzes 15%, Assignments 20%.",
    ])
    syllabus = Syllabus(
        course_id=course.id,
        source_filename=f"{source.lower().replace(' ', '-')}-syllabus",
        status="synced",
        raw_text=raw_text,
        summary=f"Synced syllabus for {course.name}: final, midterm, quizzes, assignments.",
        extracted_dates_json=json.dumps(events),
        grading_weights_json=json.dumps(weights),
    )
    db.add(syllabus)
    db.flush()
    return syllabus


def _create_synced_grade_items(db: Session, course: Course, syllabus: Syllabus) -> None:
    for item in _json_load(syllabus.grading_weights_json, []):
        due_date = date.fromisoformat(item["due_date"]) if item.get("due_date") else None
        db.add(GradeItem(
            course_id=course.id,
            syllabus_id=syllabus.id,
            title=item["title"],
            category=item["category"],
            weight_pct=item["weight_pct"],
            due_date=due_date,
        ))
        if due_date:
            _upsert_assessment(db, course.id, item["title"], due_date, item["category"])


def _create_synced_lesson(db: Session, course: Course, topic: Topic, source: str) -> bool:
    existing = (
        db.query(Lecture)
        .filter(Lecture.course_id == course.id, Lecture.topic_id == topic.id)
        .first()
    )
    if existing:
        return False

    slides = [
        ExtractedSlide(
            1,
            topic.name,
            f"{topic.name} is a required topic in {course.name}. Key ideas include definitions, problem types, and how the concept appears in assessment questions.",
            ["learning objective", "definition"],
        ),
        ExtractedSlide(
            2,
            f"{topic.name} examples",
            f"Worked examples for {topic.name} focus on recognizing the method, applying it step by step, and explaining why distractor answers are incorrect.",
            ["example"],
        ),
        ExtractedSlide(
            3,
            f"{topic.name} checkpoint",
            f"Students should be able to summarize {topic.name}, answer active-recall questions, and connect it to related material from {topic.chapter or 'the course'}.",
            ["learning objective"],
        ),
    ]
    generated = build_lesson(slides, topic.name)
    display_title = _clean_display_label(generated.title) or _clean_display_label(topic.name) or topic.name
    lecture = Lecture(
        course_id=course.id,
        topic_id=topic.id,
        title=display_title[:240],
        source_filename=f"{source.lower().replace(' ', '-')}-{topic.id}",
        source_type="sync",
        status="synced",
        summary=generated.summary,
        key_concepts_json=json.dumps(generated.key_concepts),
        learning_objectives_json=json.dumps(generated.learning_objectives),
        estimated_minutes=generated.estimated_minutes,
        local_only=True,
    )
    db.add(lecture)
    db.flush()

    for extracted in slides:
        db.add(Slide(
            lecture_id=lecture.id,
            slide_number=extracted.slide_number,
            title=extracted.title[:240],
            text=extracted.text,
            content_tags_json=json.dumps(extracted.content_tags),
        ))

    for card in generated.flashcards:
        db.add(Flashcard(
            lecture_id=lecture.id,
            topic_id=topic.id,
            slide_number=card["slide_number"],
            front=card["front"],
            back=card["back"],
        ))

    for generated_question in generated.questions:
        options = generated_question["options"]
        db.add(LessonQuestion(
            lecture_id=lecture.id,
            topic_id=topic.id,
            slide_number=generated_question["slide_number"],
            prompt=generated_question["prompt"],
            option_a=options["a"],
            option_b=options["b"],
            option_c=options["c"],
            option_d=options["d"],
            correct=generated_question["correct"],
            explanation=generated_question["explanation"],
            wrong_explanations_json=json.dumps(generated_question["wrong_explanations"]),
            difficulty=generated_question["difficulty"],
            topic_tag=generated_question["topic_tag"][:160],
        ))

    return True


def _mask_student_id(student_id: str) -> str:
    if len(student_id) <= 4:
        return "****"
    return f"{student_id[:3]}***{student_id[-3:]}"


def _get_lecture(db: Session, lecture_id: int) -> Lecture:
    lecture = (
        db.query(Lecture)
        .options(
            joinedload(Lecture.course),
            joinedload(Lecture.topic),
            selectinload(Lecture.slides),
            selectinload(Lecture.questions),
            selectinload(Lecture.flashcards),
            selectinload(Lecture.attempts),
        )
        .filter(Lecture.id == lecture_id)
        .first()
    )
    if not lecture:
        raise HTTPException(404, "Lecture not found")
    return lecture


def _lesson_detail(lecture: Lecture) -> dict:
    can_render_images = _source_material_can_render_images(lecture)
    return {
        **_lesson_item(lecture),
        "summary": lecture.summary or "",
        "ai_summary": lecture.ai_summary or "",
        "source_type": lecture.source_type,
        "extraction_error": lecture.extraction_error,
        "local_only": lecture.local_only,
        "learning_objectives": _clean_display_list(_json_load(lecture.learning_objectives_json, [])),
        "slides": [_slide(s, can_render_images) for s in sorted(lecture.slides, key=lambda s: s.slide_number)],
        "questions": [_question(q, include_answer=False) for q in sorted(lecture.questions, key=_question_sort_key)],
        "flashcards": [_flashcard(c) for c in sorted(lecture.flashcards, key=lambda c: c.slide_number)],
    }


def _text_extractable_slides(slides: list[ExtractedSlide]) -> list[ExtractedSlide]:
    return [
        slide for slide in slides
        if "visual-only" not in set(slide.content_tags or [])
    ]


def _visual_only_lesson(slides: list[ExtractedSlide], fallback_title: str) -> GeneratedLesson:
    slide_count = len(slides)
    title = _clean_display_label(fallback_title) or fallback_title or "Uploaded slides"
    summary = (
        f"{title} was uploaded as an image-based PDF with {slide_count} slide"
        f"{'s' if slide_count != 1 else ''}. StudyPace saved the slide images so the deck can be used in the plan, "
        "but the PDF did not contain selectable text for automatic summaries or generated questions."
    )
    return GeneratedLesson(
        title=title,
        summary=summary,
        key_concepts=[],
        learning_objectives=[
            "Review the rendered slide images.",
            "Mark the deck finished after reading the slides.",
        ],
        questions=[],
        flashcards=[],
        estimated_minutes=min(90, max(20, slide_count * 4)),
    )


def _count_by_lecture(db: Session, model, lecture_ids: list[int]) -> dict[int, int]:
    if not lecture_ids:
        return {}
    rows = (
        db.query(model.lecture_id, func.count(model.id))
        .filter(model.lecture_id.in_(lecture_ids))
        .group_by(model.lecture_id)
        .all()
    )
    return {int(lecture_id): int(count) for lecture_id, count in rows}


def _lesson_item(
    lecture: Lecture,
    *,
    slide_count: int | None = None,
    question_count: int | None = None,
    flashcard_count: int | None = None,
) -> dict:
    return {
        "id": lecture.id,
        "course_id": lecture.course_id,
        "topic_id": lecture.topic_id,
        "title": _lecture_display_title(lecture),
        "source_filename": lecture.source_filename,
        "source_type": lecture.source_type,
        "status": lecture.status,
        "summary": _summary_preview(lecture.summary),
        "key_concepts": _clean_display_list(_json_load(lecture.key_concepts_json, []), short=True),
        "estimated_minutes": lecture.estimated_minutes,
        "mastery_score": lecture.mastery_score,
        "question_count": question_count if question_count is not None else len(lecture.questions),
        "flashcard_count": flashcard_count if flashcard_count is not None else len(lecture.flashcards),
        "slide_count": slide_count if slide_count is not None else len(lecture.slides),
        "created_at": lecture.created_at,
    }


def _unique_source_lectures(lectures: list[Lecture]) -> list[Lecture]:
    seen: set[tuple[int, str, str]] = set()
    unique: list[Lecture] = []
    for lecture in lectures:
        filename = _safe_filename(lecture.source_filename or "").lower()
        key = (lecture.course_id, lecture.source_type or "", filename or f"lecture:{lecture.id}")
        if key in seen:
            continue
        seen.add(key)
        unique.append(lecture)
    return unique


def _summary_preview(value: str, limit: int = 360) -> str:
    text = _normalize_pdf_text(value or "")
    overview_match = re.search(r"##\s+Overview\s+(.+?)(?:\n##\s+|\Z)", text, flags=re.I | re.S)
    if overview_match:
        text = overview_match.group(1)
    text = re.sub(r"^#{1,4}\s*", "", text, flags=re.M)
    text = re.sub(r"^\s*[-*]\s+", "", text, flags=re.M)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    return _clip(text, limit)


def _slide(slide: Slide, can_render_image: bool = False) -> dict:
    title = _slide_display_title(slide)
    return {
        "id": slide.id,
        "lecture_id": slide.lecture_id,
        "slide_number": slide.slide_number,
        "title": title,
        "text": _clean_display_text(slide.text),
        "content_tags": _json_load(slide.content_tags_json, []),
        "image_url": f"/api/learning/slides/{slide.id}/image" if can_render_image else None,
    }


def _slide_display_title(slide: Slide) -> str:
    cleaned = _clean_display_label(slide.title or "")
    if cleaned:
        return cleaned
    for line in (slide.text or "").splitlines()[:8]:
        cleaned = _clean_display_label(line)
        if cleaned and not cleaned.isdigit():
            return cleaned
    return f"Slide {slide.slide_number}"


def _lecture_display_title(lecture: Lecture) -> str:
    cleaned = _clean_display_label(lecture.title or "")
    if cleaned:
        return cleaned
    filename = (lecture.source_filename or "").rsplit(".", 1)[0].replace("_", " ").replace("-", " ").strip()
    return filename or "Lecture"


def _topic_display_title(topic: Topic, lecture: Lecture | None = None) -> str:
    cleaned = _clean_display_label(topic.name or "")
    if cleaned:
        return cleaned
    if lecture:
        return _lecture_display_title(lecture)
    return "Lecture"


def _clean_display_text(value: str | None) -> str:
    lines: list[str] = []
    for raw_line in (value or "").splitlines():
        cleaned = _clean_display_label(raw_line)
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def _clean_display_list(values, *, short: bool = False) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        label = _clean_display_label(str(value))
        key = label.lower()
        if short and not _is_short_display_label(label):
            continue
        if label and key not in seen:
            seen.add(key)
            cleaned.append(label)
    return cleaned


def _clean_display_label(value: str) -> str:
    bullet_chars = "•◦◼■▪▫❑□●○▶►▸"
    cleaned = _normalize_pdf_text(value or "")
    cleaned = re.sub(r"[\x00-\x1f\x7f]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(f" -–—:\t{bullet_chars}")
    cleaned = re.sub(rf"^[{re.escape(bullet_chars)}]+\s*", "", cleaned)
    if not cleaned:
        return ""
    cleaned = re.sub(
        r"\s*[-–—:]?\s*slide\s*[‹<«]?\s*#\s*[›>»]?\s*",
        " ",
        cleaned,
        flags=re.I,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip(f" -–—:\t{bullet_chars}")
    cleaned = re.sub(rf"^[{re.escape(bullet_chars)}]+\s*", "", cleaned)
    cleaned = re.sub(r"\s*[,;:]\s*$", "", cleaned)
    cleaned = re.sub(r"\s+(and|or)\s*$", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s*[,;:]\s*$", "", cleaned)
    lower = cleaned.lower()
    if lower in {"slide", "slides", "slide #", "slide ‹#›", "slide <#>"}:
        return ""
    return cleaned


def _normalize_pdf_text(value: str) -> str:
    replacements = {
        "\uf0a3": "<=",
        "\uf0b3": ">=",
    }
    text = value or ""
    for source, replacement in replacements.items():
        text = text.replace(source, replacement)
    return re.sub(r"[\uf000-\uf8ff]", "", text)


def _is_short_display_label(label: str) -> bool:
    if not label:
        return False
    lower = label.lower()
    word_count = len(re.findall(r"\b[a-zA-Z0-9]+\b", label))
    if not re.search(r"[a-zA-Z]", label) or label.strip().isdigit():
        return False
    if len(label) > 58 or word_count > 6:
        return False
    if any(phrase in lower for phrase in (
        "positive constants",
        "such that",
        "if there are",
        " for n ",
        "following methods",
        "included with permission",
    )):
        return False
    if lower.startswith(("slide by", "note:", "do not ")):
        return False
    if re.search(r"[<>=]\s*[a-z]?\(?n\)?", lower) and word_count > 5:
        return False
    return True


def _store_source_material(lecture: Lecture, content: bytes) -> None:
    if not lecture.source_filename or not content:
        return
    storage = object_storage()
    if storage:
        storage.put_bytes(
            _source_material_object_key(lecture),
            content,
            content_type_for_filename(lecture.source_filename),
        )
        return
    folder = _source_material_dir() / str(lecture.course_id)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / _safe_filename(lecture.source_filename)).write_bytes(content)


def _source_material_object_key(lecture: Lecture | None) -> str:
    if not lecture or not lecture.source_filename:
        return ""
    return workspace_object_key("source_material", str(lecture.course_id), _safe_filename(lecture.source_filename))


def _slide_image_object_key(lecture_id: int, slide_number: int) -> str:
    return workspace_object_key("slide_images", str(lecture_id), f"slide-{slide_number}.png")


def _source_material_can_render_images(lecture: Lecture | None) -> bool:
    if not lecture or not lecture.source_filename or Path(lecture.source_filename).suffix.lower() != ".pdf":
        return False
    source_path = _source_material_path(lecture)
    if source_path and source_path.exists():
        return True
    storage = object_storage()
    source_key = _source_material_object_key(lecture)
    return bool(storage and source_key and storage.exists(source_key))


def _source_material_path(lecture: Lecture | None) -> Path | None:
    if not lecture or not lecture.source_filename:
        return None
    folder = _source_material_dir() / str(lecture.course_id)
    filename = _safe_filename(lecture.source_filename)
    candidates = [
        folder / filename,
        folder / lecture.source_filename,
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    if folder.exists():
        wanted = lecture.source_filename.lower()
        for candidate in folder.iterdir():
            if candidate.is_file() and candidate.name.lower() == wanted:
                return candidate
    return None


def _render_slide_image(source: Path, lecture_id: int, slide_number: int) -> Path:
    if fitz is None:
        raise HTTPException(503, "Slide images need PyMuPDF in the backend runtime.")
    if source.suffix.lower() != ".pdf":
        raise HTTPException(404, "Slide images are available for PDF lectures only.")

    out_dir = _slide_image_dir() / str(lecture_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    output = out_dir / f"slide-{slide_number}.png"
    if output.exists():
        return output

    doc = None
    try:
        doc = fitz.open(str(source))
        page_index = slide_number - 1
        if page_index < 0 or page_index >= doc.page_count:
            raise HTTPException(404, "Slide page was not found in the source PDF.")
        page = doc.load_page(page_index)
        pix = page.get_pixmap(matrix=fitz.Matrix(1.7, 1.7), alpha=False)
        pix.save(str(output))
        return output
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, "Could not render this slide image.") from exc
    finally:
        if doc is not None:
            doc.close()


def _render_slide_image_bytes(source_bytes: bytes, source_filename: str, slide_number: int) -> bytes:
    if fitz is None:
        raise HTTPException(503, "Slide images need PyMuPDF in the backend runtime.")
    if Path(source_filename or "").suffix.lower() != ".pdf":
        raise HTTPException(404, "Slide images are available for PDF lectures only.")

    doc = None
    try:
        doc = fitz.open(stream=source_bytes, filetype="pdf")
        page_index = slide_number - 1
        if page_index < 0 or page_index >= doc.page_count:
            raise HTTPException(404, "Slide page was not found in the source PDF.")
        page = doc.load_page(page_index)
        pix = page.get_pixmap(matrix=fitz.Matrix(1.7, 1.7), alpha=False)
        return pix.tobytes("png")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, "Could not render this slide image.") from exc
    finally:
        if doc is not None:
            doc.close()


def _delete_stored_lecture_files(
    *,
    source_key: str,
    source_path: Path | None,
    slide_image_dir: Path,
    slide_image_keys: list[str],
) -> None:
    try:
        if source_path and source_path.exists() and source_path.is_file():
            source_path.unlink()
    except Exception:
        pass

    try:
        if slide_image_dir.exists():
            shutil.rmtree(slide_image_dir)
    except Exception:
        pass

    storage = object_storage()
    if not storage:
        return
    keys = [key for key in [source_key, *slide_image_keys] if key]
    for key in keys:
        try:
            storage.delete_object(key)
        except Exception:
            pass


def _safe_filename(filename: str) -> str:
    return Path(filename).name[:260]


def _question(question: LessonQuestion, include_answer: bool) -> dict:
    data = {
        "id": question.id,
        "lecture_id": question.lecture_id,
        "topic_id": question.topic_id,
        "slide_number": question.slide_number,
        "question_type": _question_type(question),
        "prompt": question.prompt,
        "option_a": question.option_a,
        "option_b": question.option_b,
        "option_c": question.option_c,
        "option_d": question.option_d,
        "explanation": question.explanation,
        "wrong_explanations": _json_load(question.wrong_explanations_json, {}),
        "difficulty": question.difficulty,
        "topic_tag": question.topic_tag,
    }
    if include_answer:
        data["correct_answer"] = question.correct
    return data


def _question_type(question: LessonQuestion) -> str:
    difficulty = (question.difficulty or "").lower()
    if difficulty == "extracted-numerical":
        return "extracted_numerical"
    if difficulty == "extracted-problem":
        return "extracted_problem"
    if difficulty == "extracted-question":
        return "extracted_question"
    if difficulty == "coverage-problem":
        return "coverage_problem"
    if difficulty == "coverage-question":
        return "coverage_question"
    if difficulty == "claude-written":
        return "claude_written"
    return "generated_mcq"


def _question_sort_key(question: LessonQuestion) -> tuple[int, int, int]:
    priority = {
        "extracted_numerical": 0,
        "extracted_problem": 1,
        "extracted_question": 2,
        "claude_written": 3,
        "coverage_problem": 4,
        "coverage_question": 5,
        "generated_mcq": 6,
    }.get(_question_type(question), 5)
    return (priority, question.slide_number, question.id or 0)


def _is_generated_lesson_mcq(question: LessonQuestion) -> bool:
    return _question_type(question) == "generated_mcq"


def _generated_question_is_mcq(question: dict) -> bool:
    if question.get("question_type"):
        return question["question_type"] == "generated_mcq"
    return question.get("difficulty") in {"easy", "medium", "hard"}


def _flashcard(card: Flashcard) -> dict:
    return {
        "id": card.id,
        "lecture_id": card.lecture_id,
        "slide_number": card.slide_number,
        "front": card.front,
        "back": card.back,
    }


def _syllabus(syllabus: Syllabus) -> dict:
    return {
        "id": syllabus.id,
        "course_id": syllabus.course_id,
        "source_filename": syllabus.source_filename,
        "status": syllabus.status,
        "summary": syllabus.summary,
        "extracted_dates": _json_load(syllabus.extracted_dates_json, []),
        "grading_weights": _json_load(syllabus.grading_weights_json, []),
        "extraction_error": syllabus.extraction_error,
        "created_at": syllabus.created_at,
    }


def _review_items(db: Session, limit: int) -> list[dict]:
    wrong = (
        db.query(AnswerAttempt)
        .filter(AnswerAttempt.is_correct.is_(False))
        .order_by(AnswerAttempt.created_at.desc())
        .limit(limit)
        .all()
    )
    items = []
    for attempt in wrong:
        q = attempt.question
        if not q:
            continue
        items.append({
            "attempt_id": attempt.id,
            "lecture_id": attempt.lecture_id,
            "question_id": attempt.question_id,
            "question": q.prompt,
            "selected": attempt.selected,
            "correct_answer": q.correct,
            "feedback": attempt.explanation,
            "slide_number": q.slide_number,
            "topic": q.topic_tag,
            "created_at": attempt.created_at,
        })
    return items


def _weak_topics(db: Session) -> list[dict]:
    attempts = db.query(QuizAttempt).all()
    topics = {t.id: t for t in _schedulable_topics(db)}
    scores: dict[int, list[float]] = {}
    for attempt in attempts:
        if attempt.topic_id in topics:
            scores.setdefault(attempt.topic_id, []).append(attempt.score)
    weak = []
    for topic_id, values in scores.items():
        avg = sum(values) / len(values)
        if avg < 0.7 and topic_id in topics:
            weak.append({
                "topic_id": topic_id,
                "topic": topics[topic_id].name,
                "avg_score": round(avg, 2),
                "attempts": len(values),
            })
    return sorted(weak, key=lambda item: item["avg_score"])[:8]


def _upcoming_deadlines(db: Session) -> list[dict]:
    today = date.today()
    upcoming = (
        db.query(Assessment)
        .filter(Assessment.date >= today, Assessment.date <= today + timedelta(days=21))
        .order_by(Assessment.date)
        .limit(8)
        .all()
    )
    return [
        {"title": a.title, "course_id": a.course_id, "date": a.date, "type": a.type.value if hasattr(a.type, "value") else a.type}
        for a in upcoming
    ]


def _upsert_assessment(db: Session, course_id: int, title: str, event_date: date, category: str) -> None:
    existing = (
        db.query(Assessment)
        .filter(Assessment.course_id == course_id, Assessment.title == title, Assessment.date == event_date)
        .first()
    )
    if existing:
        return
    type_map = {
        "exam": AssessmentType.exam,
        "quiz": AssessmentType.quiz,
        "assignment": AssessmentType.assignment,
        "project": AssessmentType.assignment,
        "coursework": AssessmentType.assignment,
    }
    db.add(Assessment(
        course_id=course_id,
        title=title,
        date=event_date,
        type=type_map.get(category, AssessmentType.assignment),
        notes="Extracted from syllabus",
    ))


def _plan_for(db: Session, target_date: date) -> dict:
    topics = _schedulable_topics(db)
    return generate_daily_plan(
        topics,
        db.query(StudySession).all(),
        db.query(QuizAttempt).all(),
        db.query(Assessment).all(),
        [],
        _settings(db),
        target_date,
    )


def _schedulable_topics(
    db: Session,
    course_id: int | None = None,
    lecture_start: int | None = None,
    lecture_end: int | None = None,
) -> list[Topic]:
    lectures = _schedulable_lectures(db, course_id)
    if lecture_start or lecture_end:
        start = max(1, lecture_start or 1)
        end = max(start, lecture_end or len(lectures))
        lectures = lectures[start - 1:end]
    topic_ids = [lecture.topic_id for lecture in lectures if lecture.topic_id]
    query = db.query(Topic).options(joinedload(Topic.course))
    if topic_ids:
        query = query.filter(Topic.id.in_(topic_ids))
    elif course_id:
        query = query.filter(Topic.course_id == course_id)
    return query.order_by(Topic.course_id, Topic.order, Topic.id).all()


def _schedulable_lectures(db: Session, course_id: int | None = None) -> list[Lecture]:
    query = (
        db.query(Lecture)
        .options(joinedload(Lecture.topic), joinedload(Lecture.course))
        .filter(Lecture.topic_id.isnot(None), Lecture.source_type.notin_(["demo", "sync"]))
    )
    if course_id:
        query = query.filter(Lecture.course_id == course_id)
    lectures = query.all()
    return _unique_source_lectures(sorted(lectures, key=_lecture_plan_sort_key))


def _lecture_lookup_for_topics(db: Session, topics: list[Topic]) -> dict[int, Lecture]:
    topic_ids = [topic.id for topic in topics]
    if not topic_ids:
        return {}
    lectures = (
        db.query(Lecture)
        .filter(Lecture.topic_id.in_(topic_ids), Lecture.source_type.notin_(["demo", "sync"]))
        .all()
    )
    lookup: dict[int, Lecture] = {}
    for lecture in sorted(lectures, key=_lecture_plan_sort_key):
        if lecture.topic_id and lecture.topic_id not in lookup:
            lookup[lecture.topic_id] = lecture
    return lookup


def _lecture_plan_sort_key(lecture: Lecture) -> tuple[int, int, int, datetime]:
    filename = (lecture.source_filename or lecture.title or "").lower()
    number = _lecture_number_for_plan(filename)
    return (
        lecture.course_id or 0,
        0 if number is not None else 1,
        number if number is not None else (lecture.topic.order if lecture.topic else 9999),
        lecture.created_at,
    )


def _lecture_number_for_plan(value: str) -> int | None:
    patterns = [
        r"\blecture\s*(\d{1,2})\b",
        r"\bchapter\s*(\d{1,2})\s*,?\s*part\s*(\d{1,2})\b",
        r"\bch\s*(\d{1,2})(?:\.(\d{1,2}))?\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, value)
        if not match:
            continue
        first = int(match.group(1))
        second = int(match.group(2)) if len(match.groups()) > 1 and match.group(2) else 0
        return first * 10 + second if second else first
    return None


def _plan_assessment_label(value: str | None) -> str:
    normalized = (value or "").lower().strip()
    if normalized in {"midterm", "final", "quiz", "exam", "assignment"}:
        return normalized
    return ""


def _settings(db: Session) -> UserSettings:
    settings = db.get(UserSettings, 1)
    if settings:
        return settings
    settings = UserSettings(id=1, daily_minutes=135, max_course_pct=0.6, streak=0)
    db.add(settings)
    db.commit()
    db.refresh(settings)
    return settings


def _update_streak(db: Session) -> None:
    settings = _settings(db)
    today = date.today()
    if settings.last_study_date == today - timedelta(days=1):
        settings.streak += 1
    elif settings.last_study_date != today:
        settings.streak = 1
    settings.last_study_date = today


def _award_badge(db: Session, code: str, title: str, description: str) -> None:
    if not db.query(Badge).filter(Badge.code == code).first():
        db.add(Badge(code=code, title=title, description=description))


def _json_load(value: str, fallback):
    try:
        return json.loads(value or "")
    except Exception:
        return fallback


def _decode_upload(value: str) -> bytes:
    raw = value.split(",", 1)[-1]
    try:
        content = base64.b64decode(raw, validate=True)
    except Exception as exc:
        raise SlideExtractionError("Upload encoding failed. Try selecting the file again.") from exc
    limit = max_upload_bytes()
    if len(content) > limit:
        raise SlideExtractionError(f"File too large. Maximum upload size is {limit // (1024 * 1024)} MB.")
    return content


def _validate_upload(filename: str, content: bytes) -> None:
    safe_name = _safe_filename(filename)
    suffix = Path(safe_name).suffix.lower()
    if not safe_name or suffix not in ALLOWED_UPLOAD_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_UPLOAD_EXTENSIONS))
        raise SlideExtractionError(f"Unsupported file type. Upload one of: {allowed}.")
    if not content:
        raise SlideExtractionError("The uploaded file is empty.")
    if suffix == ".pdf" and not content.startswith(b"%PDF"):
        raise SlideExtractionError("This does not look like a valid PDF file.")
    if suffix in {".pptx", ".docx"} and not content.startswith(b"PK"):
        raise SlideExtractionError("This does not look like a valid Office document.")
    if suffix in {".txt", ".md"}:
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SlideExtractionError("Text uploads must be saved as UTF-8.") from exc


def _source_material_dir() -> Path:
    return current_workspace_data_dir() / "source_material"


def _slide_image_dir() -> Path:
    return current_workspace_data_dir() / "slide_images"
