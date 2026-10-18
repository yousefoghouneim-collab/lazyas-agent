"""Project routing: which reviews belong to which code folder.

A project says which pages are its own in a .lazyas.json file at its root (`lazyas link localhost:3000`):

    {"name": "Shop", "match": ["localhost:3000", "shop.example.com", "Shop – Figma"]}

A session belongs to the project when any note's page or window ("Chrome · http://localhost:3000/cart") contains
one of those strings (case-insensitive). Agents running in that folder then get that project's latest review.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .sessions import Session, all_sessions, atomic_write

FILE = ".lazyas.json"


def find_config(start: Optional[Path]) -> Optional[Path]:
    if not start:
        return None
    p = Path(start).expanduser().resolve()
    for folder in [p, *p.parents]:
        f = folder / FILE
        if f.is_file():
            return f
    return None


def load(start: Optional[Path]) -> Optional[dict]:
    f = find_config(start)
    if not f:
        return None
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    match = [m.strip() for m in data.get("match", []) if isinstance(m, str) and m.strip()]
    return {"name": data.get("name") or f.parent.name, "match": match, "folder": str(f.parent),
            "reviews": data.get("reviews", ".lazyas/reviews")}


def link(folder: Path, matches: list, name: Optional[str] = None) -> Path:
    f = Path(folder) / FILE
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    have = data.get("match", [])
    for m in matches:
        m = m.strip()
        if m and m not in have:
            have.append(m)
    data["match"] = have
    data["name"] = name or data.get("name") or Path(folder).resolve().name
    atomic_write(f, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return f


def matches(session: Session, project: dict) -> bool:
    keys = [m.lower() for m in project.get("match", [])]
    if not keys:
        return False
    for c in session.contexts():
        low = c.lower()
        if any(k in low for k in keys):
            return True
    return False


def sessions_for(project: Optional[dict], limit: int = 50) -> list:
    """Sessions that belong to the project (newest first). No project, or none match: all sessions."""
    found = all_sessions(limit=limit)
    if not project or not project.get("match"):
        return found
    return [s for s in found if matches(s, project)]


def latest_for(start: Optional[Path]) -> tuple:
    """(session, project, matched): the project's newest review, or the newest overall when nothing matches."""
    project = load(start)
    if project:
        mine = sessions_for(project)
        if mine:
            return mine[0], project, True
    found = all_sessions(limit=1)
    return (found[0] if found else None), project, False
