# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""GOOSE -- IEC 61850-8-1 Layer-2 publish/subscribe.

**Planned; nothing implemented yet.** See ROADMAP 2.0.

    pdu         GOOSE APDU encode/decode (gocbRef, stNum/sqNum, timeAllowedToLive,
                test/simulation flags, dataset allData). BER, so it reuses
                :mod:`py61850.core.ber`; the allData members are MMS ``Data``
                values, so they reuse :mod:`py61850.core.data` unchanged.
    subscriber  receive + decode live, with stNum-jump and TAL-expiry detection
    publisher   transmit from an SCL-defined GoCB + dataset, with correct
                stNum/sqNum sequencing and T0/T1 retransmission timing

Requires the ``l2`` capture source in :mod:`py61850.link`, and therefore a
libpcap/Npcap driver and an L2-adjacent NIC -- GOOSE is a non-IP EtherType, so
no plain socket can reach it on any OS.  That prerequisite is GOOSE's alone:
MMS rides TCP/IP and needs nothing installed, sniffing included.  See
:mod:`py61850.link` for the per-source prerequisites.  Import this explicitly;
the MMS client never does.
"""

__all__ = []
