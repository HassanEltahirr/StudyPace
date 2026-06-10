from __future__ import annotations

import json
import math
import os
import re
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:
    pass


# flash-lite: full flash takes 60s+ to write a 5-question set (past every
# sensible request timeout) and has lower free-tier limits; lite does it in ~7s.
# 2.5-pro is not an option here: it rejects thinkingBudget=0 (HTTP 400) and
# can't finish a question set inside the request timeout anyway.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"

SUMMARY_SYSTEM_PROMPT = (
    "You are summarizing a university lecture slide deck for a student who needs to study efficiently. "
    "Output 5-7 bullet points of the core concepts, written as knowledge statements not descriptions. "
    "If mathematical content is present, add a Key Formula block. "
    "No filler phrases like 'this slide covers' or 'in this section'. "
    "Max 150 words. Return plain text."
)

PRACTICE_SYSTEM_PROMPT = (
    "You are an exam question generator for Gulf university engineering and CS students. "
    "Write questions at the level of a KU or AUS final exam. "
    "Questions must require calculation or application — never definition recall. "
    "Distractors must reflect the most common student errors for this topic. "
    "Phrasing should match formal Gulf university exam style. "
    "Return only valid JSON, no markdown, no preamble."
)

TOPIC_CLEAN_SYSTEM_PROMPT = (
    "You extract the academic topic from messy lecture slide titles. "
    "Given a possibly corrupted title, reply with only the clean academic topic in 2-6 words, "
    "no punctuation, no explanation. If no academic topic can be recovered, reply with NONE."
)


def _env_float(name: str, fallback: float) -> float:
    try:
        return float(os.getenv(name, fallback))
    except (TypeError, ValueError):
        return fallback


def gemini_available() -> bool:
    return bool(os.getenv("GEMINI_API_KEY"))


def _generate(system_prompt: str, user_prompt: str, timeout_seconds: float, json_mode: bool = False) -> str | None:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None

    generation_config: dict = {"temperature": 0.4, "maxOutputTokens": 16384}
    if GEMINI_MODEL.startswith("gemini-2.5"):
        # 2.5 models think by default and the thoughts consume maxOutputTokens;
        # summaries and MCQs don't need it, so spend the whole budget on output.
        generation_config["thinkingConfig"] = {"thinkingBudget": 0}
    payload: dict = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": generation_config,
    }
    if json_mode:
        payload["generationConfig"]["responseMimeType"] = "application/json"

    request = urllib.request.Request(
        GEMINI_ENDPOINT.format(model=GEMINI_MODEL, key=api_key),
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None

    try:
        parts = body["candidates"][0]["content"]["parts"]
        text = "".join(part.get("text", "") for part in parts).strip()
        return text or None
    except (KeyError, IndexError, TypeError):
        return None


def generate_deck_summary(title: str, slide_texts: list[tuple[int, str, str]]) -> str | None:
    """Generate the concept-first study summary shown before a deck is studied.

    slide_texts: list of (slide_number, title, text) tuples.
    Returns None if the API key is absent or the call fails.
    """
    if not slide_texts:
        return None

    blocks: list[str] = []
    for num, slide_title, text in slide_texts[:70]:
        header = f"Slide {num}: {slide_title}" if slide_title else f"Slide {num}"
        blocks.append(f"{header}\n{text.strip()[:600]}")
    context = "\n\n".join(blocks)

    user = (
        f"Lecture deck: {title}\n\n"
        f"SLIDES:\n{context}\n\n"
        "Write the study summary now."
    )
    return _generate(SUMMARY_SYSTEM_PROMPT, user, _env_float("GEMINI_SUMMARY_TIMEOUT_SECONDS", 10))


def generate_practice_questions(
    title: str,
    slide_texts: list[tuple[int, str, str]],
    difficulty: str = "medium",
    count: int = 5,
) -> list[dict] | None:
    """Generate Gulf-exam-style MCQs on demand. Returns None when unavailable.

    Larger sets are split across parallel calls (each over its own slice of the
    deck) so wall time stays close to a single small call.
    """
    if not slide_texts:
        return None

    slides = slide_texts[:50]
    chunks = _practice_chunks(slides, count)
    if len(chunks) == 1:
        chunk_slides, chunk_count = chunks[0]
        questions = _practice_call(title, chunk_slides, difficulty, chunk_count)
        return questions or None

    questions: list[dict] = []
    with ThreadPoolExecutor(max_workers=len(chunks)) as executor:
        futures = [
            executor.submit(_practice_call, title, chunk_slides, difficulty, chunk_count)
            for chunk_slides, chunk_count in chunks
        ]
        for future in futures:
            questions.extend(future.result() or [])

    seen: set[str] = set()
    unique: list[dict] = []
    for question in questions:
        key = re.sub(r"[^a-z0-9]+", " ", question["question"].lower()).strip()
        if key in seen:
            continue
        seen.add(key)
        unique.append(question)
    return unique[:count] or None


def _practice_chunks(
    slides: list[tuple[int, str, str]],
    count: int,
) -> list[tuple[list[tuple[int, str, str]], int]]:
    """Split the deck and question count into up to 3 parallel work units."""
    workers = min(3, max(1, math.ceil(count / 4)), len(slides))
    if workers == 1:
        return [(slides, count)]

    per_chunk = [count // workers] * workers
    for index in range(count % workers):
        per_chunk[index] += 1
    slice_size = math.ceil(len(slides) / workers)
    return [
        (slides[index * slice_size:(index + 1) * slice_size] or slides, per_chunk[index])
        for index in range(workers)
    ]


def _practice_call(
    title: str,
    slide_texts: list[tuple[int, str, str]],
    difficulty: str,
    count: int,
) -> list[dict]:
    blocks: list[str] = []
    for num, slide_title, text in slide_texts:
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
        "Keep each explanation under 80 words: state the key step and the common mistake, "
        "never a full line-by-line trace."
    )
    raw = _generate(PRACTICE_SYSTEM_PROMPT, user, _env_float("GEMINI_PRACTICE_TIMEOUT_SECONDS", 18), json_mode=True)
    if not raw:
        return []
    return _parse_question_json(raw)


def clean_academic_topic(title: str) -> str | None:
    """Recover a clean academic topic from a corrupted slide title via Gemini."""
    cleaned = _generate(
        TOPIC_CLEAN_SYSTEM_PROMPT,
        f"Title: {title.strip()[:200]}",
        _env_float("GEMINI_TOPIC_TIMEOUT_SECONDS", 15),
    )
    if not cleaned:
        return None
    cleaned = re.sub(r"[^\w\s-]", "", cleaned).strip()
    if not cleaned or cleaned.upper() == "NONE" or len(cleaned) < 3:
        return None
    return cleaned


def _salvage_json_objects(text: str) -> list[dict]:
    decoder = json.JSONDecoder()
    items: list[dict] = []
    index = text.find("{")
    while index != -1:
        try:
            obj, end = decoder.raw_decode(text, index)
        except ValueError:
            index = text.find("{", index + 1)
            continue
        if isinstance(obj, dict):
            items.append(obj)
        index = text.find("{", end)
    return items


def _parse_question_json(raw: str) -> list[dict]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
    except ValueError:
        # A long response can be cut off mid-array; salvage every complete
        # question object instead of discarding the whole set.
        parsed = _salvage_json_objects(text)
        if not parsed:
            return []

    if isinstance(parsed, dict):
        parsed = [parsed]
    if not isinstance(parsed, list):
        return []

    questions: list[dict] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question") or "").strip()
        choices = item.get("choices")
        explanation = str(item.get("explanation") or "").strip()
        try:
            correct = int(item.get("correct"))
        except (TypeError, ValueError):
            continue
        if not question or not isinstance(choices, list) or len(choices) != 4:
            continue
        if not 0 <= correct <= 3:
            continue
        questions.append({
            "question": question,
            "choices": [str(choice).strip() for choice in choices],
            "correct": correct,
            "explanation": explanation,
        })
    return questions
