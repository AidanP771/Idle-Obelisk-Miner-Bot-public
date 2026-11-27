"""ADB failure propagation and claim accounting using isolated feature imports."""
import sys
import types
import unittest
from unittest.mock import Mock, patch

import test_founders as founders
import test_lootbug as lootbugs
import test_screenshot_consumers as consumers


class FoundersFailureTests(unittest.TestCase):
    def setUp(self):
        founders.FeatureTests.setUp(self)

    def test_failed_tap_does_not_count_or_timestamp_and_releases_lock(self):
        self.feature.find_template.return_value = {"found": True, "score": 1, "cx": 21, "cy": 22}
        self.feature.tap.return_value = False
        self.feature._try_run_with_lock()
        self.assertEqual(self.state.founders_clicked_count, 0)
        self.assertEqual(self.state.founders_last_click_ts, 0)
        self.assertFalse(self.state.ingame_action_lock.locked())
        self.feature.tap.assert_called_once()


class LootbugFailureTests(unittest.TestCase):
    def setUp(self):
        lootbugs.LootbugTests.setUp(self)

    def test_failed_initial_tap_never_starts_buy_wait_or_counts(self):
        self.state.lootbug_clicked_count = 0
        self.state.lootbug_last_click_ts = 0
        self.feature.find_template.return_value = dict(self.hit)
        self.feature.tap.return_value = False
        with patch.object(self.feature, "_buy_when_visible") as buy:
            self.assertFalse(self.feature._run_scan_cycle())
        buy.assert_not_called()
        self.assertEqual(self.state.lootbug_clicked_count, 0)
        self.assertEqual(self.state.lootbug_last_click_ts, 0)
        self.assertEqual(self.state.lootbug_bonus_count, 0)
        self.feature._sleep_interruptible.assert_not_called()


class FreebieResultTests(unittest.TestCase):
    """Store visit with the store already open; read_pack is scripted."""
    def setUp(self):
        consumers.ConsumerSlotTests.setUp(self)
        self.feature = self.freebies
        self.state = self.feature.state
        self.state.freebies_claimed_count = 0
        self.state.freebies_diamonds_total = 0
        self.state.freebies_last_claim_ts = 0
        self.feature.s.FREEBIES_DIAMONDS_PER_CLAIM = 28
        self.feature.get_screen.return_value = (object(), 900, 1600)
        self.hit = {"found": True, "score": 1, "cx": 20, "cy": 30, "x": 10, "y": 20}
        self.feature.find_template.return_value = self.hit      # Store open, close button found
        self.feature.tap.return_value = True
        self.feature.time = types.SimpleNamespace(sleep=Mock(), time=lambda: 100, strftime=lambda *a: "t")
        self.feature._dump = Mock()
        self.packs([2, 1, 0])
        self.stats = types.ModuleType("features.gem_statistics")
        self.stats.get_gem_statistics = Mock()
        self.patcher = patch.dict(sys.modules, {"features.gem_statistics": self.stats})
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def packs(self, banked, lit=None):
        """read_pack returns these banked counts in turn (the last one repeats)."""
        seq = [None if b is None else {"banked": b, "lit": (b > 0) if lit is None else lit, "claim": (747, 1020)}
               for b in banked]
        it = iter(seq)
        self.feature.read_pack = Mock(side_effect=lambda img: next(it, seq[-1]))

    def test_failed_claim_tap_does_not_record_reward(self):
        self.feature.tap.side_effect = [False]
        self.assertFalse(self.feature._run_claim_cycle())
        self.assertEqual(self.state.freebies_claimed_count, 0)
        self.assertEqual(self.state.freebies_diamonds_total, 0)
        self.assertEqual(self.state.freebies_last_claim_ts, 0)
        self.stats.get_gem_statistics.assert_not_called()

    def test_both_banked_claims_are_confirmed_and_close_failure_does_not_undo_them(self):
        self.feature.tap.side_effect = [True, True, False]       # claim, claim, close
        self.assertFalse(self.feature._run_claim_cycle())
        self.assertEqual(self.state.freebies_claimed_count, 2)
        self.assertEqual(self.state.freebies_diamonds_total, 56)
        self.assertEqual(self.stats.get_gem_statistics.return_value.record_claim.call_count, 2)
        self.assertTrue(any("claimed 2 of 2 banked (confirmed)" in call.args[0]
                            for call in self.feature.bot_log.call_args_list))

    def test_claim_without_visible_effect_is_not_counted(self):
        self.packs([1])
        self.assertTrue(self.feature._run_claim_cycle())          # visit done, menu closed
        self.assertEqual(self.feature.tap.call_count, 2)          # one claim, close
        self.assertEqual(self.state.freebies_claimed_count, 0)
        self.stats.get_gem_statistics.assert_not_called()
        self.feature._dump.assert_called_once()

    def test_nothing_banked_means_no_claim_tap(self):
        self.packs([0])
        self.assertTrue(self.feature._run_claim_cycle())
        self.assertEqual([c.args[:2] for c in self.feature.tap.call_args_list], [(20, 30)])   # close only
        self.assertEqual(self.state.freebies_claimed_count, 0)

    def test_failed_scroll_aborts_without_recording_claim(self):
        self.packs([None])
        self.feature.scroll_down.return_value = False
        self.assertFalse(self.feature._run_claim_cycle())
        self.assertEqual(self.state.freebies_claimed_count, 0)
        self.assertFalse(any(c.args[:2] == (747, 1020) for c in self.feature.tap.call_args_list))
        self.stats.get_gem_statistics.assert_not_called()


if __name__ == "__main__":
    unittest.main()
