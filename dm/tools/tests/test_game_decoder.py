"""dm/tools/game_decoder.py speaks the Mac host's decoder protocols (DM_OODLE_JOB's job and output files, DM_OODLE_SERVE's requests and answers, dm/host/hle_oodle.c), here with a stand-in
decoder (zlib) so no game files are needed; with DM_TEST_ZLIB_MAIN=<a main NSO that exports inflateInit_ / inflate> (Little Nightmares II's) arm64_emu.py's self-test runs the module's own
zlib under the emulator too."""
import io
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from unittest import mock

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / 'dm/tools'))
import game_decoder


class Zlib:   # a decoder as game_decoder.Decoder is called: (compressed, raw length) → bytes, or None
    name = 'zlib (stand-in)'
    def __call__(self, comp, n):
        try: d = zlib.decompress(comp)
        except zlib.error: return None
        return d if len(d) == n else None


RAWS = [b'', b'x' * 1000, bytes(range(256)) * 40]


class ProtocolTests(unittest.TestCase):
    def test_job(self):
        with tempfile.TemporaryDirectory() as t:
            job = os.path.join(t, 'oodle.job')
            recs = [(zlib.compress(r), len(r)) for r in RAWS] + [(b'not zlib', 10)]
            with open(job, 'wb') as f:
                f.write(struct.pack('<II', 0x4a4f4d44, len(recs)))
                for c, n in recs: f.write(struct.pack('<II', len(c), n) + c)
            with mock.patch('sys.stderr', io.StringIO()): rc = game_decoder.job(Zlib(), job)
            self.assertEqual(rc, 1)   # the last record fails, as the host's exit code says
            out = open(job + '.out', 'rb').read()
            self.assertEqual(struct.unpack_from('<II', out), (0x524f4d44, len(recs)))
            o, got = 8, []
            for _ in recs:
                n = struct.unpack_from('<I', out, o)[0]; got.append(out[o + 4:o + 4 + n]); o += 4 + n
            self.assertEqual(got, RAWS + [b''])
            self.assertEqual(o, len(out))

    def test_serve(self):
        req = b''.join(struct.pack('<QQ', len(c), len(r)) + c for r in RAWS for c in [zlib.compress(r)]) + struct.pack('<QQ', 3, 5) + b'bad'
        stdin, stdout = io.BytesIO(req), io.BytesIO()
        with mock.patch('sys.stdin', mock.Mock(buffer=stdin)), mock.patch('sys.stdout', mock.Mock(buffer=stdout)), mock.patch('sys.stderr', io.StringIO()):
            rc = game_decoder.serve(Zlib())
        self.assertEqual(rc, 1)
        b, o, got = stdout.getvalue(), 0, []
        while o < len(b):
            n = struct.unpack_from('<Q', b, o)[0]; got.append(b[o + 8:o + 8 + n]); o += 8 + n
        self.assertEqual(got, RAWS + [b''])

    @unittest.skipUnless(os.environ.get('DM_TEST_ZLIB_MAIN'), 'DM_TEST_ZLIB_MAIN names no main NSO with zlib')
    def test_emulated_zlib(self):
        r = subprocess.run([sys.executable, REPO / 'dm/tools/arm64_emu.py', 'selftest', os.environ['DM_TEST_ZLIB_MAIN']], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == '__main__':
    unittest.main()
