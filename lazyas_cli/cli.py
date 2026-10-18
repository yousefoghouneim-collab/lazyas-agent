"""`lazyas`: your Lazy as reviews from the command line, and the bridge to AI agents.

  lazyas list                      recent reviews and their status
  lazyas show [SESSION] [--format notes|fixlist|criteria|playwright] [--open-only]
  lazyas inbox                     everything open or waiting for you to verify
  lazyas verify REF [REF…]         you checked it: mark verified
  lazyas reopen REF "why"          still broken
  lazyas status REF STATUS [--summary …] [--evidence FILE|TEXT]
  lazyas report [SESSION] [-o FILE] [--open]       self-contained HTML report
  lazyas export [SESSION] [--format …] [--to DIR]  write the review (and screenshots) into a project
  lazyas link MATCH [MATCH…]       this folder's project owns pages containing MATCH (e.g. localhost:3000)
  lazyas license [KEY | --remove | --devices | --deactivate N]   show, install or move your licence
  lazyas setup [claude|codex|cursor|gemini|copilot]   how to connect an agent
  lazyas mcp                       run the MCP server (agents start this themselves)
  lazyas hook stop|session-start   Claude Code hooks
  lazyas doctor                    where things are
SESSION is a folder name like 2026-10-16_031500, or "latest". REF is SESSION#N, e.g. latest#3.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import webbrowser
from pathlib import Path

from . import __version__, formats, hooks, projects, report
from . import license as lic
from . import status as st
from .sessions import HOME_DIR, all_sessions, find_note, find_session, root

SETUP = {
    "claude": ("Claude Code", "Install the plugin (MCP server, review skill and the done-needs-proof hooks):\n"
               "  claude plugin marketplace add yousefoghouneim-collab/lazyas-agent\n  claude plugin install lazyas@lazyas\n"
               "Or just the MCP server:\n  claude mcp add lazyas -- {cmd} mcp"),
    "codex": ("Codex", "Add to ~/.codex/config.toml:\n  [mcp_servers.lazyas]\n  command = \"{exe}\"\n  args = {args}"),
    "cursor": ("Cursor", "Add to ~/.cursor/mcp.json (or .cursor/mcp.json in the project):\n"
               "  {{\"mcpServers\": {{\"lazyas\": {{\"command\": \"{exe}\", \"args\": {args}}}}}}}"),
    "gemini": ("Gemini CLI", "Add to ~/.gemini/settings.json:\n"
               "  {{\"mcpServers\": {{\"lazyas\": {{\"command\": \"{exe}\", \"args\": {args}}}}}}}"),
    "copilot": ("GitHub Copilot CLI", "Run /mcp add in Copilot CLI, or add to ~/.copilot/mcp-config.json:\n"
                "  {{\"mcpServers\": {{\"lazyas\": {{\"type\": \"local\", \"command\": \"{exe}\", \"args\": {args}, "
                "\"tools\": [\"*\"]}}}}}}"),
}


def _session(arg: str):
    s = find_session(arg or "latest")
    if not s:
        sys.exit(f"No review called {arg!r}. Try: lazyas list")
    return s


def _launch() -> tuple:
    """How an agent should start this tool: the installed command, else this Python and module."""
    exe = shutil.which("lazyas")
    if exe:
        return exe, ["mcp"]
    return sys.executable, ["-m", "lazyas_cli", "mcp"]


def main(argv=None) -> int:
    try:
        return _run(argv)
    except (LookupError, ValueError) as e:     # a bad reference or status: say so plainly, no traceback
        sys.exit(f"lazyas: {e}")


def _run(argv=None) -> int:
    p = argparse.ArgumentParser(prog="lazyas", description="Lazy as reviews for people and AI agents.")
    p.add_argument("--version", action="version", version=f"lazyas {__version__}")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("list").add_argument("--limit", type=int, default=15)
    sh = sub.add_parser("show")
    sh.add_argument("session", nargs="?", default="latest")
    sh.add_argument("--format", choices=formats.FORMATS, default="notes")
    sh.add_argument("--open-only", action="store_true")
    sub.add_parser("inbox")
    v = sub.add_parser("verify")
    v.add_argument("refs", nargs="+")
    r = sub.add_parser("reopen")
    r.add_argument("ref")
    r.add_argument("why")
    s = sub.add_parser("status")
    s.add_argument("ref")
    s.add_argument("status", choices=st.STATUSES)
    s.add_argument("--summary")
    s.add_argument("--evidence", action="append", default=[])
    rp = sub.add_parser("report")
    rp.add_argument("session", nargs="?", default="latest")
    rp.add_argument("-o", "--output")
    rp.add_argument("--open", action="store_true")
    ex = sub.add_parser("export")
    ex.add_argument("session", nargs="?", default="latest")
    ex.add_argument("--format", choices=formats.FORMATS, default="fixlist")
    ex.add_argument("--to")
    lk = sub.add_parser("link")
    lk.add_argument("match", nargs="+")
    lk.add_argument("--name")
    li = sub.add_parser("license")
    li.add_argument("key", nargs="*", help="your key (pasting it in pieces is fine)")
    li.add_argument("--remove", action="store_true", help="remove it from this computer (frees its slot)")
    li.add_argument("--devices", action="store_true", help="the computers using this licence")
    li.add_argument("--deactivate", metavar="N", help="free computer number N from --devices")
    su = sub.add_parser("setup")
    su.add_argument("agent", nargs="?", choices=list(SETUP))
    sub.add_parser("mcp")
    hk = sub.add_parser("hook")
    hk.add_argument("event", choices=["stop", "session-start"])
    sub.add_parser("doctor")
    a = p.parse_args(argv)

    if a.cmd == "mcp":
        from . import mcp
        mcp.serve()
        return 0
    if a.cmd == "hook":
        return hooks.stop() if a.event == "stop" else hooks.session_start()
    if a.cmd == "list":
        for s in all_sessions(limit=a.limit):
            c = st.summary_counts(s)
            state = ", ".join(f"{v} {st.LABELS[k].lower()}" for k, v in c.items() if v)
            print(f"{s.key}  {s.title}  ({state})")
        return 0
    if a.cmd == "show":
        if a.format in ("criteria", "playwright"):
            blocked = lic.pro_required("The criteria and Playwright formats")
            if blocked:
                sys.exit(blocked)
        print(formats.render(_session(a.session), a.format, only_open=a.open_only))
        return 0
    if a.cmd == "inbox":
        todo, waiting = [], []
        for s in all_sessions(limit=60):
            data = st.read(s)
            for n in s.notes:
                state = st.status_of(s, n.id, data)
                line = f"{s.ref(n)}  {n.text.strip()[:90]}"
                if state == "claimed":
                    e = st.entry(s, n.id, data)
                    waiting.append(line + (f"\n      fix: {e['summary']}" if e["summary"] else ""))
                elif state in ("sent", "reopened"):
                    todo.append(line + f"  [{st.LABELS[state]}]")
        print(f"Waiting for you to verify ({len(waiting)}):")
        print("\n".join("  " + w for w in waiting) or "  nothing")
        print(f"\nSent to an agent or reopened ({len(todo)}):")
        print("\n".join("  " + t for t in todo) or "  nothing")
        return 0
    if a.cmd in ("verify", "reopen", "status"):
        refs = a.refs if a.cmd == "verify" else [a.ref]
        for ref in refs:
            s, n = find_note(ref)
            if a.cmd == "verify":
                e = st.set_status(s, n.id, "verified", by="person")
            elif a.cmd == "reopen":
                e = st.set_status(s, n.id, "reopened", by="person", note=a.why)
            else:
                if a.status in ("sent", "claimed"):           # what agents report: same Pro rule as the MCP server
                    blocked = lic.pro_required("Reporting fixes back to Lazy as")
                    if blocked:
                        sys.exit(blocked)
                ev = [{"file": x} if Path(x).expanduser().is_file() else {"text": x} for x in a.evidence]
                e = st.set_status(s, n.id, a.status, by="person", summary=a.summary, evidence=ev)
            print(f"{s.ref(n)}: {st.LABELS[e['status']]}")
        return 0
    if a.cmd == "report":
        s = _session(a.session)
        out = report.write(s, Path(a.output) if a.output else None)
        print(out)
        if a.open:
            webbrowser.open(out.as_uri())
        return 0
    if a.cmd == "export":
        s = _session(a.session)
        if a.format in ("criteria", "playwright"):
            blocked = lic.pro_required("The criteria and Playwright formats")
            if blocked:
                sys.exit(blocked)
        project = projects.load(Path.cwd())
        dest = Path(a.to) if a.to else Path(project["folder"] if project else Path.cwd()) / (
            project["reviews"] if project else ".lazyas/reviews")
        dest = dest / s.key
        dest.mkdir(parents=True, exist_ok=True)
        for n in s.notes:
            for shot in n.shots:
                src = Path(shot.file)
                if src.is_file():
                    shutil.copyfile(src, dest / src.name)
        text = formats.render(s, a.format).replace(str(s.folder), str(dest))
        (dest / f"{a.format}.md").write_text(text, encoding="utf-8")
        print(dest / f"{a.format}.md")
        return 0
    if a.cmd == "link":
        f = projects.link(Path.cwd(), a.match, a.name)
        print(f"{f}: reviews of pages containing {', '.join(json.load(open(f))['match'])} belong to this project.")
        return 0
    if a.cmd == "license":
        return _license(a)
    if a.cmd == "setup":
        exe, args = _launch()
        cmd = " ".join([exe] + args[:-1])
        for key in ([a.agent] if a.agent else list(SETUP)):
            name, text = SETUP[key]
            print(f"## {name}\n" + text.format(exe=exe, args=json.dumps(args), cmd=cmd) + "\n")
        return 0
    if a.cmd == "doctor":
        r = root()
        print(f"lazyas {__version__}\nsessions folder: {r} ({'found' if r.exists() else 'missing'})")
        print(f"reviews: {len(all_sessions(limit=1000))}\nshared state: {HOME_DIR}")
        print(f"plan: {lic.plan()['label']}")
        project = projects.load(Path.cwd())
        print(f"project here: {project['name'] + ' ' + str(project['match']) if project else 'none (lazyas link …)'}")
        return 0
    p.print_help()
    return 0


def _license(a) -> int:
    from . import activation
    if a.remove:
        return _remove_here(activation)
    if a.key:
        try:
            payload = lic.install("".join(a.key))
        except lic.LicenseError as e:
            sys.exit(str(e))
        r = activation.activate("cli")
        print(f"Thanks! {lic.PLANS[payload['plan']]} for {payload.get('email') or 'you'}. {r['message']}")
        for i, d in enumerate(r.get("devices") or [], 1):
            print(f"  {i}. {d['name']} ({d['os']}), last seen {str(d.get('last_seen', ''))[:10]}")
        if r.get("kind") == "limit":
            print("Free one with: lazyas license --deactivate N")
        return 0 if r["ok"] or r.get("offline") else 1
    if a.devices or a.deactivate:
        r = activation.devices()
        if not r["ok"]:
            sys.exit(r["message"] or "Couldn't list computers.")
        if a.deactivate:
            try:
                n = int(a.deactivate)
                if not 1 <= n <= len(r["devices"]):
                    raise ValueError
                target = r["devices"][n - 1]
            except ValueError:
                sys.exit(f"Pick a number from 1 to {len(r['devices'])} (see: lazyas license --devices)")
            if target.get("this") or target.get("device") == activation.device_id():
                return _remove_here(activation)          # freeing this computer = removing the licence here
            lim = r.get("license") or {}
            refused_here = (lic.activation().get("problem") or {}).get("kind") == "limit"
            r = activation.deactivate(target["device"])
            if not r["ok"]:
                sys.exit(r["message"])
            r["license"] = r.get("license") or lim
            print(f"Freed {target['name']}.")
            if refused_here:                             # this computer was the one too many: take the free slot
                print(activation.activate("cli")["message"])
        lim = r.get("license") or {}
        print(f"Computers using this licence ({len(r['devices'])} of {lim.get('devices_max', '?')}):")
        for i, d in enumerate(r["devices"], 1):
            print(f"  {i}. {d['name']} ({d['os']}){' · this one' if d.get('this') else ''}, last seen {str(d.get('last_seen', ''))[:10]}")
        return 0
    info = lic.plan()
    print(info["label"] + (f" · {info.get('email')}" if info.get("email") else ""))
    for extra in ("licence_problem", "note"):
        if info.get(extra):
            print(info[extra])
    if info["source"] == "free":
        print(f"{info['notes_used']} of {lic.FREE_NOTES_PER_MONTH} free notes used this month. Pro: {lic.BUY_URL}")
    return 0


def _remove_here(activation) -> int:
    if lic.current():
        r = activation.deactivate()
        if not r["ok"]:
            print(f"Removed here, but its slot isn't freed online yet ({r['message'].rstrip('.')}). "
                  f"Free it at {lic.ACCOUNT_URL}.")
    lic.remove()
    print("Licence removed from this computer.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
