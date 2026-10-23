# Little Nightmares II (010097100EDD6000): the rewriter's patches of this game's own builds. dm/tools/patch_nso.py loads every profile's patches.py and applies those of a module's NSO build ID;
# CUTS, OBS and FRAME_STEP as described there ("Registered patches"). Patches of Nintendo SDK module builds are in dm/tools/sdk_patches.py
CUTS = {
  # Little Nightmares II 1.4 (v262144) main: Unreal Engine 4.25's FMallocBinned3 would cut 37 pools of 1 GB out of the engine's address region (nninitStartup reserves 62 GB at main+0x1d10138,
  # 2 GB with the cuts below). The allocator is made at run time, as DRAGON QUEST VII Reimagined's: FPlatformMemory::BaseAllocator (main+0x1d10760) mallocs 0x1c38 bytes
  # and constructs them (main+0x1d10778 → main+0x1b6dda0; the constructor stores the vtable main+0x63496d0, whose slot 17 returns L"Binned3"). The constructor call is cut, and observation
  # gate OBS_UE_MALLOC (OBS below) puts the host allocator's vtable into the new object, which is still in x19 (dm/host/hle_uemalloc.c). No code outside the allocator reads its pool base
  # (main+0x69472a0: its 11 reads are at main+0x1b6e4e8 to +0x1b75e54) or calls its methods directly: every FMemory call goes through the vtable
  # The engine's address region: nninitStartup and six lazy paths (FSwitchPlatformMemory's page allocator among them, main+0x1d10af4) all read "if the size is 0, set it to 62 GB and
  # AllocateAddressRegion(&base, 62 GB)", the size (main+0x697f988) standing for "reserved". -SwitchVirtualAddressSpaceSize= sets the size and so skips every reservation: the base stays 0, the
  # page allocator works over [0, 2 GB) and its first AllocateMemoryPages fails (0xe03, its assert then makes the allocator through FMemory before malloc has its block; an iPhone, 2026-10-24).
  # So the profile gives no game argument and the 62 GB in all seven pairs (the stored size, the size reserved) is 2 GB, as DRAGON QUEST VII Reimagined's region. The six other
  # orr xN,xzr,#0xf80000000 in main are ICU / HarfBuzz character-class masks
  '7f4216b6e784a4b2f880f5bfa581608d': [
    (0x1d10124, 'e81361b2', '0800b0d2', 'engine address region: mov x8,#0xf80000000 (62 GB) → mov x8,#0x80000000 (2 GB), the size it stores'),
    (0x1d10130, 'e11361b2', '0100b0d2', 'engine address region: mov x1,#0xf80000000 (62 GB) → mov x1,#0x80000000 (2 GB), the size it reserves'),
    (0x1d10960, 'e91361b2', '0900b0d2', 'engine address region: mov x9,#0xf80000000 (62 GB) → mov x9,#0x80000000 (2 GB), the size it stores'),
    (0x1d1097c, 'e11361b2', '0100b0d2', 'engine address region: mov x1,#0xf80000000 (62 GB) → mov x1,#0x80000000 (2 GB), the size it reserves'),
    (0x1d10b00, 'e91361b2', '0900b0d2', 'engine address region: mov x9,#0xf80000000 (62 GB) → mov x9,#0x80000000 (2 GB), the size it stores'),
    (0x1d10b0c, 'e11361b2', '0100b0d2', 'engine address region: mov x1,#0xf80000000 (62 GB) → mov x1,#0x80000000 (2 GB), the size it reserves'),
    (0x1d10b34, 'e91361b2', '0900b0d2', 'engine address region: mov x9,#0xf80000000 (62 GB) → mov x9,#0x80000000 (2 GB), the size it stores'),
    (0x1d10b40, 'e11361b2', '0100b0d2', 'engine address region: mov x1,#0xf80000000 (62 GB) → mov x1,#0x80000000 (2 GB), the size it reserves'),
    (0x1d10ef8, 'e91361b2', '0900b0d2', 'engine address region: mov x9,#0xf80000000 (62 GB) → mov x9,#0x80000000 (2 GB), the size it stores'),
    (0x1d10f04, 'e11361b2', '0100b0d2', 'engine address region: mov x1,#0xf80000000 (62 GB) → mov x1,#0x80000000 (2 GB), the size it reserves'),
    (0x1d11738, 'e81361b2', '0800b0d2', 'engine address region: mov x8,#0xf80000000 (62 GB) → mov x8,#0x80000000 (2 GB), the size it stores'),
    (0x1d11744, 'e11361b2', '0100b0d2', 'engine address region: mov x1,#0xf80000000 (62 GB) → mov x1,#0x80000000 (2 GB), the size it reserves'),
    (0x1d119f0, 'e91361b2', '0900b0d2', 'engine address region: mov x9,#0xf80000000 (62 GB) → mov x9,#0x80000000 (2 GB), the size it stores'),
    (0x1d119fc, 'e11361b2', '0100b0d2', 'engine address region: mov x1,#0xf80000000 (62 GB) → mov x1,#0x80000000 (2 GB), the size it reserves'),
    (0x1d10778, '8a75f997', '1f2003d5', 'FPlatformMemory::BaseAllocator: bl 0x1b6dda0 (FMallocBinned3 constructor, 37 GB of address space) → nop; the host allocator\'s vtable is put in at the gate after it (hle_uemalloc.c)'),
  ],
}
OBS = {
  # Little Nightmares II 1.4 main: right after the cut FMallocBinned3 constructor call (CUTS above), x19 = the new allocator object; the host writes its vtable there (hle_uemalloc.c)
  '7f4216b6e784a4b2f880f5bfa581608d': [
    (0x1d10780, 21, 'aa1303e0', 'FPlatformMemory::BaseAllocator: the allocator object is in x19, before it is returned: the host puts its vtable in; orig mov x0, x19'),
  ],
}
