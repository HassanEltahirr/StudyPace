"""
scheduler.py — The core study scheduling algorithm.

DESIGN PHILOSOPHY:
  - Anti-cramming: spreads topics over the whole semester, not just "what's
    coming up next". Every topic gets visited regularly from day one.
  - Spaced repetition: topics not studied recently climb in priority.
    This mirrors the Ebbinghaus forgetting curve — you need to revisit
    material before you forget it, not after.
  - Weakness-driven: low quiz scores boost a topic's priority so the system
    automatically schedules more review of things you struggle with.
  - Assessment-aware: when a quiz or exam is within 7 days, its course's
    topics get a priority multiplier so you naturally spend more time there.
  - Balanced: no single course can dominate a session (capped at 60% by default).
    Forces you to make progress on every course, not just your favourite.
"""

import hashlib
from datetime import date, timedelta
from collections import defaultdict
from typing import Optional

from models import Topic, StudySession, QuizAttempt, Assessment, DayOff, UserSettings


# ─────────────────────────────────────────────────────────────────────────────
# Priority calculation
# ─────────────────────────────────────────────────────────────────────────────

def _recency_score(topic_id: int, sessions: list[StudySession], today: date) -> tuple[float, int]:
    """
    Returns (score 0-1, days_since_last_study).

    WHY cap at 14 days: after 2 weeks without review, the forgetting curve
    has already done most of its damage. Treating 14 days and 30 days the
    same prevents edge cases where a long-untouched topic always beats fresher
    material that also needs review.
    """
    topic_sessions = [s for s in sessions if s.topic_id == topic_id and s.completed]
    if not topic_sessions:
        return 1.0, 999  # Never studied = maximum recency urgency

    last_date = max(s.date for s in topic_sessions)
    days_since = (today - last_date).days
    score = min(days_since, 14) / 14  # 0 = studied today, 1 = 14+ days ago
    return score, days_since


def _weakness_score(topic_id: int, quiz_attempts: list[QuizAttempt]) -> tuple[float, Optional[float]]:
    """
    Returns (weakness 0-1, avg_quiz_score or None).

    WHY decay older attempts: your understanding of a topic changes over time.
    A quiz you aced 2 months ago might not reflect current knowledge after
    you've half-forgotten it. We give more weight to recent attempts.
    """
    topic_quizzes = sorted(
        [q for q in quiz_attempts if q.topic_id == topic_id],
        key=lambda q: q.timestamp, reverse=True
    )
    if not topic_quizzes:
        return 0.5, None  # Unknown = medium weakness (neither penalise nor reward)

    # Weighted average: most recent quiz counts double
    weights = [2 ** (-i * 0.5) for i in range(len(topic_quizzes))]  # 1, 0.7, 0.5, ...
    weighted_score = sum(q.score * w for q, w in zip(topic_quizzes, weights))
    total_weight = sum(weights)
    avg = weighted_score / total_weight

    # Clamp avg to [0, 1] for safety
    avg = max(0.0, min(1.0, avg))
    weakness = 1.0 - avg  # 0 = perfect, 1 = terrible
    return weakness, round(avg, 2)


def _assessment_boost(course_id: int, assessments: list[Assessment], today: date) -> bool:
    """
    Returns True if there's a quiz or exam within 14 days for this course.

    WHY 14 days: two weeks is the standard final-exam review window. Boosting
    priority this far out lets the planner front-load weaker topics before the
    last-minute crunch, rather than only reacting in the final week.
    """
    return any(
        a.course_id == course_id and 0 <= (a.date - today).days <= 14
        for a in assessments
    )


def compute_priority(
    topic: Topic,
    sessions: list[StudySession],
    quiz_attempts: list[QuizAttempt],
    assessments: list[Assessment],
    today: date,
) -> dict:
    """
    Compute a composite priority score for one topic.

    Formula:
      priority = (0.40 * recency + 0.40 * weakness + 0.20 * weight_norm) * boost

    WHY these weights:
      - Recency (40%): the biggest driver. If you haven't studied something
        recently, it must be revisited — this is the core of spaced repetition.
      - Weakness (40%): quiz scores are the most objective signal we have of
        what you actually know. Poor scores mean more practice is needed.
      - Topic weight (20%): the user-defined importance. Keeps instructor-set
        priorities in the mix without letting them override objective signals.
      - Assessment boost (×1.5): a 50% boost when an assessment is imminent.
        Additive boosts (e.g., +0.3) interact poorly with scores near 1;
        a multiplier scales proportionally with existing priority.
    """
    recency, days_since = _recency_score(topic.id, sessions, today)
    weakness, avg_quiz = _weakness_score(topic.id, quiz_attempts)

    # Normalize topic weight to 0-1 range (weight is 0.5-3.0)
    weight_norm = (topic.weight - 0.5) / 2.5

    boost = _assessment_boost(topic.course_id, assessments, today)
    boost_multiplier = 1.5 if boost else 1.0

    raw = (0.40 * recency + 0.40 * weakness + 0.20 * weight_norm)
    priority = raw * boost_multiplier

    return {
        "score": round(priority, 4),
        "days_since": days_since,
        "avg_quiz": avg_quiz,
        "assessment_boost": boost,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Daily plan generation
# ─────────────────────────────────────────────────────────────────────────────

def generate_daily_plan(
    topics: list[Topic],
    sessions: list[StudySession],
    quiz_attempts: list[QuizAttempt],
    assessments: list[Assessment],
    days_off: list[DayOff],
    settings: UserSettings,
    target_date: date | None = None,
) -> dict:
    """
    Generate a study plan for target_date (defaults to today).

    Returns a dict with:
      - date
      - is_day_off
      - total_minutes (the budget used)
      - items: list of {topic, planned_minutes, priority_score, ...}

    Algorithm:
      1. Check if target_date is a day off → return empty plan.
      2. Score all topics by priority.
      3. Sort descending by priority.
      4. Greedily assign time, respecting:
         a. Total daily budget (settings.daily_minutes ±15 min)
         b. Per-course cap (settings.max_course_pct of budget)
         c. Minimum block size of 15 min (shorter = not worth context-switching)
      5. Return the plan.

    WHY greedy (not optimal): the optimal assignment (knapsack) is NP-hard
    and overkill here. Priority ordering + greedy gives a good-enough result
    in O(n log n) time and is easy to understand and debug.
    """
    today = target_date or date.today()

    # Check day off
    off_dates = {d.date for d in days_off}
    if today in off_dates:
        return {"date": today, "is_day_off": True, "total_minutes": 0, "items": []}

    budget = settings.daily_minutes

    # Cap per course at the lower of: the configured % OR a fair share per course.
    # With 4 equal-priority courses, the 60% cap would let 2 courses dominate and
    # leave the other 2 with nothing. Fair share ensures every course appears daily.
    num_courses = len({t.course_id for t in topics}) or 1
    fair_share = budget // num_courses
    max_per_course = min(int(budget * settings.max_course_pct), fair_share + 15)

    # Score every topic
    scored = []
    for topic in topics:
        p = compute_priority(topic, sessions, quiz_attempts, assessments, today)
        scored.append((p["score"], topic, p))

    # Sort: highest priority first. Break score ties with a date-seeded hash so
    # the plan rotates through all topics day-to-day rather than repeating the same
    # subset when all scores are equal (e.g. day 1 with no study history).
    studied_ids = {s.topic_id for s in sessions if s.completed}

    def _daily_tie_breaker(topic_id: int) -> int:
        key = f"{topic_id}-{today.isoformat()}".encode()
        return int.from_bytes(hashlib.md5(key).digest()[:4], "big")

    scored.sort(key=lambda x: (round(x[0], 3), _daily_tie_breaker(x[1].id)), reverse=True)

    # Coverage compression: if total topic time exceeds remaining days × budget,
    # scale per-topic blocks down so every topic gets at least a visit.
    unseen = [t for _, t, _ in scored if t.id not in studied_ids]
    days_until_assessments = _days_until_next_assessment(
        {t.course_id for _, t, _ in scored}, assessments, today
    )
    if unseen and days_until_assessments and days_until_assessments > 0:
        available_minutes = days_until_assessments * budget
        total_unseen_minutes = sum(max(t.estimated_minutes, 15) for t in unseen)
        if total_unseen_minutes > available_minutes:
            compression = available_minutes / total_unseen_minutes
        else:
            compression = 1.0
    else:
        compression = 1.0

    # Interleave topics by course so every course gets at least one slot before
    # any course gets a second. This prevents one high-priority course from
    # consuming the entire budget before others appear.
    course_queues: dict[int, list] = defaultdict(list)
    for item in scored:
        course_queues[item[1].course_id].append(item)

    interleaved = []
    queues = list(course_queues.values())
    max_len = max((len(q) for q in queues), default=0)
    for i in range(max_len):
        for q in queues:
            if i < len(q):
                interleaved.append(q[i])

    course_minutes: dict[int, int] = defaultdict(int)
    remaining = budget
    items = []

    for score, topic, meta in interleaved:
        if remaining < 15:
            break

        course_remaining = max_per_course - course_minutes[topic.course_id]
        if course_remaining < 15:
            continue

        estimated = topic.estimated_minutes
        if topic.id not in studied_ids and compression < 1.0:
            estimated = max(15, int(estimated * compression))

        minutes = min(estimated, remaining, course_remaining)
        minutes = max(minutes, 15)

        items.append({
            "topic_id": topic.id,
            "topic_name": topic.name,
            "course_name": topic.course.name if topic.course else "",
            "course_color": topic.course.color if topic.course else "#007aff",
            "chapter": topic.chapter,
            "planned_minutes": minutes,
            "priority_score": score,
            "days_since_studied": meta["days_since"] if meta["days_since"] < 999 else -1,
            "avg_quiz_score": meta["avg_quiz"],
            "assessment_boost": meta["assessment_boost"],
        })

        course_minutes[topic.course_id] += minutes
        remaining -= minutes

    return {
        "date": today,
        "is_day_off": False,
        "total_minutes": budget - remaining,
        "items": items,
    }


def _days_until_next_assessment(
    course_ids: set[int],
    assessments: list[Assessment],
    today: date,
) -> Optional[int]:
    upcoming = [
        (a.date - today).days
        for a in assessments
        if a.course_id in course_ids and (a.date - today).days > 0
    ]
    return min(upcoming) if upcoming else None
