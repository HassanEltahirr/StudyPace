from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import date, timedelta
from database import get_db, workspace_session
from models import Assessment
from schemas import AssessmentCreate, AssessmentOut
from services.simple_cache import clear_workspace_cache, get_cached, set_cached, workspace_cache_key

router = APIRouter()

@router.get("/", response_model=list[AssessmentOut])
def list_assessments(upcoming_only: bool = False):
    key = workspace_cache_key("assessments:list", bool(upcoming_only))
    cached = get_cached(key)
    if cached is not None:
        return cached
    with workspace_session() as db:
        q = db.query(Assessment)
        if upcoming_only:
            q = q.filter(Assessment.date >= date.today())
        payload = [AssessmentOut.model_validate(item).model_dump(mode="json") for item in q.order_by(Assessment.date).all()]
    set_cached(key, payload, ttl_seconds=300)
    return payload

@router.get("/upcoming", response_model=list[AssessmentOut])
def upcoming_assessments(days: int = 14):
    """Return assessments in the next `days` days — used by the dashboard alert."""
    key = workspace_cache_key("assessments:upcoming", int(days))
    cached = get_cached(key)
    if cached is not None:
        return cached
    with workspace_session() as db:
        cutoff = date.today() + timedelta(days=days)
        items = (db.query(Assessment)
                .filter(Assessment.date >= date.today(), Assessment.date <= cutoff)
                .order_by(Assessment.date)
                .all())
        payload = [AssessmentOut.model_validate(item).model_dump(mode="json") for item in items]
    set_cached(key, payload, ttl_seconds=300)
    return payload

@router.post("/", response_model=AssessmentOut, status_code=201)
def create_assessment(body: AssessmentCreate, db: Session = Depends(get_db)):
    a = Assessment(**body.model_dump())
    db.add(a); db.commit(); db.refresh(a)
    clear_workspace_cache()
    return a

@router.get("/{assessment_id}", response_model=AssessmentOut)
def get_assessment(assessment_id: int, db: Session = Depends(get_db)):
    a = db.get(Assessment, assessment_id)
    if not a: raise HTTPException(404, "Assessment not found")
    return a

@router.put("/{assessment_id}", response_model=AssessmentOut)
def update_assessment(assessment_id: int, body: AssessmentCreate, db: Session = Depends(get_db)):
    a = db.get(Assessment, assessment_id)
    if not a: raise HTTPException(404, "Assessment not found")
    for k, v in body.model_dump().items():
        setattr(a, k, v)
    db.commit(); db.refresh(a)
    clear_workspace_cache()
    return a

@router.delete("/{assessment_id}", status_code=204)
def delete_assessment(assessment_id: int, db: Session = Depends(get_db)):
    a = db.get(Assessment, assessment_id)
    if not a: raise HTTPException(404)
    db.delete(a); db.commit()
    clear_workspace_cache()
