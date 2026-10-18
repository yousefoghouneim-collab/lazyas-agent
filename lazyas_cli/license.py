"""Licences, the free trial and the free monthly allowance, checked offline from small signed notes.

    Licence key  LA1.<base64url JSON>.<base64url Ed25519 signature of "LA1.<base64url JSON>">
                 {"v":1, "product":"lazyas", "plan":"pro|lifetime|team", "email", "name", "seats", "order",
                  "issued":"YYYY-MM-DD", "expires":"YYYY-MM-DD" or null}
    Token        LT1.<base64url JSON>.<signature>   (the website's answer, bound to one computer)
                 activation: {"kind":"activation", "lic", "device", "plan", "seats", "expires", "issued", "lease"}
                 trial:      {"kind":"trial", "device", "started", "ends", "issued"}

A key alone isn't enough once paid plans are on: the website activates it for this computer (at most 3 computers per
personal licence, 2 per Team seat) and the activation works offline for 30 days; the apps renew it weekly
(activation.py). A refunded licence stops at its next renewal. The trial is counted per computer by the website, so
reinstalling doesn't restart it.

Everything lives in ~/.lazyas/: license.txt (the key), activation.json (the tokens) and state.json (first run,
whether paid plans are on). Until paid plans are on (the website says so, or EARLY_ACCESS_ENDS passes), everyone has
every feature: free early access. Set EARLY_ACCESS_ENDS to the launch date in the build you ship on launch day.
"""
from __future__ import annotations

import base64
import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Sequence, Union

from . import ed25519
from .sessions import HOME_DIR, all_sessions, atomic_write

PUBLIC_KEY = base64.b64decode("hHTfG6RvG/mXedmPHJECHcyNaAwUN+5VXSeJANFq8JM=")
BACKUP_PUBLIC_KEY = base64.b64decode("n4FbFFUe9SyWdj7wts/ETufCK+IWqcK7S1t5qyCFY+k=")   # for a key rotation
PUBLIC_KEYS = (PUBLIC_KEY, BACKUP_PUBLIC_KEY)
TRIAL_DAYS = 14
FREE_NOTES_PER_MONTH = 100
OFFLINE_GRACE_DAYS = 7          # a new key works this long before it must be activated online
LOCAL_TRIAL_DAYS = 3            # first launch offline: a short trial until the website can confirm the real one
PENDING_KEY_DAYS = 14           # the offline grace is only for keys issued in the last two weeks (new purchases)
EARLY_ACCESS_ENDS = date(2027, 3, 31)   # paid plans switch on by this date even if the website can't be reached
BUY_URL = "https://lazyas.com/pricing"
ACCOUNT_URL = "https://lazyas.com/account"
PLANS = {"pro": "Pro", "lifetime": "Founder Lifetime", "team": "Team"}

Keys = Union[bytes, Sequence[bytes]]


class LicenseError(ValueError):
    pass


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _signed(text: str, prefix: str, public: Optional[Keys]) -> dict:
    parts = "".join((text or "").split()).split(".")
    if len(parts) != 3 or parts[0] != prefix:
        raise LicenseError("That doesn't look like a Lazy as licence key (it starts with LA1.)")
    try:
        sig = _b64d(parts[2])
        payload = json.loads(_b64d(parts[1]))
    except (ValueError, TypeError):
        raise LicenseError("The key is damaged. Copy it again from your email.")
    public = PUBLIC_KEYS if public is None else public
    keys = [public] if isinstance(public, (bytes, bytearray)) else list(public)
    if not isinstance(payload, dict) or not any(ed25519.verify(k, f"{prefix}.{parts[1]}".encode(), sig) for k in keys):
        raise LicenseError("This key isn't genuine, or it was changed.")
    return payload


def parse(key: str, public: Optional[Keys] = None) -> dict:
    """Checks a key's signature and returns what it says. Raises LicenseError when it isn't genuine."""
    payload = _signed(key, "LA1", public)
    if payload.get("product", "lazyas") != "lazyas" or payload.get("plan") not in PLANS:
        raise LicenseError("This key is for a different product.")
    return payload


def parse_token(token: str, public: Optional[Keys] = None) -> dict:
    payload = _signed(token, "LT1", public)
    if payload.get("kind") not in ("activation", "trial"):
        raise LicenseError("Unknown token.")
    return payload


def make(payload: dict, seed: bytes, prefix: str = "LA1") -> str:
    """Signs a key or token (the website does this; here for tests)."""
    body = _b64e(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    return f"{prefix}.{body}.{_b64e(ed25519.sign(seed, f'{prefix}.{body}'.encode()))}"


def expired(payload: dict, today: Optional[date] = None) -> bool:
    exp = payload.get("expires")
    if not exp:
        return False
    try:
        return (today or date.today()) > date.fromisoformat(exp)
    except ValueError:
        return True


def _time(s) -> Optional[datetime]:
    """A stored time as an aware datetime (dates and times without a zone count as UTC); None if unreadable."""
    if not isinstance(s, str) or not s:
        return None
    try:
        t = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


# Stored state

def license_path() -> Path:
    return HOME_DIR / "license.txt"


def _read(name: str) -> dict:
    try:
        data = json.loads((HOME_DIR / name).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(name: str, data: dict) -> None:
    HOME_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write(HOME_DIR / name, json.dumps(data, indent=2))
    try:
        os.chmod(HOME_DIR / name, 0o600)
    except OSError:
        pass


def state() -> dict:
    return _read("state.json")


def _locked(name: str):
    from .status import _Lock
    HOME_DIR.mkdir(parents=True, exist_ok=True)
    return _Lock(HOME_DIR / name)


def update_state(**changes) -> dict:
    with _locked("state.json"):            # read-modify-write: one writer at a time (apps, tool, agents)
        data = state()
        data.update(changes)
        _write("state.json", data)
    return data


def activation() -> dict:
    return _read("activation.json")


def update_activation(**changes) -> dict:
    with _locked("activation.json"):
        data = activation()
        for k, v in changes.items():
            if v is None:
                data.pop(k, None)
            else:
                data[k] = v
        _write("activation.json", data)
    return data


def install(key: str) -> dict:
    payload = parse(key)
    if expired(payload, _utc_today()):
        raise LicenseError(f"This key expired on {payload['expires']}. Renewing sends you a new one.")
    HOME_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write(license_path(), "".join(key.split()) + "\n")
    try:
        os.chmod(license_path(), 0o600)
    except OSError:
        pass
    seen = dict(activation().get("seen") or {})
    seen.setdefault(str(payload.get("order")), datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    update_activation(seen=seen, problem=None)
    return payload


def remove() -> None:
    try:
        license_path().unlink()
    except OSError:
        pass
    update_activation(token=None, problem=None)


def current() -> Optional[dict]:
    try:
        return parse(license_path().read_text(encoding="utf-8", errors="replace"))
    except (OSError, LicenseError):
        return None


def first_run() -> datetime:
    s = state()
    try:
        return datetime.fromisoformat(s["firstRun"].replace("Z", "+00:00"))
    except (KeyError, ValueError, AttributeError):
        now = datetime.now(timezone.utc)
        update_state(firstRun=now.strftime("%Y-%m-%dT%H:%M:%SZ"))
        return now


def licensing_live(today: Optional[date] = None) -> bool:
    """Paid plans are on once the website said so (remembered in two files), once this computer has a trial from the
    website, or from EARLY_ACCESS_ENDS. LAZYAS_LICENSING=1 can switch them on for testing; nothing switches them off."""
    if os.environ.get("LAZYAS_LICENSING", "").strip().lower() in ("1", "true", "yes"):
        return True
    act = activation()
    return bool(state().get("licensing") or act.get("licensing") or act.get("trial")) or \
        (today or date.today()) >= EARLY_ACCESS_ENDS


def notes_this_month(root_dir: Optional[Path] = None, today: Optional[date] = None) -> int:
    today = today or date.today()
    count = 0
    for s in all_sessions(root_dir, limit=400):
        if s.started and (s.started.year, s.started.month) < (today.year, today.month):
            break
        for n in s.notes:
            t = n.time or s.started
            if t and t.year == today.year and t.month == today.month:
                count += 1
    return count


# What this person can use

def _token(text, device: str, kind: str) -> Optional[dict]:
    if not isinstance(text, str):
        return None
    try:
        t = parse_token(text)
    except LicenseError:
        return None
    return t if t.get("kind") == kind and t.get("device") == device else None


def decide(now: datetime, live: bool, lic: Optional[dict], act: dict, device: str) -> dict:
    """The plan, from what's on disk (no network). `now` is clamped to the newest time the website signed and the
    newest time this computer has seen, so turning the clock back doesn't stretch a trial or an activation."""
    tok = _token(act.get("token"), device, "activation")
    trial = _token(act.get("trial"), device, "trial")
    for seen in (tok and _time(tok.get("issued")), trial and _time(trial.get("issued")), _time(act.get("clock"))):
        if seen and seen > now:
            now = seen
    problem = None
    if not isinstance(act.get("seen"), dict):
        act = {**act, "seen": {}}
    if lic and not expired(lic, now.date()):
        lid = str(lic.get("order"))
        mine = tok if tok and tok.get("lic") == lid else None
        info = {"pro": True, "tier": lic["plan"], "label": PLANS[lic["plan"]], "source": "licence",
                "email": lic.get("email"), "expires": lic.get("expires")}
        if not live:
            return info
        lease = mine and _time(mine.get("lease"))
        if mine and expired(mine, now.date()):
            problem = f"Your licence ended on {mine.get('expires')}."
        elif lease and now < lease:
            return {**info, "expires": mine.get("expires"), "lease": mine.get("lease")}
        issue = act.get("problem") if isinstance(act.get("problem"), dict) else {}
        issued = _time(lic.get("issued"))
        if problem:
            pass
        elif issue.get("lic") == lid:
            problem = issue.get("message")
        else:
            seen = _time(act["seen"].get(lid))
            fresh = issued is not None and now - issued < timedelta(days=PENDING_KEY_DAYS)
            if not mine and seen and fresh and now - seen < timedelta(days=OFFLINE_GRACE_DAYS):
                return {**info, "source": "licence-pending",
                        "note": "Not activated yet: connect to the internet once in the next few days."}
            problem = ("Lazy as couldn't check your licence for a while. Connect to the internet once and it "
                       "turns back on by itself.")
    elif lic:
        problem = f"Your licence ended on {lic.get('expires')}."
    if not live:
        return {"pro": True, "tier": "early-access", "label": "Free early access", "source": "early-access"}
    extra = {"licence_problem": problem} if problem else {}
    if trial:
        ends = _time(trial.get("ends"))
        if ends and now < ends:
            left = max(1, -(-int((ends - now).total_seconds()) // 86400))
            return {"pro": True, "tier": "trial", "label": f"Pro trial · {left} day{'s' if left != 1 else ''} left",
                    "source": "trial", "trial_days_left": left, **extra}
    else:
        start = _time(act.get("localTrial"))
        if start and now - start < timedelta(days=LOCAL_TRIAL_DAYS):
            left = LOCAL_TRIAL_DAYS - max(0, int((now - start).total_seconds() // 86400))
            return {"pro": True, "tier": "trial", "label": f"Pro trial · {left} day{'s' if left != 1 else ''} left",
                    "source": "trial-local", "trial_days_left": left, **extra}
    return {"pro": False, "tier": "free", "label": "Free", "source": "free", "buy": BUY_URL,
            "expired_licence": bool(lic), **extra}


def plan(root_dir: Optional[Path] = None, now: Optional[datetime] = None) -> dict:
    """What this person can use right now."""
    from .activation import device_id
    now = now or datetime.now(timezone.utc)
    act = activation()
    live = licensing_live(now.date())
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    changes = {}
    if live and not act.get("localTrial") and not act.get("trial"):
        # The short offline trial is for a fresh install only: not after sessions from days ago (deleting the
        # app's records and blocking our server mustn't restart it).
        changes["localTrial"] = "2000-01-01T00:00:00Z" if _has_old_sessions(root_dir, now) else stamp
    clock = _time(act.get("clock"))
    if not clock or now - clock > timedelta(hours=1):
        changes["clock"] = stamp
    if changes:
        act = {**act, **changes}
        try:
            update_activation(**changes)
        except OSError:                 # a read-only ~/.lazyas: decide from what's there
            pass
    p = decide(now, live, current(), act, device_id())
    if p["source"] == "free":
        used = notes_this_month(root_dir)
        p.update(notes_used=used, notes_left=max(0, FREE_NOTES_PER_MONTH - used))
    return p


def _has_old_sessions(root_dir: Optional[Path], now: datetime) -> bool:
    try:
        for s in all_sessions(root_dir, limit=2000):
            pass
        return bool(s.started and now - (s.started if s.started.tzinfo else s.started.replace(tzinfo=timezone.utc))
                    > timedelta(days=LOCAL_TRIAL_DAYS))
    except (NameError, OSError, ValueError, TypeError, AttributeError):
        return False


def pro_required(feature: str) -> Optional[str]:
    """None when allowed, otherwise a friendly message saying why not."""
    p = plan()
    if p["pro"]:
        return None
    why = p.get("licence_problem") or "Your free trial has ended."
    return (f"{feature}: that needs Lazy as Pro. {why} Get Pro at {BUY_URL} "
            "(then paste the key into Lazy as › Settings › Pro, or run: lazyas license <key>).")


def next_month_start(today: Optional[date] = None) -> date:
    today = today or date.today()
    return (today.replace(day=1) + timedelta(days=32)).replace(day=1)
