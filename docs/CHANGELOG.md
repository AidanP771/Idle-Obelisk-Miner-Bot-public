# Change Log (fork)

Changes on the fork branch `smart-craft-early-game`
(<https://github.com/AidanP771/Idle-Obelisk-Miner-Bot-public>), based on
upstream `main` at `7aec193`. Newest first. Each entry names the commit, what
changed for the user, and how it was verified. Game facts and research behind
the changes are in [DEVELOPMENT_NOTES.md](DEVELOPMENT_NOTES.md); behavior is in
[PROJECT_CONTEXT.md](PROJECT_CONTEXT.md).

Local, unversioned changes (`config/user_workflows.json`,
`config/user_settings.json`) are listed separately because git ignores them.

## 2026-11-27

### Skill Bits are combined, the Skill Tree is visited (dry run first), item counts with '4' read again (`52c65e0`)
- User request: combine Skill Bits once 10 are held and spend the Skill
  Points in the Skill Tree, top-down. Only children of owned nodes can be
  bought, and costs are only visible for those (user).
- Items (`features/items.py`): with `ITEMS_COMBINE_SKILL_BITS` (default on)
  the bag pass uses Skill Bit ("Combine 10 into a Skill Point!", the normal
  Use button) while 10 or more are held; checked at most every 10 min.
- New `features/skill_tree.py`, run from the items cycle with
  `ITEMS_SKILL_TREE` (default off; local on) after a combine and every
  `ITEMS_SKILL_TREE_INTERVAL_S` (1800 s): main menu -> Skill-Tree, read the
  Skill Points (0 -> close), scroll to the top and walk the tree downwards.
  Nodes are found by their frames: gold = owned, light stone = buyable
  (children of owned nodes); dark and locked nodes are ignored. Buyable
  nodes are tapped top-down, left to right; a lit cost button is pressed and
  the purchase counts only when the node turns gold or the points drop
  (otherwise the visit stops and the frame goes to
  `temp/skill_tree_debug/`). `ITEMS_SKILL_TREE_DRY_RUN` (default on, local
  on) only selects nodes and logs which are affordable.
- Item counts: the user had seen many bag counts as "?". The single '4'
  reference scored 0.53-0.64 on the 4s of a new bag frame (the counts render
  at sub-pixel offsets) and one '2' 0.69 (limit 0.7), so counts with a 4,
  including Skill Bits, were unreadable and those items skipped. Digits may
  now have variants (`4_2.png`, `4_3.png`, `2_2.png`); every count on the new
  frame reads correctly, the older frames read as before.
- Verified: 363 Python and 27 JavaScript tests, with live frames
  (`tests/data/skill_tree/`, `tests/data/items/bag_skill_bit_selected.png`)
  and scripted buy/dry-run sequences. Not yet run live: combining (4 bits
  held), a lit cost button, a purchase, scrolling the tree. Needs a restart.

### Freebies claims the Freebie Pack through the Store (`d5d420f`)
- User report: the Freebie Pack was not being claimed. The pack adds one
  claim every 10 min and banks two ("Banked: N/2"), but after a detection
  the bot visited only every 45 min (`FREEBIES_CHECK_INTERVAL_S_SLOW` 2700)
  and claimed one pack per visit, so most packs were lost (log 2026-11-27:
  one claim every 45 min). It also started from the bottom-right shop icon,
  which sometimes turns into a promotional sale shortcut, and never checked
  the reward.
- Now each visit opens the main menu and the Store tile, finds the
  "Freebie Pack" row by its label (new templates
  `freebie/freebie_pack_label.png`, `freebie/store_loot_tables.png`), reads
  "Banked N/2" and taps Claim for every banked pack. A claim counts only when
  a fresh frame shows a lower banked count (or the Claim button went dark);
  unconfirmed claims are not counted and their frame goes to
  `temp/freebies_debug/`. Next visit: `FREEBIES_CHECK_INTERVAL_S_SLOW`
  (default now 900 s) after a completed visit, `..._FIRST` (300 s) after a
  failed one. `FREEBIES_DETECT_ROI` is no longer used.
  `FREEBIES_DIAMONDS_PER_CLAIM` default 5 (the pack shows 5 gems).
- Verified: 348 Python and 27 JavaScript tests, including the live Store
  frame (`tests/data/freebies/store_freebie_pack.png`: banked 0/2, dark
  Claim). Not yet run live: a lit Claim button, the banked count after a
  claim, and whether a reward popup appears. Needs a restart.

### Smart Craft finds Workshop rows below maxed rows (`dbc56b0`)
- User report: the first six Workshop upgrades are maxed and a new one
  unlocked, but Smart Craft never looked at it ("Workshop: 0 buyable row(s)"
  every cycle). Two causes, both seen on a live frame (2026-11-27 18:16):
  - Scans and buying stopped on the first page without a Buy button. Maxed
    rows have none, so the list was never scrolled to "Unlock Cherry Bomb".
    The same stop was in the Upgrades scan and buying. Now only `list_pages`
    ends a scan: at the locked section ("Unlock At ..." below every Buy),
    at the list end, or at the page limit.
  - The new row costs amethyst 4/600, topaz 0/500, obsidian 0/400. The topaz
    icon matched 2 px to the right, the "have" text box (icon x + 54) cut
    the "0", the cost read as "/" and the whole row was dropped as
    unreadable. The text boxes start at x + 49 now (47-51 read every saved
    Workshop frame).
- Verified: 347 Python tests, including the live frame
  (`tests/data/smart_craft/workshop_cherry.png`) and a scan whose first page
  has no Buy button. Needs a restart; not yet seen live.

## 2026-11-25

### Smart Craft buys free upgrade rows (`a814e7d`)
- User report: a prestige upgrade lowers pickaxe costs and can make the first
  (and further) levels free; Smart Craft did not press Buy then. Captured
  live at 22:08: "Upgrade Pickaxe - 1 > 3 / Free!" with a lit Buy button, not
  bought since the 21:51 prestige. The row has no cost icon, so
  `read_upgrade_rows` never listed it. Costs themselves are read from the
  screen (not hard-coded), so lowered prices were already handled.
- New template `smart_craft/free.png` and `read_free_rows`; `buy_upgrades`
  buys lit free rows together with the met bar rows, top-down (the pickaxe
  first), confirmed by the level box like other purchases.
- The pickaxe priority (below) worked live: after the 20:49 prestige the
  pickaxe went from level 0 to 14 in 55 min.
- Verified: 345 Python tests (reader on the live frame and on frames with
  locked "Free Bomb Chance" rows, free row bought first). Needs a restart.

### Upgrade Pickaxe gets priority in Smart Craft (`2cf9c8c`)
- User report: the pickaxe is upgraded slowly, and pickaxe damage drives
  almost everything else. Log since the 07:35 prestige: level 2 -> 3 (copper
  15) waited 17-30 min with 500-900 copper ore in stock, because `next` mode
  crafted copper only for the cheaper rows (2, 4, 8, 10, 12, 14), one purchase
  per round or cycle, and rows were bought in icon order, not top-down.
  Level 9 -> 10 (runite 50) waited 55 min: runite ore was short and the floor
  trips went for the larger citrine and adamant shortages.
- Now the Upgrade Pickaxe row (top of the Upgrades list) sets its bar's craft
  target to its own need, that bar is crafted first, Upgrades rows are bought
  top-down (pickaxe first), and while the pickaxe lacks ore a floor trip mines
  only that ore.
- Verified: 343 Python tests (new: pickaxe target, crafting order and floor-trip
  ore, top-down buying). Needs a restart; not yet seen live.

### Infinity Bomb in the bomb rotation (local workflow only)
- User report: a new bomb in the bomb panel was never used. It is the purple
  Infinity Bomb in slot 5 (Workshop unlock, full at 5 charges). The existing
  template `bomb_interface/infinity_bomb_tpl` matches it at 0.998.
- Wiki (Bombs, re-read): each Infinity Bomb fired adds a damage multiplier;
  base recharge 160 s. `bomb_rotation_early_game` now fires it after Exp and
  Plenty (rocks marked first) and before Basic and Chain, and drains it like
  Basic (until slot 5 shows "0", at most 5 shots).
- Verified: workflow validation, engine dry run with mocked screens (1, 3, 5
  and 9 charges; Basic/Chain unchanged), the slot-5 count ROI on a live frame
  (zeros in slots 1, 3, 4 score 1.0 on the same 90 px pitch); reloaded live
  (`reload_cfg`). Live: 09:49 five shots from a full slot, 09:51 the slot read "0" and the drain steps skipped. The multiplier itself is not visible to the bot.

### item_cooldown blocks only after a real item use (`28cbb85`)
- Live after the restart (06:46): `ITEMS_ENABLED` was saved at 06:47, after the
  start, so items stayed off (settings need a restart). The obelisk showed the
  damage warning at 06:47 and 07:03, and `item_cooldown obelisk 7200` blocked
  the pickaxe items for 2 h although none had been used.
- `items.block` now blocks only when a `use_items` step with that cooldown key
  used an item in the last 5 min; otherwise it logs "no recent use, not
  blocked". Verified: 340 Python tests. Needs a restart.

### Items feature: bag rules, chests and relics, use_items step (`f896e5b`)
- User request: the automation never used items; also open chests and relics
  and keep a record of items. Wiki (Items, Chests) and live frames (captured
  with the user) are in the development notes.
- New `features/items.py` (Settings section "Items", `ITEMS_ENABLED` off by
  default, restart): keep-running items with reserve, Strawberries while not
  Level Capped, Blue Cow at once, item chests (Open All + Confirm) and relic
  chests (Open All; Reroll is never tapped) every 30 min, `item_record.json`
  with bag snapshots, chest yields (bag difference) and use counts. New
  templates `images/items/` (page checks, relic buttons, Skill Bit, count
  digits 0-8).
- New workflow steps `use_items` and `item_cooldown` (engine, validation,
  editor). Local workflows: pickaxe items before each obelisk fight, a 2 h
  item block after a damage warning, Chaos Totem in the bomb rotation.
- Verified: 340 Python and 27 JS tests (bag counts and active timer on live
  frames, chest/relic flows replayed on captured frames, Reroll never tapped,
  reserve/cooldown rules, step and settings validation); workflow validation
  and engine dry run. Not verified live (needs `ITEMS_ENABLED` and a restart).

### Items research (docs only, `c6b20d2`)
- User: the automation does not use items. Read the wiki (Items, Chests,
  Store) and captured the Chests, Relics and item bag screens with the user
  (they opened 146 item chests and 71 relic chests by hand; two Bread uses
  showed that uses add time). Facts and the user's usage policy are in the
  development notes; reference frames in `tests/data/items/`. No code yet.

### Bomb rotation drains the Basic bomb (`f0faca7`)
- Bug (user report: the first bomb in the line is not used up): the rotation
  fired Basic once per run; Basic gains about one charge per 30 s, so it sat
  at 4-5 of 5 and its recharge time was wasted.
- New template `bomb_interface/count_zero_tpl` (the white "0" under a bomb
  icon; zeros 0.98-1.0, everything else <= 0.53 on 94 saved frames). The local
  `bomb_rotation_early_game` fires Basic again 2.7 s apart (past the 2.6 s
  floor turnover) while its count is not 0, at most 5 shots per run.
- Verified: validation, engine dry run (1/2/5 shots, cap 5), live after a
  workflow reload: 06:01 five shots, 06:02 two shots then stop at "0".

### Pickaxe upgrade never crafted for; faster scroll to the list top (`ddabfac`)
- Bug (user report: the pickaxe is not upgraded): live frame of the Upgrades
  top showed Upgrade Pickaxe copper 10/15 and Pickaxe Crit Chance tin 0/2 +
  copper 10/2. Two faults together: the tin icon (x=158) was left of the cost
  search area (x from 180), so Crit Chance looked like "copper 10/2"; and
  `next` mode targeted the cheapest copper need (2, already met), so copper was
  never crafted for the pickaxe's 15. Crit Chance's Buy stayed dark (tin), and
  the buy step skipped it without a log line.
- `compute_shortfalls` `next`: per bar the cheapest need that is not met yet.
  Cost area from x=150. The buy step logs a met row whose Buy is dark.
- Navigation: the game keeps each list's scroll position, so every page
  opening needed 3-5 slow drags to the top (about 50-60 s per cycle). The first
  drag to the top is now one fast 1100 px drag; normal drags only confirm.
- Workshop checked: buying works live (levels 0 -> 1 -> 2, confirmed by the
  level box).
- Verified: 328 Python and 27 JS tests (real frame of the two-cost row; met
  needs ignored; fling first). 251 saved frames: no other read changed. Not
  verified live (needs a restart); the fling's effect on the game's list is
  unobserved.

## 2026-11-24

### Smart Craft purchase check by level counter; buy step no longer stops at one row (`0a6d592`)
- Bug (user report: affordable upgrades not bought): since 12:51 every buy
  step ended at its first tap, "gold Buy ... had no visible effect -> stop
  buying". The check averaged the cost-text pixels, so one changed digit looked
  like no change, and any unconfirmed tap ended the whole buy step.
- Purchases are now confirmed by the row's level box (`read_level`: level up,
  or the box appears 0 -> 1), polling fresh frames every 0.3 s (up to 5)
  instead of one frame after 0.8 s. Without a readable level the row check
  decides (gold: Buy no longer lit or >1% of the cost pixels clearly changed).
  An unconfirmed row is skipped for that page; the other rows are still bought
  (Upgrades bar rows, gold rows and Workshop rows).
- Workshop "3" in the third cost column: new glyph `digits/3_4.png` (amethyst
  "263/", topaz "39/" were unreadable); no read changed on 239 saved frames.
- Verified: 325 Python and 27 JS tests (real-frame level reads 5/5/4 and
  2/0/0; regression test: an unconfirmed row no longer blocks the next one).
  Not verified live (needs a restart).


### Drone point check, floor Auto guard, 5 min skip (`4e7ac55`)
- Bars without a Craft row are skipped for 5 min instead of 30 (user: new
  ores unlock often).
- New workflow step `read_number` (validation, editor, tests): read digits in
  a ROI with the Smart Craft glyph reader, store them, or check they
  increased/changed within the run. `buy_drone_points` reads the drone point
  counter before Max and after Exchange; unreadable or unchanged -> close the
  Drones menu and abort the prestige. Engine dry run: 6 -> 7 ok; 6 -> 6 and
  unreadable close the menu and abort. Behaviour change: with gold below one
  point's price (about 302) the prestige now waits until enough gold accrues.
- New feature `features/floor_auto.py` (`FLOOR_AUTO_GUARD`, default on;
  `FLOOR_AUTO_CHECK_INTERVAL_S` 30 s; WebUI): switches floor Auto back on when
  the toggle is dark red; reads the cached screenshot without the lock, confirms
  under the lock on a fresh frame, verifies green. Never during Smart Craft floor
  trips, while paused, or when the button is not visible. Real frames: off /
  on / menu read as False / True / None.
- Verified: 322 Python and 27 JS tests. Needs a restart.


### Audit phase 3: fewer pauses, fewer screenshots, safe key file (`9f0724d`)
- Smart Craft yield policy: pause at once only for
  `SMART_CRAFT_YIELD_PRIORITY` (`obelisk_fight,auto_prestige,lootbug`); other
  waiting workflows (bombs every 30 s) only after
  `SMART_CRAFT_YIELD_MIN_WAIT_S` (45 s). Before, every bomb run interrupted a
  cycle (close menu, reopen page, reselect 5x: 3-5 s each). New
  `state.workflows_waiting_since` (workflows and Lootbug). WebUI hints.
- Workflow conditions accept `use_prev_screenshot`; the bomb rotation's
  Plenty/Basic/Chain checks reuse the frame of the previous Fire step.
- `web/secret_key.py` retries the key file rename on `PermissionError`
  (same Windows pattern as the JSON saves; flaky test).
- Not built: drone point check (audit item 12, open item).
- Verified: 315 Python and 27 JS tests (three clean runs); engine dry run of
  the bomb rotation. Needs a restart (the running bot does not know the new
  condition field).


### Audit phase 2: reliability and logs (`1f89f2f`)
- `_craft_ready` needs a 1x/5x quantity button and no Workshop header; it
  accepted Workshop frames before (the Workshop's third cost column has bar
  icons in the Craft icon column), a risk with the new page polling. Saved
  frames: only the 4 Workshop frames change, all 15 Craft frames still pass.
- A Smart Craft cycle that only sold ore reports `sold_only` instead of `ok`.
- Every Smart Craft tap is logged to the file (TXT).
- JSON saves retry `os.replace` on Windows `PermissionError` (cause of the
  intermittent gem-statistics/secret-key test errors; a real risk for settings,
  workflows, statistics and the trip file).
- Screenshot log: one line per successful capture instead of two; log
  rotation 2 -> 5 MB, so the four files hold several times more history.
- Verified: 313 Python and 27 JS tests. Needs a restart.


### Audit phase 1: obelisk back-off, skip missing Craft rows, stop at locked rows (`3e8590b`)
- Audit (after the 11:19 restart): the obelisk was opened every minute only to
  find the damage warning; Smart Craft spent 9-14 s per round searching the
  Craft list for each bar not unlocked yet; every Workshop scan scrolled once
  into the locked rows ("list position unknown").
- New workflow step `defer` (validation, editor, tests): the next automatic
  run of the top-level workflow is not before now + seconds. Local workflow:
  `obelisk_fight_attack` damage-warning branch defers 900 s.
- Smart Craft remembers bars whose icon is on no Craft page for 30 min and
  skips them ("no Craft row yet (skipped): ...").
- `list_pages` stops once "Unlock At" (`images/smart_craft/unlock_at.png`) is
  visible below every Buy button; checked on all saved list frames (11 stop,
  33 continue, the "Unlocked" label case included).
- Verified: 310 Python and 27 JS tests; engine dry run of the obelisk defer
  (next check +900 s). Needs a restart (the running bot rejects the new step
  type until then).


### Branch history rewritten (messages only)
- At the user's request, the `Co-Authored-By` trailers were removed from all
  branch commits with `git filter-branch --msg-filter` and the branch was
  force-pushed. Files, authors and dates are unchanged; every commit since
  `main` got a new hash, and the hashes in this log and the development notes
  were remapped. Clones of the old branch need a reset to the fork.


### Docs and working rules (`d93c033`)
- AGENTS.md: lock-taking features must check the global pause; detect
  without the lock; workflow edits are validated and dry-run before reload;
  live checks only by log and screenshots; all JS tests via
  `node --test tests/*.cjs`; no co-author trailers on fork commits.
- Lootbug help text in the settings page describes the new flow.
- Development notes: restart checklist and the obelisk timer analysis (not
  built: needs its own digit references and a workflow step; the gain is at
  most about 60 s per 20 min cooldown).


### Workshop costs with a '4' readable; misread diagnostics (`565cd02`)
- Problem: Workshop rows were skipped 248 times ("cost ... unreadable"): the
  third cost column draws the '4' one sub-pixel shifted, which matched no
  glyph reference clearly. New reference `images/smart_craft/digits/4_3.png`
  from a live frame. Offline on 239 saved frames, only the failing frame's
  reads changed (two Workshop rows now read).
- Problem: "list position unknown after scrolling" stopped 101 scans early;
  no saved frames show it. The before/after frames (and frames with an
  unreadable Workshop cost) are now saved to `temp/smart_craft_debug/`
  (newest 40, git-ignored) for the next analysis.
- Verified: 305 Python tests (real-crop regression test fails without the new
  glyph). Not verified live.


### Faster screenshots with exec-out (`0ee6f38`)
- Each capture used three ADB calls (screencap to the device, pull, rm);
  Smart Craft and workflows take thousands of screenshots per day.
- New setting `SCREENSHOT_CAPTURE_METHOD` (`exec_out` default, `pull` = old
  pipeline; WebUI). `exec_out` streams the PNG in one call
  (`adb(..., binary=True)`); staging file, validation, device binding, atomic
  publish and the no-stale-fallback rule are unchanged. After an exec-out
  failure the process switches to `pull`.
- Benchmark (read-only, 10 each, median): exec_out 0.22 s, pull 0.35 s, raw
  0.27 s.
- Verified: 302 Python tests (screen cache: exec-out path, fallback to pull,
  invalid PNG never replaces the cache; existing tests pinned to `pull`).
  Not verified in the running bot (needs a restart).
- Follow-up commit: Smart Craft opens the main menu and its pages by polling
  every 0.25 s until the page is ready (up to 3 x `SMART_CRAFT_UI_DELAY_S`)
  instead of one fixed delay: earlier when the game is fast, more tolerant
  when it is slow. Other fixed delays are unchanged.


### Smart Craft crafts for the next purchase (`e9689c9`)
- Problem: each cycle turned all ore into bars (target = sum of every visible
  upgrade's need), switching 1x/5x per bar; cycles held the game 250-360 s and
  crafted 2179 bars for 38 purchases.
- New setting `SMART_CRAFT_CRAFT_TARGET` (`next` default, `all` = old
  behaviour; WebUI). `next`: per bar the target is the cheapest row's need;
  the round buys it and the next round crafts for the following one (up to
  `SMART_CRAFT_MAX_ROUNDS`). Leftover ore is crafted in 1x only if those
  presses can reach the target. Behaviour change: ore is kept as ore instead
  of being crafted ahead.
- Verified: 299 Python tests. Not verified live (needs a restart); watch cycle
  length and purchases per cycle.


### Lootbug is taken within seconds (`68c4361`)
- Problem: a lootbug stayed on screen for minutes although the run needs three
  taps. Scans ran every 180 s; a seen lootbug needed the action lock and only
  retried every 15 s, losing to bombs and Smart Craft (255 postpones in one
  log).
- Lootbug now scans without the action lock (screenshot only) and takes the
  lock only when a lootbug or an open Buy box is visible. If the lock is busy
  it waits up to the new `LOOTBUG_LOCK_WAIT_S` (45 s, WebUI), listed in
  `state.workflows_waiting` so Smart Craft pauses for it, then re-detects on a
  fresh frame. Default `LOOTBUG_CHECK_INTERVAL_S` 180 -> 15 s. The tap / Buy /
  close sequence is unchanged (fixed waits not replaced by polling: the saving
  would be 2-3 s and the existing checks depend on them).
- Verified: 297 Python tests (40 lootbug: no lock without a lootbug, waiting
  for and re-detecting after the lock, pause while waiting, zero wait). Not
  verified live (needs a restart).


### Floor trip timing in the log; drone purchase confirmed live (`33339b0`)
- Floor trips (request: "the last trip was very short"): no real trip had
  ever run - every attempt returned within 7 s because the Auto toggle was
  unreadable (fixed in `267d269`). Trips now log their planned return time and
  the Auto state at the start, and the real duration at the end
  (`started` is stored in `smart_craft_trip.json`; older files still load).
- Drone purchase: confirmed live at 10:50 with the swipe area
  [80,420,820,1350] - Drone Upgrades tab shown, Max and Exchange hit, prestige
  done.
- Verified: 294 Python tests. Floor trips still need a restart and a live trip.


### Pause after current (`1cfc7ee`)
- Request: a WebUI option to pause the automation queue after the current
  automation has finished.
- New dashboard button **Pause after current** (command
  `automation_pause_toggle`, runtime flag `state.automation_paused`). All
  lock-taking features (Workflows, Smart Craft, Lootbug, Freebies, Founders,
  Notices) check it before taking the action lock; the running action
  finishes. Queued manual workflow runs wait for Resume. Level detection keeps
  running. Not persisted.
- Verified: 294 Python and 27 JS tests (all `tests/*.cjs`; scheduler, every feature, button
  states). Not verified live (needs a restart).


### Smart Craft: optional gold upgrades (`e1744e8`)
- Request: an option to enable or disable upgrades that cost gold.
- In game each upgrade costs either bars or gold; Smart Craft only ever bought
  bar rows. New setting `SMART_CRAFT_BUY_GOLD_UPGRADES` (default off = never
  spend gold, as before; WebUI hint). On: after the bar rows of each page, buy
  every gold row whose Buy button is lit; a purchase counts only when a fresh
  frame shows the row unaffordable or its cost text changed. Off also skips a
  row that would cost bars and gold (none seen; before, such a row would have
  been bought).
- Verified: 287 Python and 14 JS tests; offline the gold reader finds the gold
  rows on four real frames (all unaffordable). Not verified live.


### Floor Auto restored after floor trips; bombs, drones and menus (`267d269`)
- Problem: floor auto-descend was off. Every Smart Craft floor trip since at
  least 08:00 moved 19 -> 11 and straight back (6-7 s) with "floor Auto toggle
  not recognised": the off state is dark maroon, the reader only knew bright
  red. Auto was also read after the move, when picking a floor had already
  switched it off (inferred), so it would never have been switched back on.
- Smart Craft: `_auto_kind` reads dark red as off; `floor_auto_on()` reads Auto
  before the move; an undone trip switches Auto back on.
- Problem: auto prestige failed in all 30 runs since 05:00 (Drones page on the
  Drone Suits tab) and left the Drones menu open, so bombs and obelisk checks
  matched nothing until Smart Craft closed it. The bomb rotation (5 rounds,
  57 s every 90 s) held the action lock 63% of the time.
- Local workflows: see the table below (bombs every 30 s with a leftover-menu
  close, drone tab/swipe/cleanup).
- Verified: 282 Python and 14 JS tests; real frames classify Auto on/off/menu
  correctly; offline engine dry run of the drone and bomb workflows; live: bomb
  runs take 10-11 s, a failed drone step closes the menu (10:29). Not yet
  verified: a live floor trip (needs a restart), a successful drone purchase.

## 2026-11-23

### Smart Craft pauses for waiting workflows (`431e6ab`)
- Problem: Smart Craft held the action lock for 5-8 min per cycle; the
  obelisk check ran only every 8-14 min (interval 250 s) and the bomb
  rotation had ~10 min gaps.
- Busy auto workflows are recorded in `state.workflows_waiting`; Smart Craft
  pauses before each page and between bars, closes the menu, lets them run
  (`workflows.retry_waiting_now()`), takes the lock back and continues with
  its scan. New setting `SMART_CRAFT_YIELD` (default on, WebUI).
- Verified: unit tests (280 Python, 14 JS). Not yet observed live (needs a
  process restart).

### Safe drone-point purchase before prestige (`f68f2ee`)
- Problem: the 23:12 prestige ran with unspent gold. The Drones page keeps its
  scroll position; one swipe did not reach the top and the fixed-coordinate
  Max/exchange taps missed.
- New templates `menu/permanent/drones/drone_max_tpl.png` and
  `drone_exchange_tpl.png` (1.0 on the reference frame, <= 0.69 elsewhere).
- Verified: offline template scoring only.

### Prestige page scroll position, Drone Points (`9014f3d`)
- Documented that the Prestige page keeps its scroll position (the Prestige
  box is at the top) and that Drone Points survive prestige (wiki).

### Freebies close template (`e85132e`)
- Problem: the shop stayed open after a claim; the shipped `close_menu.png`
  scores 0.655-0.71 on this layout.
- `FREEBIES_CLOSE_TEMPLATE` is configurable (validation, overrides, API,
  WebUI hint); default unchanged (`close_menu.png`).
- Verified: unit tests; offline, `workflows/menu/close_menu_tpl.png` scores
  1.0 on the stuck frame. Live claim after a restart not yet observed.

### Bomb rotation and obelisk button (`0f17022`, `7daae08`)
- Documented the single 5-round bomb rotation and the fixed-position obelisk
  HUD tap (the button icon changes with every obelisk level).

### Workshop, Lootbug popup, Claim Victory, single instance (`3174698`)
- Smart Craft reads and buys permanent Workshop upgrades (Upgrades first).
- Lootbug: optional `LOOTBUG_CLOSE_POPUP` taps the popup X after a verified
  Buy; templates `Lootbug/lootbug_free.png`, `lootbug_popup_close.png`.
- Obelisk: `obelisk/claim_victory_tpl.png` for the post-victory screen.
- `run_server` refuses to start a second bot when port 6001 is taken.

### Development notes (`e46aed6`)
- Added [DEVELOPMENT_NOTES.md](DEVELOPMENT_NOTES.md) with verified game facts,
  wiki references, the local workflow setup and verification methods.

### Smart Craft and early-game support (`40f212d`)
- New feature Smart Craft: reads bar-cost upgrades, crafts missing bars
  (5x with 1x fallback), buys upgrades; optional floor trips and surplus-ore
  selling. Bar, ore, digit and page templates under `images/smart_craft/`.
- Obelisk, prestige and HUD workflow templates under `images/workflows/`.

## Local Configuration Changes (not versioned)

Backups of each step are kept in the session scratchpad, not in the repo.

| Date | File | Change |
| --- | --- | --- |
| 2026-11-23 | `user_workflows.json` | `auto_prestige` triggers on the "Level Capped" HUD template; unconditional floor-Auto tap removed. |
| 2026-11-23 | `user_workflows.json` | `prestige` opens the page, scrolls to the top, and only then calls the new helper `prestige_execute` (Sell All, drone points, Prestige). |
| 2026-11-23 | `user_workflows.json` | `buy_drone_points`: three fast swipes to the top, Max and exchange by template (a miss aborts the prestige). |
| 2026-11-23 | `user_workflows.json` | `obelisk_fight_attack`: fixed tap (843,440), Claim Victory branch. |
| 2026-11-23 | `user_workflows.json` | `bomb_rotation_early_game`: single 5-round rotation; other bomb rotation inactive. |
| 2026-11-23 | `user_workflows.json` | Intervals: `obelisk_fight` 60 s (user had set 250 s), `bomb_rotation_early_game` 90 s. |
| 2026-11-23 | `user_settings.json` | Lootbug Free/close templates and `LOOTBUG_CLOSE_POPUP`; `FREEBIES_CLOSE_TEMPLATE = workflows/menu/close_menu_tpl.png`. |
| 2026-11-23 | `user_settings.json` | Set by the user: `SMART_CRAFT_FLOOR_TRIPS`, `SMART_CRAFT_SELL_SURPLUS_ORE` on, `SMART_CRAFT_INTERVAL_S` 300. |
| 2026-11-24 | `user_workflows.json` | `bomb_rotation_early_game`: close a leftover menu first, then one round per run, interval 30 s (was 5 rounds every 90 s). |
| 2026-11-24 | `user_workflows.json` | `buy_drone_points`: tap the "Drone Upgrades" top tab; swipe area [80,420,820,1350]; Max/Exchange optional, each followed by `if last_step_failed` -> close menu and abort. |
| 2026-11-24 | `user_workflows.json` | `buy_drone_points` swipe area [80,420,820,1350] (the default area started the finger on the Drone Suits tab); confirmed live 10:50. |
| 2026-11-24 | `user_settings.json` | `LOOTBUG_CHECK_INTERVAL_S` 180 -> 15 (scans no longer take the action lock). |
| 2026-11-24 | `user_workflows.json` | `obelisk_fight_attack`: `defer` 900 s in the damage-warning branch (needs the restart that brings the new step type). |
| 2026-11-24 | `user_workflows.json` | `bomb_rotation_early_game`: Plenty/Basic/Chain conditions `use_prev_screenshot` (needs the restart). |
| 2026-11-24 | `user_settings.json` | `startup_defaults.freebies_enabled` and `lootbug_enabled` true (both stayed off after every restart). |
| 2026-11-24 | `user_settings.json` | `FREEBIES_DIAMONDS_PER_CLAIM` 28 -> 5 (the pack gives 5; earlier gem statistics are overcounted). |
| 2026-11-24 | `user_workflows.json` | `buy_drone_points`: `read_number` of the drone point counter before Max and after Exchange; unreadable or not increased -> close menu, abort (needs the restart). |
| 2026-11-24 | `user_settings.json` | `SMART_CRAFT_BUY_GOLD_UPGRADES` true (set by the user in the WebUI; gold rows are bought with the bar upgrades). |
| 2026-11-25 | `user_workflows.json` | `bomb_rotation_early_game`: four `if template_not_visible(count_zero_tpl)` drain steps after Basic (wait 2.4 s + Fire); reloaded live 06:00. Backup before the edit in the session scratchpad. |
| 2026-11-25 | `user_workflows.json` | `obelisk_fight`: `use_items` pickaxe items (cooldown key `obelisk`) before the fight; `obelisk_fight_attack` damage-warning branch: `item_cooldown obelisk 7200`; `bomb_rotation_early_game`: `use_items chaos_totem` (reserve 2). Inactive until `ITEMS_ENABLED`; needs the restart that brings the new step types. Backup in the session scratchpad. |
| 2026-11-27 | `user_settings.json` | `ITEMS_COMBINE_SKILL_BITS` true, `ITEMS_SKILL_TREE` true, `ITEMS_SKILL_TREE_INTERVAL_S` 1800, `ITEMS_SKILL_TREE_DRY_RUN` true (no node is bought until dry run is switched off). Backup in the session scratchpad. |
| 2026-11-27 | `user_settings.json` | `FREEBIES_CHECK_INTERVAL_S_SLOW` 2700 -> 900 (Store visits every 15 min; the pack banks two 10-min claims). Backup in the session scratchpad. |
| 2026-11-25 | `user_workflows.json` | `bomb_rotation_early_game`: Infinity Bomb (select + Fire) after Plenty, plus four drain steps on the slot-5 count (ROI [510,1245,550,1285]); reloaded live. Backup in the session scratchpad. |
