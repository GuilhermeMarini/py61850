#!/usr/bin/env python3
"""Connect to an IED and walk its data model.

    python examples/01_connect_and_scan.py 192.0.2.22

Shows the association + the three directory services: server directory
(logical devices), then per-LD the variables and data sets.
"""

import sys

from py61850 import MmsClient

HOST = sys.argv[1] if len(sys.argv) > 1 else "192.0.2.22"


def main():
    # The context manager connects on enter and closes on exit — even if an
    # exception is raised mid-scan, the socket is released.
    with MmsClient(HOST, timeout=10) as c:
        lds = c.get_server_directory()                 # -> ['LD0', 'PROT', ...]
        print(f"{HOST}: {len(lds)} logical device(s)")

        for ld in lds:
            variables = c.get_logical_device_directory(ld)
            data_sets = c.get_data_set_directory(ld)
            print(f"\n{ld}: {len(variables)} variables, {len(data_sets)} data sets")

            for v in variables[:10]:                   # first 10, to keep it short
                print("   ", v)
            if len(variables) > 10:
                print(f"    ... (+{len(variables) - 10} more)")


if __name__ == "__main__":
    main()
