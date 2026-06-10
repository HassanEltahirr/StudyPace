from __future__ import annotations

import json
import os
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from models import AnswerAttempt, Flashcard, Lecture, LessonQuestion, QuizQuestion, Slide
from services.question_generator import GeneratedLesson, build_lesson
from services.slide_parser import ExtractedSlide, extract_slides


SUPPORTED_SOURCE_SUFFIXES = {".pdf", ".ppt", ".pptx", ".docx", ".txt", ".md"}


@dataclass
class RegeneratedSource:
    source_path: Path
    lecture_ids: list[int]
    title: str
    slides: int
    questions: int
    flashcards: int


@dataclass
class RegenerationSummary:
    scanned_lectures: int
    matched_sources: int
    updated_lectures: int
    updated_sources: list[RegeneratedSource]
    missing_sources: list[str]
    failed_sources: list[str]


def default_data_dir() -> Path:
    return Path(os.getenv("STUDYPACE_DATA_DIR", str(Path(__file__).resolve().parent.parent / "data")))


def regenerate_lessons_from_cached_sources(
    db: Session,
    *,
    data_dir: Path | None = None,
    course_id: int | None = None,
    lecture_id: int | None = None,
    source_limit: int | None = None,
    dry_run: bool = False,
    sync_quiz_bank: bool = True,
    progress: Callable[[int, int, Path, int], None] | None = None,
) -> RegenerationSummary:
    data_dir = data_dir or default_data_dir()
    source_material_dir = data_dir / "source_material"

    query = db.query(Lecture).filter(Lecture.source_filename != "")
    if course_id is not None:
        query = query.filter(Lecture.course_id == course_id)
    if lecture_id is not None:
        query = query.filter(Lecture.id == lecture_id)

    lectures = query.order_by(Lecture.course_id, Lecture.source_filename, Lecture.id).all()
    grouped: dict[Path, list[Lecture]] = defaultdict(list)
    missing_sources: list[str] = []

    for lecture in lectures:
        source_path = _source_material_path(source_material_dir, lecture)
        if not source_path:
            missing_sources.append(f"lecture {lecture.id}: {lecture.course_id}/{lecture.source_filename}")
            continue
        if source_path.suffix.lower() not in SUPPORTED_SOURCE_SUFFIXES:
            continue
        grouped[source_path].append(lecture)

    source_items = list(grouped.items())
    if source_limit is not None:
        source_items = source_items[:source_limit]

    updated_sources: list[RegeneratedSource] = []
    failed_sources: list[str] = []

    total_sources = len(source_items)
    for index, (source_path, source_lectures) in enumerate(source_items, start=1):
        if progress:
            progress(index, total_sources, source_path, len(source_lectures))
        try:
            content = source_path.read_bytes()
            slides = extract_slides(source_path.name, content)
            text_slides = _text_extractable_slides(slides)
            generated = (
                build_lesson(text_slides, source_path.stem)
                if text_slides
                else _visual_only_lesson(slides, source_path.stem)
            )
        except Exception as exc:
            failed_sources.append(f"{source_path}: {exc}")
            continue

        updated_sources.append(
            RegeneratedSource(
                source_path=source_path,
                lecture_ids=[lecture.id for lecture in source_lectures],
                title=generated.title,
                slides=len(slides),
                questions=len(generated.questions),
                flashcards=len(generated.flashcards),
            )
        )

        if dry_run:
            continue

        for lecture in source_lectures:
            _replace_lesson_content(
                db,
                lecture,
                slides,
                generated,
                sync_quiz_bank=sync_quiz_bank,
            )

        db.commit()

    return RegenerationSummary(
        scanned_lectures=len(lectures),
        matched_sources=len(grouped),
        updated_lectures=sum(len(item.lecture_ids) for item in updated_sources),
        updated_sources=updated_sources,
        missing_sources=missing_sources,
        failed_sources=failed_sources,
    )


def _text_extractable_slides(slides: list[ExtractedSlide]) -> list[ExtractedSlide]:
    return [
        slide for slide in slides
        if "visual-only" not in set(slide.content_tags or [])
    ]


def _visual_only_lesson(slides: list[ExtractedSlide], fallback_title: str) -> GeneratedLesson:
    slide_count = len(slides)
    title = fallback_title or "Uploaded slides"
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


def _replace_lesson_content(
    db: Session,
    lecture: Lecture,
    slides: list[ExtractedSlide],
    generated: GeneratedLesson,
    *,
    sync_quiz_bank: bool,
) -> None:
    replace_questions = _should_replace_questions(list(lecture.questions), generated.questions)
    if replace_questions:
        db.query(AnswerAttempt).filter(AnswerAttempt.lecture_id == lecture.id).delete(synchronize_session=False)
        db.query(LessonQuestion).filter(LessonQuestion.lecture_id == lecture.id).delete(synchronize_session=False)
    db.query(Flashcard).filter(Flashcard.lecture_id == lecture.id).delete(synchronize_session=False)
    db.query(Slide).filter(Slide.lecture_id == lecture.id).delete(synchronize_session=False)

    if replace_questions and sync_quiz_bank and lecture.topic_id:
        db.query(QuizQuestion).filter(
            QuizQuestion.topic_id == lecture.topic_id,
            QuizQuestion.question.like("%(cite: slide %"),
        ).delete(synchronize_session=False)

    lecture.title = generated.title[:240]
    if _should_replace_summary(lecture.summary, generated.summary):
        lecture.summary = generated.summary
    lecture.key_concepts_json = json.dumps(generated.key_concepts)
    lecture.learning_objectives_json = json.dumps(generated.learning_objectives)
    lecture.estimated_minutes = generated.estimated_minutes
    lecture.status = "ready"
    lecture.extraction_error = ""
    lecture.local_only = True

    for extracted in slides:
        db.add(
            Slide(
                lecture_id=lecture.id,
                slide_number=extracted.slide_number,
                title=extracted.title[:240],
                text=extracted.text,
                content_tags_json=json.dumps(extracted.content_tags),
            )
        )

    for card in generated.flashcards:
        db.add(
            Flashcard(
                lecture_id=lecture.id,
                topic_id=lecture.topic_id,
                slide_number=card["slide_number"],
                front=card["front"],
                back=card["back"],
            )
        )

    if replace_questions:
        for generated_question in generated.questions:
            options = generated_question["options"]
            db.add(
                LessonQuestion(
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
                )
            )

            if sync_quiz_bank and lecture.topic_id and _is_generated_mcq(generated_question):
                db.add(
                    QuizQuestion(
                        topic_id=lecture.topic_id,
                        question=f"{generated_question['prompt']} (cite: slide {generated_question['slide_number']})",
                        option_a=options["a"],
                        option_b=options["b"],
                        option_c=options["c"],
                        option_d=options["d"],
                        correct=generated_question["correct"],
                        explanation=generated_question["explanation"],
                    )
                )


def _source_material_path(source_material_dir: Path, lecture: Lecture) -> Path | None:
    folder = source_material_dir / str(lecture.course_id)
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


def _safe_filename(filename: str) -> str:
    return Path(filename).name[:260]


def _is_generated_mcq(question: dict) -> bool:
    return question.get("question_type") == "generated_mcq"


def _should_replace_summary(existing: str, replacement: str) -> bool:
    if not replacement.strip():
        return False
    if _looks_like_ai_study_summary(replacement):
        return True
    return not _looks_like_ai_study_summary(existing)


def _looks_like_ai_study_summary(value: str) -> bool:
    text = value.strip().lower()
    return (
        text.startswith("# ")
        or text.startswith("## ")
        or text.startswith("summary:")
        or ("part 1 -" in text and "part 2 -" in text)
        or ("## overview" in text and "## key concepts" in text)
    )


def _should_replace_questions(existing: list[LessonQuestion], replacement: list[dict]) -> bool:
    if _has_claude_questions(replacement):
        return True
    return not _has_existing_claude_questions(existing)


def _has_claude_questions(questions: list[dict]) -> bool:
    return any(
        "source: claude" in str(question.get("topic_tag", "")).lower()
        for question in questions
    )


def _has_existing_claude_questions(questions: list[LessonQuestion]) -> bool:
    return any(
        "source: claude" in str(question.topic_tag or "").lower()
        for question in questions
    )
