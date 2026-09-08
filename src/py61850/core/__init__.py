# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""Protocol-agnostic building blocks shared by MMS, GOOSE and SV.

Nothing in this package performs I/O: it is pure encode/decode over bytes.
That is what lets the same code serve a client and a server (MMS), and a
subscriber and a publisher (GOOSE/SV), and be unit-tested offline.

    ber       definite-length BER TLV encode/decode
    data      MMS ``Data`` values -- the CHOICE reused verbatim by GOOSE
              ``allData`` and inside the SV ``savPdu`` envelope
    fc        the IEC 61850-7-2 functional constraints, the control
              model's own data attributes, and the read ranking over both
    refs      IEC 61850-8-1 object reference <-> MMS domain/item names
    quality   the IEC 61850 13-bit Quality bitstring
    time      MMS ``UtcTime`` / ``BinaryTime``

Import these from ``py61850.core.<module>``; they are internal to the package
and may change between releases.
"""
