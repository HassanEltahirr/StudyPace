"""
quiz.py — Quiz endpoints.

Flow:
  GET /api/quiz/{topic_id}/questions  → returns N random questions (without answers)
  POST /api/quiz/submit               → grades answers, stores QuizAttempt, returns results
  GET /api/quiz/attempts              → history of all attempts (for dashboard)
"""

import random
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import QuizQuestion, QuizAttempt
from schemas import QuizQuestionOut, QuizSubmission, QuizResult, QuizAttemptOut

router = APIRouter()

QUESTIONS_PER_QUIZ = 5  # keeps quizzes snappy — 5 questions, ~3-4 min


@router.get("/{topic_id}/questions", response_model=list[QuizQuestionOut])
def get_questions(topic_id: int, db: Session = Depends(get_db)):
    """
    Return a random sample of questions for a topic.
    WHY random: repeating the same 5 questions builds pattern recognition,
    not actual understanding. Randomising forces real recall.
    """
    qs = db.query(QuizQuestion).filter(QuizQuestion.topic_id == topic_id).all()
    if not qs:
        raise HTTPException(404, "No questions found for this topic. Add some in Courses → Topics.")
    sample = random.sample(qs, min(QUESTIONS_PER_QUIZ, len(qs)))
    # Return schema strips out the `correct` field (defined in QuizQuestionOut)
    return sample


@router.post("/submit", response_model=QuizResult)
def submit_quiz(body: QuizSubmission, db: Session = Depends(get_db)):
    """
    Grade the submitted answers, persist a QuizAttempt, return per-question feedback.
    The score feeds back into the scheduler's weakness_score for this topic.
    """
    question_ids = [a.question_id for a in body.answers]
    questions = {q.id: q for q in db.query(QuizQuestion).filter(QuizQuestion.id.in_(question_ids)).all()}

    per_question = []
    correct_count = 0

    for answer in body.answers:
        q = questions.get(answer.question_id)
        if not q:
            continue
        is_correct = answer.selected.lower() == q.correct.lower()
        if is_correct:
            correct_count += 1
        per_question.append({
            "question_id": q.id,
            "question": q.question,
            "selected": answer.selected,
            "correct_answer": q.correct,
            "is_correct": is_correct,
            "explanation": q.explanation,
        })

    total = len(per_question)
    score = correct_count / total if total > 0 else 0.0

    attempt = QuizAttempt(
        topic_id=body.topic_id,
        score=score,
        questions_total=total,
        questions_correct=correct_count,
    )
    db.add(attempt); db.commit()

    return QuizResult(
        topic_id=body.topic_id,
        score=round(score, 2),
        correct=correct_count,
        total=total,
        per_question=per_question,
    )


@router.get("/attempts", response_model=list[QuizAttemptOut])
def list_attempts(topic_id: int | None = None, db: Session = Depends(get_db)):
    q = db.query(QuizAttempt)
    if topic_id:
        q = q.filter(QuizAttempt.topic_id == topic_id)
    return q.order_by(QuizAttempt.timestamp.desc()).limit(200).all()
