"""Command-line interface for Strata.

    strata wrap   <payload> [-o out.strata] [-m msg]   create a new archive
    strata commit <out.strata> <payload> [-m msg]      add a new version
    strata log    <out.strata>                         list version history
    strata checkout <out.strata> [-n N] -o file        extract a version
    strata info   <out.strata>                         show self-description
    strata diff   <out.strata> A B                     compare two versions
    strata verify <out.strata>                         check integrity
    strata repair <out.strata>                         rebuild a damaged footer
"""

from __future__ import annotations

import argparse
import datetime as _dt
import mimetypes
import os
import sys

from .core import Strata
from .errors import StrataError


def _guess_mime(path: str) -> str:
    mime, _ = mimetypes.guess_type(path)
    return mime or "application/octet-stream"


def _fmt_time(ts: float) -> str:
    return _dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def _hsize(n: int) -> str:
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    f = float(n)
    for u in units:
        if f < 1024 or u == units[-1]:
            return f"{f:.0f} {u}" if u == "B" else f"{f:.1f} {u}"
        f /= 1024
    return f"{n} B"


def cmd_wrap(args) -> int:
    with open(args.payload, "rb") as f:
        data = f.read()
    out = args.output or (args.payload + ".strata")
    Strata.create(out, data, mime=_guess_mime(args.payload),
                  name=os.path.basename(args.payload),
                  message=args.message or "initial version",
                  author=args.author or "")
    print(f"created {out} ({_hsize(os.path.getsize(out))}) "
          f"wrapping {args.payload} ({_hsize(len(data))})")
    return 0


def cmd_commit(args) -> int:
    with open(args.payload, "rb") as f:
        data = f.read()
    s = Strata(args.archive)
    before = os.path.getsize(args.archive)
    c = s.commit(data, message=args.message or "", author=args.author or "")
    after = os.path.getsize(args.archive)
    print(f"committed version {len(s.versions())} ({c.id[:12]})")
    print(f"payload {_hsize(len(data))}; archive grew {_hsize(after - before)} "
          f"(dedup kept it small)")
    return 0


def cmd_log(args) -> int:
    s = Strata(args.archive)
    versions = s.versions()
    for i, c in enumerate(versions):
        marker = "*" if i == len(versions) - 1 else " "
        print(f"{marker} v{i+1}  {c.id[:12]}  {_fmt_time(c.time)}  "
              f"{_hsize(c.size):>10}  {c.message or '(no message)'}")
    return 0


def cmd_checkout(args) -> int:
    s = Strata(args.archive)
    data = s.read(args.number if args.number is not None else -1)
    if args.output == "-":
        sys.stdout.buffer.write(data)
    else:
        with open(args.output, "wb") as f:
            f.write(data)
        print(f"wrote {args.output} ({_hsize(len(data))})")
    return 0


def cmd_info(args) -> int:
    s = Strata(args.archive)
    info = s.info()
    print(f"name:     {info['name']}")
    print(f"type:     {info['mime']}")
    print(f"size:     {_hsize(info['size'])}")
    print(f"versions: {info['versions']}")
    print(f"created:  {_fmt_time(info['created'])}")
    print(f"modified: {_fmt_time(info['modified'])}")
    print(f"tool:     {info['tool']}")
    return 0


def cmd_diff(args) -> int:
    s = Strata(args.archive)
    d = s.diff(args.a, args.b)
    print(f"v{args.a+1} -> v{args.b+1}")
    print(f"  size:           {_hsize(d['size_from'])} -> {_hsize(d['size_to'])}")
    print(f"  chunks shared:  {d['chunks_shared']}")
    print(f"  chunks added:   {d['chunks_added']}")
    print(f"  chunks removed: {d['chunks_removed']}")
    print(f"  new bytes stored: {_hsize(d['bytes_added_stored'])}")
    return 0


def cmd_verify(args) -> int:
    s = Strata(args.archive)
    report = s.verify()
    print(report)
    return 0 if report.ok else 2


def cmd_repair(args) -> int:
    s = Strata(args.archive)
    if s.repair():
        print("footer rebuilt; archive is readable again")
        print(s.verify())
    return 0


def cmd_gui(args) -> int:
    from .gui import serve
    serve(port=args.port, open_browser=not args.no_browser)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="strata", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    w = sub.add_parser("wrap", help="create a new archive from a file")
    w.add_argument("payload")
    w.add_argument("-o", "--output")
    w.add_argument("-m", "--message")
    w.add_argument("--author")
    w.set_defaults(func=cmd_wrap)

    c = sub.add_parser("commit", help="add a new version")
    c.add_argument("archive")
    c.add_argument("payload")
    c.add_argument("-m", "--message")
    c.add_argument("--author")
    c.set_defaults(func=cmd_commit)

    lg = sub.add_parser("log", help="show version history")
    lg.add_argument("archive")
    lg.set_defaults(func=cmd_log)

    co = sub.add_parser("checkout", help="extract a version")
    co.add_argument("archive")
    co.add_argument("-n", "--number", type=int,
                    help="0-based version index (default: latest)")
    co.add_argument("-o", "--output", required=True, help="output file or - for stdout")
    co.set_defaults(func=cmd_checkout)

    inf = sub.add_parser("info", help="show self-description")
    inf.add_argument("archive")
    inf.set_defaults(func=cmd_info)

    df = sub.add_parser("diff", help="compare two versions")
    df.add_argument("archive")
    df.add_argument("a", type=int)
    df.add_argument("b", type=int)
    df.set_defaults(func=cmd_diff)

    v = sub.add_parser("verify", help="check integrity")
    v.add_argument("archive")
    v.set_defaults(func=cmd_verify)

    rp = sub.add_parser("repair", help="rebuild a damaged footer")
    rp.add_argument("archive")
    rp.set_defaults(func=cmd_repair)

    g = sub.add_parser("gui", help="open the drag-and-drop app in your browser")
    g.add_argument("-p", "--port", type=int, default=8733)
    g.add_argument("--no-browser", action="store_true")
    g.set_defaults(func=cmd_gui)

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except StrataError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
