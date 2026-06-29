"""Strata - a self-contained, versioned, self-verifying file container.

Strata wraps *any* payload (an image, a PDF, a database, a source file, raw
bytes) into a single portable file that carries, inside itself:

  * its full version history (content-addressed, deduplicated snapshots),
  * cryptographic integrity for every byte (BLAKE2b per chunk),
  * self-description (what it is, how to open it, who made it, notes).

It is to a plain file what a copy-on-write filesystem (btrfs/ZFS) is to FAT:
the same data, but now it remembers its past, checks itself, and explains
itself - and all of that travels with the file when you copy or send it.

The reference implementation is pure-Python and dependency-free.
"""

from .core import Strata, Commit, VerifyReport
from .errors import StrataError, CorruptArchive, VersionNotFound

__version__ = "0.1.0"
__all__ = [
    "Strata",
    "Commit",
    "VerifyReport",
    "StrataError",
    "CorruptArchive",
    "VersionNotFound",
    "__version__",
]
