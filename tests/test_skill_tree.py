"""Skill Tree: node detection and points on live frames, the buy loop with
scripted frames, dry run, and the Skill Bit combine / visit trigger in items."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

import core.vision  # noqa: F401  real module, imported before the settings stub

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "tests/data/skill_tree"


def frame(name):
    img = cv2.imread(str(DATA / name))
    assert img is not None, name
    return img


def load(name, rel, stubs):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, stubs):
        spec.loader.exec_module(module)
    return module


class TreeBase(unittest.TestCase):
    def setUp(self):
        import test_smart_craft
        fixture = test_smart_craft.SmartCraftBase()
        fixture.setUp()
        self.sc = fixture.feature                       # digit reader without ADB imports
        self.settings = types.SimpleNamespace(IMG_DIR=ROOT / "images", BASE_DIR=Path(tempfile.mkdtemp()))
        stubs = {
            "config.settings": self.settings,
            "core.file_logger": types.SimpleNamespace(bot_log=Mock()),
            "core.screen_cache": types.SimpleNamespace(get_screen=Mock()),
            "core.scroll": types.SimpleNamespace(scroll=Mock(return_value=True)),
            "core.tapper": types.SimpleNamespace(tap=Mock(return_value=True)),
        }
        self.m = load("skill_tree_under_test", "features/skill_tree.py", stubs)
        self.m.s = self.settings
        self.m.time = types.SimpleNamespace(sleep=Mock(), strftime=lambda *a: "t")
        self.taps = []
        self.m.tap = lambda x, y, jitter=0: self.taps.append((x, y)) or True
        self.m.scroll = Mock(return_value=True)
        self.features_patch = patch.dict(sys.modules, {"features.smart_craft": self.sc})
        self.features_patch.start()
        import features
        self.attr_patch = patch.object(features, "smart_craft", self.sc, create=True)
        self.attr_patch.start()
        self.addCleanup(self.features_patch.stop)
        self.addCleanup(self.attr_patch.stop)

    def play(self, frames):
        """_fresh returns these BGR frames in turn (the last one repeats)."""
        it = iter(frames)
        last = frames[-1]

        def fresh():
            img = next(it, last)
            return img, cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        self.m._fresh = fresh


class NodeReadingTests(TreeBase):
    def test_owned_and_buyable_nodes_on_the_top_page(self):
        img = frame("tree_top.png")
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        self.assertTrue(self.m.tree_open(gray))
        self.assertEqual([(n["x"], n["y"], n["kind"]) for n in self.m.find_nodes(img)],
                         [(448, 336, "owned"), (250, 668, "owned"), (650, 668, "buyable"), (248, 1001, "buyable")])
        self.assertEqual(self.m.read_points(gray), 0)
        self.assertFalse(self.m.cost_lit(img))           # "Swing Harder" selected, 0 points

    def test_dark_and_locked_nodes_are_not_reported(self):
        img = frame("tree_end_locked.png")
        self.assertEqual(self.m.find_nodes(img), [])
        self.assertTrue(self.m.tree_open(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)))

    def test_other_pages_are_not_the_tree(self):
        for name in ("tests/data/freebies/store_freebie_pack.png", "tests/data/smart_craft/workshop_cherry.png"):
            gray = cv2.imread(str(ROOT / name), cv2.IMREAD_GRAYSCALE)
            self.assertFalse(self.m.tree_open(gray), name)


def lit(img):
    out = img.copy()
    out[1330:1435, 705:850] = (60, 50, 160)
    return out


def bought(img, x, y):
    """The node at (x, y) drawn with a gold ring."""
    out = img.copy()
    cv2.rectangle(out, (x - 48, y - 48), (x + 48, y + 48), (60, 170, 220), 12)
    return out


class VisitTests(TreeBase):
    def setUp(self):
        super().setUp()
        self.top = frame("tree_top.png")
        self.open_tile = Mock(side_effect=lambda *a: (self.top, cv2.cvtColor(self.top, cv2.COLOR_BGR2GRAY)))
        self.points = [1]
        self.m.read_points = lambda gray: self.points[0]

    def test_zero_points_reads_only(self):
        self.points = [0]
        r = self.m.visit(self.open_tile, dry_run=False)
        self.assertEqual((r["points"], r["bought"]), (0, 0))
        self.assertEqual(self.taps, [])
        self.m.scroll.assert_not_called()

    def test_affordable_node_is_bought_and_confirmed(self):
        after = bought(self.top, 650, 668)
        # scroll-to-top check, then: node tap -> lit panel, buy -> gold ring, scroll -> same frame (end)
        self.play([self.top, lit(self.top), after, after])
        r = self.m.visit(self.open_tile, dry_run=False)
        self.assertEqual(r["bought"], 1)
        self.assertEqual(self.taps, [(650, 668), (777, 1383)])

    def test_dark_cost_button_is_skipped_and_next_node_tried(self):
        self.play([self.top, self.top, lit(self.top), bought(self.top, 248, 1001)])
        r = self.m.visit(self.open_tile, dry_run=False)
        self.assertEqual((r["unaffordable"], r["bought"]), (1, 1))
        self.assertEqual(self.taps, [(650, 668), (248, 1001), (777, 1383)])

    def test_dry_run_never_presses_the_cost_button(self):
        self.play([self.top, lit(self.top), lit(self.top), self.top])
        r = self.m.visit(self.open_tile, dry_run=True)
        self.assertEqual((r["would_buy"], r["bought"]), (2, 0))
        self.assertNotIn((777, 1383), self.taps)

    def test_unconfirmed_purchase_stops_the_visit(self):
        self.play([self.top, lit(self.top), lit(self.top)])
        with self.assertRaises(self.m.Abort):
            self.m.visit(self.open_tile, dry_run=False)
        self.assertEqual(self.taps, [(650, 668), (777, 1383)])

    def test_unreadable_points_stop_without_taps(self):
        self.points = [None]
        with self.assertRaises(self.m.Abort):
            self.m.visit(self.open_tile, dry_run=False)
        self.assertEqual(self.taps, [])


class ItemsSkillBitTests(unittest.TestCase):
    def setUp(self):
        import test_items
        fixture = test_items.ItemsBase()
        fixture.setUp()
        self.fixture, self.m = fixture, fixture.m

    def test_counts_on_the_bag_with_skill_bit_selected(self):
        img = cv2.imread(str(ROOT / "tests/data/items/bag_skill_bit_selected.png"))
        bag = self.m.read_bag(img, cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
        self.assertEqual({k: v["count"] for k, v in bag.items()},
                         {"juicy_plums": 5, "chaos_totem": 2, "primal_meat": 4, "strawberries": 2,
                          "charge_magnet": 2, "skill_bit": 4, "apple": 25, "bread": 14, "drone_juice": 24,
                          "banana_coffee": 14, "pike": 14, "eye_of_newt": 14, "rock_cake": 4})

    def bags(self, counts):
        seq = [{"skill_bit": {"count": c, "active": False, "x": 590, "y": 1163}} for c in counts]
        it = iter(seq)
        self.m.read_bag = Mock(side_effect=lambda i, g: next(it, seq[-1]))
        self.m._fresh = Mock(return_value=(None, None))

    def test_combines_while_ten_or_more_are_held(self):
        self.bags([24, 14, 4])
        self.m._use_one = Mock(return_value=True)
        combined, bag = self.m._combine_skill_bits(None, None)
        self.assertEqual((combined, bag["skill_bit"]["count"]), (2, 4))
        self.assertEqual(self.m._use_one.call_count, 2)

    def test_fewer_than_ten_or_unreadable_count_is_not_combined(self):
        for count in (4, None):
            self.bags([count])
            self.m._use_one = Mock(return_value=True)
            self.assertEqual(self.m._combine_skill_bits(None, None)[0], 0)
            self.m._use_one.assert_not_called()

    def test_tree_visit_alone_does_not_open_the_bag(self):
        s = self.fixture.settings
        s.ITEMS_KEEP_RUNNING, s.ITEMS_USE_BLUE_COW = "", False
        s.ITEMS_OPEN_CHESTS = s.ITEMS_OPEN_RELICS = False
        s.ITEMS_STRAWBERRIES_WHEN_UNCAPPED = False
        s.ITEMS_COMBINE_SKILL_BITS = False
        s.ITEMS_SKILL_TREE = True
        self.m._open_bag = Mock(side_effect=AssertionError("bag must not open"))
        self.m._close_bag = Mock()
        self.m._close_menu_if_open = Mock()
        self.m._visit_skill_tree = Mock(return_value="Skill Tree: 0 point(s), bought 0")
        self.assertEqual(self.m.run_cycle(), "ok")
        self.m._visit_skill_tree.assert_called_once()
        self.assertEqual(self.m.run_cycle(), "nothing")          # next visit only after the interval

    def test_a_combine_triggers_a_tree_visit(self):
        s = self.fixture.settings
        s.ITEMS_KEEP_RUNNING, s.ITEMS_USE_BLUE_COW = "", False
        s.ITEMS_OPEN_CHESTS = s.ITEMS_OPEN_RELICS = False
        s.ITEMS_STRAWBERRIES_WHEN_UNCAPPED = False
        s.ITEMS_COMBINE_SKILL_BITS = True
        s.ITEMS_SKILL_TREE = True
        self.m._next_tree_ts = 10 ** 9                            # interval not yet due
        self.m._open_bag = Mock(return_value=(None, None))
        self.m._close_bag = Mock()
        self.m._close_menu_if_open = Mock()
        self.m._run_uses = Mock(return_value=([], {}))
        self.m._combine_skill_bits = Mock(return_value=(1, {}))
        self.m._fresh = Mock(return_value=(None, None))
        self.m._record_snapshot = Mock()
        self.m._visit_skill_tree = Mock(return_value="Skill Tree: 1 point(s), bought 1")
        self.assertEqual(self.m.run_cycle(), "ok")
        self.m._visit_skill_tree.assert_called_once()

    def test_skill_tree_settings_are_validated(self):
        from config.validation import validate_settings
        validate_settings({"items": {"ITEMS_COMBINE_SKILL_BITS": True, "ITEMS_SKILL_TREE": False,
                                     "ITEMS_SKILL_TREE_INTERVAL_S": 1800, "ITEMS_SKILL_TREE_DRY_RUN": True}})
        for bad in ({"ITEMS_SKILL_TREE": "yes"}, {"ITEMS_SKILL_TREE_INTERVAL_S": 10}):
            with self.assertRaises(ValueError):
                validate_settings({"items": bad})


if __name__ == "__main__":
    unittest.main()
