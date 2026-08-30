# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""Exception hierarchy for the py61850 package.

All errors raised by the library derive from :class:`Iec61850Error`, so a
caller can catch the whole family with one ``except Iec61850Error``.

    Iec61850Error
    ├── TransportError   TPKT/COTP/socket and OSI framing        (osi/)
    ├── MmsError         MMS service errors, associations, rejects (mms/)
    ├── LinkError        packet capture: no driver, no privileges  (link/)
    ├── GooseError       GOOSE PDU decode / publish failures       (goose/)
    ├── SvError          Sampled Values PDU decode / publish       (sv/)
    └── SclError         SCL file parse / model errors             (scl/)

The last four have no raisers yet -- they are declared here so that when their
packages land, callers already catching :class:`Iec61850Error` keep working and
the tree stays in one file.
"""


class Iec61850Error(Exception):
    """Base class for every error raised by py61850."""


class TransportError(Iec61850Error):
    """TPKT / COTP / socket-level failure (connection, framing, timeout)."""


class MmsError(Iec61850Error):
    """MMS-level failure: association refused, service error, or reject PDU."""


class LinkError(Iec61850Error):
    """Capture-source failure: no libpcap/Npcap for an L2 (GOOSE/SV) capture,
    missing privileges, unknown interface, bad frame."""


class GooseError(Iec61850Error):
    """GOOSE-level failure: undecodable APDU, publisher misconfiguration."""


class SvError(Iec61850Error):
    """Sampled Values failure: undecodable savPdu, publisher misconfiguration."""


class SclError(Iec61850Error):
    """SCL (.scd/.cid/.icd) parse or model error."""
