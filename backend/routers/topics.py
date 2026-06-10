from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import Topic
from schemas import TopicCreate, TopicOut

router = APIRouter()

@router.get("/", response_model=list[TopicOut])
def list_topics(course_id: int | None = None, db: Session = Depends(get_db)):
    q = db.query(Topic)
    if course_id: q = q.filter(Topic.course_id == course_id)
    return q.order_by(Topic.course_id, Topic.order).all()

@router.post("/", response_model=TopicOut, status_code=201)
def create_topic(body: TopicCreate, db: Session = Depends(get_db)):
    t = Topic(**body.model_dump())
    db.add(t); db.commit(); db.refresh(t)
    return t

@router.put("/{topic_id}", response_model=TopicOut)
def update_topic(topic_id: int, body: TopicCreate, db: Session = Depends(get_db)):
    t = db.get(Topic, topic_id)
    if not t: raise HTTPException(404, "Topic not found")
    for k, v in body.model_dump().items():
        setattr(t, k, v)
    db.commit(); db.refresh(t)
    return t

@router.delete("/{topic_id}", status_code=204)
def delete_topic(topic_id: int, db: Session = Depends(get_db)):
    t = db.get(Topic, topic_id)
    if not t: raise HTTPException(404)
    db.delete(t); db.commit()
