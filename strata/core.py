"""High-level Strata API: wrap payloads, commit versions, verify, recover."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Optional

from . import format as fmt
from .chunker import chunk
from .errors import CorruptArchive, IntegrityError, VersionNotFound

# An index maps a content hash -> [offset, stored_length, flags, raw_length].
Index = dict[str, list[int]]


@dataclass
class Commit:
    """One version recorded inside a Strata file."""

    id: str
    parent: Optional[str]
    time: float
    message: str
    author: str
    mime: str
    name: str
    size: int
    chunks: list[str]
    tool: str = "strata-py/0.1.0"
    offset: int = -1  # absolute offset of the commit record (runtime only)

    def to_json(self) -> bytes:
        d = {k: getattr(self, k) for k in (
            "parent", "time", "message", "author", "mime", "name",
            "size", "chunks", "tool")}
        # The commit id is the hash of its canonical content, so it is not
        # part of the hashed body.
        body = json.dumps(d, separators=(",", ":"), sort_keys=True).encode()
        return body

    @classmethod
    def from_record(cls, rec: fmt.Record) -> "Commit":
        d = json.loads(rec.payload.decode())
        cid = fmt.content_hash(json.dumps(
            {k: d[k] for k in (
                "parent", "time", "message", "author", "mime", "name",
                "size", "chunks", "tool")},
            separators=(",", ":"), sort_keys=True).encode())
        return cls(id=cid, parent=d["parent"], time=d["time"],
                   message=d["message"], author=d["author"], mime=d["mime"],
                   name=d["name"], size=d["size"], chunks=d["chunks"],
                   tool=d.get("tool", "?"), offset=rec.offset)


@dataclass
class VerifyReport:
    ok: bool
    blobs_checked: int
    blobs_bad: list[str] = field(default_factory=list)
    commits_checked: int = 0
    versions_recoverable: list[int] = field(default_factory=list)
    versions_broken: list[int] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        lines = [
            f"integrity: {'OK' if self.ok else 'DAMAGED'}",
            f"blobs checked: {self.blobs_checked}, bad: {len(self.blobs_bad)}",
            f"commits checked: {self.commits_checked}",
            f"versions recoverable: {len(self.versions_recoverable)}, "
            f"broken: {len(self.versions_broken)}",
        ]
        lines += self.notes
        return "\n".join(lines)


class Strata:
    """A versioned, self-verifying container around an arbitrary payload."""

    def __init__(self, path: str):
        self.path = path

    # ----------------------------------------------------------------- create
    @classmethod
    def create(cls, path: str, payload: bytes, *, mime: str = "application/octet-stream",
               name: str = "", message: str = "initial version",
               author: str = "", compress: bool = True) -> "Strata":
        """Create a brand-new Strata file wrapping ``payload`` as version 1."""
        with open(path, "wb") as fh:
            fh.write(fmt.MAGIC)
            w = fmt.Writer(fh)
            index: Index = {}
            chunk_hashes = cls._write_payload(w, payload, index, compress)
            commit = Commit(
                id="", parent=None, time=time.time(), message=message,
                author=author, mime=mime, name=name or os.path.basename(path),
                size=len(payload), chunks=chunk_hashes)
            cls._finalize(w, index, commit)
        return cls(path)

    # ----------------------------------------------------------------- commit
    def commit(self, payload: bytes, *, message: str = "", author: str = "",
               mime: Optional[str] = None, name: Optional[str] = None,
               compress: bool = True) -> Commit:
        """Append a new version. Only changed chunks are stored."""
        idx_off, commit_off = self._read_footer()
        index = self._read_index(idx_off)
        parent = self._read_commit(commit_off)
        with open(self.path, "r+b") as fh:
            # Append over the previous footer so the footer always stays at the
            # very end of the file. Existing blobs/commits are never rewritten
            # (copy-on-write); only the trailing footer is replaced.
            fh.seek(0, 2)
            end = fh.tell()
            fh.seek(end - fmt.FOOTER_SIZE)
            w = fmt.Writer(fh)
            chunk_hashes = self._write_payload(w, payload, index, compress)
            commit = Commit(
                id="", parent=parent.id, time=time.time(), message=message,
                author=author, mime=mime or parent.mime,
                name=name or parent.name, size=len(payload), chunks=chunk_hashes)
            self._finalize(w, index, commit)
        return commit

    # ------------------------------------------------------------------- read
    def versions(self) -> list[Commit]:
        """Return all commits, oldest first."""
        _, commit_off = self._read_footer()
        out: list[Commit] = []
        with open(self.path, "rb") as fh:
            r = fmt.Reader(fh)
            offset = commit_off
            # Walk the parent chain by matching ids to offsets is expensive;
            # instead we recorded each commit's parent *offset* implicitly by
            # scanning. Simplest robust approach: scan all commits, then order.
            commits_by_id = {}
            # Skip blob/index payloads: we only need commit records here.
            for rec in r.scan_records(
                    skip_payload_types=frozenset({fmt.T_BLOB, fmt.T_INDEX})):
                if rec.type == fmt.T_COMMIT:
                    c = Commit.from_record(rec)
                    commits_by_id[c.id] = c
            # Reconstruct the chain from the head commit backwards.
            head = Commit.from_record(r.read_record(commit_off))
            chain = []
            cur: Optional[Commit] = head
            seen = set()
            while cur is not None and cur.id not in seen:
                seen.add(cur.id)
                chain.append(cur)
                cur = commits_by_id.get(cur.parent) if cur.parent else None
            chain.reverse()
            out = chain
        return out

    def read(self, version: int = -1) -> bytes:
        """Return the payload bytes of a version (default: latest)."""
        commits = self.versions()
        if not commits:
            raise VersionNotFound("archive has no versions")
        try:
            commit = commits[version]
        except IndexError:
            raise VersionNotFound(
                f"version {version} out of range (have {len(commits)})")
        idx_off, _ = self._read_footer()
        index = self._read_index(idx_off)
        with open(self.path, "rb") as fh:
            r = fmt.Reader(fh)
            parts = []
            for h in commit.chunks:
                loc = index.get(h)
                if loc is None:
                    raise CorruptArchive(f"missing chunk {h[:12]} for version")
                raw = r.read_blob(loc[0])
                if fmt.content_hash(raw) != h:
                    raise IntegrityError(f"chunk {h[:12]} failed verification")
                parts.append(raw)
        return b"".join(parts)

    def info(self) -> dict:
        """Self-description of the latest version."""
        commits = self.versions()
        latest = commits[-1]
        return {
            "name": latest.name,
            "mime": latest.mime,
            "size": latest.size,
            "versions": len(commits),
            "created": commits[0].time,
            "modified": latest.time,
            "tool": latest.tool,
        }

    # ------------------------------------------------------------------- diff
    def diff(self, a: int, b: int) -> dict:
        """Chunk-level difference between two versions."""
        commits = self.versions()
        ca, cb = commits[a], commits[b]
        sa, sb = set(ca.chunks), set(cb.chunks)
        idx_off, _ = self._read_footer()
        index = self._read_index(idx_off)

        def stored(hashes):
            return sum(index[h][1] for h in hashes if h in index)

        added = sb - sa
        removed = sa - sb
        return {
            "from": a, "to": b,
            "chunks_added": len(added),
            "chunks_removed": len(removed),
            "chunks_shared": len(sa & sb),
            "bytes_added_stored": stored(added),
            "size_from": ca.size,
            "size_to": cb.size,
        }

    # ----------------------------------------------------------------- verify
    def verify(self) -> VerifyReport:
        """Recompute every content address and report integrity."""
        report = VerifyReport(ok=True, blobs_checked=0)
        try:
            idx_off, commit_off = self._read_footer()
        except CorruptArchive as e:
            report.ok = False
            report.notes.append(f"footer: {e}")
            return self._recover_report(report)

        index = self._read_index(idx_off)
        good_hashes = set()
        with open(self.path, "rb") as fh:
            r = fmt.Reader(fh)
            for h, loc in index.items():
                report.blobs_checked += 1
                try:
                    raw = r.read_blob(loc[0])
                except Exception:  # corrupt header, bad zlib stream, etc.
                    report.blobs_bad.append(h)
                    report.ok = False
                    continue
                if fmt.content_hash(raw) == h:
                    good_hashes.add(h)
                else:
                    report.blobs_bad.append(h)
                    report.ok = False

            commits = self.versions()
            report.commits_checked = len(commits)
            for i, c in enumerate(commits):
                if all(h in good_hashes for h in c.chunks):
                    report.versions_recoverable.append(i)
                else:
                    report.versions_broken.append(i)
                    report.ok = False
        if report.versions_broken:
            report.notes.append(
                f"{len(report.versions_recoverable)} of {len(commits)} "
                f"versions are still fully recoverable despite damage")
        return report

    def _recover_report(self, report: VerifyReport) -> VerifyReport:
        """Best-effort scan when the footer is unreadable."""
        with open(self.path, "rb") as fh:
            r = fmt.Reader(fh)
            commits = 0
            for rec in r.scan_records():
                if rec.type == fmt.T_BLOB:
                    report.blobs_checked += 1
                elif rec.type == fmt.T_COMMIT:
                    commits += 1
            report.commits_checked = commits
        report.notes.append(
            "footer damaged; recovered structure by full scan - "
            "run `strata repair` to rewrite a valid footer")
        return report

    # ----------------------------------------------------------------- repair
    def repair(self) -> bool:
        """Rewrite a valid footer (and index) by scanning intact records.

        Recovers an archive whose trailing footer was truncated or corrupted,
        as long as the blob/commit log earlier in the file is intact. Returns
        True if a footer was rewritten.
        """
        index: Index = {}
        last_commit_off = -1
        last_index_off = -1
        with open(self.path, "rb") as fh:
            r = fmt.Reader(fh)
            for rec in r.scan_records():
                if rec.type == fmt.T_BLOB:
                    if rec.flags & fmt.F_ZLIB:
                        import zlib
                        raw = zlib.decompress(rec.payload)
                    else:
                        raw = rec.payload
                    h = fmt.content_hash(raw)
                    index[h] = [rec.offset, len(rec.payload), rec.flags, len(raw)]
                elif rec.type == fmt.T_COMMIT:
                    last_commit_off = rec.offset
                elif rec.type == fmt.T_INDEX:
                    last_index_off = rec.offset
        if last_commit_off < 0:
            raise CorruptArchive("no intact commit found; cannot repair")
        with open(self.path, "r+b") as fh:
            fh.seek(0, 2)
            w = fmt.Writer(fh)
            # Append a fresh, complete index rebuilt from the scan, then a footer.
            index_payload = json.dumps(index, separators=(",", ":")).encode()
            index_off = w.write_record(fmt.T_INDEX, index_payload)
            w.write_footer(index_off, last_commit_off)
        return True

    # --------------------------------------------------------------- internal
    @staticmethod
    def _write_payload(w: fmt.Writer, payload: bytes, index: Index,
                       compress: bool) -> list[str]:
        hashes = []
        for piece in chunk(payload):
            h = fmt.content_hash(piece)
            hashes.append(h)
            if h not in index:  # dedup: store each unique chunk only once
                offset, stored_len, flags = w.write_blob(piece, compress)
                index[h] = [offset, stored_len, flags, len(piece)]
        if not hashes:  # empty payload -> a single empty chunk for a clean read
            h = fmt.content_hash(b"")
            if h not in index:
                offset, stored_len, flags = w.write_blob(b"", compress)
                index[h] = [offset, stored_len, flags, 0]
            hashes.append(h)
        return hashes

    @staticmethod
    def _finalize(w: fmt.Writer, index: Index, commit: Commit) -> None:
        body = commit.to_json()
        commit.id = fmt.content_hash(body)
        commit_off = w.write_record(fmt.T_COMMIT, body)
        index_payload = json.dumps(index, separators=(",", ":")).encode()
        index_off = w.write_record(fmt.T_INDEX, index_payload)
        w.write_footer(index_off, commit_off)

    def _read_footer(self) -> tuple[int, int]:
        with open(self.path, "rb") as fh:
            r = fmt.Reader(fh)
            r.read_magic()
            return r.read_footer()

    def _read_index(self, idx_off: int) -> Index:
        with open(self.path, "rb") as fh:
            r = fmt.Reader(fh)
            rec = r.read_record(idx_off)
            if rec.type != fmt.T_INDEX:
                raise CorruptArchive("index record not found")
            return json.loads(rec.payload.decode())

    def _read_commit(self, commit_off: int) -> Commit:
        with open(self.path, "rb") as fh:
            r = fmt.Reader(fh)
            return Commit.from_record(r.read_record(commit_off))
