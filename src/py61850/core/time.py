# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""MMS time types: ``UtcTime``, ``BinaryTime`` and ``GeneralizedTime``.

``UtcTime`` (8 octets) is what every IEC 61850 timestamp attribute carries --
``t`` on a data object over MMS, and the ``t`` fields inside a GOOSE dataset --
so it lives here rather than in either protocol package.

    +----------------+--------------------+---------------+
    |  seconds (u32) | fraction (u24)     | TimeQuality   |
    +----------------+--------------------+---------------+

`seconds` is a UNIX epoch second count; `fraction` is a binary fraction of a
second (numerator over 2**24).  The trailing TimeQuality octet carries the
leap-second-known / clock-failure / clock-not-synchronised flags plus the
accuracy in its low 5 bits.

``GeneralizedTime`` is the ASN.1 text form, ``YYYYMMDDhhmmss[.f][Z]``, and is
what a file's lastModified arrives in.
"""

import re
import struct

TQ_LEAP_SECONDS_KNOWN = 0x80
TQ_CLOCK_FAILURE = 0x40
TQ_CLOCK_NOT_SYNCHRONISED = 0x20
TQ_ACCURACY_MASK = 0x1F


_GENERALIZED_TIME = re.compile(r"\d{14}(\.\d+)?(Z|[+-]\d{4})?")


def decode_generalized_time(value):
    """GeneralizedTime octets -> text, with SEL's space padding repaired.

    SEL relays pad each field with a space where the standard has a zero --
    ``2026 928183420Z`` for ``20260928183420Z``, ``1970 1 1 0 0 0Z`` for
    ``19700101000000Z``. Spaces become zeros only when that yields a valid
    GeneralizedTime; anything else is returned as it came.
    """
    text = bytes(value).decode("latin-1", "replace")
    if " " in text:
        repaired = text.replace(" ", "0")
        if _GENERALIZED_TIME.fullmatch(repaired):
            return repaired
    return text


def decode_utc_time(value):
    """8-octet UtcTime -> ``{"utc": <float epoch seconds>, "quality": <int|None>}``."""
    if len(value) < 4:
        return bytes(value).hex()
    secs = int.from_bytes(value[0:4], "big")
    frac = int.from_bytes(value[4:7], "big") / float(1 << 24) if len(value) >= 7 else 0
    return {"utc": secs + frac, "quality": value[7] if len(value) >= 8 else None}


def encode_utc_time(epoch_seconds: float, quality: int = 0) -> bytes:
    """Inverse of :func:`decode_utc_time`. Returns the 8 value octets."""
    secs = int(epoch_seconds)
    frac = int(round((epoch_seconds - secs) * (1 << 24)))
    if frac >= (1 << 24):                       # rounding carried into the next second
        secs += 1
        frac = 0
    return struct.pack("!I", secs) + frac.to_bytes(3, "big") + bytes([quality & 0xFF])


def decode_binary_time(value):
    """BinaryTime (4 or 6 octets: ms-since-midnight [+ days since 1984-01-01])."""
    return {"binary_time": bytes(value).hex()}
