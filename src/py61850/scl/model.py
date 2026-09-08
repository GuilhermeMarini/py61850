# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""The IED instance model: what one device actually serves.

Built per IED and on demand. That is the unit every consumer works in -- a
simulator stands up one relay, a browser shows one device -- and it is the
only granularity that stays affordable: one reference SCD carries 178,406
``DAI`` elements across 30 IEDs.

The type pool (:mod:`py61850.scl.templates`) is document-scoped and shared,
because ``DataTypeTemplates`` is station-wide. An instance tree without it
would know an LN's ``lnType`` string and nothing else.
"""

from __future__ import annotations

from ..core.refs import ld_name as _ld_name
from ..core.refs import ln_name as _ln_name
from .document import children_local, iter_local, privates_of


class IedHeader:
    """An IED's identifying fields, with no instance tree behind them.

    This is what a station-wide question needs -- listing devices,
    cross-matching against an inventory, resolving an IP -- and it is answered
    without touching the type pool or building a single logical node.
    """

    __slots__ = ("name", "type", "manufacturer", "desc", "config_version",
                 "eng_right", "owner", "privates")

    def __init__(self, el):
        self.name = el.get("name") or ""
        self.type = el.get("type")
        self.manufacturer = el.get("manufacturer")
        self.desc = el.get("desc")
        self.config_version = el.get("configVersion")
        self.eng_right = el.get("engRight")
        self.owner = el.get("owner")
        self.privates = privates_of(el)

    def __repr__(self):
        return f"<IedHeader {self.name!r} type={self.type!r}>"


class LogicalNode:
    """One ``LN`` or ``LN0``.

    Named as MMS spells it: ``prefix + lnClass + inst``, which makes ``LN0``
    plain ``LLN0`` with no special case -- its ``lnClass`` already says so.

    This is deliberately NOT the ``LogicalNode`` exported at ``py61850`` top
    level; that one is an LN discovered live over MMS. Same concept, different
    provenance, and a file's LN carries things a discovered one cannot (its
    type, its datasets, its privates).
    """

    __slots__ = ("element", "ldevice", "ln_class", "prefix", "inst",
                 "ln_type", "desc", "is_ln0", "privates", "_cache")

    def __init__(self, el, ldevice, is_ln0=False):
        self.element = el
        self.ldevice = ldevice
        self.ln_class = el.get("lnClass") or ("LLN0" if is_ln0 else "")
        self.prefix = el.get("prefix") or ""
        self.inst = el.get("inst") or ""
        self.ln_type = el.get("lnType")
        self.desc = el.get("desc")
        self.is_ln0 = is_ln0
        self.privates = privates_of(el)
        self._cache = {}

    @property
    def name(self) -> str:
        """The MMS spelling: ``BKR1CSWI1``, or ``LLN0``."""
        return _ln_name(self.prefix, self.ln_class, self.inst)

    @property
    def reference(self) -> str:
        """``LDName/LNName``."""
        return f"{self.ldevice.ld_name}/{self.name}"

    def __repr__(self):
        return f"<LogicalNode {self.reference!r} lnType={self.ln_type!r}>"


class LDevice:
    """One ``LDevice``: the MMS domain, and the logical nodes in it."""

    __slots__ = ("element", "ied", "inst", "desc", "_ld_name_attr",
                 "logical_nodes")

    def __init__(self, el, ied):
        self.element = el
        self.ied = ied
        self.inst = el.get("inst") or ""
        self.desc = el.get("desc")
        self._ld_name_attr = el.get("ldName")
        nodes = [LogicalNode(n, self, is_ln0=True)
                 for n in children_local(el, "LN0")]
        nodes.extend(LogicalNode(n, self) for n in children_local(el, "LN"))
        #: LN0 first: it carries the datasets and control blocks the rest of
        #: the LDevice refers to, and MMS name lists conventionally lead with it.
        self.logical_nodes = nodes

    @property
    def ln0(self):
        """The ``LN0``, or ``None`` for an LDevice that carries none."""
        return next((n for n in self.logical_nodes if n.is_ln0), None)

    @property
    def ld_name(self) -> str:
        """The MMS domain: ``ldName`` when given, else ``iedName`` + ``inst``."""
        return _ld_name(self.ied.name, self.inst, self._ld_name_attr)

    @property
    def privates(self) -> dict:
        return privates_of(self.element)

    def __repr__(self):
        return f"<LDevice {self.ld_name!r}>"


class Server:
    """The ``Server`` under one access point."""

    __slots__ = ("element", "ldevices", "privates")

    def __init__(self, el, ied):
        self.element = el
        self.ldevices = [LDevice(ld, ied) for ld in children_local(el, "LDevice")]
        self.privates = privates_of(el)

    def __repr__(self):
        return f"<Server ldevices={len(self.ldevices)}>"


class AccessPoint:
    """One ``AccessPoint``. ``server`` is ``None`` when it hosts none.

    An IED may have several -- 41 across 30 IEDs in one reference SCD -- and an
    access point without a Server is normal: one reference station has 28 IEDs
    and 14 Servers, the rest being templates.
    """

    __slots__ = ("element", "name", "server", "privates")

    def __init__(self, el, ied):
        self.element = el
        self.name = el.get("name") or ""
        server_el = next(children_local(el, "Server"), None)
        self.server = Server(server_el, ied) if server_el is not None else None
        self.privates = privates_of(el)

    def __repr__(self):
        return f"<AccessPoint {self.name!r} server={self.server is not None}>"


class Ied:
    """One IED, resolved: its access points, LDevices and logical nodes."""

    __slots__ = ("element", "document", "header", "access_points")

    def __init__(self, el, document):
        self.element = el
        self.document = document
        self.header = IedHeader(el)
        self.access_points = [AccessPoint(ap, self)
                              for ap in iter_local(el, "AccessPoint")]

    # the header's fields, readable straight off the IED
    @property
    def name(self):
        return self.header.name

    @property
    def type(self):
        return self.header.type

    @property
    def manufacturer(self):
        return self.header.manufacturer

    @property
    def desc(self):
        return self.header.desc

    @property
    def config_version(self):
        return self.header.config_version

    @property
    def privates(self):
        return self.header.privates

    def ldevices(self) -> list:
        """Every LDevice across every access point, in document order."""
        return [ld for ap in self.access_points if ap.server is not None
                for ld in ap.server.ldevices]

    def logical_nodes(self) -> list:
        """Every logical node in the IED, in document order."""
        return [n for ld in self.ldevices() for n in ld.logical_nodes]

    def ldevice(self, inst):
        """The LDevice with this ``inst``, or ``None``."""
        return next((ld for ld in self.ldevices() if ld.inst == inst), None)

    def __repr__(self):
        return f"<Ied {self.name!r} lds={len(self.ldevices())}>"
