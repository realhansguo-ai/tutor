#!/usr/bin/env python3
"""Create a safe after-class source bundle without modifying source files."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from datetime import date, datetime, timezone
from pathlib import Path


MEDIA_EXTENSIONS = {
    ".aac",
    ".flac",
    ".m4a",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".ogg",
    ".wav",
    ".webm",
}
NOTE_EXTENSIONS = {".doc", ".docx", ".md", ".pdf", ".rtf", ".txt"}
SLIDE_EXTENSIONS = {".key", ".ppt", ".pptx"}
IMAGE_EXTENSIONS = {".gif", ".heic", ".jpeg", ".jpg", ".png", ".webp"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_relative_dir(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"directory must be relative to course root: {value}")
    return path


def parse_date(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ValueError(f"date must use YYYY-MM-DD: {value}") from exc


def session_id(lesson: int | None, class_date: str | None) -> str:
    parts: list[str] = []
    if lesson is not None:
        if lesson < 1:
            raise ValueError("lesson must be a positive integer")
        parts.append(f"L{lesson:02d}")
    if class_date:
        parts.append(class_date)
    if not parts:
        raise ValueError("provide at least --lesson or --date")
    return "_".join(parts)


def safe_title(value: str | None) -> str:
    if not value:
        return "课程笔记"
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", value.strip())
    cleaned = re.sub(r"\s+", "_", cleaned).strip("._")
    return cleaned[:80] or "课程笔记"


def source_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in MEDIA_EXTENSIONS:
        return "media"
    if suffix in SLIDE_EXTENSIONS:
        return "slides"
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix in NOTE_EXTENSIONS:
        return "note-or-document"
    return "other"


def load_existing(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read existing metadata: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"existing metadata is not an object: {path}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--course-root", required=True, type=Path)
    parser.add_argument("--course", required=True)
    parser.add_argument("--source", required=True, nargs="+", type=Path)
    parser.add_argument("--lesson", type=int)
    parser.add_argument("--date")
    parser.add_argument("--title")
    parser.add_argument(
        "--label-source",
        choices=("user", "filename", "media-metadata", "project-sequence"),
        default="user",
    )
    parser.add_argument("--raw-dir", default="01_raw")
    parser.add_argument("--notes-dir", default="02_notes")
    args = parser.parse_args()

    try:
        raw_dir = safe_relative_dir(args.raw_dir)
        notes_dir = safe_relative_dir(args.notes_dir)
        class_date = parse_date(args.date)
        identifier = session_id(args.lesson, class_date)
    except ValueError as exc:
        parser.error(str(exc))

    sources = [path.expanduser().resolve() for path in args.source]
    missing = [str(path) for path in sources if not path.is_file()]
    if missing:
        parser.error("source file not found: " + ", ".join(missing))

    root = args.course_root.expanduser().resolve()
    session_dir = root / raw_dir / identifier
    originals_dir = session_dir / "originals"
    metadata_path = session_dir / "session.json"
    note_name = f"{identifier}_{safe_title(args.title)}.md"
    note_relative = notes_dir / note_name

    source_records: list[dict[str, str]] = []
    seen_names: dict[str, str] = {}
    for source in sources:
        digest = sha256(source)
        previous = seen_names.get(source.name)
        if previous is not None and previous != digest:
            parser.error(f"two source files share a name but differ: {source.name}")
        seen_names[source.name] = digest
        destination = originals_dir / source.name
        if destination.exists():
            if not destination.is_file() or sha256(destination) != digest:
                parser.error(f"refusing to overwrite different file: {destination}")
        source_records.append(
            {
                "name": source.name,
                "kind": source_kind(source),
                "sha256": digest,
                "stored_path": (raw_dir / identifier / "originals" / source.name).as_posix(),
            }
        )

    try:
        existing = load_existing(metadata_path)
    except ValueError as exc:
        parser.error(str(exc))

    if existing and existing.get("note_path"):
        existing_note = Path(existing["note_path"])
        if existing_note.is_absolute() or ".." in existing_note.parts:
            parser.error("existing session metadata has an invalid note_path")
        note_relative = existing_note

    core = {
        "course": args.course.strip(),
        "session_id": identifier,
        "lesson": args.lesson,
        "date": class_date,
    }
    if not core["course"]:
        parser.error("course name cannot be empty")

    if existing:
        for key, value in core.items():
            if existing.get(key) != value:
                parser.error(f"existing session metadata conflicts on {key}")
        old_title = existing.get("title")
        if old_title and args.title and old_title != args.title:
            parser.error("existing session metadata conflicts on title")

    originals_dir.mkdir(parents=True, exist_ok=True)
    (root / notes_dir).mkdir(parents=True, exist_ok=True)
    for source in sources:
        destination = originals_dir / source.name
        if not destination.exists():
            shutil.copy2(source, destination)

    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    prior_records = {item.get("name"): item for item in (existing or {}).get("original_files", [])}
    for record in source_records:
        prior_records[record["name"]] = record

    metadata = {
        "schema_version": 1,
        "mode": "after-class",
        **core,
        "title": args.title or (existing or {}).get("title"),
        "label_source": (existing or {}).get("label_source", args.label_source),
        "raw_bundle": (raw_dir / identifier).as_posix(),
        "note_path": note_relative.as_posix(),
        "original_files": sorted(prior_records.values(), key=lambda item: item["name"]),
        "created_at": (existing or {}).get("created_at", now),
        "updated_at": now,
    }
    temporary = metadata_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(metadata_path)

    result = {
        "session_id": identifier,
        "raw_bundle": str(session_dir),
        "metadata": str(metadata_path),
        "note_path": str(root / note_relative),
        "copied_files": len(source_records),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
