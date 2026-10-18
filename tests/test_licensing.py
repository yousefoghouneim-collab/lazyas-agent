"""Licence decisions from what's on disk: every state a person can be in, and the ways someone might try to cheat.
Run: python3 -m unittest discover -s agent/tests
"""
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("LAZYAS_HOME", tempfile.mkdtemp(prefix="lazyas-lic-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lazyas_cli import ed25519  # noqa: E402
from lazyas_cli import license as lic  # noqa: E402

SEED = bytes(range(1, 33))
PUB = ed25519.public_key(SEED)
DEV = "a" * 32
NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
Z = "%Y-%m-%dT%H:%M:%SZ"


def key(plan="pro", order="sub_1", expires="2027-11-01", seed=SEED, issued="2026-10-28"):
    return lic.make({"v": 1, "product": "lazyas", "plan": plan, "email": "a@b.c", "seats": 1, "order": order,
                     "issued": issued, "expires": expires}, seed)


def act_token(lic_id="sub_1", device=DEV, issued=NOW, lease_days=30, expires="2027-11-01", seed=SEED):
    return lic.make({"v": 1, "kind": "activation", "lic": lic_id, "device": device, "plan": "pro", "seats": 1,
                     "expires": expires, "issued": issued.strftime(Z),
                     "lease": (issued + timedelta(days=lease_days)).strftime(Z)}, seed, "LT1")


def trial_token(started=NOW, days=14, device=DEV, seed=SEED):
    return lic.make({"v": 1, "kind": "trial", "device": device, "started": started.strftime(Z), "issued": started.strftime(Z),
                     "ends": (started + timedelta(days=days)).strftime(Z)}, seed, "LT1")


class Decide(unittest.TestCase):
    def setUp(self):
        self._keys = lic.PUBLIC_KEYS
        lic.PUBLIC_KEYS = (PUB,)

    def tearDown(self):
        lic.PUBLIC_KEYS = self._keys

    def d(self, live=True, k=None, now=NOW, **act):
        return lic.decide(now, live, lic.parse(k) if k else None, act, DEV)

    def test_early_access_is_everything(self):
        self.assertEqual(self.d(live=False)["source"], "early-access")
        self.assertTrue(self.d(live=False)["pro"])

    def test_a_key_during_early_access_needs_no_activation(self):
        self.assertEqual(self.d(live=False, k=key())["source"], "licence")

    def test_activated_licence(self):
        p = self.d(k=key(), token=act_token())
        self.assertEqual((p["source"], p["tier"], p["pro"]), ("licence", "pro", True))
        self.assertEqual(self.d(k=key("lifetime", "txn_9", None), token=act_token("txn_9", expires=None))["tier"], "lifetime")

    def test_activation_for_another_computer_or_licence_doesnt_count(self):
        old = (NOW - timedelta(days=30)).strftime(Z)
        self.assertEqual(self.d(k=key(), token=act_token(device="b" * 32), seen={"sub_1": old})["source"], "free")
        self.assertEqual(self.d(k=key(), token=act_token(lic_id="sub_2"), seen={"sub_1": old})["source"], "free")

    def test_a_token_signed_by_anyone_else_is_ignored(self):
        forged = act_token(seed=bytes(32))
        old = (NOW - timedelta(days=30)).strftime(Z)
        self.assertEqual(self.d(k=key(), token=forged, seen={"sub_1": old})["source"], "free")
        with self.assertRaises(lic.LicenseError):
            lic.parse(key(seed=bytes(32)))

    def test_a_licence_key_can_never_pass_as_a_token_or_back(self):
        k = key()
        with self.assertRaises(lic.LicenseError):
            lic.parse_token(k)
        with self.assertRaises(lic.LicenseError):
            lic.parse(act_token())

    def test_new_key_works_offline_for_a_week_then_asks_to_connect(self):
        seen = {"sub_1": (NOW - timedelta(days=2)).strftime(Z)}
        self.assertEqual(self.d(k=key(), seen=seen)["source"], "licence-pending")
        old_key = key(issued="2026-09-01")
        self.assertEqual(self.d(k=old_key, seen=seen)["source"], "free",
                         "an old (maybe refunded) key re-added offline gets no grace")
        late = {"sub_1": (NOW - timedelta(days=8)).strftime(Z)}
        p = self.d(k=key(), seen=late)
        self.assertFalse(p["pro"])
        self.assertIn("internet", p["licence_problem"])

    def test_lease_runs_out_after_30_days_offline(self):
        tok = act_token(issued=NOW - timedelta(days=31))
        self.assertEqual(self.d(k=key(), token=tok, seen={"sub_1": "2026-01-01T00:00:00Z"})["source"], "free")
        self.assertEqual(self.d(k=key(), token=act_token(issued=NOW - timedelta(days=29)))["source"], "licence")

    def test_refunded_or_one_too_many_computers(self):
        for kind in ("revoked", "limit", "invalid"):
            p = self.d(k=key(), seen={"sub_1": NOW.strftime(Z)},
                       problem={"lic": "sub_1", "kind": kind, "message": f"msg {kind}"})
            self.assertFalse(p["pro"], kind)
            self.assertEqual(p["licence_problem"], f"msg {kind}")

    def test_a_refund_ends_pro_at_the_next_renewal_even_with_a_lease_left(self):
        # The server refuses the renewal (problem recorded, token removed by activation.activate).
        p = self.d(k=key(), problem={"lic": "sub_1", "kind": "revoked", "message": "refunded"})
        self.assertFalse(p["pro"])

    def test_expired_key_falls_back_to_trial_or_free(self):
        p = self.d(k=key(expires="2026-10-01"), trial=trial_token(started=NOW - timedelta(days=30)))
        self.assertEqual(p["source"], "free")
        self.assertIn("2026-10-01", p["licence_problem"])

    def test_trial_from_the_website(self):
        p = self.d(trial=trial_token(started=NOW - timedelta(days=3)))
        self.assertEqual((p["source"], p["trial_days_left"]), ("trial", 11))
        self.assertEqual(self.d(trial=trial_token(started=NOW - timedelta(days=15)))["source"], "free")

    def test_reinstalling_doesnt_restart_the_trial(self):
        # The website answers with the original start date; a local "first run" can't override it.
        p = self.d(trial=trial_token(started=NOW - timedelta(days=20)), localTrial=NOW.strftime(Z))
        self.assertEqual(p["source"], "free")

    def test_offline_first_launch_gets_three_days_then_free(self):
        self.assertEqual(self.d(localTrial=(NOW - timedelta(days=1)).strftime(Z))["source"], "trial-local")
        self.assertEqual(self.d(localTrial=(NOW - timedelta(days=4)).strftime(Z))["source"], "free")

    def test_turning_the_clock_back_doesnt_help(self):
        past = NOW - timedelta(days=40)
        t = trial_token(started=NOW - timedelta(days=20))            # signed by the website "now"
        t2 = lic.make({"v": 1, "kind": "trial", "device": DEV, "started": (NOW - timedelta(days=20)).strftime(Z),
                       "issued": NOW.strftime(Z), "ends": (NOW - timedelta(days=6)).strftime(Z)}, SEED, "LT1")
        self.assertEqual(self.d(now=past, trial=t2)["source"], "free", "clock clamped to the website's signed time")
        tok = act_token(issued=NOW - timedelta(days=31))
        self.assertEqual(self.d(now=past, k=key(), token=tok, seen={"sub_1": "2026-01-01T00:00:00Z"},
                                clock=NOW.strftime(Z))["source"], "free", "clock clamped to the newest time seen here")
        del t

    def test_early_access_ends_on_its_date_even_offline(self):
        os.environ.pop("LAZYAS_LICENSING", None)
        self.assertFalse(lic.licensing_live(date(2027, 3, 30)) and not lic.state().get("licensing"))
        self.assertTrue(lic.licensing_live(lic.EARLY_ACCESS_ENDS))

    def test_nothing_switches_paid_plans_off(self):
        lic.update_state(licensing=True)
        for v in ("0", "false", "no", ""):
            os.environ["LAZYAS_LICENSING"] = v
            self.assertTrue(lic.licensing_live(date(2026, 11, 1)), v)
        os.environ.pop("LAZYAS_LICENSING", None)
        lic.update_state(licensing=False)
        lic.update_activation(licensing=True)
        self.assertTrue(lic.licensing_live(date(2026, 11, 1)), "deleting the flag in state.json isn't enough")
        lic.update_activation(licensing=None, trial=trial_token())
        self.assertTrue(lic.licensing_live(date(2026, 11, 1)), "a trial from the website means paid plans are on")
        lic.update_activation(trial=None)

    def test_a_licence_that_ended_says_so_even_with_a_lease_left(self):
        p = self.d(k=key(), token=act_token(expires="2026-10-30"))
        self.assertFalse(p["pro"])
        self.assertIn("ended on 2026-10-30", p["licence_problem"])

    def test_damaged_records_dont_crash(self):
        for act in ({"token": 5, "trial": [], "seen": "x", "problem": "y", "clock": "2026-11-01"},
                    {"localTrial": "2026-11-01", "clock": 7}, {"seen": {"sub_1": "garbage"}}):
            self.assertIn("source", self.d(k=key(), **act))
        self.assertEqual(self.d(localTrial=(NOW + timedelta(hours=1)).strftime(Z))["trial_days_left"], 3)

    def test_backup_key_is_accepted_for_rotation(self):
        lic.PUBLIC_KEYS = (bytes(32), PUB)
        self.assertEqual(lic.parse(key())["plan"], "pro")


class Files(unittest.TestCase):
    def test_install_records_when_first_seen_and_remove_clears_the_activation(self):
        keys = lic.PUBLIC_KEYS
        lic.PUBLIC_KEYS = (PUB,)
        try:
            lic.install(key(expires="2099-01-01"))
            self.assertIn("sub_1", lic.activation()["seen"])
            lic.update_activation(token=act_token())
            if os.name != "nt":
                self.assertEqual(os.stat(lic.license_path()).st_mode & 0o777, 0o600)
            lic.remove()
            self.assertNotIn("token", lic.activation())
            self.assertIsNone(lic.current())
        finally:
            lic.PUBLIC_KEYS = keys

    def test_expired_keys_are_refused_at_install(self):
        keys = lic.PUBLIC_KEYS
        lic.PUBLIC_KEYS = (PUB,)
        try:
            with self.assertRaises(lic.LicenseError):
                lic.install(key(expires="2020-01-01"))
        finally:
            lic.PUBLIC_KEYS = keys


if __name__ == "__main__":
    unittest.main()
