from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

load_dotenv(BACKEND_DIR / ".env")

from database import SessionLocal  # noqa: E402
from services.lesson_regenerator import default_data_dir, regenerate_lessons_from_cached_sources  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate lesson summaries, slides, flashcards, and questions from cached source material."
    )
    parser.add_argument("--write", action="store_true", help="Persist regenerated content to the database.")
    parser.add_argument("--course-id", type=int, help="Only regenerate lectures for one course.")
    parser.add_argument("--lecture-id", type=int, help="Only regenerate one lecture.")
    parser.add_argument("--source-limit", type=int, help="Only process the first N matched source files.")
    parser.add_argument("--data-dir", type=Path, default=default_data_dir(), help="StudyPace data directory.")
    parser.add_argument(
        "--no-quiz-bank",
        action="store_true",
        help="Do not refresh generated quiz-bank questions for each lecture topic.",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        summary = regenerate_lessons_from_cached_sources(
            db,
            data_dir=args.data_dir,
            course_id=args.course_id,
            lecture_id=args.lecture_id,
            source_limit=args.source_limit,
            dry_run=not args.write,
            sync_quiz_bank=not args.no_quiz_bank,
            progress=lambda index, total, path, count: print(
                f"[{index}/{total}] {path} -> {count} lecture(s)",
                flush=True,
            ),
        )
    finally:
        db.close()

    mode = "WRITE" if args.write else "DRY RUN"
    print(f"{mode}: scanned {summary.scanned_lectures} lecture rows")
    print(f"Matched source files: {summary.matched_sources}")
    print(f"Lectures {'updated' if args.write else 'that would update'}: {summary.updated_lectures}")
    for item in summary.updated_sources:
        lecture_preview = ", ".join(str(lecture_id) for lecture_id in item.lecture_ids[:8])
        if len(item.lecture_ids) > 8:
            lecture_preview += f", +{len(item.lecture_ids) - 8} more"
        print(
            f"- {item.source_path}: {len(item.lecture_ids)} lecture(s) [{lecture_preview}], "
            f"{item.slides} slides, {item.questions} questions, {item.flashcards} flashcards"
        )
    if summary.missing_sources:
        print(f"Missing source refs: {len(summary.missing_sources)}")
        for missing in summary.missing_sources[:20]:
            print(f"- {missing}")
    if summary.failed_sources:
        print(f"Failed source files: {len(summary.failed_sources)}")
        for failed in summary.failed_sources:
            print(f"- {failed}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
