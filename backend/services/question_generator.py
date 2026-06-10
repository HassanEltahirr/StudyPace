from __future__ import annotations

import re
from dataclasses import dataclass

from services.claude_ai import (
    generate_lecture_summary,
    generate_lesson_practice_set,
    generate_lesson_questions,
    generate_model_answer,
)

from services.slide_parser import ExtractedSlide


@dataclass
class GeneratedLesson:
    title: str
    summary: str
    key_concepts: list[str]
    learning_objectives: list[str]
    questions: list[dict]
    flashcards: list[dict]
    estimated_minutes: int


def build_lesson(slides: list[ExtractedSlide], fallback_title: str) -> GeneratedLesson:
    concepts = _key_concepts(slides)
    objectives = _objectives(slides, concepts)
    questions = _questions(slides, concepts)
    flashcards = _flashcards(slides, concepts)
    title = _lesson_title(slides, fallback_title)
    minutes = min(90, max(20, 12 + len(slides) * 7 + len(questions) * 2))

    return GeneratedLesson(
        title=title,
        summary=_summary(slides),
        key_concepts=concepts[:10],
        learning_objectives=objectives[:6],
        questions=questions,
        flashcards=flashcards,
        estimated_minutes=minutes,
    )


def _summary(slides: list[ExtractedSlide]) -> str:
    if not slides:
        return "This lesson was created from the uploaded slides."

    # Fallback: plain-prose summary from the strongest extracted slide text.
    topics = []
    seen_topics: set[str] = set()
    for slide in slides:
        topic = _clean_concept(_slide_topic(slide))
        lower = topic.lower()
        if (
            topic
            and lower not in seen_topics
            and not lower.startswith("slide ")
            and not re.match(r"^example\s+\d+$", lower)
            and not _is_boilerplate(topic)
            and not _is_fragment(topic)
            and 2 <= len(topic.split()) <= 8
        ):
            seen_topics.add(lower)
            topics.append(topic)

    key_sentences: list[str] = []
    seen_sentences: set[str] = set()
    for slide in slides:
        for sentence in [_definition_line(slide.text), _best_sentence(slide.text), *_important_lines(slide.text)]:
            sentence = _clean_sentence(sentence)
            normalized = _normalize_question(sentence)
            if (
                sentence
                and normalized not in seen_sentences
                and not _is_question_like(sentence)
                and not _looks_like_code(sentence)
                and not _is_fragment(sentence)
                and _is_summary_statement(sentence)
            ):
                seen_sentences.add(normalized)
                key_sentences.append(f"Slide {slide.slide_number} says that {_summary_clause(sentence)}")

    lesson_title = _lesson_title(slides, "This lecture")
    parts = [f"Summary: {lesson_title}"]
    if topics:
        topic_list = ", ".join(topics[:10])
        parts.append(
            f"Part 1 - Main topics\nThis lecture introduces and develops {topic_list}. "
            "A student should study these as connected ideas rather than isolated slide titles, because the exam usually tests whether the ideas can be explained, compared, or applied."
        )
    if key_sentences:
        parts.append(
            "Part 2 - What the slides emphasize\n"
            + " ".join(key_sentences[:8])
        )
    parts.append(
        "Part 3 - How to study it\n"
        "Use retrieval practice by explaining each definition in your own words, then apply the lecture idea to a small example. "
        "When a slide gives a process, rule, formula, or example, trace it step by step and state any missing assumption instead of guessing. "
        "For comparison-heavy slides, practice saying why two methods or categories differ instead of only memorizing their names."
    )

    return "\n\n".join(parts) if len(parts) > 1 else "This lesson was created from the uploaded slides."


def _lesson_title(slides: list[ExtractedSlide], fallback_title: str) -> str:
    if not slides:
        return fallback_title

    lines = [_clean_sentence(line) for line in slides[0].text.splitlines() if _clean_sentence(line)]
    generic = {"automata, computability,", "and complexity", "automata, computability, and complexity"}
    usable_lines = [
        line for line in lines[:8]
        if not line.isdigit()
        and line.lower() not in generic
        and not line.endswith(",")
        and not _is_boilerplate(line)
        and not _is_fragment(line)
    ]
    if len(usable_lines) >= 3 and all(1 <= len(line.split()) <= 4 for line in usable_lines[:3]):
        return _clean_concept(" ".join(usable_lines[:3]))
    if len(usable_lines) >= 2 and usable_lines[0].lower() in {"artificial intelligence", "data structures", "algorithms"}:
        return _clean_concept(f"{usable_lines[0]}: {usable_lines[1]}")

    for index, line in enumerate(lines[:8]):
        lower = line.lower()
        if lower in generic or line.endswith(",") or _is_boilerplate(line) or _is_fragment(line) or line.isdigit():
            continue
        if lower.startswith("chapter") and index + 1 < len(lines):
            next_line = _clean_sentence(lines[index + 1])
            if next_line and next_line.lower() not in generic and not next_line.lower().startswith("chapter"):
                return _clean_concept(f"{line}: {next_line}")
        return _clean_concept(line)

    title = _clean_concept(slides[0].title)
    if title and title.lower() not in generic and not title.endswith(","):
        return title
    return fallback_title


def _key_concepts(slides: list[ExtractedSlide]) -> list[str]:
    seen: set[str] = set()
    concepts: list[str] = []
    for slide in slides:
        candidates = [_slide_topic(slide)]
        candidates.extend(_important_lines(slide.text))
        for candidate in candidates:
            if _is_boilerplate(candidate) or _is_question_like(candidate):
                continue
            concept = _clean_concept(candidate)
            if _is_fragment(concept):
                continue
            # Skip artifacts that slip through boilerplate check
            lower = concept.lower()
            if lower.startswith("credit:") or lower.startswith("last lecture"):
                continue
            if concept.startswith("❑") or concept.startswith("◼"):
                continue
            key = lower
            if concept and key not in seen:
                seen.add(key)
                concepts.append(concept)
    return concepts


def _objectives(slides: list[ExtractedSlide], concepts: list[str]) -> list[str]:
    explicit: list[str] = []
    for slide in slides:
        for line in _important_lines(slide.text):
            if any(word in line.lower() for word in ["objective", "outcome", "learn", "understand"]):
                cleaned = _clean_sentence(line)
                if not _is_boilerplate(cleaned) and len(cleaned.split()) >= 5:
                    explicit.append(cleaned)
    if explicit:
        return explicit
    # Only generate fallback objectives for clean, meaningful concepts
    clean_concepts = [c for c in concepts[:4] if not _is_boilerplate(c) and len(c.split()) >= 2]
    return [f"Explain {concept} using evidence from the lecture slides." for concept in clean_concepts]


def _questions(slides: list[ExtractedSlide], concepts: list[str]) -> list[dict]:
    extracted = _extracted_questions(slides)
    generated = _generated_mcqs(slides, concepts)
    return _dedupe_generated_questions([*extracted, *generated])


def _coverage_questions(slides: list[ExtractedSlide], concepts: list[str]) -> list[dict]:
    questions: list[dict] = []
    seen: set[str] = set()
    slide_counts: dict[int, int] = {}
    slide_lookup = _concept_slide_lookup(slides)

    for concept in concepts:
        if _is_question_like(concept) or _is_fragment(concept):
            continue
        slide = slide_lookup.get(concept.lower())
        if not slide:
            continue
        if not _is_question_worthy_slide(slide) and "main ai techniques" not in concept.lower():
            continue
        if slide_counts.get(slide.slide_number, 0) >= 2:
            continue
        context = _best_sentence(slide.text) or _definition_line(slide.text) or concept
        is_problem = _has_problem_solving_content(slide.text)
        prompt = _coverage_prompt(slide, concept, is_problem)
        normalized = _normalize_question(prompt)
        if normalized in seen:
            continue
        seen.add(normalized)

        question_type = "coverage_problem" if is_problem else "coverage_question"
        questions.append({
            "question_type": question_type,
            "slide_number": slide.slide_number,
            "prompt": prompt,
            "options": {
                "a": "Answer from the cited slide and your worked reasoning.",
                "b": "Skip the concept because it was not asked directly.",
                "c": "Use unrelated course material first.",
                "d": "Memorize the term without applying it.",
            },
            "correct": "a",
            "explanation": (
                f"Coverage check for slide {slide.slide_number}: {context} "
                "A strong answer should stay grounded in this slide and show the reasoning, not just a keyword."
            ),
            "wrong_explanations": {
                "b": "The goal is full concept coverage, including concepts that may appear as applied questions.",
                "c": "Start from the cited slide before bringing in anything else.",
                "d": "Retention is stronger when you explain or solve with the concept, not only recognize it.",
            },
            "difficulty": "coverage-problem" if is_problem else "coverage-question",
            "topic_tag": concept,
        })
        slide_counts[slide.slide_number] = slide_counts.get(slide.slide_number, 0) + 1

    return questions[:10]


def _coverage_prompt(slide: ExtractedSlide, concept: str, is_problem: bool) -> str:
    slide_number = slide.slide_number
    topic = _practice_topic(concept, slide)
    lower = topic.lower()

    if "motivation" in lower:
        return (
            f"Explain the limitation that motivates this topic on slide {slide_number}, "
            "and state what new structure or idea the lecture introduces to fix it."
        )
    if "typical problem" in lower:
        return (
            f"Using slide {slide_number}, choose three typical expert-system problem types and explain, "
            "for each one, what the system receives as input and what decision or output it should produce."
        )
    if "knowledge representation" in lower:
        return (
            f"From slide {slide_number}, explain why expert systems need both knowledge representation "
            "and inference rules."
        )
    if "expert system" in lower and "outline" not in lower:
        return (
            f"Using slide {slide_number}, define an expert system and explain why its answer must stay "
            "inside a specific domain."
        )
    if "modus ponens" in lower:
        return (
            f"Use the facts and implication on slide {slide_number} to apply modus ponens. "
            "State the known fact, the rule, and the conclusion."
        )
    if "socrates" in lower:
        return f"Use the Socrates example on slide {slide_number} to explain how modus ponens reaches its conclusion."
    if "inference" in lower:
        return (
            f"Explain what inference means in an expert system, then describe the role of the inference engine "
            f"using slide {slide_number}."
        )
    if "main ai techniques" in lower:
        return f"List the main AI techniques shown on slide {slide_number}, then explain which technique this lecture develops next."
    if lower == "preliminary":
        return f"Identify the preliminary issue on slide {slide_number}, then explain why it matters for expert systems."
    if "application" in lower:
        return f"Name two applications from slide {slide_number}, then explain what makes the lecture concept useful for them."
    if lower.startswith("binary search trees") or lower == "bst":
        return f"Describe what a BST is, what ordering idea it uses, and why it is useful in this lecture."
    if _looks_like_code(slide.text):
        return (
            f"Trace the code or method shown on slide {slide_number}: what case does it handle, "
            f"what value does it return/change, and why is that step needed?"
        )
    if is_problem:
        if any(word in lower for word in ["big-oh", "complexity", "o("]):
            return f"Solve an exam-style time-complexity question for {topic}; justify the answer from slide {slide_number}."
        if any(word in lower for word in ["tree", "bst", "node", "leaf", "root", "ancestor", "descendant"]):
            return f"Use the tree idea on slide {slide_number} to answer a small worked problem about {topic}."
        return f"Work a short exam-style problem about {topic} using slide {slide_number} as the solution source."
    if re.search(r"\blevel\s*\d+\b", lower):
        return f"Identify what {topic} means in the tree on slide {slide_number}, and name the nodes at that level."
    if any(word in lower for word in ["definition", "node", "root", "leaf", "internal", "subtree"]):
        return f"Define {topic} without looking first, then give one example from the slide {slide_number} diagram."
    if any(word in lower for word in ["motivation", "application", "why"]):
        return f"Explain why {topic} matters, and connect it to the problem the lecture is trying to solve."
    return f"Explain {topic} in your own words, then give one small example or implication from slide {slide_number}."


def _practice_topic(concept: str, slide: ExtractedSlide) -> str:
    cleaned = _clean_concept(concept)
    if _looks_like_code(cleaned) or _is_fragment(cleaned) or len(cleaned.split()) > 10:
        cleaned = _slide_topic(slide)
    if _is_fragment(cleaned):
        cleaned = f"slide {slide.slide_number}"
    return _shorten_phrase(cleaned, 90)


def _concept_slide_lookup(slides: list[ExtractedSlide]) -> dict[str, ExtractedSlide]:
    lookup: dict[str, ExtractedSlide] = {}
    for slide in slides:
        candidates = [_slide_topic(slide), *[_clean_concept(line) for line in _important_lines(slide.text)]]
        for candidate in candidates:
            if candidate and not _is_fragment(candidate) and candidate.lower() not in lookup:
                lookup[candidate.lower()] = slide
    return lookup


def _generated_mcqs(slides: list[ExtractedSlide], concepts: list[str]) -> list[dict]:
    pool = _concept_pool(slides)
    questions: list[dict] = []
    for slide in slides:
        if not _is_question_worthy_slide(slide):
            continue

        topic = _slide_topic(slide)
        if _is_question_like(topic) or _is_fragment(topic):
            continue

        exam_question = _fact_mcq(slide, topic, pool) or _exam_mcq(slide, topic)
        if not exam_question:
            continue

        questions.append({
            "question_type": "generated_mcq",
            "slide_number": slide.slide_number,
            "prompt": exam_question["prompt"],
            "options": exam_question["options"],
            "correct": exam_question["correct"],
            "explanation": exam_question["explanation"],
            "wrong_explanations": exam_question["wrong_explanations"],
            "difficulty": exam_question["difficulty"],
            "topic_tag": _exam_topic_tag(exam_question),
        })

        if len(questions) >= 12:
            break
    return questions


def _claude_generated_mcqs(raw_questions: list[dict] | None, slides: list[ExtractedSlide]) -> list[dict]:
    if not raw_questions:
        return []

    slide_numbers = {slide.slide_number for slide in slides}
    questions: list[dict] = []
    seen_prompts: set[str] = set()

    for raw in raw_questions:
        if not isinstance(raw, dict):
            continue

        prompt = _clean_sentence(str(raw.get("prompt", "")))
        normalized = _normalize_question(prompt)
        if not prompt or normalized in seen_prompts:
            continue

        try:
            slide_number = int(raw.get("slide_number") or 1)
        except (TypeError, ValueError):
            slide_number = 1
        if slide_number not in slide_numbers:
            slide_number = min(slide_numbers) if slide_numbers else 1

        options = raw.get("options") if isinstance(raw.get("options"), dict) else {}
        options = {key: _clean_sentence(str(options.get(key, ""))) for key in ("a", "b", "c", "d")}
        if any(not value for value in options.values()):
            continue

        correct = str(raw.get("correct", "")).lower().strip()
        if correct not in {"a", "b", "c", "d"}:
            continue

        wrong_raw = raw.get("wrong_explanations") if isinstance(raw.get("wrong_explanations"), dict) else {}
        wrong_explanations = {}
        for key in ("a", "b", "c", "d"):
            if key == correct:
                continue
            wrong_explanations[key] = _clean_sentence(str(wrong_raw.get(key, ""))) or (
                f"Slide {slide_number} supports a different answer."
            )

        difficulty = _clean_sentence(str(raw.get("difficulty", "medium"))).lower()
        if difficulty not in {"easy", "medium", "hard"}:
            difficulty = "medium"

        bloom = _clean_sentence(str(raw.get("bloom", "Application"))) or "Application"
        concept = _clean_sentence(str(raw.get("concept", ""))) or f"Slide {slide_number}"
        objective = _clean_sentence(str(raw.get("objective", ""))) or "Apply the lecture concept to an exam-style scenario."
        explanation = _clean_sentence(str(raw.get("explanation", ""))) or f"Slide {slide_number} supports the correct answer."
        practice_kind = _practice_kind_label(raw.get("practice_kind"))

        questions.append({
            "question_type": "generated_mcq",
            "slide_number": slide_number,
            "prompt": prompt,
            "options": options,
            "correct": correct,
            "explanation": explanation,
            "wrong_explanations": wrong_explanations,
            "difficulty": difficulty,
            "topic_tag": " | ".join([
                f"Difficulty: {difficulty.title()}",
                f"Type: {practice_kind}",
                f"Bloom: {bloom}",
                f"Concept: {_shorten_phrase(concept, 54)}",
                f"Objective: {_shorten_phrase(objective, 58)}",
                "Source: Claude",
            ]),
        })
        seen_prompts.add(normalized)

        if len(questions) >= 12:
            break

    return questions if len(questions) >= 4 else []


def _claude_practice_questions(raw_payload: dict | None, slides: list[ExtractedSlide]) -> list[dict]:
    if not raw_payload:
        return []

    written = _claude_written_questions(raw_payload.get("written"), slides)
    mcqs = _claude_generated_mcqs(raw_payload.get("mcq"), slides)
    questions = [*written, *mcqs]
    return questions if len(written) >= 2 and len(mcqs) >= 4 else []


def _claude_written_questions(raw_questions, slides: list[ExtractedSlide]) -> list[dict]:
    if not isinstance(raw_questions, list):
        return []

    slide_numbers = {slide.slide_number for slide in slides}
    questions: list[dict] = []
    seen_prompts: set[str] = set()

    for raw in raw_questions:
        if not isinstance(raw, dict):
            continue

        prompt = _clean_sentence(str(raw.get("prompt", "")))
        normalized = _normalize_question(prompt)
        if not prompt or normalized in seen_prompts:
            continue
        if _is_category_definition_line(prompt) or _is_fragment(prompt):
            continue

        try:
            slide_number = int(raw.get("slide_number") or 1)
        except (TypeError, ValueError):
            slide_number = 1
        if slide_number not in slide_numbers:
            slide_number = min(slide_numbers) if slide_numbers else 1

        difficulty = _clean_sentence(str(raw.get("difficulty", "medium"))).lower()
        if difficulty not in {"easy", "medium", "hard"}:
            difficulty = "medium"

        bloom = _clean_sentence(str(raw.get("bloom", "Application"))) or "Application"
        concept = _clean_sentence(str(raw.get("concept", ""))) or f"Slide {slide_number}"
        objective = _clean_sentence(str(raw.get("objective", ""))) or "Apply the lecture concept to an exam-style response."
        model_answer = _clean_model_answer(str(raw.get("model_answer", "")))
        if not model_answer:
            model_answer = f"Use slide {slide_number} as the source, explain the reasoning, and state any missing assumption instead of guessing."
        rubric = _clean_sentence(str(raw.get("rubric", "")))
        common_errors = _clean_sentence(str(raw.get("common_errors", "")))
        practice_kind = _practice_kind_label(raw.get("practice_kind"))

        questions.append({
            "question_type": "claude_written",
            "slide_number": slide_number,
            "prompt": prompt,
            "options": {
                "a": "Write a reasoned answer using the cited slide.",
                "b": "Guess from outside knowledge first.",
                "c": "Copy an unrelated slide.",
                "d": "Skip the reasoning and give only a keyword.",
            },
            "correct": "a",
            "explanation": model_answer,
            "wrong_explanations": {
                "rubric": rubric or "Identify the relevant concept; apply it to the prompt; justify with slide evidence.",
                "common_errors": common_errors or "Only naming the concept without explaining how it works.",
                "b": "Stay grounded in the uploaded lecture before using outside facts.",
                "c": "The cited slide is the intended source for this task.",
                "d": "A keyword is not enough for written exam credit.",
            },
            "difficulty": "claude-written",
            "topic_tag": " | ".join([
                f"Difficulty: {difficulty.title()}",
                f"Type: {practice_kind}",
                f"Bloom: {bloom}",
                f"Concept: {_shorten_phrase(concept, 54)}",
                f"Objective: {_shorten_phrase(objective, 58)}",
                "Source: Claude",
            ]),
        })
        seen_prompts.add(normalized)

        if len(questions) >= 6:
            break

    return questions if len(questions) >= 2 else []


def _dedupe_generated_questions(questions: list[dict]) -> list[dict]:
    deduped: list[dict] = []
    seen: set[str] = set()
    for question in questions:
        prompt = str(question.get("prompt", ""))
        normalized = _normalize_question(prompt)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(question)
    return deduped


def _practice_kind_label(value) -> str:
    raw = _clean_sentence(str(value or "")).lower().replace("-", "_").replace(" ", "_")
    labels = {
        "conceptual": "Conceptual",
        "concept": "Conceptual",
        "why_what": "Conceptual",
        "trace_apply": "Trace / apply",
        "trace": "Trace / apply",
        "apply": "Trace / apply",
        "application": "Trace / apply",
        "compare_contrast": "Compare / contrast",
        "compare": "Compare / contrast",
        "contrast": "Compare / contrast",
        "predict_extend": "Predict / extend",
        "predict": "Predict / extend",
        "extend": "Predict / extend",
        "common_exam_trap": "Common exam trap",
        "exam_trap": "Common exam trap",
        "trap": "Common exam trap",
    }
    return labels.get(raw, "Conceptual")


def _fact_mcq(slide: ExtractedSlide, topic: str, pool: list[dict]) -> dict | None:
    correct = _best_sentence(slide.text) or _definition_line(slide.text)
    correct = _clean_sentence(correct)
    if (
        not correct
        or _looks_like_code(correct)
        or _is_question_like(correct)
        or _is_fragment(correct)
        or not _is_study_statement(correct)
    ):
        return None

    distractors = _distractors(correct, slide.slide_number, pool)
    if len(distractors) < 3:
        return None

    prompt_topic = "" if topic.lower().startswith("slide ") else f" about {topic}"
    return _scenario_question(
        slide,
        topic,
        difficulty=_difficulty(slide.text),
        bloom="Understanding",
        concept=topic,
        objective="Recognize the key claim from the cited slide.",
        prompt=f"Which statement best matches slide {slide.slide_number}'s key point{prompt_topic}?",
        correct="a",
        options={
            "a": _shorten_phrase(correct, 190),
            "b": _shorten_phrase(distractors[0]["text"], 190),
            "c": _shorten_phrase(distractors[1]["text"], 190),
            "d": _shorten_phrase(distractors[2]["text"], 190),
        },
        wrong={
            "b": distractors[0]["why"],
            "c": distractors[1]["why"],
            "d": distractors[2]["why"],
        },
        explanation=f"Slide {slide.slide_number} supports option a: {correct}",
    )


def _exam_mcq(slide: ExtractedSlide, topic: str) -> dict | None:
    context = _best_sentence(slide.text) or _definition_line(slide.text)
    if not context or _looks_like_code(context) or _is_question_like(context):
        context = topic
    if not context or _is_fragment(context):
        return None

    lower_text = slide.text.lower()
    if _is_formula_or_complexity_slide(slide.text):
        return _scenario_question(
            slide,
            topic,
            difficulty="hard",
            bloom="Analysis",
            concept=topic,
            objective="Interpret the formula or bound in a new situation.",
            prompt=f"A midterm gives a new input size for {topic}. Which reasoning would earn the most credit?",
            correct="b",
            options={
                "a": "Pick the largest number shown in the lecture and use it as the answer.",
                "b": "Use the stated formula or bound to reason about growth before substituting values.",
                "c": "Ignore the bound and describe the topic in general terms only.",
                "d": "Assume the result is constant because the slide gives one example.",
            },
            wrong={
                "a": "Copying a number from a slide does not show how the formula behaves for a new case.",
                "c": "A general description misses the quantitative reasoning the question asks for.",
                "d": "One example does not make the process constant time or constant size.",
            },
            explanation=f"Slide {slide.slide_number} supports a formula/bound question about {topic}. A strong answer interprets the relationship, then applies it to the new values.",
        )

    if any(word in lower_text for word in ["case", "step", "algorithm", "insert", "delete", "rotation", "trace", "workflow"]):
        return _scenario_question(
            slide,
            topic,
            difficulty="medium",
            bloom="Application",
            concept=topic,
            objective="Apply the correct case or workflow step.",
            prompt=f"A professor gives a fresh worked example involving {topic}. What is the best way to start?",
            correct="a",
            options={
                "a": "Identify which case applies, perform that local step, and verify the required invariant afterward.",
                "b": "Memorize the slide order and repeat it without checking whether the case matches.",
                "c": "Jump to the final answer before deciding which rule or case applies.",
                "d": "Use a rule from a different topic because the keywords sound similar.",
            },
            wrong={
                "b": "Case-based questions are graded on choosing the matching condition, not reciting slide order.",
                "c": "Skipping the applicable rule is how students lose method marks on worked problems.",
                "d": "A plausible keyword match is not enough; the method must fit the actual case.",
            },
            explanation=f"Slide {slide.slide_number} is best tested as a case/application question. The exam skill is matching the scenario to the right step and checking the invariant.",
        )

    if any(word in lower_text for word in ["compare", "versus", " vs ", "advantage", "disadvantage", "tradeoff", "faster", "slower"]):
        return _scenario_question(
            slide,
            topic,
            difficulty="medium",
            bloom="Evaluation",
            concept=topic,
            objective="Evaluate a tradeoff for a given constraint.",
            prompt=f"A design question asks you to choose between the alternatives in {topic}. Which answer is strongest?",
            correct="d",
            options={
                "a": "Choose the first alternative listed because lecture order implies priority.",
                "b": "Choose the option with the most familiar name and ignore the workload.",
                "c": "State that both are identical because they appear in the same lecture.",
                "d": "Match the choice to the constraint, then justify the tradeoff using course reasoning.",
            },
            wrong={
                "a": "Lecture order is not a design criterion.",
                "b": "A familiar name is not a technical justification.",
                "c": "Comparison slides exist because the alternatives differ in meaningful ways.",
            },
            explanation=f"Slide {slide.slide_number} supports an evaluation question. The expected answer should connect the choice to a constraint or workload, not just name an option.",
        )

    if any(word in lower_text for word in ["property", "rule", "invariant", "must", "always", "valid", "definition"]):
        return _scenario_question(
            slide,
            topic,
            difficulty="medium",
            bloom="Analysis",
            concept=topic,
            objective="Diagnose whether a proposed solution satisfies the rule.",
            prompt=f"A proposed solution claims to satisfy {topic}. What should you check first?",
            correct="c",
            options={
                "a": "Whether the wording matches the slide title exactly.",
                "b": "Whether the solution looks visually similar to the lecture example.",
                "c": "Whether the defining condition or invariant still holds in the new scenario.",
                "d": "Whether the answer uses the longest possible explanation.",
            },
            wrong={
                "a": "Keyword matching does not prove the rule is satisfied.",
                "b": "A new exam scenario may look different while testing the same invariant.",
                "d": "Length is not a substitute for correct reasoning.",
            },
            explanation=f"Slide {slide.slide_number} is testing a rule or invariant around {topic}. The exam move is to check that condition in the new situation.",
        )

    return _scenario_question(
        slide,
        topic,
        difficulty=_difficulty(slide.text),
        bloom="Understanding",
        concept=topic,
        objective="Explain the mechanism and apply it to a small scenario.",
        prompt=f"A student has read the slide on {topic} but must answer a new exam scenario. Which response shows real understanding?",
        correct="a",
        options={
            "a": "Explain the mechanism, apply it to the scenario, and justify the outcome using the lecture constraints.",
            "b": "Repeat one phrase from the slide without applying it.",
            "c": "Use outside facts first and only mention the course material if there is time.",
            "d": "Answer with a definition even if the prompt asks for a prediction or decision.",
        },
        wrong={
            "b": "The practice goal is retrieval plus application, not phrase matching.",
            "c": "StudyPace keeps answers grounded in the uploaded course material first.",
            "d": "Definitions are not enough when the prompt asks for reasoning.",
        },
        explanation=f"Slide {slide.slide_number} gives the source concept, but the exam skill is applying {topic} to a new situation with justification.",
    )


def _scenario_question(
    slide: ExtractedSlide,
    topic: str,
    *,
    difficulty: str,
    bloom: str,
    concept: str,
    objective: str,
    prompt: str,
    correct: str,
    options: dict[str, str],
    wrong: dict[str, str],
    explanation: str,
) -> dict:
    return {
        "difficulty": difficulty,
        "bloom": bloom,
        "concept": _shorten_phrase(concept, 54),
        "objective": _shorten_phrase(objective, 58),
        "prompt": prompt,
        "options": options,
        "correct": correct,
        "wrong_explanations": wrong,
        "explanation": explanation,
    }


def _exam_topic_tag(question: dict) -> str:
    return " | ".join([
        f"Difficulty: {question['difficulty'].title()}",
        f"Bloom: {question['bloom']}",
        f"Concept: {question['concept']}",
        f"Objective: {question['objective']}",
    ])


def _is_formula_or_complexity_slide(text: str) -> bool:
    lower = text.lower()
    return (
        any(symbol in text for symbol in ["=", "∑", "Θ", "O("])
        or any(word in lower for word in ["formula", "equation", "complexity", "bound", "runtime", "running time"])
    )


def _extracted_questions(slides: list[ExtractedSlide]) -> list[dict]:
    numerical: list[dict] = []
    problems: list[dict] = []
    other: list[dict] = []
    seen: set[str] = set()

    for slide in slides:
        topic = _slide_topic(slide)
        for prompt in _question_candidates(slide.text):
            normalized = _normalize_question(prompt)
            if normalized in seen:
                continue
            seen.add(normalized)

            kind = _extracted_question_kind(prompt)
            question = _extracted_question(slide.slide_number, prompt, topic, kind, slide.text)
            if kind == "numerical":
                numerical.append(question)
            elif kind == "problem":
                problems.append(question)
            else:
                other.append(question)

    return [*numerical, *problems, *other]


def _extracted_question_kind(prompt: str) -> str:
    if _is_numerical_problem(prompt):
        return "numerical"
    if _is_problem_prompt(prompt):
        return "problem"
    return "question"


def _extracted_question(slide_number: int, prompt: str, topic: str, kind: str, slide_text: str = "") -> dict:
    metadata = {
        "numerical": (
            "extracted_numerical",
            "Extracted numerical",
            "extracted-numerical",
            "Work the numerical problem from the formulas, examples, and values on the cited slide. "
            "If a value or assumption is missing, flag it for confirmation instead of inventing it.",
        ),
        "problem": (
            "extracted_problem",
            "Extracted problem",
            "extracted-problem",
            "Work the proof, construction, or problem-solving step from the cited slide. "
            "If a premise is missing, flag it for confirmation instead of inventing it.",
        ),
        "question": (
            "extracted_question",
            "Extracted question",
            "extracted-question",
            "Answer using only the cited slide and nearby lecture context. If the slide is unclear, mark it for confirmation.",
        ),
    }
    question_type, label, difficulty, guidance = metadata.get(kind, metadata["question"])
    prompt = _actionable_extracted_prompt(slide_number, prompt, kind)

    model_answer = generate_model_answer(prompt, slide_text, slide_number, kind)
    explanation = (
        _clean_model_answer(model_answer)
        if model_answer
        else _local_extracted_answer(slide_number, prompt, kind, slide_text, guidance)
    )

    return {
        "question_type": question_type,
        "slide_number": slide_number,
        "prompt": prompt,
        "options": {
            "a": "Use the cited slide content to answer.",
            "b": "Guess from general knowledge.",
            "c": "Use an unrelated lecture section.",
            "d": "Ignore missing slide details.",
        },
        "correct": "a",
        "explanation": explanation,
        "wrong_explanations": {
            "b": "This app keeps practice grounded in the uploaded slides before using outside knowledge.",
            "c": "The cited slide should be the first source for this prompt.",
            "d": "Missing or unclear slide details should be confirmed, not filled in.",
        },
        "difficulty": difficulty,
        "topic_tag": label if not topic else f"{label}: {topic}",
    }


def _question_candidates(text: str) -> list[str]:
    lines = _content_lines(text)
    candidates: list[str] = []

    for index, line in enumerate(lines):
        candidate = line
        if index > 0 and line[:1].islower() and lines[index - 1].lower().startswith(("what ", "which ", "how ", "why ", "when ", "where ")):
            candidate = _clean_sentence(f"{lines[index - 1]} {line}")
        if _is_valid_question_candidate(candidate):
            candidates.append(candidate)

    for question in re.findall(r"[^?\n]{8,}\?", "\n".join(lines)):
        cleaned = _clean_sentence(question)
        if cleaned[:1].islower():
            continue
        if cleaned and _is_valid_question_candidate(cleaned):
            candidates.append(cleaned)

    cleaned_candidates: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        candidate = _clean_question_prompt(candidate)
        normalized = _normalize_question(candidate)
        if normalized and normalized not in seen:
            seen.add(normalized)
            cleaned_candidates.append(candidate)
    return cleaned_candidates


def _is_question_like(line: str) -> bool:
    lower = line.lower()
    if "?" in line and line.strip().endswith("?"):
        return True
    if re.match(r"^(?:q(?:uestion)?|problem|exercise|practice|checkpoint|try it)\s*\d*\s*[:.)-]\s+", lower):
        return True
    if re.match(r"^\d{1,2}[.)]\s+", line) and _is_problem_prompt(line):
        return True
    return _is_problem_prompt(line)


def _is_valid_question_candidate(line: str) -> bool:
    cleaned = _clean_question_prompt(line)
    lower = cleaned.lower()
    words = re.findall(r"[A-Za-z0-9]+", cleaned)
    if _is_category_definition_line(cleaned):
        return False
    if _looks_like_code(cleaned):
        return False
    if re.search(r"https?://|youtu\\.?be|youtube", lower):
        return False
    if _is_vague_slide_question(cleaned):
        return False
    if len(words) < 5 and not _is_numerical_problem(cleaned):
        return False
    if len(cleaned) > 220:
        return False
    if cleaned[0].islower() and not cleaned.endswith("?"):
        return False
    if not _is_question_like(cleaned):
        return False
    return True


def _is_vague_slide_question(line: str) -> bool:
    lower = re.sub(r"[^a-z0-9\\s]", " ", line.lower())
    lower = re.sub(r"\\s+", " ", lower).strip()
    words = lower.split()
    if lower in {"what color", "what color should it be", "where does it go", "what is the running time"}:
        return True
    if len(words) <= 4 and words[:1] in [["what"], ["where"], ["how"], ["why"]]:
        return True
    if len(words) <= 7 and any(word in {"it", "this", "that", "they", "them"} for word in words):
        return True
    return False


def _is_numerical_problem(line: str) -> bool:
    lower = line.lower()
    has_quantity = bool(re.search(
        r"(?<![a-z])\d+(?:\.\d+)?%?(?![a-z])|=|%|\b(?:ms|sec|seconds|minutes|kg|km|m/s|bytes|kb|mb|gb|marks|points|credits|gpa|rate|probability|page faults?)\b",
        lower,
    ))
    return has_quantity and _has_problem_prompt_cue(lower)


def _is_problem_prompt(line: str) -> bool:
    lower = line.lower().strip()
    if _looks_like_code(line):
        return False
    if _is_category_definition_line(line):
        return False
    if lower.startswith(("for example", "e.g.", "eg.")):
        return False
    prompt_start = r"^(?:q(?:uestion)?|problem|exercise|practice|checkpoint|try it)?\s*\d*\s*[:.)-]?\s*"
    imperative = (
        r"(?:calculate|compute|solve|find|determine|derive|estimate|prove|show\s+that|"
        r"construct|convert|trace|design)\b"
    )
    step_prefix = r"^(?:first|second|third|next|final)\s+step:\s*"

    if re.match(prompt_start + imperative, lower):
        return True
    if re.match(step_prefix + imperative, lower):
        return True
    if re.match(r"^(?:given|for|using|let|if|when)\b.{0,120}" + imperative, lower):
        return True
    return bool(re.search(r"\b(?:how\s+many|what\s+is\s+the\s+value)\b", lower))


def _actionable_extracted_prompt(slide_number: int, prompt: str, kind: str) -> str:
    cleaned = _clean_question_prompt(prompt)
    lower = cleaned.lower()
    if lower.startswith("what about "):
        topic = cleaned[11:].strip(" ?.")
        return f"According to slide {slide_number}, what answer does the lecture give for {topic}?"
    if kind in {"problem", "numerical"} and not cleaned.endswith("?"):
        return cleaned
    return cleaned


def _is_category_definition_line(line: str) -> bool:
    cleaned = _clean_sentence(line)
    lower = cleaned.lower()
    category_verbs = {
        "control": "governing",
        "debugging": "implementing",
        "design": "designing",
        "diagnosis": "determining",
        "instruction": "correcting",
        "interpretation": "conclusions",
        "monitoring": "comparing",
        "planning": "sequence",
        "prediction": "projecting",
        "repair": "implementing",
    }
    for category, follower in category_verbs.items():
        if lower.startswith(f"{category} {follower} "):
            return True
    return False


def _clean_model_answer(value: str | None) -> str:
    if not value:
        return ""
    cleaned = value.strip()
    cleaned = re.sub(r"^#{1,6}\s*", "", cleaned, flags=re.M)
    cleaned = re.sub(r"^model answer\s*:?\s*", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\*\*(.*?)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"^\s*[-*]\s+", "", cleaned, flags=re.M)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _local_extracted_answer(slide_number: int, prompt: str, kind: str, slide_text: str, guidance: str) -> str:
    answer = _answer_after_marker(slide_text)
    if answer and prompt.endswith("?"):
        return (
            f"Slide {slide_number} answers this with: {answer}. "
            "A strong response should restate that answer and briefly connect it back to the question."
        )
    if "modus ponens" in slide_text.lower():
        return (
            f"Slide {slide_number} is practising modus ponens: if E1 is true and E1 implies E2, "
            "then E2 follows. A strong answer should name the known fact, the implication rule, and the conclusion."
        )
    return f"Slide {slide_number} contains this practice prompt. {guidance}"


def _answer_after_marker(text: str) -> str:
    lines = _content_lines(text)
    for index, line in enumerate(lines[:-1]):
        if line.lower().strip(":") == "answer":
            candidate = _clean_sentence(lines[index + 1])
            if candidate and not _is_boilerplate(candidate) and not _is_fragment(candidate):
                return candidate
    return ""


def _has_problem_prompt_cue(lower: str) -> bool:
    if _is_problem_prompt(lower):
        return True

    # "Evaluate" appears in definitions like "evaluate to 1"; only treat it as a
    # task when the slide is clearly phrasing a prompt.
    return bool(re.match(r"^(?:q(?:uestion)?|problem|exercise|practice|checkpoint|try it)?\s*\d*\s*[:.)-]?\s*evaluate\b", lower))


def _has_problem_solving_content(text: str) -> bool:
    lower = text.lower()
    return (
        _is_numerical_problem(text)
        or any(symbol in text for symbol in ["=", "∑", "Θ", "O("])
        or any(re.search(cue, lower) for cue in [
            r"\bformula\b",
            r"\bequation\b",
            r"\bworked\s+example\b",
            r"\balgorithm\b",
            r"\bderive\b",
        ])
    )


def _looks_like_code(value: str) -> bool:
    lower = value.lower()
    code_markers = [
        "//",
        "public ",
        "private ",
        "protected ",
        "class ",
        "system.out",
        "compareto",
        "null",
        "extends comparable",
    ]
    if any(marker in lower for marker in code_markers):
        return True
    if re.search(r"[{};<>]", value) and re.search(r"\b(?:int|void|boolean|class|return|private|public|else|if)\b", lower):
        return True
    if re.search(r"^\s*return\b", lower):
        return True
    return False


def _clean_question_prompt(value: str) -> str:
    value = _clean_sentence(value)
    value = re.sub(r"^(?:question|problem|exercise|practice|checkpoint)\s*(\d+)\s*[:.)-]\s*", r"Q\1: ", value, flags=re.I)
    value = re.sub(r"^q\s*(\d+)\s*[:.)-]\s*", r"Q\1: ", value, flags=re.I)
    return value


def _normalize_question(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _flashcards(slides: list[ExtractedSlide], concepts: list[str]) -> list[dict]:
    cards: list[dict] = []
    for slide in slides:
        concept = _slide_topic(slide)
        answer = _best_sentence(slide.text)
        if concept and answer and not _is_boilerplate(concept):
            cards.append({
                "slide_number": slide.slide_number,
                "front": f"What should you remember about {concept} from slide {slide.slide_number}?",
                "back": answer,
            })
    if not cards:
        for concept in concepts[:6]:
            cards.append({"slide_number": 1, "front": concept, "back": "Confirm this concept against the uploaded slide text."})
    return cards[:10]


def _concept_pool(slides: list[ExtractedSlide]) -> list[dict]:
    seen: set[str] = set()
    pool = []
    for slide in slides:
        candidates = [_best_sentence(slide.text), *_important_lines(slide.text), *_content_lines(slide.text)]
        for line in candidates:
            sentence = _clean_sentence(line)
            if not sentence or _is_fragment(sentence) or _looks_like_code(sentence):
                continue
            # Accept strong study statements OR any substantive sentence (≥8 words)
            if len(sentence.split()) >= 5 and (_is_study_statement(sentence) or len(sentence.split()) >= 8):
                key = sentence.lower()
                if key not in seen:
                    seen.add(key)
                    pool.append({"slide": slide.slide_number, "text": sentence})
    return pool


def _is_question_worthy_slide(slide: ExtractedSlide) -> bool:
    lines = [
        line for line in _content_lines(slide.text)
        if not line.isdigit() and not _is_boilerplate(line)
    ]
    if not lines:
        return False

    words = re.findall(r"[A-Za-z0-9]+", " ".join(lines))
    if len(words) < 12:
        return False
    if all(len(line.split()) <= 4 for line in lines):
        return False
    return any(_is_study_statement(line) for line in lines) or len(words) >= 24


def _is_study_statement(value: str) -> bool:
    cleaned = _clean_sentence(value)
    if not cleaned:
        return False
    lower = f" {cleaned.lower()} "
    if any(token in lower for token in [
        " is ",
        " are ",
        " means ",
        " refers to ",
        " defined as ",
        " provides ",
        " consists ",
        " includes ",
        " uses ",
        " based on ",
        " because ",
        " therefore ",
        " if ",
        " then ",
        " can ",
        " must ",
    ]):
        return True
    if ":" in cleaned and len(cleaned.split()) >= 5:
        return True
    return len(cleaned.split()) >= 10 and not all(len(part.split()) <= 4 for part in re.split(r"[,;/]", cleaned))


def _is_summary_statement(value: str) -> bool:
    cleaned = _clean_sentence(value)
    if not cleaned or len(cleaned.split()) < 6:
        return False
    lower = f" {cleaned.lower()} "
    strong_tokens = [
        " is ",
        " are ",
        " means ",
        " refers to ",
        " defined as ",
        " provides ",
        " performs ",
        " uses ",
        " encoded ",
        " extracted ",
        " attempts ",
        " consists ",
        " based on ",
        " implies ",
        " true ",
        " false ",
        " between ",
    ]
    return any(token in lower for token in strong_tokens)


def _summary_clause(value: str) -> str:
    cleaned = _clean_sentence(value).rstrip(".")
    if not cleaned:
        return cleaned
    lowered = cleaned[:1].lower() + cleaned[1:]
    if lowered.startswith("is "):
        return f"this concept {lowered}"
    if lowered.startswith("are "):
        return f"these concepts {lowered}"
    return lowered


def _distractors(correct: str, slide_number: int, pool: list[dict]) -> list[dict]:
    distractors = []
    seen = {correct.lower()}
    for item in pool:
        text = item["text"]
        if text.lower() in seen or _too_similar(correct, text):
            continue
        seen.add(text.lower())
        source = item["slide"]
        why = (
            f"This comes from slide {source}, but it does not answer the slide {slide_number} prompt."
            if source != slide_number
            else f"Slide {slide_number} mentions this, but it is not the best answer to the prompt."
        )
        distractors.append({"text": text, "why": why})
        if len(distractors) == 3:
            return distractors

    # Content-aware fallbacks: extract a keyword from the correct answer so the
    # wrong options sound plausible rather than like meta-commentary.
    keywords = [w for w in re.findall(r"[A-Za-z]{5,}", correct) if not _is_boilerplate(w)]
    kw = keywords[0].lower() if keywords else f"slide {slide_number}"
    fallbacks = [
        (
            f"This reverses the direction of the claim on slide {slide_number}.",
            f"Slide {slide_number} states the relationship in the other direction.",
        ),
        (
            f"This confuses a necessary condition with the sufficient condition for {kw}.",
            f"Slide {slide_number} specifies only one direction of the implication.",
        ),
        (
            f"This applies a rule from a different section to the {kw} case.",
            f"Slide {slide_number} is the source; using a rule from elsewhere loses context.",
        ),
    ]
    for text, why in fallbacks:
        if text.lower() not in seen:
            distractors.append({"text": text, "why": why})
        if len(distractors) == 3:
            break
    return distractors[:3]


def _too_similar(left: str, right: str) -> bool:
    left_norm = left.lower().strip()
    right_norm = right.lower().strip()
    if left_norm in right_norm or right_norm in left_norm:
        return True
    left_words = {w for w in re.findall(r"[a-z0-9]+", left_norm) if len(w) > 3}
    right_words = {w for w in re.findall(r"[a-z0-9]+", right_norm) if len(w) > 3}
    if not left_words or not right_words:
        return False
    overlap = len(left_words & right_words) / max(1, min(len(left_words), len(right_words)))
    return overlap > 0.65


def _important_lines(text: str) -> list[str]:
    lines = _content_lines(text)
    scored = []
    for line in lines:
        if _is_boilerplate(line) or _is_fragment(line) or _looks_like_code(line):
            continue
        lower = line.lower()
        score = 0
        if ":" in line or " is " in lower or " are " in lower:
            score += 2
        if any(word in lower for word in ["definition", "example", "objective", "formula", "therefore", "because"]):
            score += 2
        if 5 <= len(line.split()) <= 24:
            score += 1
        if score:
            scored.append((score, line))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [_clean_sentence(line) for _, line in scored[:5]]


def _best_sentence(text: str) -> str:
    lines = _content_lines(text)
    sentences = re.split(r"(?<=[.!?])\s+|\n+", "\n".join(lines))
    candidates = [_clean_sentence(s) for s in sentences if 5 <= len(s.split()) <= 36 and not _is_boilerplate(s) and not _is_fragment(s)]
    if not candidates:
        useful_text = " ".join(line for line in lines if not _is_boilerplate(line) and not _is_fragment(line))
        words = useful_text.split()
        return _clean_sentence(" ".join(words[:24])) if len(words) >= 5 else ""
    definitions = [s for s in candidates if any(token in s.lower() for token in [" is ", " are ", " means ", " refers to "])]
    return definitions[0] if definitions else candidates[0]


def _definition_line(text: str) -> str:
    for line in _important_lines(text):
        lower = line.lower()
        if any(token in lower for token in [" is ", " are ", "means", "refers to", "defined as"]):
            return line
    return ""


def _clean_concept(value: str) -> str:
    value = _clean_sentence(value)
    value = re.sub(r"^[Nn]\s+(?=expert system\b)", "", value)
    value = re.sub(r"^(slide|lecture)\s+\d+\s*[:.-]?\s*", "", value, flags=re.I)
    value = re.sub(r"^\d+[.)]\s*", "", value)
    value = re.sub(r"^(definition|example|formula|objective|outcome|question)\s*[:.-]\s*", "", value, flags=re.I)
    return _shorten_phrase(value, 140)


def _topic_from_definition(value: str) -> str:
    cleaned = _clean_sentence(value)
    match = re.match(
        r"^(?:a|an|the)?\s*(.{2,80}?)\s+(?:is|are|means|refers to|defined as)\b",
        cleaned,
        flags=re.I,
    )
    if not match:
        return ""
    topic = _clean_concept(match.group(1))
    topic = re.sub(r"^(?:a|an|the)\s+", "", topic, flags=re.I).strip()
    if topic.lower() in {"there", "it", "this", "that"}:
        return ""
    if 1 <= len(topic.split()) <= 6 and not _is_boilerplate(topic) and not _is_fragment(topic):
        return topic[:1].upper() + topic[1:]
    return ""


def _clean_sentence(value: str) -> str:
    value = re.sub(r"\s+", " ", value or "").strip(" -•◼❑▪▫\t")
    value = re.sub(r"^[•◼❑▪▫]+\s*", "", value)
    return value


def _content_lines(text: str) -> list[str]:
    raw_lines = [_clean_sentence(line.strip(" •-\t")) for line in text.splitlines()]
    raw_lines = [line for line in raw_lines if line and not _is_boilerplate(line)]
    merged: list[str] = []
    for line in raw_lines:
        if merged and _should_merge_wrapped_line(merged[-1], line):
            merged[-1] = _clean_sentence(f"{merged[-1]} {line}")
        else:
            merged.append(line)
    return merged


def _should_merge_wrapped_line(previous: str, current: str) -> bool:
    if not previous or not current:
        return False
    if re.match(r"^(?:first|second|third|next|final)\s+step:", current.lower()):
        return False
    lower_previous = previous.lower()
    first = current[0]
    if previous.endswith((".", "?", "!", ";")):
        return False
    if first.islower():
        return True
    if lower_previous.endswith((" of", " for", " to", " in", " on", " with", " from", " and", " or", " the", " a", " an", " any", " some")):
        return True
    return previous.count("(") > previous.count(")")


def _is_fragment(value: str) -> bool:
    cleaned = _clean_sentence(value)
    if not cleaned:
        return True
    lower = cleaned.lower()
    if lower in {"introduction", "overview", "preliminary"}:
        return True
    if _is_category_definition_line(cleaned):
        return True
    if re.search(r"\be\d+\b", lower) and len(cleaned.split()) <= 8:
        return True
    if re.match(r"^[a-z]\d+\s+(?:is|are)\s+(?:true|false)\b", lower):
        return True
    if re.match(r"^(?:all\s+)?[a-z][a-z\s]*\s+(?:is|are)\s+[a-z][a-z\s]*$", lower) and len(cleaned.split()) <= 5:
        return True
    if lower in {"e.g.", "e.g.:", "eg.", "eg:"}:
        return True
    if len(cleaned.split()) == 1 and cleaned.endswith(":"):
        return True
    if len(cleaned) > 180:
        return True
    if lower.startswith(("and ", "or ", "of ", "to ", "problems,", "assignment ")):
        return True
    if lower.startswith(("how it ", "how this ", "how that ", "how they ")):
        return True
    if lower.endswith((" of", " for", " to", " in", " on", " with", " from", " and", " or", " the", " a", " an")):
        return True
    return bool(cleaned[0].islower() and len(cleaned.split()) > 3 and not re.search(r"[=≤≥∑Θ]", cleaned))


def _slide_topic(slide: ExtractedSlide) -> str:
    domain_topic = _domain_slide_topic(slide.text)
    if domain_topic:
        return domain_topic

    title = _clean_concept(slide.title)
    if title and not _is_boilerplate(title) and not _is_fragment(title):
        return title

    for line in _content_lines(slide.text)[:8]:
        concept = _clean_concept(line)
        if concept.lower() == "preliminary" and len(_content_lines(slide.text)) > 2:
            continue
        if re.match(r"^chapter\s+\d+", concept.lower()):
            continue
        defined_topic = _topic_from_definition(concept)
        if defined_topic:
            return defined_topic
        if concept and not _is_boilerplate(concept) and not _is_fragment(concept) and len(concept.split()) <= 8:
            return concept

    for line in _important_lines(slide.text):
        concept = _clean_concept(line)
        defined_topic = _topic_from_definition(concept)
        if defined_topic:
            return defined_topic
        if concept and not _is_fragment(concept):
            return concept
    return f"Slide {slide.slide_number}"


def _domain_slide_topic(text: str) -> str:
    lower = text.lower()
    if "knowledge and reasoning" in lower and "expert systems" in lower:
        return "Knowledge and Reasoning Expert Systems"
    if "typical problems" in lower and "expert" in lower:
        return "Typical Problems in Expert Systems"
    if "modus ponens" in lower:
        return "Modus Ponens Inference"
    if "inference engine" in lower:
        return "Inference Engine"
    if "propositional logic" in lower or "predicate logic" in lower:
        return "Knowledge Representation and Inference"
    if "domain-driven" in lower or "application-driven" in lower:
        return "Domain-Driven Expert-System Rules"
    if "main ai techniques" in lower:
        return "Main AI Techniques"
    return ""


def _shorten_phrase(value: str, max_length: int) -> str:
    value = _clean_sentence(value)
    if len(value) <= max_length:
        return value
    shortened = value[: max_length - 3].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return f"{shortened}..." if shortened else value[:max_length]


def _is_boilerplate(value: str) -> bool:
    cleaned = _clean_sentence(value)
    lower = cleaned.lower()
    if not cleaned:
        return True
    if "©" in cleaned or "charles e. leiserson" in lower:
        return True
    if re.search(r"\bslide\s*[‹<#]\s*#\s*[›>#]", lower):
        return True
    if lower.startswith("credit:"):
        return True
    if lower.startswith("last lecture"):
        return True
    if lower.startswith("slide ‹"):
        return True
    if lower in {"automata, computability,", "and complexity", "automata, computability, and complexity"}:
        return True
    return False


def _difficulty(text: str) -> str:
    words = len(text.split())
    if words > 140 or any(symbol in text for symbol in ["=", "∑", "Θ", "O("]):
        return "hard"
    if words < 45:
        return "easy"
    return "medium"
