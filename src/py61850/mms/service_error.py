# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""Decode the MMS confirmed-ErrorPDU into something a human can read."""

from ..core import ber

_ERROR_CLASS = {
    0x80: "vmd-state", 0x81: "application-reference", 0x82: "definition",
    0x83: "resource", 0x84: "service", 0x85: "service-preempt",
    0x86: "time-resolution", 0x87: "access", 0x88: "initiate",
    0x89: "conclude", 0x8A: "cancel", 0x8B: "file", 0x8C: "others",
}
_FILE_ERROR = {
    0: "filename-ambiguous", 1: "file-busy", 2: "filename-syntax-error",
    3: "content-type-invalid", 4: "position-invalid", 5: "file-access-denied",
    6: "file-non-existent", 7: "duplicate-filename",
    8: "insufficient-space-in-filesystem",
}


def decode_service_error(pdu: bytes) -> str:
    """pdu = confirmed-ErrorPDU (0xA2). Return a human-readable description."""
    try:
        _, body, _ = ber.read_tlv(pdu, 0)                 # unwrap 0xA2
        invoke = None
        for t, v in ber.iter_tlv(body):
            if t == 0x80:
                invoke = int.from_bytes(v, "big")
            elif t == 0xA2:                               # serviceError [2]
                for et, ev in ber.iter_tlv(v):
                    if et == 0xA0:                        # errorClass [0] CHOICE
                        for ct, cv in ber.iter_tlv(ev):
                            cls = _ERROR_CLASS.get(ct, f"class-0x{ct:02x}")
                            code = cv[0] if cv else -1
                            detail = _FILE_ERROR.get(code, str(code)) if ct == 0x8B else str(code)
                            return f"{cls}/{detail} (invoke {invoke})"
        return f"unparsed service error: {pdu.hex()}"
    except (IndexError, ValueError):
        return f"service error: {pdu.hex()}"
