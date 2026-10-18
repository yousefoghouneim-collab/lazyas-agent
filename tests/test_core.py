"""Tests for the lazyas tool: sessions, statuses, formats, projects, licences, report, MCP server and hooks.
Run: python3 -m unittest discover -s agent/tests
"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="lazyas-test-"))
os.environ["LAZYAS_ROOT"] = str(TMP / "sessions")
os.environ["LAZYAS_HOME"] = str(TMP / "home")
os.environ.pop("LAZYAS_LICENSING", None)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lazyas_cli import ed25519, formats, hooks, mcp, projects, report, sessions, status as st  # noqa: E402
from lazyas_cli import license as lic  # noqa: E402

PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                    "1f15c4890000000d49444154789c6360f8cfc000000301010018dd8db00000000049454e44ae426082")
SEED = bytes(range(32))


def make_session(key="2026-10-16_031500", notes=None, name="Checkout polish"):
    folder = Path(os.environ["LAZYAS_ROOT"]) / key
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "01.png").write_bytes(PNG)
    (folder / "02.png").write_bytes(PNG)
    notes = notes or [
        {"id": 1, "text": "This button is too small and the label is cut off.", "time": "2026-10-16T03:15:10Z",
         "context": "Chrome · http://localhost:3000/cart",
         "shots": [{"file": "/elsewhere/01.png", "quote": "this button", "element": 'button "Pay now"',
                    "nearText": "Pay now  Total $42", "rect": [10, 20, 300, 200]}]},
        {"id": 2, "text": "Make the header sticky.", "time": "2026-10-16T03:15:30Z",
         "context": "Chrome · http://localhost:3000/cart", "shots": [{"file": str(folder / "02.png")}]},
        {"id": 3, "text": "Overall the spacing feels cramped.", "time": "2026-10-16T03:16:00Z", "general": True,
         "shots": []},
    ]
    (folder / "session.json").write_text(json.dumps({"started": "2026-10-16T03:15:00Z", "name": name,
                                                      "nameIsAuto": False, "notes": notes}))
    return sessions.load(folder)


class Base(unittest.TestCase):
    def setUp(self):
        for d in ("sessions", "home"):
            shutil.rmtree(TMP / d, ignore_errors=True)
        os.environ.pop("LAZYAS_LICENSING", None)
        self.s = make_session()


class Ed25519Tests(unittest.TestCase):
    def test_rfc8032_vector_1(self):
        seed = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
        pub = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
        sig = bytes.fromhex("e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b")
        self.assertEqual(ed25519.public_key(seed), pub)
        self.assertEqual(ed25519.sign(seed, b""), sig)
        self.assertTrue(ed25519.verify(pub, b"", sig))
        self.assertFalse(ed25519.verify(pub, b"x", sig))
        self.assertFalse(ed25519.verify(pub, b"", sig[:-1] + bytes([sig[-1] ^ 1])))


class SessionTests(Base):
    def test_load_and_refs(self):
        self.assertEqual(self.s.title, "Checkout polish")
        self.assertEqual(len(self.s.notes), 3)
        n1 = self.s.note(1)
        self.assertTrue(n1.shots[0].file.endswith("01.png") and os.path.exists(n1.shots[0].file))   # resolved locally
        self.assertEqual(n1.shots[0].element, 'button "Pay now"')
        self.assertEqual(self.s.ref(n1), "2026-10-16_031500#1")
        s, n = sessions.find_note("latest#2")
        self.assertEqual((s.key, n.id), ("2026-10-16_031500", 2))

    def test_bad_refs(self):
        with self.assertRaises(ValueError):
            sessions.parse_ref("nonsense")
        with self.assertRaises(LookupError):
            sessions.find_note("../etc#1")
        with self.assertRaises(LookupError):
            sessions.find_note("2026-10-16_031500#9")

    def test_newest_first_and_empty_sessions_skipped(self):
        make_session("2026-10-17_090000", name="Later")
        (Path(os.environ["LAZYAS_ROOT"]) / "2026-10-18_000000").mkdir()
        (Path(os.environ["LAZYAS_ROOT"]) / "2026-10-18_000000" / "session.json").write_text('{"started":"2026-10-18T00:00:00Z","notes":[]}')
        self.assertEqual([s.key for s in sessions.all_sessions()], ["2026-10-17_090000", "2026-10-16_031500"])


class StatusTests(Base):
    def test_loop_and_history(self):
        self.assertEqual(st.status_of(self.s, 1), "open")
        st.set_status(self.s, 1, "sent", by="claude-code", agent=True)
        after = TMP / "after.png"
        after.write_bytes(PNG)
        e = st.set_status(self.s, 1, "fixed", by="claude-code", agent=True, summary="Bigger button",
                          evidence=[{"file": str(after)}, "npm test: 12 passed"])
        self.assertEqual(e["status"], "claimed")
        self.assertEqual(len(e["evidence"]), 2)
        self.assertTrue(Path(e["evidence"][0]["file"]).exists())
        self.assertEqual(Path(e["evidence"][0]["file"]).parent.name, "evidence")
        e = st.set_status(self.s, 1, "reopened", note="Still cut off on mobile")
        self.assertEqual([h["status"] for h in e["history"]], ["sent", "claimed", "reopened"])
        st.set_status(self.s, 1, "verified")
        self.assertEqual(st.summary_counts(self.s)["verified"], 1)

    def test_agents_cannot_verify(self):
        with self.assertRaises(st.StatusError):
            st.set_status(self.s, 1, "verified", agent=True)
        with self.assertRaises(st.StatusError):
            st.set_status(self.s, 1, "wontfix", agent=True)        # needs a reason
        with self.assertRaises(st.StatusError):
            st.set_status(self.s, 7, "sent")

    def test_needs_proof(self):
        st.set_status(self.s, 2, "claimed", agent=True)
        self.assertEqual([n.id for n in st.needs_proof(self.s)], [2])
        st.add_evidence(self.s, 2, ["header now position: sticky"])
        self.assertEqual(st.needs_proof(self.s), [])

    def test_session_json_untouched(self):
        before = (self.s.folder / "session.json").read_text()
        st.set_status(self.s, 1, "sent")
        self.assertEqual((self.s.folder / "session.json").read_text(), before)
        self.assertTrue((self.s.folder / "review.json").exists())


class FormatTests(Base):
    def test_all_formats_keep_refs(self):
        for fmt in formats.FORMATS:
            text = formats.render(self.s, fmt)
            self.assertIn("2026-10-16_031500#1", text, fmt)
        notes = formats.render(self.s, "notes")
        self.assertIn('element: button "Pay now"', notes)
        self.assertIn("(general)", notes)
        pw = formats.render(self.s, "playwright")
        self.assertIn("Navigate to http://localhost:3000/cart", pw)
        self.assertIn("Locate button \"Pay now\"", pw)

    def test_fixlist_drops_done_items(self):
        st.set_status(self.s, 2, "verified")
        text = formats.render(self.s, "fixlist")
        self.assertNotIn("#2", text)
        self.assertIn("#1", text)


class ProjectTests(Base):
    def test_link_and_match(self):
        proj = TMP / "shop"
        (proj / "src").mkdir(parents=True, exist_ok=True)
        projects.link(proj, ["localhost:3000"])
        make_session("2026-10-17_100000", name="Other site", notes=[
            {"id": 1, "text": "x", "time": "2026-10-17T10:00:00Z", "context": "Safari · https://other.example", "shots": []}])
        s, project, matched = projects.latest_for(proj / "src")
        self.assertTrue(matched)
        self.assertEqual(s.key, "2026-10-16_031500")
        s2, _, matched2 = projects.latest_for(TMP)
        self.assertFalse(matched2)
        self.assertEqual(s2.key, "2026-10-17_100000")


class LicenseTests(Base):
    def test_keys(self):
        pub = ed25519.public_key(SEED)
        key = lic.make({"v": 1, "product": "lazyas", "plan": "pro", "email": "a@b.c", "expires": "2099-01-01"}, SEED)
        self.assertEqual(lic.parse(key, pub)["email"], "a@b.c")
        with self.assertRaises(lic.LicenseError):
            lic.parse(key, lic.PUBLIC_KEY)                        # signed by someone else
        head, body, sig = key.split(".")
        tampered = json.loads(lic._b64d(body))
        tampered["plan"] = "team"
        with self.assertRaises(lic.LicenseError):
            lic.parse(f"{head}.{lic._b64e(json.dumps(tampered).encode())}.{sig}", pub)
        self.assertTrue(lic.expired({"expires": "2020-01-01"}))
        self.assertFalse(lic.expired({"expires": None}))

    def test_plans(self):
        self.assertEqual(lic.plan()["source"], "early-access")
        os.environ["LAZYAS_LICENSING"] = "1"
        self.assertEqual(lic.plan()["source"], "trial-local")          # offline first launch: a short trial
        lic.update_activation(localTrial="2020-01-01T00:00:00Z")
        p = lic.plan()
        self.assertEqual(p["source"], "free")
        self.assertIsNotNone(lic.pro_required("Something"))
        self.assertEqual(lic.notes_this_month(today=date(2026, 10, 20)), 3)
        self.assertEqual(lic.notes_this_month(today=date(2026, 11, 2)), 0)


class ReportTests(Base):
    def test_self_contained(self):
        st.set_status(self.s, 1, "claimed", summary="<b>bigger</b>", evidence=["done"])
        html = report.render(self.s, brand={})
        self.assertIn("data:image/png;base64,", html)
        self.assertIn("Made with", html)
        self.assertIn("&lt;b&gt;bigger&lt;/b&gt;", html)            # escaped
        self.assertNotIn("<b>bigger</b>", html)
        branded = report.render(self.s, brand={"name": "Studio Nine", "color": "#123456"})
        self.assertIn("Studio Nine", branded)
        self.assertNotIn("Made with", branded)


class McpTests(Base):
    def rpc(self, server, method, params=None, mid=1):
        return server.handle({"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}})

    def test_protocol_and_tools(self):
        srv = mcp.Server(cwd=str(TMP))
        init = self.rpc(srv, "initialize", {"protocolVersion": "2025-06-18", "clientInfo": {"name": "claude-code"}})
        self.assertEqual(init["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(self.rpc(srv, "initialize", {"protocolVersion": "1999"})["result"]["protocolVersion"], mcp.SUPPORTED[0])
        self.assertIsNone(srv.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        names = [t["name"] for t in self.rpc(srv, "tools/list")["result"]["tools"]]
        self.assertIn("set_status", names)
        r = self.rpc(srv, "tools/call", {"name": "get_latest_review", "arguments": {"include_images": True}})["result"]
        self.assertIn("2026-10-16_031500#1", r["content"][0]["text"])
        self.assertEqual(r["content"][1]["type"], "image")
        note = self.rpc(srv, "tools/call", {"name": "get_note", "arguments": {"ref": "latest#1"}})["result"]
        self.assertIn('button "Pay now"', note["content"][0]["text"])
        bad = self.rpc(srv, "tools/call", {"name": "set_status", "arguments": {"ref": "latest#1", "status": "verified"}})
        self.assertTrue(bad["result"]["isError"])
        ok = self.rpc(srv, "tools/call", {"name": "set_status", "arguments": {
            "ref": "latest#1", "status": "claimed", "summary": "Bigger", "evidence": [{"text": "tests pass"}]}})["result"]
        self.assertFalse(ok.get("isError"))
        self.assertEqual(st.entry(self.s, 1)["by"], "claude-code")
        self.assertEqual(self.rpc(srv, "nope")["error"]["code"], -32601)

    def test_free_tier_cannot_write_back(self):
        os.environ["LAZYAS_LICENSING"] = "1"
        lic.update_activation(localTrial="2020-01-01T00:00:00Z")
        srv = mcp.Server(cwd=str(TMP))
        r = self.rpc(srv, "tools/call", {"name": "set_status", "arguments": {"ref": "latest#1", "status": "sent"}})
        self.assertTrue(r["result"]["isError"])
        self.assertIn("Pro", r["result"]["content"][0]["text"])
        read = self.rpc(srv, "tools/call", {"name": "get_latest_review", "arguments": {}})["result"]
        self.assertFalse(read.get("isError"))                      # reading stays free

    def test_stdio_loop(self):
        lines = "\n".join(json.dumps(m) for m in [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]) + "\nnot json\n"
        out = io.StringIO()
        mcp.serve(io.StringIO(lines), out)
        replies = [json.loads(l) for l in out.getvalue().splitlines()]
        self.assertEqual([r.get("id") for r in replies], [1, 2, None])
        self.assertEqual(replies[2]["error"]["code"], -32700)


class HookTests(Base):
    def setUp(self):
        super().setUp()
        hooks._desktop_notice = lambda *a, **k: None
        mcp._remember(str(TMP / "shop"), self.s.key)

    def run_stop(self, active=False):
        out = io.StringIO()
        hooks.stop(io.StringIO(json.dumps({"cwd": str(TMP / "shop" / "src"), "stop_hook_active": active})), out)
        return out.getvalue()

    def test_blocks_claims_without_proof_once(self):
        st.set_status(self.s, 1, "claimed", agent=True)
        out = json.loads(self.run_stop())
        self.assertEqual(out["decision"], "block")
        self.assertIn("2026-10-16_031500#1", out["reason"])
        self.assertEqual(self.run_stop(active=True), "")             # never twice in a row
        events = (Path(os.environ["LAZYAS_HOME"]) / "events.jsonl").read_text().splitlines()
        self.assertEqual(len(events), 1)

    def test_quiet_when_proved(self):
        st.set_status(self.s, 1, "claimed", agent=True, evidence=["screenshot attached"])
        self.assertEqual(self.run_stop(), "")

    def test_session_start_context(self):
        (TMP / "shop").mkdir(exist_ok=True)
        projects.link(TMP / "shop", ["localhost:3000"])
        out = io.StringIO()
        hooks.session_start(io.StringIO(json.dumps({"cwd": str(TMP / "shop")})), out)
        self.assertIn("3 open review note", json.loads(out.getvalue())["hookSpecificOutput"]["additionalContext"])


if __name__ == "__main__":
    unittest.main()
