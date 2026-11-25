"""Items: bag reading on live frames, chest/relic flows replayed on captured frames,
reserve/cooldown rules, the use_items workflow step and settings validation."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import Mock, patch

import cv2

import core.vision  # noqa: F401  real module, imported before the settings stub

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "tests/data/items"


def frame(name):
    img = cv2.imread(str(DATA / name))
    assert img is not None, name
    return img, cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


class ItemsBase(unittest.TestCase):
    def setUp(self):
        self.state = types.SimpleNamespace(ingame_action_lock=threading.Lock(), automation_paused=False)
        self.settings = types.SimpleNamespace(IMG_DIR=ROOT / "images", BASE_DIR=ROOT,
                                              ITEMS_ENABLED=True, ITEMS_RESERVE=2)
        self.taps = []
        stubs = {
            "config.app_state": self.state, "config.settings": self.settings,
            "core.file_logger": types.SimpleNamespace(bot_log=Mock()),
            "core.game_state": types.SimpleNamespace(is_game_running=Mock(return_value=True)),
            "core.screen_cache": types.SimpleNamespace(get_screen=Mock(), get_cached_screen=Mock()),
            "core.tapper": types.SimpleNamespace(tap=Mock()),
        }
        spec = importlib.util.spec_from_file_location("items_under_test", ROOT / "features/items.py")
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, stubs):
            spec.loader.exec_module(module)
        module.state, module.s = self.state, self.settings
        module.time = types.SimpleNamespace(sleep=Mock(), time=lambda: 1000.0,
                                            strftime=lambda *a: "", localtime=lambda *a: None)
        module.tap = lambda x, y, jitter=0: self.taps.append((x, y)) or True
        module._RECORD_FILE = Path(tempfile.mkdtemp()) / "item_record.json"
        self.assertEqual(module.load_templates(), (14, 9))
        self.m = module

    def play(self, *names):
        """Each _fresh() returns the next captured frame (the last one repeats)."""
        frames = [frame(n) for n in names]
        it = iter(frames)
        self.m._fresh = lambda: next(it, frames[-1])


class BagReadingTests(ItemsBase):
    def test_live_bag_counts_and_active_timer(self):
        bag = self.m.read_bag(*frame("bag.png"))
        self.assertEqual({k: v["count"] for k, v in bag.items()},
                         {"juicy_plums": 5, "chaos_totem": 3, "primal_meat": 3, "strawberries": 3,
                          "charge_magnet": 1, "skill_bit": 7, "apple": 22, "bread": 22, "drone_juice": 28,
                          "banana_coffee": 21, "pike": 16, "eye_of_newt": 16, "rock_cake": 8})
        self.assertFalse(any(v["active"] for v in bag.values()))   # the green Chaos Totem icon is not a timer
        self.assertNotIn("blue_cow", bag)                           # 0.929 on Drone Juice, but Drone Juice wins
        active = self.m.read_bag(*frame("bag_bread_active.png"))
        self.assertEqual((active["bread"]["count"], active["bread"]["active"]), (20, True))
        self.assertEqual([k for k, v in active.items() if v["active"]], ["bread"])

    def test_bag_open_is_not_fooled_by_a_dimmed_menu(self):
        for name, expected in (("bag.png", True), ("chests_page.png", False), ("relics_page.png", False)):
            self.assertEqual(self.m._bag_open(frame(name)[1]), expected, name)

    def test_item_keys_match_validation(self):
        from config.validation import ITEM_KEYS
        self.assertEqual(set(ITEM_KEYS), set(self.m.ITEMS))
        self.assertEqual(self.m.item_keys("Bread, drone juice, nonsense"), ["bread", "drone_juice"])

    def test_chest_yield_counts_only_gains(self):
        self.assertEqual(self.m.chest_yield({"apple": 2, "bread": 5}, {"apple": 24, "bread": 5, "pike": 16}),
                         {"apple": 22, "pike": 16})


class ChestRelicTests(ItemsBase):
    def test_open_chests_arms_then_confirms(self):
        self.m._open_tile = Mock(return_value=frame("chests_page.png"))
        self.play("chests_confirm.png", "chests_result.png")
        self.assertTrue(self.m.open_chests())
        self.assertEqual(self.taps, [(450, 1150), (450, 1150)])

    def test_no_chests_means_no_tap(self):
        self.m._open_tile = Mock(return_value=frame("chests_result.png"))   # "Open All 0 Chests", dark
        self.assertFalse(self.m.open_chests())
        self.assertEqual(self.taps, [])

    def test_open_relics_taps_only_the_right_side_and_closes_the_list(self):
        self.m._open_tile = Mock(return_value=frame("relics_page.png"))
        self.play("relics_result.png", "relics_page.png")
        self.assertTrue(self.m.open_relics())
        open_all, close = self.taps
        self.assertGreaterEqual(open_all[0], 600)          # Reroll is at x 108-258
        self.assertEqual(close, (450, 1300))
        self.taps.clear()
        self.m._open_tile = Mock(return_value=frame("relics_result.png"))   # all opened: Open All dark
        self.assertFalse(self.m.open_relics())
        self.assertEqual(self.taps, [])


class UseRuleTests(ItemsBase):
    def test_reserve_running_buffs_and_unheld_items(self):
        img, gray = frame("bag_bread_active.png")
        self.m._fresh = lambda: (img, gray)
        self.m._use_one = Mock(return_value=True)
        used, _bag = self.m._run_uses(["bread", "drone_juice", "primal_meat", "blue_cow"], 3, "test", img, gray)
        # bread runs, drone juice 28 > 3, primal meat 3 is not above reserve 3, no blue cow held
        self.assertEqual(used, ["drone_juice"])
        self.assertEqual(self.m._use_one.call_count, 1)

    def test_use_items_respects_cooldown_and_due_times(self):
        self.m._open_bag = Mock(side_effect=AssertionError("bag must not open"))
        self.m.block("obelisk", 7200)
        self.assertEqual(self.m.use_items(["primal_meat"], cooldown_key="obelisk"), [])
        self.m._next_check["rock_cake"] = 5000.0                 # used recently: not due
        self.assertEqual(self.m.use_items(["rock_cake"]), [])

    def test_use_one_selects_checks_icon_and_confirms_lower_count(self):
        bag = self.m.read_bag(*frame("bag.png"))
        self.play("bag_bread_selected.png", "bag_bread_active.png")
        self.assertTrue(self.m._use_one("bread", bag["bread"], bag))
        self.assertEqual(self.taps[0], (bag["bread"]["x"], bag["bread"]["y"]))
        self.assertGreater(self.taps[1][0], 600)                 # the Use button


class StepAndSettingsTests(unittest.TestCase):
    def test_use_items_and_item_cooldown_steps_are_validated(self):
        from config.validation import validate_workflows
        validate_workflows({"wf": {"steps": [
            {"type": "use_items", "items": "primal_meat, rock_cake", "reserve": 0, "cooldown_key": "obelisk"},
            {"type": "item_cooldown", "key": "obelisk", "seconds": 7200}]}})
        for bad in ({"type": "use_items", "items": "golden_banana"}, {"type": "use_items", "items": ""},
                    {"type": "item_cooldown", "key": "x", "seconds": 0}):
            with self.assertRaises(ValueError):
                validate_workflows({"wf": {"steps": [bad]}})

    def test_items_settings_section_is_validated(self):
        from config.validation import validate_settings
        validate_settings({"items": {"ITEMS_ENABLED": True, "ITEMS_KEEP_RUNNING": "bread, pike",
                                     "ITEMS_RESERVE": 2, "ITEMS_CHESTS_INTERVAL_S": 1800}})
        with self.assertRaises(ValueError):
            validate_settings({"items": {"ITEMS_KEEP_RUNNING": "bread, cake"}})


if __name__ == "__main__":
    unittest.main()
