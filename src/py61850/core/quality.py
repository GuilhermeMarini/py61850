# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""The IEC 61850-7-3 ``Quality`` bitstring (13 bits).

Quality travels as a plain MMS bit-string, so :mod:`py61850.core.data` decodes
it to a "0101..." string like any other.  This module gives that string names,
for callers that want them -- and it is the same 13 bits whether they arrived in
an MMS Read response or a GOOSE dataset, which is why it sits in ``core``.
"""

VALIDITY = {0: "good", 1: "invalid", 2: "reserved", 3: "questionable"}

# bit index (MSB-first, as the decoded bit-string reads) -> attribute name
_FLAGS = [
    (2, "overflow"),
    (3, "out_of_range"),
    (4, "bad_reference"),
    (5, "oscillatory"),
    (6, "failure"),
    (7, "old_data"),
    (8, "inconsistent"),
    (9, "inaccurate"),
    (11, "test"),
    (12, "operator_blocked"),
]


class Quality:
    """Decoded Quality. ``validity`` is a name; the rest are booleans."""

    __slots__ = ("validity", "source", "bits") + tuple(n for _, n in _FLAGS)

    def __init__(self, bits: str):
        self.bits = bits
        padded = bits.ljust(13, "0")
        self.validity = VALIDITY[int(padded[0:2], 2)]
        self.source = "substituted" if padded[10] == "1" else "process"
        for idx, name in _FLAGS:
            setattr(self, name, padded[idx] == "1")

    def is_good(self) -> bool:
        return self.validity == "good" and not self.old_data

    def __repr__(self):
        set_flags = [n for _, n in _FLAGS if getattr(self, n)]
        extra = ("," + ",".join(set_flags)) if set_flags else ""
        return f"Quality({self.validity},{self.source}{extra})"
