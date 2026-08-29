#!/usr/bin/env python3
"""Find files on the relay: which folders hold a .cfg, a .dat, a .hdr?

    python examples/06_find_files.py 192.0.2.22
    python examples/06_find_files.py 192.0.2.22 cfg dat hdr

MMS FileDirectory answers one flat listing per call, so "search the IED" is
built on top of it: `search_files()` walks every folder -- the whole tree, no
depth limit -- and filters what it finds by glob, extension and regex. This
example runs the three filter kinds, groups the hits by folder, and streams a
raw walk with `walk_files()`.

Nothing here downloads anything -- see 03_file_transfer.py for that, and the
last section for how to hand a search result to `download_file()`.
"""

import sys

from py61850 import FileTransfer, MmsError, folder_of

HOST = sys.argv[1] if len(sys.argv) > 1 else "192.0.2.22"
EXTENSIONS = sys.argv[2:] or ["cfg", "dat", "hdr"]      # a leading dot is optional


def by_folder(entries):
    """Group DirEntry objects by the folder they live in. `folder_of` keeps the
    trailing separator and copes with the '\\' some vendors use."""
    groups = {}
    for e in entries:
        groups.setdefault(folder_of(e.name), []).append(e)
    return groups


def report(title, entries):
    print(f"\n{title}: {len(entries)} file(s)")
    if not entries:
        print("  (none)")
        return
    for folder, hits in sorted(by_folder(entries).items()):
        total = sum(e.size for e in hits)
        print(f"  {folder:<28} {len(hits):>4} file(s)  {total:>14,} bytes")
        for e in hits[:3]:                              # a few names per folder
            print(f"      {e.name[len(folder):]}")
        if len(hits) > 3:
            print(f"      ... and {len(hits) - 3} more")


def main():
    with FileTransfer(HOST) as ft:
        # 1) By extension -- the "where are my COMTRADE records?" question.
        #    Values inside one filter are OR-ed: .cfg or .dat or .hdr.
        hits = ft.search_files(extensions=EXTENSIONS)
        report(f"{HOST}  extensions {EXTENSIONS}", hits)

        folders = sorted(by_folder(hits))
        print(f"\n  -> those files live in {len(folders)} folder(s): "
              f"{', '.join(folders) or '-'}")

        # 2) By glob. A glob without a separator matches the file name alone;
        #    one with a separator ('/EVENTS/*.TXT') matches the whole path.
        report(f"{HOST}  glob 'C4_*.TXT'", ft.search_files("C4_*.TXT"))

        # 3) By regex, searched in the whole path. Matching ignores case unless
        #    you pass ignore_case=False.
        report(f"{HOST}  regex '1016[0-9]'", ft.search_files(regex=r"1016[0-9]"))

        # 4) The filter kinds AND together: .cfg files whose path says COMTRADE.
        report(f"{HOST}  .cfg under COMTRADE",
               ft.search_files(extensions=["cfg"], regex="COMTRADE"))

        # 5) One folder only, no descent -- a plain FileDirectory of `root`.
        if folders:
            report(f"{HOST}  {folders[0]} (not recursive)",
                   ft.search_files(root=folders[0], recursive=False))

        # 6) The walk under all of the above: every file in every folder, no
        #    depth limit unless you pass max_depth. It yields entries as they
        #    arrive, so a slow IED shows progress instead of going quiet, and a
        #    folder that cannot be listed is skipped and reported, never fatal.
        def skipped(path, exc):
            print(f"  [skip] {path}: {exc}")

        print(f"\nwalking {HOST} ...")
        seen, biggest = 0, None
        for e in ft.walk_files(on_error=skipped):
            seen += 1
            if biggest is None or e.size > biggest.size:
                biggest = e
        if biggest is None:
            print("  (no files)")
        else:
            print(f"  {seen} file(s); largest is {biggest.name} "
                  f"({biggest.size:,} bytes)")

        # 7) A search result feeds straight into a download: every DirEntry
        #    carries the full path FileOpen wants.
        #
        #    for e in ft.search_files(extensions=["cfg"]):
        #        ft.download_file(e.name, "downloads/" + e.name.lstrip("/"))


if __name__ == "__main__":
    try:
        main()
    except MmsError as ex:
        print(f"MMS error: {ex}", file=sys.stderr)
        sys.exit(2)
