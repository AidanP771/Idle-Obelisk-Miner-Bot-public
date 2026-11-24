"""Smart Craft regression tests; no ADB calls, bot startup or real game actions.

Run: python -B -m unittest discover -s tests -v
"""
import sys
import threading
import types
import unittest
from unittest.mock import Mock, patch

import numpy as np

from config.user_overrides import apply_overrides_to_settings, normalize_user_settings
from test_founders import ROOT, load_module


GLYPH_GAP = 5


def render(text, height=40):
    """Compose a grayscale text line from the shipped glyph variants."""
    import cv2
    glyphs = {}
    for path in sorted((ROOT / "images/smart_craft/digits").glob("*.png")):
        char = path.stem.split("_")[0]
        glyphs.setdefault("/" if char == "slash" else char, cv2.imread(str(path), 0))
    width = sum(glyphs[c].shape[1] + GLYPH_GAP for c in text) + 20
    img = np.full((height, width), 16, np.uint8)
    x = 10
    for char in text:
        g = glyphs[char]
        y = (height - g.shape[0]) // 2
        img[y:y + g.shape[0], x:x + g.shape[1]][g > 0] = 230
        x += g.shape[1] + GLYPH_GAP
    return img


class SmartCraftBase(unittest.TestCase):
    def setUp(self):
        self.state = types.ModuleType("config.app_state")
        self.state.smart_craft_enabled = True
        self.state.smart_craft_bars_crafted = 0
        self.state.smart_craft_upgrades_bought = 0
        self.state.smart_craft_last_run_ts = 0.0
        self.state.smart_craft_next_check_ts = 0.0
        self.state.smart_craft_last_status = ""
        self.state.ingame_action_lock = threading.Lock()
        self.settings = types.ModuleType("config.settings")
        self.settings.IMG_DIR = ROOT / "images"
        for key, value in {
            "SMART_CRAFT_MAX_PRESSES_PER_BAR": 50, "SMART_CRAFT_MAX_BATCH": 10,
            "SMART_CRAFT_UI_DELAY_S": 0.0, "SMART_CRAFT_TAP_JITTER_PX": 2,
            "SMART_CRAFT_GLYPH_MAX_RATIO": 0.75,
        }.items():
            setattr(self.settings, key, value)
        stubs = {"config.app_state": self.state, "config.settings": self.settings}
        for name, attrs in {
            "core.file_logger": {"bot_log": Mock()},
            "core.game_state": {"is_game_running": Mock(return_value=True)},
            "core.screen_cache": {"get_screen": Mock(return_value=(None, 0, 0))},
            "core.scroll": {"scroll": Mock(return_value=True)},
            "core.tapper": {"tap": Mock(return_value=True)},
            "core.vision": {"find_template": Mock()},
        }.items():
            module = types.ModuleType(name)
            module.__dict__.update(attrs)
            stubs[name] = module
        with patch.dict(sys.modules, stubs):
            self.feature = load_module("smart_craft_under_test", "features/smart_craft.py")
        self.feature.s = self.settings
        self.feature.state = self.state
        self.feature.time = types.SimpleNamespace(sleep=Mock(), time=lambda: 100.0)
        bars, glyphs = self.feature.load_templates()
        self.assertEqual(glyphs, 11)
        self.assertGreaterEqual(set(self.feature._bars),
                                {"tin", "copper", "mithril", "adamant", "runite", "citrine", "amethyst"})
        self.assertEqual(bars, len(self.feature._bars))


class ReadingTests(SmartCraftBase):
    def test_reads_every_digit_and_cost_text(self):
        for text in ("0123456789", "10341", "0/35", "1049/8", "7"):
            with self.subTest(text=text):
                img = render(text)
                self.assertEqual(self.feature.read_text(img, (0, 0, img.shape[1], img.shape[0])), text)

    def test_stray_blob_or_empty_roi_is_unreadable(self):
        img = render("12")
        img[24:28, img.shape[1] - 8:img.shape[1] - 4] = 230   # '.'-sized blob
        self.assertIsNone(self.feature.read_text(img, (0, 0, img.shape[1], img.shape[0])))
        blank = np.full((40, 80), 16, np.uint8)
        self.assertIsNone(self.feature.read_text(blank, (0, 0, 80, 40)))

    def test_parsing(self):
        self.assertEqual(self.feature.parse_cost("12/35"), (12, 35))
        for bad in (None, "", "35", "1/2/3", "/5", "5/0"):
            self.assertIsNone(self.feature.parse_cost(bad))
        self.assertEqual(self.feature.parse_int("0"), 0)
        self.assertIsNone(self.feature.parse_int("1/2"))

    def test_shortfall_sums_needs_and_ignores_covered_bars(self):
        rows = [("adamant", 0, 35, 397), ("adamant", 0, 8, 1300), ("runite", 0, 10, 1650),
                ("tin", 900, 50, 800)]
        self.assertEqual(self.feature.compute_shortfalls(rows, "all"),
                         {"adamant": (0, 43), "runite": (0, 10)})

    def test_next_target_is_the_cheapest_row_per_bar(self):
        rows = [("adamant", 0, 35, 397), ("adamant", 0, 8, 1300), ("runite", 12, 10, 1650),
                ("tin", 900, 50, 800)]
        self.assertEqual(self.feature.compute_shortfalls(rows), {"adamant": (0, 8)})  # default "next"
        self.settings.SMART_CRAFT_CRAFT_TARGET = "all"
        self.assertEqual(self.feature.compute_shortfalls(rows), {"adamant": (0, 43)})


class ListShiftTests(SmartCraftBase):
    def setUp(self):
        super().setUp()
        self.settings.SMART_CRAFT_LIST_ROI = (0, 100, 300, 1300)
        rng = np.random.default_rng(7)
        self.content = (rng.random((3000, 300)) * 255).astype(np.uint8)

    def frame(self, scrolled):
        img = np.zeros((1400, 300), np.uint8)
        img[100:1300] = self.content[scrolled:scrolled + 1200]
        return img

    def test_measures_large_shifts_in_both_directions(self):
        for start, end in ((0, 950), (500, 520), (900, 100), (300, 300)):
            with self.subTest(start=start, end=end):
                shift = self.feature.list_shift(self.frame(start), self.frame(end), content_up=end >= start)
                self.assertEqual(shift, end - start)

    def test_repetitive_or_flat_content_is_unknown(self):
        row = (np.random.default_rng(1).random((150, 300)) * 255).astype(np.uint8)
        self.content = np.vstack([row] * 20)
        self.assertIsNone(self.feature.list_shift(self.frame(0), self.frame(450)))
        self.content[:] = 40
        self.assertIsNone(self.feature.list_shift(self.frame(0), self.frame(450)))


class CraftTests(SmartCraftBase):
    def run_craft(self, readings, have=0, target=10):
        rows = iter(readings)
        self.feature._fresh = Mock(return_value=(None, None))
        self.feature._locate_craft_row = Mock(side_effect=lambda *_a: next(rows))
        self.feature._read_stable_row = Mock(side_effect=lambda *_a: next(rows))
        return self.feature.craft_bar("adamant", have, target, self.feature._run_generation)

    def test_stable_read_requires_two_equal_frames(self):
        self.feature._fresh = Mock(return_value=(None, None))
        a, b = self.row(3), self.row(5)
        self.feature.read_craft_row = Mock(side_effect=[a, b, b, self.row(5, ore=1010)])
        self.assertEqual(self.feature._read_stable_row("adamant")["ore"], 1010)  # ore may change
        self.feature.read_craft_row = Mock(side_effect=[a, b] * 3)
        self.assertIsNone(self.feature._read_stable_row("adamant"))

    @staticmethod
    def row(bars, ore=1000, ratio=8):
        return {"ore": ore, "bars": bars, "ratio": ratio, "button": (713, 900)}

    def test_leftover_ore_is_crafted_in_1x_then_5x_is_restored(self):
        f = self.feature
        self.settings.SMART_CRAFT_CRAFT_TARGET = "all"   # all ore is used, even far below the target
        f.select_quantity = Mock(return_value=True)
        rows = iter([self.row(0, ore=18, ratio=40),   # 5x: not enough for one press
                     self.row(0, ore=18, ratio=8),    # 1x confirmed
                     self.row(1, ore=10, ratio=8),    # probe press
                     self.row(2, ore=2, ratio=8)])    # one more, ore used up
        f._fresh = Mock(return_value=(None, None))
        f._locate_craft_row = Mock(side_effect=lambda *_a: next(rows))
        f._read_stable_row = Mock(side_effect=lambda *_a: next(rows))
        self.assertEqual(f.craft_bar("adamant", 0, 10, f._run_generation, "5x"), 2)
        self.assertEqual([c.args for c in f.select_quantity.call_args_list], [("1x",), ("5x",)])
        self.assertEqual(f.tap.call_count, 2)  # probe press, then one more

    def test_next_mode_uses_1x_only_when_the_leftover_reaches_the_target(self):
        f = self.feature
        f.select_quantity = Mock(return_value=True)
        f._fresh = Mock(return_value=(None, None))
        f._locate_craft_row = Mock(return_value=self.row(0, ore=18, ratio=40))   # 2 presses in 1x
        self.assertEqual(f.craft_bar("adamant", 0, 10, f._run_generation, "5x"), 0)
        f.select_quantity.assert_not_called()                                    # 2 < 10: keep the ore
        self.assertIn("adamant", f._ore_missing)
        rows = iter([self.row(0, ore=18, ratio=8), self.row(1, ore=10, ratio=8), self.row(2, ore=2, ratio=8)])
        f._read_stable_row = Mock(side_effect=lambda *_a: next(rows))
        self.assertEqual(f.craft_bar("adamant", 0, 2, f._run_generation, "5x"), 2)  # 2 presses reach 2
        self.assertEqual([c.args for c in f.select_quantity.call_args_list], [("1x",), ("5x",)])

    def test_unconfirmed_1x_switch_stops_without_crafting(self):
        f = self.feature
        self.settings.SMART_CRAFT_CRAFT_TARGET = "all"
        f.select_quantity = Mock(return_value=True)
        rows = iter([self.row(0, ore=18, ratio=40), self.row(0, ore=18, ratio=40)])
        f._fresh = Mock(return_value=(None, None))
        f._locate_craft_row = Mock(side_effect=lambda *_a: next(rows))
        f._read_stable_row = Mock(side_effect=lambda *_a: next(rows))
        self.assertEqual(f.craft_bar("adamant", 0, 10, f._run_generation, "5x"), 0)
        f.tap.assert_not_called()

    def test_batches_until_target_and_counts_only_verified_bars(self):
        crafted = self.run_craft([self.row(0), self.row(1), self.row(10)])
        self.assertEqual(crafted, 10)
        self.assertEqual(self.state.smart_craft_bars_crafted, 10)
        self.assertEqual(self.feature.tap.call_count, 10)   # 1 probe press, then 9
        self.feature.tap.assert_called_with(713, 900, jitter=2)

    def test_mismatch_with_upgrades_reading_never_taps(self):
        self.assertEqual(self.run_craft([self.row(5)], have=0), 0)
        self.feature.tap.assert_not_called()

    def test_no_progress_twice_or_implausible_change_stops_without_counting(self):
        for readings, taps in (([self.row(0), self.row(0), self.row(0)], 2),
                               ([self.row(0), self.row(500)], 1)):
            with self.subTest(readings=readings):
                self.state.smart_craft_bars_crafted = 0
                self.feature.tap.reset_mock()
                self.assertEqual(self.run_craft(readings), 0)
                self.assertEqual(self.feature.tap.call_count, taps)
                self.assertEqual(self.state.smart_craft_bars_crafted, 0)

    def test_single_dropped_press_is_retried(self):
        crafted = self.run_craft([self.row(0), self.row(0), self.row(5), self.row(10)])
        self.assertEqual(crafted, 10)
        self.assertEqual(self.feature.tap.call_count, 3)

    def test_switches_to_5x_only_when_inactive(self):
        red = np.zeros((10, 10, 3), np.uint8)
        red[:, :, 2] = 170
        green = np.zeros((10, 10, 3), np.uint8)
        green[:, :, 1] = 170
        self.feature._button = Mock(return_value={"found": True, "x": 0, "y": 0, "w": 10, "h": 10,
                                                   "cx": 160, "cy": 1432, "score": 1.0})
        self.feature._fresh = Mock(side_effect=[(red, None), (green, None)])
        self.assertTrue(self.feature.select_quantity_5x())
        self.feature.tap.assert_called_once_with(160, 1432, jitter=2)
        self.feature.tap.reset_mock()
        self.feature._fresh = Mock(return_value=(green, None))
        self.assertTrue(self.feature.select_quantity_5x())
        self.feature.tap.assert_not_called()

    def test_insufficient_ore_and_press_limit(self):
        self.assertEqual(self.run_craft([self.row(0, ore=7)]), 0)
        self.feature.tap.assert_not_called()
        self.settings.SMART_CRAFT_MAX_PRESSES_PER_BAR = 3
        crafted = self.run_craft([self.row(0), self.row(1), self.row(3)], target=50)
        self.assertEqual((crafted, self.feature.tap.call_count), (3, 3))

    def test_failed_tap_aborts_without_counting(self):
        self.feature.tap.return_value = False
        with self.assertRaises(self.feature._Abort):
            self.run_craft([self.row(0)])
        self.assertEqual(self.state.smart_craft_bars_crafted, 0)

    def test_toggle_cancels_a_running_cycle(self):
        generation = self.feature._run_generation
        self.feature.toggle()
        with self.assertRaises(self.feature._Abort):
            self.feature._check_cancel(generation)


class BuyTests(SmartCraftBase):
    def setUp(self):
        super().setUp()
        self.feature._open_page = Mock(return_value=(None, None))
        self.feature._fresh = Mock(return_value=(None, None))
        self.feature.list_pages = lambda img, gray, gen: iter([(img, gray, 0)])
        self.feature._button = Mock(return_value={"found": False})

    def test_buys_affordable_rows_and_counts_only_visible_changes(self):
        affordable = ("adamant", 43, 35, 397, 760, 400, True)
        expensive = ("runite", 0, 10, 900, 760, 900, False)
        cheaper_now = ("adamant", 8, 8, 1200, 760, 1200, True)
        frames = [[affordable, expensive, cheaper_now],                # page read
                  [("adamant", 8, 45, 397, 760, 400, False), expensive, cheaper_now],  # after buy 1
                  [("adamant", 8, 45, 397, 760, 400, False), expensive, cheaper_now],  # loop read
                  [("adamant", 0, 45, 397, 760, 400, False), expensive],              # after buy 2
                  [("adamant", 0, 45, 397, 760, 400, False), expensive]]
        self.feature.read_upgrade_rows = Mock(side_effect=frames)
        self.assertEqual(self.feature.buy_upgrades(self.feature._run_generation), 2)
        self.assertEqual(self.state.smart_craft_upgrades_bought, 2)
        self.assertEqual([c.args[:2] for c in self.feature.tap.call_args_list], [(760, 400), (760, 1200)])

    def test_purchase_without_visible_effect_is_not_counted(self):
        row = ("adamant", 43, 35, 397, 760, 400, True)
        self.feature.read_upgrade_rows = Mock(return_value=[row])
        self.assertEqual(self.feature.buy_upgrades(self.feature._run_generation), 0)
        self.assertEqual(self.feature.tap.call_count, 1)
        self.assertEqual(self.state.smart_craft_upgrades_bought, 0)


class RoundTests(SmartCraftBase):
    def setUp(self):
        super().setUp()
        self.settings.SMART_CRAFT_MAX_ROUNDS = 3
        f = self.feature
        f._open_page = Mock(return_value=(None, None))
        f._close_menu = Mock()
        f.select_quantity_5x = Mock(return_value=True)
        f.craft_bar = Mock(return_value=5)
        f.scan_workshop = Mock(return_value=[])
        f.buy_workshop = Mock(return_value=0)

    def test_workshop_needs_join_targets_and_are_bought_after_upgrades(self):
        f = self.feature
        self.settings.SMART_CRAFT_CRAFT_TARGET = "all"   # targets add up across Upgrades and Workshop
        f._floor_step = Mock()
        f.scan_upgrades = Mock(return_value=[("runite", 10, 20, 400)])
        f.scan_workshop = Mock(return_value=[("runite", 10, 350, 600), ("citrine", 0, 300, 600)])
        order = []
        f.buy_upgrades = Mock(side_effect=lambda g: order.append("upgrades") or 0)
        f.buy_workshop = Mock(side_effect=lambda g: order.append("workshop") or 0)
        f.run_cycle()
        targets = {c.args[0]: c.args[2] for c in f.craft_bar.call_args_list}
        self.assertEqual(targets, {"runite": 370, "citrine": 300})
        self.assertEqual(order, ["upgrades", "workshop"])
        self.settings.SMART_CRAFT_WORKSHOP = False
        f.scan_workshop.reset_mock()
        f.buy_workshop.reset_mock()
        f.run_cycle()
        f.scan_workshop.assert_not_called()
        f.buy_workshop.assert_not_called()

    def test_rounds_repeat_while_purchases_happen(self):
        f = self.feature
        f.scan_upgrades = Mock(return_value=[("adamant", 0, 10, 400)])
        f.buy_upgrades = Mock(side_effect=[1, 1, 0])
        self.assertEqual(f.run_cycle(), "ok")
        self.assertEqual(f.buy_upgrades.call_count, 3)
        self.assertEqual(f.craft_bar.call_count, 3)
        self.assertIn("crafted 15 bar(s), bought 2", self.state.smart_craft_last_status)

    def test_round_limit_and_nothing_needed(self):
        f = self.feature
        f.scan_upgrades = Mock(return_value=[("adamant", 0, 10, 400)])
        f.buy_upgrades = Mock(return_value=1)
        f.run_cycle()
        self.assertEqual(f.buy_upgrades.call_count, 3)
        f.scan_upgrades = Mock(return_value=[])
        f.buy_upgrades = Mock(return_value=0)
        f.craft_bar.reset_mock()
        self.assertEqual(f.run_cycle(), "nothing_needed")
        f.craft_bar.assert_not_called()


class WorkshopTests(SmartCraftBase):
    def setUp(self):
        super().setUp()
        self.settings.SMART_CRAFT_LIST_ROI = (40, 170, 860, 1395)
        f = self.feature
        f.find_all = Mock(return_value=[])                     # no coin icons
        f.find_bar_icons = Mock(return_value=[("mithril", 145, 596, 0.9), ("adamant", 330, 597, 0.9),
                                              ("runite", 513, 597, 1.0), ("citrine", 145, 980, 0.9)])
        # (icon x, icon y) -> ("have/" line, "need" line), as rendered in the Workshop
        texts = {(145, 596): ("0/", "85"), (330, 597): ("0/", "68"),
                 (513, 597): ("12/", "51"), (145, 980): ("300/", "300")}

        def read_text(_g, roi):
            for (ix, iy), (have, need) in texts.items():
                if roi[0] == ix + 54 and roi[1] == iy - 6:
                    return have
                if roi[0] == ix + 54 and roi[1] == iy + 26:
                    return need
            return None
        f.read_text = read_text
        img = np.zeros((1600, 900, 3), np.uint8)
        img[970:1010, 700:780, 2] = 170   # bright Buy for the citrine row
        self.img = img
        f._button = Mock(side_effect=lambda g, tpl, roi: {"found": True, "x": 700, "y": roi[1] + 50, "w": 80,
                                                          "h": 40, "cx": 740, "cy": roi[1] + 70, "score": 1.0})

    def test_groups_costs_per_row_and_reads_have_and_need(self):
        rows = self.feature.read_workshop_rows(self.img, None)
        self.assertEqual([r["costs"] for r in rows],
                         [[("mithril", 0, 85), ("adamant", 0, 68), ("runite", 12, 51)],
                          [("citrine", 300, 300)]])
        self.assertEqual([r["affordable"] for r in rows], [False, True])

    def test_workshop_rows_with_a_gold_cost_are_skipped_unless_gold_upgrades_are_on(self):
        f = self.feature
        f.find_all = Mock(return_value=[(700, 975)])          # coin in the citrine row
        rows = f.read_workshop_rows(self.img, None)            # default off
        self.assertEqual([r["costs"][0][0] for r in rows], ["mithril"])
        f.find_all.reset_mock()
        self.settings.SMART_CRAFT_BUY_GOLD_UPGRADES = True
        self.assertEqual(len(f.read_workshop_rows(self.img, None)), 2)
        f.find_all.assert_not_called()                         # no coin search when gold may be spent

    def test_buys_only_rows_with_all_costs_met(self):
        f = self.feature
        f._open_page = Mock(return_value=(self.img, None))
        f.list_pages = lambda img, gray, gen: iter([(img, gray, 0)])
        bought_frame = [{"costs": [("citrine", 0, 400)], "y": 980, "buy": (740, 980), "affordable": False}]
        reads = [f.read_workshop_rows(self.img, None), bought_frame, bought_frame]
        f.read_workshop_rows = Mock(side_effect=reads)
        f._fresh = Mock(return_value=(self.img, None))
        self.assertEqual(f.buy_workshop(f._run_generation), 1)
        self.assertEqual(f.tap.call_count, 1)
        self.assertEqual(self.state.smart_craft_upgrades_bought, 1)


class TwoCostTests(SmartCraftBase):
    def test_number_stops_at_the_next_cost_icon(self):
        f = self.feature
        f.find_bar_icons = Mock(return_value=[("tin", 300, 400, 0.99), ("copper", 520, 400, 0.99)])
        f.find_all = Mock(return_value=[])
        f._button = Mock(return_value={"found": True})
        f.read_text = Mock(return_value="3/10")
        rows = f.read_upgrade_rows(None, None)
        self.assertEqual([r[0] for r in rows], ["tin", "copper"])
        first_roi = f.read_text.call_args_list[0].args[1]
        self.assertEqual(first_roi[2], 516)   # copper icon at x=520 minus 4

    def test_rows_with_a_gold_cost_are_skipped_unless_gold_upgrades_are_on(self):
        f = self.feature
        f.find_bar_icons = Mock(return_value=[("tin", 300, 400, 0.99), ("copper", 300, 600, 0.99)])
        f.find_all = Mock(return_value=[(520, 402)])          # coin in the tin row only
        f._button = Mock(return_value={"found": True})
        f.read_text = Mock(return_value="3/10")
        self.assertEqual([r[0] for r in f.read_upgrade_rows(None, None)], ["copper"])   # default off
        self.settings.SMART_CRAFT_BUY_GOLD_UPGRADES = True
        self.assertEqual([r[0] for r in f.read_upgrade_rows(None, None)], ["tin", "copper"])


class GoldUpgradeTests(SmartCraftBase):
    """Gold-cost rows: coin icon, no bar icon; bought only with the option on."""
    def setUp(self):
        super().setUp()
        self.settings.SMART_CRAFT_LIST_ROI = (40, 170, 860, 1395)
        f = self.feature
        f.find_bar_icons = Mock(return_value=[("adamant", 340, 297, 0.99)])
        f.find_all = Mock(return_value=[(341, 472), (341, 819)])   # two gold rows
        self.img = np.zeros((1600, 900, 3), np.uint8)
        self.img[440:500, 700:800, 2] = 170                        # first gold row: lit Buy
        self.gray = np.zeros((1600, 900), np.uint8)
        f._button = Mock(side_effect=lambda g, tpl, roi: {"found": True, "x": 700, "y": roi[1] + 20, "w": 100,
                                                          "h": 40, "cx": 750, "cy": roi[1] + 40, "score": 1.0})

    def test_reads_gold_rows_without_bar_rows(self):
        rows = self.feature.read_gold_rows(self.img, self.gray)
        self.assertEqual([(r[0], r[3]) for r in rows], [(472, True), (819, False)])

    def test_bought_only_with_the_option_and_verified_by_the_cost_text(self):
        f = self.feature
        f.read_upgrade_rows = Mock(return_value=[])
        f._open_page = Mock(return_value=(self.img, self.gray))
        f.list_pages = lambda img, gray, gen: iter([(img, gray, 0)])
        f._fresh = Mock(return_value=(self.img, self.gray))
        self.assertEqual(f.buy_upgrades(f._run_generation), 0)    # default off: gold untouched
        f.tap.assert_not_called()
        self.settings.SMART_CRAFT_BUY_GOLD_UPGRADES = True
        after = self.gray.copy()
        after[480:520, 391:671] = 200                               # cost text changed after the tap
        bought_img = np.zeros_like(self.img)                        # no lit Buy any more
        f._fresh = Mock(return_value=(bought_img, after))
        self.assertEqual(f.buy_upgrades(f._run_generation), 1)
        self.assertEqual(f.tap.call_count, 1)
        self.assertEqual(self.state.smart_craft_upgrades_bought, 1)

    def test_unchanged_row_after_tap_stops_buying(self):
        f = self.feature
        self.settings.SMART_CRAFT_BUY_GOLD_UPGRADES = True
        f.read_upgrade_rows = Mock(return_value=[])
        f._open_page = Mock(return_value=(self.img, self.gray))
        f.list_pages = lambda img, gray, gen: iter([(img, gray, 0)])
        f._fresh = Mock(return_value=(self.img, self.gray))         # same frame: no effect
        self.assertEqual(f.buy_upgrades(f._run_generation), 0)
        self.assertEqual(f.tap.call_count, 1)


class LockTests(SmartCraftBase):
    def test_busy_lock_absent_game_and_missing_templates_do_nothing(self):
        self.feature.run_cycle = Mock(return_value="ok")
        with self.state.ingame_action_lock:
            self.assertEqual(self.feature._try_run_with_lock(), "busy")
        self.feature.is_game_running.return_value = False
        self.assertEqual(self.feature._try_run_with_lock(), "no_game")
        self.feature._glyphs = []
        self.assertEqual(self.feature._try_run_with_lock(), "error")
        self.feature.run_cycle.assert_not_called()

    def test_pause_starts_no_cycle_and_skips_yielding(self):
        self.feature.run_cycle = Mock(return_value="ok")
        self.state.automation_paused = True
        self.assertEqual(self.feature._try_run_with_lock(), "paused")
        self.feature.run_cycle.assert_not_called()
        self.assertFalse(self.state.ingame_action_lock.locked())
        # A cycle already running finishes instead of waiting for paused workflows.
        import time as _time
        self.state.workflows_waiting = {"obelisk_fight": _time.time()}
        self.feature._lock_owned = True
        try:
            self.assertFalse(self.feature._yield_point(self.feature._run_generation))
        finally:
            self.feature._lock_owned = False

    def test_lock_is_released_on_error(self):
        self.feature.run_cycle = Mock(side_effect=RuntimeError("simulated failure"))
        with self.assertRaises(RuntimeError):
            self.feature._try_run_with_lock()
        self.assertFalse(self.state.ingame_action_lock.locked())

    def test_cycle_errors_close_the_menu_and_report_status(self):
        self.feature._open_page = Mock(side_effect=self.feature._Abort("main menu did not open"))
        self.feature._close_menu = Mock()
        self.assertEqual(self.feature.run_cycle(), "error")
        self.feature._close_menu.assert_called_once()
        self.assertEqual(self.state.smart_craft_last_status, "stopped: main menu did not open")


class FloorTripTests(SmartCraftBase):
    def setUp(self):
        super().setUp()
        import tempfile
        from pathlib import Path
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.feature._TRIP_FILE = Path(self.tmp.name) / "trip.json"
        self.state.smart_craft_trip = None
        self.settings.SMART_CRAFT_FLOOR_TRIPS = True
        self.settings.SMART_CRAFT_FLOOR_TRIP_S = 600.0

    def panel(self, unlocked=19, current=19, world1=True):
        """Synthetic Floor Select panel: red buttons, the current one green."""
        img = np.full((1600, 900, 3), 60, np.uint8)
        if world1:
            img[115:150, 560:607] = (40, 180, 40)
        for floor in range(1, unlocked + 1):
            x, y = self.feature._floor_center(floor)
            img[y - 16:y + 17, x - 50:x + 50] = (40, 180, 40) if floor == current else (40, 40, 170)
        return img

    def test_reads_floor_buttons_and_the_current_floor(self):
        floors = self.feature.read_floor_panel(self.panel())
        self.assertEqual(sorted(floors), list(range(1, 20)))
        self.assertEqual(self.feature.current_floor_from(floors), 19)
        self.assertEqual(self.feature._floor_center(19), (342, 649))
        self.assertEqual(self.feature.read_floor_panel(self.panel(world1=False)), {})

    def test_best_floor_prefers_the_largest_share_and_only_lower_floors(self):
        best = self.feature.best_floor
        self.assertEqual(best({"runite": 400}, 19), 9)                      # runite 80 %
        self.assertEqual(best({"runite": 300, "citrine": 200}, 19), 9)      # runite 80 %, citrine 20 %
        self.assertEqual(best({"runite": 100, "citrine": 300}, 19), 11)     # citrine 80 %
        self.assertIsNone(best({"adamant": 100}, 4))                        # only drops higher up
        self.assertIsNone(best({"citrine": 100}, 10))                       # current floor drops it
        self.assertIsNone(best({"topaz": 100}, 1))

    def test_ore_shortage_starts_a_trip_and_records_auto(self):
        f = self.feature
        f._open_floor_panel = Mock(return_value=(None, {n: "red" for n in range(1, 19)} | {19: "green"}))
        f._close_floor_panel = Mock()
        f.change_floor = Mock(return_value=19)
        f.floor_auto_on = Mock(return_value=True)    # read before moving
        f.set_floor_auto = Mock(return_value=False)  # the floor tap already switched it off
        f._floor_step({"runite": 400})
        f.change_floor.assert_called_once_with(9)
        f.set_floor_auto.assert_called_once_with(False)
        self.assertEqual((f._trip["origin"], f._trip["floor"], f._trip["auto_was_on"]), (19, 9, True))
        self.assertIn("started", f._trip)
        self.assertTrue(f._TRIP_FILE.exists())
        # Not due yet: stays; due: returns and restores Auto.
        f._floor_step({})
        f.change_floor.assert_called_once()
        f._trip["until"] = 0
        f._floor_step({})
        f.change_floor.assert_called_with(19)
        f.set_floor_auto.assert_called_with(True)
        self.assertIsNone(f._trip)
        self.assertIsNone(self.state.smart_craft_trip)
        self.assertFalse(f._TRIP_FILE.exists())
        ended = [c.args[0] for c in f.bot_log.call_args_list if "floor trip ended" in c.args[0]]
        self.assertTrue(ended and " after " in ended[0] and "Auto on again" in ended[0], ended)

    def test_trip_is_undone_when_auto_cannot_be_switched_off(self):
        f = self.feature
        f._open_floor_panel = Mock(return_value=(None, {9: "red", 19: "green"}))
        f._close_floor_panel = Mock()
        f.change_floor = Mock(return_value=19)
        f.floor_auto_on = Mock(return_value=True)
        f.set_floor_auto = Mock(side_effect=[f._Abort("floor Auto not switched off"), True])
        with self.assertRaises(f._Abort):
            f._floor_step({"runite": 400})
        self.assertEqual([c.args for c in f.change_floor.call_args_list], [(9,), (19,)])
        self.assertEqual([c.args for c in f.set_floor_auto.call_args_list], [(False,), (True,)])
        self.assertIsNone(f._trip)

    def test_unreadable_auto_never_moves(self):
        f = self.feature
        f.floor_auto_on = Mock(side_effect=f._Abort("floor Auto toggle not recognised"))
        f._open_floor_panel = Mock()
        f.change_floor = Mock()
        with self.assertRaises(f._Abort):
            f._floor_step({"runite": 400})
        f._open_floor_panel.assert_not_called()
        f.change_floor.assert_not_called()

    def test_auto_toggle_colours(self):
        img = np.full((100, 100, 3), 60, np.uint8)
        img[:50] = (66, 178, 16)       # on: green (BGR)
        img[50:] = (8, 4, 41)          # off: dark maroon, seen in game 2026-11-24
        self.assertIs(self.feature._auto_kind(img, 50, 20), True)
        self.assertIs(self.feature._auto_kind(img, 50, 80), False)
        img[50:] = (40, 40, 170)       # brighter red counts as off too
        self.assertIs(self.feature._auto_kind(img, 50, 80), False)
        img[50:] = (8, 8, 8)           # menu overlay / black: unknown
        self.assertIsNone(self.feature._auto_kind(img, 50, 80))

    def test_disabled_setting_or_no_shortage_never_moves(self):
        f = self.feature
        f._open_floor_panel = Mock()
        self.settings.SMART_CRAFT_FLOOR_TRIPS = False
        f._floor_step({"runite": 400})
        self.settings.SMART_CRAFT_FLOOR_TRIPS = True
        f._floor_step({})
        f._open_floor_panel.assert_not_called()

    def test_trip_is_dropped_when_the_origin_floor_is_gone(self):
        f = self.feature
        f._set_trip({"origin": 19, "floor": 9, "until": 0.0, "auto_was_on": True})
        f._open_floor_panel = Mock(return_value=(None, {1: "green", 2: "red"}))
        f._close_floor_panel = Mock()
        f.change_floor = Mock()
        f._floor_step({})
        f.change_floor.assert_not_called()
        self.assertIsNone(f._trip)

    def test_trip_survives_a_restart(self):
        f = self.feature
        f._set_trip({"origin": 19, "floor": 9, "until": 123.0, "auto_was_on": True})
        f._trip = None
        f._load_trip()
        self.assertEqual(f._trip["origin"], 19)

    def test_ore_shortage_is_recorded_per_bar(self):
        f = self.feature
        rows = iter([{"ore": 5, "bars": 51, "ratio": 8, "button": (713, 900)}])
        f._locate_craft_row = Mock(side_effect=lambda *_a: next(rows))
        f._ore_missing.clear()
        f.craft_bar("adamant", 51, 64, f._run_generation)
        self.assertEqual(f._ore_missing, {"adamant": 13 * 8 - 5})
        f.tap.assert_not_called()


class SellSurplusTests(SmartCraftBase):
    ORES = ["tin", "copper", "demonite", "vr_demonite"]

    def setUp(self):
        super().setUp()
        self.feature.load_templates(ROOT / "images")
        self.settings.SMART_CRAFT_LIST_ROI = (40, 170, 860, 1395)

    def page(self, locked=()):
        """Synthetic Sell page: one row per ore, Sell button bright or dimmed."""
        import cv2
        f = self.feature
        img = np.full((1600, 900, 3), (8, 20, 24), np.uint8)
        for i, ore in enumerate(self.ORES):
            top = 358 + 133 * i
            img[top:top + 77, 660:780] = (40, 40, 70) if ore in locked else (57, 45, 172)
            tpl = cv2.imread(str(ROOT / f"images/smart_craft/ores/{ore}.png"))
            img[top + 5:top + 5 + tpl.shape[0], 113:113 + tpl.shape[1]] = tpl
        return img

    def test_reads_rows_with_ore_and_lock_state(self):
        rows = self.feature.read_sell_rows(self.page(locked=("copper",)))
        self.assertEqual([(o, l) for o, _t, l in rows],
                         [("tin", False), ("copper", True), ("demonite", False), ("vr_demonite", False)])

    def test_locks_needed_and_unknown_bar_ores_sells_rest_and_unlocks(self):
        f = self.feature
        state = {"locked": {"copper"}}   # copper was locked by the player
        f._open_page = Mock(return_value=(None, None))
        f.list_pages = lambda img, gray, gen: iter([(self.page(state["locked"]), None, 0)])
        f._fresh = Mock(side_effect=lambda: (self.page(state["locked"]), None))
        f._sell_ready = Mock(return_value=True)
        taps = []

        def toggle(top, ore, want):
            taps.append((ore, want))
            (state["locked"].add if want else state["locked"].discard)(ore)
            return self.page(state["locked"])
        f._toggle_lock = toggle
        f._count_box = Mock(side_effect=[np.zeros((4, 4)), np.full((4, 4), 50)])
        sold = f.sell_surplus_ore({"demonite"}, f._run_generation)
        # demonite is needed, vr_demonite has no bar template -> both kept and restored afterwards.
        self.assertEqual(sold, ["tin"])
        self.assertEqual(taps, [("demonite", True), ("vr_demonite", True),
                                ("demonite", False), ("vr_demonite", False)])
        self.assertIn("copper", state["locked"])            # the player's lock is untouched
        self.assertEqual([c.args[:2] for c in f.tap.call_args_list], [f._SELL_ALL, f._SELL_ALL])

    def test_unrecognised_row_aborts_before_selling(self):
        f = self.feature
        img = self.page()
        img[358:400, 100:150] = 0  # icon unreadable
        f._open_page = Mock(return_value=(None, None))
        f.list_pages = lambda *_a: iter([(img, None, 0)])
        f._toggle_lock = Mock()
        with self.assertRaises(f._Abort):
            f.sell_surplus_ore(set(), f._run_generation)
        f.tap.assert_not_called()


class YieldTests(SmartCraftBase):
    """Smart Craft pauses for waiting auto workflows and resumes with its scan."""

    def setUp(self):
        super().setUp()
        self.settings.SMART_CRAFT_MAX_ROUNDS = 1
        self.settings.SMART_CRAFT_WORKSHOP = False
        self.state.workflows_waiting = {}
        f = self.feature
        self.events = []
        f._open_page = Mock(side_effect=lambda tile, *a, **k: self.events.append(f"open {tile}") or (None, None))
        f._close_menu = Mock(side_effect=lambda: self.events.append("close"))
        f._fresh = Mock(return_value=(None, None))
        f._button = Mock(return_value={"found": False})   # menu closed
        f.select_quantity_5x = Mock(return_value=True)
        f.scan_upgrades = Mock(return_value=[("adamant", 0, 10, 400), ("runite", 0, 10, 500)])
        f.craft_bar = Mock(side_effect=self.craft)
        f.buy_upgrades = Mock(return_value=0)
        f._floor_step = Mock()
        f._wake_workflows = Mock(side_effect=self.workflow_runs)
        self.crafting_waits_for = None

    def craft(self, bar, *_args):
        self.events.append(f"craft {bar}")
        if bar == "adamant" and self.crafting_waits_for:
            self.state.workflows_waiting[self.crafting_waits_for] = 100.0
        return 5

    def workflow_runs(self):
        lock = self.state.ingame_action_lock
        self.assertTrue(lock.acquire(blocking=False), "lock must be free during the pause")
        lock.release()
        self.events.append("workflow")
        self.state.workflows_waiting.clear()

    def test_no_waiting_workflow_means_no_pause(self):
        self.assertEqual(self.feature._try_run_with_lock(), "ok")
        self.feature._wake_workflows.assert_not_called()
        self.assertEqual(self.events.count("open craft_tpl"), 1)
        self.assertFalse(self.state.ingame_action_lock.locked())

    def test_pause_between_bars_reopens_craft_and_keeps_the_scan(self):
        self.crafting_waits_for = "obelisk_fight"
        self.assertEqual(self.feature._try_run_with_lock(), "ok")
        self.assertEqual(self.events[:7], ["open upgrades_tpl", "open craft_tpl", "craft adamant",
                                           "close", "workflow", "open craft_tpl", "craft runite"])
        self.feature.scan_upgrades.assert_called_once()
        self.assertEqual(self.feature.select_quantity_5x.call_count, 2)
        self.assertFalse(self.state.ingame_action_lock.locked())

    def test_stale_waiting_entries_and_disabled_setting_are_ignored(self):
        self.state.workflows_waiting["old"] = 100.0 - 60
        self.feature._try_run_with_lock()
        self.feature._wake_workflows.assert_not_called()
        self.settings.SMART_CRAFT_YIELD = False
        self.crafting_waits_for = "bombs"
        self.feature._try_run_with_lock()
        self.feature._wake_workflows.assert_not_called()

    def test_menu_that_stays_open_blocks_the_pause(self):
        self.crafting_waits_for = "bombs"
        self.feature._button = Mock(return_value={"found": True})
        self.assertEqual(self.feature._try_run_with_lock(), "ok")
        self.feature._wake_workflows.assert_not_called()
        self.assertFalse(self.state.ingame_action_lock.locked())

    def test_cancel_during_the_pause_neither_taps_nor_double_releases(self):
        self.crafting_waits_for = "bombs"
        f = self.feature

        def cancel_while_waiting():
            self.events.append("workflow")
            f._stop_evt.set()
        f._wake_workflows = Mock(side_effect=cancel_while_waiting)
        self.addCleanup(f._stop_evt.clear)
        self.assertEqual(f._try_run_with_lock(), "cancelled")
        self.assertEqual(self.events[-2:], ["close", "workflow"])   # no close tap after the pause
        self.assertFalse(self.state.ingame_action_lock.locked())
        self.assertFalse(f._lock_owned or f._paused)


class SettingsTests(unittest.TestCase):
    def test_overrides_apply_and_invalid_values_are_rejected(self):
        data = {"startup_defaults": {"SMART_CRAFT_ENABLED": True},
                "smart_craft": {"SMART_CRAFT_LIST_ROI": [1, 2, 30, 40], "SMART_CRAFT_BUY_UPGRADES": False}}
        settings = {}
        apply_overrides_to_settings(settings, data)
        self.assertEqual(settings["SMART_CRAFT_LIST_ROI"], (1, 2, 30, 40))
        self.assertFalse(settings["SMART_CRAFT_BUY_UPGRADES"])
        self.assertTrue(settings["SMART_CRAFT_ENABLED"])
        for bad in ({"SMART_CRAFT_MAX_BATCH": 0}, {"SMART_CRAFT_GLYPH_MAX_RATIO": 1.5},
                    {"SMART_CRAFT_BUY_UPGRADES": "yes"}, {"SMART_CRAFT_LIST_ROI": [5, 5, 1, 1]},
                    {"SMART_CRAFT_UNKNOWN": 1}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                normalize_user_settings({"smart_craft": bad})

    def test_api_defaults_cover_every_validated_key(self):
        from config.validation import SETTINGS
        import config.settings as s
        for key in SETTINGS["smart_craft"]:
            self.assertTrue(hasattr(s, key), key)
        self.assertIn("SMART_CRAFT_ENABLED", SETTINGS["startup_defaults"])
        api = (ROOT / "web/config_api.py").read_text(encoding="utf-8")
        for key in SETTINGS["smart_craft"]:
            self.assertIn(f'"{key}": ', api)


if __name__ == "__main__":
    unittest.main()
