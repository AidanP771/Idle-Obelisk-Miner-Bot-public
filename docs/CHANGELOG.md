# Change Log (fork)

Changes on the fork branch `smart-craft-early-game`
(<https://github.com/AidanP771/Idle-Obelisk-Miner-Bot-public>), based on
upstream `main` at `7aec193`. Newest first. Each entry names the commit, what
changed for the user, and how it was verified. Game facts and research behind
the changes are in [DEVELOPMENT_NOTES.md](DEVELOPMENT_NOTES.md); behavior is in
[PROJECT_CONTEXT.md](PROJECT_CONTEXT.md).

Local, unversioned changes (`config/user_workflows.json`,
`config/user_settings.json`) are listed separately because git ignores them.

## 2026-11-24

### Pause after current (this entry's commit)
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


### Smart Craft: optional gold upgrades (`617e8e0`)
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


### Floor Auto restored after floor trips; bombs, drones and menus (`9f734b3`)
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

### Smart Craft pauses for waiting workflows (`986be36`)
- Problem: Smart Craft held the action lock for 5-8 min per cycle; the
  obelisk check ran only every 8-14 min (interval 250 s) and the bomb
  rotation had ~10 min gaps.
- Busy auto workflows are recorded in `state.workflows_waiting`; Smart Craft
  pauses before each page and between bars, closes the menu, lets them run
  (`workflows.retry_waiting_now()`), takes the lock back and continues with
  its scan. New setting `SMART_CRAFT_YIELD` (default on, WebUI).
- Verified: unit tests (280 Python, 14 JS). Not yet observed live (needs a
  process restart).

### Safe drone-point purchase before prestige (`779e4db`)
- Problem: the 23:12 prestige ran with unspent gold. The Drones page keeps its
  scroll position; one swipe did not reach the top and the fixed-coordinate
  Max/exchange taps missed.
- New templates `menu/permanent/drones/drone_max_tpl.png` and
  `drone_exchange_tpl.png` (1.0 on the reference frame, <= 0.69 elsewhere).
- Verified: offline template scoring only.

### Prestige page scroll position, Drone Points (`65e4ef3`)
- Documented that the Prestige page keeps its scroll position (the Prestige
  box is at the top) and that Drone Points survive prestige (wiki).

### Freebies close template (`d37898f`)
- Problem: the shop stayed open after a claim; the shipped `close_menu.png`
  scores 0.655-0.71 on this layout.
- `FREEBIES_CLOSE_TEMPLATE` is configurable (validation, overrides, API,
  WebUI hint); default unchanged (`close_menu.png`).
- Verified: unit tests; offline, `workflows/menu/close_menu_tpl.png` scores
  1.0 on the stuck frame. Live claim after a restart not yet observed.

### Bomb rotation and obelisk button (`55c3489`, `a12a32f`)
- Documented the single 5-round bomb rotation and the fixed-position obelisk
  HUD tap (the button icon changes with every obelisk level).

### Workshop, Lootbug popup, Claim Victory, single instance (`b423cf2`)
- Smart Craft reads and buys permanent Workshop upgrades (Upgrades first).
- Lootbug: optional `LOOTBUG_CLOSE_POPUP` taps the popup X after a verified
  Buy; templates `Lootbug/lootbug_free.png`, `lootbug_popup_close.png`.
- Obelisk: `obelisk/claim_victory_tpl.png` for the post-victory screen.
- `run_server` refuses to start a second bot when port 6001 is taken.

### Development notes (`8680df4`)
- Added [DEVELOPMENT_NOTES.md](DEVELOPMENT_NOTES.md) with verified game facts,
  wiki references, the local workflow setup and verification methods.

### Smart Craft and early-game support (`592fd62`)
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
