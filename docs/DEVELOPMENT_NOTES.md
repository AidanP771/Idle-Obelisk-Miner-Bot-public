# Development Notes

Working notes for future sessions: game facts verified on the emulator, wiki
references, the local early-game workflow setup, verification methods and open
items. Architecture and feature behavior are described in
[PROJECT_CONTEXT.md](PROJECT_CONTEXT.md); this file records what was learned
while building Smart Craft, the obelisk fight, floor trips, surplus-ore selling,
auto-prestige and the bomb rotation (2026-11-22 to 2026-11-23). The dated list
of changes is in [CHANGELOG.md](CHANGELOG.md).

## Environment of the Reference Setup

| Item | Value |
| --- | --- |
| Emulator | Bluestacks instance 1, ADB `127.0.0.1:5556` (added to `ADB_SCAN_PORTS`) |
| Resolution | 900 x 1600 portrait. At 1080 x 1920 every template fails and the bot reports "game not running". |
| Game language | English |
| Game version (wiki) | v2.2.51 |
| Python | Project `.venv` (system Python lacks Flask); run tests with `.venv/Scripts/python.exe` |

## Game Wiki

Main page: <https://shminer.miraheze.org/wiki/Obelisk_Miner_Wiki> (content
licence CC BY-NC-SA 4.0). Pages used so far:

| Page | Used for |
| --- | --- |
| [Floors](https://shminer.miraheze.org/wiki/Floors) | Ore pairs per floor, 80/20 and 50/50 split, clear requirement (World 1: floor x 5) |
| [Ores](https://shminer.miraheze.org/wiki/Ores) | Floor range per ore (Tin 1-2, Copper 1-4, Mithril 3-6, Adamant 5-8, Runite 7-10, Citrine 9-12, Amethyst 11-14, Topaz 13-16, Obsidian 15-18, Demonite 17-18, VR-Tin 19-20) |
| [Craft](https://shminer.miraheze.org/wiki/Craft) | Ore per bar (Tin 5, Copper 6, Mithril 7, Adamant 8, Runite 9, Citrine 10, Amethyst 11), quantities 1x/5x/50x/500x/Max, Craft All at Obelisk 6 |
| [Prestige](https://shminer.miraheze.org/wiki/Prestige) | Minimum level 20; resets level, gold, ores, bars, contracts and "This Prestige" upgrades; keeps Relics, Artifacts, Veins, Drone Points, bomb charges, challenge progress and items; tip: sell ore and buy drone points before prestiging; level cap 30 + 5 per obelisk defeated |
| [Drones](https://shminer.miraheze.org/wiki/Drones) | Drone Points bought with gold (cost +1 gold per two points); spent on drone upgrades and suits. Whether drone upgrades survive prestige is not stated. |
| [Upgrades](https://shminer.miraheze.org/wiki/Upgrades) | "Upgrades are lost when prestiging" (the World Upgrades menu, bars and gold) |
| [Bombs](https://shminer.miraheze.org/wiki/Bombs) (stub) | Bomb types and base recharge times (Basic 30 s, Chain 60 s, Plenty 110 s, Exp 120 s); Fire / Fire Slow (1.25 s) / Fire All |
| Obelisk (main page section) | 30 s fights, 20 min cooldown, armor is subtracted from damage, damage carries over |

Wiki pages were last read 2026-11-23 (game v2.2.52 on the Prestige, Drones and
Upgrades pages). Add every newly used page to this table with what it was used for.

The wiki does not cover: drop amounts per floor, the floor-select UI, max
bomb charges, rock respawn timing. Those were measured in game (below).

## Game Facts Verified in Game

Measured at 900 x 1600, English UI. Coordinates are full-resolution pixels.

### Upgrades and Craft
- Upgrade costs are `have/need` with a bar icon, a coin icon (gold), or both.
  The "Upgrade Pickaxe" row (All Worlds Upgrade) costs Demonite.
- Per-row **Buy** is bright red when affordable (mean R about 165), dark when not (about 80).
- **Buy All** arms on the first tap (orange) and buys every affordable upgrade on the second. Smart Craft never taps it.
- Craft page quantity buttons 1x and 5x (green = active); the game keeps the mode. In 5x the ratio column shows the cost of one press (5 bars).
- The Upgrades list ends in identical "Unlock At Level N" rows; scroll
  measurement treats them as ambiguous ("list position unknown"), which ends
  the scan correctly.

- Each Upgrades row costs either bars or gold (coin icon, e.g. Pickaxe Crit Damage 0/60, Free Bomb Chance 0/80); no row with both was seen on 30 saved rows (2026-11-24). Gold rows have the same Buy button (bright red = affordable).

### Workshop (Permanent -> Workshop)
- The third cost column renders digits one sub-pixel shifted; its '4' matched no reference clearly, so every third-column cost with a 4 (amethyst/runite/citrine "14/", "74/") was unreadable and the row skipped (248 times in one log, 2026-11-24). Added `digits/4_3.png` from a live frame; on 239 saved frames only the failing frame's reads changed (both rows now read). Regression crops: `tests/data/smart_craft/`.
- Header "Spend Bars on Permanent Upgrades!"; rows cost up to three bar types,
  each shown as an icon with `have/` on one line and `need` below (grey text).
- Rows seen 2026-11-23: Basic & Chain Damage (Mithril 85, Adamant 68, Runite 51),
  Chain Bomb Amount (Adamant 211, Runite 169, Citrine 127), Unlock Infinity Bomb
  (Runite 350, Citrine 300, Amethyst 250), Plenty Bomb Ore Multi (Citrine 300,
  Amethyst 250, Topaz 200); below them "Unlock At Obelisk Level 5..8".
- Upgrades are permanent. Priority chosen by the user: regular Upgrades first,
  Workshop with what is left.
- Three glyph variants (`1_3`, `1_4`, `2_3`) were added from Workshop text.

### Lootbug popup
- This setup shows two offers: a free one (green **Free** button, centre about
  (283, 881)) and the same bonus for longer at 15 gems (red button). Offers
  vary (Gems +2, Game Speed 2x, Ore Income 2x); the buttons do not.
- The popup X sits at about (450, 1034); a 15 s timer closes it otherwise.
- The shipped `lootbug_buy_bonus.png` ("Buy Checked") belongs to another game
  layout and scores about 0.15 here. Local settings use
  `Lootbug/lootbug_free.png` (ROI 150,780,420,980) and `LOOTBUG_CLOSE_POPUP`
  with `Lootbug/lootbug_popup_close.png`. The paid offer is never tapped.

Timing (2026-11-24): a lootbug could stay on screen for minutes. Scans ran only every 180 s, and when one was seen the feature needed the action lock and retried every 15 s without waiting for it; bombs and Smart Craft kept winning (255 postpones in one log, e.g. 10:33:41-10:35:57). The run itself (bug tap, Free/Buy, X) takes 6-8 s, mostly the fixed waits `LOOTBUG_POST_CLICK_DELAY_S` (2 s) and `LOOTBUG_BUY_RETRY_S` (3 s). Now: scan without the lock every 15 s, wait up to 45 s for the lock when a bug is visible, re-detect on a fresh frame after waiting.

### Freebies (Store -> Freebie Pack)
- Claim works with the shipped templates. The shipped `close_menu.png` (white X,
  thick border) scores about 0.71 here, so the shop stayed open after a claim.
  `FREEBIES_CLOSE_TEMPLATE` is now configurable; local setting
  `workflows/menu/close_menu_tpl.png` (1.0 on the shop screen).
- The pack showed 5 gems (gems 372 -> 377), while `FREEBIES_DIAMONDS_PER_CLAIM`
  is 28, so gem statistics overstate. The pack banks up to 2 claims; the
  feature claims one per cycle.

### Sell page
- A row's own **Sell** button sells **one** ore per tap.
- **Sell All** sells every unlocked row; first tap arms it (orange), second tap sells.
- The box left of each row locks it: row dimmed, Sell button dark (mean R about 51 vs 172). Locked rows are skipped by Sell All. Lock state persists.
- The digit reader cannot read most multi-digit counts on this page (different glyph rendering); sales are verified by a change of the count box instead.

### Floors
- HUD button at about (843, 658) opens Floor Select; Auto toggle at about (843, 867): green = on, dark maroon (median BGR about 8,4,41; seen 2026-11-24) = off. The old reader only knew bright red and reported off as "not recognised".
- Picking a floor in Floor Select appears to switch Auto off (inferred 2026-11-24 from the log: Auto was on before the 19 -> 11 trips and off afterwards; not watched directly). Smart Craft therefore reads Auto before moving.
- Floor Select shows World 1 as a fixed two-column grid (odd floors left at x 342, even right at x 692, row 0 at y 184, pitch 51.65); current floor is green, unlocked red. 21 rows fit, so World 1 needs no scrolling.
- The number under the floor button is the clear counter, not the floor.
- After a prestige: floor 1, floor Auto stayed **on**.
- Not yet verified: whether a floor tap asks for confirmation; whether leaving a floor resets its clear counter.

### Obelisk
- Main-screen timer at about (840, 490) shows yellow `0:00` when ready.
- The 20 min cooldown starts after the most recent fight (user observation 2026-11-23); a skipped fight does not start it.
- The fight needs a **hold** on the obelisk at (450, 800); a fight lasts about 34 s. ADB commands time out at 30 s, so the workflow holds 5 x 8 s.
- After the fight: Exit, then "Return To Mines".
- The red "Warning: Pickaxe Damage is lower than Obelisk Armor" box (`damage_warning_tpl`) means a fight deals 0 damage; the workflow closes without fighting, so the 20 min cooldown is not spent.
- Timing, 2026-11-23: checks ran only every 8-14 min although the interval was 250-300 s, because Smart Craft held the action lock for 5-8 min per cycle (log: `postponed - another feature busy`). Fixed by `SMART_CRAFT_YIELD`.
- When the obelisk's HP reaches 0, the Fight tab shows an orange **Claim
  Victory** button (about (450, 1230)) instead of Attack, and the main-screen
  timer keeps showing `0:00`. Claiming returns to the mine with "Obelisk Level
  N!" and "New Menu Unlocked! Check the Menu Hub". Obelisk 4 was claimed
  2026-11-23 (rewards: obelisk level 3 -> 4, level cap 45 -> 50, Skill-Tree).
- A plain `adb shell input tap` on Claim Victory was ignored; a short press
  (the bot's tapper, or a 90 ms swipe) works.

### Prestige
- Prestige page: "Earn Prestige Points (PP) at level 20+"; shows the PP gain.
  The HUD top-left shows `PP(+gain)`.
- First automatic prestige ran 2026-11-23 17:54: 29 -> 230 PP, level 45 -> 1, cap 45 -> 50.
- The Prestige page keeps its scroll position. The Prestige box is at the top, above Artifacts and the PP upgrades; a scrolled-down page hides it (seen 2026-11-23 21:45, after Obelisk 4 added rows).
- The Drones page also keeps its scroll position. On 2026-11-23 23:12 a single mid swipe did not reach the top, the fixed-coordinate Max/exchange taps missed, and the prestige ran with unspent gold. Fixed with templates and three fast swipes.
- Saving workflows from a WebUI editor that was opened before a file edit overwrites that edit (22:51: an interval change dropped the Prestige scroll step).
- Wiki (Prestige page): Drone Points are permanent (kept on prestige), as are Relics, Artifacts, Veins, bomb charges, challenge progress and items. The wiki does not say whether drone upgrades bought with Drone Points are kept; unverified.
- The Drones page has top tabs Drone Upgrades / Drone Suits / Suit Upgrades and remembers the last one; the exchange panel is only on Drone Upgrades (2026-11-24: the page was on Drone Suits and every `buy_drone_points` since 05:00 failed, 30 times).
- A swipe with the default area (0,102,900,1457) and direction down starts the finger at (450,122), on the Drone Suits tab; the workflow swipe therefore uses area [80,420,820,1350]. Confirmed live 2026-11-24 10:50: with tab tap and this area the page showed Drone Upgrades, Max (0.91) and Exchange (1.0) were hit and the prestige ran.
- The Max quantity button stays selected (green) after use; `drone_max_tpl` (cut from the red state) still matches it at 0.91 because workflow matching is grayscale.
- The exchange costs about 302 gold per drone point at this stage; with less gold (135 at 10:50) Exchange does nothing and the prestige still runs (the workflow cannot see whether points were bought).
- Drones page "Exchange Gold for Drone Points": quantity buttons 1x/5x/25x/250x/Max at y 250 (Max x 716), exchange button at (450, 352).

### Bombs
- Bomb panel toggle at about (816, 1302). Selected bomb shows its recharge timer; counts show at the icon (yellow `5` observed when full; the cap was 5 for all four bombs).
- Holding **Fire** fires rapidly; a tap fires one charge.
- The panel shows each bomb's charge count; the Bomb Bear suit (maxed) fires a Basic bomb by itself when Basic is at max capacity, so Basic charges are not wasted.
- A 5-round rotation took 57 s and held the action lock 63% of the time at a 90 s interval (2026-11-24); one round per run every 30 s takes 10-11 s.
- After a Basic bomb the floor turns over (fresh rock set) about **2.6 s** later. Charges fired inside that window hit already broken rocks.

## Local Workflow Setup (not versioned)

`config/user_workflows.json` is git-ignored (all `*.json` are) and fully
replaces the default workflows. The reference setup uses:

| Slug | Auto | Purpose |
| --- | --- | --- |
| `bomb_rotation_early_game` | on, 30 s | Optional close of a leftover menu (`menu/close_menu_tpl`, ROI [405,1471,658,1546]: 1.0 with a menu open, <= 0.27 on the main screen over 239 frames); optional panel open; one round of Exp, Plenty (mark), Basic (break), Chain - one tap each, each bomb in an `if template_visible` (0.9) so locked bombs are skipped; optional panel close. About 10 s per run (2026-11-24; was 5 rounds every 90 s, 57 s per run). |
| `bomb_basic_rotation_all` | off, inactive | Original default rotation (2 s holds); `enabled_default` false so it stays off after restarts. |
| `obelisk_fight` | on, 60 s (only a screenshot when not ready) | If `obelisk/main_ready_tpl` is visible -> call `obelisk_fight_attack`. |
| `obelisk_fight_attack` | helper | Tap the obelisk HUD button at (843,440) (fixed position: its icon changes with every obelisk level, so `obelisk_button_tpl` stopped matching after Obelisk 4), Fight tab; Claim Victory visible (`obelisk/claim_victory_tpl`, 0.9) -> claim; damage warning or no ready Attack -> close; else Attack, hold (450,800) 5 x 8 s, optional Exit and Return To Mines. |
| `auto_prestige` | on, 600 s | If `hud/level_capped_tpl` (0.9) is visible -> call `prestige`. |
| `prestige` | helper | Open Prestige page, swipe down twice (scroll to top); only if the Prestige button is visible -> close menu, call `prestige_execute`; else close menu. Drone points are therefore bought only right before a prestige. |
| `prestige_execute` | helper | Sell All (two taps), call `buy_drone_points`, open Prestige page, press Prestige (2 clicks), close. |
| `buy_drone_points` | helper | Drones -> Upgrades nav, tap the "Drone Upgrades" top tab ([150,112,340,148]), swipe down 3x in area [80,420,820,1350] (scroll to top), click `drones/drone_max_tpl` (optional), `if last_step_failed` -> close menu + re-check Max (fails), click `drones/drone_exchange_tpl` (optional), same check. A miss closes the Drones menu and aborts the chain, so no prestige happens with unspent gold and no menu is left open. |
| `level_up_early_game` | off | Aborts at the locked Contracts tile and leaves the menu open; competes with Smart Craft. |
| `craft_upgrades_early_game` | inactive | Replaced by Smart Craft. |

Templates for these workflows are versioned under `images/workflows/obelisk/`,
`images/workflows/hud/` and `images/workflows/menu/close_menu_tpl.png`. The
default `pins/close_pin_tpl.png` scores only about 0.65 on this setup.

Local settings (`config/user_settings.json`): `startup_defaults.SMART_CRAFT_ENABLED = true`; `LOOTBUG_CHECK_INTERVAL_S` 15 (was 180, changed 2026-11-24);
`SMART_CRAFT_INTERVAL_S` 300, floor trips and surplus-ore selling on (set by the
user); Lootbug Free/close templates with `LOOTBUG_CLOSE_POPUP`;
`FREEBIES_CLOSE_TEMPLATE = workflows/menu/close_menu_tpl.png`. Defaults in
`config/settings.py` keep floor trips and selling off.

Pitfalls:
- Two bot processes (seen 2026-11-23: `tray_runner.py` and `main.py` started
  5 s apart) both drove the emulator with separate action locks: menus were
  closed under Smart Craft and API commands such as workflow reloads reached
  only the instance owning port 6001. `run_server` now refuses to start a bot
  when the port is taken. Check with the process list if behaviour looks doubled.
- A WebUI tab opened before a workflow change still holds the old definitions;
  refresh before "Save Workflows" or it overwrites the change.
- Workflows reload at execution boundaries (WebUI Reload or command
  `reload_cfg`); general settings and new templates loaded by features need a
  process restart.
- Workflow `if` steps cannot be nested; use a helper workflow via `call_workflow` (max depth 5).
- General settings and code changes need a process restart; on 2026-11-23 a
  correct Freebies setting had no effect for an hour because the bot ran since
  before the change. Compare the last `[STARTUP]` log line with the change time.
- Menu pages keep their scroll position (Prestige, Drones). Scroll to a known
  end before matching or tapping, and prefer templates over fixed coordinates.
- Features that poll the action lock non-blockingly lose to frequent lock users (lootbug, 2026-11-24). A feature with a time-critical target should detect without the lock and then wait for it (`lock.acquire(timeout=...)`), listing itself in `state.workflows_waiting` so Smart Craft yields.
- A long lock holder starves time-critical workflows; look for
  `postponed - another feature busy` when timing looks off.
- An aborted workflow leaves its menu open; every later HUD-based workflow then silently matches nothing (2026-11-24: the Drones menu stayed open after each failed prestige until Smart Craft closed it). Close the menu before a deliberate abort, and let a frequent workflow close leftover menus first.
- A deliberate abort without nested `if`: an optional click, then `if last_step_failed` -> close menu + a non-optional click on the same missing template.
- Before `reload_cfg`, validate an edited `user_workflows.json` with `config.config_workflows.load_workflow_definitions()` (it rejected nested `if` blocks that the JSON itself allowed) and dry-run it through the engine with mocked screens (see Verification Methods).
- Workflow matching is grayscale; a step cannot tell a green toggle from a red one (a 1x/5x button matched both states at 0.95+).

## Verification Methods

- Unit tests: `.venv/Scripts/python.exe -B -m unittest discover -s tests -v`
  and the `node --test` command from `AGENTS.md`. One intermittent error was
  seen in unrelated tests (again 2026-11-24: `test_gem_statistics` fourteen-days test and `test_secret_key` concurrent starts) and did not reproduce on rerun.
- Offline checks: save frames from a read-only pass (open page, scroll, close)
  and run the feature's readers on them (`read_upgrade_rows`, `read_craft_row`,
  `read_floor_panel`, `read_sell_rows`). This caught the missing Demonite icon.
- Live checks: never send own ADB taps or swipes while the bot runs, even
  when `/api/state` shows the lock free; a check is not a lock (a stray tap hit
  "Setup Pins" twice on 2026-11-23). Screenshots are fine. For taps use a
  workflow run (`wf_run:<slug>`) or ask the user to pause the bot.
- Workflow dry run: load the edited definitions and run `_run_workflow` from `features/workflows.py` with the test fixture of `tests/test_screenshot_consumers.py` and a `find_template` mock that decides per template name; this checks branches and aborts without ADB.
- Watching a run: poll `/api/screenshot` (read-only) once a second after the run's `starting` log line.
- Logs: `bot.log` in the project root, rotated to `bot.log.1..3` at 2 MB.
  Prefer read-only navigation; anything that spends resources must be agreed
  with the user first.
- Template building: crop from a 900 x 1600 screenshot, then score the
  template against all saved frames (true matches about 1.0, others well below
  the threshold) before using it.

## Open Items

- Restart pending (2026-11-24, all code since `9f734b3`): floor-trip Auto handling, gold upgrades option, Pause after current, Lootbug lock waiting, craft target `next`, exec-out screenshots, page polling, the new '4' glyph. After the restart check the log for `method=exec_out`, `floor trip ... returning after`, `[LOOTBUG] lootbug visible ... waiting`, and Smart Craft cycle length. Floor Auto was off on 2026-11-24 and must be switched on once by hand.
- Obelisk timer scheduling (not built): the HUD countdown (`m:ss`, about (805-885, 475-505)) uses the pixel font at 17 px height; the Smart Craft glyph set reads only some digits there, and ':' breaks segmentation. It needs its own digit references and a workflow step that sets the next due time. Current cost of polling: one screenshot per 60 s, at most about 60 s late per 20 min cooldown.
- Smart Craft "list position unknown after scrolling" (101 times on 2026-11-24): cause not found yet. The before/after frames are now saved to `temp/smart_craft_debug/` (newest 40); analyse them with `list_shift` offline. Unreadable Workshop costs are dumped there too.
- Screenshots: `exec_out` capture (2026-11-24) not yet observed in the running bot; check `method=exec_out` and `elapsed=` in `[SCREENSHOT]` lines after a restart.
- Smart Craft page opening polls for the page (2026-11-24); other taps (craft batches, buys, scrolls, closing) still wait a fixed `SMART_CRAFT_UI_DELAY_S`.
- `SMART_CRAFT_CRAFT_TARGET = next` (2026-11-24): compare cycle length and purchases per cycle with the old `all` behaviour (2026-11-24 morning: 250-360 s per cycle, 2179 bars for 38 purchases).
- Floor trips: no trip has run yet (trip start/end now log planned and real duration). Every attempt since at least 2026-11-24 08:00 moved 19 -> 11 and back within 7 s (Auto unreadable) and left Auto off; fixed in code, needs a restart and a live trip.
- Surplus-ore selling: full sequence not yet run in game.
- Gold upgrades (`SMART_CRAFT_BUY_GOLD_UPGRADES`, off by default): not yet run live; offline the reader finds the gold rows on four saved frames, all unaffordable (gold 0), so a lit gold Buy button has not been seen yet.
- Workshop buying is not yet observed live (bars were 0 after the prestige).
- `SMART_CRAFT_YIELD` and the Freebies close template: verify after the next restart (`pausing for waiting workflow(s)`, `tap close-menu`).
- `buy_drone_points` cannot tell whether the exchange bought anything (gold below one drone point's price); reading the drone point counter before/after would make that visible.
- Whether drone upgrades survive prestige: compare before/after the next prestige.
- `FREEBIES_DIAMONDS_PER_CLAIM` is 28 but this pack gives 5; the pack banks 2 claims and the feature claims one per visit.
- Skill-Tree (unlocked at Obelisk 4): automation ideas from the wiki discussed, nothing built (user: explore as we go).
- An Upgrades pin was added to the bottom bar by a stray tap (harmless; remove via Setup Pins).
- Bomb rotation: one round every 30 s; charge counts are not read, so empty bombs get a no-op tap. Recharge upgrades change the ideal interval.
- Bar types beyond World 1 floor 20 (VR-Demonite has an ore icon but no bar template yet) need templates as they unlock.
- The default workflows in `config/config_workflows.py` still contain the original `auto_prestige` (level 127 plus an unconditional floor-Auto tap) and `close_pin_tpl`.
