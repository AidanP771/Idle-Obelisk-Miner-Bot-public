# features/items.py - use items from the item bag, open item and relic chests
"""
Items (game wiki "Items", "Chests"; verified in game 2026-11-25):

- Item bag: the backpack button bottom right of the mine screen. Held items sit
  in a grid of 80 px slots (7 per row, rows at y 1123 and 1210); positions
  change with the items held, so items are found by their picture. Each slot
  shows its count bottom right; an active buff shows a bright green timer at
  the top of its slot. Tapping a slot selects it (its icon appears in the
  panel below) and lights the Use button. Using an item while its buff runs
  ADDS its duration, so an early use wastes nothing.
- Chests page (main menu -> Chests): "Open All N Chests" (quantity "All"),
  first tap arms it (orange "Confirm?"), the second opens. No cost.
- Relics page (main menu -> Relics): "Open All" top right opens every relic
  chest with one tap; a loot list follows ("Tap anywhere to close"). The Reroll
  button (top left) costs 200 gems: this module never taps the left half of
  that button row.

Policy (user, 2026-11-25), all switchable in the WebUI:
- ITEMS_KEEP_RUNNING (Bread, Drone Juice, Banana Coffee, Eye of Newt) are used
  whenever their buff is not running, while more than ITEMS_RESERVE are held.
- Strawberries (EXP 3x) likewise, but only while the HUD is not "Level Capped".
- Blue Cow (2x game speed) as soon as one is held.
- Pickaxe items (Primal Meat, Rock Cake, Juicy Plums, Apple, Pike) and Chaos
  Totem are used by workflows through the `use_items` step (obelisk fight,
  bomb rotation); `item_cooldown` blocks a step's items for a while.
- Every ITEMS_CHESTS_INTERVAL_S item chests and relic chests are opened. The
  bag is read before and after, and the difference is recorded as the chest
  yield in item_record.json (next to gem_statistics.json).

Every action holds state.ingame_action_lock; new work waits while
state.automation_paused is set. The workflow step runs inside the workflow's
own lock.
"""
import os
import threading
import time
from pathlib import Path

import cv2
import numpy as np

import config.app_state as state
import config.settings as s
from config.json_store import atomic_write_json, read_json
from core.file_logger import bot_log
from core.game_state import is_game_running
from core.image_io import read_image
from core.screen_cache import get_cached_screen, get_screen
from core.tapper import tap
from core.vision import find_template

_SLOT_NAME = "items_scr"
_IT = "workflows/item_interface"

# key -> (template under images/, display name, wiki base duration in s; 0 = instant)
ITEMS = {
    "apple": (f"{_IT}/Apple_tpl.png", "Apple", 90),
    "bread": (f"{_IT}/bread_tpl.png", "Bread", 90),
    "drone_juice": (f"{_IT}/juice_tpl.png", "Drone Juice", 90),
    "banana_coffee": (f"{_IT}/coffee_tpl.png", "Banana Coffee", 120),
    "pike": (f"{_IT}/pike_tpl.png", "Pike", 120),
    "eye_of_newt": (f"{_IT}/eye_tpl.png", "Eye of Newt", 120),
    "rock_cake": (f"{_IT}/rockcake_tpl.png", "Rock Cake", 150),
    "juicy_plums": (f"{_IT}/juncyplums_tpl.png", "Juicy Plums", 150),
    "chaos_totem": (f"{_IT}/chaostotem_tpl.png", "Chaos Totem", 150),
    "primal_meat": (f"{_IT}/primalmeat_tpl.png", "Primal Meat", 180),
    "strawberries": (f"{_IT}/strawberries_tpl.png", "Strawberries", 180),
    "charge_magnet": (f"{_IT}/chargemagnet_tpl.png", "Charge Magnet", 0),
    "blue_cow": (f"{_IT}/BlueCow_tpl.png", "Blue Cow", 600),
    "skill_bit": ("items/skill_bit.png", "Skill Bit", 0),
}

# --- geometry (900 x 1600) ------------------------------------------------------------
_SLOT_X0, _SLOT_PITCH, _SLOT_ROWS, _SLOT_COLS, _SLOT_SIZE = 102, 90, (1123, 1210), 7, 80
_ITEM_THRESHOLD = 0.9          # own slot 0.97-0.998, any other slot <= 0.77 (2026-11-25 bag)
_DIGIT_MIN_SCORE = 0.7
_BAG_BUTTON_ROI = (735, 1289, 890, 1491)
_SELECTED_ICON_ROI = (100, 1295, 195, 1385)
_USE_ROI = (600, 1375, 725, 1455)
_MENU_ROI = (10, 769, 890, 1579)
_CLOSE_MENU_ROI = (405, 1471, 658, 1546)
_HUD_CAPPED_ROI = (100, 30, 380, 95)
_CHEST_BUTTON = (300, 1090, 600, 1140)          # color sample inside "Open All N Chests"
_CHEST_BUTTON_TAP = (450, 1150)
_CHEST_ALL = (700, 1290, 810, 1340)              # quantity "All" (green when selected)
_CHEST_ALL_TAP = (757, 1316)
_RELIC_OPEN_ALL_ROI = (600, 140, 830, 260)       # right side only: Reroll is at x 108-258
_RELIC_CLOSE_TAP = (450, 1300)                    # blank part of the loot list ("tap anywhere")
_RELIC_MIN_TAP_X = 560

_BAG_OPEN_TPL = f"{_IT}/nav/item_interface_opened_tpl.png"
_BAG_CLOSED_TPL = f"{_IT}/nav/item_interface_closed_tpl.png"
_MAIN_MENU_TPL = "workflows/menu/main_menu_tpl.png"
_CLOSE_MENU_TPL = "workflows/menu/close_menu_tpl.png"
_MENU_READY_TPL = "workflows/menu/this_prestige/craft_tpl.png"
_CHESTS_TILE = "workflows/menu/permanent/chests_tpl.png"
_RELICS_TILE = "workflows/menu/permanent/relics_tpl.png"
_CHESTS_READY = "items/chests_ready.png"
_RELICS_READY = "items/relics_ready.png"
_RELICS_OPEN_ALL = "items/relics_open_all.png"
_RELICS_TAP_TO_CLOSE = "items/relics_tap_to_close.png"
_LEVEL_CAPPED = "workflows/hud/level_capped_tpl.png"

_RECORD_FILE = Path(str(getattr(s, "BASE_DIR", "."))) / "item_record.json"
_RECORD_KEEP = 1000

_templates = {}        # key -> gray template
_digits = {}           # "0".."9" -> gray reference cell (19 x 11)
_next_check = {}       # key -> earliest time worth opening the bag for it again
_blocked_until = {}    # item_cooldown key -> time
_key_used_ts = {}      # cooldown_key -> last time use_items used an item for it
_BLOCK_WINDOW_S = 300.0  # item_cooldown only blocks after a real use this recently
_last_chests_ts = 0.0
_record_lock = threading.Lock()
_thread = None
_stop_evt = threading.Event()


class _Abort(Exception):
    pass


def _log(msg: str, level: str = "TXT"):
    bot_log(msg, level)


def load_templates(img_dir=None) -> tuple:
    """Load item templates and count digits. Returns (items, digits)."""
    base = str(img_dir or s.IMG_DIR)
    _templates.clear()
    _digits.clear()
    for key, (rel, _name, _dur) in ITEMS.items():
        img = read_image(os.path.join(base, rel), cv2.IMREAD_GRAYSCALE)
        if img is not None:
            _templates[key] = img
    for d in "0123456789":
        img = read_image(os.path.join(base, "items", "digits", f"{d}.png"), cv2.IMREAD_GRAYSCALE)
        if img is not None:
            _digits[d] = img
    return len(_templates), len(_digits)


def item_keys(text) -> list:
    """Comma-separated setting -> known item keys (unknown names are ignored)."""
    keys = [k.strip().lower().replace(" ", "_") for k in str(text or "").split(",")]
    return [k for k in keys if k in ITEMS]


# ============================================================
#  Reading the bag
# ============================================================

def _slot_origin(row, col):
    return _SLOT_X0 + _SLOT_PITCH * col, _SLOT_ROWS[row]


def read_count(gray, row, col):
    """The count at the bottom right of a slot (right-aligned digits on a 12 px
    pitch), or None when no digit is readable."""
    if not _digits:
        return None
    x0, y0 = _slot_origin(row, col)
    text = ""
    for k in range(3):
        x = x0 + 60 - 12 * k
        cell = gray[y0 + 51:y0 + 70, x:x + 11]
        if cell.shape != (19, 11) or int((cell > 150).sum()) < 6:
            break
        best, score = None, -1.0
        for d, ref in _digits.items():
            sc = float(cv2.matchTemplate(cell, ref, cv2.TM_CCOEFF_NORMED)[0][0])
            if sc > score:
                best, score = d, sc
        if score < _DIGIT_MIN_SCORE:
            return None if not text else int(text)  # an unknown glyph left of known digits
        text = best + text
    return int(text) if text else None


def slot_active(img, row, col) -> bool:
    """A running buff shows a bright green timer at the top of its slot
    (G > 200, R and B < 90; the green Chaos Totem icon stays below that)."""
    x0, y0 = _slot_origin(row, col)
    reg = img[y0 + 3:y0 + 30, x0 + 3:x0 + 77].astype(np.int16)
    green = (reg[..., 1] > 200) & (reg[..., 2] < 90) & (reg[..., 0] < 90)
    return int(green.sum()) >= 20


def read_bag(img, gray) -> dict:
    """{key: {row, col, x, y, count, active}} for every recognised slot; each
    slot goes to its best-matching template (>= _ITEM_THRESHOLD)."""
    found = {}
    for row in range(len(_SLOT_ROWS)):
        for col in range(_SLOT_COLS):
            x0, y0 = _slot_origin(row, col)
            reg = gray[y0:y0 + _SLOT_SIZE, x0:x0 + _SLOT_SIZE]
            best, score = None, _ITEM_THRESHOLD
            for key, tpl in _templates.items():
                if tpl.shape[0] > reg.shape[0] or tpl.shape[1] > reg.shape[1]:
                    continue
                sc = float(cv2.matchTemplate(reg, tpl, cv2.TM_CCOEFF_NORMED).max())
                if sc >= score:
                    best, score = key, sc
            if best and best not in found:
                found[best] = {"row": row, "col": col, "x": x0 + 40, "y": y0 + 40,
                               "count": read_count(gray, row, col), "active": slot_active(img, row, col)}
    return found


def _summary(bag) -> str:
    return ", ".join(f"{ITEMS[k][1]} {v['count'] if v['count'] is not None else '?'}"
                     + (" (active)" if v["active"] else "") for k, v in sorted(bag.items())) or "empty"


# ============================================================
#  Screen helpers
# ============================================================

def _fresh():
    img, _w, _h = get_screen(_SLOT_NAME, max_age=0.0, force=True, color=True)
    if img is None:
        raise _Abort("screenshot failed")
    return img, cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def _find(gray, template, roi, threshold=0.85):
    return find_template(gray, template, roi=roi, threshold=threshold)


def _tap(x, y, what):
    bot_log(f"[ITEMS] tap {what} at ({int(x)},{int(y)})", "TXT")
    if not tap(int(x), int(y), jitter=3):
        raise _Abort(f"tap failed: {what}")


def _wait_until(ready, timeout=2.5, step=0.25):
    img = gray = None
    for _ in range(max(1, int(timeout / step))):
        time.sleep(step)
        img, gray = _fresh()
        if ready(img, gray):
            return img, gray, True
    return img, gray, False


def _mean_bgr(img, box):
    x1, y1, x2, y2 = box
    return img[y1:y2, x1:x2].reshape(-1, 3).mean(0)


def _bag_open(gray) -> bool:
    """The open-bag button alone also matches the dimmed button behind a menu,
    so the bag panel's Use button must be visible too."""
    return (_find(gray, _BAG_OPEN_TPL, _BAG_BUTTON_ROI, 0.9)["found"]
            and _find(gray, f"{_IT}/nav/use_item_tpl.png", _USE_ROI, 0.9)["found"])


def _open_bag():
    img, gray = _fresh()
    if _bag_open(gray):
        return img, gray
    button = _find(gray, _BAG_CLOSED_TPL, _BAG_BUTTON_ROI, 0.8)
    if not button["found"]:
        raise _Abort(f"item bag button not visible (score={button['score']:.3f})")
    _tap(button["cx"], button["cy"], "item bag")
    img, gray, ok = _wait_until(lambda _i, g: _bag_open(g))
    if not ok:
        raise _Abort("item bag did not open")
    return img, gray


def _close_bag():
    try:
        _img, gray = _fresh()
        button = _find(gray, _BAG_OPEN_TPL, _BAG_BUTTON_ROI, 0.9)
        if button["found"] and _bag_open(gray):
            _tap(button["cx"], button["cy"], "close item bag")
            _wait_until(lambda _i, g: not _bag_open(g), timeout=1.5)
    except _Abort as exc:
        _log(f"[ITEMS] closing the item bag failed: {exc}")


def _close_menu():
    try:
        _img, gray = _fresh()
        button = _find(gray, _CLOSE_MENU_TPL, _CLOSE_MENU_ROI, 0.8)
        if button["found"]:
            _tap(button["cx"], button["cy"], "close menu")
            time.sleep(0.6)
    except _Abort as exc:
        _log(f"[ITEMS] closing the menu failed: {exc}")


def _open_tile(tile, ready_tpl, name):
    img, gray = _fresh()
    if not _find(gray, _MENU_READY_TPL, _MENU_ROI, 0.8)["found"]:
        button = _find(gray, _MAIN_MENU_TPL, _MENU_ROI, 0.8)
        if not button["found"]:
            raise _Abort(f"main menu button not found (score={button['score']:.3f})")
        _tap(button["cx"], button["cy"], "main menu")
        img, gray, ok = _wait_until(lambda _i, g: _find(g, _MENU_READY_TPL, _MENU_ROI, 0.8)["found"])
        if not ok:
            raise _Abort("main menu did not open")
    match = _find(gray, tile, (10, 100, 890, 1579), 0.8)
    if not match["found"]:
        raise _Abort(f"{name} tile not found (score={match['score']:.3f})")
    _tap(match["cx"], match["cy"], name)
    img, gray, ok = _wait_until(lambda _i, g: _find(g, ready_tpl, None, 0.85)["found"])
    if not ok:
        raise _Abort(f"{name} page did not open")
    return img, gray


# ============================================================
#  Using items (caller holds the action lock)
# ============================================================

def _use_one(key, info, bag):
    """Select the slot, check the selected icon and a lit Use button, tap Use,
    confirm by a lower count, a running timer or the slot disappearing."""
    name = ITEMS[key][1]
    _tap(info["x"], info["y"], f"select {name}")

    def selected(img, gray):
        icon = _find(gray, ITEMS[key][0], _SELECTED_ICON_ROI, 0.85)["found"]
        return icon and _mean_bgr(img, _USE_ROI)[2] > 120
    img, gray, ok = _wait_until(selected, timeout=1.5)
    if not ok:
        _log(f"[ITEMS] {name}: not selected or Use not lit -> skipped")
        return False
    use = _find(gray, f"{_IT}/nav/use_item_tpl.png", _USE_ROI, 0.8)
    if not use["found"]:
        _log(f"[ITEMS] {name}: Use button not found -> skipped")
        return False
    _tap(use["cx"], use["cy"], f"Use {name}")
    for _ in range(5):
        time.sleep(0.3)
        img, gray = _fresh()
        after = read_bag(img, gray).get(key)
        if after is None:
            return True                       # last one used and no timer shown
        if info["count"] is not None and after["count"] is not None and after["count"] < info["count"]:
            return True
        if after["active"] and not info["active"]:
            return True
    _log(f"[ITEMS] {name}: Use had no visible effect")
    return False


def _due(keys, now):
    return [k for k in keys if now >= _next_check.get(k, 0.0)]


def _run_uses(keys, reserve, reason, img, gray):
    """Use every key that is held above reserve and not running. Returns (used, bag)."""
    bag = read_bag(img, gray)
    used = []
    now = time.time()
    for key in keys:
        name = ITEMS[key][1]
        info = bag.get(key)
        if info is None:
            _next_check[key] = now + 300
            continue
        if info["active"]:
            _next_check[key] = now + 30
            continue
        if info["count"] is None:
            _log(f"[ITEMS] {name}: count unreadable -> not used")
            _next_check[key] = now + 300
            continue
        if info["count"] <= reserve:
            _next_check[key] = now + 300
            continue
        if _use_one(key, info, bag):
            used.append(key)
            state.items_used = getattr(state, "items_used", 0) + 1
            _next_check[key] = now + max(30.0, ITEMS[key][2] * 0.9)
            _log(f"[ITEMS] used {name} ({reason}; held {info['count']})", "FULL")
            _record_use(key)
        else:
            _next_check[key] = now + 120
        img, gray = _fresh()
        bag = read_bag(img, gray)
    return used, bag


def use_items(keys, reserve=0, reason="workflow", cooldown_key=None) -> list:
    """For the `use_items` workflow step (the workflow holds the action lock).
    Opens the bag only when one of the items may need it. Returns used keys."""
    keys = [k for k in keys if k in ITEMS]
    if cooldown_key and time.time() < _blocked_until.get(cooldown_key, 0.0):
        _log(f"[ITEMS] {reason}: items blocked ({cooldown_key}) until "
             f"{time.strftime('%H:%M:%S', time.localtime(_blocked_until[cooldown_key]))}")
        return []
    if not _templates:
        load_templates()
    if not _due(keys, time.time()):
        return []
    try:
        img, gray = _open_bag()
        used, bag = _run_uses(keys, int(reserve), reason, img, gray)
        if used and cooldown_key:
            _key_used_ts[cooldown_key] = time.time()
        _record_snapshot(bag)
        return used
    except _Abort as exc:
        _log(f"[ITEMS] {reason}: {exc}")
        return []
    finally:
        _close_bag()


def block(key, seconds) -> bool:
    """item_cooldown step: no use_items with this cooldown_key for `seconds`,
    but only when that key's items were really used just before (the attempt
    happened). Without a recent use (items off, none held, still running from
    an earlier use) nothing is blocked. Returns True when blocked."""
    key = str(key)
    if time.time() - _key_used_ts.get(key, -1e18) > _BLOCK_WINDOW_S:
        _log(f"[ITEMS] no items used for '{key}' just before -> not blocked")
        return False
    _blocked_until[key] = time.time() + max(0.0, float(seconds))
    _log(f"[ITEMS] items for '{key}' blocked for {float(seconds):.0f}s")
    return True


# ============================================================
#  Chests and relics (caller holds the action lock)
# ============================================================

def open_chests() -> bool:
    """Open all item chests. Returns True when chests were opened."""
    img, gray = _open_tile(_CHESTS_TILE, _CHESTS_READY, "Chests")
    b, g, r = _mean_bgr(img, _CHEST_BUTTON)
    if r < 120:
        _log("[ITEMS] no item chests to open")
        return False
    _b, g_all, r_all = _mean_bgr(img, _CHEST_ALL)
    if not g_all > r_all + 40:
        _tap(*_CHEST_ALL_TAP, "quantity All")
        img, gray, ok = _wait_until(lambda i, _g: (lambda m: m[1] > m[2] + 40)(_mean_bgr(i, _CHEST_ALL)))
        if not ok:
            raise _Abort("quantity All not selected")
    _tap(*_CHEST_BUTTON_TAP, "Open All Chests")
    img, gray, ok = _wait_until(lambda i, _g: (lambda m: m[2] > 180 and m[1] > 80)(_mean_bgr(i, _CHEST_BUTTON)))
    if not ok:
        raise _Abort("chest confirmation did not appear")
    _tap(*_CHEST_BUTTON_TAP, "Confirm Open All Chests")
    img, gray, ok = _wait_until(lambda i, _g: _mean_bgr(i, _CHEST_BUTTON)[2] < 120, timeout=4.0)
    if not ok:
        raise _Abort("chests did not open")
    _log("[ITEMS] item chests opened", "FULL")
    state.items_chest_batches = getattr(state, "items_chest_batches", 0) + 1
    return True


def open_relics() -> bool:
    """Open all relic chests with the top-right Open All. Never touches Reroll."""
    img, gray = _open_tile(_RELICS_TILE, _RELICS_READY, "Relics")
    match = _find(gray, _RELICS_OPEN_ALL, _RELIC_OPEN_ALL_ROI, 0.85)
    if not match["found"] or match["cx"] < _RELIC_MIN_TAP_X:
        raise _Abort(f"relic Open All not found (score={match['score']:.3f})")
    x1, y1 = match["x"], match["y"]
    if _mean_bgr(img, (x1, y1, x1 + match["w"], y1 + 30))[2] < 120:
        _log("[ITEMS] no relic chests to open")
        return False
    _tap(match["cx"], match["cy"], "relic Open All")
    img, gray, ok = _wait_until(lambda _i, g: _find(g, _RELICS_TAP_TO_CLOSE, None, 0.85)["found"], timeout=4.0)
    if not ok:
        raise _Abort("relic loot list did not appear")
    _log("[ITEMS] relic chests opened", "FULL")
    state.items_relic_batches = getattr(state, "items_relic_batches", 0) + 1
    _tap(*_RELIC_CLOSE_TAP, "close relic loot")
    _wait_until(lambda _i, g: not _find(g, _RELICS_TAP_TO_CLOSE, None, 0.85)["found"], timeout=2.0)
    return True


# ============================================================
#  Record (item_record.json)
# ============================================================

def _counts(bag):
    return {k: v["count"] for k, v in bag.items() if v["count"] is not None}


def _update_record(fn):
    with _record_lock:
        try:
            data = read_json(_RECORD_FILE) if _RECORD_FILE.exists() else {}
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        data.setdefault("snapshots", [])
        data.setdefault("chest_batches", [])
        data.setdefault("used", {})
        fn(data)
        data["snapshots"] = data["snapshots"][-_RECORD_KEEP:]
        data["chest_batches"] = data["chest_batches"][-_RECORD_KEEP:]
        try:
            atomic_write_json(_RECORD_FILE, data)
        except Exception as exc:
            _log(f"[ITEMS] record not saved: {type(exc).__name__}: {exc}")


def _record_snapshot(bag):
    counts = _counts(bag)
    _log(f"[ITEMS] bag: {_summary(bag)}")
    _update_record(lambda d: d["snapshots"].append({"ts": round(time.time()), "counts": counts}))


def _record_use(key):
    _update_record(lambda d: d["used"].__setitem__(key, d["used"].get(key, 0) + 1))


def chest_yield(before, after) -> dict:
    """Per item: count after minus count before (absent = 0); only gains."""
    gained = {}
    for key in set(before) | set(after):
        diff = after.get(key, 0) - before.get(key, 0)
        if diff > 0:
            gained[key] = diff
    return gained


# ============================================================
#  Periodic cycle
# ============================================================

def _level_capped() -> bool:
    img, _w, _h = get_cached_screen("game_scr", max_age=120.0, color=False)
    if img is None:
        return True   # unknown: do not spend Strawberries
    return _find(img, _LEVEL_CAPPED, _HUD_CAPPED_ROI, 0.9)["found"]


def _periodic_keys() -> list:
    keys = item_keys(getattr(s, "ITEMS_KEEP_RUNNING", ""))
    if getattr(s, "ITEMS_STRAWBERRIES_WHEN_UNCAPPED", True) and not _level_capped():
        keys.append("strawberries")
    return keys


def run_cycle() -> str:
    """Caller holds the action lock. Returns ok | nothing | error."""
    global _last_chests_ts
    now = time.time()
    reserve = int(getattr(s, "ITEMS_RESERVE", 2))
    keys = _periodic_keys()
    cow = ["blue_cow"] if getattr(s, "ITEMS_USE_BLUE_COW", True) else []
    chests_due = (now - _last_chests_ts >= float(getattr(s, "ITEMS_CHESTS_INTERVAL_S", 1800.0))
                  and (getattr(s, "ITEMS_OPEN_CHESTS", True) or getattr(s, "ITEMS_OPEN_RELICS", True)))
    if not chests_due and not _due(keys + cow, now):
        return "nothing"
    try:
        before = None
        opened = False
        if chests_due:
            _last_chests_ts = now
            img, gray = _open_bag()
            before = _counts(read_bag(img, gray))
            _close_bag()
            if getattr(s, "ITEMS_OPEN_CHESTS", True):
                opened = open_chests()
                _close_menu()
            if getattr(s, "ITEMS_OPEN_RELICS", True):
                open_relics()
                _close_menu()
        img, gray = _open_bag()
        if opened and before is not None:
            gained = chest_yield(before, _counts(read_bag(img, gray)))   # before any use
            _log("[ITEMS] chest yield: " + (", ".join(f"{ITEMS[k][1]} +{n}" for k, n in sorted(gained.items()))
                                            or "nothing readable"), "FULL")
            _update_record(lambda d: d["chest_batches"].append({"ts": round(time.time()), "gained": gained}))
        used, bag = _run_uses(keys, reserve, "keep running", img, gray)
        used_cow, bag = _run_uses(cow, 0, "Blue Cow", *_fresh()) if cow else ([], bag)
        _record_snapshot(bag)
        state.items_last_status = (f"ok: used {', '.join(ITEMS[k][1] for k in used + used_cow) or 'nothing'}"
                                   + ("; chests opened" if opened else ""))
        return "ok"
    except _Abort as exc:
        state.items_last_status = f"stopped: {exc}"
        _log(f"[ITEMS] cycle stopped: {exc}", "FULL")
        return "error"
    finally:
        _close_bag()
        _close_menu_if_open()


def _close_menu_if_open():
    try:
        _img, gray = _fresh()
        if _find(gray, _MENU_READY_TPL, _MENU_ROI, 0.8)["found"] or _find(gray, _CHESTS_READY, None, 0.85)["found"] \
                or _find(gray, _RELICS_READY, None, 0.85)["found"]:
            _close_menu()
    except _Abort:
        pass


def _try_run_with_lock() -> str:
    if not is_game_running():
        return "no_game"
    if getattr(state, "automation_paused", False):
        return "paused"
    lock = state.ingame_action_lock
    if not lock.acquire(blocking=False):
        return "busy"
    try:
        if getattr(state, "automation_paused", False):
            return "paused"
        return run_cycle()
    finally:
        lock.release()


def _worker():
    _log("[ITEMS] worker thread started")
    _stop_evt.wait(20.0)
    while not _stop_evt.is_set():
        interval = float(getattr(s, "ITEMS_CHECK_INTERVAL_S", 60.0))
        try:
            if getattr(s, "ITEMS_ENABLED", False):
                result = _try_run_with_lock()
                if result == "busy":
                    interval = min(interval, 10.0)
                elif result == "error":
                    interval = max(interval, 300.0)
        except Exception as exc:
            _log(f"[ITEMS] worker error: {type(exc).__name__}: {exc}", "FULL")
            interval = max(interval, 300.0)
        _stop_evt.wait(interval)


def start():
    """Load templates and start the worker (idempotent)."""
    global _thread
    if _thread is not None:
        return
    items, digits = load_templates()
    _log(f"[ITEMS] armed ({items} item templates, {digits} count digits; "
         f"enabled={getattr(s, 'ITEMS_ENABLED', False)})")
    _thread = threading.Thread(target=_worker, daemon=True, name="items-worker")
    _thread.start()
