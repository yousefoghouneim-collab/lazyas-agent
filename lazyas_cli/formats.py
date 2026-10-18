"""Turns a review into text an agent can act on. Every note keeps its reference, so the agent can report back
per note ("2026-10-16_031500#3: fixed, here's the proof").

Formats:
  notes      the review as the person said it (numbered, with screenshots, pointer details and status)
  fixlist    a checklist of what to fix, open items only
  criteria   acceptance criteria per note (Given / When / Then), for writing or checking tests
  playwright a test plan in the Markdown shape Playwright's planner and generator agents read
"""
from __future__ import annotations

import re
from typing import Optional

from . import status as st
from .sessions import Note, Session

FORMATS = ["notes", "fixlist", "criteria", "playwright"]

AGENT_RULES = (
    "How to work through it:\n"
    "1. Read each screenshot before changing that part (get_note or get_image returns it).\n"
    "2. Fix the notes by reference. Notes marked (general) apply to the whole thing.\n"
    "3. For every note you fix, call set_status(ref, \"claimed\", summary, evidence). Evidence is proof a person can "
    "check: a screenshot of the result (file path), test output, or the exact change. Never claim a fix you did not "
    "check.\n"
    "4. If a note can't or shouldn't be done, set_status(ref, \"wontfix\", summary=why). Ask when unsure.\n"
    "5. Only a person can mark a note verified. Don't say everything is done; say what you claimed and what's left.\n"
)


def _pointer_details(note: Note) -> list:
    out = []
    for sh in note.shots:
        bits = []
        if sh.element:
            bits.append(f"element: {sh.element}")
        if sh.near_text:
            bits.append(f'text near pointer: "{_clip(sh.near_text, 160)}"')
        if bits:
            out.append(f"{sh.name}: " + "; ".join(bits))
    return out


def _clip(s: str, n: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _first_sentence(s: str, n: int = 80) -> str:
    s = " ".join(s.split())
    m = re.split(r"(?<=[.!?])\s", s, maxsplit=1)
    return _clip(m[0] if m else s, n)


def _url(context: Optional[str]) -> Optional[str]:
    if not context:
        return None
    m = re.search(r"(https?://\S+|localhost:\d+\S*|\b[\w-]+(\.[\w-]+)+\.\w{2,}/\S*)", context)
    return m.group(0) if m else None


def render(session: Session, fmt: str = "notes", only_open: bool = False, data: Optional[dict] = None) -> str:
    fmt = fmt if fmt in FORMATS else "notes"
    data = data or st.read(session)
    notes = session.notes
    if fmt == "fixlist" or only_open:
        notes = [n for n in notes if st.status_of(session, n.id, data) not in st.DONE | st.AWAITING]
    head = (f"# Lazy as review: {session.title}\n"
            f"Session: {session.key} · {len(session.notes)} notes · screenshots in {session.folder}\n")
    counts = st.summary_counts(session, data)
    done = {k: v for k, v in counts.items() if v and k != "open"}
    if done:
        head += "Status: " + ", ".join(f"{v} {st.LABELS[k].lower()}" for k, v in done.items()) + "\n"
    if fmt == "notes":
        return head + "\n" + _notes(session, notes, data)
    if fmt == "fixlist":
        return head + "\n" + _fixlist(session, notes)
    if fmt == "criteria":
        return head + "\n" + _criteria(session, notes)
    return _playwright(session, notes)


def _notes(session: Session, notes: list, data: dict) -> str:
    out = []
    last_ctx = None
    for n in notes:
        e = st.entry(session, n.id, data)
        tag = "" if e["status"] == "open" else f" [{st.LABELS[e['status']]}]"
        line = f"{session.ref(n)}{tag} "
        if n.general:
            line += "(general) "
        elif n.context and n.context != last_ctx:
            line += f"[{n.context}] "
            last_ctx = n.context
        out.append(line + n.text.strip())
        for sh in n.shots:
            out.append(f"   - {sh.file}" + (" (whole window, nothing pointed at)" if sh.whole else "")
                       + (f': "{sh.quote}"' if sh.quote else ""))
        for d in _pointer_details(n):
            out.append(f"   - {d}")
        if e["status"] == "reopened" and e["history"]:
            why = next((h.get("note") for h in reversed(e["history"]) if h.get("status") == "reopened"), None)
            if why:
                out.append(f"   - reopened because: {why}")
        if e["summary"] and e["status"] in ("claimed", "verified", "wontfix"):
            out.append(f"   - agent said: {_clip(e['summary'], 300)}")
    return "\n".join(out) + "\n"


def _fixlist(session: Session, notes: list) -> str:
    if not notes:
        return "Nothing left to fix: every note is claimed, verified or won't fix.\n"
    out = []
    general = [n for n in notes if n.general]
    for n in general + [n for n in notes if not n.general]:
        where = "everywhere" if n.general else (n.context or "")
        out.append(f"- [ ] {session.ref(n)}" + (f" ({where})" if where else "") + f": {n.text.strip()}")
        for sh in n.shots:
            out.append(f"      screenshot {sh.file}" + (f' while saying "{sh.quote}"' if sh.quote else ""))
        for d in _pointer_details(n):
            out.append(f"      {d}")
    return "\n".join(out) + "\n"


def _criteria(session: Session, notes: list) -> str:
    out = ["Acceptance criteria, one block per note. Then is the reviewer's own words: turn it into a precise, "
           "testable expectation before using it.\n"]
    for n in notes:
        sh = n.shots[0] if n.shots else None
        where = "the whole app" if n.general else (n.context or "the app")
        target = (sh.element if sh and sh.element else (f"the area shown in {sh.name}" if sh else "the page"))
        out.append(f"## {session.ref(n)}: {_first_sentence(n.text)}")
        out.append(f"Given I am on {where}")
        out.append(f"When I look at {target}")
        out.append(f"Then {n.text.strip()}")
        if sh:
            out.append(f"Reference: {sh.file}")
        out.append("")
    return "\n".join(out)


def _playwright(session: Session, notes: list) -> str:
    out = [f"# {session.title}: test plan", "",
           f"Written from Lazy as review {session.key}. Each scenario checks one note; the screenshot shows what was "
           "wrong. Expected results are the reviewer's words: make them exact before generating tests.", "",
           "## Test Scenarios", ""]
    for i, n in enumerate([n for n in notes if not n.general], 1):
        sh = n.shots[0] if n.shots else None
        url = _url(n.context)
        out.append(f"### {i}. {session.ref(n)}: {_first_sentence(n.text, 70)}")
        out.append("")
        out.append("**Steps:**")
        k = 1
        if url:
            out.append(f"{k}. Navigate to {url if url.startswith('http') else 'http://' + url}")
            k += 1
        elif n.context:
            out.append(f"{k}. Open {n.context}")
            k += 1
        if sh and sh.element:
            out.append(f"{k}. Locate {sh.element}")
        elif sh and sh.near_text:
            out.append(f'{k}. Locate the part of the page showing "{_clip(sh.near_text, 80)}"')
        else:
            out.append(f"{k}. Locate the area shown in {sh.name if sh else 'the review'}")
        out.append("")
        out.append(f"**Expected Results:** {n.text.strip()}")
        if sh:
            out.append(f"Reference screenshot: {sh.file}")
        out.append("")
    general = [n for n in notes if n.general]
    if general:
        out.append("## Applies to every scenario")
        out.extend(f"- {session.ref(n)}: {n.text.strip()}" for n in general)
        out.append("")
    return "\n".join(out)
