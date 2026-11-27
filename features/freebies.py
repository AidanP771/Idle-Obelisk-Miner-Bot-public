# features/freebies.py - Auto-claim the Store's Freebie Pack
"""
The Freebie Pack refills one claim every 10 minutes and banks up to two
("Banked: N/2" in Store -> Resource Packs). Each visit:

    1. Verify the game is running and take the action lock (worker).
    2. Open the main menu and the Store tile. The bottom-right shop icon is
       not used: it sometimes turns into a promotional sale shortcut.
    3. Find the "Freebie Pack" label (scrolling to the top first, then slowly
       down, if it is not visible) and read "Banked N/2" next to it.
    4. While a claim is banked and the Claim button is lit, tap Claim and
       confirm on a fresh frame (banked count lower, or Claim went dark).
       Only confirmed claims are counted (FREEBIES_DIAMONDS_PER_CLAIM each).
    5. Close the menu.

After a completed visit the next one is due in FREEBIES_CHECK_INTERVAL_S_SLOW
(default 900 s, so the bank of two never overflows); after a failed visit in
FREEBIES_CHECK_INTERVAL_S_FIRST. The worker runs in its own daemon thread and
is gated by `state.freebies_enabled` (toggleable via WebUI).
"""
import os
import threading
import time

import config.app_state as state
import config.settings as s

from core.file_logger import bot_log
from core.screen_cache import get_screen
from core.vision import find_template
from core.tapper import tap
from core.scroll import scroll_up, scroll_down
from core.game_state import is_game_running


_SCR_SLOT = "freebie_scr"
_thread = None
_thread_started = False
_stop_evt = threading.Event()
_wake_evt = threading.Event()   # set to make the worker run a cycle ASAP
_activation_generation = 0
_last_visit_ok = False          # the last visit reached the Freebie Pack row

# Navigation (900 x 1600, English; templates relative to images/)
_MAIN_MENU_TPL = "workflows/menu/main_menu_tpl.png"
_MENU_READY_TPL = "workflows/menu/this_prestige/craft_tpl.png"
_MENU_ROI = (10, 769, 890, 1579)
_STORE_TILE_TPL = "workflows/menu/permanent/store_tpl.png"
_TILES_ROI = (10, 100, 890, 1579)
_STORE_READY_TPL = "freebie/store_loot_tables.png"   # "Loot Tables" button, Store only
_STORE_READY_ROI = (600, 100, 900, 170)
_LABEL_TPL = "freebie/freebie_pack_label.png"        # "Freebie Pack" (best other row 0.74)
_LIST_ROI = (0, 160, 900, 1470)
# Relative to the label match's top-left (live frame 2026-11-27: label at (184, 930)).
_BANKED_REL = (551, -2, 631, 30)     # "0/2" inside "Banked: 0/2"
_CLAIM_REL = (506, 55, 621, 125)     # Claim button sample box
_CLAIM_TAP_REL = (563, 90)
_CLAIM_LIT_MIN_RED = 110             # mean red: dark (nothing banked) 74, lit button about 142
_MAX_BANKED = 2
_PAGE_WAIT_S = 3.0


def _check_interval() -> float:
    if _last_visit_ok:
        return float(s.FREEBIES_CHECK_INTERVAL_S_SLOW)
    return float(s.FREEBIES_CHECK_INTERVAL_S_FIRST)


def _log(msg: str):
    level = getattr(s, "FREEBIES_LOG_LEVEL", "FULL")
    bot_log(msg, level)


def _fresh_screen():
    """Force a new screenshot pull and return the color image (or None)."""
    img, _, _ = get_screen(_SCR_SLOT, max_age=0.0, force=True, color=True)
    return img


def _gray(img):
    import cv2
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img is not None and getattr(img, "ndim", 2) == 3 else img


def _find(img, template, roi, threshold=None):
    thr = float(getattr(s, "FREEBIES_MATCH_THRESHOLD", 0.8)) if threshold is None else threshold
    return find_template(_gray(img), template, roi=roi, threshold=thr)


def _click_with_log(cx: int, cy: int, what: str):
    j = int(getattr(s, "FREEBIES_TAP_JITTER_PX", 4))
    _log(f"[FREEBIES] tap {what} at ({cx},{cy}) jitter=+/-{j}px")
    ok = tap(cx, cy, jitter=j)
    if not ok:
        _log(f"[FREEBIES] tap failed: {what} at ({cx},{cy})")
    return ok


def _cycle_active(generation):
    return (getattr(state, "freebies_enabled", True) and not _stop_evt.is_set()
            and generation == _activation_generation)


def _cycle_wait(seconds, generation):
    """Keep animation timing, but release a cancelled cycle within 100 ms."""
    remaining = max(0.0, float(seconds))
    while remaining > 0:
        if not _cycle_active(generation):
            return False
        delay = min(0.1, remaining)
        time.sleep(delay)
        remaining -= delay
    return _cycle_active(generation)


def _wait_for(ready, generation, timeout=_PAGE_WAIT_S, step=0.25):
    """Poll fresh frames until ready(img) is true. Returns the frame or None."""
    for _ in range(max(1, int(timeout / step))):
        if not _cycle_wait(step, generation):
            return None
        img = _fresh_screen()
        if img is not None and ready(img):
            return img
    return None


def _scroll_to_top(generation):
    """Scroll area hard up (several full scrolls to reach the top)."""
    pre = int(getattr(s, "FREEBIES_SCROLL_TO_TOP_COUNT", 6))
    area = tuple(getattr(s, "FREEBIES_CLAIM_ROI", (645, 107, 835, 1472)))
    _log(f"[FREEBIES] scrolling to top ({pre}x fast, area={area})")
    for _ in range(pre):
        if not _cycle_active(generation):
            return False
        if not scroll_down(area=area, speed="fast"):  # finger DOWN -> list UP
            _log("[FREEBIES] scroll to top failed -> abort cycle")
            return False
        if not _cycle_wait(0.15, generation):
            return False
    return True


# ============================================================
#  Store page reading
# ============================================================

def _read_banked(gray, roi):
    """'N/2' -> N, or None if unreadable (Smart Craft's pixel-font reader)."""
    from features import smart_craft as sc
    if not sc._glyphs:
        sc.load_templates()
    text = sc.read_text(gray, roi) or ""
    head, _, total = text.partition("/")
    return int(head) if head.isdigit() and total.isdigit() else None


def read_pack(img):
    """Freebie Pack row on a Store frame: {banked, lit, claim: (x, y)} or None."""
    gray = _gray(img)
    label = find_template(gray, _LABEL_TPL, roi=_LIST_ROI, threshold=0.9)
    if not label["found"]:
        return None
    lx, ly = label["x"], label["y"]
    bx1, by1, bx2, by2 = _BANKED_REL
    cx1, cy1, cx2, cy2 = _CLAIM_REL
    h, w = gray.shape[:2]
    if ly + cy2 > h:
        return None      # row cut off at the bottom: scroll further
    patch = img[ly + cy1:ly + cy2, lx + cx1:lx + cx2]
    red = float(patch[:, :, 2].mean()) if getattr(img, "ndim", 2) == 3 else 0.0
    return {"banked": _read_banked(gray, (lx + bx1, ly + by1, lx + bx2, ly + by2)),
            "lit": red > _CLAIM_LIT_MIN_RED,
            "claim": (lx + _CLAIM_TAP_REL[0], ly + _CLAIM_TAP_REL[1])}


def _store_open(img):
    return _find(img, _STORE_READY_TPL, _STORE_READY_ROI, 0.9)["found"]


def _open_store(generation):
    """Main menu -> Store. Returns the first Store frame or None."""
    img = _fresh_screen()
    if img is None:
        _log("[FREEBIES] no screenshot -> skipping cycle")
        return None
    if _store_open(img):
        return img
    if not _find(img, _MENU_READY_TPL, _MENU_ROI)["found"]:
        button = _find(img, _MAIN_MENU_TPL, _MENU_ROI)
        if not button["found"]:
            _log(f"[FREEBIES] main menu button not found (score={button['score']:.3f}) -> skipping cycle")
            return None
        if not _cycle_active(generation) or not _click_with_log(button["cx"], button["cy"], "main menu"):
            return None
        img = _wait_for(lambda i: _find(i, _MENU_READY_TPL, _MENU_ROI)["found"], generation)
        if img is None:
            _log("[FREEBIES] main menu did not open -> abort cycle")
            return None
    tile = _find(img, _STORE_TILE_TPL, _TILES_ROI, 0.9)   # 0.97 on the menu, up to 0.79 elsewhere
    if not tile["found"]:
        _log(f"[FREEBIES] Store tile not found (score={tile['score']:.3f}) -> abort cycle")
        return None
    if not _cycle_active(generation) or not _click_with_log(tile["cx"], tile["cy"], "Store"):
        return None
    wait = max(_PAGE_WAIT_S, float(getattr(s, "FREEBIES_POST_OPEN_DELAY_S", 1.0)))
    img = _wait_for(_store_open, generation, timeout=wait)
    if img is None:
        _log("[FREEBIES] Store did not open -> abort cycle")
    return img


def _find_pack(img, generation):
    """Return (frame, pack) with the Freebie Pack row visible, or (None, None)."""
    pack = read_pack(img)
    if pack is not None:
        return img, pack
    _log("[FREEBIES] Freebie Pack row not visible -> scrolling")
    if not _scroll_to_top(generation) or not _cycle_wait(0.4, generation):
        return None, None
    attempts = int(getattr(s, "FREEBIES_SCROLL_ATTEMPTS", 4))
    area = tuple(getattr(s, "FREEBIES_CLAIM_ROI", (645, 107, 835, 1472)))
    for attempt in range(attempts + 1):
        img = _fresh_screen()
        pack = read_pack(img) if img is not None else None
        if pack is not None:
            return img, pack
        if attempt == attempts or not _cycle_active(generation):
            break
        _log(f"[FREEBIES] slow-scroll searching attempt {attempt + 1}/{attempts}")
        if not scroll_up(area=area, speed="slow"):
            _log("[FREEBIES] search scroll failed -> abort cycle")
            return None, None
        if not _cycle_wait(0.4, generation):
            return None, None
    _log(f"[FREEBIES] Freebie Pack row NOT found after {attempts} scrolls -> abort cycle")
    return None, None


def _claimed(before, after):
    if after is None:
        return False
    if before["banked"] is not None and after["banked"] is not None:
        return after["banked"] < before["banked"]
    return before["lit"] and not after["lit"]


def _record_claim(amount):
    state.freebies_claimed_count = getattr(state, "freebies_claimed_count", 0) + 1
    state.freebies_diamonds_total = getattr(state, "freebies_diamonds_total", 0) + amount
    state.freebies_last_claim_ts = time.time()
    # Freebie-Gems statistics (survive restarts, tracked per hourly bucket)
    try:
        from features.gem_statistics import get_gem_statistics
        recorded = (get_gem_statistics().record_claim("gem", amount=amount, in_flight=True)
                    if getattr(state, "gem_statistics_enabled", True) else None)
        if recorded is not None:
            _log(f"[FREEBIES] Freebie-Gems stats updated (+{recorded})")
    except Exception as e:
        _log(f"[FREEBIES] stats update failed: {type(e).__name__}: {e}")


def _close_menu(generation):
    if not _cycle_wait(getattr(s, "FREEBIES_POST_CLAIM_DELAY_S", 1.0), generation):
        return False
    img = _fresh_screen()
    if img is None:
        return False
    close_roi = tuple(getattr(s, "FREEBIES_CLOSE_ROI", (3, 1472, 898, 1547)))
    close_tpl = getattr(s, "FREEBIES_CLOSE_TEMPLATE", "close_menu.png")
    cm = _find(img, close_tpl, close_roi)
    if not cm["found"]:
        _log(f"[FREEBIES] close button NOT found (score={cm['score']:.3f}) -> menu stays open")
        return False
    if not _cycle_active(generation):
        return False
    return bool(_click_with_log(cm["cx"], cm["cy"], "close-menu"))


def _dump(img, reason):
    try:
        import cv2
        folder = os.path.join(str(getattr(s, "BASE_DIR", ".")), "temp", "freebies_debug")
        os.makedirs(folder, exist_ok=True)
        cv2.imwrite(os.path.join(folder, f"{time.strftime('%Y%m%d_%H%M%S')}_{reason}.png"), img)
    except Exception:
        pass


def _run_claim_cycle() -> bool:
    """One Store visit. Returns True when the Freebie Pack row was reached
    (whether or not anything was banked) and the menu was closed."""
    global _last_visit_ok
    generation = _activation_generation
    if not _cycle_active(generation):
        return False
    img = _open_store(generation)
    if img is None:
        _last_visit_ok = False if _cycle_active(generation) else _last_visit_ok
        return False
    img, pack = _find_pack(img, generation)
    if pack is None:
        if _cycle_active(generation):
            _last_visit_ok = False
            _close_menu(generation)
        return False
    _last_visit_ok = True
    banked_at_start = pack["banked"]
    amount = int(getattr(s, "FREEBIES_DIAMONDS_PER_CLAIM", 0))
    claimed = 0
    for _ in range(_MAX_BANKED):
        if pack["banked"] == 0 or not pack["lit"]:
            if pack["banked"] and not pack["lit"]:
                _log(f"[FREEBIES] {pack['banked']} banked but Claim is dark -> not claiming")
            break
        if not _cycle_active(generation):
            return False
        if not _click_with_log(*pack["claim"], "claim-button"):
            return False
        after = None
        for _attempt in range(6):
            if not _cycle_wait(0.3, generation):
                return False
            frame = _fresh_screen()
            after = read_pack(frame) if frame is not None else None
            if _claimed(pack, after):
                break
        if not _claimed(pack, after):
            _log(f"[FREEBIES] claim not confirmed (banked {pack['banked']} -> "
                 f"{after['banked'] if after else 'row not visible'}) -> stopping")
            if frame is not None:
                _dump(frame, "claim_unconfirmed")
            break
        claimed += 1
        _record_claim(amount)
        pack = after
    banked = "?" if banked_at_start is None else banked_at_start
    if claimed:
        claimed_level = getattr(s, "FREEBIES_LOG_LEVEL_CLAIMED", getattr(s, "FREEBIES_LOG_LEVEL", "FULL"))
        bot_log(f"[FREEBIES] claimed {claimed} of {banked} banked (confirmed); estimated +{claimed * amount} "
                f"Diamonds (totals: {state.freebies_diamonds_total} estimated gems / "
                f"{state.freebies_claimed_count}x)", claimed_level)
    else:
        _log(f"[FREEBIES] nothing claimed ({banked} banked)")
    return _close_menu(generation)


def _worker():
    _log(f"[FREEBIES] worker thread started (enabled={state.freebies_enabled}, "
         f"FIRST={s.FREEBIES_CHECK_INTERVAL_S_FIRST:g}s, "
         f"SLOW={s.FREEBIES_CHECK_INTERVAL_S_SLOW:g}s)")
    # Small initial delay so ADB + game_state get a head start (wake-able)
    _sleep_interruptible(3.0)
    while not _stop_evt.is_set():
        try:
            interval = _check_interval()
            if not getattr(state, "freebies_enabled", True):
                state.freebies_next_check_ts = 0.0  # disabled -> no cooldown shown
                # Use interruptible sleep so a toggle-ON wake doesn't get
                # buffered and trigger an extra cycle after the first run.
                _sleep_interruptible(min(5.0, interval))
                continue

            if not is_game_running():
                _log("[FREEBIES] game not running -> skipping tick")
                wait = min(30.0, interval)
                _wait_for_next_check(wait, "no_game")
                continue

            if getattr(state, "automation_paused", False):
                _log("[FREEBIES] automation paused -> waiting")
                _wait_for_next_check(min(10.0, interval), "paused")
                continue
            lock = getattr(state, "ingame_action_lock", None)
            if lock is not None:
                # Block — freebies cycles are infrequent, waiting is fine.
                if lock.locked():
                    _log("[FREEBIES] another feature is busy -> waiting for action lock")
                with lock:
                    # A pause may have been requested while this thread waited.
                    paused = getattr(state, "automation_paused", False)
                    if state.freebies_enabled and not paused:
                        _run_claim_cycle()
                if paused:
                    _wait_for_next_check(min(10.0, interval), "paused")
                    continue
            else:
                _run_claim_cycle()
            _wait_for_next_check(_check_interval(), "ok" if _last_visit_ok else "visit_failed")
        except Exception as e:
            _log(f"[FREEBIES] worker error: {type(e).__name__}: {e}")
            time.sleep(5.0)


def _wait_for_next_check(wait: float, reason: str):
    state.freebies_next_check_ts = time.time() + wait if state.freebies_enabled else 0.0
    if state.freebies_enabled:
        _log(f"[FREEBIES] next check in {wait:g}s (reason={reason})")
    _sleep_interruptible(wait)


def _sleep_interruptible(seconds: float):
    """Sleep until `seconds` elapse, or until stop/wake is signalled.
    Wake clears itself, so a toggle()-induced wake triggers exactly one cycle."""
    if _wake_evt.wait(timeout=seconds):
        _wake_evt.clear()


def _sync_statistics(active):
    """Keep statistics failures from stopping game automation or its switch."""
    try:
        from features.gem_statistics import get_gem_statistics
        get_gem_statistics().set_active(active and getattr(state, "gem_statistics_enabled", True))
    except Exception as exc:
        _log(f"[FREEBIES] statistics state update failed: {type(exc).__name__}: {exc}")


def start():
    """Start the background worker (idempotent)."""
    global _thread, _thread_started, _last_visit_ok
    if _thread_started:
        return
    _sync_statistics(bool(getattr(state, "freebies_enabled", True)))
    _thread_started = True
    _last_visit_ok = False
    _stop_evt.clear()
    _thread = threading.Thread(target=_worker, daemon=True, name="freebies-worker")
    _thread.start()


def toggle() -> bool:
    """Flip state.freebies_enabled. Returns the new value.
    On ON-transition the worker wakes immediately and runs a cycle — no
    waiting for the configured interval first."""
    global _activation_generation, _last_visit_ok
    new = not bool(getattr(state, "freebies_enabled", True))
    _sync_statistics(new)
    state.freebies_enabled = new
    state.freebies_next_check_ts = 0.0
    if new:
        _activation_generation += 1
        _last_visit_ok = False
    _wake_evt.set()
    if new:
        _log(f"[FREEBIES] toggled -> ON (checking now)")
    else:
        _log("[FREEBIES] toggled -> OFF")
    return new
