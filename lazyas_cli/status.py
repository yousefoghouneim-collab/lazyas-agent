"""Note statuses: the fix-and-verify loop. Stored per session in review.json, next to session.json.

    open ──sent──▶ claimed (the agent says it's fixed, with proof) ──▶ verified (only a person)
                         └──────────────────────────────────────────▶ reopened ──▶ claimed …
    any ──▶ wontfix (with a reason)

Agents may set: sent, claimed, wontfix. People (the apps, or `lazyas verify/reopen`) may set anything.
Every change is kept in the note's history, so the record of asked → fixed → verified survives.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Optional

from .sessions import Session, atomic_write, now_iso

STATUSES = ["open", "sent", "claimed", "verified", "reopened", "wontfix"]
AGENT_STATUSES = {"sent", "claimed", "wontfix"}
OPEN = {"open", "sent", "reopened"}            # still to do
AWAITING = {"claimed"}                          # waiting for a person to check
DONE = {"verified", "wontfix"}

LABELS = {"open": "Open", "sent": "Sent to agent", "claimed": "Claimed fixed", "verified": "Verified",
          "reopened": "Reopened", "wontfix": "Won't fix"}


class StatusError(ValueError):
    pass


def path(session: Session) -> Path:
    return session.folder / "review.json"


def read(session: Session) -> dict:
    try:
        data = json.loads(path(session).read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("notes"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"version": 1, "notes": {}}


def entry(session: Session, note_id: int, data: Optional[dict] = None) -> dict:
    d = (data or read(session))["notes"].get(str(note_id)) or {}
    return {"status": d.get("status", "open"), "summary": d.get("summary"), "by": d.get("by"),
            "evidence": d.get("evidence", []), "history": d.get("history", []), "updated": d.get("updated")}


def status_of(session: Session, note_id: int, data: Optional[dict] = None) -> str:
    return entry(session, note_id, data)["status"]


class _Lock:
    """A tiny cross-process lock (an exclusive lock file), so an agent and the app never write at once."""

    def __init__(self, target: Path):
        self.file = target.with_name(target.name + ".lock")

    def __enter__(self):
        deadline = time.time() + 5
        while True:
            try:
                fd = os.open(self.file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                return self
            except FileExistsError:
                try:                                  # a lock left by a crashed process
                    if time.time() - self.file.stat().st_mtime > 30:
                        self.file.unlink()
                        continue
                except OSError:
                    pass
                if time.time() > deadline:
                    raise StatusError("review.json is busy; try again")
                time.sleep(0.05)

    def __exit__(self, *exc):
        try:
            self.file.unlink()
        except OSError:
            pass


def set_status(session: Session, note_id: int, status: str, *, by: str = "person", summary: Optional[str] = None,
               evidence: Optional[list] = None, agent: bool = False, note: Optional[str] = None) -> dict:
    """Changes a note's status and records it. `agent=True` limits what can be set (only people verify).

    evidence: list of {"text": "..."} and/or {"file": "/path/to/after.png"} (files are copied into the session).
    """
    status = status.strip().lower().replace(" ", "").replace("_", "").replace("-", "")
    status = {"fixed": "claimed", "done": "claimed", "claimedfixed": "claimed", "wontdo": "wontfix",
              "inprogress": "sent", "started": "sent"}.get(status, status)
    if status not in STATUSES:
        raise StatusError(f"Unknown status {status!r}. Use one of: {', '.join(STATUSES)}")
    if agent and status not in AGENT_STATUSES:
        raise StatusError("Agents can set sent, claimed or wontfix. Only a person can verify or reopen a note.")
    if session.note(note_id) is None:
        raise StatusError(f"Session {session.key} has no note #{note_id}")
    if status == "wontfix" and not (summary or note):
        raise StatusError("Say why it won't be fixed (summary).")
    target = path(session)
    with _Lock(target):
        data = read(session)
        cur = data["notes"].setdefault(str(note_id), {})
        when = now_iso()
        items = [_store_evidence(session, note_id, e, when, by) for e in (evidence or [])]
        items = [e for e in items if e]
        cur.setdefault("evidence", []).extend(items)
        cur.setdefault("history", []).append({"status": status, "at": when, "by": by,
                                              **({"note": note} if note else {}),
                                              **({"summary": summary} if summary else {})})
        cur["status"] = status
        cur["updated"] = when
        cur["by"] = by
        if summary:
            cur["summary"] = summary
        if status == "reopened" and note:
            cur["reopenReason"] = note
        atomic_write(target, json.dumps(data, indent=2, ensure_ascii=False))
    return entry(session, note_id, data)


def add_evidence(session: Session, note_id: int, evidence: list, by: str = "agent") -> dict:
    target = path(session)
    with _Lock(target):
        data = read(session)
        cur = data["notes"].setdefault(str(note_id), {"status": "open"})
        when = now_iso()
        items = [e for e in (_store_evidence(session, note_id, e, when, by) for e in evidence) if e]
        cur.setdefault("evidence", []).extend(items)
        cur["updated"] = when
        atomic_write(target, json.dumps(data, indent=2, ensure_ascii=False))
    return entry(session, note_id, data)


def _store_evidence(session: Session, note_id: int, e, when: str, by: str) -> Optional[dict]:
    if isinstance(e, str):
        e = {"text": e}
    if not isinstance(e, dict):
        return None
    out = {"at": when, "by": by}
    text = (e.get("text") or "").strip()
    if text:
        out["text"] = text[:4000]
    src = e.get("file")
    if src:
        p = Path(str(src)).expanduser()
        if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
            folder = session.folder / "evidence"
            folder.mkdir(exist_ok=True)
            n = len(list(folder.glob(f"{note_id:02d}-*"))) + 1
            dest = folder / f"{note_id:02d}-{n}{p.suffix.lower()}"
            shutil.copyfile(p, dest)
            out["file"] = str(dest)
        else:
            out["text"] = (out.get("text", "") + f" (attachment not found or not an image: {src})").strip()
    return out if ("text" in out or "file" in out) else None


def needs_proof(session: Session, data: Optional[dict] = None) -> list:
    """Notes an agent claimed fixed without any evidence."""
    data = data or read(session)
    out = []
    for n in session.notes:
        e = entry(session, n.id, data)
        if e["status"] == "claimed" and not e["evidence"]:
            out.append(n)
    return out


def summary_counts(session: Session, data: Optional[dict] = None) -> dict:
    data = data or read(session)
    counts = {s: 0 for s in STATUSES}
    for n in session.notes:
        counts[status_of(session, n.id, data)] += 1
    return counts
