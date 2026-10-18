"""Claude Code hooks: "done needs proof".

Stop          When the agent finishes a turn: if it claimed notes fixed, tell the person ("Claude says 4 items are
              fixed. Verify now.": free). With Pro, notes claimed fixed without any evidence keep the agent working
              until it attaches proof (a screenshot of the result, test output). Never blocks twice in a row.
SessionStart  Tells the agent how many Lazy as notes are open for this project, so it can offer to fix them.

Hooks read the event as JSON on stdin and answer on stdout, per Claude Code's hook contract.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from . import license as lic
from . import projects
from . import status as st
from .sessions import HOME_DIR, find_session, now_iso, read_config

RECENT = timedelta(hours=12)


def _read_event(stdin) -> dict:
    try:
        data = json.loads(stdin.read() or "{}")
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def _active_review(cwd: str):
    """The review an agent fetched in this folder in the last 12 hours (set by the MCP server)."""
    reviews = lic.state().get("reviews") or {}
    best = None
    for folder, info in reviews.items():
        if not (cwd == folder or cwd.startswith(folder.rstrip("/\\") + ("\\" if "\\" in folder else "/"))):
            continue
        try:
            at = datetime.strptime(info["at"].replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S%z")
        except (KeyError, ValueError):
            continue
        if datetime.now(timezone.utc) - at > RECENT:
            continue
        if not best or len(folder) > len(best[0]):
            best = (folder, info["session"])
    return find_session(best[1]) if best else None


def stop(stdin=None, stdout=None, notify: bool = True) -> int:
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    event = _read_event(stdin)
    session = _active_review(str(event.get("cwd") or Path.cwd()))
    if not session:
        return 0
    data = st.read(session)
    claimed = [n for n in session.notes if st.status_of(session, n.id, data) == "claimed"]
    state = lic.state()
    told = set((state.get("notified") or {}).get(session.key, []))
    fresh = [n for n in claimed if n.id not in told]
    if fresh and notify:
        _tell_person(session, claimed, fresh)
        notified = state.get("notified") or {}
        notified[session.key] = sorted(told | {n.id for n in fresh})
        lic.update_state(notified=dict(list(notified.items())[-30:]))

    if event.get("stop_hook_active"):
        return 0
    if read_config().get("enforceProof") is False or not lic.plan()["pro"]:
        return 0
    missing = st.needs_proof(session, data)
    if not missing:
        return 0
    refs = ", ".join(session.ref(n) for n in missing)
    reason = (f"Lazy as: these notes are marked fixed without any proof: {refs}. For each one, check the fix and "
              "call add_evidence with a screenshot of the result (a file path) or the test output. If you didn't "
              "actually check it, set it back to 'sent' and say what's left.")
    stdout.write(json.dumps({"decision": "block", "reason": reason}) + "\n")
    return 0


def session_start(stdin=None, stdout=None) -> int:
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    event = _read_event(stdin)
    project = projects.load(Path(str(event.get("cwd") or Path.cwd())))
    if not project:
        return 0
    open_refs, waiting = [], 0
    for s in projects.sessions_for(project, limit=20):
        data = st.read(s)
        for n in s.notes:
            state = st.status_of(s, n.id, data)
            if state in st.OPEN:
                open_refs.append(s.ref(n))
            elif state == "claimed":
                waiting += 1
    if not open_refs:
        return 0
    text = (f"Lazy as has {len(open_refs)} open review note(s) for {project['name']}"
            + (f" and {waiting} waiting for the person to verify" if waiting else "")
            + ". If the person asks to fix their review, use the lazyas MCP tools (get_latest_review or list_open), "
              "fix notes by reference and report each with set_status and evidence.")
    stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}) + "\n")
    return 0


def _tell_person(session, claimed: list, fresh: list) -> None:
    """Lets the app show its badge ('3 awaiting verification') and a nudge."""
    HOME_DIR.mkdir(parents=True, exist_ok=True)
    event = {"at": now_iso(), "kind": "claimed", "session": session.key, "refs": [session.ref(n) for n in fresh]}
    try:
        with open(HOME_DIR / "events.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")
    except OSError:
        pass
    msg = (f"Your agent says {len(claimed)} item{'s are' if len(claimed) != 1 else ' is'} fixed in "
           f"“{session.title}”. Verify now.")
    _desktop_notice(msg, session.key)


def _desktop_notice(message: str, key: str) -> None:
    try:
        if sys.platform == "darwin":
            app = Path("/Applications/Lazy as.app")
            if app.exists():   # the app shows its own nudge with a Verify button
                subprocess.run(["open", "-g", f"lazyas://claimed?session={key}"], timeout=5, check=False)
                return
            script = f'display notification {json.dumps(message)} with title "Lazy as"'
            subprocess.run(["osascript", "-e", script], timeout=5, check=False)
        elif sys.platform.startswith("linux") and shutil.which("notify-send"):
            subprocess.run(["notify-send", "Lazy as", message], timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        pass
