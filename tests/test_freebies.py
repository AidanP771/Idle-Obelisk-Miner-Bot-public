"""Freebie Store visits, FIRST/SLOW scheduling, logs and configuration without game access."""
import copy
import json
from pathlib import Path
import tempfile
import sys
import threading
import unittest
from unittest.mock import patch

from flask import Flask

from config.user_overrides import normalize_user_settings, apply_overrides_to_settings
import test_action_results as action_results
import test_screenshot_consumers as consumers
from test_founders import ROOT, load_module


class FreebieScheduleTests(unittest.TestCase):
    def setUp(self):
        action_results.FreebieResultTests.setUp(self)
        self.state.freebies_enabled = True
        self.state.freebies_next_check_ts = 0
        self.state.ingame_action_lock = threading.Lock()
        self.feature.is_game_running.return_value = True

    def packs(self, banked):
        action_results.FreebieResultTests.packs(self, banked)

    def test_disable_or_reactivation_during_a_claim_stops_further_taps(self):
        for reactivate in (False, True):
            with self.subTest(reactivate=reactivate):
                self.state.freebies_enabled = True
                self.feature.tap.reset_mock()
                self.packs([2, 1, 0])

                def disable(*args, **kwargs):
                    self.feature.toggle()
                    if reactivate:
                        self.feature.toggle()
                    return True
                self.feature.tap.side_effect = disable
                self.assertFalse(self.feature._run_claim_cycle())
                self.feature.tap.assert_called_once()
                self.assertEqual(self.state.freebies_claimed_count, 0)

    def test_disable_during_long_wait_stops_before_closing(self):
        self.packs([0])
        self.feature.s.FREEBIES_POST_CLAIM_DELAY_S = 60

        def disable(seconds):
            self.assertLessEqual(seconds, 0.1)
            self.feature.toggle()
        self.feature.time.sleep.side_effect = disable
        self.assertFalse(self.feature._run_claim_cycle())
        self.feature.time.sleep.assert_called_once()
        self.feature.tap.assert_not_called()

    def test_navigation_opens_menu_then_store(self):
        miss = {"found": False, "score": .1}
        menu = {"found": True, "score": 1, "cx": 450, "cy": 1512, "x": 0, "y": 0}
        tile = {"found": True, "score": 1, "cx": 166, "cy": 400, "x": 0, "y": 0}
        # store?, menu ready?, main menu button, menu ready (poll), store tile, store (poll), close
        self.feature.find_template.side_effect = [miss, miss, menu, menu, tile, self.hit, self.hit]
        self.packs([0])
        self.assertTrue(self.feature._run_claim_cycle())
        self.assertEqual([c.args[:2] for c in self.feature.tap.call_args_list],
                         [(450, 1512), (166, 400), (20, 30)])

    def test_store_tile_missing_aborts_without_claim(self):
        miss = {"found": False, "score": .1}
        self.feature.find_template.side_effect = [miss, self.hit, miss]   # store?, menu ready, tile
        self.assertFalse(self.feature._run_claim_cycle())
        self.feature.tap.assert_not_called()
        self.assertEqual(self.feature._check_interval(), 300)

    def test_disabled_statistics_does_not_track_enabled_freebies(self):
        self.state.gem_statistics_enabled = False
        self.feature._sync_statistics(True)
        self.stats.get_gem_statistics.return_value.set_active.assert_called_once_with(False)
        self.feature._run_claim_cycle()
        self.stats.get_gem_statistics.return_value.record_claim.assert_not_called()

    def run_checks(self, count=1):
        sleeps = []

        def sleep(seconds):
            sleeps.append(seconds)
            if len(sleeps) == count + 1:
                self.feature._stop_evt.set()

        with patch.object(self.feature, "_sleep_interruptible", side_effect=sleep):
            self.feature._worker()
        return sleeps

    def messages(self):
        return [call.args[0] for call in self.feature.bot_log.call_args_list]

    def test_completed_visit_schedules_slow_and_failed_visit_first(self):
        self.assertEqual(self.feature._check_interval(), 300)
        self.assertEqual(self.run_checks(), [3, 900])
        self.assertEqual(self.state.freebies_next_check_ts, 1000)
        self.assertEqual(self.state.freebies_claimed_count, 2)
        self.assertFalse(self.state.ingame_action_lock.locked())
        self.assertIn("[FREEBIES] worker thread started (enabled=True, FIRST=300s, SLOW=900s)", self.messages())
        self.assertIn("[FREEBIES] next check in 900s (reason=ok)", self.messages())
        self.feature._stop_evt.clear()
        self.feature.find_template.return_value = {"found": False, "score": .1}   # no Store, no menu
        self.assertEqual(self.run_checks(), [3, 300])
        self.assertIn("[FREEBIES] next check in 300s (reason=visit_failed)", self.messages())

    def test_missing_screenshot_is_a_failed_visit(self):
        self.feature._last_visit_ok = True
        self.feature.get_screen.return_value = (None, 0, 0)
        self.assertFalse(self.feature._run_claim_cycle())
        self.assertEqual(self.feature._check_interval(), 300)
        self.feature.tap.assert_not_called()

    def test_toggle_resets_phase_countdown_and_wakes_worker(self):
        self.feature._run_claim_cycle()
        self.assertEqual(self.feature._check_interval(), 900)
        self.state.freebies_next_check_ts = 1000
        self.assertFalse(self.feature.toggle())
        self.assertEqual(self.state.freebies_next_check_ts, 0)
        self.feature._wake_evt.clear()
        self.assertTrue(self.feature.toggle())
        self.assertTrue(self.feature._wake_evt.is_set())
        self.assertEqual(self.feature._check_interval(), 300)
        self.stats.get_gem_statistics.return_value.set_active.assert_called_with(True)
        self.assertIn("[FREEBIES] toggled -> ON (checking now)", self.messages())
        self.assertIn("[FREEBIES] toggled -> OFF", self.messages())

    def test_start_resets_phase_once_and_keeps_statistics_hook(self):
        self.feature._last_visit_ok = True
        with patch.object(self.feature.threading, "Thread") as thread:
            self.feature.start()
            self.feature.start()
        thread.assert_called_once()
        thread.return_value.start.assert_called_once()
        self.assertEqual(self.feature._check_interval(), 300)
        self.stats.get_gem_statistics.return_value.set_active.assert_called_once_with(True)

    def test_game_absent_retries_remain_short(self):
        self.feature.is_game_running.return_value = False
        for slow in (False, True):
            with self.subTest(slow=slow):
                self.feature._stop_evt.clear()
                self.feature._last_visit_ok = slow
                self.assertEqual(self.run_checks(), [3, 30])
                self.assertEqual(self.state.freebies_next_check_ts, 130)
                self.assertIn("[FREEBIES] next check in 30s (reason=no_game)", self.messages())
        self.feature.get_screen.assert_not_called()

    def test_disabled_worker_never_captures_or_clicks(self):
        self.state.freebies_enabled = False
        self.assertEqual(self.run_checks(), [3, 5])
        self.assertEqual(self.state.freebies_next_check_ts, 0)
        self.feature.get_screen.assert_not_called()
        self.feature.tap.assert_not_called()

    def test_waiting_for_lock_is_logged_and_disable_prevents_queued_claim(self):
        feature = self.feature

        class BusyLock:
            def locked(self):
                return True

            def __enter__(self):
                feature.state.freebies_enabled = False

            def __exit__(self, *args):
                return False

        self.state.ingame_action_lock = BusyLock()
        self.assertEqual(self.run_checks(), [3, 300])
        self.assertIn("[FREEBIES] another feature is busy -> waiting for action lock", self.messages())
        self.feature.get_screen.assert_not_called()
        self.assertEqual(self.state.freebies_next_check_ts, 0)

    def test_pause_waits_without_lock_and_a_pause_during_the_lock_wait_skips_the_claim(self):
        self.state.automation_paused = True
        self.assertEqual(self.run_checks(), [3, 10])
        self.assertIn("[FREEBIES] automation paused -> waiting", self.messages())
        self.feature.get_screen.assert_not_called()
        feature = self.feature

        class PausingLock:
            def locked(self):
                return True

            def __enter__(self):
                feature.state.automation_paused = True   # pause requested while waiting

            def __exit__(self, *args):
                return False

        self.state.automation_paused = False
        self.state.ingame_action_lock = PausingLock()
        self.feature._stop_evt.clear()
        self.assertEqual(self.run_checks(), [3, 10])
        self.feature.get_screen.assert_not_called()
        self.feature.tap.assert_not_called()


class FreebieStoreFrameTests(unittest.TestCase):
    """Live Store frame (2026-11-27): Freebie Pack row with "Banked: 0/2" and a dark Claim."""
    def setUp(self):
        import cv2
        import core.vision as vision
        import test_smart_craft
        consumers.ConsumerSlotTests.setUp(self)
        self.feature = self.freebies
        self.feature.find_template = vision.find_template
        fixture = test_smart_craft.SmartCraftBase()
        fixture.setUp()
        self.sc = fixture.feature
        self.img = cv2.imread(str(ROOT / "tests/data/freebies/store_freebie_pack.png"))

    def read(self, img):
        import features
        with patch.dict(sys.modules, {"features.smart_craft": self.sc}), \
             patch.object(features, "smart_craft", self.sc, create=True):
            return self.feature.read_pack(img)

    def test_reads_banked_count_and_dark_claim(self):
        self.assertEqual(self.read(self.img), {"banked": 0, "lit": False, "claim": (747, 1020)})
        self.assertTrue(self.feature._store_open(self.img))

    def test_lit_claim_is_detected(self):
        img = self.img.copy()
        img[985:1055, 690:805] = (40, 40, 170)
        self.assertTrue(self.read(img)["lit"])

    def test_row_cut_off_at_the_bottom_is_not_used(self):
        self.assertIsNotNone(self.read(self.img[:1100]))   # Claim box ends at y 1055
        self.assertIsNone(self.read(self.img[:1040]))      # label visible, Claim cut off


class FreebieConfigTests(unittest.TestCase):
    def test_legacy_migration_preserves_input_and_explicit_new_values(self):
        old = {"freebies": {"FREEBIES_CHECK_INTERVAL_S": 75}}
        original = copy.deepcopy(old)
        migrated = normalize_user_settings(old)
        self.assertEqual(old, original)
        self.assertEqual(migrated, {"freebies": {"FREEBIES_CHECK_INTERVAL_S_FIRST": 75}})
        self.assertEqual(normalize_user_settings(migrated), migrated)
        old["freebies"].update(FREEBIES_CHECK_INTERVAL_S_FIRST=12,
                              FREEBIES_CHECK_INTERVAL_S_SLOW=2700)
        settings = {}
        apply_overrides_to_settings(settings, old)
        self.assertEqual(settings, {"FREEBIES_CHECK_INTERVAL_S_FIRST": 12,
                                    "FREEBIES_CHECK_INTERVAL_S_SLOW": 2700})

    def test_api_migrates_saves_and_validates_both_intervals(self):
        api = load_module("freebie_config_api_under_test", "web/config_api.py")
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            root = Path(folder)
            (root / "config").mkdir()
            path = root / "config/user_settings.json"
            path.write_text(json.dumps({"freebies": {"FREEBIES_CHECK_INTERVAL_S": 75}}), encoding="utf-8")
            app = Flask(__name__)
            app.register_blueprint(api.config_bp)
            with patch.object(api, "BASE_DIR", root), patch.object(api, "_push_log"):
                client = app.test_client()
                response = client.get("/api/config/settings")
                self.assertEqual(response.status_code, 200)
                data = response.get_json()
                self.assertEqual(data["freebies"]["FREEBIES_CHECK_INTERVAL_S_FIRST"], 75)
                self.assertEqual(data["freebies"]["FREEBIES_CHECK_INTERVAL_S_SLOW"], 900)
                self.assertNotIn("FREEBIES_CHECK_INTERVAL_S", data["freebies"])
                data["freebies"]["FREEBIES_CHECK_INTERVAL_S_SLOW"] = 2800
                self.assertEqual(client.post("/api/config/settings", json=data).status_code, 200)
                saved = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(saved["freebies"], data["freebies"])
                for key in ("FREEBIES_CHECK_INTERVAL_S_FIRST", "FREEBIES_CHECK_INTERVAL_S_SLOW"):
                    for invalid in (0, -1, True, "45", None, float("inf")):
                        bad = copy.deepcopy(data)
                        bad["freebies"][key] = invalid
                        self.assertEqual(client.post("/api/config/settings", json=bad).status_code, 400)
                        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), saved)
