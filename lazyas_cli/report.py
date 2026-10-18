"""A review as one self-contained HTML file (screenshots embedded): send it to a client, attach it to a pull
request, open it anywhere. No cloud. Free reports say "Made with Lazy as"; Pro reports can carry your own name,
colour and logo (~/.lazyas/config.json "branding": {"name", "color", "logo"}).
"""
from __future__ import annotations

import base64
import html
import mimetypes
from pathlib import Path
from typing import Optional

from . import license as lic
from . import status as st
from .sessions import Session, read_config

STATUS_COLORS = {"open": "#EADFC6", "sent": "#E2F0FF", "claimed": "#FFE7A3", "verified": "#C9F0D8",
                 "reopened": "#FFD3CC", "wontfix": "#E5E5E5"}


def _img(path: str, max_bytes: int = 8_000_000) -> Optional[str]:
    p = Path(path)
    try:
        data = p.read_bytes()
    except OSError:
        return None
    if len(data) > max_bytes:
        return None
    mime = mimetypes.guess_type(p.name)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def _e(s) -> str:
    return html.escape(str(s or ""), quote=True)


def branding() -> dict:
    """Your own brand on reports (Pro). Free reports always show the Lazy as footer."""
    b = read_config().get("branding") or {}
    if not b or not lic.plan()["pro"]:
        return {}
    out = {"name": str(b.get("name") or "")[:80], "color": str(b.get("color") or "")[:20]}
    logo = b.get("logo")
    if logo:
        out["logo"] = _img(str(Path(logo).expanduser()), 600_000)
    if not out["color"].startswith("#") or len(out["color"]) not in (4, 7):
        out["color"] = ""
    return out


def render(session: Session, data: Optional[dict] = None, brand: Optional[dict] = None) -> str:
    data = data or st.read(session)
    brand = branding() if brand is None else brand
    accent = brand.get("color") or "#FFD23F"
    counts = st.summary_counts(session, data)
    chips = "".join(f'<span class="chip" style="background:{STATUS_COLORS[k]}">{v} {_e(st.LABELS[k].lower())}</span>'
                    for k, v in counts.items() if v)
    cards = []
    for n in session.notes:
        e = st.entry(session, n.id, data)
        shots = []
        for sh in ([] if n.general else n.shots):
            src = _img(sh.file)
            pointer = []
            if sh.element:
                pointer.append(f"Element: {_e(sh.element)}")
            if sh.whole:
                pointer.append("Whole window, nothing pointed at")
            if sh.near_text:
                pointer.append(f"Text nearby: “{_e(sh.near_text[:140])}”")
            shots.append('<figure>' + (f'<img src="{src}" alt="Screenshot {_e(sh.name)}">' if src else
                                       f'<div class="missing">{_e(sh.name)} (not found)</div>') +
                         (f'<figcaption>“{_e(sh.quote)}”' if sh.quote else '<figcaption>') +
                         ("<br><small>" + " · ".join(pointer) + "</small>" if pointer else "") + '</figcaption></figure>')
        ev = []
        for item in e["evidence"]:
            if item.get("file"):
                src = _img(item["file"])
                if src:
                    ev.append(f'<figure class="after"><img src="{src}" alt="After"><figcaption>After</figcaption></figure>')
            if item.get("text"):
                ev.append(f'<p class="evtext">{_e(item["text"])}</p>')
        summary = f'<p class="agent"><b>Fix:</b> {_e(e["summary"])}</p>' if e["summary"] else ""
        reopened = next((h.get("note") for h in reversed(e["history"]) if h.get("status") == "reopened" and h.get("note")), None)
        why = f'<p class="agent"><b>Reopened:</b> {_e(reopened)}</p>' if reopened and e["status"] == "reopened" else ""
        tag = "General comment" if n.general else _e(n.context or "")
        cards.append(f'''<article class="note" id="n{n.id}">
  <header><span class="num">#{n.id}</span><span class="st" style="background:{STATUS_COLORS[e["status"]]}">{_e(st.LABELS[e["status"]])}</span><span class="ctx">{tag}</span></header>
  <p class="text">{_e(n.text)}</p>
  {"<div class='shots'>" + "".join(shots) + "</div>" if shots else ""}
  {summary}{why}{"<div class='evidence'>" + "".join(ev) + "</div>" if ev else ""}
</article>''')
    who = brand.get("name")
    logo = f'<img class="logo" src="{brand["logo"]}" alt="">' if brand.get("logo") else ""
    foot = (f"{_e(who)} · review notes" if who else
            'Made with <a href="https://lazyas.com">Lazy as</a>: point at your screen, talk, get a fix list.')
    date = session.started.astimezone().strftime("%d %b %Y, %H:%M") if session.started else ""
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(session.title)} · review</title>
<style>
:root{{--bg:#FFFBF1;--card:#fff;--ink:#1B1B1F;--muted:#5F5A4D;--line:#EADFC6;--accent:{accent}}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1B1B1F;--card:#26252B;--ink:#FFFBF1;--muted:#B8B1A0;--line:#3A3842}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}}
main{{max-width:980px;margin:0 auto;padding:28px 16px 60px}}h1{{font-size:clamp(1.6rem,4vw,2.3rem);margin:8px 0 4px;line-height:1.15}}
.logo{{height:40px;width:auto;display:block}}.meta{{color:var(--muted);margin:0 0 10px}}
.chip{{display:inline-block;color:#1B1B1F;font-size:.8rem;font-weight:700;padding:4px 10px;border-radius:999px;margin:0 6px 6px 0}}
.note{{background:var(--card);border:2px solid var(--line);border-radius:16px;padding:16px 18px;margin:16px 0}}
.note header{{display:flex;flex-wrap:wrap;gap:8px;align-items:center}}.num{{font-weight:900;font-size:1.1rem}}
.st{{color:#1B1B1F;font-size:.78rem;font-weight:700;padding:3px 9px;border-radius:999px}}.ctx{{color:var(--muted);font-size:.85rem;overflow-wrap:anywhere}}
.text{{font-size:1.05rem;margin:10px 0}}.shots,.evidence{{display:grid;gap:12px;grid-template-columns:repeat(auto-fill,minmax(min(100%,280px),1fr))}}
figure{{margin:0}}figure img{{width:100%;height:auto;border-radius:10px;border:1px solid var(--line);display:block}}
figcaption{{font-size:.85rem;color:var(--muted);margin-top:4px}}.after figcaption{{font-weight:700;color:var(--ink)}}
.agent,.evtext{{background:color-mix(in srgb,var(--accent) 22%,transparent);border-radius:10px;padding:8px 12px;margin:10px 0 0;white-space:pre-wrap}}
.missing{{padding:30px;border:1px dashed var(--line);border-radius:10px;color:var(--muted)}}
footer{{color:var(--muted);font-size:.85rem;margin-top:30px;border-top:1px solid var(--line);padding-top:14px}}footer a{{color:inherit}}
</style></head><body><main>
{logo}<h1>{_e(session.title)}</h1>
<p class="meta">{_e(date)} · {len(session.notes)} note{"s" if len(session.notes) != 1 else ""}</p>
<div>{chips}</div>
{"".join(cards)}
<footer>{foot}</footer>
</main></body></html>
'''


def write(session: Session, out: Optional[Path] = None) -> Path:
    out = Path(out) if out else session.folder / "report.html"
    out.write_text(render(session), encoding="utf-8")
    return out
