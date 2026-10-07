"""Turn existing knowledge (Markdown docs, JSON exports, CSV sheets) into memory items.

The result is a list of dicts accepted by ``MemoryClient.remember_many`` /
``POST /api/memories/bulk``. Long Markdown documents are split by headings so
each memory stays focused (better embeddings and more precise retrieval).
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any, Iterable

MAX_CONTENT = 19000  # stay below the server's default 20 000 character limit
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FRONT_MATTER = re.compile(r"^---\s*\n.*?\n---\s*\n", re.S)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "section"


def _tag(value: str) -> str | None:
    t = re.sub(r"[^a-z0-9_.\-]+", "-", value.strip().lower()).strip("-")[:50]
    return t or None


def _chunks(text: str, limit: int = MAX_CONTENT) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts, current = [], ""
    for para in re.split(r"\n\s*\n", text):
        if len(current) + len(para) + 2 > limit and current:
            parts.append(current.strip())
            current = ""
        while len(para) > limit:
            parts.append(para[:limit])
            para = para[limit:]
        current += para + "\n\n"
    if current.strip():
        parts.append(current.strip())
    return parts


def from_markdown(
    text: str,
    source_name: str = "document.md",
    *,
    split: str = "heading",
    level: int = 2,
    memory_type: str = "documentation",
    tags: Iterable[str] | None = None,
    min_chars: int = 40,
    **defaults: Any,
) -> list[dict]:
    """Split a Markdown document into memories.

    ``split="heading"`` creates one memory per heading of depth <= ``level``
    (title = "<document title> — <section>"); ``split="file"`` keeps one memory
    per file. Sections shorter than ``min_chars`` are merged away.
    """
    text = _FRONT_MATTER.sub("", text.replace("\r\n", "\n")).strip()
    lines = text.split("\n")
    doc_title = Path(source_name).stem.replace("-", " ").replace("_", " ").strip().capitalize()
    for line in lines:
        m = _HEADING.match(line)
        if m and len(m.group(1)) == 1:
            doc_title = m.group(2).strip()
            break
    base_tags = [t for t in (_tag(x) for x in (tags or [])) if t]

    sections: list[tuple[str, str]] = []
    if split == "file":
        sections = [(doc_title, text)]
    else:
        heading, buf = None, []
        for line in lines:
            m = _HEADING.match(line)
            if m and len(m.group(1)) <= level and len(m.group(1)) > 1:
                if buf:
                    sections.append((heading or doc_title, "\n".join(buf).strip()))
                heading, buf = m.group(2).strip(), []
            elif m and len(m.group(1)) == 1:
                continue  # the document title itself
            else:
                buf.append(line)
        if buf:
            sections.append((heading or doc_title, "\n".join(buf).strip()))

    items: list[dict] = []
    for heading, body in sections:
        if len(body) < min_chars:
            continue
        title_base = heading if heading == doc_title else f"{doc_title} — {heading}"
        parts = _chunks(body)
        for i, part in enumerate(parts):
            title = title_base if len(parts) == 1 else f"{title_base} (part {i + 1})"
            items.append({
                "title": title[:200] if len(title) >= 3 else f"{title} doc",
                "content": part,
                "memory_type": memory_type,
                "tags": base_tags,
                "source": "import",
                "source_ref": f"{source_name}#{_slug(heading)}"[:255],
                **defaults,
            })
    return items


def from_records(records: Iterable[dict], **defaults: Any) -> list[dict]:
    """Normalise dict records (from JSON or CSV) into memory items."""
    items = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        item = {**defaults, **{k: v for k, v in rec.items() if v not in (None, "")}}
        if isinstance(item.get("tags"), str):
            item["tags"] = [t for t in (_tag(x) for x in re.split(r"[;,]", item["tags"])) if t]
        for key in ("importance", "confidence"):
            if isinstance(item.get(key), str):
                try:
                    item[key] = float(item[key])
                except ValueError:
                    item.pop(key)
        if "title" in item and "content" in item:
            items.append(item)
    return items


def from_json(text: str, **defaults: Any) -> list[dict]:
    data = json.loads(text)
    if isinstance(data, dict):
        data = data.get("memories") or data.get("items") or [data]
    return from_records(data, **defaults)


def from_csv(text: str, **defaults: Any) -> list[dict]:
    return from_records(csv.DictReader(text.splitlines()), **defaults)


def load_path(path: str | Path, **options: Any) -> list[dict]:
    """Load a file or (recursively) a directory of .md/.markdown/.txt/.json/.csv files."""
    path = Path(path)
    if path.is_dir():
        items: list[dict] = []
        for child in sorted(path.rglob("*")):
            if child.is_file() and child.suffix.lower() in {".md", ".markdown", ".txt", ".json", ".csv"}:
                items.extend(load_path(child, **options))
        return items
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    md_opts = {k: v for k, v in options.items() if k in {"split", "level", "memory_type", "tags", "min_chars", "visibility", "project_id", "sensitivity"}}
    rec_opts = {k: v for k, v in options.items() if k in {"memory_type", "visibility", "project_id", "sensitivity"}}
    if suffix == ".json":
        return from_json(text, **rec_opts)
    if suffix == ".csv":
        return from_csv(text, **rec_opts)
    return from_markdown(text, source_name=path.name, **md_opts)
