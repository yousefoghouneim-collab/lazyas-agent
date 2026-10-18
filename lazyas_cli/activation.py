"""Talking to the website about licences: activate this computer, renew the activation, start the trial, list and
free computers. Only these calls go online, and they send only: the licence key, a SHA-256 of this computer's hardware
id (never the id itself), the computer's name, the system (mac/windows/linux) and the app version.

The apps call maintain() once a day; `lazyas license <key>` calls activate().
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import license as lic
from .sessions import HOME_DIR

SITE = os.environ.get("LAZYAS_SITE", "https://lazyas.com").rstrip("/")
RENEW_WHEN_DAYS_LEFT = 23       # leases last 30 days: renew about once a week


def os_name() -> str:
    return "mac" if sys.platform == "darwin" else "windows" if sys.platform.startswith("win") else "linux"


def _hardware_id() -> str:
    try:
        if sys.platform == "darwin":
            out = subprocess.run(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"], capture_output=True, text=True,
                                 timeout=5).stdout
            m = re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', out)
            if m:
                return m.group(1)
        elif sys.platform.startswith("win"):
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0,
                                winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
                return str(winreg.QueryValueEx(k, "MachineGuid")[0])
        else:
            for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
                try:
                    v = open(p, encoding="ascii").read().strip()
                    if v:
                        return v
                except OSError:
                    pass
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    # No hardware id: a random one kept in ~/.lazyas (deleting it looks like a new computer to the website).
    f = HOME_DIR / "device-id"
    try:
        return f.read_text(encoding="ascii").strip()
    except OSError:
        v = str(uuid.uuid4())
        HOME_DIR.mkdir(parents=True, exist_ok=True)
        f.write_text(v, encoding="ascii")
        return v


_device: Optional[str] = None


def device_id() -> str:
    """32 hex characters, the same in the Mac app, the Windows/Linux app and this tool."""
    global _device
    if _device is None:
        _device = hashlib.sha256(("lazyas-device:" + _hardware_id().strip().lower()).encode()).hexdigest()[:32]
    return _device


def device_name() -> str:
    if sys.platform == "darwin":
        try:
            n = subprocess.run(["scutil", "--get", "ComputerName"], capture_output=True, text=True, timeout=5).stdout.strip()
            if n:
                return n[:64]
        except (OSError, subprocess.SubprocessError):
            pass
    return (platform.node() or "Computer")[:64]


OFFLINE = "Couldn't reach the licence server (no connection?). Lazy as tries again later."


def _json(raw: bytes) -> dict:
    try:
        j = json.loads(raw.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        return {}
    return j if isinstance(j, dict) else {}


def _post(path: str, body: dict, timeout: float = 15) -> tuple:
    """(status, json object). Raises OSError when the website can't be reached, or doesn't answer like itself
    (a captive portal or proxy page counts as unreachable)."""
    req = urllib.request.Request(f"{SITE}{path}", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "lazyas"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            j = _json(raw)
            if not j and raw.strip() not in (b"", b"{}"):
                raise OSError("unexpected answer")
            return r.status, j
    except urllib.error.HTTPError as e:
        return e.code, _json(e.read())
    except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError) as e:
        raise OSError(str(e)) from e


def _key_text() -> Optional[str]:
    try:
        return "".join(lic.license_path().read_text(encoding="utf-8").split()) or None
    except OSError:
        return None


def activate(app: str = "cli") -> dict:
    """Activates (or renews) the installed key on this computer. Returns {"ok", "message", ...}."""
    key = _key_text()
    k = lic.current()
    if not key or not k:
        return {"ok": False, "message": "No licence key on this computer."}
    try:
        code, j = _post("/api/activate", {"key": key, "device": device_id(), "name": device_name(), "os": os_name(),
                                          "app": app})
    except OSError:
        return {"ok": False, "offline": True, "message": OFFLINE}
    lid = str(k.get("order"))
    if code == 200 and isinstance(j.get("token"), str):
        try:
            t = lic.parse_token(j["token"])
        except lic.LicenseError:
            return {"ok": False, "message": "The licence server's answer wasn't signed properly."}
        if t.get("device") != device_id() or t.get("lic") != lid:
            return {"ok": False, "message": "The licence server answered for a different computer."}
        if isinstance(j.get("key"), str):
            try:
                lic.install(j["key"])               # a renewal: the same licence with a later end date
            except lic.LicenseError:
                pass
        lic.update_activation(token=j["token"], problem=None)
        return {"ok": True, "message": "Activated on this computer.", "license": j.get("license") or {}}
    if code in (402, 403, 409):
        kind = "limit" if code == 409 else "expired" if code == 402 else "revoked" if j.get("revoked") else "invalid"
        msg = str(j.get("error") or "This licence can't be used here.")
        lic.update_activation(token=None, problem={"lic": lid, "kind": kind, "message": msg,
                                                   "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
        return {"ok": False, "kind": kind, "message": msg, "devices": _list(j.get("devices"))}
    return {"ok": False, "offline": True, "message": OFFLINE if code in (429, 503) or code >= 500 else
            str(j.get("error") or f"The licence server said {code}. Trying again later.")}


def _list(v) -> list:
    return [d for d in v if isinstance(d, dict)] if isinstance(v, list) else []


def ensure_trial(app: str = "cli") -> Optional[dict]:
    """Asks the website when this computer's trial started (the first time it asks, it starts now)."""
    try:
        code, j = _post("/api/trial", {"device": device_id(), "os": os_name(), "app": app})
    except OSError:
        return None
    if code == 200 and isinstance(j.get("token"), str):
        try:
            t = lic.parse_token(j["token"])
        except lic.LicenseError:
            return None
        if t.get("kind") == "trial" and t.get("device") == device_id():
            lic.update_activation(trial=j["token"])
            return t
    return None


def devices() -> dict:
    key = _key_text()
    if not key:
        return {"ok": False, "message": "No licence key on this computer."}
    try:
        code, j = _post("/api/activate", {"key": key, "device": device_id(), "action": "devices"})
    except OSError:
        return {"ok": False, "message": OFFLINE}
    return {"ok": code == 200, "message": str(j.get("error", "")), "devices": _list(j.get("devices")),
            "license": j.get("license") if isinstance(j.get("license"), dict) else {}}


def deactivate(target: Optional[str] = None) -> dict:
    """Frees a computer's slot (this one by default). Up to 5 moves a month."""
    key = _key_text()
    if not key:
        return {"ok": False, "message": "No licence key on this computer."}
    target = target or device_id()
    try:
        code, j = _post("/api/activate", {"key": key, "device": device_id(), "action": "deactivate", "target": target})
    except OSError:
        return {"ok": False, "message": OFFLINE}
    if code == 200 and target == device_id():
        lic.update_activation(token=None)
    if code == 200:
        lic.update_activation(problem=None)
    return {"ok": code == 200, "message": str(j.get("error", "")), "devices": _list(j.get("devices")),
            "license": j.get("license") if isinstance(j.get("license"), dict) else {}}


def needs_renewal(now: Optional[datetime] = None) -> bool:
    now = now or datetime.now(timezone.utc)
    k = lic.current()
    if not k:
        return False
    try:
        t = lic.parse_token(lic.activation().get("token") or "")
    except lic.LicenseError:
        return True
    lease = lic._time(t.get("lease"))
    return t.get("device") != device_id() or t.get("lic") != str(k.get("order")) or not lease or \
        lease - now < timedelta(days=RENEW_WHEN_DAYS_LEFT) or lic.expired(t, now.date())


def trial_needs_refresh(now: Optional[datetime] = None) -> bool:
    """No trial yet, or it ends within 2 days (the owner may have extended it): ask again, at most daily."""
    now = now or datetime.now(timezone.utc)
    act = lic.activation()
    try:
        t = lic.parse_token(act.get("trial") or "")
    except lic.LicenseError:
        return True
    ends, asked = lic._time(t.get("ends")), lic._time(t.get("issued"))
    return bool(ends and ends - now < timedelta(days=2) and (not asked or now - asked > timedelta(hours=20)))


def maintain(app: str) -> Optional[dict]:
    """Once a day from the apps: keep the activation fresh, or fetch the trial. Never raises."""
    try:
        if lic.current():
            problem = lic.activation().get("problem") or {}
            # A computer that was refused for being one too many retries daily (a slot may have been freed).
            if needs_renewal() or problem.get("kind") == "limit":
                return activate(app)
            return None
        if lic.licensing_live() and trial_needs_refresh():
            t = ensure_trial(app)
            return {"ok": bool(t), "trial": t}
    except Exception as e:      # noqa: BLE001  (background housekeeping must never take the app down)
        return {"ok": False, "message": str(e)}
    return None
