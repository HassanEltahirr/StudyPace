from __future__ import annotations

import json
import math
import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:
    pass


# claude-3-7-sonnet was retired Feb 2026 (returns 404); sonnet-4-6 is the
# current model that still fits inside this app's synchronous timeouts.
DEFAULT_CLAUDE_MODEL = "claude-sonnet-4-6"


def _env_float(name: str, fallback: float) -> float:
    try:
        return float(os.getenv(name, fallback))
    except (TypeError, ValueError):
        return fallback


def _client(timeout_seconds: float):
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        import anthropic
        return anthropic.Anthropic(api_key=api_key, timeout=timeout_seconds, max_retries=0)
    except Exception:
        return None


def _model(env_name: str) -> str:
    return os.getenv(env_name) or os.getenv("CLAUDE_MODEL") or DEFAULT_CLAUDE_MODEL


def generate_lecture_summary(slide_texts: list[tuple[int, str, str]]) -> str | None:
    """Generate a comprehensive plain-text summary from slide content.

    slide_texts: list of (slide_number, title, text) tuples.
    Returns None if the API key is absent or the call fails.
    """
    timeout_seconds = _env_float("CLAUDE_SUMMARY_TIMEOUT_SECONDS", 40)
    client = _client(timeout_seconds)
    if not client or not slide_texts:
        return None

    # Keep broad coverage while bounding the prompt for long lecture decks.
    blocks: list[str] = []
    for num, title, text in slide_texts[:70]:
        header = f"Slide {num}: {title}" if title else f"Slide {num}"
        blocks.append(f"{header}\n{text.strip()[:700]}")
    context = "\n\n".join(blocks)

    system = (
        "You are a careful university tutor generating a study summary from lecture slides. "
        "Use only information present in the provided slides. "
        "Never invent definitions, formulas, or examples not shown in the slides. "
        "If a concept is only partially covered, say what the slides do and do not provide. "
        "Write in plain text, not Markdown."
    )

    user = (
        "Read these lecture slides carefully. Then produce a comprehensive plain-text summary organized by topic.\n\n"
        "Cover every meaningful concept, algorithm, definition, example, and process that appears in the slides. "
        "Explain the intuition behind each idea, not just the name. When the lecture includes an algorithm or worked process, "
        "explain what problem it solves, the steps, why the steps work, and any condition or limitation stated in the slides. "
        "When the slides include only a definition or category list, explain what that category means in student-friendly prose.\n\n"
        "Use flowing prose paragraphs. Do not use bullet points, bold text, Markdown headings, hash symbols, decorative formatting, "
        "or checklist formatting anywhere. Use plain section titles only, like 'Summary: Lecture Topic' and 'Part 1 - Topic Name'. "
        "Mention slide numbers naturally in parentheses, like (Slide 4), when useful for grounding. "
        "Do not include practice questions in this response; practice questions are generated separately.\n\n"
        "Before writing, work out the logical flow of the lecture: which concepts build on which, "
        "and what the central insight of each section is. Organize the summary around that flow so a student "
        "reading it understands why each idea matters, not just what it says.\n\n"
        "---\n"
        f"LECTURE SLIDES:\n{context}\n\n"
        "Write the plain-text study summary now."
    )

    try:
        response = client.messages.create(
            model=_model("CLAUDE_SUMMARY_MODEL"),
            max_tokens=1800,
            system=system,
            messages=[{"role": "user", "content": user}],
            timeout=timeout_seconds,
        )
        text = next((b.text for b in response.content if b.type == "text"), "")
        return _plain_study_text(text) or None
    except Exception:
        return None


def generate_lesson_questions(slide_texts: list[tuple[int, str, str]], concepts: list[str] | None = None) -> list[dict] | None:
    """Generate exam-style MCQs from slide content.

    Returns normalized dictionaries, or None if Claude is unavailable, slow, or
    returns invalid JSON. The caller keeps the local generator as the fallback.
    """
    timeout_seconds = _env_float("CLAUDE_QUESTION_TIMEOUT_SECONDS", 25)
    client = _client(timeout_seconds)
    if not client or not slide_texts:
        return None

    blocks: list[str] = []
    for num, title, text in slide_texts[:40]:
        header = f"Slide {num}: {title}" if title else f"Slide {num}"
        blocks.append(f"{header}\n{text.strip()[:750]}")
    context = "\n\n".join(blocks)
    concept_hint = ", ".join((concepts or [])[:12])

    system = (
        "You generate study questions from lecture slides. "
        "Use only facts, examples, formulas, algorithms, and vocabulary present in the slides. "
        "Never invent outside course content. Return valid JSON only."
    )

    user = (
        "Create 10 to 12 exam-style multiple-choice questions from these lecture slides.\n"
        "Prefer questions that test understanding, application, analysis, and common mistakes, not trivia.\n"
        "Every question must cite a real source slide_number from the provided slides.\n"
        "Each question needs exactly four options: a, b, c, d. Exactly one option is correct.\n"
        "Keep options concise but specific. Distractors should be plausible mistakes based on the slides.\n"
        "For each question, include:\n"
        "- slide_number: integer\n"
        "- prompt: string\n"
        "- options: object with keys a, b, c, d\n"
        "- correct: one of a, b, c, d\n"
        "- explanation: why the correct option is right, citing the slide\n"
        "- wrong_explanations: object with keys for the incorrect options\n"
        "- difficulty: easy, medium, or hard\n"
        "- bloom: Remembering, Understanding, Application, Analysis, or Evaluation\n"
        "- concept: short concept label\n"
        "- objective: short exam skill being tested\n\n"
        "Quality bar for every question: it must test application or analysis of the concept, not recall of a "
        "phrase from the slide; each distractor must be a mistake a real student would plausibly make (wrong "
        "step order, confused definitions, off-by-one, swapped conditions); and the explanation must be "
        "verifiable against the cited slide. Discard and replace any question that fails this bar.\n"
        'Return JSON in this exact shape: {"questions":[...]}\n\n'
        f"Important concepts to cover if supported by the slides: {concept_hint or 'choose from the slides'}\n\n"
        f"LECTURE SLIDES:\n{context}"
    )

    try:
        response = client.messages.create(
            model=_model("CLAUDE_QUESTION_MODEL"),
            max_tokens=3000,
            system=system,
            messages=[{"role": "user", "content": user}],
            timeout=timeout_seconds,
        )
        text = next((b.text for b in response.content if b.type == "text"), "")
        payload = _loads_json_object(text)
        questions = payload.get("questions")
        return questions if isinstance(questions, list) and questions else None
    except Exception:
        return None


def generate_lesson_practice_set(slide_texts: list[tuple[int, str, str]], concepts: list[str] | None = None) -> dict | None:
    """Generate written practice and MCQs from slide content with Claude.

    Returns a raw JSON dict with ``written`` and ``mcq`` arrays. The caller
    validates and normalizes it into LessonQuestion rows.
    """
    timeout_seconds = _env_float("CLAUDE_PRACTICE_TIMEOUT_SECONDS", 35)
    client = _client(timeout_seconds)
    if not client or not slide_texts:
        return None

    blocks: list[str] = []
    for num, title, text in slide_texts[:45]:
        header = f"Slide {num}: {title}" if title else f"Slide {num}"
        blocks.append(f"{header}\n{text.strip()[:900]}")
    context = "\n\n".join(blocks)
    concept_hint = ", ".join((concepts or [])[:14])

    system = (
        "You are an expert university tutor creating useful practice from lecture slides. "
        "Use only the supplied slides. Do not invent facts, formulas, examples, values, or terminology. "
        "Use retrieval practice, spaced repetition, and interleaving principles. Return valid JSON only."
    )

    user = (
        "Create a high-quality practice set for this lecture using the same pattern as a strong human tutor.\n\n"
        "Make the questions feel like a student can actually solve, explain, trace, compare, or predict something, not like copied slide fragments.\n"
        "Avoid prompts made from category-definition fragments such as 'Design designing systems...'. "
        "If a slide is only a list of categories, ask the student to classify, compare, or apply the categories in a small scenario.\n\n"
        "Return:\n"
        "- 6 written_response questions.\n"
        "- 8 multiple-choice questions for quick exam checks.\n\n"
        "Across the full set, include these practice kinds exactly: conceptual, trace_apply, compare_contrast, predict_extend, and common_exam_trap. "
        "Use conceptual questions for why/what understanding. Use trace_apply questions to run an algorithm, rule, inference, or process by hand on a small example when the slides support it. "
        "Use compare_contrast questions between algorithms, structures, methods, categories, or cases. Use predict_extend questions for what changes when a condition changes. "
        "Use common_exam_trap for at least one item that targets a likely student misconception.\n\n"
        "Every item must cite a real slide_number from the slides. Cover the lecture broadly, not only the first slides. "
        "Do not use bullet points, bold text, Markdown, decorative formatting, or headers inside prompt, model_answer, explanation, rubric, or common_errors. "
        "Question prompts should be plain prose. Numbering is not needed because the app numbers them.\n\n"
        "Written response item fields:\n"
        "- slide_number: integer\n"
        "- practice_kind: one of conceptual, trace_apply, compare_contrast, predict_extend, common_exam_trap\n"
        "- prompt: actionable student task, 1-2 sentences\n"
        "- model_answer: clear answer guide, 3-7 plain prose sentences, cite slide number naturally\n"
        "- rubric: 3 short marking points separated by semicolons\n"
        "- common_errors: one common mistake students make\n"
        "- difficulty: easy, medium, or hard\n"
        "- bloom: Understanding, Application, Analysis, or Evaluation\n"
        "- concept: short concept label\n"
        "- objective: short exam skill being tested\n\n"
        "MCQ item fields:\n"
        "- slide_number: integer\n"
        "- practice_kind: one of conceptual, trace_apply, compare_contrast, predict_extend, common_exam_trap\n"
        "- prompt: string\n"
        "- options: object with keys a, b, c, d\n"
        "- correct: one of a, b, c, d\n"
        "- explanation: why the correct option is right, citing the slide in plain prose\n"
        "- wrong_explanations: object with keys for the incorrect options\n"
        "- difficulty: easy, medium, or hard\n"
        "- bloom: Remembering, Understanding, Application, Analysis, or Evaluation\n"
        "- concept: short concept label\n"
        "- objective: short exam skill being tested\n\n"
        "Quality bar for every item: it must require the student to apply, trace, compare, or predict — never "
        "to repeat a slide fragment; MCQ distractors must encode real exam traps for this topic; and model "
        "answers must be checkable against the cited slide. Discard and replace any item that fails this bar.\n"
        'Return JSON in this exact shape: {"written":[...],"mcq":[...]}\n\n'
        f"Important concepts to cover if supported by the slides: {concept_hint or 'choose from the slides'}\n\n"
        f"LECTURE SLIDES:\n{context}"
    )

    try:
        response = client.messages.create(
            model=_model("CLAUDE_QUESTION_MODEL"),
            max_tokens=5200,
            system=system,
            messages=[{"role": "user", "content": user}],
            timeout=timeout_seconds,
        )
        text = next((b.text for b in response.content if b.type == "text"), "")
        payload = _loads_json_object(text)
        if not isinstance(payload.get("written"), list) and not isinstance(payload.get("mcq"), list):
            return None
        return payload
    except Exception:
        return None


PRACTICE_MCQ_SYSTEM = (
    "You are an exam question writer for Gulf university engineering and CS finals (KU/AUS level). "
    "Use only facts, formulas, algorithms, and examples present in the provided slides. "
    "Every question must require calculation, tracing, or application — never definition recall. "
    "Each distractor must encode a specific, common student error for this topic (wrong step order, "
    "swapped condition, off-by-one, confused definitions), not a random wrong value. "
    "Return only a valid JSON array, no markdown, no preamble."
)


def generate_practice_mcqs(
    title: str,
    slide_texts: list[tuple[int, str, str]],
    difficulty: str = "medium",
    count: int = 5,
) -> list[dict] | None:
    """Generate exam-style MCQs in the normalized practice shape
    ({question, choices, correct, explanation}) used by the practice-exam endpoint.

    Larger sets are split across up to 3 parallel calls over slices of the deck
    so wall time stays close to a single small call.
    """
    if not slide_texts or not os.getenv("ANTHROPIC_API_KEY"):
        return None

    slides = slide_texts[:50]
    workers = min(3, max(1, math.ceil(count / 5)), len(slides))
    if workers == 1:
        return _practice_mcq_call(title, slides, difficulty, count) or None

    per_chunk = [count // workers] * workers
    for index in range(count % workers):
        per_chunk[index] += 1
    slice_size = math.ceil(len(slides) / workers)
    chunks = [
        (slides[index * slice_size:(index + 1) * slice_size] or slides, per_chunk[index])
        for index in range(workers)
    ]

    questions: list[dict] = []
    with ThreadPoolExecutor(max_workers=len(chunks)) as executor:
        futures = [
            executor.submit(_practice_mcq_call, title, chunk_slides, difficulty, chunk_count)
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


def _practice_mcq_call(
    title: str,
    slide_texts: list[tuple[int, str, str]],
    difficulty: str,
    count: int,
) -> list[dict]:
    timeout_seconds = _env_float("CLAUDE_PRACTICE_MCQ_TIMEOUT_SECONDS", 16)
    client = _client(timeout_seconds)
    if not client:
        return []

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

    try:
        response = client.messages.create(
            model=_model("CLAUDE_QUESTION_MODEL"),
            max_tokens=min(4000, 300 + count * 280),
            system=PRACTICE_MCQ_SYSTEM,
            messages=[{"role": "user", "content": user}],
            timeout=timeout_seconds,
        )
        text = next((b.text for b in response.content if b.type == "text"), "")
    except Exception:
        return []
    # Same wire shape as the Gemini generator, so reuse its parser/validator.
    from services.gemini_ai import _parse_question_json
    return _parse_question_json(text)


def generate_model_answer(prompt: str, slide_text: str, slide_number: int, kind: str) -> str | None:
    """Generate a worked model answer for a practice question.

    Returns None if the API key is absent or the call fails,
    so the caller can fall back to its static guidance string.
    """
    timeout_seconds = _env_float("CLAUDE_ANSWER_TIMEOUT_SECONDS", 7)
    client = _client(timeout_seconds)
    if not client:
        return None

    kind_instruction = {
        "numerical": (
            "Work through the calculation step by step using only the numbers, "
            "formulas, and values on this slide. Show each step clearly."
        ),
        "problem": (
            "Work through this proof or construction step by step using only "
            "the definitions, theorems, and reasoning shown on this slide. "
            "State each step and justify it."
        ),
        "question": (
            "Answer this question directly and concisely using only the "
            "information on this slide."
        ),
    }.get(kind, "Answer using only the information on this slide.")

    user = (
        f"A student is practising with this question taken from Slide {slide_number}:\n\n"
        f"QUESTION: {prompt}\n\n"
        f"SLIDE {slide_number} CONTENT:\n{slide_text.strip()[:1400]}\n\n"
        f"{kind_instruction}\n\n"
        "If the slide does not contain enough information to give a complete answer, "
        "state exactly what is missing rather than guessing. "
        "Write the model answer now (plain prose, no headers, 2–8 sentences):"
    )

    try:
        response = client.messages.create(
            model=_model("CLAUDE_ANSWER_MODEL"),
            max_tokens=500,
            messages=[{"role": "user", "content": user}],
            timeout=timeout_seconds,
        )
        text = next((b.text for b in response.content if b.type == "text"), "")
        return _plain_study_text(text) or None
    except Exception:
        return None


def _loads_json_object(text: str) -> dict:
    cleaned = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", cleaned, flags=re.S | re.I)
    if fenced:
        cleaned = fenced.group(1).strip()
    if not cleaned.startswith("{"):
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            cleaned = cleaned[start:end + 1]
    data = json.loads(cleaned)
    return data if isinstance(data, dict) else {}


def _plain_study_text(text: str | None) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        return ""
    cleaned = re.sub(r"^#{1,6}\s*", "", cleaned, flags=re.M)
    cleaned = re.sub(r"\*\*(.*?)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"^\s*[-*]\s+", "", cleaned, flags=re.M)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()
