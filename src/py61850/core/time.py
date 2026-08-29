"""MMS time types: ``UtcTime`` and ``BinaryTime``.

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
"""

import struct

TQ_LEAP_SECONDS_KNOWN = 0x80
TQ_CLOCK_FAILURE = 0x40
TQ_CLOCK_NOT_SYNCHRONISED = 0x20
TQ_ACCURACY_MASK = 0x1F


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
