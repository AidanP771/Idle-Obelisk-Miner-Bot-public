# Development Notes

Working notes for future sessions: game facts verified on the emulator, wiki
references, the local early-game workflow setup, verification methods and open
items. Architecture and feature behavior are described in
[PROJECT_CONTEXT.md](PROJECT_CONTEXT.md); this file records what was learned
while building Smart Craft, the obelisk fight, floor trips, surplus-ore selling,
auto-prestige and the bomb rotation (2026-11-22 to 2026-11-23).

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
| [Prestige](https://shminer.miraheze.org/wiki/Prestige) | Minimum level 20; resets level, gold, ores, bars, contracts and "This Prestige" upgrades; level cap 30 + 5 per obelisk defeated |
| [Bombs](https://shminer.miraheze.org/wiki/Bombs) (stub) | Bomb types and base recharge times (Basic 30 s, Chain 60 s, Plenty 110 s, Exp 120 s); Fire / Fire Slow (1.25 s) / Fire All |
| Obelisk (main page section) | 30 s fights, 20 min cooldown, armor is subtracted from damage, damage carries over |

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

### Workshop (Permanent -> Workshop)
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

### Sell page
- A row's own **Sell** button sells **one** ore per tap.
- **Sell All** sells every unlocked row; first tap arms it (orange), second tap sells.
- The box left of each row locks it: row dimmed, Sell button dark (mean R about 51 vs 172). Locked rows are skipped by Sell All. Lock state persists.
- The digit reader cannot read most multi-digit counts on this page (different glyph rendering); sales are verified by a change of the count box instead.

### Floors
- HUD button at about (843, 658) opens Floor Select; Auto toggle at about (843, 867), green = on.
- Floor Select shows World 1 as a fixed two-column grid (odd floors left at x 342, even right at x 692, row 0 at y 184, pitch 51.65); current floor is green, unlocked red. 21 rows fit, so World 1 needs no scrolling.
- The number under the floor button is the clear counter, not the floor.
- After a prestige: floor 1, floor Auto stayed **on**.
- Not yet verified: whether a floor tap asks for confirmation; whether leaving a floor resets its clear counter.

### Obelisk
- Main-screen timer at about (840, 490) shows yellow `0:00` when ready.
- The fight needs a **hold** on the obelisk at (450, 800); a fight lasts about 34 s. ADB commands time out at 30 s, so the workflow holds 5 x 8 s.
- After the fight: Exit, then "Return To Mines".
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
- Drones page "Exchange Gold for Drone Points": quantity buttons 1x/5x/25x/250x/Max at y 250 (Max x 716), exchange button at (450, 352).

### Bombs
- Bomb panel toggle at about (816, 1302). Selected bomb shows its recharge timer; counts show at the icon (yellow `5` observed when full; the cap was 5 for all four bombs).
- Holding **Fire** fires rapidly; a tap fires one charge.
- After a Basic bomb the floor turns over (fresh rock set) about **2.6 s** later. Charges fired inside that window hit already broken rocks.

## Local Workflow Setup (not versioned)

`config/user_workflows.json` is git-ignored (all `*.json` are) and fully
replaces the default workflows. The reference setup uses:

| Slug | Auto | Purpose |
| --- | --- | --- |
| `bomb_rotation_early_game` | on, 60 s | Optional panel open; Exp, Plenty (mark), Basic (break), wait 3 s, Chain, wait 3 s, Basic; single taps; each bomb in an `if template_visible` (0.9) so locked bombs are skipped; optional panel close. |
| `obelisk_fight` | on, 300 s | If `obelisk/main_ready_tpl` is visible -> call `obelisk_fight_attack`. |
| `obelisk_fight_attack` | helper | Obelisk button, Fight tab; Claim Victory visible (`obelisk/claim_victory_tpl`, 0.9) -> claim; damage warning or no ready Attack -> close; else Attack, hold (450,800) 5 x 8 s, optional Exit and Return To Mines. |
| `auto_prestige` | on, 600 s | If `hud/level_capped_tpl` (0.9) is visible -> call `prestige`. |
| `prestige` | helper | Open Prestige page; only if the Prestige button is visible -> close menu, call `prestige_execute`; else close menu. Drone points are therefore bought only right before a prestige. |
| `prestige_execute` | helper | Sell All (two taps), call `buy_drone_points`, open Prestige page, press Prestige (2 clicks), close. |
| `buy_drone_points` | helper | Drones -> Upgrades tab, Max (716,250), exchange (450,352). |
| `level_up_early_game` | off | Aborts at the locked Contracts tile and leaves the menu open; competes with Smart Craft. |
| `craft_upgrades_early_game` | inactive | Replaced by Smart Craft. |

Templates for these workflows are versioned under `images/workflows/obelisk/`,
`images/workflows/hud/` and `images/workflows/menu/close_menu_tpl.png`. The
default `pins/close_pin_tpl.png` scores only about 0.65 on this setup.

Local settings (`config/user_settings.json`): `startup_defaults.SMART_CRAFT_ENABLED = true`.
Floor trips and surplus-ore selling are off unless enabled in the WebUI.

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
- Workflow matching is grayscale; a step cannot tell a green toggle from a red one (a 1x/5x button matched both states at 0.95+).

## Verification Methods

- Unit tests: `.venv/Scripts/python.exe -B -m unittest discover -s tests -v`
  and the `node --test` command from `AGENTS.md`. One intermittent error was
  seen twice in unrelated tests and did not reproduce on rerun.
- Offline checks: save frames from a read-only pass (open page, scroll, close)
  and run the feature's readers on them (`read_upgrade_rows`, `read_craft_row`,
  `read_floor_panel`, `read_sell_rows`). This caught the missing Demonite icon.
- Live checks: only when the action lock is free and no workflow is due
  (`/api/state` shows `action_lock`, `workflows` with `next_remaining`).
  Prefer read-only navigation; anything that spends resources must be agreed
  with the user first.
- Template building: crop from a 900 x 1600 screenshot, then score the
  template against all saved frames (true matches about 1.0, others well below
  the threshold) before using it.

## Open Items

- Floor trips: first live floor change not yet observed (confirmation dialog? clear counter reset?).
- Surplus-ore selling: full sequence not yet run in game.
- Smart Craft buys only bar-cost upgrades; gold-cost upgrades (Pickaxe Radius, Bomb Damage, ...) are not bought.
- Workshop buying is not yet observed live (bars were 0 after the prestige).
- Bomb rotation: pacing is fixed in the workflow; bomb charge counts are not read. Recharge upgrades change the ideal interval.
- Bar types beyond World 1 floor 20 (VR-Demonite has an ore icon but no bar template yet) need templates as they unlock.
- The default workflows in `config/config_workflows.py` still contain the original `auto_prestige` (level 127 plus an unconditional floor-Auto tap) and `close_pin_tpl`.
