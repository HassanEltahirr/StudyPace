"""
models.py — SQLAlchemy ORM models (the database schema in Python form).

Each class maps to a table. Relationships let us navigate between rows
without writing JOIN queries manually.
"""

from datetime import date, datetime
from sqlalchemy import ForeignKey, String, Integer, Float, Boolean, Date, DateTime, Text, Enum
from sqlalchemy.orm import Mapped, mapped_column, relationship
import enum

from database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    first_name: Mapped[str] = mapped_column(String(80), default="")
    last_name: Mapped[str] = mapped_column(String(80), default="")
    password_hash: Mapped[str] = mapped_column(String(260))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AssessmentType(str, enum.Enum):
    quiz = "quiz"
    midterm = "midterm"
    final = "final"
    exam = "exam"
    assignment = "assignment"


class Course(Base):
    __tablename__ = "courses"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    total_hours: Mapped[float] = mapped_column(Float, default=40.0)
    exam_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    color: Mapped[str] = mapped_column(String(20), default="#007aff")  # for UI
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships — SQLAlchemy loads these lazily by default
    topics: Mapped[list["Topic"]] = relationship("Topic", back_populates="course", cascade="all, delete-orphan")
    assessments: Mapped[list["Assessment"]] = relationship("Assessment", back_populates="course", cascade="all, delete-orphan")
    lectures: Mapped[list["Lecture"]] = relationship("Lecture", back_populates="course", cascade="all, delete-orphan")
    syllabi: Mapped[list["Syllabus"]] = relationship("Syllabus", back_populates="course", cascade="all, delete-orphan")
    grade_items: Mapped[list["GradeItem"]] = relationship("GradeItem", back_populates="course", cascade="all, delete-orphan")


class Topic(Base):
    __tablename__ = "topics"

    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"))
    name: Mapped[str] = mapped_column(String(200))
    chapter: Mapped[str] = mapped_column(String(200), default="")
    # estimated_minutes: how long one full study pass of this topic takes
    estimated_minutes: Mapped[int] = mapped_column(Integer, default=45)
    # weight: user-defined importance, 1-3. Hard/important topics get higher weight
    # which means they appear more frequently in the schedule.
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    course: Mapped["Course"] = relationship("Course", back_populates="topics")
    sessions: Mapped[list["StudySession"]] = relationship("StudySession", back_populates="topic")
    quiz_attempts: Mapped[list["QuizAttempt"]] = relationship("QuizAttempt", back_populates="topic")
    questions: Mapped[list["QuizQuestion"]] = relationship("QuizQuestion", back_populates="topic", cascade="all, delete-orphan")


class StudySession(Base):
    __tablename__ = "study_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id"))
    date: Mapped[date] = mapped_column(Date)
    planned_minutes: Mapped[int] = mapped_column(Integer)
    # actual_minutes: filled in when user marks session complete
    actual_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    topic: Mapped["Topic"] = relationship("Topic", back_populates="sessions")


class QuizAttempt(Base):
    __tablename__ = "quiz_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id"))
    # score: 0.0-1.0 (fraction of questions answered correctly)
    score: Mapped[float] = mapped_column(Float)
    questions_total: Mapped[int] = mapped_column(Integer)
    questions_correct: Mapped[int] = mapped_column(Integer)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    topic: Mapped["Topic"] = relationship("Topic", back_populates="quiz_attempts")


class QuizQuestion(Base):
    """
    Stores quiz questions per topic. Multiple choice with 4 options.
    WHY store questions in DB (not hardcode): lets users add their own,
    and lets the system learn which questions are most useful over time.
    """
    __tablename__ = "quiz_questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id"))
    question: Mapped[str] = mapped_column(Text)
    option_a: Mapped[str] = mapped_column(Text)
    option_b: Mapped[str] = mapped_column(Text)
    option_c: Mapped[str] = mapped_column(Text)
    option_d: Mapped[str] = mapped_column(Text)
    correct: Mapped[str] = mapped_column(String(1))  # "a", "b", "c", or "d"
    explanation: Mapped[str] = mapped_column(Text, default="")

    topic: Mapped["Topic"] = relationship("Topic", back_populates="questions")


class Assessment(Base):
    """
    Upcoming quizzes/exams. The scheduler boosts topic priority when an
    assessment is within 7 days of today.
    """
    __tablename__ = "assessments"

    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"))
    title: Mapped[str] = mapped_column(String(200))
    date: Mapped[date] = mapped_column(Date)
    type: Mapped[AssessmentType] = mapped_column(Enum(AssessmentType), default=AssessmentType.quiz)
    notes: Mapped[str] = mapped_column(Text, default="")

    course: Mapped["Course"] = relationship("Course", back_populates="assessments")


class DayOff(Base):
    """Days the user marks as unavailable (holiday, sick, etc.)"""
    __tablename__ = "days_off"

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[date] = mapped_column(Date, unique=True)
    reason: Mapped[str] = mapped_column(String(200), default="")


class UserSettings(Base):
    """Global user preferences — single row, id=1."""
    __tablename__ = "user_settings"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    daily_minutes: Mapped[int] = mapped_column(Integer, default=135)   # 120-150 target
    max_course_pct: Mapped[float] = mapped_column(Float, default=0.6)  # cap per course
    streak: Mapped[int] = mapped_column(Integer, default=0)
    last_study_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    phone_number: Mapped[str] = mapped_column(String(30), default="")
    call_reminder_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    call_reminder_hour: Mapped[int] = mapped_column(Integer, default=8)


class Lecture(Base):
    __tablename__ = "lectures"

    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"))
    topic_id: Mapped[int | None] = mapped_column(ForeignKey("topics.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(240))
    source_filename: Mapped[str] = mapped_column(String(260), default="")
    source_type: Mapped[str] = mapped_column(String(20), default="manual")
    status: Mapped[str] = mapped_column(String(30), default="ready")
    summary: Mapped[str] = mapped_column(Text, default="")
    key_concepts_json: Mapped[str] = mapped_column(Text, default="[]")
    learning_objectives_json: Mapped[str] = mapped_column(Text, default="[]")
    extraction_error: Mapped[str] = mapped_column(Text, default="")
    estimated_minutes: Mapped[int] = mapped_column(Integer, default=45)
    mastery_score: Mapped[float] = mapped_column(Float, default=0.0)
    local_only: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    course: Mapped["Course"] = relationship("Course", back_populates="lectures")
    topic: Mapped["Topic"] = relationship("Topic")
    slides: Mapped[list["Slide"]] = relationship("Slide", back_populates="lecture", cascade="all, delete-orphan")
    questions: Mapped[list["LessonQuestion"]] = relationship("LessonQuestion", back_populates="lecture", cascade="all, delete-orphan")
    flashcards: Mapped[list["Flashcard"]] = relationship("Flashcard", back_populates="lecture", cascade="all, delete-orphan")
    attempts: Mapped[list["AnswerAttempt"]] = relationship("AnswerAttempt", back_populates="lecture", cascade="all, delete-orphan")


class Slide(Base):
    __tablename__ = "slides"

    id: Mapped[int] = mapped_column(primary_key=True)
    lecture_id: Mapped[int] = mapped_column(ForeignKey("lectures.id"))
    slide_number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(240), default="")
    text: Mapped[str] = mapped_column(Text, default="")
    content_tags_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    lecture: Mapped["Lecture"] = relationship("Lecture", back_populates="slides")


class LessonQuestion(Base):
    __tablename__ = "lesson_questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    lecture_id: Mapped[int] = mapped_column(ForeignKey("lectures.id"))
    topic_id: Mapped[int | None] = mapped_column(ForeignKey("topics.id"), nullable=True)
    slide_number: Mapped[int] = mapped_column(Integer, default=1)
    prompt: Mapped[str] = mapped_column(Text)
    option_a: Mapped[str] = mapped_column(Text)
    option_b: Mapped[str] = mapped_column(Text)
    option_c: Mapped[str] = mapped_column(Text)
    option_d: Mapped[str] = mapped_column(Text)
    correct: Mapped[str] = mapped_column(String(1))
    explanation: Mapped[str] = mapped_column(Text, default="")
    wrong_explanations_json: Mapped[str] = mapped_column(Text, default="{}")
    difficulty: Mapped[str] = mapped_column(String(20), default="medium")
    topic_tag: Mapped[str] = mapped_column(String(160), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    lecture: Mapped["Lecture"] = relationship("Lecture", back_populates="questions")
    topic: Mapped["Topic"] = relationship("Topic")
    attempts: Mapped[list["AnswerAttempt"]] = relationship("AnswerAttempt", back_populates="question", cascade="all, delete-orphan")


class AnswerAttempt(Base):
    __tablename__ = "answer_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    lecture_id: Mapped[int] = mapped_column(ForeignKey("lectures.id"))
    question_id: Mapped[int] = mapped_column(ForeignKey("lesson_questions.id"))
    selected: Mapped[str] = mapped_column(String(1))
    is_correct: Mapped[bool] = mapped_column(Boolean, default=False)
    explanation: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    lecture: Mapped["Lecture"] = relationship("Lecture", back_populates="attempts")
    question: Mapped["LessonQuestion"] = relationship("LessonQuestion", back_populates="attempts")


class Flashcard(Base):
    __tablename__ = "flashcards"

    id: Mapped[int] = mapped_column(primary_key=True)
    lecture_id: Mapped[int] = mapped_column(ForeignKey("lectures.id"))
    topic_id: Mapped[int | None] = mapped_column(ForeignKey("topics.id"), nullable=True)
    slide_number: Mapped[int] = mapped_column(Integer, default=1)
    front: Mapped[str] = mapped_column(Text)
    back: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    lecture: Mapped["Lecture"] = relationship("Lecture", back_populates="flashcards")
    topic: Mapped["Topic"] = relationship("Topic")


class CalendarBlock(Base):
    __tablename__ = "calendar_blocks"

    id: Mapped[int] = mapped_column(primary_key=True)
    lecture_id: Mapped[int | None] = mapped_column(ForeignKey("lectures.id"), nullable=True)
    topic_id: Mapped[int | None] = mapped_column(ForeignKey("topics.id"), nullable=True)
    date: Mapped[date] = mapped_column(Date)
    title: Mapped[str] = mapped_column(String(240))
    planned_minutes: Mapped[int] = mapped_column(Integer, default=30)
    status: Mapped[str] = mapped_column(String(30), default="planned")
    priority: Mapped[str] = mapped_column(String(30), default="normal")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    lecture: Mapped["Lecture"] = relationship("Lecture")
    topic: Mapped["Topic"] = relationship("Topic")


class Badge(Base):
    __tablename__ = "badges"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(80), unique=True)
    title: Mapped[str] = mapped_column(String(140))
    description: Mapped[str] = mapped_column(Text, default="")
    earned_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Syllabus(Base):
    __tablename__ = "syllabi"

    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"))
    source_filename: Mapped[str] = mapped_column(String(260), default="")
    status: Mapped[str] = mapped_column(String(30), default="ready")
    raw_text: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    extracted_dates_json: Mapped[str] = mapped_column(Text, default="[]")
    grading_weights_json: Mapped[str] = mapped_column(Text, default="[]")
    extraction_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    course: Mapped["Course"] = relationship("Course", back_populates="syllabi")
    grade_items: Mapped[list["GradeItem"]] = relationship("GradeItem", back_populates="syllabus", cascade="all, delete-orphan")


class GradeItem(Base):
    __tablename__ = "grade_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"))
    syllabus_id: Mapped[int | None] = mapped_column(ForeignKey("syllabi.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(220))
    category: Mapped[str] = mapped_column(String(60), default="coursework")
    weight_pct: Mapped[float] = mapped_column(Float, default=0.0)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    current_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    course: Mapped["Course"] = relationship("Course", back_populates="grade_items")
    syllabus: Mapped["Syllabus"] = relationship("Syllabus", back_populates="grade_items")
