#!/usr/bin/env python3
# A function of a game's own code run under an ARM64 emulator (Unicorn: pip install unicorn), for the offline tools where the game's code cannot run natively: the Mac host runs it on Apple
# silicon (dm/host/hle_oodle.c), a Linux PC on x86-64 cannot. Only self-contained functions: the decoders the shader tools need (Oodle, LZ5, zlib), which compute and call nothing but the C
# library. The module is the extracted NSO as it is (not the rewritten one: that needs the host's run time): its segments and bss at one base, its relocations applied (RELATIVE, and
# GLOB_DAT / JUMP_SLOT / ABS64 to its own symbols), every import a stub that the C library functions below answer in Python (memcpy, memset, malloc, ...); an import with no answer
# stops the call with its name. The module's own malloc / free / calloc / realloc / aligned_alloc (Unreal Engine defines them over FMalloc, which nninitStartup sets up) are answered the
# same way: a bump heap that each call starts empty. Calls follow AAPCS64 (x0-x7, then 8-byte stack slots); FP / SIMD is on.
# Usage: arm64_emu.py selftest <main NSO>    inflate a zlib stream with the module's own zlib (inflateInit_ / inflate), as a check of the emulation (Little Nightmares II's main has it)
import struct, sys, zlib

PAGE = 0x1000
BASE, STUBS, STACK, HEAP, TLS = 0x10_0000_0000, 0x0F_0000_0000, 0x0E_0000_0000, 0x20_0000_0000, 0x0D_0000_0000
STACK_SIZE, HEAP_SIZE = 8 << 20, 1 << 30
RET = 0xD65F03C0   # ret
ALLOC = ('malloc', 'free', 'calloc', 'realloc', 'aligned_alloc', 'memalign', 'posix_memalign')


def nso_image(path):
    """The NSO's segments and bss as one image, and (text end, read-only start, data start, data end)"""
    import lz4.block
    d = open(path, 'rb').read()
    flags = struct.unpack_from('<I', d, 0xc)[0]; segs = [struct.unpack_from('<III', d, 0x10 + i * 0x10) for i in range(3)]; csz = struct.unpack_from('<III', d, 0x60)
    img = bytearray(segs[2][1] + segs[2][2] + struct.unpack_from('<I', d, 0x3c)[0])
    for i, (fo, mo, sz) in enumerate(segs):
        raw = d[fo:fo + csz[i]]
        img[mo:mo + sz] = lz4.block.decompress(raw, uncompressed_size=sz) if flags >> i & 1 else raw
    return img, (segs[0][1] + segs[0][2], segs[1][1], segs[2][1], segs[2][1] + segs[2][2])


class Stop(Exception):
    pass


class Emu:
    def __init__(self, nso):
        try: from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
        except ImportError: sys.exit(f'[RED] arm64_emu: the Python running this ({sys.executable}) has no Unicorn (pip install unicorn): it runs the game\'s own decoder off a Mac')
        from unicorn import arm64_const as A
        self.A = A
        self.img, (self.text_end, self.ro, self.data, self.data_end) = nso_image(nso)
        self.syms, self.imports = {}, {}   # defined symbol → offset; import name → stub address
        self._relocate()
        self.uc = uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        size = (len(self.img) + PAGE - 1) & ~(PAGE - 1)
        uc.mem_map(BASE, size); uc.mem_write(BASE, bytes(self.img))
        nstub = (len(self.imports) + 2) * 4
        uc.mem_map(STUBS, (nstub + PAGE - 1) & ~(PAGE - 1)); uc.mem_write(STUBS, struct.pack('<I', RET) * (len(self.imports) + 2))
        uc.mem_map(STACK, STACK_SIZE); uc.mem_map(HEAP, HEAP_SIZE); uc.mem_map(TLS, 0x10000)
        uc.reg_write(A.UC_ARM64_REG_CPACR_EL1, 3 << 20)   # FP / SIMD on
        uc.reg_write(A.UC_ARM64_REG_TPIDRRO_EL0, TLS); uc.reg_write(A.UC_ARM64_REG_TPIDR_EL0, TLS + 0x8000)
        self.at = {v: k for k, v in self.imports.items()}   # stub address → import name
        for n in ALLOC:   # the module's own allocator, answered as the imports are
            if n in self.syms: self.at[BASE + self.syms[n]] = n
        self.ret_trap = STUBS + 4 * (len(self.imports) + 1)
        uc.hook_add(UC_HOOK_CODE, self._stub, begin=STUBS, end=STUBS + nstub)
        for a, n in self.at.items():
            if a < STUBS or a >= STUBS + nstub: uc.hook_add(UC_HOOK_CODE, self._stub, begin=a, end=a)
        self.error = None; self.reset_heap()

    # ---- the module ----
    def _relocate(self):
        img = self.img
        mod0 = struct.unpack_from('<I', img, 4)[0]
        if img[mod0:mod0 + 4] != b'MOD0': raise ValueError('no MOD0')
        dyn = mod0 + struct.unpack_from('<i', img, mod0 + 4)[0]
        tags = {}
        for i in range(4096):
            t, v = struct.unpack_from('<qQ', img, dyn + i * 16)
            if t == 0: break
            tags.setdefault(t, v)
        symtab, strtab = tags[6], tags[5]
        def sym(i):
            name, info, other, shndx, value, size = struct.unpack_from('<IBBHQQ', img, symtab + i * 24)
            return img[strtab + name:img.index(b'\0', strtab + name)].decode('latin1'), shndx != 0, value
        n = (strtab - symtab) // 24 if strtab > symtab else 0
        for i in range(1, n):
            try: nm, defined, value = sym(i)
            except (struct.error, ValueError): break
            if defined and nm: self.syms.setdefault(nm, value)
        for start, size in ((tags.get(7), tags.get(8)), (tags.get(23), tags.get(2))):   # RELA, JMPREL
            if not start: continue
            for o in range(start, start + size, 24):
                off, info, add = struct.unpack_from('<QQq', img, o); typ, si = info & 0xffffffff, info >> 32
                if typ == 0x403: struct.pack_into('<Q', img, off, BASE + add); continue   # R_AARCH64_RELATIVE
                if typ not in (0x401, 0x402, 0x101): continue   # GLOB_DAT, JUMP_SLOT, ABS64 (TLS relocations: none in the decoders)
                nm, defined, value = sym(si)
                if defined: s = BASE + value
                else: s = self.imports.setdefault(nm, STUBS + 4 * len(self.imports))
                struct.pack_into('<Q', img, off, (s + add) & (1 << 64) - 1)

    def find_referrer(self, name):
        """The start of the one function whose code takes the address of the NUL-terminated string name (adrp + add), from the module's .eh_frame_hdr (hle_oodle.c oodle_find:
        OodleLZ_Decompress has no export, its profiler scope names it). None when the string or the function is not one"""
        img, s = self.img, name.encode() + b'\0'
        strs, p = set(), self.ro
        while (p := img.find(s, p, self.data_end)) >= 0:
            if img[p - 1] == 0: strs.add(p)
            p += len(s)
        if not strs: return None
        words = struct.unpack_from(f'<{self.text_end // 4}I', img); hits = set()
        for pc, w in enumerate(words):
            if (w & 0x9F000000) != 0x90000000: continue   # adrp
            imm = ((w >> 29) & 3) | (((w >> 5) & 0x7FFFF) << 2)
            if imm & (1 << 20): imm -= 1 << 21
            page = ((pc * 4) & ~0xFFF) + (imm << 12)
            for k in range(1, 8):
                if pc + k >= len(words): break
                w2 = words[pc + k]
                if (w2 & 0xFF800000) != 0x91000000 or ((w2 >> 5) & 31) != (w & 31): continue   # add xd, xn (the adrp's), #imm
                if page + ((w2 >> 10) & 0xFFF) in strs: hits.add(self.func_start((pc + k) * 4))
                break
        return hits.pop() if len(hits) == 1 else None

    def func_start(self, off):
        img = self.img; m0 = struct.unpack_from('<I', img, 4)[0]; eh = m0 + struct.unpack_from('<i', img, m0 + 16)[0]
        def rd(o, enc):
            fmt, app = enc & 0xF, enc & 0x70
            if fmt == 0x3: v, n = struct.unpack_from('<I', img, o)[0], 4
            elif fmt == 0xB: v, n = struct.unpack_from('<i', img, o)[0], 4
            elif fmt == 0x4: v, n = struct.unpack_from('<Q', img, o)[0], 8
            else: raise ValueError(f'.eh_frame_hdr encoding {enc:#x}')
            return (v + o if app == 0x10 else v + eh if app == 0x30 else v), n
        o = eh + 4; _, k = rd(o, img[eh + 1]); o += k; cnt, k = rd(o, img[eh + 2]); o += k; best = 0
        for _ in range(cnt):
            s, k = rd(o, img[eh + 3]); o += k; _, k = rd(o, img[eh + 3]); o += k
            if best < s <= off: best = s
        return best

    # ---- memory ----
    def reset_heap(self): self.brk, self.sizes = HEAP, {}

    def alloc(self, n, align=16):
        a = (self.brk + align - 1) & ~(align - 1)
        if a + n > HEAP + HEAP_SIZE: raise Stop(f'the emulated heap ({HEAP_SIZE >> 20} MB) is full')
        self.brk, self.sizes[a] = a + max(n, 1), n
        return a

    def write(self, a, b): self.uc.mem_write(a, bytes(b))
    def read(self, a, n): return bytes(self.uc.mem_read(a, n))
    def cstr(self, a):
        out = b''
        while 0 not in (c := self.read(a + len(out), 64)): out += c
        return out + c[:c.index(0)]

    # ---- C library ----
    def _stub(self, uc, addr, size, _):
        A, name = self.A, self.at.get(addr)
        if addr == self.ret_trap: return
        if name is None: return
        x = [uc.reg_read(getattr(A, f'UC_ARM64_REG_X{i}')) for i in range(4)]
        try: r = self._libc(name, x)
        except Stop as e: self.error = str(e); uc.emu_stop(); return
        if r is None: self.error = f'the code called {name}, which the emulation does not answer'; uc.emu_stop(); return
        uc.reg_write(A.UC_ARM64_REG_X0, r & (1 << 64) - 1)
        if addr >= BASE: uc.reg_write(A.UC_ARM64_REG_PC, uc.reg_read(A.UC_ARM64_REG_LR))   # the module's own allocator: return from it at once

    def _libc(self, name, x):
        if name in ('memcpy', 'memmove'):
            if x[2]: self.write(x[0], self.read(x[1], x[2]))
            return x[0]
        if name == 'memset':
            if x[2]: self.write(x[0], bytes([x[1] & 0xff]) * x[2])
            return x[0]
        if name == 'memcmp':
            a, b = self.read(x[0], x[2]), self.read(x[1], x[2])
            return next(((p - q) & (1 << 64) - 1 for p, q in zip(a, b) if p != q), 0)
        if name == 'strlen': return len(self.cstr(x[0]))
        if name == 'malloc': return self.alloc(x[0])
        if name == 'calloc': a = self.alloc(x[0] * x[1]); self.write(a, bytes(x[0] * x[1])); return a
        if name in ('aligned_alloc', 'memalign'): return self.alloc(x[1], max(x[0], 16))
        if name == 'posix_memalign': a = self.alloc(x[2], max(x[1], 16)); self.write(x[0], struct.pack('<Q', a)); return 0
        if name == 'realloc':
            a = self.alloc(x[1])
            if x[0]: self.write(a, self.read(x[0], min(self.sizes.get(x[0], 0), x[1])))
            return a
        if name == 'free': return 0
        return None

    # ---- calls ----
    def call(self, off, *args, limit=0):
        """The module's function at offset off with integer / pointer arguments (AAPCS64: x0-x7, the rest in 8-byte stack slots); returns x0. Raises RuntimeError when the call stops"""
        A, uc = self.A, self.uc
        sp = STACK + STACK_SIZE - 0x1000 - 8 * max(0, len(args) - 8); sp &= ~0xF
        for i, a in enumerate(args[:8]): uc.reg_write(getattr(A, f'UC_ARM64_REG_X{i}'), a & (1 << 64) - 1)
        for i, a in enumerate(args[8:]): self.write(sp + 8 * i, struct.pack('<Q', a & (1 << 64) - 1))
        uc.reg_write(A.UC_ARM64_REG_SP, sp); uc.reg_write(A.UC_ARM64_REG_LR, self.ret_trap); uc.reg_write(A.UC_ARM64_REG_X29, 0)
        self.error = None
        from unicorn import UcError
        try: uc.emu_start(BASE + off, self.ret_trap, count=limit)
        except UcError as e:
            pc = uc.reg_read(A.UC_ARM64_REG_PC)
            raise RuntimeError(f'emulation stopped at {"main+%#x" % (pc - BASE) if pc >= BASE else hex(pc)}: {e}') from None
        if self.error: raise RuntimeError(self.error)
        if uc.reg_read(A.UC_ARM64_REG_PC) != self.ret_trap: raise RuntimeError('the call did not return')
        return uc.reg_read(A.UC_ARM64_REG_X0)


def selftest(nso):
    """The module's own zlib inflates a stream Python's zlib wrote: relocations, the C library stubs and the calling convention at work"""
    e = Emu(nso)
    if not {'inflateInit_', 'inflate'} <= e.syms.keys(): sys.exit(f'{nso} exports no inflateInit_ / inflate')
    raw = bytes(range(256)) * 300 + b'nxnative' * 1000; comp = zlib.compress(raw, 9)
    strm, ver, src, dst = e.alloc(112), e.alloc(16), e.alloc(len(comp)), e.alloc(len(raw))
    e.write(strm, bytes(112)); e.write(ver, b'1.2.11\0'); e.write(src, comp)
    rc = e.call(e.syms['inflateInit_'], strm, ver, 112)
    if rc: sys.exit(f'inflateInit_ returned {rc}')
    e.write(strm, struct.pack('<QIxxxxQ', src, len(comp), 0)); e.write(strm + 24, struct.pack('<QI', dst, len(raw)))   # next_in, avail_in, total_in; next_out, avail_out
    rc = e.call(e.syms['inflate'], strm, 4)   # Z_FINISH
    out = e.read(dst, len(raw))
    print(f'inflate returned {rc} (1 = Z_STREAM_END); output {"matches" if out == raw else "DIFFERS"} ({len(raw)} bytes)')
    return 0 if rc == 1 and out == raw else 1


if __name__ == '__main__':
    if sys.argv[1:2] == ['selftest'] and len(sys.argv) == 3: sys.exit(selftest(sys.argv[2]))
    sys.exit('usage: arm64_emu.py selftest <main NSO>')
