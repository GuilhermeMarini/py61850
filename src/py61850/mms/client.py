# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""The MMS client: association, the data-phase transaction loop, and the
composed service classes.

This is the only place in ``mms`` that owns a socket.  Everything it sends is
built by :mod:`py61850.mms.pdu` and wrapped by :mod:`py61850.osi.stack`;
everything it receives goes back through the same two modules in reverse.

    MmsClient      directory / read / data-definition services
    FileTransfer   the above plus the MMS file services

One client owns one socket and one invoke counter, so it is **not thread-safe**.
One client per thread; a pool is ROADMAP 0.2.
"""

from ..errors import MmsError
from ..osi import stack
from ..osi.cotp import CotpTransport
from . import pdu
from .services.directory import DirectoryMixin
from .services.files import FileServicesMixin
from .services.read import ReadMixin


class MmsClientBase:
    """Association + confirmed-service transactions. No services of its own."""

    #: MMS PDU ceiling assumed until the peer tells us its own, in bytes.
    DEFAULT_MAX_PDU_SIZE = 1024

    def __init__(self, host, port=102, timeout=10):
        self.t = CotpTransport(host, port, timeout=timeout)
        self.invoke = 0
        # Negotiated in connect(); read-only for callers. max_pdu_size is the
        # largest request the server accepts (its localDetailCalled), which is
        # what a batching read has to size itself against -- see read_many.
        self.max_pdu_size = self.DEFAULT_MAX_PDU_SIZE
        self.max_outstanding = 1

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def connect(self):
        self.t.connect()
        self.t.send(stack.build_associate_request(pdu.build_initiate()))
        mms = stack.extract_mms_from_response(self.t.recv())
        if mms[0] != pdu.INITIATE_RESPONSE:
            raise MmsError(f"association failed, MMS tag 0x{mms[0]:02x}")
        limits = pdu.decode_initiate_response(mms)
        self.max_pdu_size = limits.get("localDetailCalled",
                                       self.DEFAULT_MAX_PDU_SIZE)
        self.max_outstanding = limits.get("maxServOutstandingCalling", 1)
        return mms

    def close(self):
        self.t.close()

    # ---- data-phase transaction ------------------------------------------
    def _transact(self, service: bytes) -> bytes:
        """Send one confirmed request, return the service-response TLV bytes."""
        self.invoke = (self.invoke + 1) & 0x7FFF
        self.t.send(stack.wrap_mms_pdu(pdu.confirmed_request(self.invoke, service)))
        mms = stack.extract_mms_from_response(self.t.recv())
        return pdu.service_from_response(mms)


class MmsClient(DirectoryMixin, ReadMixin, MmsClientBase):
    """Association + directory / read / data-definition services."""


class FileTransfer(FileServicesMixin, MmsClient):
    """MmsClient extended with the MMS file services."""
