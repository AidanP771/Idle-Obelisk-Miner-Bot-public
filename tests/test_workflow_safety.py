"""Workflow failures, cooperative cancellation and reload across nested calls."""
from concurrent.futures import ThreadPoolExecutor
import copy
import threading
import time
import types
import unittest
from unittest.mock import Mock

import test_screenshot_consumers as consumers


class WorkflowSafetyTests(unittest.TestCase):
    def setUp(self):
        consumers.WorkflowScreenshotTests.setUp(self)
        self.w = self.workflow
        self.w.state = types.SimpleNamespace(workflows_lock=threading.RLock(), workflows={},
            workflows_running=None, workflows_master_enabled=True, ingame_action_lock=threading.Lock(),
            workflows_reload_status="idle", workflows_reload_error="", workflows_waiting={})
        self.w.cfg_wf = types.SimpleNamespace(WORKFLOWS={}, load_workflow_definitions=Mock())
        self.w.is_game_running.return_value = True
        self.w.tap.return_value = True
        self.w.scroll.return_value = True
        self.w._push_toast = Mock()

    def test_scheduler_wake_does_not_shorten_action_wait_or_consume_wake(self):
        self.w.cfg_wf.WORKFLOWS = {"other": {"steps": []}}
        self.w.toggle_wf("other")
        started = time.monotonic()
        self.w._interruptible_sleep(0.06)
        self.assertGreaterEqual(time.monotonic() - started, 0.05)
        self.assertTrue(self.w._wake_evt.is_set())
        self.w._cancel_evt.set()
        started = time.monotonic()
        self.w._interruptible_sleep(2)
        self.assertLess(time.monotonic() - started, 0.2)

    def test_missing_template_policy_applies_to_both_condition_kinds(self):
        self.w.get_screen.return_value = (object(), 900, 1600)
        for kind in ("template_visible", "template_not_visible"):
            condition = {"kind": kind, "template": "missing"}
            self.w.find_template.return_value = {"found": False, "score": 0}
            self.assertEqual(self.w._eval_condition(condition, {}, False, "fail_wf"), (False, True))
            self.assertEqual(self.w._eval_condition(condition, {}, False, "false"),
                             (kind == "template_not_visible", False))
            self.w.find_template.return_value = {"found": True, "score": 1}
            self.assertEqual(self.w._eval_condition(condition, {}, False, "fail_wf"),
                             (kind == "template_visible", False))

    def test_condition_can_reuse_the_previous_screenshot(self):
        self.w.get_screen.return_value = (object(), 900, 1600)
        self.w.find_template.return_value = {"found": True, "score": 1}
        self.w._eval_condition({"kind": "template_visible", "template": "x"}, {}, False, "false")
        self.assertTrue(self.w.get_screen.call_args.kwargs["force"])
        self.w._eval_condition({"kind": "template_visible", "template": "x", "use_prev_screenshot": True},
                               {}, False, "false")
        self.assertFalse(self.w.get_screen.call_args.kwargs["force"])
        from config.validation import validate_workflows
        validate_workflows({"wf": {"steps": [{"type": "if", "branches": [{"condition": {
            "kind": "template_visible", "template": "x", "use_prev_screenshot": True}, "steps": []}]}]}})

    def test_all_tap_step_types_abort_repetition_on_failure_even_if_optional(self):
        hit = {"found": True, "score": 1, "cx": 10, "cy": 20, "angle": 0, "rotations_tried": 1}
        self.w.get_screen.return_value = (object(), 900, 1600)
        self.w.find_template.return_value = hit
        self.w.find_template_rotating.return_value = hit
        for step in ({"type": "tap_roi", "roi": [0, 0, 20, 30], "count": 3},
                     {"type": "tap_xy", "x": 10, "y": 20, "count": 3},
                     {"type": "click_template", "template": "x", "clicks": 3, "optional": True},
                     {"type": "click_rotating", "template": "x", "clicks": 3, "optional": True}):
            with self.subTest(kind=step["type"]):
                self.w.tap.reset_mock()
                self.w.tap.return_value = False
                self.assertEqual(self.w._run_workflow("wf", {"steps": [step]}), "error")
                self.w.tap.assert_called_once()
                self.assertFalse(self.w.state.ingame_action_lock.locked())
                self.assertIsNone(self.w.state.workflows_running)

    def test_optional_template_miss_still_skips_and_tracks_functional_failure(self):
        self.w.get_screen.return_value = (object(), 900, 1600)
        self.w.find_template.return_value = {"found": False, "score": 0}
        steps = [{"type": "click_template", "template": "x", "optional": True},
                 {"type": "if", "branches": [{"condition": {"kind": "last_step_failed"},
                  "steps": [{"type": "tap_xy", "x": 1, "y": 2}]}]}]
        self.assertEqual(self.w._run_workflow("wf", {"steps": steps}), "ok")
        self.w.tap.assert_called_once()

    def test_failed_workflow_is_not_counted_and_exceptions_publish_error_status(self):
        for failure in (False, RuntimeError("ADB failed")):
            with self.subTest(failure=failure):
                self.w.tap.side_effect = failure if isinstance(failure, Exception) else None
                self.w.tap.return_value = failure
                self.w.cfg_wf.WORKFLOWS = {"wf": {"steps": [{"type": "tap_xy", "x": 1, "y": 2}]}}
                self.w._manual_queue.append("wf")
                self.w._scheduler_tick()
                rt = self.w.state.workflows["wf"]
                self.assertEqual(rt["last_status"], "error")
                self.assertEqual(rt.get("run_count", 0), 0)
                self.assertFalse(self.w.state.ingame_action_lock.locked())

    def test_busy_auto_run_is_recorded_as_waiting_until_it_runs(self):
        self.w.cfg_wf.WORKFLOWS = {"wf": {"steps": [{"type": "tap_xy", "x": 1, "y": 2}],
                                          "enabled_default": True, "interval_s": 300}}
        self.w._ensure_runtime_entry("wf", self.w.cfg_wf.WORKFLOWS["wf"])
        rt = self.w.state.workflows["wf"]
        rt["next_due_ts"] = time.time() - 1
        with self.w.state.ingame_action_lock:
            self.w._scheduler_tick()
        self.assertEqual(rt["last_status"], "busy")
        self.assertIn("wf", self.w.state.workflows_waiting)
        self.assertGreater(rt["next_due_ts"], time.time())
        self.w.retry_waiting_now()                    # a long lock holder paused for it
        self.assertLessEqual(rt["next_due_ts"], time.time())
        self.w._scheduler_tick()
        self.assertEqual(rt["last_status"], "ok")
        self.assertNotIn("wf", self.w.state.workflows_waiting)

    def test_pause_runs_nothing_keeps_manual_queue_and_resume_continues(self):
        self.w.cfg_wf.WORKFLOWS = {"wf": {"steps": [{"type": "tap_xy", "x": 1, "y": 2}],
                                          "enabled_default": True, "interval_s": 300}}
        self.w._ensure_runtime_entry("wf", self.w.cfg_wf.WORKFLOWS["wf"])
        self.w.state.workflows["wf"]["next_due_ts"] = time.time() - 1
        self.w._manual_queue.append("wf")
        self.w.state.automation_paused = True
        self.w._scheduler_tick()
        self.w.tap.assert_not_called()
        self.assertEqual(self.w._manual_queue, ["wf"])
        self.assertNotIn("wf", self.w.state.workflows_waiting)
        self.w.state.automation_paused = False
        self.w._scheduler_tick()
        self.assertEqual(self.w.tap.call_count, 2)                 # the manual and the due auto run
        self.assertEqual(self.w._manual_queue, [])

    def test_pause_requested_during_a_run_lets_it_finish_but_starts_no_next(self):
        self.w.cfg_wf.WORKFLOWS = {"a": {"steps": [{"type": "tap_xy", "x": 1, "y": 2}]},
                                   "b": {"steps": [{"type": "tap_xy", "x": 3, "y": 4}]}}
        def pause_on_tap(*args, **kwargs):
            self.w.state.automation_paused = True
            return True
        self.w.tap.side_effect = pause_on_tap
        self.w._manual_queue.extend(["a", "b"])
        self.w._scheduler_tick()
        self.assertEqual(self.w.state.workflows["a"]["last_status"], "ok")
        self.assertEqual(self.w.tap.call_count, 1)
        self.assertEqual(self.w._manual_queue, ["b"])

    def test_defer_step_in_a_called_helper_postpones_the_top_level_workflow(self):
        self.w.cfg_wf.WORKFLOWS = {
            "parent": {"steps": [{"type": "call_workflow", "target": "child"}],
                       "enabled_default": True, "interval_s": 60},
            "child": {"steps": [{"type": "defer", "seconds": 900}, {"type": "tap_xy", "x": 1, "y": 2}]},
            "other": {"steps": [{"type": "tap_xy", "x": 3, "y": 4}], "enabled_default": True, "interval_s": 60}}
        for slug in ("parent", "other"):
            self.w._ensure_runtime_entry(slug, self.w.cfg_wf.WORKFLOWS[slug])
            self.w.state.workflows[slug]["next_due_ts"] = time.time() - 1
        before = time.time()
        self.w._scheduler_tick()
        self.assertEqual(self.w.state.workflows["parent"]["last_status"], "ok")
        self.assertEqual(self.w.tap.call_count, 2)                       # the run continued after defer
        self.assertGreaterEqual(self.w.state.workflows["parent"]["next_due_ts"], before + 899)
        self.assertLess(self.w.state.workflows["other"]["next_due_ts"], before + 120)   # not affected
        self.assertEqual(self.w._defer_until, 0.0)

    def test_defer_step_is_validated(self):
        from config.validation import validate_workflows
        validate_workflows({"wf": {"steps": [{"type": "defer", "seconds": 600}]}})
        with self.assertRaises(ValueError):
            validate_workflows({"wf": {"steps": [{"type": "defer", "seconds": 0}]}})

    def test_read_number_stores_and_checks_increase_within_one_run(self):
        import numpy as np
        self.w.get_screen.return_value = (np.zeros((20, 20), np.uint8), 20, 20)
        readings = iter([6, 6, 5, 7, None])   # store, check, no-stored-value run, 6 -> 7, unreadable
        self.w._read_number_in = lambda gray, roi: next(readings)
        store = {"type": "read_number", "roi": [0, 0, 10, 10], "key": "dp", "expect": "store"}
        check = {"type": "read_number", "roi": [0, 0, 10, 10], "key": "dp", "expect": "increased", "optional": True}
        steps = [store, check, {"type": "if", "branches": [{"condition": {"kind": "last_step_failed"},
                 "steps": [{"type": "tap_xy", "x": 1, "y": 2}]}]}]
        self.assertEqual(self.w._run_workflow("wf", {"steps": steps}), "ok")
        self.w.tap.assert_called_once()                      # 6 -> 6: not increased, branch ran
        self.w.tap.reset_mock()
        self.w._reset_defer()                                # next top-level run
        self.assertEqual(self.w._run_workflow("wf", {"steps": [dict(check, optional=False)]}), "error")  # nothing stored
        self.w._numbers["dp"] = 6
        self.assertEqual(self.w._run_workflow("wf", {"steps": [dict(check, optional=False)]}), "ok")     # 6 -> 7
        self.assertEqual(self.w._run_workflow("wf", {"steps": [store]}), "error")                       # unreadable
        from config.validation import validate_workflows
        validate_workflows({"wf": {"steps": [store, check]}})
        with self.assertRaises(ValueError):
            validate_workflows({"wf": {"steps": [dict(store, expect="bigger")]}})

    def test_master_off_during_last_step_is_cancelled_and_does_not_count(self):
        def turn_off(*args, **kwargs):
            self.w.toggle_master()
            return True
        self.w.tap.side_effect = turn_off
        self.w.cfg_wf.WORKFLOWS = {"wf": {"steps": [{"type": "tap_xy", "x": 1, "y": 2}]}}
        self.w._manual_queue.append("wf")
        self.w._scheduler_tick()
        self.assertEqual(self.w.state.workflows["wf"]["last_status"], "cancelled")
        self.assertEqual(self.w.state.workflows["wf"].get("run_count", 0), 0)
        self.assertFalse(self.w.state.ingame_action_lock.locked())
        self.w.manual_trigger("wf")
        self.assertFalse(self.w._manual_queue)

    def test_reload_during_parent_keeps_old_child_until_next_idle_boundary(self):
        entered, release = threading.Event(), threading.Event()
        def wait_step(step, wf):
            entered.set()
            self.assertTrue(release.wait(5))
            return True
        self.w._STEP_EXECUTORS["wait"] = wait_step
        old = {"parent": {"steps": [{"type": "wait"}, {"type": "call_workflow", "target": "child"}]},
               "child": {"steps": [{"type": "tap_xy", "x": 1, "y": 2}]}, "removed": {"steps": []}}
        new = copy.deepcopy(old)
        new["child"]["steps"][0]["x"] = 99
        del new["removed"]
        new["new"] = {"enabled_default": True, "steps": []}
        self.w.cfg_wf.WORKFLOWS = old
        self.w.cfg_wf.load_workflow_definitions.return_value = new
        self.w.state.workflows["removed"] = {"enabled": False}
        self.w._manual_queue.append("parent")
        with ThreadPoolExecutor(max_workers=1) as pool:
            running = pool.submit(self.w._scheduler_tick)
            try:
                self.assertTrue(entered.wait(5))
                self.w.reload_workflows()
                self.assertEqual(self.w.state.workflows_reload_status, "pending")
                self.assertIs(self.w.cfg_wf.WORKFLOWS, old)
                self.assertFalse(self.w._wake_evt.is_set())  # reload never shortens waits
                self.w.cfg_wf.load_workflow_definitions.assert_not_called()
            finally:
                release.set()
            running.result(timeout=5)
        self.assertEqual(self.w.tap.call_args.args, (1, 2))
        self.assertEqual(self.w.state.workflows["parent"]["run_count"], 1)
        self.w._manual_queue.extend(["child", "removed"])
        self.w._scheduler_tick()
        self.assertEqual(self.w.tap.call_args.args, (99, 2))
        self.assertEqual(self.w.state.workflows_reload_status, "applied")
        self.assertNotIn("removed", self.w.state.workflows)
        self.assertEqual(self.w.state.workflows["parent"]["run_count"], 1)
        self.assertTrue(self.w.state.workflows["new"]["enabled"])

    def test_failed_reload_keeps_definitions_runtime_and_reports_error(self):
        old = {"old": {"steps": []}}
        self.w.cfg_wf.WORKFLOWS = old
        self.w.state.workflows = {"old": {"enabled": True, "run_count": 7}}
        self.w.cfg_wf.load_workflow_definitions.side_effect = ValueError("invalid JSON")
        self.w.reload_workflows()
        self.w._apply_pending_reload()
        self.assertIs(self.w.cfg_wf.WORKFLOWS, old)
        self.assertEqual(self.w.state.workflows["old"]["run_count"], 7)
        self.assertEqual(self.w.state.workflows_reload_status, "error")
        self.assertIn("invalid JSON", self.w.state.workflows_reload_error)

    def test_empty_reload_removes_all_workflows_and_stale_manual_queue(self):
        self.w.cfg_wf.WORKFLOWS = {"old": {"steps": []}}
        self.w.state.workflows = {"old": {"enabled": True}}
        self.w._manual_queue.append("old")
        self.w.cfg_wf.load_workflow_definitions.return_value = {}
        self.w.reload_workflows()
        self.w._scheduler_tick()
        self.assertEqual(self.w.cfg_wf.WORKFLOWS, {})
        self.assertEqual(self.w.state.workflows, {})
        self.assertEqual(self.w._manual_queue, [])

    def test_reload_between_manual_runs_preserves_the_rest_of_the_queue(self):
        old = {"first": {"steps": [{"type": "tap_xy", "x": 1, "y": 2}]},
               "second": {"steps": [{"type": "tap_xy", "x": 3, "y": 4}]}}
        candidate = copy.deepcopy(old)
        candidate["second"]["steps"][0]["x"] = 99
        self.w.cfg_wf.WORKFLOWS = old
        self.w.cfg_wf.load_workflow_definitions.return_value = candidate
        self.w._manual_queue.extend(["first", "second"])
        def request_reload(*args, **kwargs):
            self.w.reload_workflows()
            return True
        self.w.tap.side_effect = request_reload
        self.w._scheduler_tick()
        self.w.tap.assert_called_once()
        self.assertEqual(self.w._manual_queue, ["second"])
        self.w.tap.side_effect = None
        self.w._scheduler_tick()
        self.assertEqual(self.w.tap.call_args.args, (99, 4))
        self.assertEqual(self.w.state.workflows["second"]["run_count"], 1)

    def test_second_reload_request_during_load_is_kept_pending(self):
        def loading():
            self.w.reload_workflows()
            return {"first": {"steps": []}}
        self.w.cfg_wf.load_workflow_definitions.side_effect = loading
        self.w.reload_workflows()
        self.w._scheduler_tick()
        self.assertEqual(self.w.state.workflows_reload_status, "pending")
        self.assertTrue(self.w._reload_evt.is_set())
        self.w.cfg_wf.load_workflow_definitions.side_effect = None
        self.w.cfg_wf.load_workflow_definitions.return_value = {"second": {"steps": []}}
        self.w._scheduler_tick()
        self.assertEqual(set(self.w.cfg_wf.WORKFLOWS), {"second"})
        self.assertEqual(self.w.state.workflows_reload_status, "applied")


if __name__ == "__main__":
    unittest.main()
