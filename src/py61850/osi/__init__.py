# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""The OSI stack MMS rides on over TCP -- one module per layer.

    TCP -> tpkt (RFC1006) -> cotp (ISO8073 class 0) -> session (ISO8327)
        -> presentation (ISO8823) -> acse (ISO8650) -> [MMS PDU]

``stack`` composes them into the two operations the MMS layer actually needs:
build an association request, and dig a received MMS PDU back out.

None of this applies to GOOSE or SV -- those are raw Layer-2 multicast with no
OSI stack above Ethernet at all, which is why they live under ``link`` instead.
"""
