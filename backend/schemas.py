"""
schemas.py — Pydantic models for request/response validation.

WHY separate from ORM models: SQLAlchemy models describe the DB shape;
Pydantic schemas describe what the API accepts and returns. Keeping them
separate lets you expose only safe fields (e.g., hide internal IDs or
timestamps) and validate inputs without touching the DB layer.
"""

from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel, Field, field_validator


# ── Course ────────────────────────────────────────────────────────────────────

class CourseCreate(BaseModel):
    name: str
    description: str = ""
    total_hours: float = 40.0
    exam_date: Optional[date] = None
    color: str = "#007aff"

class CourseOut(CourseCreate):
    id: int
    created_at: datetime
    model_config = {"from_attributes": True}  # allows ORM -> Pydantic conversion


# ── Topic ─────────────────────────────────────────────────────────────────────

class TopicCreate(BaseModel):
    course_id: int
    name: str
    chapter: str = ""
    estimated_minutes: int = Field(default=45, ge=5, le=240)
    weight: float = Field(default=1.0, ge=0.5, le=3.0)
    order: int = 0

class TopicOut(TopicCreate):
    id: int
    created_at: datetime
    model_config = {"from_attributes": True}


# ── Study Session ─────────────────────────────────────────────────────────────

class SessionCreate(BaseModel):
    topic_id: int
    date: date
    planned_minutes: int

class SessionComplete(BaseModel):
    actual_minutes: int
    notes: str = ""

class SessionOut(SessionCreate):
    id: int
    actual_minutes: Optional[int]
    completed: bool
    notes: str
    model_config = {"from_attributes": True}


# ── Quiz ──────────────────────────────────────────────────────────────────────

class QuizQuestionOut(BaseModel):
    id: int
    topic_id: int
    question: str
    option_a: str
    option_b: str
    option_c: str
    option_d: str
    # correct answer NOT included in response — sent only after submission
    explanation: str
    model_config = {"from_attributes": True}

class QuizAnswer(BaseModel):
    question_id: int
    selected: str  # "a", "b", "c", or "d"

class QuizSubmission(BaseModel):
    topic_id: int
    answers: list[QuizAnswer]

class QuizResult(BaseModel):
    topic_id: int
    score: float           # 0.0-1.0
    correct: int
    total: int
    per_question: list[dict]  # {question_id, correct, explanation}

class QuizAttemptOut(BaseModel):
    id: int
    topic_id: int
    score: float
    questions_total: int
    questions_correct: int
    timestamp: datetime
    model_config = {"from_attributes": True}


# ── Assessment ────────────────────────────────────────────────────────────────

class AssessmentCreate(BaseModel):
    course_id: int
    title: str
    date: date
    type: str = "quiz"
    notes: str = ""

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        from models import AssessmentType
        try:
            return AssessmentType(v).value
        except ValueError:
            allowed = [e.value for e in AssessmentType]
            raise ValueError(f"type must be one of: {', '.join(allowed)}")

class AssessmentOut(AssessmentCreate):
    id: int
    model_config = {"from_attributes": True}


# ── Planner ───────────────────────────────────────────────────────────────────

class PlanItem(BaseModel):
    topic_id: int
    topic_name: str
    course_name: str
    course_color: str
    chapter: str
    planned_minutes: int
    priority_score: float
    days_since_studied: int
    avg_quiz_score: Optional[float]   # None if never quizzed
    assessment_boost: bool            # True if course has assessment in ≤7 days


class DailyPlan(BaseModel):
    date: date
    total_minutes: int
    items: list[PlanItem]
    is_day_off: bool


# ── Settings ──────────────────────────────────────────────────────────────────

class SettingsUpdate(BaseModel):
    daily_minutes: Optional[int] = Field(default=None, ge=30, le=1080)
    max_course_pct: Optional[float] = Field(default=None, ge=0.3, le=1.0)
    phone_number: Optional[str] = Field(default=None, max_length=30)
    call_reminder_enabled: Optional[bool] = None
    call_reminder_hour: Optional[int] = Field(default=None, ge=0, le=23)

class SettingsOut(BaseModel):
    daily_minutes: int
    max_course_pct: float
    streak: int
    last_study_date: Optional[date]
    phone_number: str
    call_reminder_enabled: bool
    call_reminder_hour: int
    model_config = {"from_attributes": True}


# ── Offline Learning MVP ─────────────────────────────────────────────────────

class SlideOut(BaseModel):
    id: int
    lecture_id: int
    slide_number: int
    title: str
    text: str
    content_tags: list[str] = []
    image_url: Optional[str] = None
    model_config = {"from_attributes": True}


class FlashcardOut(BaseModel):
    id: int
    lecture_id: int
    slide_number: int
    front: str
    back: str
    model_config = {"from_attributes": True}


class LessonQuestionOut(BaseModel):
    id: int
    lecture_id: int
    topic_id: Optional[int]
    slide_number: int
    question_type: str = "generated_mcq"
    prompt: str
    option_a: str
    option_b: str
    option_c: str
    option_d: str
    explanation: str
    wrong_explanations: dict[str, str] = {}
    difficulty: str
    topic_tag: str
    model_config = {"from_attributes": True}


class LessonOut(BaseModel):
    id: int
    course_id: int
    topic_id: Optional[int]
    title: str
    source_filename: str
    source_type: str
    status: str
    summary: str
    ai_summary: str = ""
    key_concepts: list[str]
    learning_objectives: list[str]
    extraction_error: str
    estimated_minutes: int
    mastery_score: float
    local_only: bool
    created_at: datetime
    slides: list[SlideOut] = []
    questions: list[LessonQuestionOut] = []
    flashcards: list[FlashcardOut] = []


class LessonListItem(BaseModel):
    id: int
    course_id: int
    topic_id: Optional[int]
    title: str
    source_filename: str
    source_type: str
    status: str
    summary: str
    key_concepts: list[str]
    estimated_minutes: int
    mastery_score: float
    question_count: int
    flashcard_count: int
    slide_count: int
    created_at: datetime


class LessonQuizAnswer(BaseModel):
    question_id: int
    selected: Optional[str] = None
    response: Optional[str] = None


class LessonQuizSubmission(BaseModel):
    answers: list[LessonQuizAnswer]


class LectureUpload(BaseModel):
    filename: str
    content_base64: str


class GradeItemOut(BaseModel):
    id: int
    course_id: int
    syllabus_id: Optional[int]
    title: str
    category: str
    weight_pct: float
    due_date: Optional[date]
    current_score: Optional[float]
    model_config = {"from_attributes": True}


class SyllabusOut(BaseModel):
    id: int
    course_id: int
    source_filename: str
    status: str
    summary: str
    extracted_dates: list[dict]
    grading_weights: list[dict]
    extraction_error: str
    created_at: datetime


class LessonQuizResult(BaseModel):
    lecture_id: int
    score: float
    correct: int
    total: int
    mastery_score: float
    xp_earned: int
    unlocked_next: bool
    per_question: list[dict]


class CourseWithLessons(BaseModel):
    course: CourseOut
    topics: list[TopicOut]
    lectures: list[LessonListItem]
    syllabi: list[SyllabusOut] = []
    grade_items: list[GradeItemOut] = []


class CalendarBlockOut(BaseModel):
    id: Optional[int] = None
    lecture_id: Optional[int] = None
    topic_id: Optional[int] = None
    course_name: Optional[str] = None
    course_color: Optional[str] = None
    assessment_id: Optional[int] = None
    assessment_title: Optional[str] = None
    assessment_type: Optional[str] = None
    days_until_assessment: Optional[int] = None
    date: date
    title: str
    planned_minutes: int
    status: str
    priority: str
    pass_number: Optional[int] = None
    pass_total: Optional[int] = None


class LearningOverview(BaseModel):
    xp: int
    level: int
    streak: int
    mastery_score: float
    courses_count: int
    lectures_count: int
    syllabi_count: int = 0
    grade_items_count: int = 0
    questions_count: int
    review_count: int
    today_tasks: list[CalendarBlockOut]
    weak_topics: list[dict]
    upcoming_deadlines: list[dict]
    badges: list[dict]
    privacy_mode: str
    ai_status: dict
