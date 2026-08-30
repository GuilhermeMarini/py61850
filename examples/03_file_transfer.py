#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""List, search, view and download files on the relay over the MMS file services.

    python examples/03_file_transfer.py 192.0.2.22

Demonstrates FileTransfer: directory listing, searching the IED's folders for a
set of file types, an in-memory fetch, and a download-to-disk with a live
progress callback.
"""

import sys

from py61850 import FileTransfer, folder_of

HOST = sys.argv[1] if len(sys.argv) > 1 else "192.0.2.22"


def progress(got, total):
    """Called once at 0 (to learn the size FileOpen reports) then per chunk."""
    pct = (got / total * 100) if total else 0
    print(f"\r  {got:>10} / {total or '?'} bytes ({pct:5.1f}%)", end="", flush=True)


def main():
    with FileTransfer(HOST) as ft:
        # 1) List files. Pass a prefix/dir, or None for the whole tree.
        entries = ft.file_directory()                  # -> list[DirEntry]
        print(f"{HOST}: {len(entries)} file(s)")
        for e in entries[:15]:
            # DirEntry has .name, .size, .last_modified
            print(f"  {e.size:>12}  {e.last_modified or '-':<18}  {e.name}")
        if not entries:
            print("  (no files reported)")
            return

        # 2) Search the IED's folders for file types, and report where they are.
        #    search_files() walks into subfolders (one FileDirectory per folder;
        #    relays that answer the root listing with every full path need only
        #    the one). The filters combine: name glob AND extension AND regex.
        hits = ft.search_files(extensions=[".cfg", ".dat", ".hdr"])
        print(f"\n{len(hits)} COMTRADE-ish file(s):")
        by_folder = {}
        for e in hits:
            by_folder.setdefault(folder_of(e.name), []).append(e)
        for folder, group in sorted(by_folder.items()):
            total = sum(e.size for e in group)
            print(f"  {folder:<30} {len(group):>4} file(s)  {total:>12,} bytes")

        #    Other shapes: a glob on the file name, or a regex on the whole path.
        #    ft.search_files("C4_*.TXT", root="/EVENTS/")
        #    ft.search_files(regex=r"1011[0-9]")

        target = entries[0].name

        # 3) Fetch a file fully into memory (good for small text files).
        data = ft.get_file(target)
        print(f"\n\nget_file({target!r}) -> {len(data)} bytes")
        preview = data[:200].decode("latin-1", "replace")
        print("  preview:", preview.replace("\n", "\\n"))

        # 4) Download straight to disk, with the progress bar above.
        out = f"downloads/{target.lstrip('/').replace('/', '_')}"
        print(f"\ndownloading {target} -> {out}")
        written = ft.download_file(target, out, progress=progress)
        print(f"\n  wrote {written} bytes")


if __name__ == "__main__":
    main()
