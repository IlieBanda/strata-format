# Contributing to Strata

Strata is a **format first**, a tool second. The most valuable contributions are
the ones that make the format real and durable.

## Ways to help

- **Independent implementations.** Re-implement the [SPEC](SPEC.md) in another
  language (Rust, Go, JS, C). Cross-implementation byte-compatibility is what
  turns a project into a standard. If your implementation can read the files in
  `examples/` and pass an equivalent of `tests/`, it is conformant.
- **Spec review.** Found an ambiguity in [SPEC.md](SPEC.md) that two
  implementers could read differently? That's a bug — open an issue.
- **Format extensions.** The reserved roadmap (parity/self-healing, encryption,
  branching) needs careful design. Propose before you build.
- **Integrations.** File-manager plugins, "open with history" viewers, library
  bindings.

## Reference implementation

Pure Python, **standard library only** — please keep it dependency-free so it
stays trivial to vendor and audit.

```bash
python -m unittest discover -s tests -v     # run tests
python examples/demo.py                      # run the tour
```

The test suite doubles as a conformance check. New format behavior must come
with tests *and* a corresponding change to `SPEC.md` in the same PR.

## Versioning the format

The on-disk format is versioned by the 7th magic byte. Any change that alters
how bytes are written bumps the format version and must remain readable by a
v1-aware reader where possible. Don't change `SPEC.md` v1 semantics; add a new
section/version instead.
