"""Content-defined chunking (CDC).

The whole point of Strata's cheap version history is *deduplication*: when you
commit a new version of a payload, only the regions that actually changed need
to be stored. To make that work even when bytes are inserted or removed (which
shifts everything after them), we cut the payload at boundaries determined by
the *content* itself, not by fixed offsets. Two payloads that share a run of
identical bytes will be cut at the same places inside that run, so the
unchanged chunks hash identically and are stored only once.

This is a small, dependency-free gear-hash CDC in the spirit of FastCDC. It is
deterministic: the same bytes always produce the same chunk boundaries, which
is required for cross-version dedup and for interoperable implementations.
"""

from __future__ import annotations

from typing import Iterator

# Default chunking parameters. Average ~8 KiB chunks keeps the per-chunk
# overhead small while still giving fine-grained dedup. Min/max bound the
# distribution so a pathological input cannot produce huge or tiny chunks.
MIN_SIZE = 2 * 1024
AVG_SIZE = 8 * 1024
MAX_SIZE = 64 * 1024

# A 13-bit mask gives an average chunk size of 2**13 = 8192 bytes.
_MASK = (1 << 13) - 1

# Deterministic gear table: 256 pseudo-random 64-bit values derived from a
# fixed seed via splitmix64. Hard-coding the *generator* (not a giant literal
# table) keeps the spec short and lets any implementation reproduce it exactly.
_SEED = 0x9E3779B97F4A7C15
_MASK64 = (1 << 64) - 1


def _build_gear() -> list[int]:
    table = []
    x = _SEED
    for _ in range(256):
        # splitmix64 step
        x = (x + 0x9E3779B97F4A7C15) & _MASK64
        z = x
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & _MASK64
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & _MASK64
        z = z ^ (z >> 31)
        table.append(z)
    return table


GEAR = _build_gear()


def chunk_bounds(data: bytes,
                 min_size: int = MIN_SIZE,
                 avg_size: int = AVG_SIZE,
                 max_size: int = MAX_SIZE) -> Iterator[tuple[int, int]]:
    """Yield ``(start, end)`` byte ranges for each content-defined chunk."""
    n = len(data)
    if n == 0:
        return
    start = 0
    while start < n:
        # A chunk is at least min_size (unless the tail is shorter).
        end = min(start + max_size, n)
        fp = 0
        i = start + min_size
        if i < end:
            cut = end
            while i < end:
                fp = ((fp << 1) + GEAR[data[i]]) & _MASK64
                if (fp & _MASK) == 0:
                    cut = i + 1
                    break
                i += 1
            end = cut
        yield (start, end)
        start = end


def chunk(data: bytes, **kw) -> Iterator[bytes]:
    """Yield the content-defined chunks of ``data`` as byte strings."""
    for s, e in chunk_bounds(data, **kw):
        yield data[s:e]
