"""TPKT (RFC 1006) -- the 4-byte frame that carries a COTP TPDU over TCP.

    +--------+--------+----------------+
    | vrsn=3 | resvd  |  length (u16)  |   length = whole TPKT incl. these 4 bytes
    +--------+--------+----------------+

Pure framing, no socket: :class:`py61850.osi.cotp.CotpTransport` owns the I/O.
"""

import struct

from ..errors import TransportError

VERSION = 0x03
HEADER_LEN = 4


def wrap(payload: bytes) -> bytes:
    """Wrap a COTP TPDU in a TPKT header."""
    return b"\x03\x00" + struct.pack("!H", len(payload) + HEADER_LEN) + payload


def payload_len(header: bytes) -> int:
    """Validate a 4-byte TPKT header; return the length of the payload after it."""
    if len(header) < HEADER_LEN:
        raise TransportError("short TPKT header")
    if header[0] != VERSION:
        raise TransportError(f"bad TPKT version 0x{header[0]:02x}")
    total = struct.unpack("!H", header[2:4])[0]
    if total < HEADER_LEN:
        raise TransportError(f"bad TPKT length {total}")
    return total - HEADER_LEN
