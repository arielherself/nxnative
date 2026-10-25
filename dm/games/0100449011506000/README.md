# The Last Campfire (`0100449011506000`)

Update 1.0.5 (v327680), SDK 10.4.1, Hello Games' game on Unity 2018.4.24f1 IL2CPP, on NVN. Modules `main`, `subsdk0` (the SDK's movie library) and `sdk`. Packed with the NSP script on Linux ([Pack on Linux](../../../docs/development.md#pack-on-linux)); the provisional profile needed only its pool. The unsigned IPA is confirmed playable on the iPhone.

## What it needs

| Setting | Where | Why |
|---|---|---|
| 5 GB layout: `mem_reserve 0x140000000`, `mem_alias 0x118000000`, `mem_stack 0x10000000` | [`game.tsv`](game.tsv) | the NPDM declares 16 MB of system resource (`sys_resource 0x1000000`): virtual address memory |
| `app_pool 0xA0000000` | `game.tsv` | Engage's pool in the same layout; the heap check needs a Mac and did not run. `nninitStartup` hands pool − used to StandardAllocator as one address region and Unity reserves its own regions beside it; the guessed 3.2 GB pool would leave those about 1.1 GB of alias region |
| the node-area and 1 GB alignment cuts on `sdk` `d9add76f…` | [`sdk_patches.py`](../../tools/sdk_patches.py) | the SDK 10.4.1 build it shares with Advance Wars 1+2 and Little Nightmares II ([The SDK's address-region node area](../../../docs/reference/memory.md#the-sdks-address-region-node-area)) |

## Shaders

- Everything is in `Data/data.unity3d` (a UnityFS bundle) and `Data/Resources`, files without type trees. The `Shader` objects read with Raging Loop's 2018.3 layout (the same hash).
- **Current:** 11,435 shaders, the 4 `ComputeShader` objects among them: Unity's `Internal-Skinning` and the game's `FogTextureGenerator` (three kernels: the room fog texture, its border and the ambient cubemap). Their type hash (`1a95529a…`) had no layout, so their kernels were left out and the fog was drawn as large flat coloured planes over the scenes; `unityfs.py` now carries Unity 2018.4's layout (`COMPUTE_2018_4`), in which a kernel is its own one variant ([Unity ComputeShader objects](../../../docs/reference/shaders.md#unity-computeshader-objects)).
- **Current:** two red lines, two pipelines whose pixel shader reads `user(locn3)`, which its vertex shader does not write: pre-2019.3 Unity subprograms hold one stage each, so the split pairs no vertex and pixel shaders by variant and `vs_pad` pads none of them. Nothing was seen missing in play.

## On an iPhone 16 Pro

- 60 fps (frame time p50 16.66 ms, p95 17.1 ms, no frame dropped) with a 1650 MiB footprint; sound with no audio underrun.

## Found on this game

- **Unity 2018.4's `ComputeShader` layout** (`unityfs.py` `COMPUTE_2018_4`, a kernel with its code in itself): [Shaders](#shaders).

## Open

- The two pipelines that fail to link ([Shaders](#shaders)); pairing pre-2019.3 Unity's single-stage subprograms would also change the tables of Cuphead, Raging Loop and AI: THE SOMNIUM FILES.
- Not run on a Mac: no prewarm list, and the heap check did not run.
