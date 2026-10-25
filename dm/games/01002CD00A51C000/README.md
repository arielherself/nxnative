# Baba Is You (`01002CD00A51C000`)

Update 1.0.10 (v655360), SDK 13.3.2, on NVN. Hempuli Oy's game on Chowdren (Clickteam Fusion compiled to C++) running the game's Lua scripts. Modules `main` and `sdk`. Packed with the NSP script on Linux ([Pack on Linux](../../../docs/development.md#pack-on-linux)); the provisional profile needed no change beyond the pad layout. The unsigned IPA is confirmed playable on the iPhone.

## What it needs

| Setting | Where | Why |
|---|---|---|
| 4 GB layout: `mem_reserve 0x100000000`, `mem_alias 0x10000000`, `mem_stack 0x10000000`, `app_pool 0xCD500000` | [`game.tsv`](game.tsv) | no virtual address memory (the NPDM declares no system resource): `main` sets a heap of at most the pool − 64 MB, a 3328 MB heap region |
| `mem_used 0x6200000` | `game.tsv` | the rewritten modules with `main`'s 71 MB bss are larger than the default 64 MB of used memory |
| `pad_layout dpad` | `game.tsv` | a grid game that moves one step per press (its own controls map movement to the D-pad as well as the stick): the D-pad layout |

## Shaders

- **Current:** 224 GLSLC outputs in the RomFS, 120 of them in `Assets.dat` holding a pixel and a vertex shader each ([GLSLC outputs holding several stages](../../../docs/reference/shaders.md#glslc-outputs-holding-several-stages)).

## Saves

- `save:/ba.ba` (global), `save:/<slot>ba.ba` (slot n+1, named in `Data/Editor/editor_menudata.lua`), `Settings.txt` and `levels/` (the editor's).
- The INI files are the PC version's, byte for byte in format (LF, the same sections), so a PC save (`%APPDATA%\Baba_Is_You\0ba.ba` and `ba.ba`; not `SettingsC.txt`, the PC controls) copied into the app's `Documents/save/` while the game is closed carries the progress over (the host clones `save/` into `save.work` at each mount). Checked with a PC save on the iPhone.

## On an iPhone 16 Pro

- It boots and plays at 60 fps (frame time p50 16.66 ms, no frame dropped) with a 470 MB footprint and no red line.

## Found on this game

- **GLSLC outputs holding several stages** (`shader_split.py` `glslc_program`): [Shaders](#shaders).
- **The D-pad layout** of the iPhone gamepad (`pad_layout dpad`, `DmGamepad.m`): [What it needs](#what-it-needs).

## Open

- Not run on a Mac: no prewarm list.
