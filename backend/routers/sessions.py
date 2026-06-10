from datetime import date, timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import StudySession, UserSettings
from schemas import SessionCreate, SessionComplete, SessionOut
from services.simple_cache import clear_workspace_cache, get_cached, set_cached, workspace_cache_key

router = APIRouter()

@router.get("/", response_model=list[SessionOut])
def list_sessions(from_date: date | None = None, to_date: date | None = None,
                  db: Session = Depends(get_db)):
    key = workspace_cache_key("sessions:list", from_date.isoformat() if from_date else "", to_date.isoformat() if to_date else "")
    cached = get_cached(key)
    if cached is not None:
        return cached
    q = db.query(StudySession)
    if from_date: q = q.filter(StudySession.date >= from_date)
    if to_date:   q = q.filter(StudySession.date <= to_date)
    payload = [SessionOut.model_validate(item).model_dump(mode="json") for item in q.order_by(StudySession.date.desc()).all()]
    set_cached(key, payload, ttl_seconds=20)
    return payload

@router.post("/", response_model=SessionOut, status_code=201)
def create_session(body: SessionCreate, db: Session = Depends(get_db)):
    s = StudySession(**body.model_dump())
    db.add(s); db.commit(); db.refresh(s)
    clear_workspace_cache()
    return s

@router.post("/{session_id}/complete", response_model=SessionOut)
def complete_session(session_id: int, body: SessionComplete, db: Session = Depends(get_db)):
    """
    Mark a session done and update the user's streak.
    WHY update streak here: this is the single authoritative point where
    'study happened today' is recorded. Centralizing it prevents streak bugs.
    """
    s = db.get(StudySession, session_id)
    if not s: raise HTTPException(404)
    s.completed = True
    s.actual_minutes = body.actual_minutes
    s.notes = body.notes

    # Update streak
    settings = db.get(UserSettings, 1)
    if settings:
        today = date.today()
        if settings.last_study_date == today - timedelta(days=1):
            settings.streak += 1
        elif settings.last_study_date != today:
            settings.streak = 1
        settings.last_study_date = today

    db.commit(); db.refresh(s)
    clear_workspace_cache()
    return s
