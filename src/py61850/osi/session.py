# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""ISO 8327 Session layer.

Two shapes matter: the CONNECT SPDU that carries the presentation CP-type
during association, and the GIVE-TOKENS + DATA-TRANSFER pair that prefixes
every data-phase PDU.
"""

SPDU_CONNECT = 0x0D
SPDU_ACCEPT = 0x0E
SPDU_GIVE_TOKENS = 0x01

PGI_USER_DATA = 0xC1
PGI_EXTENDED_USER_DATA = 0xC2

# GIVE-TOKENS (SI=1, LI=0) + DATA-TRANSFER (SI=1, LI=0)
DATA_PREFIX = b"\x01\x00\x01\x00"


def build_connect(pres: bytes, calling_ssel=b"\x00\x01", called_ssel=b"\x00\x01") -> bytes:
    """CONNECT SPDU carrying the presentation CP-type as User Data."""
    p = b""
    p += bytes([0x05, 0x06, 0x13, 0x01, 0x00, 0x16, 0x01, 0x02])  # Connect Accept Item
    p += bytes([0x14, 0x02, 0x00, 0x02])                          # Session requirements: duplex
    p += bytes([0x33, len(calling_ssel)]) + calling_ssel          # Calling Session Selector
    p += bytes([0x34, len(called_ssel)]) + called_ssel            # Called  Session Selector
    p += bytes([PGI_USER_DATA, len(pres)]) + pres                 # User Data (PGI 193)
    return bytes([SPDU_CONNECT, len(p)]) + p


def wrap_data(user_data: bytes) -> bytes:
    """Prefix a data-phase presentation PDU with GIVE-TOKENS + DATA-TRANSFER."""
    return DATA_PREFIX + user_data


def strip(payload: bytes) -> bytes:
    """Consume the Session layer, returning the presentation bytes."""
    if not payload:
        return payload
    si = payload[0]
    if si == SPDU_GIVE_TOKENS:               # data phase: GIVE-TOKENS + DATA-XFER
        i = 0
        while i < len(payload) and payload[i] == SPDU_GIVE_TOKENS:
            li = payload[i + 1]
            i += 2 + li
        return payload[i:]
    if si in (SPDU_CONNECT, SPDU_ACCEPT):
        li = payload[1]
        params = payload[2:2 + li]
        j = 0
        while j < len(params):               # walk PI/PGI units, find User Data
            code = params[j]
            plen = params[j + 1]
            val = params[j + 2:j + 2 + plen]
            if code in (PGI_USER_DATA, PGI_EXTENDED_USER_DATA):
                return val
            j += 2 + plen
    return payload
