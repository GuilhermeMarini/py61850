# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""SCL -- the substation configuration files (``.scd`` / ``.cid`` / ``.icd``).

    from py61850.scl import SclDocument

    doc = SclDocument.parse("station.scd")
    for name in doc.ied_names:
        ied = doc.ied(name)
        for ln in ied.logical_nodes():
            for attr in ln.walk():
                print(attr.reference(), attr.mms_item(), attr.btype)

    doc.write("station.scd")        # atomically, and byte for byte

    undo = doc.apply_edit(SetAttributes(el, {"desc": "52a"}))
    doc.apply_edit(undo)           # and it is the file it was again

**Reading is not the whole of it: a document written back out is the file it
came from.** ``SclDocument.to_bytes`` and ``SclDocument.write`` hold a
fidelity guarantee -- comments, indentation, attribute order, namespace
prefixes, declarations nothing uses, the line ending and the XML declaration
all survive a parse and a serialise, with five exceptions no XML parser can
observe. :meth:`SclDocument.to_bytes` names all five. It matters because the
file goes back to DIGSI and to SEL Architect: a library that reformats the
99 % of a station export it did not touch turns every save into a whole-file
diff.

**What this package is for.** It is a general IEC 61850-6 implementation,
judged against what any SCL tool would need -- IEDScout, IEC Browser, OpenSCD
-- not against what one consumer happens to extract today. If a vendor-neutral
tool would show it or act on it, the model should express it.

**Vendor specifics never enter this package; they attach to it.** Two seams
carry them, and both are standard SCL:

- ``Private`` elements, exposed on every model node as ``.privates``, keyed by
  ``type``. One reference station carries 5,886 of a single vendor's type.
- Standard attributes whose VALUE holds a vendor grammar. ``sAddr`` is the
  common one: this package hands over the string and takes no view on it,
  because the grammar inside it belongs to whoever wrote the file.

A vendor library reads its own half off these model nodes. It never parses the
XML a second time, and no vendor concept is reflected here.

**Layers.** The granularity follows SCL's own sections rather than being
imposed on them:

    SclDocument     the file: load/parse, edition, header, privates
    .templates      DataTypeTemplates -- station-wide, built once, cached
    .communication  SubNetwork/ConnectedAP/Address/GSE/SMV -- shallow, cheap
    .ied_headers    identifying fields, no instance tree
    .ied(name)      the full instance tree for ONE IED, on demand and cached

Templates are document-scoped because they are shared by every IED; instance
trees are per IED because that is the unit a consumer works in. One reference
SCD carries 178,406 ``DAI`` elements across 30 IEDs.

This package is never imported by ``py61850`` itself, so the MMS client keeps
installing and running unprivileged on any OS. It is pure ``xml.etree`` over
the standard library: no network, no privileges, no dependencies.

**Not implemented yet**, because no file in the reference corpus carries them:
the ``Substation`` section (VoltageLevel/Bay/ConductingEquipment) and ``Log``.
Both are in the schema; neither has test material, and building a tree with
nothing to check it against is how a reader acquires confident wrong answers.
"""

from ._xmlsafe import DtdNotAllowed, reject_dtd_in_bytes, reject_dtd_in_file
from .communication import (
    Address,
    Communication,
    ConnectedAP,
    ControlBlockAddress,
    SubNetwork,
)
from .controls import ControlBlock, DataSet, ExtRef, FCDA, SettingControl
from .document import (
    Header,
    SclDocument,
    children_local,
    iter_local,
    privates_of,
    strip_ns,
)
from .edit import (
    EditRejected,
    Insert,
    Remove,
    SetAttributes,
    SetTextContent,
)
from .model import (
    AccessPoint,
    DataAttribute,
    DataObject,
    Ied,
    IedHeader,
    LDevice,
    LogicalNode,
    Server,
)
from .templates import AttributeSpec, DoTypeSpec, LNodeTypeSpec, TemplatePool

__all__ = [
    "SclDocument", "Header",
    "DtdNotAllowed", "reject_dtd_in_file", "reject_dtd_in_bytes",
    "TemplatePool", "LNodeTypeSpec", "DoTypeSpec", "AttributeSpec",
    "Communication", "SubNetwork", "ConnectedAP", "Address",
    "ControlBlockAddress",
    "IedHeader", "Ied", "AccessPoint", "Server", "LDevice", "LogicalNode",
    "DataObject", "DataAttribute",
    "DataSet", "FCDA", "ControlBlock", "SettingControl", "ExtRef",
    "strip_ns", "iter_local", "children_local", "privates_of",
    "Insert", "Remove", "SetAttributes", "SetTextContent", "EditRejected",
]
