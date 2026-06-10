from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


class LocalAI:
    def __init__(self) -> None:
        self.base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        self.model = os.getenv("OLLAMA_MODEL", "phi3:mini")

    def status(self) -> dict:
        models = self._models()
        active_model = self._active_model(models)
        if models:
            return {
                "available": True,
                "provider": "ollama",
                "model": active_model,
                "preferred_model": self.model,
                "installed_models": models,
                "offline_ready": True,
            }
        return {
            "available": False,
            "provider": "ollama",
            "model": self.model,
            "preferred_model": self.model,
            "installed_models": [],
            "offline_ready": False,
        }

    def _models(self) -> list[str]:
        try:
            with urllib.request.urlopen(f"{self.base_url}/api/tags", timeout=0.6) as response:
                data = json.loads(response.read().decode("utf-8"))
            return [m.get("name", "") for m in data.get("models", []) if m.get("name")]
        except Exception:
            return []

    def _active_model(self, models: list[str]) -> str:
        if self.model in models:
            return self.model
        preferred_order = ["phi3", "llama3.2:1b", "qwen2.5:1.5b", "llama3.2", "mistral", "llama3.1:8b", "llama3"]
        for preferred in preferred_order:
            for model in models:
                if model == preferred or model.startswith(f"{preferred}:") or preferred in model:
                    return model
        return models[0] if models else self.model

    def warmup(self) -> None:
        models = self._models()
        if not models:
            return
        payload = {
            "model": self._active_model(models),
            "prompt": "ready",
            "stream": False,
            "keep_alive": "10m",
            "options": {
                "num_predict": 1,
                "num_ctx": 128,
                "temperature": 0,
            },
        }
        try:
            req = urllib.request.Request(
                f"{self.base_url}/api/generate",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5.0) as response:
                response.read()
        except Exception:
            return

    def explain(self, question: str, context: str, allow_general: bool = False) -> str:
        return self._complete(
            question,
            context,
            allow_general=allow_general,
            num_predict=70,
            context_limit=1400,
            response_limit=500,
            timeout_seconds=2.0,
        )

    def summarize_lecture(self, question: str, context: str, allow_general: bool = False) -> str:
        return self._complete(
            question,
            context,
            allow_general=allow_general,
            mode="lecture_summary",
            num_predict=140,
            context_limit=2600,
            response_limit=1400,
            timeout_seconds=2.5,
        )

    def chat(self, message: str, context: str, history: list[dict] | None = None, allow_general: bool = False) -> str:
        recent = []
        for item in (history or [])[-6:]:
            role = "Student" if item.get("role") == "user" else "Tutor"
            content = str(item.get("content", "")).strip()
            if content:
                recent.append(f"{role}: {content[:500]}")
        transcript = "\n".join(recent)
        question = f"Recent chat:\n{transcript}\n\nStudent: {message}" if transcript else message
        return self._complete(
            question,
            context,
            allow_general=allow_general,
            num_predict=70,
            context_limit=1500,
            response_limit=500,
            timeout_seconds=2.0,
        )

    def general_chat(self, message: str, history: list[dict] | None = None) -> str:
        recent = []
        for item in (history or [])[-4:]:
            role = "Student" if item.get("role") == "user" else "Tutor"
            content = str(item.get("content", "")).strip()
            if content:
                recent.append(f"{role}: {content[:260]}")
        transcript = "\n".join(recent)
        question = f"Recent chat:\n{transcript}\n\nStudent: {message}" if transcript else message
        return self._complete_general(question)

    def _complete_general(self, question: str) -> str:
        models = self._models()
        if not models:
            return "Ollama is not reachable right now."
        active_model = self._active_model(models)
        prompt = (
            "Answer this general student question in one complete sentence under 28 words. "
            "Do not cite course slides unless asked. For medical, legal, financial, or safety advice, keep it high-level and suggest a qualified professional.\n\n"
            f"{question}\n\nAnswer:"
        )
        payload = {
            "model": active_model,
            "prompt": prompt,
            "stream": False,
            "keep_alive": "10m",
            "options": {
                "temperature": 0.2,
                "num_predict": 36,
                "num_ctx": 512,
                "top_k": 20,
                "top_p": 0.85,
            },
        }
        try:
            req = urllib.request.Request(
                f"{self.base_url}/api/generate",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=3.0) as response:
                data = json.loads(response.read().decode("utf-8"))
            return (data.get("response") or "").strip()[:700]
        except (urllib.error.URLError, TimeoutError, Exception):
            return "Ollama is not reachable right now."

    def _complete(
        self,
        question: str,
        context: str,
        allow_general: bool = False,
        num_predict: int = 160,
        mode: str = "answer",
        context_limit: int = 5200,
        response_limit: int = 900,
        timeout_seconds: int = 30,
    ) -> str:
        models = self._models()
        if not models:
            if mode == "lecture_summary" and context.strip():
                return self._fallback_lecture_summary(context)
            if context.strip():
                return f"Ollama is busy right now. From your local course material: {context.strip()[:420]}"
            return "Ollama is busy right now, and I do not have enough local course context to answer safely."
        active_model = self._active_model(models)
        guardrail = (
            "Answer only from the local course material and study state below. Do not fill gaps with outside knowledge. If the material is unclear or too generic, say what needs confirmation."
            if not allow_general
            else "Use the local course material first, then add a short general explanation if needed."
        )
        if mode == "lecture_summary":
            prompt = (
                f"{guardrail}\n"
                "Read the local lecture context carefully and write a plain-text study summary for a university student.\n"
                "Organize it by topic using simple titles like 'Summary: Lecture Topic' and 'Part 1 - Topic Name'. "
                "Use flowing prose paragraphs. Do not use bullet points, bold text, Markdown headings, hash symbols, decorative formatting, or checklist formatting. "
                "Cover every meaningful concept, algorithm, definition, example, and process available in the context. Explain the intuition behind each idea, not just the name. "
                "Cite slide numbers naturally in parentheses like (Slide 4). If a formula, value, or assumption is missing, say it needs confirmation. Do not invent definitions, formulas, examples, or values.\n\n"
                f"Local lecture context:\n{context[:context_limit]}\n\nStudent request:\n{question}\n\nTutor study briefing:"
            )
        else:
            prompt = (
                f"{guardrail}\n"
                "Keep the answer short, practical, and student-friendly. Mention slide/course evidence when useful. Do not invent definitions, formulas, examples, or values that are not in the local context.\n\n"
                "If the student names a course, lecture, chapter, slide, or topic, use the matching local course material first. If the resolved course or lecture seems wrong, say what needs confirmation.\n"
                "Use course and lecture names exactly as written in the local context. Never mention a different course name than the cited sources.\n"
                "If the student asks what to study, what to do next, or whether they are ready, prioritize Today's study plan, Weak topics, Upcoming assessments, mistakes, and Grade weights before slide snippets.\n"
                f"Local context:\n{context[:context_limit]}\n\nQuestion:\n{question}\n\nTutor answer:"
            )
        payload = {
            "model": active_model,
            "prompt": prompt,
            "stream": False,
            "keep_alive": "10m",
            "options": {
                "temperature": 0.1,
                "num_predict": num_predict,
                "num_ctx": 1024 if mode != "lecture_summary" else 2048,
                "top_k": 20,
                "top_p": 0.8,
            },
        }
        try:
            req = urllib.request.Request(
                f"{self.base_url}/api/generate",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
            answer = (data.get("response") or "").strip()[:response_limit]
            if mode == "lecture_summary" and not _is_grounded_summary(answer):
                return self._fallback_lecture_summary(context)
            return answer
        except (urllib.error.URLError, TimeoutError, Exception):
            if mode == "lecture_summary" and context.strip():
                return self._fallback_lecture_summary(context)
            if context.strip():
                return f"Ollama is not reachable right now. From your local course material: {context.strip()[:420]}"
            return "Ollama is not reachable right now, and I do not have enough local course context to answer safely."

    def _fallback_lecture_summary(self, context: str) -> str:
        lines = [line.strip() for line in context.splitlines() if line.strip()]
        title = _first_value(lines, "Lecture:") or _first_value(lines, "Title:") or "This lecture"
        summary = _first_value(lines, "Stored summary:") or _first_value(lines, "Lecture summary:") or ""
        concepts = _split_csv(_first_value(lines, "Key concepts:"))
        objectives = _split_csv(_first_value(lines, "Learning objectives:"))
        slide_items = [_parse_slide_line(line) for line in lines if line.lower().startswith("slide ")]
        slide_items = [item for item in slide_items if item]

        parts = [
            f"Summary: {title}\n{_build_big_picture(title, summary, concepts, slide_items)}",
        ]
        if concepts:
            concept_text = " ".join(
                f"{item} is one of the concepts the student should be able to explain from the lecture."
                for item in concepts[:10]
            )
            parts.append(f"Part 1 - Core concepts\n{concept_text}")
        if objectives:
            objective_text = " ".join(
                f"The student should be able to {item[0].lower() + item[1:] if item else item}."
                for item in objectives[:6]
            )
            parts.append(f"Part 2 - What the lecture expects you to do\n{objective_text}")
        if slide_items:
            roadmap = _select_slide_roadmap(slide_items)
            roadmap_text = " ".join(
                f"Slide {item['number']} focuses on {item['title']}. {_first_sentence(item['text'])}"
                for item in roadmap
            )
            parts.append(f"Part 3 - Slide-by-slide development\n{roadmap_text}")
            process_items = _select_process_slides(slide_items)
            if process_items:
                process_text = " ".join(
                    f"Slide {item['number']} is important for process work around {item['title']}. {_first_sentence(item['text'])}"
                    for item in process_items[:10]
                )
                parts.append(f"Part 4 - Processes and problem solving\n{process_text}")
        parts.append(
            "Part 5 - Common traps\n"
            "Do not use outside facts unless the question asks for a general explanation. If a slide omits a value, formula, or assumption, the safe answer is to flag what is missing instead of inventing it. For algorithms, structures, or rule systems, the student should explain the invariant, operation steps, and cost or reasoning idea from the slides."
        )
        parts.append(
            "Part 6 - Readiness\n"
            "A student is ready when they can explain each listed concept without looking, recreate any process, formula, or example cited in the slides, and answer the lecture practice prompts while clearly flagging missing values or assumptions."
        )
        return "\n\n".join(parts)[:7600]


def _first_value(lines: list[str], prefix: str) -> str:
    prefix_lower = prefix.lower()
    for line in lines:
        if line.lower().startswith(prefix_lower):
            return line[len(prefix):].strip()
    return ""


def _split_csv(value: str) -> list[str]:
    if not value:
        return []
    return [item.strip(" -") for item in value.split(";") if item.strip(" -")]


def _is_grounded_summary(answer: str) -> bool:
    lower = answer.lower()
    if "slide" not in lower:
        return False
    banned = [
        "additional resources",
        "online resources",
        "textbooks",
        "web search",
        "real-world applications",
    ]
    return not any(term in lower for term in banned)


def _parse_slide_line(line: str) -> dict | None:
    if not line.lower().startswith("slide "):
        return None
    number_part, _, rest = line.partition(":")
    number_text = "".join(ch for ch in number_part if ch.isdigit())
    if not number_text:
        return None
    title_part, _, text_part = rest.partition(" Text: ")
    title = title_part.replace(" Tags:", ". Tags:").split(". Tags:")[0].strip(" .")
    text = text_part.strip() if text_part else rest.strip()
    return {"number": int(number_text), "title": title or f"Slide {number_text}", "text": text}


def _select_slide_roadmap(slides: list[dict]) -> list[dict]:
    if len(slides) <= 18:
        return slides
    selected = slides[:8]
    middle = slides[8:-5]
    step = max(1, len(middle) // 6)
    selected.extend(middle[::step][:6])
    selected.extend(slides[-5:])
    seen = set()
    unique = []
    for slide in selected:
        if slide["number"] in seen:
            continue
        seen.add(slide["number"])
        unique.append(slide)
    return unique


def _select_process_slides(slides: list[dict]) -> list[dict]:
    cues = [
        "algorithm",
        "formula",
        "operation",
        "insert",
        "delete",
        "getitem",
        "findmin",
        "findmax",
        "height",
        "length",
        "travers",
        "step",
        "complexity",
        "big-o",
        "o(",
    ]
    return [
        slide for slide in slides
        if any(cue in f"{slide['title']} {slide['text']}".lower() for cue in cues)
    ]


def _first_sentence(value: str) -> str:
    value = " ".join((value or "").split())
    if not value:
        return "No extracted text beyond the title."
    for marker in [". ", "? ", "! "]:
        if marker in value:
            return value.split(marker, 1)[0].strip()[:220] + "."
    return value[:220]


def _build_big_picture(title: str, summary: str, concepts: list[str], slides: list[dict]) -> str:
    focus_titles = []
    focus_cues = [
        "motivation",
        "application",
        "tree structure",
        "binary search tree",
        "insert",
        "delete",
        "getitem",
        "findmin",
        "useful functions",
        "big-oh",
    ]
    for slide in slides:
        title_text = slide["title"]
        lower = f"{title_text} {slide['text']}".lower()
        if any(cue in lower for cue in focus_cues):
            focus_titles.append(title_text)
    focus = _dedupe(concepts[:6] + focus_titles)[:10]
    if focus:
        overview = f"{title} focuses on " + ", ".join(focus[:-1])
        if len(focus) > 1:
            overview += f", and {focus[-1]}."
        else:
            overview += f"{focus[0]}."
    else:
        overview = f"{title} is covered by the extracted slide material."
    if summary and len(summary) > 160:
        overview += f" The extracted lecture summary adds: {summary[:360]}"
    return overview


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    unique = []
    for item in items:
        cleaned = " ".join(str(item).split()).strip(" -.")
        key = cleaned.lower()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        unique.append(cleaned)
    return unique
