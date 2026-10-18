"""Reads Lazy as review sessions (read-only). Same folder layout as the Mac and Windows/Linux apps:
<root>/<yyyy-MM-dd_HHmmss>/session.json, notes.md, prompt.md, 01.png, 01b.png…

Every note gets a stable reference, "<session folder>#<note number>", e.g. "2026-10-16_031500#3".
Statuses live next to it in review.json (see status.py), never inside session.json, so the apps can keep
rewriting session.json without losing anything an agent or a reviewer recorded.
"""
from __future__ import annotations

import json
import os
import tempfile
import plistlib
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

HOME_DIR = Path(os.environ.get("LAZYAS_HOME") or Path.home() / ".lazyas")     # shared by the apps and this tool


@dataclass
class Shot:
    file: str
    quote: Optional[str] = None
    element: Optional[str] = None      # accessibility label under the pointer, e.g. 'button "Save changes"'
    near_text: Optional[str] = None    # on-screen text read around the pointer
    rect: Optional[list] = None        # [x, y, w, h] of the crop on screen (global points), for verify mode
    whole: bool = False                # not pointing at anything: the whole window, nothing highlighted

    @property
    def name(self) -> str:
        return os.path.basename(self.file)


@dataclass
class Note:
    id: int
    text: str
    shots: list
    time: Optional[datetime] = None
    context: Optional[str] = None
    general: bool = False


@dataclass
class Session:
    folder: Path
    started: Optional[datetime]
    ended: Optional[datetime]
    notes: list = field(default_factory=list)
    name: Optional[str] = None

    @property
    def key(self) -> str:
        return self.folder.name

    @property
    def title(self) -> str:
        if self.name:
            return self.name
        return self.started.astimezone().strftime("%a %d %b, %H:%M") if self.started else self.key

    @property
    def prompt(self) -> Optional[str]:
        try:
            return (self.folder / "prompt.md").read_text(encoding="utf-8")
        except OSError:
            return None

    def ref(self, note: Note) -> str:
        return f"{self.key}#{note.id}"

    def note(self, note_id: int) -> Optional[Note]:
        return next((n for n in self.notes if n.id == note_id), None)

    def contexts(self) -> list:
        return [n.context for n in self.notes if n.context]


# Where the sessions are

def root() -> Path:
    """The sessions folder: $LAZYAS_ROOT, then what the apps last told us (~/.lazyas/config.json), then each
    app's own settings, then Documents/LazyAs."""
    env = os.environ.get("LAZYAS_ROOT")
    if env:
        return Path(env).expanduser()
    cfg = read_config().get("root")
    if cfg:
        return Path(cfg).expanduser()
    for guess in (_mac_root(), _desktop_root()):
        if guess:
            return guess
    return Path.home() / "Documents" / "LazyAs"


def read_config() -> dict:
    try:
        return json.loads((HOME_DIR / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_config(**changes) -> dict:
    data = read_config()
    data.update({k: v for k, v in changes.items() if v is not None})
    HOME_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write(HOME_DIR / "config.json", json.dumps(data, indent=2, ensure_ascii=False))
    return data


def _mac_root() -> Optional[Path]:
    if sys.platform != "darwin":
        return None
    try:
        with open(Path.home() / "Library/Preferences/com.lazyas.app.plist", "rb") as f:
            p = plistlib.load(f).get("rootFolder")
        return Path(p).expanduser() if p else None
    except (OSError, ValueError, plistlib.InvalidFileException):
        return None


def _desktop_root() -> Optional[Path]:
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "LazyAs"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "LazyAs"
    try:
        p = json.loads((base / "settings.json").read_text(encoding="utf-8")).get("rootFolder")
        return Path(p).expanduser() if p else None
    except (OSError, ValueError):
        return None


# Loading

def parse_time(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.strptime(s.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        try:
            return datetime.fromisoformat(s)
        except ValueError:
            return None


def load(folder: Path) -> Optional[Session]:
    folder = Path(folder)
    try:
        disk = json.loads((folder / "session.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    notes = []
    for n in disk.get("notes", []):
        try:
            raw = n.get("shots")
            if raw is None and n.get("shot"):
                raw = [{"file": n["shot"]}]
            shots = [Shot(file=_resolve(folder, s.get("file", "")), quote=s.get("quote"), element=s.get("element"),
                          near_text=s.get("nearText"), rect=s.get("rect"),
                          whole=bool(s.get("whole"))) for s in raw or []]
            notes.append(Note(id=int(n["id"]), text=n.get("text", ""), shots=shots, time=parse_time(n.get("time")),
                              context=n.get("context"), general=bool(n.get("general"))))
        except (KeyError, TypeError, ValueError):
            continue
    return Session(folder=folder, started=parse_time(disk.get("started")), ended=parse_time(disk.get("ended")),
                   notes=notes, name=disk.get("name"))


def _resolve(folder: Path, file: str) -> str:
    """A session made on another computer has paths from there: use the file next to session.json."""
    if file and os.path.exists(file):
        return file
    local = folder / os.path.basename(file.replace("\\", "/"))
    return str(local) if local.exists() else file


def all_sessions(root_dir: Optional[Path] = None, limit: Optional[int] = None) -> list:
    """Saved sessions with at least one note, newest first."""
    r = Path(root_dir) if root_dir else root()
    if not r.exists():
        return []
    folders = sorted((p for p in r.iterdir() if (p / "session.json").exists()), key=lambda p: p.name, reverse=True)
    out = []
    for f in folders:
        s = load(f)
        if s and s.notes:
            out.append(s)
            if limit and len(out) >= limit:
                break
    return out


def parse_ref(ref: str) -> tuple:
    """'2026-10-16_031500#3' -> ('2026-10-16_031500', 3). Also accepts 'latest#3'."""
    key, _, num = ref.strip().rpartition("#")
    if not key or not num.isdigit():
        raise ValueError(f"Not a note reference: {ref!r}. Expected something like 2026-10-16_031500#3")
    return key, int(num)


def find_session(key: str, root_dir: Optional[Path] = None) -> Optional[Session]:
    if key in ("latest", "last"):
        found = all_sessions(root_dir, limit=1)
        return found[0] if found else None
    if not key or key.startswith(".") or "/" in key or "\\" in key:     # references are plain folder names only
        return None
    r = Path(root_dir) if root_dir else root()
    return load(r / key)


def find_note(ref: str, root_dir: Optional[Path] = None) -> tuple:
    key, num = parse_ref(ref)
    s = find_session(key, root_dir)
    if not s:
        raise LookupError(f"No session called {key!r} in {Path(root_dir) if root_dir else root()}")
    n = s.note(num)
    if not n:
        raise LookupError(f"Session {s.key} has no note #{num} (it has {len(s.notes)})")
    return s, n


def atomic_write(path: Path, text: str) -> None:
    """Writes via a temporary file of its own (unique per call, so threads and processes never collide)."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
