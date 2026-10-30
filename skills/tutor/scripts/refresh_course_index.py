#!/usr/bin/env python3
"""Regenerate the after-class course index from session metadata and note frontmatter."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


MARKER = "<!-- tutor:index:v1 -->"


def safe_relative_dir(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"directory must be relative to course root: {value}")
    return path


def unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def parse_frontmatter(path: Path) -> dict[str, str | list[str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    data: dict[str, str | list[str]] = {}
    current_list: str | None = None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        list_item = re.match(r"^\s+-\s+(.+?)\s*$", line)
        if list_item and current_list:
            value = data.setdefault(current_list, [])
            if isinstance(value, list):
                value.append(unquote(list_item.group(1)))
            continue
        field = re.match(r"^([A-Za-z][A-Za-z0-9_-]*):\s*(.*?)\s*$", line)
        if not field:
            current_list = None
            continue
        key, value = field.groups()
        if value:
            data[key] = unquote(value)
            current_list = None
        else:
            data[key] = []
            current_list = key
    return data


def display(value: object, fallback: str = "—") -> str:
    if value is None or value == "":
        return fallback
    if isinstance(value, list):
        return "、".join(str(item) for item in value) or fallback
    return str(value)


def cell(value: object) -> str:
    return display(value).replace("|", "\\|").replace("\n", " ")


def markdown_link(label: str, target: Path) -> str:
    return f"[{label}](<{target.as_posix()}>)"


def sort_key(item: dict) -> tuple:
    lesson = item.get("lesson")
    class_date = item.get("date") or ""
    if isinstance(lesson, int):
        return (0, lesson, class_date, item.get("session_id", ""))
    return (1, class_date, item.get("session_id", ""))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--course-root", required=True, type=Path)
    parser.add_argument("--raw-dir", default="01_raw")
    parser.add_argument("--index-name", default="00_course_index.md")
    args = parser.parse_args()

    try:
        raw_dir = safe_relative_dir(args.raw_dir)
        index_relative = safe_relative_dir(args.index_name)
    except ValueError as exc:
        parser.error(str(exc))

    root = args.course_root.expanduser().resolve()
    index_path = root / index_relative
    if index_path.exists() and not index_path.read_text(encoding="utf-8").startswith(MARKER):
        parser.error(f"refusing to overwrite unmanaged index: {index_path}")

    sessions: list[dict] = []
    for metadata_path in sorted((root / raw_dir).glob("*/session.json")):
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            parser.error(f"cannot read session metadata {metadata_path}: {exc}")
        if not isinstance(metadata, dict) or not metadata.get("session_id"):
            parser.error(f"invalid session metadata: {metadata_path}")

        note_relative = Path(metadata.get("note_path", ""))
        if note_relative.is_absolute() or ".." in note_relative.parts or not note_relative.parts:
            parser.error(f"invalid note_path in {metadata_path}")
        note_path = root / note_relative
        frontmatter = parse_frontmatter(note_path) if note_path.is_file() else {}
        for key in ("course", "mode", "session_id", "lesson", "date"):
            frontmatter_value = frontmatter.get(key)
            metadata_value = metadata.get(key)
            if frontmatter_value not in (None, "") and str(frontmatter_value) != str(metadata_value):
                parser.error(f"{key} mismatch in note: {note_path}")

        raw_relative = Path(metadata.get("raw_bundle", raw_dir / metadata["session_id"]))
        if raw_relative.is_absolute() or ".." in raw_relative.parts or not raw_relative.parts:
            parser.error(f"invalid raw_bundle in {metadata_path}")

        sessions.append(
            {
                **metadata,
                "title": frontmatter.get("title") or metadata.get("title") or "课程笔记",
                "learning_modes": frontmatter.get("learning_modes", []),
                "note_relative": note_relative,
                "note_exists": note_path.is_file(),
                "raw_relative": raw_relative,
            }
        )

    sessions.sort(key=sort_key)
    course_names = {item.get("course") for item in sessions if item.get("course")}
    heading = next(iter(course_names)) if len(course_names) == 1 else "课程"
    lines = [
        MARKER,
        f"# {heading}笔记索引",
        "",
        "> 本文件由 `tutor` 自动生成。请修改各课笔记或会话元数据后重新刷新索引。",
        "",
        "| 课次 | 日期 | 主题 | 分类 | 状态 | 笔记 | 原始材料 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in sessions:
        lesson = f"第 {item['lesson']} 课" if isinstance(item.get("lesson"), int) else "—"
        status = "已完成" if item["note_exists"] else "待生成"
        note_link = markdown_link("查看", item["note_relative"]) if item["note_exists"] else "—"
        raw_link = markdown_link("查看", item["raw_relative"])
        lines.append(
            "| "
            + " | ".join(
                [
                    cell(lesson),
                    cell(item.get("date")),
                    cell(item.get("title")),
                    cell(item.get("learning_modes")),
                    status,
                    note_link,
                    raw_link,
                ]
            )
            + " |"
        )
    lines.append("")

    index_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = index_path.with_suffix(index_path.suffix + ".tmp")
    temporary.write_text("\n".join(lines), encoding="utf-8")
    temporary.replace(index_path)
    print(json.dumps({"index": str(index_path), "sessions": len(sessions)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
