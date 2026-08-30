# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""Compose the OSI layers into the two operations MMS needs.

    Session CONNECT (ISO 8327)
      -> Presentation CP-type (ISO 8823)
           -> ACSE AARQ (ISO 8650)
                -> MMS initiate-RequestPDU (ISO 9506)

The whole thing goes out as ONE COTP DT payload.  The relay replies with the
mirror image: Session ACCEPT -> CPA -> AARE -> initiate-ResponsePDU.

These functions are direction-agnostic byte plumbing -- the MMS *content* they
carry is built in :mod:`py61850.mms.pdu`, so an MMS server can reuse this same
module to unwrap requests and wrap responses.
"""

from . import acse, presentation, session
from ..errors import TransportError

MMS_CONTEXT_ID = presentation.CTX_MMS


def build_associate_request(mms_initiate: bytes, **sel) -> bytes:
    """Wrap an MMS initiate-RequestPDU in ACSE + Presentation + Session."""
    aarq = acse.build_aarq(mms_initiate)
    cp = presentation.build_cp(
        aarq,
        calling_psel=sel.get("calling_psel", b"\x00\x00\x00\x01"),
        called_psel=sel.get("called_psel", b"\x00\x00\x00\x01"),
    )
    return session.build_connect(
        cp,
        calling_ssel=sel.get("calling_ssel", b"\x00\x01"),
        called_ssel=sel.get("called_ssel", b"\x00\x01"),
    )


def wrap_mms_pdu(pdu: bytes) -> bytes:
    """Wrap a data-phase MMS PDU in Presentation + Session."""
    return session.wrap_data(presentation.wrap_user_data(MMS_CONTEXT_ID, pdu))


def extract_mms_from_response(payload: bytes) -> bytes:
    """Dig the MMS PDU out of a Session/Presentation/ACSE response payload.

        Session SPDU(s) -> Presentation (CPA 0x31 or fully-encoded-data 0x61)
                        -> PDV-list -> single-ASN1-type [0] -> MMS PDU
    """
    pres = session.strip(payload)
    fed = presentation.find_fully_encoded_data(pres)
    if fed is None:
        raise TransportError("no presentation user-data found in response")
    mms = presentation.pdu_from_fully_encoded_data(fed)
    if mms is None:
        raise TransportError("no MMS PDU found in presentation user-data")
    return acse.strip(mms)
