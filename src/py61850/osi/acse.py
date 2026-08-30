# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""ISO 8650 ACSE -- the AARQ/AARE that names the application context and
carries the MMS initiate PDU as user-information.
"""

from ..core import ber
from . import oids

AARQ = 0x60
AARE = 0x61
USER_INFORMATION = 0xBE
EXTERNAL = 0x28
SINGLE_ASN1_TYPE = 0xA0


def build_aarq(mms_pdu: bytes, context_oid: bytes = oids.MMS_ACSE) -> bytes:
    # user-information [30] -> EXTERNAL -> indirect-ref 3 -> single-ASN1-type
    ext = ber.int_tlv(0x02, 3)                            # indirect-reference = MMS ctx 3
    ext += ber.tlv(SINGLE_ASN1_TYPE, mms_pdu)
    user_info = ber.tlv(USER_INFORMATION, ber.tlv(EXTERNAL, ext))

    aarq = ber.tlv(0xA1, oids.oid_tlv(context_oid))       # application-context-name [1]
    aarq += user_info
    return ber.tlv(AARQ, aarq)                            # AARQ [APPLICATION 0]


def strip(pdu: bytes) -> bytes:
    """If pdu is an ACSE AARE/AARQ, drill to the MMS PDU in user-information."""
    if not pdu or pdu[0] not in (AARQ, AARE):
        return pdu                            # already an MMS PDU
    _, body, _ = ber.read_tlv(pdu, 0)         # AARE/AARQ contents
    for tag, val in ber.iter_tlv(body):
        if tag == USER_INFORMATION:
            _, ext, _ = ber.read_tlv(val, 0)  # EXTERNAL (0x28)
            for t2, v2 in ber.iter_tlv(ext):
                if t2 == SINGLE_ASN1_TYPE:
                    return v2
    return pdu
