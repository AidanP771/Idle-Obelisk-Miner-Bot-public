# features/smart_craft.py - Craft the bars that buyable upgrades still need
"""Smart Craft: read upgrade costs, craft the missing bars, buy the upgrades.

Cycle (holds the ingame action lock for the whole cycle):
    1. Open the main menu -> Upgrades. Scroll through the list and read every
       buyable row whose cost is a bar: cost icon (bar type) + "have/need".
       Rows without a "Buy" button (maxed, locked) are ignored.
    2. Per bar type: target = sum of all "need" values; shortfall = target - have.
    3. Open Craft. For each bar with a shortfall, read ore / bars / ore-per-bar
       from its row and press its Craft button in small batches. Every batch is
       verified with a fresh screenshot; crafting stops when the target is
       reached, ore runs out, or a batch shows no progress.
    4. Optionally press Buy on every bar-cost upgrade that is now affordable
       (bright Buy button, have >= need), verifying each purchase with a fresh
       frame, then close the menu. The in-game "Buy All" (first tap arms it,
       a second tap buys every affordable upgrade) is never tapped.

Recognition (900x1600 portrait, English UI):
    - Bar icons: images/smart_craft/bars/<name>.png. The icons share one sprite
      in different colours, so a shape match finds an icon and the mean colour
      decides which bar it is. Adding a PNG adds a bar type.
    - Numbers: the game draws text at half resolution and upscales it, so
      glyph pixels shift by one between positions. Glyphs are segmented as
      connected components and classified by coarse 3x5 ink zones against the
      reference variants images/smart_craft/digits/<0-9|slash>_<n>.png. A
      glyph is accepted only if its nearest reference is clearly closer than
      any other character; any unknown glyph or stray blob (e.g. "1.2k")
      rejects the whole number instead of guessing.
    - Cross-checks: the bar count read on Upgrades must match the Craft row,
      and each craft batch must change the bar count plausibly.

Only verified bar-count increases are counted. Selling is out of scope.
"""
import glob
import math
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
from core.screen_cache import get_screen
from core.scroll import scroll
from core.tapper import tap
from core.vision import find_template


_SLOT = "smart_craft_scr"
_TPL_DIR = "smart_craft"

# Shared in-game backgrounds of the list boxes (BGR) - used to isolate icon pixels.
_BOX_BG = np.array([24, 20, 8])

# Layout (900x1600). Upgrade offsets are relative to the cost icon's top-left,
# Craft offsets relative to the row's output-bar icon top-left.
_MENU_ROI = (10, 769, 890, 1579)
_CLOSE_ROI = (430, 1450, 670, 1575)
_BUY_ALL_ROI = (560, 1380, 890, 1485)
_UPGRADE_ICON_X = (180, 640)          # cost area between the row icon and the Buy button
_UPGRADE_TEXT = (50, 10, 330, 50)      # "have/need" right of the icon (x2 capped by the next icon)
_UPGRADE_BUY = (640, -50, 850, 40)     # Buy button: absolute x range, y relative to the icon
_QTY_ROI = (20, 1380, 420, 1480)      # Craft page quantity buttons (1x / 5x)
_CRAFT_ICON_X = (490, 600)            # output bar icon column
_CRAFT_ORE_TEXT = (-360, -4, -228, 28)
_CRAFT_BAR_TEXT = (-360, 46, -228, 78)
_CRAFT_RATIO_TEXT = (-190, 46, -116, 78)
_CRAFT_BUTTON = (120, -20, 290, 90)

_GLYPH_MIN_H, _GLYPH_MAX_H, _GLYPH_MAX_W = 12, 28, 18
_GLYPH_CORE_FRACTION = 0.45  # brightness share above background that counts as ink
_GLYPH_MAX_DIST = 0.7        # correct glyphs measured <= 0.56; misreads ~1.0+
_MAX_BARS_PER_PRESS = 50   # 5x mode times craft-chance multipliers stays below this
_MAX_COLOR_DIST = 20.0     # real icons are within ~1 of their template; bar types >= 46 apart
_AFFORDABLE_MIN_RED = 125  # mean red of the Buy button: affordable ~165, not affordable ~80

_thread = None
_thread_started = False
_stop_evt = threading.Event()
_wake_evt = threading.Event()
_run_generation = 0
_lock_owned = False   # the cycle holds state.ingame_action_lock (taken by _try_run_with_lock)
_paused = False       # the lock is released while waiting workflows run

_WAITING_MAX_AGE_S = 30.0   # waiting entries are refreshed every WORKFLOWS_BUSY_RETRY_S
_YIELD_MAX_WAIT_S = 300.0   # stop waiting for workflows after this long
_RELOCK_MAX_WAIT_S = 600.0  # give up the cycle if the lock cannot be taken back

_bars = {}     # name -> (bgr template, mean foreground colour)
_glyphs = []   # [(char, zone feature vector)] - several variants per char
_ores = {}     # name -> bgr template of the small ore icon on the Sell page


class _Abort(Exception):
    """Ends a cycle early; the message is logged and shown as the status."""


# ============================================================
#  Logging, templates
# ============================================================

def _log(msg: str):
    bot_log(msg, getattr(s, "SMART_CRAFT_LOG_LEVEL", "FULL"))


def _icon_color(img_bgr):
    pixels = img_bgr.reshape(-1, 3).astype(int)
    fg = pixels[np.abs(pixels - _BOX_BG).sum(axis=1) > 30]
    return fg.mean(axis=0) if len(fg) else np.zeros(3)


def load_templates(img_dir=None) -> tuple[int, int]:
    """Load bar icons and digit glyphs. Returns (bar_count, glyph_count)."""
    global _bars, _glyphs
    base = os.path.join(str(img_dir or s.IMG_DIR), _TPL_DIR)
    bars, glyphs = {}, []
    for path in sorted(glob.glob(os.path.join(base, "bars", "*.png"))):
        img = read_image(path, cv2.IMREAD_COLOR)
        if img is not None:
            bars[os.path.splitext(os.path.basename(path))[0]] = (img, _icon_color(img))
    for path in sorted(glob.glob(os.path.join(base, "digits", "*.png"))):
        name = os.path.splitext(os.path.basename(path))[0].split("_")[0]
        img = read_image(path, cv2.IMREAD_GRAYSCALE)
        if img is not None and (name.isdigit() and len(name) == 1 or name == "slash"):
            glyphs.append(("/" if name == "slash" else name, glyph_features(img > 0)))
    ores = {}
    for path in sorted(glob.glob(os.path.join(base, "ores", "*.png"))):
        img = read_image(path, cv2.IMREAD_COLOR)
        if img is not None:
            ores[os.path.splitext(os.path.basename(path))[0]] = img
    _bars, _glyphs = bars, glyphs
    _ores.clear()
    _ores.update(ores)
    return len(bars), len({char for char, _v in glyphs})


def _tpl(name: str) -> str:
    return f"{_TPL_DIR}/{name}"


# ============================================================
#  Number reading
# ============================================================

def segment_glyphs(gray_roi):
    """Return glyph masks left to right, or None if the ROI holds a blob that
    is not glyph-sized (e.g. '.' or a suffix). Components touching the ROI
    edge (box borders, icons) are ignored."""
    if gray_roi is None or gray_roi.size == 0:
        return []
    low, high = float(np.percentile(gray_roi, 5)), float(gray_roi.max())
    if high - low < 40:
        return []
    # Relative threshold keeps only the glyph core; the grey drop shadow is
    # excluded consistently (Otsu included it depending on the ROI content).
    binary = gray_roi > low + _GLYPH_CORE_FRACTION * (high - low)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(binary.astype(np.uint8), 8)
    h_roi, w_roi = binary.shape
    parts = []
    for i in range(1, count):
        x, y, w, h, area = stats[i]
        if x == 0 or y == 0 or x + w >= w_roi or y + h >= h_roi:
            continue
        if not (_GLYPH_MIN_H <= h <= _GLYPH_MAX_H) or w > _GLYPH_MAX_W:
            if area >= 4:
                return None
            continue
        parts.append([x, y, x + w, y + h, [i]])
    parts.sort(key=lambda p: p[0])
    merged = []
    for part in parts:  # glyphs made of several components overlap in x
        if merged and part[0] < merged[-1][2]:
            last = merged[-1]
            last[:4] = [min(last[0], part[0]), min(last[1], part[1]),
                        max(last[2], part[2]), max(last[3], part[3])]
            last[4] += part[4]
        else:
            merged.append(part)
    masks = []
    for x1, y1, x2, y2, ids in merged:
        masks.append(np.isin(labels[y1:y2, x1:x2], ids))
    return masks


def glyph_features(mask):
    """Ink share in a 3x5 grid plus aspect ratio; tolerant of 1 px shifts."""
    h, w = mask.shape
    ink = mask.astype(float)
    zones = [ink[round(r * h / 5):round((r + 1) * h / 5), round(c * w / 3):round((c + 1) * w / 3)].mean()
             for r in range(5) for c in range(3)]
    return np.array(zones + [w / h])


def classify_glyph(mask):
    """Return the character, or None unless the nearest reference is clearly
    closer than the nearest reference of any other character."""
    if not _glyphs:
        return None
    vec = glyph_features(mask)
    best = {}
    for char, ref in _glyphs:
        dist = float(np.linalg.norm(vec - ref))
        best[char] = min(dist, best.get(char, dist))
    ranked = sorted(best.items(), key=lambda item: item[1])
    if len(ranked) == 1:
        return ranked[0][0]
    (char, dist), (_other, other_dist) = ranked[0], ranked[1]
    max_ratio = float(getattr(s, "SMART_CRAFT_GLYPH_MAX_RATIO", 0.75))
    return char if dist <= _GLYPH_MAX_DIST and dist <= max_ratio * other_dist else None


def read_text(gray, roi):
    """Read digits and '/' inside roi. Returns the string, or None if any
    glyph is unknown or nothing was found."""
    x1, y1, x2, y2 = (max(0, int(v)) for v in roi)
    masks = segment_glyphs(gray[y1:y2, x1:x2])
    if not masks:
        return None
    chars = [classify_glyph(mask) for mask in masks]
    return None if None in chars else "".join(chars)


def parse_int(text):
    return int(text) if text and text.isdigit() else None


def parse_cost(text):
    """'12/35' -> (12, 35); anything else -> None."""
    if not text or text.count("/") != 1:
        return None
    have, need = text.split("/")
    if not have.isdigit() or not need.isdigit() or int(need) <= 0:
        return None
    return int(have), int(need)


# ============================================================
#  Icon detection
# ============================================================

def find_all(gray, template, roi, threshold: float):
    """Top-left (x, y) of every grayscale match of template inside roi."""
    tpl = read_image(os.path.join(str(s.IMG_DIR), template), cv2.IMREAD_GRAYSCALE)
    x1, y1, x2, y2 = roi
    area = gray[y1:y2, x1:x2] if gray is not None else None
    if tpl is None or area is None or area.shape[0] < tpl.shape[0] or area.shape[1] < tpl.shape[1]:
        return []
    res = cv2.matchTemplate(area, tpl, cv2.TM_CCOEFF_NORMED)
    th, tw = tpl.shape[:2]
    found = []
    for y, x in sorted(zip(*np.where(res >= threshold)), key=lambda p: -res[p]):
        if all(abs(x + x1 - fx) >= tw // 2 or abs(y + y1 - fy) >= th // 2 for fx, fy in found):
            found.append((int(x) + x1, int(y) + y1))
    return found


def find_bar_icons(img_bgr, roi, threshold: float):
    """Return [(bar_name, x, y, score)] for icons inside roi (top-left coords)."""
    if not _bars or img_bgr is None:
        return []
    x1, y1, x2, y2 = roi
    area = img_bgr[y1:y2, x1:x2]
    hits = []
    for name, (tpl, _color) in _bars.items():
        th, tw = tpl.shape[:2]
        if area.shape[0] < th or area.shape[1] < tw:
            continue
        res = cv2.matchTemplate(area, tpl, cv2.TM_CCOEFF_NORMED)
        ys, xs = np.where(res >= threshold)
        hits += [(float(res[y, x]), int(x) + x1, int(y) + y1, tw, th) for y, x in zip(ys, xs)]
    hits.sort(reverse=True)
    icons = []
    for score, x, y, tw, th in hits:  # one icon per position (NMS)
        if any(abs(x - ix) < tw // 2 and abs(y - iy) < th // 2 for _n, ix, iy, _s in icons):
            continue
        color = _icon_color(img_bgr[y:y + th, x:x + tw])
        dist, name = min((float(np.linalg.norm(_bars[n][1] - color)), n) for n in _bars)
        if dist > _MAX_COLOR_DIST:  # bar type without template, dimmed/locked icon, other sprite
            continue
        icons.append((name, x, y, score))
    icons.sort(key=lambda icon: (icon[2], icon[1]))
    return icons


# ============================================================
#  Screen access and taps
# ============================================================

def _delay():
    time.sleep(float(getattr(s, "SMART_CRAFT_UI_DELAY_S", 0.8)))


def _check_cancel(generation):
    if _stop_evt.is_set() or generation != _run_generation:
        raise _Abort("cancelled")


def _fresh():
    img, _w, _h = get_screen(_SLOT, max_age=0.0, force=True, color=True)
    if img is None:
        raise _Abort("screenshot failed")
    return img, cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def _button(gray, template, roi):
    return find_template(gray, template, roi=roi,
                         threshold=float(getattr(s, "SMART_CRAFT_BUTTON_THRESHOLD", 0.8)))


def _tap(x, y, what):
    if not tap(int(x), int(y), jitter=int(getattr(s, "SMART_CRAFT_TAP_JITTER_PX", 3))):
        raise _Abort(f"tap failed: {what}")


def _menu_open(gray):
    return _button(gray, "workflows/menu/this_prestige/craft_tpl.png", _MENU_ROI)["found"]


def _open_page(tile, ready, name, generation, section="this_prestige"):
    """Open the main menu if needed, tap a page tile, wait until ready(img, gray)."""
    _check_cancel(generation)
    img, gray = _fresh()
    if not _menu_open(gray):
        button = _button(gray, "workflows/menu/main_menu_tpl.png", _MENU_ROI)
        if not button["found"]:
            raise _Abort(f"main menu button not found (score={button['score']:.3f})")
        _tap(button["cx"], button["cy"], "main menu")
        _delay()
        img, gray = _fresh()
        if not _menu_open(gray):
            raise _Abort("main menu did not open")
    match = _button(gray, f"workflows/menu/{section}/{tile}.png", (10, 100, 890, 1579))
    if not match["found"]:
        raise _Abort(f"{name} tile not found (score={match['score']:.3f})")
    _tap(match["cx"], match["cy"], name)
    _delay()
    img, gray = _fresh()
    if not ready(img, gray):
        raise _Abort(f"{name} page did not open")
    return img, gray


def _upgrades_ready(_img, gray):
    return _button(gray, _tpl("buy_all.png"), _BUY_ALL_ROI)["found"]


def _craft_ready(img, _gray):
    return bool(_craft_icons(img))


def _close_menu():
    """Best effort: tap the bottom-bar X if a menu page is open."""
    try:
        _img, gray = _fresh()
        match = _button(gray, _tpl("close_menu.png"), _CLOSE_ROI)
        if match["found"]:
            _tap(match["cx"], match["cy"], "close menu")
            _delay()
    except _Abort as exc:
        _log(f"[SMART_CRAFT] closing menu failed: {exc}")


def _waiting_workflows():
    now = time.time()
    waiting = dict(getattr(state, "workflows_waiting", {}) or {})
    return sorted(slug for slug, ts in waiting.items() if now - ts <= _WAITING_MAX_AGE_S)


def _wake_workflows():
    try:
        from features import workflows
        workflows.retry_waiting_now()
    except Exception as exc:  # they still retry on their own busy interval
        _log(f"[SMART_CRAFT] could not wake waiting workflows: {exc}")


def _yield_point(generation) -> bool:
    """Pause for auto workflows that wait for the action lock (obelisk fight,
    bombs, ...): close the menu, release the lock until they have run, then
    take it back. The cycle keeps its scan results and continues where it
    stopped. Returns True after a pause; the caller reopens its page."""
    global _lock_owned, _paused
    if not _lock_owned or not getattr(s, "SMART_CRAFT_YIELD", True):
        return False
    if getattr(state, "automation_paused", False):
        return False  # paused workflows will not run; finish this cycle instead
    waiting = _waiting_workflows()
    if not waiting:
        return False
    _check_cancel(generation)
    _close_menu()
    _img, gray = _fresh()
    if _button(gray, _tpl("close_menu.png"), _CLOSE_ROI)["found"]:
        _log("[SMART_CRAFT] cannot pause: the menu is still open")
        return False
    started = time.time()
    _log(f"[SMART_CRAFT] pausing for waiting workflow(s): {', '.join(waiting)}")
    _paused = True
    _lock_owned = False
    state.ingame_action_lock.release()
    _wake_workflows()
    while True:
        time.sleep(0.5)
        _check_cancel(generation)
        elapsed = time.time() - started
        if (_waiting_workflows() and elapsed < _YIELD_MAX_WAIT_S
                and not getattr(state, "automation_paused", False)):
            continue
        if state.ingame_action_lock.acquire(blocking=False):
            break
        if elapsed >= _RELOCK_MAX_WAIT_S:
            raise _Abort("action lock not available after pausing")
    _lock_owned = True
    _paused = False
    _log(f"[SMART_CRAFT] resuming after {time.time() - started:.0f}s")
    return True


# ============================================================
#  Upgrades: what is needed
# ============================================================

def read_upgrade_rows(img, gray, with_buttons=False):
    """Return [(bar, have, need, y)] for bar-cost rows with a Buy button on this
    frame; with_buttons appends (buy_x, buy_y, affordable) to each row."""
    threshold = float(getattr(s, "SMART_CRAFT_ICON_THRESHOLD", 0.8))
    lx1, ly1, lx2, ly2 = _list_roi()
    cost_area = (_UPGRADE_ICON_X[0], ly1, _UPGRADE_ICON_X[1], ly2)
    bars = find_bar_icons(img, cost_area, threshold)
    # Every cost icon in the cost area (bars and gold) - a row can have two costs.
    coins = find_all(gray, _tpl("coin.png"), cost_area, 0.9)
    icons = [(x, y) for _b, x, y, _s in bars] + coins
    skip_gold = not getattr(s, "SMART_CRAFT_BUY_GOLD_UPGRADES", False)
    rows = []
    for bar, x, y, _score in bars:
        if skip_gold and any(abs(cy - y) < 25 for _cx, cy in coins):
            _log(f"[SMART_CRAFT] {bar} row at y={y} also costs gold -> skipped (SMART_CRAFT_BUY_GOLD_UPGRADES off)")
            continue
        tx1, ty1, tx2, ty2 = _UPGRADE_TEXT
        # The number ends where the next cost icon of the same row starts.
        text_end = min([ix - 4 for ix, iy in icons if abs(iy - y) < 25 and ix > x + 10] + [x + tx2])
        text = read_text(gray, (x + tx1, y + ty1, text_end, y + ty2))
        cost = parse_cost(text)
        bx1, by1, bx2, by2 = _UPGRADE_BUY
        buy = _button(gray, _tpl("buy.png"), (bx1, max(0, y + by1), bx2, y + by2))
        if cost is None:
            _log(f"[SMART_CRAFT] {bar} cost at y={y} unreadable ('{text}') -> skipped")
        elif not buy["found"]:
            _log(f"[SMART_CRAFT] {bar} {text} at y={y}: no Buy button (maxed/locked) -> skipped")
        elif with_buttons:
            # Affordable Buy buttons are bright red (R ~165), others dark (R ~80).
            patch = img[buy["y"]:buy["y"] + buy["h"], buy["x"]:buy["x"] + buy["w"]]
            affordable = float(patch[:, :, 2].mean()) > _AFFORDABLE_MIN_RED
            rows.append((bar, cost[0], cost[1], y, buy["cx"], buy["cy"], affordable))
        else:
            rows.append((bar, cost[0], cost[1], y))
    return rows


def read_gold_rows(img, gray):
    """[(y, buy_x, buy_y, affordable, cost_crop)] for gold-cost rows (a coin
    icon and no bar icon in the row) that have a Buy button. The gold amount
    is not parsed: affordability comes from the Buy button colour, and a
    purchase is verified by the cost text crop changing."""
    _lx1, ly1, _lx2, ly2 = _list_roi()
    cost_area = (_UPGRADE_ICON_X[0], ly1, _UPGRADE_ICON_X[1], ly2)
    bars = find_bar_icons(img, cost_area, float(getattr(s, "SMART_CRAFT_ICON_THRESHOLD", 0.8)))
    rows = []
    for x, y in find_all(gray, _tpl("coin.png"), cost_area, 0.9):
        if any(abs(by - y) < 25 for _b, _bx, by, _s in bars):
            continue  # a bar row with a second (gold) cost is handled by read_upgrade_rows
        bx1, by1, bx2, by2 = _UPGRADE_BUY
        buy = _button(gray, _tpl("buy.png"), (bx1, max(0, y + by1), bx2, y + by2))
        if not buy["found"]:
            continue
        patch = img[buy["y"]:buy["y"] + buy["h"], buy["x"]:buy["x"] + buy["w"]]
        tx1, ty1, tx2, ty2 = _UPGRADE_TEXT
        crop = gray[max(0, y + ty1):y + ty2, x + tx1:x + tx2].astype(np.int16)
        rows.append((y, buy["cx"], buy["cy"], float(patch[:, :, 2].mean()) > _AFFORDABLE_MIN_RED, crop))
    return rows


def _gold_row_unchanged(before, after):
    """True if `after` still shows `before`'s row affordable with the same cost text."""
    y, _bx, _by, _aff, crop = before
    for ay, _abx, _aby, affordable, acrop in after:
        if abs(ay - y) < 5 and affordable and acrop.shape == crop.shape:
            return float(np.abs(acrop - crop).mean()) < 6.0
    return False


def _buy_gold_rows(img, gray, generation, bought, limit):
    """Buy affordable gold-cost rows on this page; returns (img, gray, bought, stop)."""
    while bought < limit:
        _check_cancel(generation)
        targets = [r for r in read_gold_rows(img, gray) if r[3]]
        if not targets:
            return img, gray, bought, False
        row = targets[0]
        _tap(row[1], row[2], f"Buy (gold) at y={row[0]}")
        _delay()
        img, gray = _fresh()
        if _gold_row_unchanged(row, read_gold_rows(img, gray)):
            _log(f"[SMART_CRAFT] gold Buy at y={row[0]} had no visible effect -> stop buying")
            return img, gray, bought, True
        bought += 1
        state.smart_craft_upgrades_bought += 1
        _log(f"[SMART_CRAFT] bought gold upgrade at y={row[0]}")
    return img, gray, bought, False


def _list_roi():
    return tuple(getattr(s, "SMART_CRAFT_LIST_ROI", (40, 170, 860, 1395)))


def list_shift(before, after, content_up=True):
    """Pixels the list content moved up (negative: down) between two frames,
    or None. The reference band is taken from the side the content moves
    towards, so shifts of almost a full list height stay measurable."""
    x1, y1, x2, y2 = _list_roi()
    band_h = 120
    tops = range(y2 - band_h - 10, y1, -40) if content_up else range(y1 + 10, y2 - band_h, 40)
    top = next((t for t in tops if before[t:t + band_h, x1:x2].std() > 15), None)
    if top is None:
        return None
    res = cv2.matchTemplate(after[y1:y2, x1:x2], before[top:top + band_h, x1:x2], cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(res)
    if score < 0.9:
        return None
    # Repetitive rows (e.g. "Unlock At Level N") match in several places.
    others = res.copy()
    others[max(0, loc[1] - band_h // 2):loc[1] + band_h // 2, :] = -1
    if others.max() > score - 0.05:
        return None
    return top - (loc[1] + y1)


def _scroll_list(direction):
    """direction: finger 'up' reveals rows below, 'down' rows above."""
    if not scroll(direction, area=_list_roi(), distance=int(getattr(s, "SMART_CRAFT_SCROLL_PX", 400)),
                  duration_ms=int(getattr(s, "SMART_CRAFT_SCROLL_MS", 1000))):
        raise _Abort("scroll failed")
    _delay()


def _scroll_to_top(img, gray, generation):
    for _ in range(int(getattr(s, "SMART_CRAFT_MAX_SCROLL_PAGES", 6)) + 2):
        _check_cancel(generation)
        _scroll_list("down")
        new_img, new_gray = _fresh()
        shift = list_shift(gray, new_gray, content_up=False)
        img, gray = new_img, new_gray
        if shift is not None and abs(shift) < 15:
            return img, gray
    raise _Abort("list top not reached")


def list_pages(img, gray, generation):
    """Yield (img, gray, offset) from the top of the list to its end; offset is
    how far the content has moved up since the first page."""
    img, gray = _scroll_to_top(img, gray, generation)
    pages, offset = int(getattr(s, "SMART_CRAFT_MAX_SCROLL_PAGES", 6)), 0
    for page in range(pages + 1):
        _check_cancel(generation)
        yield img, gray, offset
        if page == pages:
            _log(f"[SMART_CRAFT] scroll limit reached ({pages} pages)")
            return
        _scroll_list("up")
        new_img, new_gray = _fresh()
        shift = list_shift(gray, new_gray, content_up=True)
        img, gray = new_img, new_gray
        if shift is None:
            _log("[SMART_CRAFT] list position unknown after scrolling -> stopping here")
            return
        if shift < 15:
            return
        offset += shift


def scan_upgrades(img, gray, generation):
    """Read the Upgrades list down to the locked section; return deduplicated rows."""
    found = []
    for img, gray, offset in list_pages(img, gray, generation):
        second = read_upgrade_rows(*_fresh())  # numbers may animate: require two equal reads
        for bar, have, need, y in read_upgrade_rows(img, gray):
            if not any(r[:3] == (bar, have, need) and abs(r[3] - y) < 5 for r in second):
                _log(f"[SMART_CRAFT] {bar} {have}/{need} at y={y} not stable across frames -> skipped")
                continue
            absolute = y + offset
            if not any(b == bar and abs(a - absolute) < 25 for b, _h, _n, a in found):
                found.append((bar, have, need, absolute))
        if not _button(gray, _tpl("buy.png"), _list_roi())["found"]:
            break  # only locked ("Unlock At Level") rows from here on
    return found


# ============================================================
#  Workshop: permanent upgrades (Permanent -> Workshop), up to three bar costs
# ============================================================
# Each cost is a bar icon with "have/" on one line and "need" below it.
# Policy: Upgrades are bought first; Workshop rows are bought when affordable.

_WORKSHOP_HEADER_ROI = (100, 90, 850, 180)
_WORKSHOP_ICON_X = (120, 700)
_WORKSHOP_HAVE = (54, -6, 140, 24)    # relative to the cost icon's top-left
_WORKSHOP_NEED = (54, 26, 140, 60)
_WORKSHOP_BUY = (640, -50, 880, 40)   # absolute x, y relative to the icons


def _workshop_ready(_img, gray):
    return _button(gray, _tpl("workshop_header.png"), _WORKSHOP_HEADER_ROI)["found"]


def read_workshop_rows(img, gray):
    """[{costs: [(bar, have, need)], y, buy: (x, y), affordable}] for Workshop
    rows with a Buy button. A row with any unreadable cost is skipped."""
    threshold = float(getattr(s, "SMART_CRAFT_ICON_THRESHOLD", 0.8))
    _lx1, ly1, _lx2, ly2 = _list_roi()
    icons = find_bar_icons(img, (_WORKSHOP_ICON_X[0], ly1, _WORKSHOP_ICON_X[1], ly2), threshold)
    groups = []
    for bar, x, y, _score in sorted(icons, key=lambda i: i[2]):
        if groups and abs(groups[-1][0][2] - y) < 15:
            groups[-1].append((bar, x, y))
        else:
            groups.append([(bar, x, y)])
    coins = []
    if not getattr(s, "SMART_CRAFT_BUY_GOLD_UPGRADES", False):
        coins = find_all(gray, _tpl("coin.png"), (_WORKSHOP_ICON_X[0], ly1, _WORKSHOP_ICON_X[1], ly2), 0.9)
    rows = []
    for group in groups:
        top = min(y for _b, _x, y in group)
        if any(abs(cy - top) < 25 for _cx, cy in coins):
            _log(f"[SMART_CRAFT] Workshop row at y={top} also costs gold -> skipped (SMART_CRAFT_BUY_GOLD_UPGRADES off)")
            continue
        costs = []
        for bar, x, y in sorted(group, key=lambda i: i[1]):
            hx1, hy1, hx2, hy2 = _WORKSHOP_HAVE
            nx1, ny1, nx2, ny2 = _WORKSHOP_NEED
            have = parse_int((read_text(gray, (x + hx1, y + hy1, x + hx2, y + hy2)) or "").rstrip("/"))
            need = parse_int(read_text(gray, (x + nx1, y + ny1, x + nx2, y + ny2)))
            if have is None or need is None or need <= 0:
                costs = None
                _log(f"[SMART_CRAFT] Workshop {bar} cost at y={y} unreadable -> row skipped")
                break
            costs.append((bar, have, need))
        if not costs:
            continue
        bx1, by1, bx2, by2 = _WORKSHOP_BUY
        buy = _button(gray, _tpl("buy.png"), (bx1, max(0, top + by1), bx2, top + by2))
        if not buy["found"]:
            continue
        patch = img[buy["y"]:buy["y"] + buy["h"], buy["x"]:buy["x"] + buy["w"]]
        rows.append({"costs": costs, "y": top, "buy": (buy["cx"], buy["cy"]),
                     "affordable": float(patch[:, :, 2].mean()) > _AFFORDABLE_MIN_RED})
    return rows


def scan_workshop(img, gray, generation):
    """Read the Workshop list down to the locked section. Returns cost rows in
    the Upgrades format [(bar, have, need, y)] so needs can be merged."""
    found = []
    for img, gray, offset in list_pages(img, gray, generation):
        second = read_workshop_rows(*_fresh())  # numbers may animate: require two equal reads
        for row in read_workshop_rows(img, gray):
            if not any(r["costs"] == row["costs"] and abs(r["y"] - row["y"]) < 5 for r in second):
                _log(f"[SMART_CRAFT] Workshop row at y={row['y']} not stable across frames -> skipped")
                continue
            absolute = row["y"] + offset
            for bar, have, need in row["costs"]:
                if not any(b == bar and abs(a - absolute) < 25 for b, _h, _n, a in found):
                    found.append((bar, have, need, absolute))
        if not _button(gray, _tpl("buy.png"), _list_roi())["found"]:
            break  # only locked ("Unlock At Obelisk Level") rows from here on
    return found


def buy_workshop(generation):
    """Tap Buy on Workshop rows whose every cost is met; a purchase counts only
    when a fresh frame no longer shows the same affordable row."""
    img, gray = _open_page("workshop_tpl", _workshop_ready, "Workshop", generation, section="permanent")
    limit = int(getattr(s, "SMART_CRAFT_MAX_BUYS", 20))
    bought = 0
    for img, gray, _offset in list_pages(img, gray, generation):
        while bought < limit:
            _check_cancel(generation)
            targets = [r for r in read_workshop_rows(img, gray)
                       if r["affordable"] and all(h >= n for _b, h, n in r["costs"])]
            if not targets:
                break
            row = targets[0]
            summary = ", ".join(f"{b} {h}/{n}" for b, h, n in row["costs"])
            _tap(*row["buy"], f"Workshop Buy ({summary})")
            _delay()
            img, gray = _fresh()
            if any(r["costs"] == row["costs"] and abs(r["y"] - row["y"]) < 5 and r["affordable"]
                   for r in read_workshop_rows(img, gray)):
                _log(f"[SMART_CRAFT] Workshop Buy ({summary}) had no visible effect -> stop buying")
                return bought
            bought += 1
            state.smart_craft_upgrades_bought += 1
            _log(f"[SMART_CRAFT] bought Workshop upgrade ({summary})")
        if bought >= limit or not _button(gray, _tpl("buy.png"), _list_roi())["found"]:
            break
    return bought


def _craft_target_mode():
    mode = str(getattr(s, "SMART_CRAFT_CRAFT_TARGET", "next")).lower()
    return mode if mode in ("next", "all") else "next"


def compute_shortfalls(rows, mode=None):
    """rows: [(bar, have, need, y)] -> {bar: (have, target)} for bars still missing.
    mode "next" (SMART_CRAFT_CRAFT_TARGET default): target = the cheapest row's
    need per bar, i.e. enough for the next purchase; the next round crafts for
    the following one. Mode "all": target = sum of all rows' needs (crafts every
    bar the visible upgrades will ever need, usually all ore)."""
    mode = mode or _craft_target_mode()
    result = {}
    for bar in sorted({r[0] for r in rows}):
        mine = [r for r in rows if r[0] == bar]
        have = max(r[1] for r in mine)
        needs = [r[2] for r in mine]
        target = min(needs) if mode == "next" else sum(needs)
        if target > have:
            result[bar] = (have, target)
    return result


# ============================================================
#  Craft: produce the missing bars
# ============================================================

def _craft_icons(img):
    roi = (_CRAFT_ICON_X[0], 150, _CRAFT_ICON_X[1], 1400)
    return find_bar_icons(img, roi, float(getattr(s, "SMART_CRAFT_ICON_THRESHOLD", 0.8)))


def read_craft_row(img, gray, bar):
    """Return dict(ore, bars, ratio, button=(x, y)) for bar's Craft row, or None
    if the row is not (fully) visible or not readable."""
    _x1, y1, _x2, y2 = _list_roi()
    for name, x, y, _score in _craft_icons(img):
        if name != bar or y + _CRAFT_ORE_TEXT[1] < y1 or y + _CRAFT_BUTTON[3] > y2 + 60:
            continue

        def rel(box):
            return (x + box[0], y + box[1], x + box[2], y + box[3])

        ore = parse_int(read_text(gray, rel(_CRAFT_ORE_TEXT)))
        bars = parse_int(read_text(gray, rel(_CRAFT_BAR_TEXT)))
        ratio = parse_int(read_text(gray, rel(_CRAFT_RATIO_TEXT)))
        button = _button(gray, _tpl("craft.png"), rel(_CRAFT_BUTTON))
        if None in (ore, bars, ratio) or not ratio or not button["found"]:
            _log(f"[SMART_CRAFT] {bar} row unreadable (ore={ore} bars={bars} "
                 f"ratio={ratio} button={button['score']:.3f})")
            return None
        return {"ore": ore, "bars": bars, "ratio": ratio, "button": (button["cx"], button["cy"])}
    return None


def _read_stable_row(bar):
    """Read bar's row on two frames; bars/ratio/button must agree (counts
    animate briefly after crafting). Ore is taken from the second frame
    because mining changes it continuously."""
    for _attempt in range(3):
        first = read_craft_row(*_fresh(), bar)
        time.sleep(0.4)
        second = read_craft_row(*_fresh(), bar)
        if first and second and all(first[k] == second[k] for k in ("bars", "ratio", "button")):
            return second
    return None


def _locate_craft_row(bar, generation):
    """Bring bar's Craft row into view (scrolling if needed) and read it."""
    img, gray = _fresh()
    if read_craft_row(img, gray, bar) is None:
        for img, gray, _offset in list_pages(img, gray, generation):
            if read_craft_row(img, gray, bar) is not None:
                break
        else:
            return None
    return _read_stable_row(bar)


def select_quantity(mode="5x"):
    """Activate the Craft page quantity button `mode` ("1x" or "5x"). The
    active button is green, the others red. Returns True if `mode` is active
    afterwards."""
    for attempt in range(2):
        img, gray = _fresh()
        match = _button(gray, _tpl(f"qty_{mode}.png"), _QTY_ROI)
        if not match["found"]:
            _log(f"[SMART_CRAFT] {mode} button not found -> crafting in the current mode")
            return False
        patch = img[match["y"]:match["y"] + match["h"], match["x"]:match["x"] + match["w"]].reshape(-1, 3)
        b, g, r = patch.mean(axis=0)
        if g > r + 30:  # green = active
            return True
        if attempt == 0:
            _tap(match["cx"], match["cy"], f"quantity {mode}")
            _delay()
    _log(f"[SMART_CRAFT] could not switch to {mode} -> crafting in the current mode")
    return False


def select_quantity_5x():
    return select_quantity("5x")


def _one_x_reaches(row, target, per_press):
    """In "next" mode, switch the Craft page to 1x for leftover ore only if
    those presses can still reach `target` (each switch costs two taps and
    several screenshots). Mode "all" always uses the leftover ore."""
    if _craft_target_mode() == "all":
        return True
    ore_per_1x = row["ratio"] / 5           # the shown ratio is for 5x
    presses = int(row["ore"] // ore_per_1x) if ore_per_1x > 0 else 0
    bars_per_1x = (per_press / 5) if per_press else 1.0
    return row["bars"] + presses * bars_per_1x >= target


def craft_bar(bar, have, target, generation, quantity=None):
    """Press Craft until `bars >= target`; returns verified bars crafted.
    `have` is the count read on Upgrades and must match the Craft row.
    `quantity` is the active mode ("5x" or None); in 5x mode, ore for less
    than one 5x press is used up in 1x mode, after which 5x is restored
    (each cycle also selects 5x again at its start)."""
    switched_to_1x = False
    row = _locate_craft_row(bar, generation)
    if row is None:
        _log(f"[SMART_CRAFT] {bar}: Craft row not found/readable -> skipped")
        return 0
    if row["bars"] != have:
        _log(f"[SMART_CRAFT] {bar}: Craft shows {row['bars']} bars but Upgrades showed {have} "
             "-> possible misread, skipped")
        return 0
    max_presses = int(getattr(s, "SMART_CRAFT_MAX_PRESSES_PER_BAR", 50))
    max_batch = int(getattr(s, "SMART_CRAFT_MAX_BATCH", 10))
    crafted = presses = stalls = 0
    per_press = None
    while row["bars"] < target and presses < max_presses:
        _check_cancel(generation)
        if row["ore"] < row["ratio"]:
            if quantity == "5x" and not switched_to_1x and not _one_x_reaches(row, target, per_press):
                _log(f"[SMART_CRAFT] {bar}: ore {row['ore']} < {row['ratio']} for 5x and 1x presses "
                     f"cannot reach {target} -> keeping the ore")
            elif quantity == "5x" and not switched_to_1x:
                # The rest of the ore may still cover single crafts.
                _log(f"[SMART_CRAFT] {bar}: ore {row['ore']} < {row['ratio']} for 5x -> trying 1x")
                switched_to_1x = True
                if not select_quantity("1x"):
                    break
                new = _read_stable_row(bar)
                if new is None or new["ratio"] >= row["ratio"] or new["bars"] != row["bars"]:
                    _log(f"[SMART_CRAFT] {bar}: 1x mode not confirmed on the Craft row -> stop")
                    break
                row, per_press = new, None
                continue
            _log(f"[SMART_CRAFT] {bar}: not enough ore ({row['ore']} < {row['ratio']})")
            per_bar = row["ratio"] / 5 if quantity == "5x" and not switched_to_1x else row["ratio"]
            _ore_missing[bar] = max(1, math.ceil((target - row["bars"]) * per_bar - row["ore"]))
            break
        remaining = target - row["bars"]
        batch = 1 if per_press is None else math.ceil(remaining / per_press)
        affordable = row["ore"] // row["ratio"]  # presses the remaining ore covers
        batch = max(1, min(batch, max_batch, max_presses - presses, affordable))
        for _ in range(batch):
            _tap(*row["button"], f"Craft {bar}")
            time.sleep(0.15)
        presses += batch
        _delay()
        new = _read_stable_row(bar)
        if new is None:
            _log(f"[SMART_CRAFT] {bar}: row unreadable after crafting -> stop")
            break
        delta = new["bars"] - row["bars"]
        if delta <= 0:
            stalls += 1  # a single press can be dropped by the game; retry once
            if stalls >= 2:
                _log(f"[SMART_CRAFT] {bar}: no progress after {batch} press(es), twice -> stop")
                break
            _log(f"[SMART_CRAFT] {bar}: no progress after {batch} press(es) -> retrying")
            row = new
            continue
        stalls = 0
        if delta > batch * _MAX_BARS_PER_PRESS:
            _log(f"[SMART_CRAFT] {bar}: implausible change {row['bars']} -> {new['bars']} "
                 f"after {batch} press(es) -> possible misread, stop")
            break
        crafted += delta
        state.smart_craft_bars_crafted += delta
        per_press = delta / batch
        row = new
    _log(f"[SMART_CRAFT] {bar}: crafted {crafted} (now {row['bars']}/{target}, "
         f"ore {row['ore']}, {presses} press(es))")
    if switched_to_1x:
        select_quantity("5x")
    return crafted


# ============================================================
#  Buy: purchase the bar-cost upgrades that are now affordable
# ============================================================

def buy_upgrades(generation):
    """Tap Buy on affordable bar-cost upgrades; each purchase is counted only
    after a fresh frame shows the row changed. With SMART_CRAFT_BUY_GOLD_UPGRADES
    gold-cost rows are bought after the bar rows of each page; without it no
    row with a gold cost is touched (filtered in read_upgrade_rows)."""
    img, gray = _open_page("upgrades_tpl", _upgrades_ready, "Upgrades", generation)
    limit = int(getattr(s, "SMART_CRAFT_MAX_BUYS", 20))
    bought = 0
    for img, gray, _offset in list_pages(img, gray, generation):
        while bought < limit:
            _check_cancel(generation)
            targets = [r for r in read_upgrade_rows(img, gray, with_buttons=True)
                       if r[6] and r[1] >= r[2]]
            if not targets:
                break
            bar, have, need, y, bx, by, _affordable = targets[0]
            _tap(bx, by, f"Buy ({bar} {have}/{need})")
            _delay()
            img, gray = _fresh()
            same = [r for r in read_upgrade_rows(img, gray, with_buttons=True)
                    if abs(r[3] - y) < 5 and r[:3] == (bar, have, need) and r[6]]
            if same:
                _log(f"[SMART_CRAFT] Buy ({bar} {have}/{need}) had no visible effect -> stop buying")
                return bought
            bought += 1
            state.smart_craft_upgrades_bought += 1
            _log(f"[SMART_CRAFT] bought upgrade at y={y} ({bar} {have}/{need})")
        if getattr(s, "SMART_CRAFT_BUY_GOLD_UPGRADES", False) and bought < limit:
            img, gray, bought, stop = _buy_gold_rows(img, gray, generation, bought, limit)
            if stop:
                return bought
        if bought >= limit:
            _log(f"[SMART_CRAFT] purchase limit reached ({limit})")
            break
        if not _button(gray, _tpl("buy.png"), _list_roi())["found"]:
            break  # locked section
    return bought


# ============================================================
#  Floor trips: mine missing ore on a lower floor, then return
# ============================================================

# World 1 floors 1-20 drop ore pairs (Floor Select panel; game wiki "Floors").
# Per the wiki, the first floor of a pair splits 80/20 (older/newer ore) and
# the second 50/50. Only bar types with templates matter here.
_FLOOR_PAIRS = {1: ("tin", "copper"), 3: ("copper", "mithril"), 5: ("mithril", "adamant"),
                7: ("adamant", "runite"), 9: ("runite", "citrine"), 11: ("citrine", "amethyst"),
                13: ("amethyst", "topaz"), 15: ("topaz", "obsidian"), 17: ("obsidian", "demonite"),
                19: ("vr_tin", "vr_amethyst")}
FLOOR_ORE_SHARES = {}
for _first, (_old, _new) in _FLOOR_PAIRS.items():
    FLOOR_ORE_SHARES[_first] = {_old: 0.8, _new: 0.2}
    FLOOR_ORE_SHARES[_first + 1] = {_old: 0.5, _new: 0.5}
# Floor 19 starts a new zone with two new ores; the 80/20 rule is unverified there.
FLOOR_ORE_SHARES[19] = {"vr_tin": 0.5, "vr_amethyst": 0.5}

# Floor Select panel (World 1 fits without scrolling: 21 rows x 2 floors).
_FLOOR_BUTTON_ROI = (760, 600, 900, 720)   # right-side HUD button that opens the panel
_FLOOR_TITLE_ROI = (60, 90, 400, 180)
_FLOOR_CLOSE_ROI = (330, 1200, 570, 1380)
_FLOOR_WORLD1 = (583, 131)                  # World "1" tab, green when selected
_FLOOR_COLUMNS = (342, 692)                 # odd floors left, even floors right
_FLOOR_ROW0_Y, _FLOOR_ROW_PITCH, _FLOOR_ROWS = 184.0, 51.65, 21
_AUTO_OFFSET_Y = 209                        # floor Auto toggle below the panel button

_trip = None   # {"origin", "floor", "until", "auto_was_on"} while on a lower floor
_ore_missing = {}   # bar -> ore still needed, from the last craft round
# A trip survives a bot restart so the bot still returns to the origin floor.
_TRIP_FILE = Path(__file__).resolve().parent.parent / "smart_craft_trip.json"


def _set_trip(trip):
    global _trip
    _trip = trip
    state.smart_craft_trip = dict(trip) if trip else None
    try:
        if trip:
            atomic_write_json(_TRIP_FILE, trip)
        else:
            _TRIP_FILE.unlink(missing_ok=True)
    except OSError as exc:
        _log(f"[SMART_CRAFT] saving the floor trip failed: {exc}")


def _load_trip():
    try:
        data = read_json(_TRIP_FILE) if _TRIP_FILE.exists() else None
        if data and {"origin", "floor", "until", "auto_was_on"} <= set(data):
            _set_trip({"origin": int(data["origin"]), "floor": int(data["floor"]),
                       "until": float(data["until"]), "auto_was_on": bool(data["auto_was_on"]),
                       "started": float(data.get("started") or 0.0) or None})
            _log(f"[SMART_CRAFT] resuming floor trip {_trip['origin']} -> {_trip['floor']}")
    except (OSError, ValueError, TypeError) as exc:
        _log(f"[SMART_CRAFT] ignoring unreadable floor trip file: {exc}")


def best_floor(missing_ore, current_floor):
    """Lower floor that best supplies {bar: missing ore}, or None when the
    current floor already drops one of them (then staying is better)."""
    if any(FLOOR_ORE_SHARES.get(current_floor, {}).get(bar) for bar in missing_ore):
        return None
    scores = {floor: sum(shares.get(bar, 0.0) * amount for bar, amount in missing_ore.items())
              for floor, shares in FLOOR_ORE_SHARES.items() if floor < current_floor}
    scores = {f: v for f, v in scores.items() if v > 0}
    return max(scores, key=lambda f: (scores[f], -f)) if scores else None


def _patch_kind(img, cx, cy, half_w=30, half_h=8):
    """'green' / 'red' / None for the button colour around (cx, cy)."""
    patch = img[max(0, cy - half_h):cy + half_h, max(0, cx - half_w):cx + half_w].reshape(-1, 3).astype(int)
    if not len(patch):
        return None
    b, g, r = patch[:, 0], patch[:, 1], patch[:, 2]
    if ((g > 140) & (r < 120) & (b < 120)).mean() > 0.5:
        return "green"
    if ((r > 130) & (g < 70) & (b < 80)).mean() > 0.5:
        return "red"
    return None


def read_floor_panel(img):
    """{floor: 'green'|'red'} for the World 1 floor buttons shown in the panel."""
    if _patch_kind(img, *_FLOOR_WORLD1, half_w=15, half_h=10) != "green":
        return {}
    floors = {}
    for row in range(_FLOOR_ROWS):
        y = int(round(_FLOOR_ROW0_Y + row * _FLOOR_ROW_PITCH))
        for col, x in enumerate(_FLOOR_COLUMNS):
            kind = _patch_kind(img, x, y)
            if kind:
                floors[2 * row + 1 + col] = kind
    return floors


def _floor_center(floor):
    row, col = divmod(floor - 1, 2)
    return _FLOOR_COLUMNS[col], int(round(_FLOOR_ROW0_Y + row * _FLOOR_ROW_PITCH))


def _panel_open(gray):
    return _button(gray, _tpl("floors/floor_title.png"), _FLOOR_TITLE_ROI)["found"]


def _open_floor_panel():
    """Open Floor Select; returns (img, floors) or raises _Abort."""
    img, gray = _fresh()
    if not _panel_open(gray):
        button = _button(gray, _tpl("floors/floor_button.png"), _FLOOR_BUTTON_ROI)
        if not button["found"]:
            raise _Abort(f"floor button not found (score={button['score']:.3f})")
        _tap(button["cx"], button["cy"], "floor select")
        _delay()
        img, gray = _fresh()
        if not _panel_open(gray):
            raise _Abort("floor panel did not open")
    floors = read_floor_panel(img)
    if sum(kind == "green" for kind in floors.values()) != 1:
        raise _Abort(f"floor panel unreadable ({len(floors)} button(s), current floor unclear)")
    return img, floors


def _close_floor_panel():
    _img, gray = _fresh()
    if _panel_open(gray):
        match = _button(gray, _tpl("floors/floor_close.png"), _FLOOR_CLOSE_ROI)
        if not match["found"]:
            raise _Abort("floor panel close button not found")
        _tap(match["cx"], match["cy"], "close floor panel")
        _delay()


def current_floor_from(floors):
    return next(f for f, kind in floors.items() if kind == "green")


def change_floor(target):
    """Select `target` in Floor Select and verify it is the green floor
    afterwards. Returns the floor that was current before."""
    _img, floors = _open_floor_panel()
    origin = current_floor_from(floors)
    if origin != target:
        if target not in floors:
            raise _Abort(f"floor {target} not available in the panel")
        _tap(*_floor_center(target), f"floor {target}")
        _delay()
        _img, floors = _open_floor_panel()   # reopens if the tap closed the panel
        now = current_floor_from(floors)
        if now != target:
            raise _Abort(f"floor change not confirmed (wanted {target}, panel shows {now})")
    _close_floor_panel()
    _log(f"[SMART_CRAFT] floor {origin} -> {target}")
    return origin


def _auto_kind(img, cx, cy, half_w=26, half_h=15):
    """True (green, on) / False (dark red, off) / None for the floor Auto toggle.
    Off is a dark maroon (median BGR about (8, 4, 41), seen 2026-11-24), far
    darker than the red floor buttons, so it gets its own check."""
    kind = _patch_kind(img, cx, cy, half_w=half_w, half_h=half_h)
    if kind == "green":
        return True
    patch = img[max(0, cy - half_h):cy + half_h, max(0, cx - half_w):cx + half_w].reshape(-1, 3).astype(int)
    if kind == "red" or (len(patch) and ((patch[:, 2] >= 25) & (patch[:, 2] > patch[:, 1] + 15)
                                         & (patch[:, 2] > patch[:, 0] + 15)).mean() > 0.5):
        return False
    return None


def _auto_state(img, gray):
    button = _button(gray, _tpl("floors/floor_button.png"), _FLOOR_BUTTON_ROI)
    if not button["found"]:
        return None, None
    pos = (button["cx"], button["cy"] + _AUTO_OFFSET_Y)
    return _auto_kind(img, *pos), pos  # whole button; text covers the centre


def floor_auto_on():
    """Current floor Auto state from a fresh main-screen frame, or _Abort."""
    on, _pos = _auto_state(*_fresh())
    if on is None:
        raise _Abort("floor Auto toggle not recognised")
    return on


def set_floor_auto(on):
    """Set the floor Auto toggle (green = on). Returns the previous state."""
    img, gray = _fresh()
    before, pos = _auto_state(img, gray)
    if before is None:
        raise _Abort("floor Auto toggle not recognised")
    if before != on:
        _tap(*pos, f"floor Auto {'on' if on else 'off'}")
        _delay()
        after, _pos = _auto_state(*_fresh())
        if after != on:
            raise _Abort(f"floor Auto not switched {'on' if on else 'off'}")
    return before


def _start_trip(missing_ore):
    """Called with the menu closed. Moves to a lower floor if that helps."""
    # Read Auto before moving: picking a floor in the panel appears to switch
    # Auto off by itself, so reading it afterwards would always say "off" and
    # the return trip would never switch it back on.
    auto_was_on = floor_auto_on()
    _img, floors = _open_floor_panel()
    current = current_floor_from(floors)
    target = best_floor(missing_ore, current)
    if target is None or target not in floors:
        _close_floor_panel()
        _log(f"[SMART_CRAFT] no floor trip (floor {current}; missing ore {missing_ore})")
        return None
    change_floor(target)
    try:
        set_floor_auto(False)
    except _Abort:
        change_floor(current)  # never stay down with Auto in an unknown state
        if auto_was_on:
            try:
                set_floor_auto(True)
            except _Abort as exc:
                _log(f"[SMART_CRAFT] could not switch floor Auto back on: {exc}")
        raise
    trip_s = float(getattr(s, "SMART_CRAFT_FLOOR_TRIP_S", 600.0))
    _set_trip({"origin": current, "floor": target, "auto_was_on": auto_was_on,
               "until": time.time() + trip_s, "started": time.time()})
    _log(f"[SMART_CRAFT] floor trip {current} -> {target} for missing ore {missing_ore}; "
         f"returning after {trip_s:.0f}s at the next cycle (Auto was {'on' if auto_was_on else 'off'})")
    return target


def _end_trip():
    """Return to the origin floor and restore Auto. Keeps the trip on failure
    so a later cycle retries."""
    if not _trip:
        return
    _img, floors = _open_floor_panel()
    if _trip["origin"] not in floors:
        # e.g. a prestige reset the floors; Auto is left as it is now.
        _close_floor_panel()
        _log(f"[SMART_CRAFT] origin floor {_trip['origin']} no longer available -> trip dropped")
        _set_trip(None)
        return
    change_floor(_trip["origin"])
    if _trip["auto_was_on"]:
        set_floor_auto(True)
    started = _trip.get("started")
    took = f" after {time.time() - started:.0f}s" if started else ""
    _log(f"[SMART_CRAFT] floor trip ended{took}: back on floor {_trip['origin']}"
         f"{', Auto on again' if _trip['auto_was_on'] else ''}")
    _set_trip(None)


def _floor_step(missing_ore):
    """After a cycle (menu closed): end a due trip or start a new one."""
    if _trip:
        if time.time() >= _trip["until"] or not state.smart_craft_enabled:
            _end_trip()
        return
    if missing_ore and getattr(s, "SMART_CRAFT_FLOOR_TRIPS", False):
        _start_trip(missing_ore)


# ============================================================
#  Sell surplus ore: Sell All with the still-needed ores locked
# ============================================================
# Sell page facts (verified 2026-11-23): a row's own "Sell" button sells ONE
# ore per tap; "Sell All" sells every unlocked row and needs two taps (the
# first arms it, orange). The box left of a row locks it (row dimmed, Sell
# button dark) so Sell All skips it.

_SELL_BUTTON_X = (660, 780)          # per-row Sell button column
_SELL_ALL = (683, 1422)              # Sell All button centre
_SELL_LABEL_ROI = (380, 1380, 640, 1480)
_ORE_ICON = (100, -4, 150, 40)       # abs x, y relative to the row's Sell button top
_ORE_COUNT = (140, 2, 270, 34)       # count box, same convention
_LOCK_DY = 44                        # lock box centre below the Sell button top
_LOCK_X = 78
_ORE_MIN_SCORE, _ORE_MIN_MARGIN = 0.8, 0.15
_LOCKED_MAX_RED = 110                # Sell button mean red: unlocked ~172, locked ~51


def _sell_ready(_img, gray):
    return _button(gray, _tpl("sell_all_label.png"), _SELL_LABEL_ROI)["found"]


def classify_ore(img, top):
    """Ore name of the Sell row whose button top is `top`, or None."""
    x1, dy1, x2, dy2 = _ORE_ICON
    area = img[max(0, top + dy1):top + dy2, x1:x2]
    scores = sorted(((float(cv2.matchTemplate(area, tpl, cv2.TM_CCOEFF_NORMED).max()), name)
                     for name, tpl in _ores.items()
                     if area.shape[0] >= tpl.shape[0] and area.shape[1] >= tpl.shape[1]), reverse=True)
    if not scores or scores[0][0] < _ORE_MIN_SCORE:
        return None
    if len(scores) > 1 and scores[0][0] - scores[1][0] < _ORE_MIN_MARGIN:
        return None
    return scores[0][1]


def read_sell_rows(img):
    """[(ore or None, button_top, locked)] for fully visible Sell rows."""
    _x1, list_top, _x2, list_bottom = _list_roi()
    x1, x2 = _SELL_BUTTON_X
    band = img[:, x1:x2].astype(int)
    b, g, r = band[..., 0], band[..., 1], band[..., 2]
    mask = ((r > g + 20) & (r > 40) & (g < 80)).astype(np.uint8)  # bright or dimmed red
    n, _lab, stats, _c = cv2.connectedComponentsWithStats(mask)
    rows = []
    for x, y, w, h, _a in stats[1:n]:
        if w < 100 or not 70 <= h <= 85 or y < list_top or y + h > list_bottom:
            continue
        red = img[y + 10:y + h - 10, x1 + x + 10:x1 + x + w - 10, 2].mean()
        rows.append((classify_ore(img, int(y)), int(y), red < _LOCKED_MAX_RED))
    return sorted(rows, key=lambda row: row[1])


def _toggle_lock(top, ore, want_locked):
    _tap(_LOCK_X, top + _LOCK_DY, f"{'lock' if want_locked else 'unlock'} {ore}")
    _delay()
    img, _gray = _fresh()
    row = next((r for r in read_sell_rows(img) if abs(r[1] - top) < 10), None)
    if row is None or row[2] != want_locked:
        raise _Abort(f"{ore}: lock state did not change")
    return img


def _count_box(img, top):
    x1, dy1, x2, dy2 = _ORE_COUNT
    return cv2.cvtColor(img[top + dy1:top + dy2, x1:x2], cv2.COLOR_BGR2GRAY).astype(int)


def sell_surplus_ore(needed, generation):
    """Sell every ore whose bar no buyable upgrade needs. Ores of the
    `needed` bars, ores without a bar template and unrecognised rows are kept;
    rows the player locked stay locked. Rows locked here are unlocked again
    afterwards. Returns the sold ore names."""
    img, gray = _open_page("sell_tpl", _sell_ready, "Sell", generation)
    locked_here, sold, before = [], [], {}
    try:
        for img, gray, _offset in list_pages(img, gray, generation):
            for ore, top, locked in read_sell_rows(img):
                _check_cancel(generation)
                if locked or ore in locked_here or ore in before:
                    continue
                if ore is None:
                    raise _Abort("unrecognised ore row on the Sell page")
                if ore in _bars and ore not in needed:
                    before[ore] = None  # sold below; count box read on the last page
                    continue
                img = _toggle_lock(top, ore, True)
                locked_here.append(ore)
        if not before:
            _log("[SMART_CRAFT] no surplus ore to sell")
            return []
        # The list is now at its end; remember which sellable rows are visible
        # there to confirm the sale.
        img, _gray = _fresh()
        visible = {ore: _count_box(img, top) for ore, top, locked in read_sell_rows(img)
                   if ore in before and not locked}
        _tap(*_SELL_ALL, "Sell All (arm)")
        _delay()
        _tap(*_SELL_ALL, "Sell All (confirm)")
        _delay()
        img, _gray = _fresh()
        after = {ore: _count_box(img, top) for ore, top, _l in read_sell_rows(img) if ore in visible}
        changed = [ore for ore in visible if ore in after and np.abs(after[ore] - visible[ore]).mean() > 3]
        sold = sorted(before)
        if visible and not changed:
            _log(f"[SMART_CRAFT] Sell All showed no visible effect (ores {sold})")
            sold = []
        else:
            note = "" if changed else " (no sold row visible to confirm)"
            _log(f"[SMART_CRAFT] sold surplus ore: {', '.join(sold)}{note} "
                 f"(kept: {', '.join(locked_here) or '-'})")
        return sold
    finally:
        _unlock_rows(locked_here, generation)


def _unlock_rows(names, generation):
    """Best effort: unlock the rows this cycle locked."""
    pending = set(names)
    if not pending:
        return
    try:
        img, gray = _fresh()
        if not _sell_ready(img, gray):
            raise _Abort("Sell page no longer open")
        for img, gray, _offset in list_pages(img, gray, generation):
            for ore, top, locked in read_sell_rows(img):
                if ore in pending and locked:
                    img = _toggle_lock(top, ore, False)
                    pending.discard(ore)
            if not pending:
                break
    except _Abort as exc:
        _log(f"[SMART_CRAFT] restoring ore locks failed: {exc}")
    if pending:
        _log(f"[SMART_CRAFT] WARNING: ores still locked from selling: {', '.join(sorted(pending))}")


# ============================================================
#  Cycle
# ============================================================

def run_cycle(generation=None) -> str:
    """Caller owns the action lock. Returns ok | nothing_needed | error | cancelled."""
    generation = _run_generation if generation is None else generation
    crafted = bought = 0
    first_summary = None
    try:
        # Each round: read needs -> craft -> buy. Buying raises the next level's
        # cost, so further rounds continue while purchases still happen.
        # _yield_point() pauses for waiting workflows; every page is reopened afterwards.
        for round_no in range(1, int(getattr(s, "SMART_CRAFT_MAX_ROUNDS", 3)) + 1):
            _yield_point(generation)
            img, gray = _open_page("upgrades_tpl", _upgrades_ready, "Upgrades", generation)
            rows = scan_upgrades(img, gray, generation)
            workshop = getattr(s, "SMART_CRAFT_WORKSHOP", True)
            if workshop:
                _yield_point(generation)
                img, gray = _open_page("workshop_tpl", _workshop_ready, "Workshop", generation,
                                       section="permanent")
                ws_rows = scan_workshop(img, gray, generation)
                _log(f"[SMART_CRAFT] Workshop: {len({r[3] for r in ws_rows})} buyable row(s)")
                rows = rows + ws_rows
            shortfalls = compute_shortfalls(rows)
            summary = ", ".join(f"{b} {h}/{t}" for b, (h, t) in shortfalls.items()) or "none"
            first_summary = first_summary or summary
            _ore_missing.clear()
            _log(f"[SMART_CRAFT] round {round_no}: {len(rows)} bar-cost upgrade(s); missing: {summary}")
            if shortfalls:
                quantity = None
                for index, (bar, (have, target)) in enumerate(shortfalls.items()):
                    if _yield_point(generation) or index == 0:
                        _open_page("craft_tpl", _craft_ready, "Craft", generation)
                        quantity = None
                        if getattr(s, "SMART_CRAFT_USE_5X", True) and select_quantity_5x():
                            quantity = "5x"
                    crafted += craft_bar(bar, have, target, generation, quantity)
            bought_now = 0
            if getattr(s, "SMART_CRAFT_BUY_UPGRADES", True):
                _yield_point(generation)
                bought_now = buy_upgrades(generation)       # Upgrades first (user policy)
                if workshop:
                    _yield_point(generation)
                    bought_now += buy_workshop(generation)
            bought += bought_now
            if not bought_now:
                break
        sold = []
        if getattr(s, "SMART_CRAFT_SELL_SURPLUS_ORE", False):
            if bought_now:
                # A purchase after the last scan can add a cost in a new bar type.
                _log("[SMART_CRAFT] selling skipped: upgrade list changed after the last scan")
            else:
                _yield_point(generation)
                sold = sell_surplus_ore({r[0] for r in rows}, generation)
        status = "ok" if crafted or bought or sold else "nothing_needed"
        state.smart_craft_last_status = (f"{status}: crafted {crafted} bar(s), bought {bought} "
                                         f"upgrade(s); missing at start: {first_summary}")
        if sold:
            state.smart_craft_last_status += f"; sold ore: {', '.join(sold)}"
        _close_menu()
        _floor_step(dict(_ore_missing))
        if _trip:
            state.smart_craft_last_status += (f"; mining on floor {_trip['floor']} "
                                              f"(from {_trip['origin']})")
        return status
    except _Abort as exc:
        state.smart_craft_last_status = f"stopped: {exc}"
        _log(f"[SMART_CRAFT] cycle stopped: {exc}")
        return "cancelled" if str(exc) == "cancelled" else "error"
    finally:
        if not _paused:  # never tap without the lock (cancelled during a pause)
            _close_menu()


def _try_run_with_lock() -> str:
    """Returns: ok | nothing_needed | busy | paused | no_game | error | cancelled."""
    if not _bars or not _glyphs:
        return "error"
    if not is_game_running():
        _log("[SMART_CRAFT] game not running -> skipping tick")
        return "no_game"
    lock = state.ingame_action_lock
    global _lock_owned, _paused
    if getattr(state, "automation_paused", False):
        _log("[SMART_CRAFT] automation paused -> not starting a cycle")
        return "paused"
    if not lock.acquire(blocking=False):
        _log("[SMART_CRAFT] another feature is busy -> postponing")
        return "busy"
    _lock_owned, _paused = True, False
    try:
        state.smart_craft_last_run_ts = time.time()
        return run_cycle()
    finally:
        if _lock_owned:  # a pause may have ended without taking the lock back
            lock.release()
        _lock_owned = _paused = False


def _return_with_lock() -> bool:
    """Smart Craft was switched off during a trip: go back to the origin
    floor. Returns True when no trip is pending any more."""
    if not is_game_running() or not state.ingame_action_lock.acquire(blocking=False):
        return False
    try:
        _end_trip()
        return True
    except _Abort as exc:
        _log(f"[SMART_CRAFT] returning from the floor trip failed: {exc}")
        return False
    finally:
        state.ingame_action_lock.release()


def _sleep_interruptible(seconds: float):
    if _wake_evt.wait(timeout=seconds):
        _wake_evt.clear()


def _worker():
    _log(f"[SMART_CRAFT] worker thread started (enabled={state.smart_craft_enabled})")
    _sleep_interruptible(3.0)
    while not _stop_evt.is_set():
        try:
            interval = float(getattr(s, "SMART_CRAFT_INTERVAL_S", 600.0))
            if not state.smart_craft_enabled:
                state.smart_craft_next_check_ts = 0.0
                if _trip and not _return_with_lock():
                    _sleep_interruptible(float(getattr(s, "SMART_CRAFT_BUSY_RETRY_S", 15.0)))
                    continue
                _sleep_interruptible(5.0)
                continue
            result = _try_run_with_lock()
            if result == "paused":
                wait = 5.0
            elif result == "busy":
                wait = float(getattr(s, "SMART_CRAFT_BUSY_RETRY_S", 15.0))
            elif result == "no_game":
                wait = min(30.0, interval)
            else:
                wait = interval
            if _trip and result not in ("busy", "paused"):
                wait = min(wait, max(15.0, _trip["until"] - time.time()))
            state.smart_craft_next_check_ts = time.time() + wait if state.smart_craft_enabled else 0.0
            _sleep_interruptible(wait)
        except Exception as exc:
            _log(f"[SMART_CRAFT] worker error: {type(exc).__name__}: {exc}")
            time.sleep(5.0)


def start():
    """Load templates and start the worker (idempotent)."""
    global _thread, _thread_started
    if _thread_started:
        return
    _thread_started = True
    bars, glyphs = load_templates()
    if bars == 0 or glyphs < 11:
        state.smart_craft_last_status = "disabled: templates missing in images/smart_craft/"
        _log(f"[SMART_CRAFT] disabled: {bars} bar icon(s), {glyphs}/11 glyphs in images/smart_craft/")
        return
    _log(f"[SMART_CRAFT] armed ({bars} bar types: {', '.join(_bars)})")
    _load_trip()
    _stop_evt.clear()
    _thread = threading.Thread(target=_worker, daemon=True, name="smart-craft-worker")
    _thread.start()


def toggle() -> bool:
    """Toggle Smart Craft; switching ON runs a cycle now, OFF cancels a running one."""
    global _run_generation
    state.smart_craft_enabled = not state.smart_craft_enabled
    _run_generation += 1
    state.smart_craft_next_check_ts = 0.0
    _wake_evt.set()
    _log(f"[SMART_CRAFT] toggled -> {'ON (running now)' if state.smart_craft_enabled else 'OFF'}")
    return state.smart_craft_enabled
