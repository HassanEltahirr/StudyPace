"""OpenAI-compatible practice question generation.

Targets any OpenAI-compatible chat-completions endpoint via OPENAI_BASE_URL +
OPENAI_API_KEY (e.g. a local 9router proxying Claude Code / Codex
subscriptions). Dormant unless both env vars are set, so production is
unaffected until the secrets exist.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:
    pass


PRACTICE_SYSTEM_PROMPT = (
    "You are an exam question generator for Gulf university engineering and CS students. "
    "Write questions at the level of a KU or AUS final exam. "
    "Questions must require calculation or application — never definition recall. "
    "Distractors must reflect the most common student errors for this topic. "
    "Return only a valid JSON array, no markdown, no preamble."
)


def _env_float(name: str, fallback: float) -> float:
    try:
        return float(os.getenv(name, fallback))
    except (TypeError, ValueError):
        return fallback


def openai_available() -> bool:
    return bool(os.getenv("OPENAI_API_KEY") and os.getenv("OPENAI_BASE_URL"))


def generate_practice_mcqs_openai(
    title: str,
    slide_texts: list[tuple[int, str, str]],
    difficulty: str = "medium",
    count: int = 5,
) -> list[dict] | None:
    """Generate MCQs in the normalized practice shape
    ({question, choices, correct, explanation}). Returns None when unavailable."""
    if not slide_texts or not openai_available():
        return None

    blocks: list[str] = []
    for num, slide_title, text in slide_texts[:50]:
        header = f"Slide {num}: {slide_title}" if slide_title else f"Slide {num}"
        blocks.append(f"{header}\n{text.strip()[:500]}")
    context = "\n\n".join(blocks)

    user = (
        f"Lecture deck: {title}\n"
        f"Difficulty: {difficulty}\n\n"
        f"SLIDES:\n{context}\n\n"
        f"Write exactly {count} multiple-choice questions grounded in this material. "
        "Each question must require calculation or multi-step application of the slide content. "
        "For mathematical topics, use actual numbers, never abstract-only questions. "
        "Return a JSON array where each item is "
        '{"question": "...", "choices": ["A) ...", "B) ...", "C) ...", "D) ..."], '
        '"correct": 0, "explanation": "why this is correct and where the common mistake is"}. '
        '"correct" is the zero-based index into "choices". '
        "Keep each explanation under 80 words."
    )

    payload = {
        "model": os.getenv("OPENAI_PRACTICE_MODEL", "cc/claude-sonnet-4-6"),
        "messages": [
            {"role": "system", "content": PRACTICE_SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
        "max_tokens": min(4000, 300 + count * 280),
    }

    base = os.getenv("OPENAI_BASE_URL", "").rstrip("/")
    request = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {os.getenv('OPENAI_API_KEY')}",
        },
        method="POST",
    )
    timeout_seconds = _env_float("OPENAI_PRACTICE_TIMEOUT_SECONDS", 30)
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = json.loads(response.read().decode("utf-8"))
        text = (body["choices"][0]["message"]["content"] or "").strip()
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError, IndexError, TypeError):
        return None

    # Same wire shape as the Gemini generator, so reuse its parser/validator.
    from services.gemini_ai import _parse_question_json
    questions = _parse_question_json(text)
    return questions[:count] or None
