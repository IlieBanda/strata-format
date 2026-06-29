"""Test suite for Strata. Run with: python -m pytest -q   (or python tests/test_strata.py)."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strata import Strata
from strata.chunker import chunk, chunk_bounds
from strata.errors import VersionNotFound, CorruptArchive


class TempArchive(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "test.strata")

    def tearDown(self):
        for f in os.listdir(self.dir):
            os.remove(os.path.join(self.dir, f))
        os.rmdir(self.dir)


class TestRoundTrip(TempArchive):
    def test_create_and_read(self):
        data = b"hello strata, the smart file" * 100
        Strata.create(self.path, data, mime="text/plain", name="hello.txt")
        s = Strata(self.path)
        self.assertEqual(s.read(), data)

    def test_empty_payload(self):
        Strata.create(self.path, b"")
        self.assertEqual(Strata(self.path).read(), b"")

    def test_binary_payload(self):
        data = os.urandom(200_000)
        Strata.create(self.path, data)
        self.assertEqual(Strata(self.path).read(), data)

    def test_info(self):
        Strata.create(self.path, b"x" * 10, mime="text/plain", name="x.txt")
        info = Strata(self.path).info()
        self.assertEqual(info["name"], "x.txt")
        self.assertEqual(info["mime"], "text/plain")
        self.assertEqual(info["versions"], 1)


class TestVersioning(TempArchive):
    def test_multiple_versions(self):
        s = Strata.create(self.path, b"version one content here")
        s.commit(b"version two content here")
        s.commit(b"version three content here")
        versions = s.versions()
        self.assertEqual(len(versions), 3)
        self.assertEqual(s.read(0), b"version one content here")
        self.assertEqual(s.read(1), b"version two content here")
        self.assertEqual(s.read(-1), b"version three content here")

    def test_parent_chain(self):
        s = Strata.create(self.path, b"a")
        s.commit(b"b")
        s.commit(b"c")
        v = s.versions()
        self.assertIsNone(v[0].parent)
        self.assertEqual(v[1].parent, v[0].id)
        self.assertEqual(v[2].parent, v[1].id)

    def test_missing_version(self):
        Strata.create(self.path, b"only one")
        with self.assertRaises(VersionNotFound):
            Strata(self.path).read(5)


class TestDeduplication(TempArchive):
    def test_unchanged_tail_is_deduped(self):
        # A large shared body with only a small prefix change should add very
        # little to the archive: that is the whole copy-on-write promise.
        body = os.urandom(500_000)
        s = Strata.create(self.path, b"AAAA" + body)
        size_after_v1 = os.path.getsize(self.path)
        s.commit(b"BBBB" + body)  # same body, different 4-byte prefix
        growth = os.path.getsize(self.path) - size_after_v1
        # Growth should be a tiny fraction of the 500 KB body.
        self.assertLess(growth, 100_000,
                        f"dedup failed: archive grew by {growth} bytes")
        self.assertEqual(s.read(0), b"AAAA" + body)
        self.assertEqual(s.read(1), b"BBBB" + body)

    def test_diff_reports_sharing(self):
        body = os.urandom(300_000)
        s = Strata.create(self.path, body + b"end1")
        s.commit(body + b"end2")
        d = s.diff(0, 1)
        self.assertGreater(d["chunks_shared"], 0)


class TestIntegrity(TempArchive):
    def test_verify_clean(self):
        s = Strata.create(self.path, os.urandom(100_000))
        s.commit(os.urandom(100_000))
        report = s.verify()
        self.assertTrue(report.ok, str(report))
        self.assertEqual(len(report.blobs_bad), 0)

    def test_detects_corruption(self):
        data = b"important data " * 5000
        Strata.create(self.path, data)
        # Flip a byte in the middle of the blob region.
        with open(self.path, "r+b") as f:
            f.seek(200)
            b = f.read(1)
            f.seek(200)
            f.write(bytes([b[0] ^ 0xFF]))
        report = Strata(self.path).verify()
        self.assertFalse(report.ok)
        self.assertGreater(len(report.blobs_bad), 0)

    def test_detects_truncated_footer(self):
        Strata.create(self.path, b"data here")
        with open(self.path, "r+b") as f:
            f.truncate(os.path.getsize(self.path) - 5)
        with self.assertRaises(CorruptArchive):
            Strata(self.path).read()


class TestRecovery(TempArchive):
    def test_repair_rewrites_footer(self):
        s = Strata.create(self.path, b"recoverable content " * 1000)
        s.commit(b"second version content " * 1000)
        good = s.read(-1)
        # Destroy the footer.
        with open(self.path, "r+b") as f:
            f.truncate(os.path.getsize(self.path) - 10)
        # Repair should rebuild a working footer from the intact log.
        Strata(self.path).repair()
        self.assertEqual(Strata(self.path).read(-1), good)


class TestChunker(unittest.TestCase):
    def test_deterministic(self):
        data = os.urandom(300_000)
        a = list(chunk_bounds(data))
        b = list(chunk_bounds(data))
        self.assertEqual(a, b)

    def test_chunks_reassemble(self):
        data = os.urandom(300_000)
        self.assertEqual(b"".join(chunk(data)), data)

    def test_insertion_shifts_few_chunks(self):
        # Inserting bytes near the front should leave most later chunks intact.
        data = os.urandom(400_000)
        chunks_a = set(c for c in _hashed(chunk(data)))
        modified = data[:1000] + b"INSERTED" + data[1000:]
        chunks_b = set(c for c in _hashed(chunk(modified)))
        shared = chunks_a & chunks_b
        # Most chunks should survive an insertion (resync property of CDC).
        self.assertGreater(len(shared), len(chunks_a) * 0.5)


class TestStreamingAPI(TempArchive):
    def test_wrap_file(self):
        src = os.path.join(self.dir, "hello.txt")
        with open(src, "wb") as f:
            f.write(b"hello from a file" * 50)
        s = Strata.wrap_file(src, self.path)
        with open(src, "rb") as f:
            self.assertEqual(s.read(-1), f.read())

    def test_commit_file(self):
        Strata.create(self.path, b"v1 content" * 50)
        src = os.path.join(self.dir, "v2.txt")
        with open(src, "wb") as f:
            f.write(b"v2 content" * 50)
        Strata(self.path).commit_file(src, message="v2")
        self.assertEqual(len(Strata(self.path).versions()), 2)
        with open(src, "rb") as f:
            self.assertEqual(Strata(self.path).read(-1), f.read())

    def test_checkout_to_file(self):
        data = b"checkout content" * 30
        Strata.create(self.path, data)
        out = os.path.join(self.dir, "out.bin")
        n = Strata(self.path).checkout_to_file(out)
        self.assertEqual(n, len(data))
        with open(out, "rb") as f:
            self.assertEqual(f.read(), data)


def _hashed(pieces):
    import hashlib
    return [hashlib.blake2b(p, digest_size=32).hexdigest() for p in pieces]


if __name__ == "__main__":
    unittest.main(verbosity=2)
