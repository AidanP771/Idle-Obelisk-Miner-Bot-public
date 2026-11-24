# features/floor_auto.py - keep the floor Auto toggle (auto-descend) switched on
"""
The floor Auto toggle below the Floor Select HUD button is bright green when on
and dark red when off; it does not move. The game sometimes switches it off
(picking a floor by hand, possibly other events), and then the miner stays on
its floor.

Every FLOOR_AUTO_CHECK_INTERVAL_S this worker reads the latest shared
screenshot (no capture, no action lock). Only when the toggle reads "off" it
takes the action lock, confirms on a fresh frame and taps the toggle once, then
verifies it turned green.

Never acts while a Smart Craft floor trip is active (the trip switches Auto off
on purpose and restores it itself), while "Pause after current" is set, or when
the button is not visible (a menu or popup is open).
"""
import threading
import time

import cv2

import config.app_state as state
import config.settings as s
from core.file_logger import bot_log
from core.game_state import is_game_running
from core.screen_cache import get_cached_screen, get_screen
from core.tapper import tap

_SLOT = "floor_auto_scr"
_thread = None
_thread_started = False
_stop_evt = threading.Event()


def _log(msg: str, level: str = "TXT"):
    bot_log(msg, level)


def _reader():
    from features import smart_craft  # owns the toggle reader and floor templates
    return smart_craft


def _read(img):
    """(on: True/False/None, tap position) for a color frame."""
    if img is None:
        return None, None
    return _reader()._auto_state(img, cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))


def _blocked() -> str:
    if getattr(state, "smart_craft_trip", None):
        return "floor trip active"
    if getattr(state, "automation_paused", False):
        return "automation paused"
    return ""


def check_once() -> str:
    """One check. Returns on | unknown | blocked | busy | restored | failed | no_game."""
    if not is_game_running():
        return "no_game"
    if _blocked():
        return "blocked"
    img, _w, _h = get_cached_screen("game_scr", max_age=float(getattr(s, "FLOOR_AUTO_CHECK_INTERVAL_S", 30.0)),
                                    color=True)
    on, _pos = _read(img)
    if on is not False:
        return "on" if on else "unknown"
    lock = state.ingame_action_lock
    if not lock.acquire(blocking=False):
        return "busy"
    try:
        reason = _blocked()  # a trip may have started while we looked
        if reason:
            return "blocked"
        img, _w, _h = get_screen(_SLOT, max_age=0.0, force=True, color=True)
        on, pos = _read(img)
        if on is not False:
            return "on" if on else "unknown"
        _log(f"[FLOOR_AUTO] floor Auto is off -> switching it on at {pos}", "FULL")
        if not tap(int(pos[0]), int(pos[1]), jitter=3):
            _log("[FLOOR_AUTO] tap failed", "FULL")
            return "failed"
        time.sleep(0.8)
        img, _w, _h = get_screen(_SLOT, max_age=0.0, force=True, color=True)
        on, _pos = _read(img)
        if on:
            state.floor_auto_restored = getattr(state, "floor_auto_restored", 0) + 1
            _log("[FLOOR_AUTO] floor Auto is on again", "FULL")
            return "restored"
        _log(f"[FLOOR_AUTO] floor Auto still not on after the tap (read: {on})", "FULL")
        return "failed"
    finally:
        lock.release()


def _worker():
    _log("[FLOOR_AUTO] worker thread started")
    _stop_evt.wait(5.0)
    while not _stop_evt.is_set():
        interval = float(getattr(s, "FLOOR_AUTO_CHECK_INTERVAL_S", 30.0))
        try:
            if getattr(s, "FLOOR_AUTO_GUARD", True):
                result = check_once()
                if result == "busy":
                    interval = min(interval, 10.0)
                elif result == "failed":
                    interval = max(interval, 120.0)  # do not hammer a toggle that does not react
        except Exception as exc:
            _log(f"[FLOOR_AUTO] check error: {type(exc).__name__}: {exc}", "FULL")
        _stop_evt.wait(interval)


def start():
    """Start the background worker (idempotent)."""
    global _thread, _thread_started
    if _thread_started:
        return
    _thread_started = True
    _stop_evt.clear()
    _thread = threading.Thread(target=_worker, daemon=True, name="floor-auto-worker")
    _thread.start()
