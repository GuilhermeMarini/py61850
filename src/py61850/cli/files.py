#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""
Scan, search, view and download files on an IEC 61850 relay over MMS file
services.

Uses the file_transfer class (MMS FileDirectory / FileOpen+FileRead+FileClose):
    GetServerDirectory{FILE}  -> FileDirectory   (scan / search)
    GetFile                   -> FileOpen/Read/Close (view, download)

Examples
--------
    # scan: every file in every folder on the relay
    mms-files 192.0.2.22 --list
    mms-files 192.0.2.22 --search --folders     # just the folder tree

    # search the relay's folders for file types, and see which folder holds them
    mms-files 192.0.2.22 --search --ext cfg,dat,hdr
    mms-files 192.0.2.22 --search "C4_*.TXT" --root /EVENTS/
    mms-files 192.0.2.22 --search --regex "1011[0-9]" --folders

    # view a text file on screen
    mms-files 192.0.2.22 --view /CFG.TXT

    # download one file to ./downloads/
    mms-files 192.0.2.22 --get /EVENTS/C4_10117.TXT

    # download everything under a prefix, or every file of a type
    mms-files 192.0.2.22 --get-all --filter /EVENTS/ --out relay_files
    mms-files 192.0.2.22 --get-all --ext cfg,dat --out comtrade
"""

import argparse
import os
import sys
import time

from .. import FileTransfer, MmsError, folder_of


def _hsize(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024.0


class ProgressBar:
    """In-place progress bar with %, bytes, speed and ETA. Renders on a TTY;
    degrades to periodic newline updates when output is redirected."""

    def __init__(self, total, label="", width=28, stream=None, prefix=""):
        self.total = total or 0
        self.label = label
        self.width = width
        self.prefix = prefix
        self.stream = stream or sys.stderr
        self.tty = self.stream.isatty()
        self.start = time.monotonic()
        self.last_draw = 0.0
        self.got = 0
        self._complete = False

    def update(self, got, total=None):
        if total and not self.total:
            self.total = total                      # learn size lazily (from FileOpen)
        self.got = got
        now = time.monotonic()
        if now - self.last_draw < 0.1 and got < self.total:
            return                                  # throttle redraws
        self.last_draw = now
        self._draw(now)

    def _draw(self, now):
        elapsed = max(now - self.start, 1e-6)
        speed = self.got / elapsed
        if self.total:
            frac = 1.0 if self._complete else min(self.got / self.total, 1.0)
            filled = int(self.width * frac)
            bar = "#" * filled + "-" * (self.width - filled)
            eta = (self.total - self.got) / speed if speed > 0 else 0
            line = (f"{self.prefix}{self.label} [{bar}] {frac*100:5.1f}%  "
                    f"{_hsize(self.got)}/{_hsize(self.total)}  "
                    f"{_hsize(speed)}/s  eta {eta:4.0f}s")
        else:
            line = (f"{self.prefix}{self.label}  {_hsize(self.got)}  "
                    f"{_hsize(speed)}/s")
        if self.tty:
            self.stream.write("\r\033[K" + line)
        else:
            self.stream.write(line + "\n")
        self.stream.flush()

    def finish(self, complete=True):
        self._complete = complete
        self._draw(time.monotonic())
        if self.tty:
            self.stream.write("\n")
        self.stream.flush()


def fmt_time(gt):
    """GeneralizedTime 'YYYYMMDDhhmmssZ' -> 'YYYY-MM-DD hh:mm:ss'."""
    if not gt or len(gt) < 14:
        return gt or "-"
    return f"{gt[0:4]}-{gt[4:6]}-{gt[6:8]} {gt[8:10]}:{gt[10:12]}:{gt[12:14]}"


def _select(c, args, recursive):
    """The files this invocation acts on: one FileDirectory scan (recursive when
    asked), narrowed by --search/--ext/--regex, then by the --filter prefix."""
    def skipped(path, ex):
        print(f"  [skip] {path}: {ex}", file=sys.stderr)

    entries = c.search_files(args.search or None,
                             regex=args.regex,
                             extensions=args.ext,
                             root=args.root,
                             recursive=recursive,
                             ignore_case=not args.case_sensitive,
                             max_depth=args.depth,
                             on_error=skipped)
    if args.filter:
        entries = [e for e in entries if e.name.startswith(args.filter)]
    return entries


def _scope(args):
    """How much of the IED this invocation walks, for the banner line."""
    if not args.recursive:
        return "this folder only"
    return "every folder" if args.depth is None else f"depth<={args.depth}"


def cmd_search(c, args):
    print(f"searching {args.host} from {args.root or '/'} ({_scope(args)}) ...",
          file=sys.stderr)
    entries = _select(c, args, recursive=args.recursive)
    if not entries:
        print("no files matched")
        return

    if args.flat:
        for e in entries:
            print(e.name)
    else:
        by_folder = {}
        for e in entries:
            by_folder.setdefault(folder_of(e.name), []).append(e)
        for folder in sorted(by_folder):
            hits = by_folder[folder]
            size = sum(e.size for e in hits)
            print(f"\n{folder}   ({len(hits)} file{'s' if len(hits) != 1 else ''}, "
                  f"{_hsize(size)})")
            if args.folders:
                continue
            for e in hits:
                print(f"  {e.size:>12,}  {fmt_time(e.last_modified):<19}  "
                      f"{e.name[len(folder):] or e.name}")

    total = sum(e.size for e in entries)
    folders = {folder_of(e.name) for e in entries}
    print("-" * 70)
    print(f"{len(entries)} file{'s' if len(entries) != 1 else ''} in "
          f"{len(folders)} folder{'s' if len(folders) != 1 else ''}, "
          f"{total:,} bytes reported")


def cmd_list(c, args):
    entries = _select(c, args, recursive=args.recursive)   # the whole tree by default
    print(f"{'SIZE':>12}  {'LAST MODIFIED':<19}  NAME")
    print("-" * 70)
    total = 0
    for e in entries:
        total += e.size
        print(f"{e.size:>12,}  {fmt_time(e.last_modified):<19}  {e.name}")
    print("-" * 70)
    print(f"{len(entries)} files, {total:,} bytes reported")


def cmd_view(c, args):
    name = args.view
    data = c.get_file(name)
    sys.stdout.write(f"===== {name}  ({len(data):,} bytes) =====\n")
    limit = None if args.full else args.max_view
    chunk = data if limit is None else data[:limit]
    try:
        sys.stdout.write(chunk.decode("utf-8"))
    except UnicodeDecodeError:
        sys.stdout.write(chunk.decode("latin-1", "replace"))
    if limit is not None and len(data) > limit:
        sys.stdout.write(f"\n... [truncated at {limit} bytes; use --full] ...\n")
    sys.stdout.write("\n")


def _local_path(outdir, remote_name):
    rel = remote_name.lstrip("/")
    return os.path.join(outdir, rel)


def cmd_get(c, args):
    name = args.get
    local = args.out_path or _local_path(args.out, name)
    bar = ProgressBar(0, label=os.path.basename(name))   # size learned from FileOpen
    n = c.download_file(name, local, progress=bar.update)
    bar.finish()
    print(f"  saved -> {local}  ({n:,} bytes)")


def cmd_get_all(c, args):
    entries = _select(c, args, recursive=args.recursive)
    grand_total = sum(e.size for e in entries)
    print(f"downloading {len(entries)} files ({_hsize(grand_total)}) into {args.out}/ ...")

    ok = 0
    done_bytes = 0
    start = time.monotonic()
    for i, e in enumerate(entries, 1):
        local = _local_path(args.out, e.name)
        prefix = f"[{i:>3}/{len(entries)}] "
        bar = ProgressBar(e.size, label=os.path.basename(e.name), prefix=prefix)
        try:
            n = c.download_file(e.name, local, progress=bar.update)
            bar.finish()
            done_bytes += n
            ok += 1
            # overall summary line under each file
            elapsed = max(time.monotonic() - start, 1e-6)
            pct = 100.0 * done_bytes / grand_total if grand_total else 0
            print(f"        overall {ok}/{len(entries)}  {_hsize(done_bytes)}"
                  f"/{_hsize(grand_total)} ({pct:.0f}%)  avg {_hsize(done_bytes/elapsed)}/s")
        except Exception as ex:
            bar.finish(complete=False)
            print(f"  [FAIL] {e.name}: {ex}")
    print(f"done: {ok}/{len(entries)} files, {_hsize(done_bytes)} -> {args.out}/")


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="mms-files", description="MMS file scan / view / download")
    ap.add_argument("host")
    ap.add_argument("--port", type=int, default=102)
    ap.add_argument("--timeout", type=float, default=15)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--list", action="store_true", help="scan: list files (FileDirectory)")
    g.add_argument("--search", "--scan", nargs="?", const="", metavar="GLOB",
                   help="search every file in every folder and report which "
                        "folder each match is in; GLOB matches the file name "
                        "(e.g. 'C4_*.TXT'), or omit it for the whole tree and "
                        "narrow with --ext / --regex")
    g.add_argument("--view", metavar="NAME", help="download & print a file to screen")
    g.add_argument("--get", metavar="NAME", help="download one file to disk")
    g.add_argument("--get-all", action="store_true", help="download all (matching) files")

    sel = ap.add_argument_group(
        "file selection (--list, --search and --get-all walk every folder by "
        "default; a file must match all of the filters you give)")
    sel.add_argument("--ext", action="append", metavar="EXT[,EXT...]",
                     help="extensions to match, e.g. --ext cfg,dat,hdr")
    sel.add_argument("--regex", metavar="RE",
                     help="regex searched in the whole path, e.g. --regex '1011[0-9]'")
    sel.add_argument("--filter", help="only files whose name starts with this prefix")
    sel.add_argument("--root", metavar="DIR",
                     help="start the scan at this folder instead of the root")
    sel.add_argument("--depth", type=int, default=None, metavar="N",
                     help="stop after N folder levels (default: no limit)")
    sel.add_argument("--no-recursive", dest="recursive", action="store_false",
                     help="list only the top folder, do not descend")
    sel.add_argument("-r", "--recursive", dest="recursive", action="store_true",
                     help="descend into folders (the default)")
    sel.add_argument("-s", "--case-sensitive", action="store_true",
                     help="match names case-sensitively (default: ignore case)")
    ap.set_defaults(recursive=True)

    ap.add_argument("--out", default="downloads", help="output directory (default: downloads)")
    ap.add_argument("--out-path", help="exact output file path (overrides --out for --get)")
    ap.add_argument("--full", action="store_true", help="--view: print the whole file")
    ap.add_argument("--max-view", type=int, default=4000, help="--view byte cap (default 4000)")
    ap.add_argument("--flat", action="store_true",
                    help="--search: print one full path per line, ungrouped")
    ap.add_argument("--folders", action="store_true",
                    help="--search: print only the folders that hold matches")
    args = ap.parse_args(argv)

    args.ext = [x.strip() for group in (args.ext or [])
                for x in group.split(",") if x.strip()]

    c = FileTransfer(args.host, args.port, timeout=args.timeout)
    c.connect()
    try:
        if args.list:
            cmd_list(c, args)
        elif args.search is not None:
            cmd_search(c, args)
        elif args.view:
            cmd_view(c, args)
        elif args.get:
            cmd_get(c, args)
        elif args.get_all:
            cmd_get_all(c, args)
    except MmsError as ex:
        print(f"\nMMS error: {ex}", file=sys.stderr)
        if "file/" in str(ex):
            print("  (file-class errors often mean the relay's file-transfer\n"
                  "   handles are busy/exhausted; retry shortly, or the file is\n"
                  "   locked/being written by the relay.)", file=sys.stderr)
        return 2
    finally:
        c.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except KeyboardInterrupt:
        sys.exit(1)
