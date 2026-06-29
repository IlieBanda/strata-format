#!/usr/bin/env python3
"""A runnable tour of Strata: history, dedup, integrity, and recovery.

    python examples/demo.py

Creates everything in a throwaway temp directory and prints what happens.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strata import Strata


def hsize(n):
    f = float(n)
    for u in ["B", "KiB", "MiB"]:
        if f < 1024 or u == "MiB":
            return f"{f:.0f} {u}" if u == "B" else f"{f:.1f} {u}"
        f /= 1024


def main():
    workdir = tempfile.mkdtemp(prefix="strata-demo-")
    archive = os.path.join(workdir, "novel.strata")

    # A realistic, non-degenerate payload: a chapter of lorem-like prose.
    import random
    random.seed(42)
    words = ("the quick brown fox jumps over a lazy dog while time keeps "
             "passing and the river flows gently past the old stone bridge")
    words = words.split()
    chapter = " ".join(random.choice(words) for _ in range(40_000)).encode()

    print("=" * 64)
    print("1. WRAP — turn a plain payload into a smart file")
    print("=" * 64)
    s = Strata.create(archive, chapter, mime="text/plain", name="novel.txt",
                      message="first draft", author="alice")
    print(f"payload:  {hsize(len(chapter))}")
    print(f"archive:  {hsize(os.path.getsize(archive))}  (compressed + indexed)")

    print("\n" + "=" * 64)
    print("2. COMMIT — edit only the beginning, watch dedup work")
    print("=" * 64)
    edited = b"CHAPTER ONE. " + chapter[200:]  # change the front, keep the body
    before = os.path.getsize(archive)
    s.commit(edited, message="add chapter title", author="alice")
    after = os.path.getsize(archive)
    print(f"new payload is {hsize(len(edited))}, but the archive grew only "
          f"{hsize(after - before)}")
    print("→ unchanged chunks were reused instead of re-stored")

    print("\n" + "=" * 64)
    print("3. LOG — the history now lives inside the file")
    print("=" * 64)
    for i, c in enumerate(s.versions()):
        print(f"  v{i+1}  {c.id[:12]}  {hsize(c.size):>9}  {c.message}")

    print("\n" + "=" * 64)
    print("4. TIME TRAVEL — read any past version, exactly")
    print("=" * 64)
    assert s.read(0) == chapter
    assert s.read(-1) == edited
    print("  v1 and v2 both reconstruct byte-for-byte ✓")

    print("\n" + "=" * 64)
    print("5. VERIFY — the file checks itself")
    print("=" * 64)
    print("  " + str(s.verify()).replace("\n", "\n  "))

    print("\n" + "=" * 64)
    print("6. SURVIVE DAMAGE — corrupt the footer, then recover")
    print("=" * 64)
    with open(archive, "r+b") as f:
        f.truncate(os.path.getsize(archive) - 12)  # tear the footer
    print("  footer truncated by a 'bad transfer'...")
    try:
        Strata(archive).read()
    except Exception as e:
        print(f"  read now fails cleanly: {type(e).__name__}")
    Strata(archive).repair()
    print("  strata repair → footer rebuilt from the intact log")
    assert Strata(archive).read(-1) == edited
    print("  latest version recovered byte-for-byte ✓")

    print("\nDemo archive:", archive)


if __name__ == "__main__":
    main()
