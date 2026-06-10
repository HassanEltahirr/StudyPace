from datetime import date
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session, joinedload
from database import get_db
from models import Topic, StudySession, QuizAttempt, Assessment, DayOff, UserSettings
from scheduler import generate_daily_plan
from schemas import DailyPlan

router = APIRouter()

@router.get("/today", response_model=DailyPlan)
def get_today_plan(db: Session = Depends(get_db)):
    return _build_plan(db, date.today())

@router.get("/{plan_date}", response_model=DailyPlan)
def get_plan_for_date(plan_date: date, db: Session = Depends(get_db)):
    return _build_plan(db, plan_date)

@router.post("/day-off/{off_date}", status_code=201)
def mark_day_off(off_date: date, reason: str = "", db: Session = Depends(get_db)):
    existing = db.query(DayOff).filter(DayOff.date == off_date).first()
    if not existing:
        db.add(DayOff(date=off_date, reason=reason))
        db.commit()
    return {"date": off_date, "reason": reason}

@router.delete("/day-off/{off_date}", status_code=204)
def unmark_day_off(off_date: date, db: Session = Depends(get_db)):
    d = db.query(DayOff).filter(DayOff.date == off_date).first()
    if d:
        db.delete(d); db.commit()


def _build_plan(db: Session, target_date: date) -> dict:
    """
    Load all needed data then delegate to the scheduler.
    WHY load everything at once: the scheduler needs cross-topic data
    (all sessions, all quiz attempts) to compute relative priorities.
    Loading lazily inside the algorithm would cause N+1 query problems.
    """
    topics = (
        db.query(Topic)
        .options(joinedload(Topic.course))
        .order_by(Topic.course_id, Topic.order, Topic.id)
        .all()
    )
    sessions = db.query(StudySession).all()
    attempts = db.query(QuizAttempt).all()
    assessments = db.query(Assessment).all()
    days_off = db.query(DayOff).all()
    settings = db.get(UserSettings, 1) or UserSettings(daily_minutes=135, max_course_pct=0.6)

    return generate_daily_plan(topics, sessions, attempts, assessments, days_off, settings, target_date)
