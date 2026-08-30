# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""MMS (ISO 9506) -- the confirmed-service protocol IEC 61850 maps ACSI onto.

    pdu        every request builder and response decoder, with no I/O at all
    types      TypeDescription (GetVariableAccessAttributes result)
    service_error   confirmed-ErrorPDU decoding
    client     MmsClient / FileTransfer -- association, transactions, sockets
    services/  one mixin per service group, composed onto the client

The split between ``pdu`` and ``client`` is deliberate: a request builder and a
response decoder are the same code a *server* needs, only used in the opposite
direction, and code with no sockets in it can be tested against recorded PDUs
offline.  Keep ``pdu`` socket-free.
"""
