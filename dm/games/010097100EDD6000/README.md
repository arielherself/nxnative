# Little Nightmares II (`010097100EDD6000`)

Update 1.4 (v262144), SDK 10.4.1, Unreal Engine 4.25 (Tarsier Studios / Bandai Namco) with Wwise, on NVN. Modules `main`, `subsdk0` (the SDK's movie library) and `sdk`. Two DLC NSPs are licences with no content. Brought up on Linux with an iPhone and no Mac: packed with the NSP script on Linux ([Pack on Linux](../../../docs/development.md#pack-on-linux)), checked by reading the code and by builds tried on the phone. The unsigned IPA is confirmed playable on the iPhone.

## What it needs

| Setting | Where | Why |
|---|---|---|
| 14 cuts of `mov xN,#0xf80000000` (62 GB) → 2 GB | [`patches.py`](patches.py) | the engine address region; see [Memory](#memory) |
| the FMallocBinned3 constructor call cut, an `OBS_UE_MALLOC` gate at `main+0x1d10780` | `patches.py` | the host's allocator in place of Unreal's (`hle_uemalloc.c` layout `UE4_25`) |
| the node-area and 1 GB alignment cuts on `sdk` `d9add76f…` | [`sdk_patches.py`](../../tools/sdk_patches.py) | the 2 GB region starts 64 MB into the alias region |
| DRAGON QUEST VII Reimagined's layout: `mem_reserve 0xF0000000`, `mem_alias 0xC8000000`, `mem_stack 0x10000000`, `app_pool 0xCD500000` | [`game.tsv`](game.tsv) | a 3.125 GB alias region, a 384 MB heap region (the game sets 4 MB) |
| `sys_resource 0x5000000` | `game.tsv` | the NPDM declares 80 MB of system resource |
| no `game_args` | `game.tsv` | `-SwitchVirtualAddressSpaceSize=` skips the reservation in this build ([Memory](#memory)) |

## Files

- One version 8 pak (UE 4.22 to 4.25), `Helios/Content/Paks/Helios-Switch.pak`: 4.9 GB, 43,523 files, none compressed (five empty compression method names in the footer). Its index is encrypted.
- **Current:** the AES key is in no 32-byte window of `main`. The key callback (`main+0x18e96a0`, address-taken, no unwind entry) copies two 16-byte constants from two pages (`ldr q` / `ldr q` / `stp q, q, [x0]`), which `uepak.py` `split_keys` finds; `uepak.pak_key` takes it from the given `main` and keeps it in memory only.

## Shaders

- **Current:** 20,170, inline in the cooked packages and `Engine/GlobalShaderCache-GLSL_SWITCH.bin`, read as DRAGON QUEST XI S's (`shader_split.py` `ue_legacy_jobs`): 16,293 pixel, 3,195 vertex, 637 compute, 45 geometry. All translate.
- **Resolved:** in the build first tested on the iPhone, 54 of the 306 readable-tile variants did not compile (`dmsh_fetch.py` gave `main0_u` no `fb0`…`fb7` inputs), which only left those passes unmerged; `4e91a3d` (PR #83) makes them compile.

## Memory

- `nninitStartup` (`main+0x1d0fe20`) needs virtual address memory and reserves one 62 GB address region for the engine (`main+0x1d10138`); malloc gets a 3 MB block and the heap is 4 MB.
- **Current:** `nninitStartup` and six lazy paths (FSwitchPlatformMemory's page allocator among them, `main+0x1d10af4`) all read "if the size is 0, set it to 62 GB and `AllocateAddressRegion(&base, 62 GB)`": the size (`main+0x697f988`) stands for "reserved". With `-SwitchVirtualAddressSpaceSize=` the size is set, so every reservation is skipped: the base stayed 0, the page allocator worked over [0, 2 GB) and its first `AllocateMemoryPages` failed (0xe03). So the 62 GB in all seven pairs (the size stored, the size reserved) is cut to 2 GB, and the profile gives no argument ([Unreal's engine address region and its options](../../../docs/reference/memory.md#unreals-engine-address-region-and-its-options)). The six other `orr xN,xzr,#0xf80000000` in `main` are ICU / HarfBuzz character-class masks.
- **Current:** FMallocBinned3 is made at run time as DRAGON QUEST VII Reimagined's: `FPlatformMemory::BaseAllocator` (`main+0x1d10760`) mallocs 0x1c38 bytes and calls the constructor (`main+0x1d10778` → `main+0x1b6dda0`, vtable `main+0x63496d0`, whose slot 17 returns `L"Binned3"`). UE 4.25's FMalloc has 18 vtable slots and neither `TryMalloc` nor `TryRealloc` (`hle_uemalloc.c` layout `UE4_25`, -1 for the slots it lacks) ([Unreal's FMallocBinned3 replaced by the host allocator](../../../docs/reference/memory.md#unreals-fmallocbinned3-replaced-by-the-host-allocator)).

## Rendering

- **Resolved:** a black screen and then a crash a few seconds after boot. The first render passes zero-filled enough new targets (`tex_zero_fill`) to come round to an upload slot within one block of the encode thread, and the RHIThread waited in `up_wait_zero` for that slot's last batch, which was a commit record still in the encode thread's unsent block: never committed, never finished. `up_begin` now hands the encode thread what is recorded before it waits for a busy upload slot (`nvn_mtl.m`, every game; PR #92, upstream `9cb7af3`).

## First run on an iPhone 16 Pro

- The first start compiles 21,646 source libraries in 21 min; later starts and reinstalls with the same shaders skip it.
- 7.5 minutes of play at the game's 30 fps (frame time p50 33.4 ms, GPU 21.7 ms a frame, 1,063 draws a frame), a 1.76 GB footprint, no red line but four audio device stalls.

## Found on this game

- **Encrypted pak index keys built from two 16-byte constants** (`uepak.py` `split_keys`): [Files](#files).
- **Upload slots handed back before waiting** (`nvn_mtl.m` `up_begin`, PR #92): [Rendering](#rendering).

## Open

- Not run on a Mac: no prewarm list, and the heap check did not run.
