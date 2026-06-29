# Strata Container Format — Specification v1

Status: **Draft**. This document defines the on-disk format precisely enough
for an independent, byte-compatible implementation.

## 0. Design goals

A Strata file makes a single, portable file behave like a copy-on-write
filesystem behaves for a volume:

1. **Versioned** — it carries its own full history. Past versions travel with
   the file when it is copied or sent. No external repository or service.
2. **Deduplicated** — committing a new version stores only the regions that
   changed (content-defined chunking + content addressing).
3. **Self-verifying** — every stored byte is addressed by its cryptographic
   hash, so corruption is *detectable* and damaged regions can be localized.
4. **Self-describing** — the file states what it contains and how to interpret
   it (MIME type, original name, authorship, free-form notes).
5. **Append-only & recoverable** — new versions never rewrite existing bytes,
   so an interrupted write or a damaged footer can be recovered from the
   intact log earlier in the file.

Non-goals for v1: encryption, full forward-error-correction (parity), and
merge/branching. These are reserved for future versions (see §8).

## 1. Conventions

- All integers are **big-endian**, unsigned.
- Hash function is **BLAKE2b** with a 32-byte (256-bit) digest unless stated
  otherwise. Content addresses are the lowercase hex encoding of that digest.
- "Offset" means an absolute byte offset from the start of the file.

## 2. File layout

```
+-----------------------------------------------------------+
| MAGIC            8 bytes                                   |
| RECORD*          zero or more records (the append log)     |
| FOOTER           44 bytes, always the last bytes of file   |
+-----------------------------------------------------------+
```

### 2.1 Magic

```
0x53 0x54 0x52 0x41 0x54 0x41 0x01 0x00      "STRATA" + version(1) + reserved(0)
```

The 7th byte is the format version (currently `1`). The 8th byte is reserved
and MUST be `0` in v1.

## 3. Records

Every record has a 10-byte header followed by its payload:

```
[type:1][flags:1][length:8]   then   [payload: <length> bytes]
```

| Field  | Size | Meaning                                  |
|--------|------|------------------------------------------|
| type   | 1    | record type (see below)                  |
| flags  | 1    | bit flags (see below)                    |
| length | 8    | payload length in bytes                  |

Record types:

| Value | Name   | Payload                                              |
|-------|--------|-----------------------------------------------------|
| 0x01  | BLOB   | one content chunk, optionally compressed            |
| 0x02  | COMMIT | UTF-8 JSON describing one version (§5)               |
| 0x03  | INDEX  | UTF-8 JSON mapping content hash → location (§6)      |

Record flags:

| Bit  | Name   | Meaning                              |
|------|--------|--------------------------------------|
| 0x01 | ZLIB   | payload is zlib-compressed (RFC 1950)|

A reader MUST ignore unknown record types only when recovering; a conformant
v1 writer MUST emit only the types above.

## 4. Blobs and content-defined chunking

Payload bytes are split into **chunks** using content-defined chunking (CDC)
so that inserting or deleting bytes only changes the chunks near the edit, not
every chunk after it. The chunk's content address is `BLAKE2b-256(raw_chunk)`.

A BLOB record stores one chunk. If the `ZLIB` flag is set, the payload is the
zlib-compressed chunk; otherwise it is the raw chunk. The content address is
ALWAYS computed over the **raw** (uncompressed) chunk bytes. A writer SHOULD
compress only when it reduces size.

Identical chunks are stored once (deduplication): a writer MUST NOT append a
BLOB whose content address already exists in the archive.

### 4.1 Chunking algorithm (normative)

A gear-hash CDC. Parameters: `MIN = 2048`, `AVG = 8192`, `MAX = 65536`,
boundary mask `M = 0x1FFF` (13 bits → average 8192).

The 256-entry 64-bit gear table `GEAR` is generated deterministically with
splitmix64 seeded by `0x9E3779B97F4A7C15`:

```
x = SEED
for i in 0..255:
    x = (x + 0x9E3779B97F4A7C15) mod 2^64
    z = x
    z = ((z xor (z >> 30)) * 0xBF58476D1CE4E5B9) mod 2^64
    z = ((z xor (z >> 27)) * 0x94D049BB133111EB) mod 2^64
    GEAR[i] = z xor (z >> 31)
```

Boundaries: starting at chunk start `s`, set `fp = 0`; for byte index
`i` from `s + MIN` up to `min(s + MAX, len)`:

```
fp = ((fp << 1) + GEAR[byte[i]]) mod 2^64
if (fp AND M) == 0:  cut after i  (chunk end = i + 1)
```

If no boundary is found, the chunk ends at `min(s + MAX, len)`. A chunk is at
least `MIN` bytes unless it is the final chunk. An empty payload is encoded as
a single empty chunk (`BLAKE2b-256("")`).

## 5. Commit records

A COMMIT payload is a UTF-8 JSON object with these keys (all required):

| Key      | Type        | Meaning                                        |
|----------|-------------|------------------------------------------------|
| parent   | string/null | content id of the previous commit, or null     |
| time     | number      | POSIX timestamp (seconds, float) of the commit |
| message  | string      | human commit message                           |
| author   | string      | free-form author identifier                    |
| mime     | string      | MIME type of the payload                        |
| name     | string      | original file name                             |
| size     | number      | byte length of the full payload at this version |
| chunks   | array       | ordered content addresses of the payload chunks |
| tool     | string      | writer implementation/version                  |

The **commit id** is `BLAKE2b-256` over the canonical JSON of the object
**above** (keys sorted, no insignificant whitespace: separators `","` and
`":"`). The id is *not* stored in the payload; it is always recomputed on
read. The payload reconstructs as the concatenation of the raw chunks named in
`chunks`, in order.

Commits form a singly linked chain via `parent`. The newest commit is found
through the footer (§7).

## 6. Index records

An INDEX payload is a UTF-8 JSON object mapping each live content address to a
4-element array:

```json
{ "<hexhash>": [offset, stored_length, flags, raw_length], ... }
```

- `offset` — absolute offset of that BLOB record's header.
- `stored_length` — payload length as stored (compressed or not).
- `flags` — the BLOB record's flags.
- `raw_length` — length of the uncompressed chunk.

The index referenced by the footer MUST cover every chunk reachable from every
commit in the chain (the format keeps full history, so all chunks stay live).

## 7. Footer

The last 44 bytes of the file:

```
[MAGIC:8]["FOOT":4][index_offset:8][commit_offset:8][digest:16]
```

- `index_offset` — offset of the current INDEX record.
- `commit_offset` — offset of the newest COMMIT record.
- `digest` — `BLAKE2b-128` of `MAGIC + "FOOT" + index_offset + commit_offset`.

A reader locates current state in O(1): seek to `EOF − 44`, validate the
digest, follow `commit_offset` and `index_offset`.

### 7.1 Committing (append-only)

To add a version, a writer:

1. Seeks to `EOF − 44` (overwriting the old footer; everything before it is
   immutable).
2. Appends any BLOB records for chunks not already in the index.
3. Appends a COMMIT record (with `parent` = previous commit id).
4. Appends an INDEX record covering all live chunks.
5. Writes a new FOOTER.

Existing blobs and commits are never modified — this is the copy-on-write
property and the basis of recovery.

## 7.2 Threat model

Strata provides **integrity** (detecting accidental corruption) but not
**authenticity** (proving who wrote the content or detecting a motivated
adversary with write access).

**Within scope:**

- Bit-rot, partial disk failures, truncated transfers — detected by chunk
  BLAKE2b-256 hashes and the footer BLAKE2b-128 digest.
- Torn writes (process killed mid-commit) — caught by the footer digest;
  recoverable via forward scan.
- Silent data corruption in transit — any chunk that changes will fail its
  hash check.

**Out of scope:**

- *Adversarial replacement:* an attacker who can write to the file can create
  a fully valid Strata file with different content and correct hashes.
  BLAKE2b is not a MAC; without a key the hash can be recomputed over any
  payload.
- *Authorship / provenance:* there is no signing or PKI in the format. Use
  `gpg`, `minisign`, Sigstore, or similar to sign the Strata file as a whole
  if authenticity matters.
- *Confidentiality:* blobs are stored in plaintext (optionally zlib-compressed).
  Encryption is reserved for a future format version.

## 8. Integrity, recovery, and future work

- **Detection.** Recomputing `BLAKE2b-256` of each chunk and comparing to its
  address detects any bit flip. A torn final write is caught by the footer
  digest.
- **Recovery.** Because writes are append-only, if the footer is damaged a
  reader can scan records from the start (stopping at the first malformed
  record) to rebuild a valid index/footer from the intact log (`strata
  repair`). Versions whose chunks are all intact remain fully recoverable even
  when other regions are damaged.
- **Reserved for v2+:** optional Reed–Solomon parity records for *correction*
  (not just detection), payload encryption, and branching/merge commits.

## 9. Conformance

An implementation is **conformant** if it can read any file produced by §2–§7
and, for files it writes, satisfies the dedup (§4) and append-only (§7.1)
rules. The reference implementation lives in this repository and its test
suite (`tests/`) doubles as a conformance check.
