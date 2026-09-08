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
from ..core.refs import mms_item as _mms_item
from ..core.refs import object_reference as _object_reference
from .document import children_local, privates_of, strip_ns


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

    __slots__ = ("element", "ldevice", "ied", "ln_class", "prefix", "inst",
                 "ln_type", "desc", "is_ln0", "privates", "_cache")

    def __init__(self, el, ldevice, is_ln0=False, ied=None):
        self.element = el
        #: The LDevice this LN is served from, or ``None`` for one declared
        #: straight under an ``AccessPoint``. See :attr:`reference`.
        self.ldevice = ldevice
        self.ied = ied if ldevice is None else ldevice.ied
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
    def reference(self):
        """``LDName/LNName``, or ``None`` for an access-point-level LN.

        ``None`` is not a failure: an LN declared directly under an
        ``AccessPoint`` sits in no logical device, so it has no MMS domain
        and 61850-6 gives it no ``LDName/LNName`` reference to return. It is
        still a real logical node with a type, inputs and privates -- see
        :attr:`AccessPoint.logical_nodes`.
        """
        if self.ldevice is None:
            return None
        return f"{self.ldevice.ld_name}/{self.name}"

    @property
    def data_objects(self) -> dict:
        """``{name: DataObject}`` -- every DO this LN's type declares.

        Resolved through the document's type pool and cached on the node. An
        LN whose ``lnType`` the file does not carry gets an empty mapping: a
        dangling type reference is a fact to report, not a reason to fail the
        document.
        """
        dos = self._cache.get("data_objects")
        if dos is None:
            pool = self.ied.document.templates
            spec = pool.lnode_type(self.ln_type)
            dos = self._cache["data_objects"] = {}
            if spec is not None:
                doi_by_name = {d.get("name"): d
                               for d in children_local(self.element, "DOI")}
                for do_name, do_type in spec.objects.items():
                    dos[do_name] = DataObject(do_name, do_type, self,
                                              doi_by_name.get(do_name))
        return dos

    def walk(self):
        """Every data attribute in this logical node, depth first."""
        for do in self.data_objects.values():
            for attr in do.walk():
                yield attr

    @property
    def data_sets(self) -> dict:
        """``{name: DataSet}`` declared by THIS logical node.

        Direct children only. A DataSet under LN0 and one under an LN are
        both descendants of the LDevice, so collecting by descent would
        attribute each of them to both.
        """
        return self._lazy("data_sets", lambda: _controls().data_sets_of(self))

    @property
    def control_blocks(self) -> dict:
        """``{name: ControlBlock}`` -- report, GOOSE, sampled-value and log."""
        return self._lazy("control_blocks",
                          lambda: _controls().control_blocks_of(self))

    @property
    def setting_control(self):
        """The ``SettingControl``, or ``None``."""
        return self._lazy("setting_control",
                          lambda: _controls().setting_control_of(self))

    @property
    def ext_refs(self) -> list:
        """The ``Inputs``/``ExtRef`` entries, in document order."""
        return self._lazy("ext_refs", lambda: _controls().ext_refs_of(self))

    def _lazy(self, key, build):
        if key not in self._cache:
            self._cache[key] = build()
        return self._cache[key]

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
    """One ``AccessPoint``: a ``Server``, and any LNs declared beside it.

    An IED may have several -- 41 across 30 IEDs in one reference SCD -- and an
    access point without a Server is normal: it may delegate to a sibling
    access point's Server via ``<ServerAt>`` instead of hosting one itself.
    Measured on the reference corpus: of the access points with no Server,
    10 of 11 in the SEL station and 12 of 12 in the mixed-vendor station
    carry a ``ServerAt``; the Siemens station has none without a Server at
    all (0 of 14). ``ServerAt`` is not yet modelled here, so ``server is
    None`` on this class means "no Server, and possibly a ServerAt instead"
    rather than "no Server, full stop" -- a consumer that needs the
    delegation resolved still has to read ``<ServerAt>`` off ``element``
    itself.

    ``logical_nodes`` holds the LNs 61850-6 allows DIRECTLY under an access
    point, outside any ``Server``. They are how a gateway or proxy declares
    the interface it presents -- an ``ITCI`` for a telecontrol interface, an
    ``IHMI`` for an operator one -- and they are real logical nodes: they
    carry a type, privates, and their own ``Inputs``. What they do not carry
    is a logical device, so they have no MMS domain and no
    ``LDName/LNName`` reference; :attr:`LogicalNode.reference` is ``None``
    for them.

    Measured on the reference corpus: exactly one, the ``ITCI`` on the RTAC
    gateway's ``C1`` access point in the SEL station, and it holds 58 bound
    ``ExtRef`` entries -- a quarter of that station's subscriptions.  Before
    they were modelled, ``Ied.ext_refs()`` claimed to return every ExtRef in
    the IED and returned 1,290 of that gateway's 1,348.
    """

    __slots__ = ("element", "name", "server", "logical_nodes", "privates")

    def __init__(self, el, ied):
        self.element = el
        self.name = el.get("name") or ""
        server_el = next(children_local(el, "Server"), None)
        self.server = Server(server_el, ied) if server_el is not None else None
        self.logical_nodes = [LogicalNode(n, None, ied=ied)
                              for n in children_local(el, "LN")]
        self.privates = privates_of(el)

    def __repr__(self):
        return (f"<AccessPoint {self.name!r} server={self.server is not None} "
                f"lns={len(self.logical_nodes)}>")


class Ied:
    """One IED, resolved: its access points, LDevices and logical nodes."""

    __slots__ = ("element", "document", "header", "access_points")

    def __init__(self, el, document):
        self.element = el
        self.document = document
        self.header = IedHeader(el)
        # DIRECT CHILDREN only -- not `iter_local`'s descendant search.
        # `<AccessPoint>` is schema-valid only directly under `<IED>`, and an
        # IED's `Private` blocks are exactly where DIGSI already nests
        # device-shaped elements (see `document._ied_elements`'s docstring
        # for the decoy `<IED>` this same shape of bug produced once); a
        # descendant scan here would be one more thing a vendor `Private`
        # could shadow.
        self.access_points = [AccessPoint(ap, self)
                              for ap in children_local(el, "AccessPoint")]

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
        """Every logical node in the IED, in document order.

        The LDevices' nodes first, then the ones declared directly under an
        access point. The access-point ones come last rather than in strict
        document order because they are the exception: a consumer walking
        this list is almost always after the servable model, and the
        handful that answer :attr:`LogicalNode.reference` with ``None``
        are better met at the end than interleaved.
        """
        nodes = [n for ld in self.ldevices() for n in ld.logical_nodes]
        nodes.extend(n for ap in self.access_points for n in ap.logical_nodes)
        return nodes

    def ldevice(self, inst):
        """The LDevice with this ``inst``, or ``None``."""
        return next((ld for ld in self.ldevices() if ld.inst == inst), None)

    def ext_refs(self) -> list:
        """Every ExtRef in the IED, in document order."""
        return [r for n in self.logical_nodes() for r in n.ext_refs]

    def control_blocks(self) -> list:
        """Every control block in the IED, in document order."""
        return [cb for n in self.logical_nodes()
                for cb in n.control_blocks.values()]

    def __repr__(self):
        return f"<Ied {self.name!r} lds={len(self.ldevices())}>"


# Cycle detection is the primary guard against a self-referencing SDO chain
# -- see the `seen` frozenset threaded through DataObject.__init__, the same
# shape as TemplatePool._attribute's `seen` for a self-referencing DAType.
# _MAX_DO_DEPTH remains underneath as a backstop for a long chain of
# DISTINCT types that never repeats, not as what stops a cycle: a bare depth
# cap alone lets a DOType with three same-typed SDOs build 9,841 DataObject
# instances from a single top-level DO before it gives up.
_MAX_DO_DEPTH = 8


class DataAttribute:
    """One data attribute of one instance: what the type declares, plus what
    the instance overrides.

    ``value`` and ``s_addr`` come from the ``DAI``, when the file carries one;
    everything else comes from the type. An attribute with no ``DAI`` is a
    complete, servable attribute with no configured value -- which is the
    normal case and the reason this model exists.
    """

    __slots__ = ("name", "fc", "btype", "type", "enum_values", "value",
                 "s_addr", "sub_attributes", "path", "privates",
                 "logical_node", "_do_path")

    def __init__(self, spec, logical_node, do_path, dai_el=None):
        self.name = spec.name
        self.fc = spec.fc
        self.btype = spec.btype
        self.type = spec.type
        self.enum_values = spec.enum_values
        self.privates = spec.privates
        self.logical_node = logical_node
        self._do_path = tuple(do_path)
        #: The descent from the data object to this attribute, one entry per
        #: level: ``("Pos", "Oper", "ctlVal")``.
        self.path = self._do_path + (spec.name,)
        self.value = None
        self.s_addr = None
        self.sub_attributes = {}
        if dai_el is not None:
            self._apply_instance(dai_el)
        for name, sub_spec in spec.sub_specs.items():
            self.sub_attributes[name] = DataAttribute(
                sub_spec, logical_node, self.path,
                _child_instance(dai_el, name))

    def _apply_instance(self, el):
        self.s_addr = el.get("sAddr")
        val = next((c for c in el if strip_ns(c.tag) == "Val"), None)
        if val is not None:
            self.value = val.text

    def mms_item(self) -> str:
        """``CSWI1$CO$Pos$Oper$ctlVal`` -- the 61850-8-1 name."""
        return _mms_item(self.logical_node.name, self.fc or "", self.path)

    def reference(self):
        """``QPC1PRO/CSWI1.Pos.Oper.ctlVal`` -- the 61850-6 object reference.

        ``None`` when the logical node is not in a logical device, which is
        the access-point-level case: there is no MMS domain to name it in.
        :meth:`mms_item` is unaffected -- it names the attribute within its
        LN and never needed the domain.
        """
        ldevice = self.logical_node.ldevice
        if ldevice is None:
            return None
        return _object_reference(ldevice.ld_name,
                                 self.logical_node.name, self.path)

    def walk(self):
        """This attribute and every attribute beneath it, depth first."""
        yield self
        for sub in self.sub_attributes.values():
            for item in sub.walk():
                yield item

    def __repr__(self):
        return f"<DataAttribute {self.mms_item()!r} bType={self.btype!r}>"


class DataObject:
    """One data object of one logical node, resolved through its ``DOType``.

    ``attributes`` holds EVERY attribute the type declares, not only those the
    instance overrides. That is the difference between a model and an
    extraction: an MMS server answering ``GetNameList``, or a browser drawing
    a device tree, needs the ones nobody configured too.
    """

    __slots__ = ("name", "cdc", "do_type", "attributes", "sub_objects",
                 "privates", "logical_node", "path")

    def __init__(self, name, do_type_id, logical_node, doi_el=None,
                 path=(), depth=0, seen=frozenset()):
        self.name = name
        self.logical_node = logical_node
        self.path = tuple(path) + (name,)
        self.do_type = do_type_id
        self.attributes = {}
        self.sub_objects = {}
        self.privates = privates_of(doi_el) if doi_el is not None else {}
        pool = logical_node.ied.document.templates
        spec = pool.do_type(do_type_id)
        if spec is None:
            self.cdc = None
            return
        self.cdc = spec.cdc
        for attr_name, attr_spec in spec.attributes.items():
            self.attributes[attr_name] = DataAttribute(
                attr_spec, logical_node, self.path,
                _child_instance(doi_el, attr_name))
        if depth >= _MAX_DO_DEPTH or do_type_id in seen:
            return
        next_seen = seen | {do_type_id}
        for sdo_name, sdo_type in spec.sub_objects.items():
            self.sub_objects[sdo_name] = DataObject(
                sdo_name, sdo_type, logical_node,
                _child_instance(doi_el, sdo_name), self.path, depth + 1,
                next_seen)

    def walk(self):
        """Every attribute in this object and its sub-objects, depth first."""
        for attr in self.attributes.values():
            for item in attr.walk():
                yield item
        for sub in self.sub_objects.values():
            for item in sub.walk():
                yield item

    @property
    def reference(self):
        """``None`` for an access-point-level LN; see
        :meth:`DataAttribute.reference`."""
        ldevice = self.logical_node.ldevice
        if ldevice is None:
            return None
        return _object_reference(ldevice.ld_name,
                                 self.logical_node.name, self.path)

    def __repr__(self):
        return f"<DataObject {self.reference!r} cdc={self.cdc!r}>"


def _child_instance(el, name):
    """The ``DOI``/``SDI``/``DAI`` child of ``el`` carrying ``name``, or ``None``.

    One helper for all three because the instance side spells the descent with
    three element names for what the type side spells with one nesting: a
    ``DOI`` holds ``SDI``s and ``DAI``s, an ``SDI`` holds more of both.
    """
    if el is None:
        return None
    for child in el:
        if (strip_ns(child.tag) in ("DOI", "SDI", "DAI")
                and child.get("name") == name):
            return child
    return None


def _controls():
    """Imported lazily so `model` and `controls` can refer to each other."""
    from . import controls
    return controls
