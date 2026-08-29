#!/usr/bin/env python3
"""Error handling — the whole library raises one exception family.

    python examples/04_error_handling.py 192.0.2.22

    Iec61850Error                 (catch this to get everything)
    ├── TransportError            socket / TPKT / COTP failures (host down, refused, timeout)
    └── MmsError                  MMS-level: refused association, service error, reject PDU

This is what an unattended fleet job wants: one `except` that never lets a raw
socket error escape.
"""

import sys

from py61850 import MmsClient, Iec61850Error, TransportError, MmsError

HOST = sys.argv[1] if len(sys.argv) > 1 else "192.0.2.22"


def main():
    # Distinguish the two failure kinds when you care about the difference...
    try:
        with MmsClient(HOST, timeout=5) as c:
            lds = c.get_server_directory()
            print(f"connected, {len(lds)} logical device(s)")

            # An MMS service error (e.g. reading a non-existent domain) raises
            # MmsError, carrying the relay's error class/detail.
            try:
                c.read("NOPE", "NOPE$ST$x")
            except MmsError as e:
                print(f"expected MMS error: {e}")

    except TransportError as e:
        print(f"transport problem (host unreachable/refused/timeout): {e}")
        return 1
    except MmsError as e:
        print(f"association/service problem: {e}")
        return 1

    # ...or just catch the base when you only need "did it work?"
    try:
        with MmsClient("203.0.113.1", timeout=2) as c:   # a host that won't answer
            c.get_server_directory()
    except Iec61850Error as e:
        print(f"caught via base class: {type(e).__name__}: {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
