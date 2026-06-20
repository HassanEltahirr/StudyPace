"""
timetable.py — Coherent, exam-backward, multi-day study timetable.

WHY THIS EXISTS (vs scheduler.py):
  The old planner computed each day in isolation (`generate_daily_plan`), so a
  multi-day view was N independent greedy plans run against study history that
  barely changes day to day. Result: the same high-priority topics surfaced
  every single day ("messy / repeats the same courses").

  This module computes ONE coherent plan by forward-simulation: when it lays
  out Tuesday it already knows what Monday's plan accomplished, so a topic
  studied (in simulation) yesterday is not "due" again today. Reviews reappear
  on a spaced-repetition cadence — not because the algorithm forgot it scheduled
  them. It works backward from each exam date to guarantee every topic is
  learned-then-reviewed before its exam, and flags topics that can't fit so the
  student knows where they're at risk instead of trusting a plan that silently
  drops material.

  Pure functions over duck-typed objects (ORM rows or test fakes) — no DB here.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import date, timedelta
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# Spaced-repetition cadence
# ─────────────────────────────────────────────────────────────────────────────

# Days to wait before the next pass, indexed by how many passes a topic has had.
# Pass 0 -> first learn. After 1 pass, review in 2 days; after 2, in 6; etc.
# The forgetting curve widens as a topic is reinforced, so intervals grow.
_REVIEW_INTERVALS = [0, 2, 6, 13, 24]
_MAX_INTERVAL = 30

# A topic always gets one "final review" inside this window before its exam,
# regardless of where its normal cadence landed.
_FINAL_REVIEW_WINDOW_DAYS = 4


def _interval_for(passes: int, avg_quiz: Optional[float]) -> int:
    """Next-review gap in days. Weak topics (low quiz avg) come back sooner."""
    base = _REVIEW_INTERVALS[min(passes, len(_REVIEW_INTERVALS) - 1)]
    if base == 0:
        return 0
    # avg 0.0 -> 0.6x (sooner), 0.5 -> 1.0x, 1.0 -> 1.4x (later)
    scale = 0.6 + 0.8 * (avg_quiz if avg_quiz is not None else 0.5)
    return max(1, min(_MAX_INTERVAL, round(base * scale)))


# ─────────────────────────────────────────────────────────────────────────────
# Per-topic state derived from real history, then advanced during simulation
# ─────────────────────────────────────────────────────────────────────────────

class _TopicState:
    __slots__ = (
        "topic", "course_id", "exam_date", "avg_quiz", "passes",
        "last_studied", "next_due", "final_review_done", "emphasis", "priority_bump",
    )

    def __init__(self, topic, course_id, exam_date, avg_quiz, passes, last_studied,
                 emphasis=1.0, priority_bump=0.0):
        self.topic = topic
        self.course_id = course_id
        self.exam_date = exam_date
        self.avg_quiz = avg_quiz
        self.passes = passes
        self.last_studied = last_studied  # date | None
        self.final_review_done = False
        # AI-supplied steering (defaults = neutral, so a missing/failed agent is a no-op):
        self.emphasis = emphasis            # >1 = review sooner/more (harder topic)
        self.priority_bump = priority_bump  # additive urgency nudge
        self.next_due = self._compute_next_due(last_studied)

    def _compute_next_due(self, anchor: Optional[date]) -> Optional[date]:
        if anchor is None:
            return None  # never studied -> due as soon as coverage allows
        gap = _interval_for(self.passes, self.avg_quiz)
        if self.emphasis > 0:
            gap = max(1, round(gap / self.emphasis))
        return anchor + timedelta(days=gap)

    def record_pass(self, day: date, is_final_review: bool) -> None:
        self.passes += 1
        self.last_studied = day
        self.next_due = self._compute_next_due(day)
        if is_final_review:
            self.final_review_done = True


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _avg_quiz(topic_id: int, attempts: list) -> Optional[float]:
    qs = [q for q in attempts if q.topic_id == topic_id]
    if not qs:
        return None
    qs.sort(key=lambda q: q.timestamp, reverse=True)
    weights = [2 ** (-i * 0.5) for i in range(len(qs))]
    avg = sum(q.score * w for q, w in zip(qs, weights)) / sum(weights)
    return max(0.0, min(1.0, avg))


def _initial_state(topic_id: int, sessions: list, today: date) -> tuple[int, Optional[date]]:
    """Returns (passes_already_done, last_studied_date) from completed history."""
    done = [s for s in sessions if s.topic_id == topic_id and getattr(s, "completed", False)]
    if not done:
        return 0, None
    return len(done), max(s.date for s in done)


def _course_exam_date(course_id, courses_by_id, assessments, today) -> Optional[date]:
    """Earliest relevant exam date: Course.exam_date or earliest future assessment."""
    candidates: list[date] = []
    course = courses_by_id.get(course_id)
    if course is not None and getattr(course, "exam_date", None):
        candidates.append(course.exam_date)
    for a in assessments:
        if a.course_id == course_id and a.date >= today:
            candidates.append(a.date)
    future = [d for d in candidates if d >= today]
    return min(future) if future else None


def _tie(topic_id: int, day: date) -> int:
    return int.from_bytes(hashlib.md5(f"{topic_id}-{day.isoformat()}".encode()).digest()[:4], "big")


# ─────────────────────────────────────────────────────────────────────────────
# Bloom's taxonomy laddering (premium "research-backed depth" layer)
# ─────────────────────────────────────────────────────────────────────────────
#
# Each scheduled pass is tagged with a target cognitive level from Bloom's
# revised taxonomy. The level climbs as a topic is reinforced — early passes
# build foundations (Remember/Understand), middle passes practise transfer
# (Apply/Analyze), and the pre-exam pass demands exam-level synthesis
# (Evaluate). Pairing spaced repetition with rising cognitive demand is what
# the study-science literature calls "successive relearning"; the focus line
# tells the student *how* to study each pass, not just what.

def _bloom_for(pass_type: str, pass_number: int) -> tuple[str, str]:
    if pass_type == "final_review":
        return ("Evaluate", "Judge trade-offs and predict the questions an examiner would ask.")
    if pass_type == "learn":
        return ("Remember", "Build the foundation: capture the key terms and definitions.")
    # review passes deepen with each repetition
    if pass_number <= 2:
        return ("Understand", "Reconstruct the reasoning from memory, in your own words.")
    if pass_number == 3:
        return ("Apply", "Work unseen problems end-to-end without looking back.")
    return ("Analyze", "Break ideas apart and connect them across topics.")


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

def generate_timetable(
    topics: list,
    sessions: list,
    attempts: list,
    assessments: list,
    courses: list,
    days_off: list,
    settings,
    start_date: Optional[date] = None,
    horizon_days: Optional[int] = None,
    topic_overrides: Optional[dict] = None,
    course_intensity: Optional[dict] = None,
) -> dict:
    """
    Build a coherent multi-day timetable.

    Returns:
      {
        "start_date", "end_date",
        "days": [ {date, is_day_off, total_minutes, items:[...]} ],
        "courses": [ {course_id, name, exam_date, days_until_exam,
                      coverage_pct, reviewed_pct, at_risk:[topic_name,...]} ],
        "summary": {topics_total, topics_at_risk, fully_ready_courses, ...}
      }

    Each item: topic_id, topic_name, course_name, course_color, chapter,
               planned_minutes, pass_type ("learn"|"review"|"final_review"),
               pass_number, days_since_studied, avg_quiz_score, exam_in_days.
    """
    today = start_date or date.today()
    budget = max(30, int(getattr(settings, "daily_minutes", 135)))
    max_course_pct = float(getattr(settings, "max_course_pct", 0.6))
    off_dates = {d.date for d in days_off}
    courses_by_id = {c.id: c for c in courses}
    topic_overrides = topic_overrides or {}
    course_intensity = course_intensity or {}

    # Resolve exam date per course; horizon runs to the last exam (+2 days) or a cap.
    exam_by_course: dict[int, Optional[date]] = {}
    for cid in {t.course_id for t in topics}:
        exam_by_course[cid] = _course_exam_date(cid, courses_by_id, assessments, today)
    exam_dates = [d for d in exam_by_course.values() if d]
    if horizon_days is None:
        if exam_dates:
            horizon_days = (max(exam_dates) - today).days + 2
        else:
            horizon_days = 21
    horizon_days = max(1, min(horizon_days, 90))
    end_date = today + timedelta(days=horizon_days - 1)

    # Build per-topic simulation state from real history.
    states: dict[int, _TopicState] = {}
    for t in topics:
        passes, last = _initial_state(t.id, sessions, today)
        ov = topic_overrides.get(t.id, {})
        states[t.id] = _TopicState(
            topic=t,
            course_id=t.course_id,
            exam_date=exam_by_course.get(t.course_id),
            avg_quiz=_avg_quiz(t.id, attempts),
            passes=passes,
            last_studied=last,
            emphasis=float(ov.get("emphasis", 1.0)),
            priority_bump=float(ov.get("priority_bump", 0.0)),
        )

    # Per-course fair share so no course is crowded out.
    num_courses = len({t.course_id for t in topics}) or 1
    fair_share = budget // num_courses
    max_per_course = min(int(budget * max_course_pct), fair_share + 20)

    days_out: list[dict] = []

    for offset in range(horizon_days):
        day = today + timedelta(days=offset)
        if day in off_dates:
            days_out.append({"date": day, "is_day_off": True, "total_minutes": 0, "items": []})
            continue

        items = _plan_one_day(
            day, states, courses_by_id, budget, max_per_course, today, course_intensity,
            num_courses,
        )
        total = sum(i["planned_minutes"] for i in items)
        days_out.append({"date": day, "is_day_off": False, "total_minutes": total, "items": items})

    courses_summary = _coverage_report(states, courses_by_id, exam_by_course, today, end_date)
    summary = _roll_up(courses_summary, states)

    return {
        "start_date": today,
        "end_date": end_date,
        "days": days_out,
        "courses": courses_summary,
        "summary": summary,
    }


def _plan_one_day(day, states, courses_by_id, budget, max_per_course, today,
                  course_intensity=None, num_courses=1) -> list[dict]:
    """Select topics for a single simulated day and advance their state."""
    course_intensity = course_intensity or {}
    # Which topics are actionable today, and at what urgency?
    candidates: list[tuple[float, str, _TopicState]] = []
    for st in states.values():
        urgency, pass_type = _urgency(st, day)
        if urgency <= 0:
            continue
        # AI coordinator can scale a course up/down; topic agent can nudge one topic.
        urgency = urgency * float(course_intensity.get(st.course_id, 1.0)) + st.priority_bump
        candidates.append((urgency, pass_type, st))

    # Per-course minute cap for *today*. The base cap (max_course_pct) is a
    # fairness knob to stop one course hogging the day when many compete — but it
    # backfires in a crunch: a lone imminent exam would be throttled and its
    # topics spill onto (or past) the exam day. So relax the cap toward the full
    # budget when (a) only one or two courses have work, or (b) a course's exam
    # is imminent — that course needs whatever time it takes to make the deadline.
    competing = len({st.course_id for _, _, st in candidates}) or 1
    caps: dict[int, int] = {}
    for cid in {st.course_id for _, _, st in candidates}:
        course = courses_by_id.get(cid)
        exam = getattr(course, "exam_date", None) if course else None
        exam_in = (exam - day).days if exam else None
        imminent = exam_in is not None and exam_in <= _FINAL_REVIEW_WINDOW_DAYS + 2
        if competing <= 2 or imminent:
            caps[cid] = budget
        else:
            caps[cid] = max_per_course

    # Highest urgency first; rotate exact ties by day so we don't lock an order.
    candidates.sort(key=lambda c: (round(c[0], 4), _tie(c[2].topic.id, day)), reverse=True)

    # Interleave by course so every due course gets a slot before any gets a second.
    by_course: dict[int, list] = defaultdict(list)
    for c in candidates:
        by_course[c[2].course_id].append(c)
    interleaved: list[tuple[float, str, _TopicState]] = []
    queues = list(by_course.values())
    for i in range(max((len(q) for q in queues), default=0)):
        for q in queues:
            if i < len(q):
                interleaved.append(q[i])

    remaining = budget
    course_minutes: dict[int, int] = defaultdict(int)
    items: list[dict] = []

    for urgency, pass_type, st in interleaved:
        if remaining < 15:
            break
        cap = caps.get(st.course_id, max_per_course)
        if cap - course_minutes[st.course_id] < 15:
            continue

        est = max(15, int(getattr(st.topic, "estimated_minutes", 45)))
        planned = est if pass_type == "learn" else max(15, int(est * 0.5))
        planned = min(planned, remaining, cap - course_minutes[st.course_id])
        planned = max(planned, 15)

        course = courses_by_id.get(st.course_id)
        days_since = (day - st.last_studied).days if st.last_studied else -1
        bloom_level, bloom_focus = _bloom_for(pass_type, st.passes + 1)
        items.append({
            "topic_id": st.topic.id,
            "topic_name": st.topic.name,
            "course_name": getattr(course, "name", "") if course else "",
            "course_color": getattr(course, "color", "#007aff") if course else "#007aff",
            "chapter": getattr(st.topic, "chapter", ""),
            "planned_minutes": planned,
            "pass_type": pass_type,
            "pass_number": st.passes + 1,
            "days_since_studied": days_since,
            "avg_quiz_score": st.avg_quiz,
            "exam_in_days": (st.exam_date - day).days if st.exam_date else -1,
            "bloom_level": bloom_level,
            "bloom_focus": bloom_focus,
        })

        st.record_pass(day, is_final_review=(pass_type == "final_review"))
        course_minutes[st.course_id] += planned
        remaining -= planned

    return items


def _urgency(st: _TopicState, day: date) -> tuple[float, str]:
    """
    Return (urgency, pass_type). urgency<=0 means 'do not schedule today'.

    This is the anti-repeat heart: a topic studied in simulation is pushed to
    next_due, so it scores 0 until its review actually comes due.
    """
    exam = st.exam_date
    exam_in = (exam - day).days if exam else None

    # Past its exam? Stop scheduling it.
    if exam_in is not None and exam_in < 0:
        return 0.0, "review"

    # Final-review window: force one last pass right before the exam.
    if exam_in is not None and 0 <= exam_in <= _FINAL_REVIEW_WINDOW_DAYS:
        if not st.final_review_done and st.passes >= 1:
            # Closer to exam = more urgent; weak topics rank higher.
            return 100.0 + (5 - exam_in) + (1 - (st.avg_quiz or 0.5)), "final_review"

    # Never learned yet -> coverage pressure, front-loaded by exam proximity.
    if st.passes == 0:
        # Less runway before the exam => higher urgency now.
        proximity = 0.0
        if exam_in is not None:
            proximity = max(0.0, (30 - exam_in)) / 30  # 0..1, 1 == exam imminent
        weakness = 1 - (st.avg_quiz or 0.5)
        weight = (float(getattr(st.topic, "weight", 1.0)) - 0.5) / 2.5
        return 40.0 + 20 * proximity + 8 * weakness + 4 * weight, "learn"

    # Already learned: only schedule when a review is actually due.
    if st.next_due is not None and day >= st.next_due:
        overdue = (day - st.next_due).days
        weakness = 1 - (st.avg_quiz or 0.5)
        proximity = 0.0
        if exam_in is not None:
            proximity = max(0.0, (30 - exam_in)) / 30
        return 20.0 + min(overdue, 10) + 6 * weakness + 6 * proximity, "review"

    return 0.0, "review"


def _coverage_report(states, courses_by_id, exam_by_course, today, end_date) -> list[dict]:
    """Per-course readiness: how much is learned + reviewed before its exam."""
    by_course: dict[int, list[_TopicState]] = defaultdict(list)
    for st in states.values():
        by_course[st.course_id].append(st)

    out = []
    for cid, sts in by_course.items():
        course = courses_by_id.get(cid)
        exam = exam_by_course.get(cid)
        total = len(sts)
        learned = sum(1 for s in sts if s.passes >= 1)
        reviewed = sum(1 for s in sts if s.passes >= 2)
        # A topic is "at risk" if, by the end of the sim, it isn't learned, or
        # (when it has an exam) didn't get its final review.
        at_risk = []
        for s in sts:
            ready = s.passes >= 1 and (exam is None or s.final_review_done or s.passes >= 2)
            if not ready:
                at_risk.append(s.topic.name)
        out.append({
            "course_id": cid,
            "name": getattr(course, "name", "") if course else "",
            "color": getattr(course, "color", "#007aff") if course else "#007aff",
            "exam_date": exam,
            "days_until_exam": (exam - today).days if exam else None,
            "coverage_pct": round(100 * learned / total) if total else 0,
            "reviewed_pct": round(100 * reviewed / total) if total else 0,
            "topics_total": total,
            "at_risk": at_risk,
        })
    out.sort(key=lambda c: (c["days_until_exam"] is None, c["days_until_exam"] or 0))
    return out


def _roll_up(courses_summary, states) -> dict:
    at_risk = sum(len(c["at_risk"]) for c in courses_summary)
    ready_courses = sum(1 for c in courses_summary if not c["at_risk"] and c["exam_date"])
    exam_courses = sum(1 for c in courses_summary if c["exam_date"])
    return {
        "topics_total": len(states),
        "topics_at_risk": at_risk,
        "exam_courses": exam_courses,
        "fully_ready_courses": ready_courses,
        "on_track": at_risk == 0,
    }
