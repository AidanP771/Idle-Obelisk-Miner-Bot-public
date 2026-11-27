# features/skill_tree.py - buy Skill Tree nodes with Skill Points
"""
Skill Tree (main menu -> Permanent -> Skill-Tree; verified in game 2026-11-27):

- One vertically scrolling tree that starts from a single node at the top.
  Owned nodes have gold frames. Only children of owned nodes can be bought;
  they have light stone frames (ring brightness about 86). Other nodes are
  dark (about 42) or show a lock with the obelisk level that unlocks them
  (about 33).
- Tapping a node selects it: the panel at the bottom shows its name, effect
  and a cost button (Skill Points) at about (777,1383), dark when the points
  are not enough. "Skill Points" and "Skill Bits" are shown on the right.
- Skill Points come from combining 10 Skill Bits in the item bag (items.py).

A visit (items worker, under the action lock) reads the points and, if there
are any, walks the tree from the top: on each page the buyable nodes are
tried top-down, left to right; a node is bought when its cost button is lit,
and the purchase counts only when the node turns gold or the points drop.
With ITEMS_SKILL_TREE_DRY_RUN nodes are only selected and logged (frames go
to temp/skill_tree_debug/), and the cost button is never pressed.
"""
import os
import time

import cv2
import numpy as np

import config.settings as s
from core.file_logger import bot_log
from core.screen_cache import get_screen
from core.scroll import scroll
from core.tapper import tap
from core.vision import find_template

TILE_TPL = "workflows/menu/permanent/Skilltree_tpl.png"
READY_TPL = "skill_tree/header.png"     # "Use Skill Points to unlock permanent perks!"

_SLOT_NAME = "skill_tree_scr"
_TREE_ROI = (0, 160, 780, 1255)         # x < 780 leaves out the points panel and shop button
_TAP_Y = (215, 1200)                    # only nodes fully on screen are tapped
_POINTS_ROI = (765, 285, 890, 315)
_COST_BOX = (705, 1330, 850, 1435)
_COST_TAP = (777, 1383)
_COST_LIT_MIN_RED = 115                 # dark button: mean red 85; lit red buttons about 140
_OWNED_MIN_GOLD = 0.25                  # gold share of the frame ring (owned about 0.5)
_BUYABLE_MIN_STONE = 65                 # stone ring brightness: buyable 86, dark 42, locked 33
_SCROLL_AREA = (20, 220, 120, 1240)     # left margin: a drag there never starts on a node
_PAGE_STEP_PX = 600
_MAX_PAGES = 12
_SAME_FRAME_DIFF = 3.0                  # mean abs gray difference of an unchanged tree
_DEBUG_DIR = "skill_tree_debug"
_DEBUG_KEEP = 20


class Abort(Exception):
    pass


def _log(msg, level="TXT"):
    bot_log(msg, level)


def _fresh():
    img, _w, _h = get_screen(_SLOT_NAME, max_age=0.0, force=True, color=True)
    if img is None:
        raise Abort("screenshot failed")
    return img, cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def _tap(x, y, what):
    _log(f"[SKILL_TREE] tap {what} at ({int(x)},{int(y)})")
    if not tap(int(x), int(y), jitter=2):
        raise Abort(f"tap failed: {what}")


def _dump(img, reason):
    try:
        folder = os.path.join(str(getattr(s, "BASE_DIR", ".")), "temp", _DEBUG_DIR)
        os.makedirs(folder, exist_ok=True)
        cv2.imwrite(os.path.join(folder, f"{time.strftime('%Y%m%d_%H%M%S')}_{reason}.png"), img)
        files = sorted(f for f in os.listdir(folder) if f.endswith(".png"))
        for old in files[:-_DEBUG_KEEP]:
            os.remove(os.path.join(folder, old))
    except Exception:
        pass


# ============================================================
#  Reading
# ============================================================

def tree_open(gray) -> bool:
    return find_template(gray, READY_TPL, roi=(150, 100, 750, 155), threshold=0.85)["found"]


def _ring(img, cx, cy, outer=50, inner=34):
    patch = img[cy - outer:cy + outer, cx - outer:cx + outer].astype(int)
    yy, xx = np.mgrid[-outer:outer, -outer:outer][:, :patch.shape[0], :patch.shape[1]]
    d = np.maximum(abs(yy), abs(xx))
    return patch[(d >= inner) & (d < outer)]


def classify(img, cx, cy) -> str:
    """owned | buyable | other for the node centred at (cx, cy)."""
    ring = _ring(img, cx, cy)
    if not len(ring):
        return "other"
    b, g, r = ring[:, 0], ring[:, 1], ring[:, 2]
    if ((r > 110) & (r - b > 50) & (g > 70)).mean() >= _OWNED_MIN_GOLD:
        return "owned"
    spread = ring.max(1) - ring.min(1)
    stone = ring[(spread < 35) & (ring.max(1) > 28)]
    if len(stone) > 0.3 * len(ring) and stone.max(1).mean() >= _BUYABLE_MIN_STONE:
        return "buyable"
    return "other"


def find_nodes(img) -> list:
    """[{x, y, kind}] for owned and buyable nodes (dark and locked nodes are
    too dim to be found and are never needed). Sorted top-down, left-right."""
    x1, y1, x2, y2 = _TREE_ROI
    roi = img[y1:y2, x1:x2]
    mask = (roi.max(axis=2) > 24).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((11, 11), np.uint8))   # drops 6 px edges, stars
    n, _labels, stats, _c = cv2.connectedComponentsWithStats(mask)
    nodes = []
    for i in range(1, n):
        x, y, w, h, _area = stats[i]
        if not (75 <= w <= 135 and 75 <= h <= 135):
            continue
        cx, cy = int(x + w // 2 + x1), int(y + h // 2 + y1)
        kind = classify(img, cx, cy)
        if kind != "other":
            nodes.append({"x": cx, "y": cy, "kind": kind})
    return sorted(nodes, key=lambda n: (n["y"] // 40, n["x"]))


def read_points(gray):
    from features import smart_craft as sc
    if not sc._glyphs:
        sc.load_templates()
    return sc.parse_int(sc.read_text(gray, _POINTS_ROI))


def cost_lit(img) -> bool:
    x1, y1, x2, y2 = _COST_BOX
    return float(img[y1:y2, x1:x2, 2].mean()) > _COST_LIT_MIN_RED


def _same(a, b) -> bool:
    x1, y1, x2, y2 = _TREE_ROI
    return float(cv2.absdiff(a[y1:y2, x1:x2], b[y1:y2, x1:x2]).mean()) < _SAME_FRAME_DIFF


# ============================================================
#  Visit (caller holds the action lock; the tree page is open)
# ============================================================

def _scroll(direction, distance, duration_ms):
    if not scroll(direction, area=_SCROLL_AREA, distance=distance, duration_ms=duration_ms):
        raise Abort("scroll failed")
    time.sleep(0.6)


def _scroll_to_top(img, gray):
    for _ in range(8):
        _scroll("down", 1100, 300)          # finger down: content moves down (towards the top)
        new_img, new_gray = _fresh()
        if _same(gray, new_gray):
            return new_img, new_gray
        img, gray = new_img, new_gray
    return img, gray


def _owned_near(img, x, y):
    return any(n["kind"] == "owned" and abs(n["x"] - x) < 25 and abs(n["y"] - y) < 25 for n in find_nodes(img))


def _buy_page(img, gray, points, dry_run, result):
    """Try the buyable nodes of this page; returns (img, gray, points)."""
    tried = []
    while points > 0:
        nodes = [n for n in find_nodes(img) if n["kind"] == "buyable" and _TAP_Y[0] <= n["y"] <= _TAP_Y[1]
                 and not any(abs(n["x"] - x) < 25 and abs(n["y"] - y) < 25 for x, y in tried)]
        if not nodes:
            break
        node = nodes[0]
        tried.append((node["x"], node["y"]))
        _tap(node["x"], node["y"], "node")
        time.sleep(0.6)
        img, gray = _fresh()
        if not cost_lit(img):
            _log(f"[SKILL_TREE] node at ({node['x']},{node['y']}): cost button dark (not enough points)")
            result["unaffordable"] += 1
            continue
        if dry_run:
            _log(f"[SKILL_TREE] dry run: node at ({node['x']},{node['y']}) is affordable "
                 f"({points} point(s)) -> not buying", "FULL")
            result["would_buy"] += 1
            _dump(img, "dry_run_affordable")
            continue
        _tap(*_COST_TAP, "buy node")
        confirmed = False
        for _ in range(6):
            time.sleep(0.4)
            img, gray = _fresh()
            after = read_points(gray)
            if _owned_near(img, node["x"], node["y"]) or (after is not None and after < points):
                confirmed = True
                points = after if after is not None and after < points else points - 1
                break
        if not confirmed:
            _dump(img, "buy_unconfirmed")
            raise Abort(f"purchase of node at ({node['x']},{node['y']}) not confirmed")
        result["bought"] += 1
        _log(f"[SKILL_TREE] bought node at ({node['x']},{node['y']}); {points} point(s) left", "FULL")
    return img, gray, points


def visit(open_tile, dry_run=True) -> dict:
    """open_tile(tile, ready_tpl, name) -> (img, gray) opens a main-menu tile
    (items._open_tile). Returns {points, bought, would_buy, unaffordable, pages}."""
    result = {"points": None, "bought": 0, "would_buy": 0, "unaffordable": 0, "pages": 0}
    img, gray = open_tile(TILE_TPL, READY_TPL, "Skill-Tree")
    points = read_points(gray)
    result["points"] = points
    if points is None:
        _dump(img, "points_unreadable")
        raise Abort("Skill Points unreadable")
    if points == 0:
        _log("[SKILL_TREE] 0 Skill Points -> nothing to buy")
        return result
    if dry_run:
        _dump(img, "dry_run_start")
    img, gray = _scroll_to_top(img, gray)
    for page in range(_MAX_PAGES):
        result["pages"] = page + 1
        nodes = find_nodes(img)
        _log(f"[SKILL_TREE] page {page + 1}: " + (", ".join(f"{n['kind']}@({n['x']},{n['y']})" for n in nodes)
                                                   or "no owned or buyable nodes"))
        if dry_run and page < 3:
            _dump(img, f"dry_run_page{page + 1}")
        if page > 0 and not nodes:
            break                            # past the last owned node: nothing further is buyable
        img, gray, points = _buy_page(img, gray, points, dry_run, result)
        if points <= 0:
            break
        _scroll("up", _PAGE_STEP_PX, 1000)   # finger up: reveals the rows below
        new_img, new_gray = _fresh()
        if _same(gray, new_gray):
            break                            # end of the tree
        img, gray = new_img, new_gray
    result["points"] = points
    return result
