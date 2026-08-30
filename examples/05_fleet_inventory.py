#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""A small 'third app': build a CSV inventory across a fleet of relays.

    python examples/05_fleet_inventory.py 192.0.2.22 192.0.2.23 ...

Connects to each host, walks its logical devices, and writes one CSV row per
(host, logical-device, variable-count). Unreachable relays are recorded with
their error instead of aborting the run — the pattern any unattended collector
built on this library wants.
"""

import csv
import sys

from py61850 import MmsClient, Iec61850Error

HOSTS = sys.argv[1:] or ["192.0.2.22"]
OUT = "inventory.csv"


def inventory_one(host):
    """Yield (host, ld, n_vars, n_datasets, status) rows for one relay."""
    try:
        with MmsClient(host, timeout=8) as c:
            lds = c.get_server_directory()
            for ld in lds:
                n_vars = len(c.get_logical_device_directory(ld))
                n_ds = len(c.get_data_set_directory(ld))
                yield (host, ld, n_vars, n_ds, "ok")
    except Iec61850Error as e:
        # One bad relay must not sink the whole sweep.
        yield (host, "", "", "", f"{type(e).__name__}: {e}")


def main():
    rows = []
    for host in HOSTS:
        print(f"scanning {host} ...")
        for row in inventory_one(host):
            rows.append(row)
            print("   ", row)

    with open(OUT, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["host", "logical_device", "n_variables", "n_datasets", "status"])
        w.writerows(rows)

    print(f"\nwrote {len(rows)} row(s) to {OUT}")


if __name__ == "__main__":
    main()
