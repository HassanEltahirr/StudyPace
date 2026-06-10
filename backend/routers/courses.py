from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session
from database import get_db, workspace_session
from models import Course, CalendarBlock
from schemas import CourseCreate, CourseOut
from services.simple_cache import clear_workspace_cache, get_cached, set_cached, workspace_cache_key

router = APIRouter()

@router.get("/", response_model=list[CourseOut])
def list_courses():
    key = workspace_cache_key("courses:list")
    cached = get_cached(key)
    if cached is not None:
        return cached
    with workspace_session() as db:
        courses = db.query(Course).order_by(Course.id).all()
        payload = [CourseOut.model_validate(course).model_dump(mode="json") for course in courses]
    set_cached(key, payload, ttl_seconds=300)
    return payload

@router.get("/{course_id}", response_model=CourseOut)
def get_course(course_id: int):
    key = workspace_cache_key("courses:item", int(course_id))
    cached = get_cached(key)
    if cached is not None:
        return cached
    with workspace_session() as db:
        c = db.get(Course, course_id)
        if not c: raise HTTPException(404, "Course not found")
        payload = CourseOut.model_validate(c).model_dump(mode="json")
    set_cached(key, payload, ttl_seconds=300)
    return payload

@router.post("/", response_model=CourseOut, status_code=201)
def create_course(body: CourseCreate, db: Session = Depends(get_db)):
    c = Course(**body.model_dump())
    db.add(c)
    db.commit()
    course_id = c.id
    db.expire_all()
    c = db.query(Course).filter(Course.id == course_id).first()
    clear_workspace_cache()
    return c

@router.put("/{course_id}", response_model=CourseOut)
def update_course(course_id: int, body: CourseCreate, db: Session = Depends(get_db)):
    c = db.get(Course, course_id)
    if not c: raise HTTPException(404, "Course not found")
    for k, v in body.model_dump().items():
        setattr(c, k, v)
    db.commit()
    db.expire_all()
    c = db.query(Course).filter(Course.id == course_id).first()
    clear_workspace_cache()
    return c

@router.delete("/{course_id}", status_code=204)
def delete_course(course_id: int, db: Session = Depends(get_db)):
    c = db.get(Course, course_id)
    if not c: raise HTTPException(404, "Course not found")
    lecture_ids = [l.id for l in c.lectures]
    topic_ids = [t.id for t in c.topics]
    if lecture_ids or topic_ids:
        filters = []
        if lecture_ids:
            filters.append(CalendarBlock.lecture_id.in_(lecture_ids))
        if topic_ids:
            filters.append(CalendarBlock.topic_id.in_(topic_ids))
        db.query(CalendarBlock).filter(or_(*filters)).delete(synchronize_session=False)
    db.delete(c)
    db.commit()
    clear_workspace_cache()
