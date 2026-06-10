from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from database import get_db, workspace_session
from models import UserSettings
from schemas import SettingsUpdate, SettingsOut
from services.simple_cache import clear_workspace_cache, get_cached, set_cached, workspace_cache_key

router = APIRouter()

@router.get("/", response_model=SettingsOut)
def get_settings():
    key = workspace_cache_key("settings")
    cached = get_cached(key)
    if cached is not None:
        return cached
    with workspace_session() as db:
        s = db.get(UserSettings, 1)
        if not s:
            s = UserSettings(id=1); db.add(s); db.commit(); db.refresh(s)
        payload = SettingsOut.model_validate(s).model_dump(mode="json")
    set_cached(key, payload, ttl_seconds=300)
    return payload

@router.patch("/", response_model=SettingsOut)
def update_settings(body: SettingsUpdate, db: Session = Depends(get_db)):
    s = db.get(UserSettings, 1)
    if not s:
        s = UserSettings(id=1); db.add(s)
    if body.daily_minutes is not None:
        s.daily_minutes = body.daily_minutes
    if body.max_course_pct is not None:
        s.max_course_pct = body.max_course_pct
    if body.phone_number is not None:
        s.phone_number = body.phone_number.strip()
    if body.call_reminder_enabled is not None:
        s.call_reminder_enabled = body.call_reminder_enabled
    if body.call_reminder_hour is not None:
        s.call_reminder_hour = body.call_reminder_hour
    db.commit(); db.refresh(s)
    clear_workspace_cache()
    return s
