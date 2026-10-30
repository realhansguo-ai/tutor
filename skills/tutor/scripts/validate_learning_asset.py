#!/usr/bin/env python3
"""Check deterministic Markdown, math-delimiter, and local media rules for learning assets."""

from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote


FORBIDDEN = {
    r"\[": r"use $$ on its own line instead of \\[",
    r"\]": r"use $$ on its own line instead of \\]",
    r"\newcommand": "custom LaTeX macros are not portable",
    r"\DeclareMathOperator": "custom LaTeX operators are not portable",
}

LINK = re.compile(r"!?\[[^\]]*\]\(\s*(<[^>]+>|[^)\s]+)(?:\s+[\"'][^)]*)?\)")
# Test transcripts and snapshots are evidence, not source assets; skip them unless asked.
DEFAULT_EXCLUDES = (("evals", "results"),)
EXTERNAL_ASSET = re.compile(r"<(?:script|link)\b[^>]*\b(?:src|href)\s*=\s*[\"']?(?:https?:)?//", re.IGNORECASE)


def local_target(raw: str) -> str | None:
    target = raw.strip("<>")
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target) or target.startswith(("#", "//")):
        return None
    target = unquote(target.split("#", 1)[0].split("?", 1)[0])
    return target or None


def check_media(path: Path) -> list[str]:
    suffix = path.suffix.lower()
    if suffix == ".svg":
        try:
            ET.parse(path)
        except ET.ParseError as exc:
            return [f"{path}: SVG is not well-formed XML ({exc})"]
    elif suffix in {".html", ".htm"}:
        text = path.read_text(encoding="utf-8", errors="replace")
        if EXTERNAL_ASSET.search(text):
            return [f"{path}: HTML loads an external script or stylesheet; inline it so the file works offline"]
    return []


def excluded(path: Path, root: Path, excludes: tuple[tuple[str, ...], ...]) -> bool:
    parts = path.relative_to(root).parts
    return any(
        parts[index : index + len(pattern)] == pattern
        for pattern in excludes
        for index in range(len(parts) - len(pattern) + 1)
    )


def walk(root: Path, pattern: str, excludes: tuple[tuple[str, ...], ...]) -> list[Path]:
    return sorted(p for p in root.rglob(pattern) if p.is_file() and not excluded(p, root, excludes))


def markdown_files(paths: list[Path], excludes: tuple[tuple[str, ...], ...]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(walk(path, "*.md", excludes))
        elif path.is_file() and path.suffix.lower() == ".md":
            files.append(path)
        else:
            raise ValueError(f"not a Markdown file or directory: {path}")
    return list(dict.fromkeys(files))


def unescaped_dollars(line: str) -> list[int]:
    positions: list[int] = []
    for index, char in enumerate(line):
        if char != "$":
            continue
        backslashes = 0
        cursor = index - 1
        while cursor >= 0 and line[cursor] == "\\":
            backslashes += 1
            cursor -= 1
        if backslashes % 2 == 0:
            positions.append(index)
    return positions


def check_file(path: Path, media_targets: set[Path]) -> list[str]:
    issues: list[str] = []
    in_fence = False
    in_block_math = False

    for number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = raw_line.strip()

        if stripped.startswith("```"):
            if stripped.startswith("```math"):
                issues.append(f"{path}:{number}: use $$ blocks instead of ```math")
            in_fence = not in_fence
            continue
        if in_fence:
            continue

        line = re.sub(r"`[^`]*`", "", raw_line)
        stripped = line.strip()

        for match in LINK.finditer(line):
            target = local_target(match.group(1))
            if target is None:
                continue
            resolved = (path.parent / target).resolve()
            if not resolved.exists():
                issues.append(f"{path}:{number}: local link target not found: {target}")
            elif resolved.is_file() and resolved.suffix.lower() in {".svg", ".html", ".htm"}:
                media_targets.add(resolved)

        if stripped == "$$":
            in_block_math = not in_block_math
            continue

        if "$$" in line:
            issues.append(f"{path}:{number}: block $$ delimiters must be on their own lines")
            continue

        for token, message in FORBIDDEN.items():
            if token in line:
                issues.append(f"{path}:{number}: {message}")

        if in_block_math:
            continue

        dollars = unescaped_dollars(line)
        if len(dollars) % 2:
            issues.append(f"{path}:{number}: unpaired inline $ delimiter")
            continue

        for start, end in zip(dollars[::2], dollars[1::2]):
            if start + 1 == end:
                issues.append(f"{path}:{number}: empty inline math expression")
            if start > 0 and not line[start - 1].isspace():
                issues.append(f"{path}:{number}: add a space before inline math")
            if end + 1 < len(line) and not line[end + 1].isspace():
                issues.append(f"{path}:{number}: add a space after inline math")

    if in_fence:
        issues.append(f"{path}: unclosed fenced code block")
    if in_block_math:
        issues.append(f"{path}: unclosed $$ math block")
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path, help="Markdown files or directories")
    parser.add_argument(
        "--include-eval-results",
        action="store_true",
        help="also scan evals/results (skipped by default because it holds raw test transcripts)",
    )
    args = parser.parse_args()
    excludes = () if args.include_eval_results else DEFAULT_EXCLUDES

    try:
        files = markdown_files(args.paths, excludes)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if not files:
        print("ERROR: no Markdown files found", file=sys.stderr)
        return 2

    media: set[Path] = set()
    issues = [issue for path in files for issue in check_file(path, media)]
    for root in args.paths:
        if root.is_dir():
            media.update(p.resolve() for p in walk(root, "*", excludes) if p.suffix.lower() in {".svg", ".html", ".htm"})
    for item in sorted(media):
        issues.extend(check_media(item))
    if issues:
        print("\n".join(issues))
        print(f"FAILED: {len(issues)} issue(s) in {len(files)} file(s)")
        return 1

    print(f"OK: checked {len(files)} Markdown file(s) and {len(media)} linked or bundled media file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
