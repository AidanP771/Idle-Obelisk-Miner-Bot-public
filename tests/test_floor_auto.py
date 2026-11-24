"""Floor Auto guard: switch auto-descend back on, never during trips, pauses or busy locks."""
import importlib.util
from pathlib import Path
import sys
import threading
import types
import unittest
from unittest.mock import Mock, patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]


class FloorAutoTests(unittest.TestCase):
    def setUp(self):
        self.state = types.SimpleNamespace(ingame_action_lock=threading.Lock(), smart_craft_trip=None,
                                           automation_paused=False, floor_auto_restored=0)
        self.settings = types.SimpleNamespace(FLOOR_AUTO_GUARD=True, FLOOR_AUTO_CHECK_INTERVAL_S=30.0)
        frame = np.zeros((1600, 900, 3), np.uint8)
        self.cached = Mock(return_value=(frame, 900, 1600))
        self.fresh = Mock(return_value=(frame, 900, 1600))
        self.tap = Mock(return_value=True)
        stubs = {
            "config.app_state": self.state, "config.settings": self.settings,
            "core.file_logger": types.SimpleNamespace(bot_log=Mock()),
            "core.game_state": types.SimpleNamespace(is_game_running=Mock(return_value=True)),
            "core.screen_cache": types.SimpleNamespace(get_cached_screen=self.cached, get_screen=self.fresh),
            "core.tapper": types.SimpleNamespace(tap=self.tap),
        }
        spec = importlib.util.spec_from_file_location("floor_auto_under_test", ROOT / "features/floor_auto.py")
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, stubs):
            spec.loader.exec_module(module)
        module.time = types.SimpleNamespace(sleep=Mock())
        # 'import config.x as y' binds the already imported real package attribute: set explicitly.
        module.state, module.s = self.state, self.settings
        module.get_cached_screen, module.get_screen, module.tap = self.cached, self.fresh, self.tap
        self.f = module

    def reads(self, *values):
        it = iter(values)
        self.f._read = Mock(side_effect=lambda img: (next(it), (843, 867)))

    def test_on_or_unknown_takes_no_lock_and_no_tap(self):
        for value, expected in ((True, "on"), (None, "unknown")):
            self.reads(value)
            self.assertEqual(self.f.check_once(), expected)
        self.fresh.assert_not_called()
        self.tap.assert_not_called()

    def test_off_is_confirmed_on_a_fresh_frame_then_tapped_and_verified(self):
        self.reads(False, False, True)
        self.assertEqual(self.f.check_once(), "restored")
        self.tap.assert_called_once_with(843, 867, jitter=3)
        self.assertEqual(self.state.floor_auto_restored, 1)
        self.assertFalse(self.state.ingame_action_lock.locked())

    def test_cached_off_but_fresh_on_does_not_tap(self):
        self.reads(False, True)
        self.assertEqual(self.f.check_once(), "on")
        self.tap.assert_not_called()

    def test_tap_without_effect_reports_failed(self):
        self.reads(False, False, False)
        self.assertEqual(self.f.check_once(), "failed")
        self.assertEqual(self.state.floor_auto_restored, 0)

    def test_trip_pause_and_busy_lock_never_tap(self):
        self.state.smart_craft_trip = {"origin": 19, "floor": 11}
        self.reads(False)
        self.assertEqual(self.f.check_once(), "blocked")
        self.state.smart_craft_trip = None
        self.state.automation_paused = True
        self.assertEqual(self.f.check_once(), "blocked")
        self.state.automation_paused = False
        self.reads(False)
        with self.state.ingame_action_lock:
            self.assertEqual(self.f.check_once(), "busy")
        self.tap.assert_not_called()

    def test_trip_started_while_looking_is_respected_under_the_lock(self):
        def start_trip(img):
            self.state.smart_craft_trip = {"origin": 19, "floor": 11}
            return False, (843, 867)
        self.f._read = Mock(side_effect=start_trip)
        self.assertEqual(self.f.check_once(), "blocked")
        self.tap.assert_not_called()
        self.assertFalse(self.state.ingame_action_lock.locked())


if __name__ == "__main__":
    unittest.main()
