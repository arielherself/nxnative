#!/usr/bin/env python3
# The game's own decoder for the offline shader tools off a Mac: what the Mac host does with DM_OODLE_JOB / DM_OODLE_SERVE (dm/host/hle_oodle.c), with the decoder run under an ARM64 emulator
# (arm64_emu.py, Unicorn) instead of natively, so a Linux PC packs the games whose shaders only the game's code decodes: Unreal Engine's Oodle (Octopath Traveler 0, FINAL FANTASY RESONANCE),
# Trinity's Oodle (Pokémon Legends: Z-A), DRAGON QUEST XI S's LZ5. The decoder is the host's: LZ5_decompress_safe when main exports it, else the one function that references the string
# "OodleLZ_Decompress" (its profiler scope), called as the host calls it (fuzz safe, no CRC check, silent, 16 MB of decoder memory given, unthreaded). The protocols are the host's:
#   DM_OODLE_JOB=<job file>: "DMOJ", u32 count, per record u32 compressed length, u32 raw length, the bytes → <job file>.out: "DMOR", u32 count, per record u32 decoded length (0 = failed)
#     and the bytes; exit 1 when a record failed
#   DM_OODLE_SERVE=1: requests on stdin (u64 compressed length, u64 raw length, the bytes), answers on stdout (u64 bytes decoded, 0 on failure, then the bytes), until stdin ends
# Usage: game_decoder.py --nso <main NSO> [<rewritten modules>]   (called as the Mac host is: the NSP script writes a launcher that gives --nso, the extracted main; the modules are not read)
import os, struct, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import arm64_emu

SCRATCH = 16 << 20   # the decoder's work memory, so Oodle never calls an allocator (hle_oodle.c)


def log(msg): print(f'[dm] {msg}', file=sys.stderr, flush=True)


class Decoder:
    def __init__(self, nso, mode):
        self.e = e = arm64_emu.Emu(nso)
        if 'LZ5_decompress_safe' in e.syms: self.name, self.off, self.lz5 = 'LZ5_decompress_safe', e.syms['LZ5_decompress_safe'], True
        else:
            self.name, self.off, self.lz5 = 'OodleLZ_Decompress', e.find_referrer('OodleLZ_Decompress'), False
            if not self.off: sys.exit(f'[RED] {mode}: main has no function that references "OodleLZ_Decompress" (no Oodle decoder) and exports no LZ5_decompress_safe')
        log(f'{mode}: {self.name} at main+{self.off:#x}, emulated (arm64_emu.py)')

    def __call__(self, comp, n):
        """The decoded bytes, or None when the decoder fails"""
        e = self.e; e.reset_heap()
        c, r = e.alloc(len(comp)), e.alloc(n + 0x10000); e.write(c, comp)
        try:
            if self.lz5:
                if len(comp) > 0x7FFFFFFF or n > 0x7FFFFFFF: return None
                got = e.call(self.off, c, r, len(comp), n) & 0xFFFFFFFF
                got = got - (1 << 32) if got >> 31 else got
            else:
                s = e.alloc(SCRATCH)
                got = e.call(self.off, c, len(comp), r, n, 1, 0, 0, 0, 0, 0, 0, s, SCRATCH, 3)
                got = got - (1 << 64) if got >> 63 else got
        except RuntimeError as x:
            log(f'[RED] the decoder stopped: {x}'); return None
        return e.read(r, n) if got == n else None


def job(dec, path):
    with open(path, 'rb') as f: return _job(dec, path, f)


def _job(dec, path, f):
    def rd32():
        b = f.read(4)
        if len(b) != 4: sys.exit('[RED] DM_OODLE_JOB: the job file ends early')
        return struct.unpack('<I', b)[0]
    if rd32() != 0x4a4f4d44: sys.exit(f'[RED] DM_OODLE_JOB: {path} is not a job file')
    n, bad, total = rd32(), 0, 0
    with open(path + '.out', 'wb') as out:
        out.write(struct.pack('<II', 0x524f4d44, n))
        for i in range(n):
            cl, rl = rd32(), rd32(); c = f.read(cl)
            d = dec(c, rl) if len(c) == cl else None
            if d is None and bad < 8: log(f'[RED] DM_OODLE_JOB: record {i} ({cl} → {rl} bytes) failed')
            bad += d is None; total += len(d or b'')
            out.write(struct.pack('<I', len(d) if d is not None else 0)); out.write(d or b'')
    log(f'DM_OODLE_JOB: {n - bad} records decoded with the game\'s {dec.name} ({bad} failed, {total / 1048576:.1f} MB out) → {path}.out')
    return 1 if bad else 0


def serve(dec):
    i, o, n, bad = sys.stdin.buffer, sys.stdout.buffer, 0, 0
    while True:
        h = i.read(16)
        if len(h) < 16: break
        cl, rl = struct.unpack('<QQ', h)
        if cl > 1 << 32 or rl > 1 << 32: sys.exit(f'[RED] DM_OODLE_SERVE: request too large ({cl:#x} → {rl:#x})')
        c = i.read(cl)
        if len(c) != cl: sys.exit('[RED] DM_OODLE_SERVE: request cut short')
        d = dec(c, rl) if rl else b''
        if d is None and bad < 8: log(f'[RED] DM_OODLE_SERVE: request {n} ({cl} → {rl} bytes) failed')
        bad += d is None
        o.write(struct.pack('<Q', len(d) if d else 0)); o.write(d or b''); o.flush(); n += 1
    log(f'DM_OODLE_SERVE: {n} requests, {bad} failed')
    return 1 if bad else 0


def main(a):
    if len(a) < 2 or a[0] != '--nso': sys.exit('usage: game_decoder.py --nso <main NSO> [<rewritten modules>]   (with DM_OODLE_JOB=<job file> or DM_OODLE_SERVE=1)')
    if os.environ.get('DM_OODLE_JOB'):
        return job(Decoder(a[1], 'DM_OODLE_JOB'), os.environ['DM_OODLE_JOB'])
    if os.environ.get('DM_OODLE_SERVE'):
        return serve(Decoder(a[1], 'DM_OODLE_SERVE'))
    sys.exit('[RED] game_decoder.py: neither DM_OODLE_JOB nor DM_OODLE_SERVE is set')


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
