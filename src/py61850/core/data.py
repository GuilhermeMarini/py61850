# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""MMS ``Data`` values -- encode and decode.

    MMS Data ::= CHOICE {
        array [1], structure [2], boolean [3], bit-string [4], integer [5],
        unsigned [6], floating-point [7], octet-string [9], visible-string [10],
        binary-time [12], bcd [13], mms-string [16], utc-time [17] ... }

This CHOICE is *not* MMS-only: IEC 61850-8-1 reuses it verbatim for the GOOSE
``allData`` members, and it appears inside the SV ``savPdu`` envelope -- which is
why the codec lives in ``core`` rather than under ``mms``.

Both directions are here on purpose.  Decoding serves the MMS client and the
GOOSE/SV subscribers; encoding serves the MMS server and the GOOSE publisher.
Keeping the pair in one file is what keeps them symmetrical -- every
``encode_*`` round-trips through its matching branch of :func:`decode_data`.
"""

import struct

from . import ber
from .time import decode_utc_time, encode_utc_time, decode_binary_time

# Data CHOICE tags (context-specific; array/structure are constructed)
ARRAY = 0xA1
STRUCTURE = 0xA2
BOOLEAN = 0x83
BIT_STRING = 0x84
INTEGER = 0x85
UNSIGNED = 0x86
FLOATING_POINT = 0x87
OCTET_STRING = 0x89
VISIBLE_STRING = 0x8A
BINARY_TIME = 0x8C
BCD = 0x8D
MMS_STRING = 0x90
UTC_TIME = 0x91


# ---- decoding ------------------------------------------------------------
def decode_data(tag: int, value):
    if tag in (ARRAY, STRUCTURE):
        return [decode_data(t, v) for t, v in ber.iter_tlv(value)]
    if tag == BOOLEAN:
        return bool(value[0]) if value else False
    if tag == BIT_STRING:
        unused = value[0] if value else 0
        bits = "".join(f"{b:08b}" for b in value[1:])
        return bits[:len(bits) - unused] if unused else bits
    if tag == INTEGER:
        return int.from_bytes(value, "big", signed=True)
    if tag == UNSIGNED:
        return int.from_bytes(value, "big")
    if tag == FLOATING_POINT:
        if len(value) == 5:                       # 1 byte exp-width + IEEE754 single
            return struct.unpack("!f", value[1:5])[0]
        if len(value) == 9:
            return struct.unpack("!d", value[1:9])[0]
        return bytes(value).hex()
    if tag == OCTET_STRING:
        return bytes(value).hex()
    if tag in (VISIBLE_STRING, MMS_STRING):
        return bytes(value).decode("latin-1", "replace")
    if tag == UTC_TIME:
        return decode_utc_time(value)
    if tag == BINARY_TIME:
        return decode_binary_time(value)
    return {"tag": f"0x{tag:02x}", "raw": bytes(value).hex()}


# ---- encoding ------------------------------------------------------------
def encode_boolean(v: bool) -> bytes:
    return ber.tlv(BOOLEAN, b"\xff" if v else b"\x00")


def encode_integer(v: int) -> bytes:
    return ber.tlv(INTEGER, ber.enc_int(v))


def encode_unsigned(v: int) -> bytes:
    if v < 0:
        raise ValueError("unsigned MMS Data cannot be negative")
    return ber.tlv(UNSIGNED, ber.enc_uint(v))


def encode_float(v: float, double: bool = False) -> bytes:
    """FloatingPoint: one exponent-width octet, then the IEEE 754 value."""
    if double:
        return ber.tlv(FLOATING_POINT, b"\x0b" + struct.pack("!d", v))
    return ber.tlv(FLOATING_POINT, b"\x08" + struct.pack("!f", v))


def encode_bitstring(bits: str) -> bytes:
    """``bits`` is the "0101..." form :func:`decode_data` produces."""
    unused = (-len(bits)) % 8
    padded = bits + "0" * unused
    body = bytes(int(padded[i:i + 8], 2) for i in range(0, len(padded), 8))
    return ber.tlv(BIT_STRING, bytes([unused]) + body)


def encode_octet_string(v: bytes) -> bytes:
    return ber.tlv(OCTET_STRING, bytes(v))


def encode_visible_string(v: str) -> bytes:
    return ber.tlv(VISIBLE_STRING, v.encode("latin-1", "replace"))


def encode_mms_string(v: str) -> bytes:
    return ber.tlv(MMS_STRING, v.encode("utf-8"))


def encode_utc(epoch_seconds: float, quality: int = 0) -> bytes:
    return ber.tlv(UTC_TIME, encode_utc_time(epoch_seconds, quality))


def encode_structure(members) -> bytes:
    return ber.tlv(STRUCTURE, b"".join(encode_data(m) for m in members))


def encode_array(members) -> bytes:
    return ber.tlv(ARRAY, b"".join(encode_data(m) for m in members))


def encode_data(value, as_array: bool = False) -> bytes:
    """Encode a Python value, inferring the MMS type.

    bool -> boolean, int -> integer, float -> floating-point (single),
    str -> visible-string, bytes -> octet-string, list/tuple -> structure
    (or array when ``as_array``).  A dict of the shape ``decode_data`` returns
    for utc-time round-trips back to utc-time.

    The inference cannot express every MMS type -- unsigned, double-precision
    floats, bit-strings and mms-string are all reachable only through their
    explicit ``encode_*`` function.  Use those when the type matters, which for
    a server answering GetVariableAccessAttributes it always does.
    """
    if isinstance(value, bool):
        return encode_boolean(value)
    if isinstance(value, int):
        return encode_integer(value)
    if isinstance(value, float):
        return encode_float(value)
    if isinstance(value, str):
        return encode_visible_string(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return encode_octet_string(bytes(value))
    if isinstance(value, (list, tuple)):
        return encode_array(value) if as_array else encode_structure(value)
    if isinstance(value, dict) and "utc" in value:
        return encode_utc(value["utc"], value.get("quality") or 0)
    raise TypeError(f"cannot encode {type(value).__name__} as MMS Data")
