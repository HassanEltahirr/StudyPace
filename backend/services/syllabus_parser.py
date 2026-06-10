from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from dateutil import parser as date_parser

from services.slide_parser import SlideExtractionError, extract_slides


@dataclass
class ParsedSyllabus:
    raw_text: str
    summary: str
    dates: list[dict]
    weights: list[dict]


DATE_HINT = re.compile(
    r"((?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+\d{1,2}(?:,\s*\d{4})?|\d{4}-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2}/\d{2,4})",
    re.I,
)

WEIGHT_HINT = re.compile(r"(.{3,80}?)(?:[:\-–—]|\s)\s*(\d{1,3}(?:\.\d+)?)\s*%")
PERCENT_ONLY = re.compile(r"^(\d{1,3}(?:\.\d+)?)\s*%$")


def parse_syllabus(filename: str, content: bytes) -> ParsedSyllabus:
    slides = extract_slides(filename, content)
    raw_text = "\n".join(slide.text for slide in slides)
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    dates = _extract_dates(lines)
    weights = _extract_weights(lines, dates)
    summary = _summary(lines, dates, weights)
    return ParsedSyllabus(raw_text=raw_text, summary=summary, dates=dates, weights=weights)


def _extract_dates(lines: list[str]) -> list[dict]:
    found: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for line in lines:
        for match in DATE_HINT.finditer(line):
            try:
                parsed = date_parser.parse(match.group(1), fuzzy=True, default=datetime.now().replace(month=1, day=1))
            except Exception:
                continue
            label = _label_from_line(line)
            item = {"label": label, "date": parsed.date().isoformat(), "source": line[:220]}
            key = (item["label"].lower(), item["date"])
            if key not in seen:
                seen.add(key)
                found.append(item)
    return found[:20]


def _extract_weights(lines: list[str], dates: list[dict]) -> list[dict]:
    table_weights = _extract_weight_table(lines)
    selected_table = _best_weight_group(table_weights)
    if selected_table:
        return selected_table[:20]

    weights: list[dict] = []
    seen: set[str] = set()
    for line in lines:
        if _ignore_weight_line(line):
            continue
        for title, value in WEIGHT_HINT.findall(line):
            pct = float(value)
            if pct <= 0 or pct > 100:
                continue
            clean_title = _clean_title(title)
            if not clean_title or clean_title.lower() in seen or not _looks_like_assessment_title(clean_title):
                continue
            seen.add(clean_title.lower())
            weights.append({
                "title": clean_title,
                "category": _category(clean_title),
                "weight_pct": pct,
                "due_date": _date_for_line(line, dates),
                "source": line[:220],
            })
    return weights[:20]


def _best_weight_group(weights: list[dict]) -> list[dict]:
    candidates: list[tuple[int, list[dict]]] = []
    for start in range(len(weights)):
        total = 0.0
        group: list[dict] = []
        for item in weights[start:]:
            group.append(item)
            total += item["weight_pct"]
            if 90 <= total <= 110 and len(group) >= 3:
                candidates.append((start, group.copy()))
            if total > 110:
                break
    if not candidates:
        return []
    return max(candidates, key=lambda candidate: (
        len(candidate[1]),
        sum(item["weight_pct"] for item in candidate[1]),
        candidate[0],
    ))[1]


def _extract_weight_table(lines: list[str]) -> list[dict]:
    weights: list[dict] = []
    seen: set[str] = set()
    for index, line in enumerate(lines):
        if _ignore_weight_line(line) or not _looks_like_assessment_title(line):
            continue
        pct = _nearby_percent(lines, index)
        if pct is None:
            continue
        clean_title = _clean_title(line)
        key = clean_title.lower()
        if key in seen:
            continue
        seen.add(key)
        weights.append({
            "title": clean_title,
            "category": _category(clean_title),
            "weight_pct": pct,
            "due_date": None,
            "source": " | ".join(lines[index:index + 3])[:220],
        })
    return weights


def _nearby_percent(lines: list[str], index: int) -> float | None:
    for offset in (1, 2):
        if index + offset >= len(lines):
            continue
        match = PERCENT_ONLY.match(lines[index + offset])
        if match:
            pct = float(match.group(1))
            return pct if 0 < pct <= 100 else None
    return None


def _ignore_weight_line(line: str) -> bool:
    lower = line.lower()
    if "penalty" in lower or "late submission" in lower:
        return True
    if "letter grade" in lower or "grade point" in lower or "percentage grade range" in lower:
        return True
    if re.search(r"\d+\s*%\s*[-–—]\s*\d+\s*%", line):
        return True
    return False


def _looks_like_assessment_title(title: str) -> bool:
    lower = title.lower().strip(" :")
    if not lower or "%" in lower:
        return False
    if lower in {"weight", "tentative dates", "assessment", "assessment methodology"}:
        return False
    return any(word in lower for word in [
        "quiz",
        "exam",
        "mid",
        "final",
        "assignment",
        "homework",
        "hw",
        "coursework",
        "project",
        "participation",
        "lab",
    ])


def _date_for_line(line: str, dates: list[dict]) -> str | None:
    for item in dates:
        if item["source"] == line[:220]:
            return item["date"]
    return None


def _label_from_line(line: str) -> str:
    lowered = line.lower()
    for keyword in ["final", "midterm", "exam", "quiz", "assignment", "project", "deadline"]:
        if keyword in lowered:
            return _clean_title(line)
    return _clean_title(line)


def _clean_title(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip(" :;-–—\t")
    value = re.sub(r"^(grading|grade|assessment|evaluation|weight|component)s?\s*", "", value, flags=re.I)
    if len(value) > 120:
        value = value[:117].rstrip() + "..."
    return value


def _category(title: str) -> str:
    lower = title.lower()
    if "final" in lower or "exam" in lower:
        return "exam"
    if "quiz" in lower:
        return "quiz"
    if "project" in lower:
        return "project"
    if "assignment" in lower or "homework" in lower or "hw" in lower:
        return "assignment"
    if "participation" in lower or "attendance" in lower:
        return "participation"
    return "coursework"


def _summary(lines: list[str], dates: list[dict], weights: list[dict]) -> str:
    pieces = []
    if weights:
        total = sum(item["weight_pct"] for item in weights)
        pieces.append(f"Found {len(weights)} grading components totaling {round(total, 1)}%.")
    if dates:
        pieces.append(f"Found {len(dates)} dated course events.")
    if not pieces:
        pieces.append("Syllabus text was extracted, but grading weights or exam dates need confirmation.")
    for line in lines[:3]:
        if len(line.split()) >= 5:
            pieces.append(line)
            break
    return " ".join(pieces)
