# Project Context: Idle Obelisk Miner Bot

Updated: 2026-10-24. Based on existing source code, local configurations,
`README.md` and the former project overview. Primarily a static inventory; the screenshot layer
described below was also checked on the emulator. Local settings and workflows
may change independently of versioned code. Game facts measured in game, the
game wiki (<https://shminer.miraheze.org/wiki/Obelisk_Miner_Wiki>) and the
local workflow setup are recorded in [DEVELOPMENT_NOTES.md](DEVELOPMENT_NOTES.md).

## Purpose and Operation

The Python bot automates the Android game **Idle Obelisk Miner** through ADB,
primarily on Windows with MEmu or MuMu. It captures screenshots, detects game
elements, and performs clicks and drag scrolling. The Flask dashboard controls
features and workflows and displays status, logs, screenshots and statistics.
According to the README, the project is unpublished and used privately.

Detection primarily uses OpenCV template matching within fixed regions of
interest (ROIs), without an OCR library. Levels are assembled from digit
templates; Rainbow Floors are detected using HSV values and hue distribution.

## Architecture and Module Dependencies

| Component | Responsibilities and Main Connections |
| --- | --- |
| `main.py` | Sets the working directory to the project root and starts `web.app.run_server()`. |
| `tray_runner.py` | Starts the server in the background and provides the Windows tray menu for the WebUI, logs, restart, exit and local configuration reset. |
| `web/app.py` | Flask/Socket.IO server, command queue, bot thread, status broadcasts every second and dashboard endpoints. `run_server` refuses to start a second bot when the dashboard port is already in use (waits up to 5 s for a restarting instance). |
| `bot_app.py` | Connects ADB, initializes runtime values, starts feature threads, processes commands and checks game state. The source sets `MAIN_LOOP_TICK` to 1.5 seconds. |
| `config/settings.py`, `user_overrides.py` | Central defaults and application of `config/user_settings.json` at import time. |
| `config/app_state.py`, `runtime_flags.py` | Shared mutable runtime state, action lock and exception-handling flag propagation. |
| `config/config_workflows.py` | Default workflows, logging configuration and loading of local workflow definitions. |
| `core/adb_helper.py` | ADB discovery, connection, emulator port scanning and reconnection for certain errors; used by screenshot and input helpers. |
| `core/screen_cache.py`, `game_state.py` | Isolated full-device Android captures, atomic per-slot screenshot files in `temp/`, validated cache and serialized game detection. `core/screenshot.py` uses the same capture pipeline as a compatibility helper. |
| `core/vision.py`, `rotation_cache.py` | Grayscale image matching, cached rotated templates, masking and downscaled matching. Templates reside in `images/`. |
| `core/tapper.py`, `scroll.py` | Central click and scroll primitives with configurable timing, coordinate jitter and drag gestures. |
| `features/` | Independent workers for Freebies, Lootbugs, Founders, Notices, Smart Craft, level detection and workflow scheduling; gem statistics with JSON persistence. |
| `web/config_api.py`, `state_builder.py`, `security.py` | Configuration/statistics API, dashboard state construction and LAN/origin access checks. |
| `web/templates/`, `web/static/` | HTML/CSS and JavaScript dashboard without an apparent frontend build step. The workflow editor comprises six scripts loaded together. |

**Data flow:** Browser -> HTTP/Socket.IO command -> `command_queue` ->
`bot_app.py` -> feature control. Feature -> screenshot/image detection ->
ADB input -> runtime state/logging -> dashboard.

`state.cfg` references `config_workflows` and supplies the logger with settings
including file path and enablement. The logger connects to the WebUI through
lazy imports to avoid circular imports. Level detection writes
`state.current_level`; workflow conditions read this value.

## Implemented Features

- **Notices:** independent cache-first worker and Controls switch. Saves color
  notice crops before a single dismiss tap, then checks fresh screenshots until
  no notice remains. Default interval: 45 minutes +/-15%; operational settings
  are editable in WebUI, startup is OFF. Two masked frame variants support the
  supplied level and decorated shard boxes at 900 x 1600. Persistent inbox below
  Last Screenshot, manual acknowledgement, animated deletion, preview and a
  cache-first detection test with fresh-capture fallback. Shared action lock, bounded chains and protection
  against blind repeated taps. No OCR. Details: [Notices](NOTICES.md).
- **Freebies:** periodic detection using two icon templates, menu opening,
  scrolling to find the claim button, claiming, counting and closing the menu.
  The close button is matched with `FREEBIES_CLOSE_TEMPLATE` (default
  `close_menu.png`) inside `FREEBIES_CLOSE_ROI`; layouts with a light-edged X
  need `workflows/menu/close_menu_tpl.png`, otherwise the shop stays open.
  Enabling wakes the worker for a prompt cycle.
  Startup and each runtime activation use `FREEBIES_CHECK_INTERVAL_S_FIRST`
  (default 300 seconds) until either available-icon template is detected.
  The first detection immediately selects `FREEBIES_CHECK_INTERVAL_S_SLOW`
  (default 2700 seconds / 45 minutes), even if the subsequent claim fails.
  A scan without either available icon returns to FIRST (fast checks) until
  the next detection selects SLOW again. Missing screenshots do not change
  the phase; cancelled or older activation scans cannot reset it.
  Restart or reactivation resets to FIRST. The countdown uses the chosen
  delay; game-absent retries remain capped at 30 seconds. Freebies still waits
  for the shared action lock. Logs show startup intervals, FIRST on activation,
  transitions to FIRST after icon misses and to SLOW after detection,
  action-lock waits and next-check delay,
  mode and reason (`ok`, `no_game`), routed via `FREEBIES_LOG_LEVEL` (default
  TXT, file only). Claim accounting and statistics activation are unchanged.
  Both intervals are editable in the WebUI and require a process restart.
  Legacy `FREEBIES_CHECK_INTERVAL_S` migrates to FIRST, explicit new values win,
  and SLOW uses its default when absent. Migration does not modify files until
  settings are saved.
- **Lootbugs:** detect `lootbug_tpl_*.png`, click, wait and capture a fresh
  screenshot; then find `Lootbug/lootbug_buy_bonus.png` within
  `LOOTBUG_BONUS_BUY_ROI` and tap at the match center. Wait
  `LOOTBUG_POST_CLICK_DELAY_S` (default 2 seconds) before the first Buy scan. Buy ROI:
  `(310, 370, 505, 672)`. If notifications obscure the Buy box, retry with fresh
  screenshots every `LOOTBUG_BUY_RETRY_S` (default 3 seconds), up to
  `LOOTBUG_BUY_MAX_ATTEMPTS` (default 5; integer 1-100). Missing screenshots
  and failed Buy taps also consume attempts. After each successful ADB Buy tap,
  wait `LOOTBUG_BUY_RETRY_S` and verify with a fresh own-slot screenshot. A still
  visible Buy box or missing verification frame triggers another bounded attempt.
  Count once only when that fresh post-tap frame no longer matches Buy; this
  observes disappearance, not the actual reward. No Close tap is issued unless
  `LOOTBUG_CLOSE_POPUP` is enabled (default off): then, after a verified Buy and
  after exhausted attempts, a fresh frame is checked for
  `LOOTBUG_POPUP_CLOSE_TEMPLATE` in `LOOTBUG_POPUP_CLOSE_ROI` and the X is tapped
  if visible. Game layouts differ: the reference setup of
  [DEVELOPMENT_NOTES.md](DEVELOPMENT_NOTES.md) shows a "Free" offer instead of
  "Buy Checked" and uses `Lootbug/lootbug_free.png` as Buy template.
  When no Lootbug is detected, also check for an existing Buy box and resume
  its bounded search, including after a Founders interruption. Cached detections
  are only hints; purchase taps always require a fresh match. Exhaustion aborts this cycle and
  releases the action lock; the next regular scan uses the normal interval unless
  Founders has become due. Optional `LOOTBUG_TRIGGER_FOUNDERS_ON_BUY_FAILURE`
  (default OFF, editable in the Lootbug settings; process restart required)
  triggers Founders only after all Buy attempts are exhausted. When Founders
  automation is enabled, reset its deadline and wake its worker for an immediate
  check after Lootbug releases the lock. When disabled, run one bounded manual
  Founders chain under the already held action lock, starting with a fresh
  `founders_scr` capture. The manual chain uses the Founders template, ROI,
  tap settings and `FOUNDERS_MAX_CLAIMS_PER_CYCLE`; it never enables automation
  or leaves automatic follow-up retries while disabled. Lootbug disable/stop
  cancels recovery at action boundaries. Successful Buy dismissal and early
  cancellation do not trigger recovery. Existing settings files need no migration
  and keep the previous behavior until the option is enabled. No local user
  settings are changed automatically.
  Disabling/stopping or an enabled Founders check becoming due aborts early.
  The action lock stays held only during this bounded search. There is
  no automatic closing; obsolete close settings are removed.
  The switch is ON/OFF only; the bot no longer selects bonuses or double-clicks.
  Regular scan intervals use `LOOTBUG_INTERVAL_VARIANCE_PCT` (default +/-15%,
  0 means fixed, bounded to 0-100%). After each cycle the worker draws exactly
  one delay and publishes the same target time as `lootbug_next_check_ts`; the
  status countdown derives from it. Busy, game-absent and Buy retries retain
  their separate delays without this variance.
  Enabled Founders checks due within `LOOTBUG_FOUNDERS_LOOKAHEAD_S` (default
  5 seconds), overdue/uninitialized checks and pending Founders follow-ups
  take priority. Lootbug yields without holding the action lock, retrying its
  scheduling check after at most one second without interval variance. The
  priority is rechecked after acquiring the lock and during Buy retries.
  A zero lookahead still yields to due/pending checks; disabled Founders does
  not block Lootbug. Attempt numbers, exhausted attempts and priority yields
  are logged. Both new settings are configurable in the WebUI.
  With the feature disabled, select the desired bonuses in the game and confirm
  with Buy; the game remembers the selection. Enable **Remove lootbug gem
  confirmation** in the game settings. Old bonus-selection settings are removed
  on load/save; the previous default Buy ROI migrates to the new value. Other
  custom ROIs are preserved.
- **Founders:** independent worker in `features/founders.py`, with its own
  switch, status tile, counters and countdown. The **Founders** WebUI section
  (`founders`, `FOUNDERS_*`) provides separate scan interval, detection and click
  settings. Independent of the Lootbug switch; both still share
  `state.ingame_action_lock`.
  Flow: detect `Founders/founders_bundle_tpl.png` and click once; immediately
  force a fresh screenshot in `founders_scr` after every successful tap and
  handle any further bundle under the same action lock. No shared cached frame
  is used for follow-ups and there is no post-click sleep or closing logic.
  `FOUNDERS_MAX_CLAIMS_PER_CYCLE` (default 3; integer 1-100) caps each chain;
  even its final tap gets a fresh recheck. If a detected bundle tap fails,
  a bundle remains or that fresh capture fails, release the lock and schedule a short retry capped by
  `FOUNDERS_BUSY_RETRY_S`; Lootbug continues yielding to the pending follow-up.
  Failed taps never count or start another immediate recheck. Disable, stop
  and reactivation stop the current chain at execution boundaries. The cap is
  editable in the WebUI. Its template resides in `images/Founders/`,
  so Lootbug image changes do not affect Founders.
  Legacy `lootbug_founders` settings migrate to `founders` on load/API access;
  previously inherited Lootbug overrides are copied, and explicit Founders
  values take precedence. Saving writes only the new format and removes
  obsolete Founders popup settings.
  `FOUNDERS_ENABLED` belongs to **Startup Defaults** (`startup_defaults`) and
  controls the initial value; the dashboard switch only changes
  `state.founders_enabled` at runtime.
  Startup and each runtime activation use `FOUNDERS_CHECK_INTERVAL_S_FIRST`
  (default 180 seconds) until the first template detection. That detection
  immediately selects `FOUNDERS_CHECK_INTERVAL_S_SLOW` (default 3000 seconds),
  including when the tap fails; failed taps still do not increase counters.
  A later regular scan without a bundle returns to FIRST (fast checks) until
  the next detection selects SLOW again. Immediate fresh rechecks after taps
  do not reset SLOW; unavailable screenshots do not change the phase.
  Restarting or disabling and enabling resets
  the phase and activation wakes the worker for a prompt scan. Busy/game-absent
  retries keep their shorter delays. Both intervals are editable in the WebUI
  and require a process restart. Legacy `FOUNDERS_CHECK_INTERVAL_S` migrates
  to FIRST; explicit new values win and SLOW uses its default when absent.
  Logs show startup interval values, FIRST on reactivation, transitions back to
  FIRST after misses and to SLOW after detection, and each next-check delay with mode and scheduling reason
  (`ok`, `busy`, `no_game`, `pending`), fresh rechecks and chain limits.
  These use the configured Founders log routing;
  busy scheduling uses `FOUNDERS_LOG_LEVEL_BUSY`.
- **Smart Craft:** independent worker in `features/smart_craft.py` with its
  own switch (`smart_craft_toggle`), status tile (verified bars crafted and
  upgrades bought; last cycle result as tooltip) and countdown. Settings live
  in the `smart_craft` section (`SMART_CRAFT_*`); `SMART_CRAFT_ENABLED` belongs
  to **Startup Defaults**. One cycle holds `state.ingame_action_lock`
  throughout: open main menu -> Upgrades, scroll from the top through the
  list and read every row that has a Buy button and a bar cost (bar icon +
  `have/need`), stopping at the locked "Unlock At Level" section. Per bar
  type the target is the sum of all needs. Rows with two costs (two bars, or
  bar + gold per the game wiki) are read up to the next cost icon (bar
  templates plus `images/smart_craft/coin.png`). Craft page: switch to 5x
  (`SMART_CRAFT_USE_5X`, `qty_5x.png`; green = active, the game keeps the
  mode), locate each bar's row (scrolling if needed), press its Craft button in batches
  (`SMART_CRAFT_MAX_BATCH`, capped by `SMART_CRAFT_MAX_PRESSES_PER_BAR` and by
  the presses the shown ore covers) and
  re-read the row after every batch. When the ore no longer covers one 5x
  press, the rest is crafted in 1x (`qty_1x.png`; the switch counts only if the
  row's ore cost drops) and 5x is selected again afterwards. One batch without
  progress is retried
  once (the game occasionally drops a press). Then (`SMART_CRAFT_BUY_UPGRADES`) tap
  Buy on each affordable bar-cost row (bright button and `have >= need`), up
  to `SMART_CRAFT_MAX_BUYS`. Buying raises the next level's cost, so the
  read -> craft -> buy round repeats while purchases happen, up to
  `SMART_CRAFT_MAX_ROUNDS` (default 3); then the menu is closed. Bars are never sold.
  Gold (`SMART_CRAFT_BUY_GOLD_UPGRADES`, default off): in game an upgrade costs
  either bars or gold. Off, no row with a gold cost is read or bought, so gold
  stays for drone points. On, after the bar rows of each list page, every
  gold-cost row with a lit (affordable) Buy button is bought (`read_gold_rows`:
  coin icon and no bar icon in the row). The gold amount is not parsed; a
  purchase counts when a fresh frame shows the row no longer affordable or its
  cost text changed, otherwise buying stops.
  Pausing (`SMART_CRAFT_YIELD`, default on): a cycle holds the action lock for
  minutes. Auto workflows that find the lock busy are recorded in
  `state.workflows_waiting` (slug -> time, entries older than 30 s are ignored).
  Before each page it opens and between bars, Smart Craft checks that list; when
  a workflow waits, it closes the menu (only if the X is gone afterwards),
  releases the lock, calls `workflows.retry_waiting_now()` so they run at the
  next tick, waits until none waits (max 300 s), takes the lock back and
  reopens its page. Scan results stay in memory, so the cycle continues
  instead of rescanning. A cancel during the pause ends the cycle without taps.
  Workshop (`SMART_CRAFT_WORKSHOP`, default on): each round also opens
  Permanent -> Workshop (`workshop_header.png`) and reads the buyable rows
  (up to three bar costs, "have/" above "need"; icons grouped per row). Their
  costs join the Upgrades rows, so the crafting target per bar is the sum of
  both. Buying keeps the user-chosen priority: regular Upgrades first, then
  every Workshop row whose costs are all met and whose Buy button is bright,
  verified on a fresh frame. Workshop upgrades are permanent (survive prestige).
  Surplus ore (`SMART_CRAFT_SELL_SURPLUS_ORE`, default off): only when the
  last round bought nothing (so its upgrade scan is current), the Sell page is
  opened (`sell_all_label.png`). Rows are found by their red Sell button
  (dark = locked) and identified by colour template matching of the small ore
  icon (`images/smart_craft/ores/`, best >= 0.8 and 0.15 ahead of the next).
  Ores of bar types some buyable upgrade costs, and ores without a bar
  template, are locked; an unrecognised row aborts before selling. Sell All is
  tapped twice (first tap arms it, verified in game 2026-11-23; a row's own
  Sell button sells only one ore). The rows locked by the bot are unlocked
  again; rows the player locked are never touched. A sale counts if a sold
  row's count box changes. Gold is not spent here.
  Floor trips (`SMART_CRAFT_FLOOR_TRIPS`, default off): when crafting stopped
  for lack of ore, the missing ore per bar is estimated and, unless the current
  floor drops one of those ores, Smart Craft opens Floor Select (HUD button,
  `images/smart_craft/floors/`), reads the World 1 buttons by colour (fixed
  two-column grid, 21 rows; red = unlocked, green = current), moves to the lower
  floor with the best weighted ore share (`FLOOR_ORE_SHARES`: floors 1-20,
  first floor of a pair 80/20, second 50/50 per the wiki), confirms the new
  green floor, and switches the floor Auto toggle (below the HUD button, green =
  on, dark red = off) off. Auto is read before the move, because picking a
  floor can switch it off by itself. If Auto cannot be read, nothing moves; if
  it cannot be switched off, it moves back at once and switches Auto on again
  when it was on. After
  `SMART_CRAFT_FLOOR_TRIP_S` the next cycle crafts and buys first, then returns
  to the origin floor and restores Auto. Switching Smart Craft off also returns.
  The trip is stored in `smart_craft_trip.json` (project root, git-ignored) so
  a restart still returns. Only World 1 without a scrolled panel is supported;
  the first live floor change has not been verified yet (a confirmation dialog
  after the floor tap would abort the trip with "floor change not confirmed"). The in-game **Buy All** button is not used:
  its first tap only arms it (orange), a second tap buys every affordable
  upgrade.
  Recognition (900x1600 portrait, English): `images/smart_craft/bars/*.png`
  are the large bar icons (Craft output icon = Upgrades cost icon). All bars
  share one sprite in different colours, so a shape match finds an icon and
  the mean colour picks the bar; icons farther than 20 from every known
  colour (dimmed/locked icons, bar types without a template) are ignored.
  Adding a PNG adds a bar type. Numbers: the game renders text at roughly
  1/1.8 scale and upscales it, so glyph pixels vary with position. Glyphs are
  segmented as connected components with a relative brightness threshold
  (core strokes only, excluding the grey drop shadow), described by 3x5 ink
  zones and matched against reference variants in
  `images/smart_craft/digits/<char>_<n>.png`. A glyph is accepted only if its
  nearest reference is within 0.7 and clearly closer than any other character
  (`SMART_CRAFT_GLYPH_MAX_RATIO`, default 0.75); unknown glyphs or stray blobs
  (e.g. "1.2k") make the whole number unreadable, and that row is skipped.
  Safeguards: Upgrades rows and Craft bar counts must read identically on two
  consecutive frames (numbers animate after changes); the Craft bar count must
  equal the Upgrades `have` value before crafting; a batch without progress or
  with an implausible change (> 50 bars per press) stops crafting; purchases
  count only if a fresh frame shows the row changed. List scrolling measures
  the real content shift (drags overshoot by momentum) from a textured band
  and treats repetitive matches as unknown, ending the scan with the rows read
  so far. Verified on the emulator (2026-11-22, level 37-42, World 1 with
  seven bar types; 2026-11-23 offline on saved frames for Topaz, Obsidian,
  Demonite, VR-Tin and VR-Amethyst, e.g. the Demonite cost of Upgrade
  Pickaxe). Bar types without a template are invisible to the scan; other worlds, larger numbers with suffixes and other
  resolutions are not supported yet.
- **Level detection:** periodic scan, digit assembly for levels 1-999, filtering
  of horizontally overlapping matches, display and manual scan. Templates for
  digits 0-9 exist in the inspected files.
- **Workflows:** individual intervals and random variance, automatic/manual
  execution, master switch, per-workflow activation, status and counters.
  Steps: `tap_roi`, `tap_xy`, `swipe`, `click_template`, `click_rotating`, `wait`,
  `rainbow_check`, `call_workflow`, `hold_and_wiggle` and `if`.
  Supports repeats, long presses, optional image matches and If/ElseIf/Else
  branches based on image visibility, level or the previous step result.
- **Workflow editor:** creation, duplication, deletion, sorting, drag-and-drop,
  step comments, disabled steps and hierarchical template selection.
  `dashboard-workflow-editor.js` is an intentionally retained placeholder;
  `web/templates/index.html` lists the scripts actually loaded.
- **Gem statistics:** Freebie yields, hourly buckets, session/all-time counters,
  24-hour/7-day summaries, manual additions and reset. Uptime and claim recording
  pause with the Freebie switch; the dashboard greys values and shows
  **Freebies inactive**. Wall-clock windows continue while paused. The statistics
  period starts on first activation, independently of the first claim. All-time
  uptime is a permanent sum even after hourly pruning. The amount per Freebie
  comes from configuration, not a measurement of the actual game balance.
  Lifecycle, legacy migration and verification: [Statistics](STATISTICS.md).
- **Operation:** ADB reconnection, screenshot viewer, log streaming/filtering,
  rotating file log, and restart through the dashboard or tray.

Default workflows include Bomb Rotation, Prestige, Drone Switch,
Upgrades/Contracts and Rainbow Floor Bombs. The inspected local
`user_workflows.json` also contains a Stargazing Collector. Definitions document
the available flow, not its current reliability in the game.

## Dashboard Scan Countdowns

The Freebie, Lootbug, Founders, Level and Notices status tiles use a fixed,
single-line footer: a slim progress track and compact `m:ss` / `h:mm:ss`
countdown. Disabled scans hide the entire progress/countdown footer; due scans show `Ready`.
The Notices tile shows red `OFF` while disabled and the saved notice count while
enabled. The inbox count and saved notices remain available while disabled.
Level setup issues use `Setup`. Last-detection/scan ages and errors remain in
the footer tooltip and accessible label instead of extending the tile height.
All visible countdown text stays dim grey, including Ready; status colors apply
to the bar. Countdown initialization replaces the entire footer even when a
cached older template still nests the timer beside legacy last-scan text.
Tiles use content-driven height without an imposed minimum height; the compact
footer keeps only four pixels of spacing above the progress track and timer.

Existing one-second Socket.IO state updates drive the display. Footer nodes
are created once, and subsequent updates change only text, attributes and a
CSS transform. Unchanged status value markup is retained. There is no added
polling loop or full status-panel replacement. Progress fills from the first
observed remaining time for a deadline to its expiry; a changed deadline or
feature reactivation resets it. Reloading the page starts a new observation.
The remaining time always comes from the worker's published schedule, including
variance and busy retries. The Notices state now also exposes `next_check_ts`.
Reduced-motion preferences disable the short transform transition.

Regression tests: `tests/test_dashboard_status.cjs` and
`tests/test_dashboard_notices.cjs` cover node preservation, schedule resets,
disabled states, compact times and tooltip details without game access.

## Technologies, Startup and Configuration

- Python 3.10+ according to the type annotation syntax used.
  The README retains 3.10-3.13 as the existing setup recommendation. Windows CI
  includes Python 3.10, 3.13 and 3.14; configured coverage alone does not confirm
  successful CI runs or real-game compatibility. The initial inventory did not
  verify installed interpreter/package versions; subsequent automated checks
  used the available local Python 3.10.
- `requirements.txt`: `opencv-python`, `flask>=3.0`, `flask-socketio>=5.3`,
  `pystray`, `pillow`, `pywin32`. NumPy is used directly but is not listed
  separately; OpenCV supplies it as a dependency.
- ADB runs through `subprocess`. The WebUI uses native JavaScript and a Socket.IO
  4.7.5 client loaded from cdnjs over HTTPS with SHA-384 Subresource Integrity
  and anonymous CORS. When updating the client, update the integrity hash to
  match the exact CDN file. Dashboard loading still requires CDN access.
- Development: `python main.py [port]`; default port 6001. Tray operation:
  `pythonw tray_runner.py`, also through `StartWebUI.bat` or `.vbs`.
  The server binds to `0.0.0.0` by default and is reachable on the LAN.
- Settings: defaults in `config/settings.py`, then local overrides from
  `config/user_settings.json`. Overrides mutate the settings module's globals
  at import time. Later assignments would not update values already copied
  through `from ... import ...`.
- Workflows: after validation, `config/user_workflows.json` replaces the Python
  defaults completely, without a deep merge. `{}` is a valid empty workflow
  collection. A missing file selects built-in defaults.
- **Reload only reloads workflows. General settings require a process restart.**
  The API initially saves only the JSON files.
- Persistent statistics: `gem_statistics.json`; screenshots: `temp/`; file log:
  `bot.log`. `.gitignore` excludes all JSON files, logs and temporary
  screenshots, among other files. A Git checkout therefore does not necessarily
  reproduce the local configuration.
- Flask signing key: the first Web-App import creates a random 256-bit key in
  `config/web_secret.json`, which is reused afterwards. The existing `*.json`
  ignore rule excludes it; do not publish it or copy it between independent
  installations. Missing write permissions or a damaged file cause startup to
  fail without a fixed fallback key. To rotate it intentionally, delete the
  file while the server is stopped; existing signed sessions become invalid.
  This does not add authentication or change the LAN/origin policy.
  Tests: `tests/test_secret_key.py`, without bot startup or ADB.
  Creation atomically publishes a fully written, flushed temporary file without
  replacing an existing key (Windows rename, POSIX hard link). Concurrent
  threads/processes reuse the winning key. Reads retry Windows access/sharing/
  lock errors (5, 32, 33) up to six times, with exponential delays totaling
  0.63 seconds, because concurrent renames can briefly block readers. Persistent
  failures still abort startup; invalid content is never retried or replaced.

## Existing Architecture Decisions and Conventions

These points are supported by code or documentation; further historical
rationale is not documented:

- Communicate with the user in German. Use English throughout the project
  directory and all project content, including file and directory names, code,
  comments, logs, UI text, tests, documentation and working instructions
  (`AGENTS.md`). German requests do not change this policy unless the user
  explicitly requests an exception. Check added or edited content before
  completing a change.
- Central settings and reusable ADB/image/input helpers are preferred over
  scattered feature configuration.
- ROIs use `(x1, y1, x2, y2)` in screen pixels. Templates, game language and
  coordinates belong together; the README requires the game to use English.
- Clicks normally use a zero-distance swipe to control hold duration.
  `hold_ms=0` uses a normal tap. Scrolling uses drag gestures with speed presets.
  Default tap holds now use bounded triangular duration variation (enabled by
  default: 100 ms +/-30%, concentrating values near 100 ms). The WebUI's
  **Humanized Taps** section (`tapping`) exposes the switch, base duration,
  variation and global position jitter. Explicit workflow `hold_ms` values
  remain exact, including zero; a zero global base disables duration variation.
  Feature/step-specific position jitter still takes precedence. Settings require
  a process restart; old configuration files need no migration. No extra waits
  or gesture-path changes are introduced. Detection avoidance is not verified.
  `tests/test_tapper.py` covers duration/position bounds, exact explicit holds,
  disabled variation, plain taps, ADB failures and API/configuration round trips
  without game access. The dashboard editor tests cover rendering and saving
  the new controls. Real-game registration of the varied holds is unverified.
- Feature workers are daemon threads. Game actions normally coordinate through
  `state.ingame_action_lock`; pure detection does not need this lock. Freebies
  waits for it; Workflows postpone if it is busy. Lootbug scans without the
  lock and takes it only when a lootbug is visible, waiting up to
  `LOOTBUG_LOCK_WAIT_S` (listed in `state.workflows_waiting`, so Smart Craft
  yields) and re-detecting on a fresh frame after a wait.
- Global pause ("Pause after current", command `automation_pause_toggle`,
  runtime flag `state.automation_paused`, not persisted): every feature checks
  the flag right before it takes `state.ingame_action_lock` for new work and
  takes its postpone path instead (Workflows: the scheduler starts nothing and
  keeps queued manual runs; Smart Craft returns `paused`; Lootbug and Founders
  `busy`; Notices `retry`; Freebies waits and re-checks the flag after it got
  the lock). Work already holding the lock finishes: nested `call_workflow`,
  Smart Craft's re-lock after a yield (it stops yielding while paused) and the
  floor-trip return when Smart Craft is switched off are not gated. The
  dashboard derives *Pausing…* from `action_lock.locked`. A new feature that
  takes the action lock must check the flag too.
- Workflows run serially and normally hold the lock for the full flow, including
  subworkflows. `ignore_action_lock` is an existing intentional exception that
  allows interaction with other features.
- Turning the workflow master switch OFF blocks manual starts and cooperatively
  cancels running flows. An in-flight ADB call is not interrupted immediately.
  Subworkflow calls have recursion protection and a configurable depth limit
  (default 5); If blocks are limited to one level.
- Logging routes: `FULL` = file and UI, `TXT` = file, `UI` = UI only.

## Known Observations and Statically Identifiable Risks

These observations are not failure reports reproduced in the running bot:

| Code Observation | Implications for Changes |
| --- | --- |
| Features and workflow steps propagate click failures; failed actions do not increment their counters. | A successful ADB command still does not verify a reward. Freebie-Gems are labelled as calculated yields. |
| Settings/workflow APIs and local loaders share schema/value validation; storage uses atomic replacement and an in-process lock. | Migrations remain supported. Invalid API data does not overwrite a valid file; concurrently saved whole documents are not merged. |
| The worker applies workflow reloads between complete executions; metadata uses a shared lock. | Parent/subworkflows retain one definition generation; the dashboard and logs show requests, completion or errors. |
| The dashboard remains reachable on the LAN and checks origin scheme, host and port, including Socket.IO. There is no authentication. | LAN devices remain trusted. Foreign private origins and `null` are blocked; local JSON scripts without Origin remain supported. `OBELISKBOT_ALLOW_PUBLIC` can disable the IP restriction. |
| Statistics retain hourly buckets for seven days and permanent all-time gem/active-uptime sums. | Legacy files preserve gems but cannot reconstruct historical active uptime; the dashboard marks all-time uptime unavailable until an explicit reset. Rolling windows intentionally retain hourly precision. |
| Statistics tracking/card and screenshot visibility startup options are applied by the override loader and bot startup. | General settings still require restart; level startup enablement has one canonical field with legacy migration. |

Other details: many current ROIs target **900 x 1600**; automatic adaptation to
arbitrary resolutions is not implemented. The WebUI explicitly distinguishes settings
restart from safe workflow reload. Dependencies are mostly unpinned. Frequent
exception suppression makes troubleshooting harder.

## Guidance for Future Development and Verification

The screenshot layer was hardened on 2026-10-23. All captures remain full-device
emulator screenshots. Each capture and retry uses its own Android file with a
slot name and UUID. Local files are published atomically only after PNG
validation; readers and writers use the same lock per destination path.
`get_cached_screen()` and `get_latest_cached_screen()` read only sufficiently
fresh local images. File timestamps up to 50 ms ahead of Python's wall clock
are treated as age zero to tolerate Windows timestamp precision/rounding;
larger future offsets and expired frames remain invalid. This also prevents
the viewer from rejecting just-published PNGs with slightly negative ages.
If the shared cache is unavailable, Lootbug and Founders
capture only in their own slots. Forced or failed fresh captures never return
old images as a fallback. Rainbow Check receives its color image directly from
the validated capture/cache access. The dashboard displays an independent copy
of the last valid image and may therefore still show an older frame. Run
Detection serializes its checks; exhausted capture retries preserve the
existing `game_running=False` behavior, explicitly noting that an actual game
stop remains unconfirmed.

`REMOTE_SCREEN` now only determines the Android directory, not a shared output
file. Related ADB commands are bound to a device ID; a device switch caused by
reconnection requires a new complete capture attempt. Locks apply within one
process; two bot processes should not share a local temporary directory.
Terminated processes can leave their own temporary files behind; there is
intentionally no blanket deletion of other or older Android files.

Settings, logging and verification details: [Screenshots](SCREENSHOTS.md).

- Preserve existing working features; keep changes small, understandable and
  testable. Present a concrete plan before larger changes; agree fundamental
  architecture changes with the user.
- Reread relevant source files and local overrides before changes. This context
  does not replace checking the current state. Record important new architecture
  decisions here or in relevant documentation. Do not include secrets in
  documentation or output.
- For settings/workflow changes, consider the backend, editor fields, JSON
  format, reload behavior and dashboard state together. For feature changes,
  consider locks, screenshot age and menu/popup state.
- Targeted Founders regression tests: `tests/test_founders.py`. Smart Craft
  (number reading from the shipped glyphs, list-shift measurement, craft and
  purchase verification, locking, settings): `tests/test_smart_craft.py`.
  Freebie phase scheduling, logs, reactivation, migration and configuration API
  regressions: `tests/test_freebies.py`; editor controls: `tests/test_dashboard_editors.cjs`.
  Run `python -B -m unittest discover -s tests -v`. Tests cover migration,
  configuration API, independent activation, exactly one click without a popup,
  and locking/timing with simulated game access. There is no complete end-to-end
  suite for all real game flows. Windows CI runs Python and JavaScript
  regression tests.
  `tests/test_lootbug.py` covers the Buy-box flow with post-tap verification,
  ignored taps, existing-dialog recovery, obscured Buy box, missing screenshots,
  tap failures, bounded retries, ON/OFF
  and configuration migration without real game access.
  `tests/test_screen_cache.py` and `tests/test_screenshot_consumers.py` cover
  isolated capture paths, actual PNG decoding, failures/retries, concurrency,
  atomic replacement, device binding, all feature consumers, Run Detection,
  Rainbow color images, dashboard and configuration API without real ADB calls.
  A separate local emulator check confirmed six parallel slots, two competing
  requests for one slot and Run-template matching at 900 x 1600 pixels; it
  performed no game actions or bot starts.
  `TEST-ENVIRONMENT.bat` prefers the project's `.venv`, then searches `py`
  versions 3.14 down to 3.10 and finally Python 3.10+ on `PATH`; Python 3.14 is
  no longer excluded. It checks dependencies and ADB, executes imports and writes
  temporary files; it is neither a read-only check nor a feature regression
  test. `INSTALL-ENVIRONMENT.bat` installs packages.
- `tools/calibrate_rainbow.py` analyzes existing images for calibration.
- `tools/create_notice_template.py` creates sanitized Notices frame/mask pairs
  from local screenshots using known-frame presets, explicit crop/region
  coordinates or interactive selection. Defaults to staging under `temp/`;
  `--install` explicitly activates new pairs. Validation uses the existing
  detector without starting the bot. See [template guide](../tools/CREATE_NOTICE_TEMPLATE.md).
  `tools/sync_workflows.py` writes local workflows into
  `config/config_workflows.py`, modifying source code.
- Bot starts are not side-effect-free checks: importing `bot_app.py` already
  connects ADB and logs. A statistics request can initialize the background
  ticker, which later writes JSON. Local startup flags can immediately enable
  game actions.
- Before game-related functional tests, establish the emulator/game version,
  resolution, language, active ADB device and approved test flows. These
  operating details and a binding manual acceptance checklist are not yet fully
  documented.
- New settings must also be configurable in the WebUI.

Further sources: [README](../README.md),
[Collaboration Instructions](../AGENTS.md). Resolve conflicts against current
code and state documentation discrepancies explicitly.

## Action, Configuration, Reload and LAN Hardening

[RELIABILITY.md](RELIABILITY.md) describes these changes, their limitations,
tests and manual acceptance. New regression tests:
`tests/test_action_results.py`, `tests/test_config_safety.py`,
`tests/test_workflow_safety.py`, `tests/test_dashboard_security.py` and
`tests/test_dashboard_editors.cjs`. Local settings and workflows were also
checked against the new validator without overwriting their files.


## Maintenance fixes and intentionally retained behavior (2026-10-24)

- Multiple processes are supported for multiple emulators. Use separate project
  directories, distinct WebUI ports and fixed DEVICE values with
  ADB_AUTO_CONNECT=false to prevent fallback switching. See the README FAQ.
  Command dispatch, ADB selection/reconnection, hard process termination,
  workflow swipe repetitions and the screenshot capture pipeline are retained.
- Workflow action waits ignore scheduler wake events; master cancellation and
  stop remain interruptible. If-condition fail_wf aborts on a missing match,
  unavailable template or screenshot. Default inverted-condition semantics remain.
- Freebies cancels old activation generations before further actions. It does
  not perform cleanup taps after cancellation. A successful in-flight claim
  remains counted even if statistics was paused during ADB completion.
- Settings migration consolidates level activation into startup_defaults.
  Legacy detailed enablement preserves its old effective precedence once;
  normalized saves contain only the canonical startup switch. Statistics/card
  and screenshot visibility startup settings now affect runtime state.
- core/image_io.py decodes file bytes for Unicode-safe template loading across
  vision, rotation, notices, level detection and rainbow calibration. Templates
  still use their original resolution and matching algorithms.
- Dashboard text escapes HTML attribute quotes; dynamic inline-handler
  arguments also use JSON string encoding before HTML encoding.
- Notice listings cache PNG metadata by file signature, stat on each refresh
  and invalidate additions, replacements and deletions. Polling stays at 5s.
- Statistics use UTC hour keys and monotonic duration accounting. Legacy keys
  migrate once in the installation's current timezone without resetting totals.
  Already merged DST buckets are unrecoverable. Only changed data is persisted;
  failed saves retain the dirty state for retry. See STATISTICS.md.
- Tray output really does log: its own messages and stdout/stderr now share
  core.file_logger's file lock, path and rotation. One active bot.log remains,
  together with the existing rotation copies. No separate tray log is created.
- Installation creates a project-local .venv and uses requirements.txt. Both
  launchers prefer it, and no packages are installed merely by launching.
  Legacy global installations remain usable. Installation was not run as a test.
- Removed unreferenced ADB/device-byte helpers, WebUI emit wrappers, old select
  helpers, unused imports/state duplication and retired Wave-Pilot CSS rules.
  Retained screenshot_gray, clear_cache, legacy state response fields, registered
  routes/executors and the documented editor placeholder for compatibility.
- Added regression coverage for timing/cancellation, settings migration, quote
  roundtrips, notice cache invalidation, UTC migration, idle writes, Unicode
  templates, concurrent file rotation and mocked interpreter installation/launch.
  No bot or emulator acceptance run was performed for these changes.
