# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""Sampled Values -- IEC 61850-9-2 Layer-2 streaming.

**Planned; nothing implemented yet.** Follows GOOSE (ROADMAP 2.0) -- once the
L2 machinery in :mod:`py61850.link` exists, SV is the same machinery at a
different EtherType (0x88BA).

    pdu         savPdu envelope (BER, via :mod:`py61850.core.ber`) around ASDUs
    subscriber  decode a live stream
    publisher   transmit a stream

SV is the reason :mod:`py61850.core.ber` carries a zero-copy decode path
(``read_tlv_at`` / ``iter_tlv_view``): at 4000 ASDUs/second the per-TLV copy in
the convenience API is the dominant cost.
"""

__all__ = []
