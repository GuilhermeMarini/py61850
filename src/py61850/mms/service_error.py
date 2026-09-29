# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""Decode the MMS confirmed-ErrorPDU and RejectPDU into something a human
can read."""

from ..core import ber

_ERROR_CLASS = {
    0x80: "vmd-state", 0x81: "application-reference", 0x82: "definition",
    0x83: "resource", 0x84: "service", 0x85: "service-preempt",
    0x86: "time-resolution", 0x87: "access", 0x88: "initiate",
    0x89: "conclude", 0x8A: "cancel", 0x8B: "file", 0x8C: "others",
}
# ServiceError.errorClass.file (ISO 9506-2) -- numbering starts at other (0)
_FILE_ERROR = {
    0: "other", 1: "filename-ambiguous", 2: "file-busy",
    3: "filename-syntax-error", 4: "content-type-invalid",
    5: "position-invalid", 6: "file-access-denied", 7: "file-non-existent",
    8: "duplicate-filename", 9: "insufficient-space-in-filestore",
}


def _parse_service_error(pdu: bytes):
    """confirmed-ErrorPDU -> (invoke, class_name, code_name), or None when the
    errorClass cannot be found. Raises IndexError/ValueError on bad BER."""
    _, body, _ = ber.read_tlv(pdu, 0)                     # unwrap 0xA2
    invoke = None
    for t, v in ber.iter_tlv(body):
        if t == 0x80:
            invoke = int.from_bytes(v, "big")
        elif t == 0xA2:                                   # serviceError [2]
            for et, ev in ber.iter_tlv(v):
                if et == 0xA0:                            # errorClass [0] CHOICE
                    for ct, cv in ber.iter_tlv(ev):
                        cls = _ERROR_CLASS.get(ct, f"class-0x{ct:02x}")
                        code = cv[0] if cv else -1
                        detail = _FILE_ERROR.get(code, str(code)) if ct == 0x8B else str(code)
                        return invoke, cls, detail
    return None


def decode_service_error(pdu: bytes) -> str:
    """pdu = confirmed-ErrorPDU (0xA2). Return a human-readable description."""
    try:
        parsed = _parse_service_error(pdu)
        if parsed is None:
            return f"unparsed service error: {pdu.hex()}"
        invoke, cls, detail = parsed
        return f"{cls}/{detail} (invoke {invoke})"
    except (IndexError, ValueError):
        return f"service error: {pdu.hex()}"


def service_error_kind(pdu: bytes):
    """pdu = confirmed-ErrorPDU. Return ``(error_class, error_code)`` by name
    -- ``("file", "file-busy")`` -- or ``(None, None)`` if it does not parse.
    What a retry decision reads, so it never has to match message text."""
    try:
        parsed = _parse_service_error(pdu)
    except (IndexError, ValueError):
        return None, None
    return (None, None) if parsed is None else parsed[1:]


# RejectPDU.rejectReason CHOICE (ISO 9506-2): [n] -> which PDU was refused
_REJECT_PDU = {
    0x81: "confirmed-request", 0x82: "confirmed-response",
    0x83: "confirmed-error", 0x84: "unconfirmed", 0x85: "pdu-error",
    0x86: "cancel-request", 0x87: "cancel-response", 0x88: "cancel-error",
    0x89: "conclude-request", 0x8A: "conclude-response", 0x8B: "conclude-error",
}
_REJECT_REASON = {
    0x81: {0: "other", 1: "unrecognized-service", 2: "unrecognized-modifier",
           3: "invalid-invokeID", 4: "invalid-argument", 5: "invalid-modifier",
           6: "max-serv-outstanding-exceeded", 8: "max-recursion-exceeded",
           9: "value-out-of-range"},
    0x85: {0: "unknown-pdu-type", 1: "invalid-pdu", 2: "illegal-acse-mapping"},
}


def decode_reject(pdu: bytes) -> str:
    """pdu = RejectPDU (0xA4). Return a human-readable description."""
    try:
        _, body, _ = ber.read_tlv(pdu, 0)                 # unwrap 0xA4
        invoke, reason = None, None
        for t, v in ber.iter_tlv(body):
            if t == 0x80:                                 # originalInvokeID [0]
                invoke = int.from_bytes(v, "big")
            elif t in _REJECT_PDU:                        # rejectReason CHOICE
                code = int.from_bytes(v, "big") if v else -1
                detail = _REJECT_REASON.get(t, {}).get(code, str(code))
                reason = f"{_REJECT_PDU[t]}/{detail}"
        if reason is None:
            return f"rejected: {pdu.hex()}"
        return f"rejected: {reason}" + (f" (invoke {invoke})" if invoke is not None else "")
    except (IndexError, ValueError):
        return f"rejected: {pdu.hex()}"
