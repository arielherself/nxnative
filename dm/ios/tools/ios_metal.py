# The single compile standard for the iOS shader packaging chain: MSL from any source (PC chain / constant patches / in future, the Switch original translated via Ryujinx) → iphoneos metallib.
# Compile parameters are defined only once, here; the chain.metal record in the reflection json is generated from the same parameters, record == actual.
import os, shutil, subprocess
# -fmetal-math-mode=relaxed: keep fast math's reassociation / contraction, but forbid assuming there are no NaN / Inf. The translated shaders reproduce Maxwell's NaN semantics with isnan selects (8337 / 12809 use them),
# and the default fast mode lets each GPU's backend compiler treat them as always false; on the A20 Pro the opening course's odd frames had 727k NaN pixels in the main image (a few dozen with the same AIR on the M4 Pro), which became the black band / black-edged glow
# -fpreserve-invariance: [[invariant]] position outputs only agree across shaders with this flag (as dmsh_build.py's MAC_METAL: a depth pre-pass followed by a less-or-equal G-buffer)
METAL = ['xcrun', '-sdk', 'iphoneos', 'metal', '-c', '-mios-version-min=18.0', '-fmetal-math-mode=relaxed', '-fpreserve-invariance']
METALLIB = ['xcrun', '-sdk', 'iphoneos', 'metallib']
# MSL source libraries: where no Metal compiler runs, a "library" is the shader's Metal source itself, in the file the metallib would be (no MTLB magic), and the device compiles it
# with the same options (Metal 3.2 for iOS 18, relaxed math, invariance preserved: dm/gfx/nvn/mtl_srclib.m), all of them at the app's first start. On Linux no Metal compiler can run:
# upstream LLVM has no Metal front end or AIR target, Xcode 26's metal is a macOS shim for a toolchain it downloads separately, and the Windows tools need Wine and an Apple developer login.
# DMSH_SOURCE_LIBS=1 makes source libraries on a Mac too (to test them); without xcrun they are the only kind
SOURCE = os.environ.get('DMSH_SOURCE_LIBS') == '1' or shutil.which('xcrun') is None
SOURCE_RECORD = 'MSL source library, compiled on the device: Metal 3.2 (iOS 18), math mode relaxed, preserve invariance'
RECORD = SOURCE_RECORD if SOURCE else ' '.join(METAL) + '; ' + ' '.join(METALLIB)   # Written into json chain.metal

def compile(msl, air, lib):
    if SOURCE: shutil.copyfile(msl, lib); return   # the source is the library (see SOURCE)
    subprocess.run([*METAL, msl, '-o', air], check=True)
    subprocess.run([*METALLIB, air, '-o', lib], check=True)
