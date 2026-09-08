# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""py61850 -- a pure-Python IEC 61850 toolkit.

Today: an MMS (ISO 9506) client that speaks the full OSI stack MMS rides on,
using only the standard library:

    TCP -> TPKT (RFC1006) -> COTP (ISO8073) -> ISO Session -> ISO Presentation
        -> ACSE (AARQ/AARE) -> MMS (Initiate + confirmed services)

Public API
----------
    MmsClient      association + directory/read/data-definition services
    FileTransfer   MmsClient + MMS file services (list / search / download)
    DirEntry       one entry returned by FileTransfer.file_directory()
    LogicalNode    one LN returned by MmsClient.find_logical_nodes()
    folder_of      the folder part of an MMS file name, for grouping hits

    FUNCTIONAL_CONSTRAINTS / fc_is_control / fc_read_rank
                   the 61850-7-2 functional constraints, shared by the SCL
                   reader and the live MMS path

    CONTROL_DATA_ATTRIBUTES / fc_is_control_attribute
                   the control model's own data attributes -- the same
                   "command, not a reading" question asked of an attribute
                   name rather than of an FC

    mms_item / object_reference / split_item / da_parts
                   61850-8-1 naming: object reference <-> MMS domain and item

    Iec61850Error  base of every error the library raises
      TransportError   TPKT/COTP/socket and OSI framing failures
      MmsError         MMS service errors, rejects, refused associations
      LinkError / GooseError / SvError / SclError   reserved (see ROADMAP)

    decode_read_response / decode_data_definition / decode_service_error
                   decoders for the raw TLV bytes MmsClient.read() &c. return

Quick start
-----------
    from py61850 import MmsClient

    with MmsClient("192.0.2.22") as c:
        for ld in c.get_server_directory():
            print(ld, c.read_value(ld, "LLN0$ST$Beh$stVal"))

Package layout
--------------
    core/   BER, MMS Data values, Quality, time -- shared by MMS, GOOSE and SV,
            pure encode/decode with no I/O
    osi/    the OSI layers under MMS-over-TCP (MMS only; GOOSE/SV are raw L2)
    mms/    MMS PDU codec (``mms.pdu``) and the client that drives it
    link/   raw Ethernet -- planned, and never imported from here, so the MMS
    goose/  client keeps installing and running unprivileged on any OS
    sv/
    scl/    SCL parsing -- planned; the shared spine of both simulators
    cli/    the command-line drivers

Only the names re-exported below are public; everything else may change between
releases.
"""

from .errors import (
    Iec61850Error,
    TransportError,
    MmsError,
    LinkError,
    GooseError,
    SvError,
    SclError,
)
from .mms.client import MmsClient, FileTransfer
from .mms.pdu import DirEntry, decode_read_response
from .mms.services.directory import LogicalNode
from .mms.services.files import folder_of
from .mms.service_error import decode_service_error
from .mms.types import decode_data_definition
from .core.fc import CONTROL_DATA_ATTRIBUTES, FUNCTIONAL_CONSTRAINTS, \
    is_control as fc_is_control, \
    is_control_attribute as fc_is_control_attribute, \
    read_rank as fc_read_rank
from .core.refs import da_parts, mms_item, object_reference, split_item

__version__ = "0.3.0.dev1"

__all__ = [
    "MmsClient",
    "FileTransfer",
    "DirEntry",
    "LogicalNode",
    "folder_of",
    "FUNCTIONAL_CONSTRAINTS",
    "fc_is_control",
    "fc_read_rank",
    "CONTROL_DATA_ATTRIBUTES",
    "fc_is_control_attribute",
    "mms_item",
    "object_reference",
    "split_item",
    "da_parts",
    "Iec61850Error",
    "TransportError",
    "MmsError",
    "LinkError",
    "GooseError",
    "SvError",
    "SclError",
    "decode_read_response",
    "decode_data_definition",
    "decode_service_error",
    "__version__",
]
