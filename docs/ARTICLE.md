# The file that remembers: why we need a smart-file format

## The thing we all quietly put up with

A file is one of the oldest abstractions in computing, and it is astonishingly
dumb. Open `report.pdf` and it will show you exactly one thing: its current
bytes. It cannot tell you what it looked like last week. It cannot tell you
whether a bad disk sector or a flaky transfer just silently corrupted it. It
cannot even tell you, reliably, what it *is* without you guessing from a
three-letter extension.

We've papered over each of these gaps — but always *outside* the file:

- **History** lives in git, Dropbox, or Google Drive. The instant you copy the
  file onto a USB stick or attach it to an email, its entire past is gone.
- **Integrity** lives in the filesystem (ZFS, btrfs) or in a `.sha256` file you
  have to remember to keep next to it. Copy the file off that filesystem and
  the protection stays behind.
- **Identity** lives in app-specific metadata that is format-specific and
  routinely stripped on upload or export.

The pattern is the same every time: the capability exists, but it is bolted to
the *environment*, not to the *file*. So the moment the file leaves that
environment — which is the whole point of a file — the capability evaporates.

We have simply learned to live with this. It is the FAT-filesystem era, frozen
in place at the level of the individual file.

## The filesystem already showed us the way

Filesystems made exactly this leap and we barely remember the before-times. FAT
stored bytes. Then ZFS and btrfs added three things that turned a dumb volume
into a smart one:

1. **Snapshots** — the volume remembers its past states cheaply, via
   copy-on-write.
2. **Checksums** — every block is verified on read; corruption is detected and,
   with redundancy, healed.
3. **Self-description** — the volume carries its own structure and metadata.

Strata is the same three ideas, moved down one level — from the *volume* to the
*single file* — and made **portable**, so they ride along when the file moves.

## What Strata is

A Strata file wraps any payload (an image, a PDF, a SQLite database, a source
file, arbitrary bytes) and embeds, inside the one file:

- its **full version history**, deduplicated so new versions are cheap;
- a **cryptographic checksum for every chunk**, so any bit flip is detectable
  and damaged regions are localizable;
- **self-description** — what it is, what it's called, who made it, free notes.

Crucially, all of this is *in the file's own bytes*. Copy it, email it, drop it
on a stick — the history, the checksums, and the description go with it.

## How it works, briefly

Three well-understood ideas, combined in a way nobody had packaged as a single
portable file:

- **Content-defined chunking.** The payload is cut at boundaries chosen by the
  content itself (a gear-hash CDC), so an edit only disturbs the chunks near it.
  Inserting a paragraph at the top of a document does not change the chunks at
  the bottom.
- **Content addressing.** Each chunk is named by its BLAKE2b-256 hash. Identical
  chunks are stored once — that is the deduplication *and* the integrity check,
  in one stroke. A version is just an ordered list of chunk hashes.
- **Append-only log + footer.** Commits never rewrite existing bytes; they
  append new chunks and a new footer at the end. That is the copy-on-write
  property, and it is what makes recovery possible: if the last write is torn,
  the intact log before it can be replayed.

The full byte-level format is in [`SPEC.md`](../SPEC.md) and is small enough to
re-implement in an afternoon — which is the point. A standard only becomes a
standard if other people can implement it.

## Why this is empty ground (prior-art check)

This was the first question we asked, because the metadata graveyard is full of
good ideas that were already taken. The honest finding:

- **ZFS / btrfs** give you snapshots + checksums + self-healing, but at the
  *filesystem* level. Their guarantees do not survive `cp` to another disk.
- **IPFS** is content-addressed and versioned, but it is a *distributed
  system*, not a single self-contained file you can hand to someone offline.
- **git** is the gold standard for history, but history lives in a *repository*
  beside the file, not inside it; a lone checked-out file knows nothing.
- **LibreOffice "Save Version"** keeps versions in one file — but only for ODF,
  only inside that app, and almost nobody knows it exists.
- **RCS** keeps deltas, but in separate `,v` files and only for text.
- **C2PA / XMP** attach rich, even signed metadata to media — but they are about
  *provenance and identity*, not version history or self-healing, and are
  largely scoped to media formats.

None of them is *one portable file, for any payload, that carries its own
past and proves its own integrity*. That is the gap Strata fills.

## What it's good for

- **Sending a document with its history intact** — the recipient can see what
  changed across drafts without access to your cloud.
- **Long-term archival** — a file that can prove, years later, that it has not
  rotted, and that can be partially recovered if it has.
- **AI-generated artifacts** — wrap a generated file together with the prompt,
  the model, and the chain of revisions, all in one portable unit.
- **Any "single source of truth" file** — config, datasets, design files — that
  benefits from cheap built-in versioning without standing up a repo.

## What it is not (yet)

v1 *detects* corruption; it does not yet *correct* it — Reed–Solomon parity is
the obvious v2 addition that turns detection into self-healing. There is no
encryption and no branching/merge in v1. These are deliberately deferred so the
core can be small, correct, and easy to adopt.

## The ask

Strata is a format first and a tool second. The reference implementation is
there to prove the format works and to make it trivially usable today — but the
goal is for the *format* to be re-implemented in other languages, embedded in
file managers and AI tools, and eventually understood natively by the systems
we already use. If a file can remember, verify, and explain itself, a lot of
the scaffolding we build around files simply stops being necessary.

A file should not be a thing that forgets.
