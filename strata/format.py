"""Low-level on-disk container format for Strata.

A Strata file is an *append-only log of records* followed by a fixed-size
footer. Appending is what makes new versions cheap (the copy-on-write spirit):
committing a new version never rewrites existing bytes, it only appends the
blobs that are new plus a fresh commit and index.

Layout::

    +--------------------------------------------------+
    | MAGIC (8 bytes)                                  |
    | record | record | record | ...                   |   <- append region
    | FOOTER (fixed size, at the very end)             |
    +--------------------------------------------------+

Each *record* is::

    [type:1][flags:1][length:8 BE][payload:length]

Record types:
    0x01 BLOB    - one (optionally zlib-compressed) content chunk
    0x02 COMMIT  - UTF-8 JSON describing one version
    0x03 INDEX   - UTF-8 JSON: content-hash -> location of every live blob

The FOOTER lets a reader find the latest state in O(1) by seeking to the end::

    [MAGIC 8]["FOOT" 4][index_offset:8 BE][commit_offset:8 BE][digest:16]

``digest`` is the BLAKE2b-128 of the rest of the footer, so a truncated or
torn file is detected immediately. Because the format is append-only, an
older footer remains intact earlier in the file, which is what makes
*recovery* of previous good states possible after a bad write.
"""

from __future__ import annotations

import hashlib
import struct
import zlib
from dataclasses import dataclass

from .errors import CorruptArchive

MAGIC = b"STRATA\x01\x00"  # 8 bytes; trailing byte is the format version (1)
FORMAT_VERSION = 1

# Record types
T_BLOB = 0x01
T_COMMIT = 0x02
T_INDEX = 0x03

# Record flags
F_ZLIB = 0x01  # payload is zlib-compressed

_REC_HDR = struct.Struct(">BBQ")          # type, flags, length
_FOOTER = struct.Struct(">8s4sQQ16s")     # magic, "FOOT", idx_off, commit_off, digest
FOOTER_SIZE = _FOOTER.size


def content_hash(data: bytes) -> str:
    """Stable content address for a chunk: hex BLAKE2b-256 of the raw bytes."""
    return hashlib.blake2b(data, digest_size=32).hexdigest()


@dataclass
class Record:
    type: int
    flags: int
    payload: bytes
    offset: int  # absolute offset of the record header in the file


class Writer:
    """Append records to an open binary file handle and finalize a footer."""

    def __init__(self, fh):
        self.fh = fh

    def tell(self) -> int:
        return self.fh.tell()

    def write_record(self, rtype: int, payload: bytes, flags: int = 0) -> int:
        offset = self.fh.tell()
        self.fh.write(_REC_HDR.pack(rtype, flags, len(payload)))
        self.fh.write(payload)
        return offset

    def write_blob(self, raw: bytes, compress: bool = True) -> tuple[int, int, int]:
        """Write a blob; return ``(offset, stored_length, flags)``."""
        flags = 0
        payload = raw
        if compress:
            packed = zlib.compress(raw, 6)
            # Only keep compression if it actually helped.
            if len(packed) < len(raw):
                payload = packed
                flags = F_ZLIB
        offset = self.write_record(T_BLOB, payload, flags)
        return offset, len(payload), flags

    def write_footer(self, index_offset: int, commit_offset: int) -> None:
        body = MAGIC + b"FOOT" + struct.pack(">QQ", index_offset, commit_offset)
        digest = hashlib.blake2b(body, digest_size=16).digest()
        self.fh.write(_FOOTER.pack(MAGIC, b"FOOT", index_offset, commit_offset, digest))
        self.fh.flush()


class Reader:
    """Random-access reader over a Strata container."""

    def __init__(self, fh):
        self.fh = fh

    def read_magic(self) -> None:
        self.fh.seek(0)
        magic = self.fh.read(8)
        if magic != MAGIC:
            raise CorruptArchive("not a Strata file (bad magic)")

    def read_footer(self) -> tuple[int, int]:
        """Return ``(index_offset, commit_offset)`` from the trailing footer."""
        self.fh.seek(0, 2)
        size = self.fh.tell()
        if size < 8 + FOOTER_SIZE:
            raise CorruptArchive("file too small to be a Strata container")
        self.fh.seek(size - FOOTER_SIZE)
        raw = self.fh.read(FOOTER_SIZE)
        magic, foot, idx_off, commit_off, digest = _FOOTER.unpack(raw)
        if magic != MAGIC or foot != b"FOOT":
            raise CorruptArchive("footer not found or damaged")
        body = MAGIC + b"FOOT" + struct.pack(">QQ", idx_off, commit_off)
        if hashlib.blake2b(body, digest_size=16).digest() != digest:
            raise CorruptArchive("footer checksum mismatch (file truncated?)")
        return idx_off, commit_off

    def read_record(self, offset: int) -> Record:
        self.fh.seek(offset)
        hdr = self.fh.read(_REC_HDR.size)
        if len(hdr) != _REC_HDR.size:
            raise CorruptArchive(f"short record header at offset {offset}")
        rtype, flags, length = _REC_HDR.unpack(hdr)
        payload = self.fh.read(length)
        if len(payload) != length:
            raise CorruptArchive(f"short record payload at offset {offset}")
        return Record(rtype, flags, payload, offset)

    def read_blob(self, offset: int) -> bytes:
        rec = self.read_record(offset)
        if rec.type != T_BLOB:
            raise CorruptArchive(f"expected blob at {offset}, found type {rec.type}")
        if rec.flags & F_ZLIB:
            return zlib.decompress(rec.payload)
        return rec.payload

    def scan_records(self, skip_payload_types: frozenset = frozenset()):
        """Yield every structurally valid record from the start of the file.

        Stops at the first malformed record. Used by recovery to rebuild an
        index from intact blobs/commits when the trailing footer is damaged.

        For record types in ``skip_payload_types`` the payload is seeked past
        rather than read (the yielded ``Record.payload`` is empty). This keeps
        history scans fast: listing commits does not pull every blob into RAM.
        """
        self.fh.seek(0, 2)
        size = self.fh.tell()
        pos = 8  # skip magic
        # The footer begins with MAGIC ('S'), which is not a valid record
        # type byte, so the scan naturally stops before consuming it.
        while pos + _REC_HDR.size <= size:
            self.fh.seek(pos)
            hdr = self.fh.read(_REC_HDR.size)
            if len(hdr) != _REC_HDR.size:
                return
            rtype, flags, length = _REC_HDR.unpack(hdr)
            if rtype not in (T_BLOB, T_COMMIT, T_INDEX):
                return
            if rtype in skip_payload_types:
                if pos + _REC_HDR.size + length > size:
                    return
                yield Record(rtype, flags, b"", pos)
            else:
                payload = self.fh.read(length)
                if len(payload) != length:
                    return
                yield Record(rtype, flags, payload, pos)
            pos += _REC_HDR.size + length
