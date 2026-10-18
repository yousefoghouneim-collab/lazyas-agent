"""The Lazy as MCP server (stdio, JSON-RPC 2.0, no dependencies). Lets any agent read your reviews and report back.

Reading is always free. Writing statuses back (set_status, add_evidence) is Pro once paid plans are on.
Run: `lazyas mcp` (Claude Code plugin, Codex, Cursor, Gemini CLI, Copilot CLI: see `lazyas setup`).
"""
from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

from . import __version__, formats, projects
from . import license as lic
from . import status as st
from .sessions import Session, all_sessions, find_note, find_session, now_iso

SUPPORTED = ["2025-06-18", "2025-03-26", "2024-11-05"]
MAX_IMAGES = 8

INSTRUCTIONS = (
    "Lazy as holds review notes a person recorded by pointing at their screen and talking. Each note has a "
    "reference like 2026-10-16_031500#3, a screenshot, and a status (open, sent, claimed, verified, reopened, "
    "wontfix). Start with get_latest_review. After fixing a note call set_status with status 'claimed', a one-line "
    "summary and evidence (a screenshot path of the result, test output, or the exact change). Never claim a fix "
    "you didn't check. Only the person can mark a note verified."
)

REF = {"type": "string", "description": "Note reference, e.g. 2026-10-16_031500#3 (latest#3 = note 3 of the newest review)"}
TOOLS = [
    {"name": "get_latest_review",
     "description": "The newest review for this project (matched by its .lazyas.json, otherwise the newest overall), "
                    "with every note's reference, words, screenshots and status, plus how to report back.",
     "inputSchema": {"type": "object", "properties": {
         "project": {"type": "string", "description": "Path of the project folder (defaults to the current folder)"},
         "format": {"type": "string", "enum": formats.FORMATS, "description": "notes (default), fixlist, criteria, playwright"},
         "include_images": {"type": "boolean", "description": f"Attach up to {MAX_IMAGES} screenshots (default false)"}}}},
    {"name": "list_reviews", "description": "Recent review sessions with their status counts.",
     "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 50}}}},
    {"name": "get_review", "description": "One review session by its key (the folder name, e.g. 2026-10-16_031500).",
     "inputSchema": {"type": "object", "required": ["session"], "properties": {
         "session": {"type": "string"}, "format": {"type": "string", "enum": formats.FORMATS},
         "include_images": {"type": "boolean"}}}},
    {"name": "list_open", "description": "Notes still to do (open, sent or reopened) across recent reviews of this project.",
     "inputSchema": {"type": "object", "properties": {"project": {"type": "string"}}}},
    {"name": "get_note", "description": "One note in full: words, page, pointer details, status, history and its screenshots.",
     "inputSchema": {"type": "object", "required": ["ref"], "properties": {"ref": REF}}},
    {"name": "get_image", "description": "A note's screenshot (index 0 is the first) or one of its evidence images.",
     "inputSchema": {"type": "object", "required": ["ref"], "properties": {
         "ref": REF, "index": {"type": "integer", "minimum": 0},
         "evidence": {"type": "boolean", "description": "Return evidence images instead of the original screenshots"}}}},
    {"name": "set_status",
     "description": "Report on a note: 'sent' (you picked it up), 'claimed' (you fixed it: include evidence), or "
                    "'wontfix' (summary says why). Only a person can verify.",
     "inputSchema": {"type": "object", "required": ["ref", "status"], "properties": {
         "ref": REF, "status": {"type": "string", "enum": ["sent", "claimed", "wontfix"]},
         "summary": {"type": "string", "description": "One line: what you changed, or why not"},
         "evidence": {"type": "array", "description": "Proof: {\"text\": ...} and/or {\"file\": \"/abs/path/after.png\"}",
                      "items": {"type": "object", "properties": {"text": {"type": "string"}, "file": {"type": "string"}}}}}}},
    {"name": "add_evidence", "description": "Attach more proof to a note (a screenshot path or text).",
     "inputSchema": {"type": "object", "required": ["ref"], "properties": {
         "ref": REF, "text": {"type": "string"}, "file": {"type": "string"}}}},
]


class Server:
    def __init__(self, cwd: Optional[str] = None):
        self.cwd = cwd or os.getcwd()
        self.client = "agent"

    # JSON-RPC plumbing

    def handle(self, msg: dict) -> Optional[dict]:
        method, mid = msg.get("method"), msg.get("id")
        if method is None:                      # a response to something we never send: ignore
            return None
        try:
            if method == "initialize":
                result = self._initialize(msg.get("params") or {})
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                result = self._call(msg.get("params") or {})
            elif method in ("resources/list", "prompts/list"):
                result = {method.split("/")[0]: []}
            elif method.startswith("notifications/"):
                return None
            else:
                return None if mid is None else _error(mid, -32601, f"Unknown method {method}")
        except Exception as e:                    # never crash the agent's session
            return None if mid is None else _error(mid, -32603, f"{type(e).__name__}: {e}")
        return None if mid is None else {"jsonrpc": "2.0", "id": mid, "result": result}

    def _initialize(self, params: dict) -> dict:
        name = (params.get("clientInfo") or {}).get("name")
        if name:
            self.client = str(name)[:60]
        asked = params.get("protocolVersion")
        return {"protocolVersion": asked if asked in SUPPORTED else SUPPORTED[0],
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "lazyas", "title": "Lazy as", "version": __version__},
                "instructions": INSTRUCTIONS}

    def _call(self, params: dict) -> dict:
        name, args = params.get("name"), params.get("arguments") or {}
        fn = getattr(self, f"t_{name}", None)
        if not fn:
            return _fail(f"Unknown tool {name}")
        try:
            return fn(args)
        except (LookupError, ValueError, st.StatusError) as e:
            return _fail(str(e))

    # Tools

    def _project_dir(self, args: dict) -> str:
        return str(Path(args.get("project") or self.cwd).expanduser())

    def _review(self, s: Session, args: dict, note: str = "") -> dict:
        fmt = args.get("format") or "notes"
        if fmt in ("criteria", "playwright"):
            blocked = lic.pro_required("The criteria and Playwright formats")
            if blocked:
                return _fail(blocked)
        _remember(self._project_dir(args), s.key)
        text = (note + formats.render(s, fmt) + "\n" + formats.AGENT_RULES)
        if s.prompt and fmt == "notes":
            text += "\nThe person also tidied this review into a prompt (same notes, shorter):\n\n" + s.prompt
        content = [{"type": "text", "text": text}]
        if args.get("include_images"):
            content += _images([sh.file for n in s.notes for sh in n.shots][:MAX_IMAGES])
        return {"content": content}

    def t_get_latest_review(self, args: dict) -> dict:
        s, project, matched = projects.latest_for(Path(self._project_dir(args)))
        if not s:
            return _fail("No reviews yet. Record one with Lazy as (hold the shortcut, point, talk).")
        note = ""
        if project and not matched:
            note = (f"(No review mentions this project's pages {project['match']}; showing the newest review "
                    "overall. Check it's the right one.)\n\n")
        return self._review(s, args, note)

    def t_get_review(self, args: dict) -> dict:
        s = find_session(str(args.get("session", "")))
        if not s:
            return _fail(f"No review called {args.get('session')!r}. Use list_reviews.")
        return self._review(s, args)

    def t_list_reviews(self, args: dict) -> dict:
        limit = max(1, min(50, int(args.get("limit") or 10)))
        lines = []
        for s in all_sessions(limit=limit):
            c = st.summary_counts(s)
            state = ", ".join(f"{v} {st.LABELS[k].lower()}" for k, v in c.items() if v)
            lines.append(f"- {s.key}: {s.title} ({state})")
        return _ok("\n".join(lines) or "No reviews yet.")

    def t_list_open(self, args: dict) -> dict:
        project = projects.load(Path(self._project_dir(args)))
        lines = []
        for s in projects.sessions_for(project, limit=30):
            data = st.read(s)
            for n in s.notes:
                state = st.status_of(s, n.id, data)
                if state in st.OPEN:
                    tag = "" if state == "open" else f" [{st.LABELS[state]}]"
                    lines.append(f"- {s.ref(n)}{tag}: {n.text.strip()[:200]}")
        return _ok("\n".join(lines) or "Nothing open. Every note is claimed, verified or won't fix.")

    def t_get_note(self, args: dict) -> dict:
        s, n = find_note(str(args.get("ref", "")))
        e = st.entry(s, n.id)
        out = [f"{s.ref(n)} · status: {st.LABELS[e['status']]}",
               "General comment (applies to the whole thing)" if n.general else f"Page: {n.context or 'unknown'}",
               f"Said: {n.text.strip()}"]
        for i, sh in enumerate(n.shots):
            bits = [f"screenshot {i}: {sh.file}" + (" (whole window, nothing pointed at)" if sh.whole else "")]
            if sh.quote:
                bits.append(f'while saying "{sh.quote}"')
            if sh.element:
                bits.append(f"element: {sh.element}")
            if sh.near_text:
                bits.append(f'text near pointer: "{sh.near_text[:200]}"')
            out.append(" · ".join(bits))
        for h in e["history"]:
            out.append(f"history: {h.get('at')} {h.get('by')} → {h.get('status')}"
                       + (f": {h.get('note') or h.get('summary')}" if h.get("note") or h.get("summary") else ""))
        for ev in e["evidence"]:
            out.append("evidence: " + (ev.get("text") or "") + (f" [{ev['file']}]" if ev.get("file") else ""))
        return {"content": [{"type": "text", "text": "\n".join(out)}] + _images([sh.file for sh in n.shots][:4])}

    def t_get_image(self, args: dict) -> dict:
        s, n = find_note(str(args.get("ref", "")))
        files = ([e["file"] for e in st.entry(s, n.id)["evidence"] if e.get("file")] if args.get("evidence")
                 else [sh.file for sh in n.shots])
        i = int(args.get("index") or 0)
        if not 0 <= i < len(files):
            return _fail(f"{s.ref(n)} has {len(files)} image(s).")
        return {"content": _images([files[i]]) or [{"type": "text", "text": f"Couldn't read {files[i]}"}]}

    def t_set_status(self, args: dict) -> dict:
        blocked = lic.pro_required("Reporting fixes back to Lazy as")
        if blocked:
            return _fail(blocked)
        s, n = find_note(str(args.get("ref", "")))
        evidence = args.get("evidence") or []
        e = st.set_status(s, n.id, str(args.get("status", "")), by=self.client, summary=args.get("summary"),
                          evidence=evidence if isinstance(evidence, list) else [evidence], agent=True)
        msg = f"{s.ref(n)} is now {st.LABELS[e['status']].lower()}."
        if e["status"] == "claimed" and not e["evidence"]:
            msg += (" It has no evidence yet: add a screenshot of the result or test output with add_evidence, "
                    "or the person will be asked to check it blind.")
        return _ok(msg)

    def t_add_evidence(self, args: dict) -> dict:
        blocked = lic.pro_required("Reporting fixes back to Lazy as")
        if blocked:
            return _fail(blocked)
        s, n = find_note(str(args.get("ref", "")))
        item = {k: args[k] for k in ("text", "file") if args.get(k)}
        if not item:
            return _fail("Give text or a file path.")
        e = st.add_evidence(s, n.id, [item], by=self.client)
        return _ok(f"{s.ref(n)} now has {len(e['evidence'])} piece(s) of evidence.")


def _remember(project_dir: str, key: str) -> None:
    """The review an agent is working on in this folder (the Stop hook checks it)."""
    try:
        reviews = lic.state().get("reviews") or {}
        reviews[project_dir] = {"session": key, "at": now_iso()}
        if len(reviews) > 50:
            reviews = dict(sorted(reviews.items(), key=lambda kv: kv[1].get("at", ""))[-50:])
        lic.update_state(reviews=reviews)
    except OSError:
        pass


def _images(files: list) -> list:
    out = []
    for f in files:
        try:
            data = Path(f).read_bytes()
        except OSError:
            continue
        mime = "image/jpeg" if f.lower().endswith((".jpg", ".jpeg")) else "image/png"
        out.append({"type": "image", "data": base64.b64encode(data).decode(), "mimeType": mime})
    return out


def _ok(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


def _fail(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": True}


def _error(mid: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def serve(stdin=None, stdout=None) -> None:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    server = Server()
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            reply = _error(None, -32700, "Parse error")
        else:
            if isinstance(msg, list):          # a batch (older protocol versions)
                replies = [r for r in (server.handle(m) for m in msg if isinstance(m, dict)) if r]
                reply = replies or None
            else:
                reply = server.handle(msg) if isinstance(msg, dict) else _error(None, -32600, "Invalid request")
        if reply is not None:
            stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            stdout.flush()
