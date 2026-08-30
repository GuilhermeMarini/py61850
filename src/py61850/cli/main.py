# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""``py61850`` -- one entry point that dispatches to the protocol drivers.

    py61850 scan  <host> [...]     data-model scan over MMS   (= mms-scan)
    py61850 files <host> [...]     file scan/view/download    (= mms-files)

``mms-scan`` and ``mms-files`` remain installed and unchanged.  This wrapper
exists so that the GOOSE and SV drivers (ROADMAP 2.0) arrive as
``py61850 goose ...`` / ``py61850 sv ...`` rather than as a growing spray of
top-level ``mms-*``-style commands.

Each driver owns its own argument parser; this only picks one and hands it the
remaining argv.
"""

import sys

COMMANDS = {
    "scan": ("py61850.cli.scan", "data-model scan over MMS (same as mms-scan)"),
    "files": ("py61850.cli.files", "file scan / view / download (same as mms-files)"),
}


def _usage(stream=sys.stdout):
    print("usage: py61850 <command> [options]\n\ncommands:", file=stream)
    for name, (_, help_text) in COMMANDS.items():
        print(f"  {name:<8} {help_text}", file=stream)
    print("\nRun 'py61850 <command> --help' for a command's options.", file=stream)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        _usage()
        return 0
    cmd = argv[0]
    if cmd not in COMMANDS:
        print(f"py61850: unknown command {cmd!r}\n", file=sys.stderr)
        _usage(sys.stderr)
        return 2
    module_name = COMMANDS[cmd][0]
    module = __import__(module_name, fromlist=["main"])
    return module.main(argv[1:]) or 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(1)
