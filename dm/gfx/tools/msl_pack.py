# MSL source packs: the Vulkan and GL layers' one library per game (vk_shaders.py, gl_shaders.py) where no Metal compiler runs (Linux: dm/ios/tools/ios_metal.py SOURCE). Where the metallib
# linked from every shader's AIR would be, the file holds every shader's MSL as a part of its own (the shaders' helper names may clash, so they are not one source): a first line
# "//@@dmsh-msl-pack 1", then per shader "//@@part <entry> [<entry>...]" and its MSL. The host compiles a part when one of its entry points is first asked for, and all of them at the app's
# first start (dm/gfx/nvn/mtl_srclib.m), with ios_metal.py's options
import os, re, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '../../ios/tools')); import ios_metal

SOURCE = ios_metal.SOURCE   # no Metal compiler here: write packs instead of metallibs
ENTRY = re.compile(r'^\s*(?:\[\[[^\]]*\]\]\s*)?(?:vertex|fragment|kernel)\s+[\w:<>, ]+?\s+(\w+)\s*\(', re.M)

def write(msls, lib):
    """The pack of the MSL files msls at lib; returns lib. An MSL file with no entry point stops (the host would find nothing of it)"""
    parts = []
    for m in msls:
        src = open(m).read(); names = ENTRY.findall(src)
        if not names: sys.exit(f'[RED] {m}: no vertex, fragment or kernel function for the MSL source pack')
        parts.append(f'//@@part {" ".join(names)}\n{src.rstrip()}\n')
    with open(lib + '.tmp', 'w') as f: f.write('//@@dmsh-msl-pack 1\n' + ''.join(parts))
    os.replace(lib + '.tmp', lib)
    return lib
