# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""Datasets, control blocks, and the subscriptions that point at them.

These all hang off a logical node -- usually ``LN0`` -- and they are what turns
a static data model into a communicating one: a dataset says which attributes
travel together, a control block says how and when they are published, and an
``ExtRef`` says who is listening.
"""

from __future__ import annotations

from ..core.refs import ln_name as _ln_name
from ..core.refs import mms_item as _mms_item
from .document import children_local, privates_of

#: The control-block elements this module reads, by their SCL element name.
#: ``SettingControl`` is deliberately absent -- it has no name and there is at
#: most one per LN0, so it does not belong in a mapping keyed by name.
#:
#: Public because :mod:`py61850.scl.control_block` decides what a `DataSet` is
#: used BY from the same list, and a second copy of it there is how the read
#: model and the edit layer would come to disagree about whether a `LogControl`
#: is a control block.
CONTROL_BLOCK_TAGS = ("ReportControl", "GSEControl", "SampledValueControl",
                      "LogControl")


class FCDA:
    """One dataset member: a functionally-constrained data attribute."""

    __slots__ = ("element", "ld_inst", "prefix", "ln_class", "ln_inst",
                 "do_name", "da_name", "fc")

    def __init__(self, el):
        #: The live element. Every capability in :mod:`py61850.scl.extref`
        #: takes elements, as the reference implementation does, so a caller
        #: that found this member through the model can hand it straight over.
        self.element = el
        self.ld_inst = el.get("ldInst") or ""
        self.prefix = el.get("prefix") or ""
        self.ln_class = el.get("lnClass") or ""
        self.ln_inst = el.get("lnInst") or ""
        self.do_name = el.get("doName") or ""
        self.da_name = el.get("daName") or ""
        self.fc = el.get("fc") or ""

    @property
    def ln_name(self) -> str:
        return _ln_name(self.prefix, self.ln_class, self.ln_inst)

    def mms_item(self) -> str:
        """The 8-1 item name for this member.

        ``daName`` may be empty -- an FCDA naming a whole data object is legal
        -- and then the item simply has no trailing attribute segment.
        """
        path = [p for p in self.do_name.split(".") if p]
        path += [p for p in self.da_name.split(".") if p]
        return _mms_item(self.ln_name, self.fc, path)

    def __repr__(self):
        return f"<FCDA {self.mms_item()!r}>"


class DataSet:
    """One ``DataSet`` and its members, in declared order.

    Order is load-bearing: a GOOSE frame's ``allData`` follows the dataset's
    member order, so a subscriber decoding by position depends on it.
    """

    __slots__ = ("name", "desc", "fcdas", "privates", "logical_node")

    def __init__(self, el, logical_node):
        self.name = el.get("name") or ""
        self.desc = el.get("desc")
        self.fcdas = [FCDA(f) for f in children_local(el, "FCDA")]
        self.privates = privates_of(el)
        self.logical_node = logical_node

    def __repr__(self):
        return f"<DataSet {self.name!r} members={len(self.fcdas)}>"


class ControlBlock:
    """One ``ReportControl``, ``GSEControl``, ``SampledValueControl`` or
    ``LogControl``.

    ``key`` is ``(iedName, ldInst, name)`` -- the same triple
    :class:`~py61850.scl.communication.ControlBlockAddress` is keyed by, which
    is what lets the two halves of the file be joined.
    """

    __slots__ = ("kind", "name", "desc", "dat_set", "conf_rev", "app_id",
                 "type", "privates", "logical_node", "element")

    def __init__(self, kind, el, logical_node):
        self.kind = kind
        self.element = el
        self.name = el.get("name") or ""
        self.desc = el.get("desc")
        self.dat_set = el.get("datSet")
        self.conf_rev = el.get("confRev")
        # GSEControl spells it appID; SampledValueControl carries both smvID
        # and (optionally) appID, and it is smvID that every real SVCB in
        # the reference corpus actually uses -- of the 16 SampledValueControl
        # elements in the mixed-vendor reference station, all 16 carry
        # smvID and none carries appID, so this preference order never
        # actually chooses between them for an SVCB in practice.
        self.app_id = el.get("appID") or el.get("smvID")
        self.type = el.get("type")
        self.privates = privates_of(el)
        self.logical_node = logical_node

    @property
    def key(self) -> tuple:
        """``(iedName, ldInst, name)``.

        ``ldInst`` is ``""`` for a control block on an access-point-level LN,
        which is what the SCL would spell there too: such a node is in no
        logical device, so there is no ``inst`` to quote. No file in the
        reference corpus carries one -- 61850-6 puts control blocks on an
        ``LN0``, and an access-point LN is never one -- but the key must
        still be a triple rather than raise, because it is what every
        publisher/subscriber join in this package looks up by.
        """
        ld = self.logical_node.ldevice
        return (self.logical_node.ied.name, "" if ld is None else ld.inst,
                self.name)

    def __repr__(self):
        return f"<{self.kind} {self.key!r} datSet={self.dat_set!r}>"


class SettingControl:
    """The ``SettingControl`` of an LN0: how many setting groups, and which
    one is active."""

    __slots__ = ("num_of_sgs", "act_sg", "privates")

    def __init__(self, el):
        self.num_of_sgs = el.get("numOfSGs")
        self.act_sg = el.get("actSG")
        self.privates = privates_of(el)

    def __repr__(self):
        return f"<SettingControl numOfSGs={self.num_of_sgs!r}>"


class ExtRef:
    """One ``ExtRef``: an input this logical node takes from somewhere else.

    Every attribute is reported as the file spells it, and nothing is
    filtered. Two things make that the right policy rather than a lazy one:

    - An ExtRef with no publisher is a TEMPLATE, common in an SCD exported
      before every connection was made. Which inputs are still unbound is
      exactly what a commissioning tool is asking, so they are kept and
      flagged by :attr:`is_bound` rather than dropped.
    - ``serviceType`` is not a reliable discriminator. Measured across the
      reference corpus, one SCD omits it entirely on a whole class of ExtRef
      while another spells it ``GOOSE`` on the same thing. Filtering on it
      here would hide a different subset depending on which tool exported the
      file.
    """

    __slots__ = ("ied_name", "ld_inst", "prefix", "ln_class", "ln_inst",
                 "do_name", "da_name", "service_type", "src_ld_inst",
                 "src_prefix", "src_ln_class", "src_ln_inst", "src_cb_name",
                 "int_addr", "desc", "privates", "logical_node", "element")

    def __init__(self, el, logical_node):
        #: The live element -- see :attr:`FCDA.element`. The attributes below
        #: are read once, here, so a list obtained before an edit reports what
        #: the file said before it; the element itself is always current.
        self.element = el
        self.ied_name = el.get("iedName")
        self.ld_inst = el.get("ldInst")
        self.prefix = el.get("prefix")
        self.ln_class = el.get("lnClass")
        self.ln_inst = el.get("lnInst")
        self.do_name = el.get("doName")
        self.da_name = el.get("daName")
        self.service_type = el.get("serviceType")
        self.src_ld_inst = el.get("srcLDInst")
        self.src_prefix = el.get("srcPrefix")
        self.src_ln_class = el.get("srcLNClass")
        self.src_ln_inst = el.get("srcLNInst")
        self.src_cb_name = el.get("srcCBName")
        self.int_addr = el.get("intAddr")
        self.desc = el.get("desc")
        self.privates = privates_of(el)
        self.logical_node = logical_node

    @property
    def is_bound(self) -> bool:
        """Does this input name both a publisher and a control block?"""
        return bool(self.ied_name and self.src_cb_name)

    @property
    def source_key(self):
        """``(publisher, srcLDInst, srcCBName)``, or ``None`` when unbound.

        The same triple a control block and its address are keyed by, so a
        subscription resolves against its publisher by dictionary lookup.
        """
        if not self.is_bound:
            return None
        return (self.ied_name, self.src_ld_inst or "", self.src_cb_name)

    def __repr__(self):
        return f"<ExtRef source={self.source_key!r} intAddr={self.int_addr!r}>"


def data_sets_of(node) -> dict:
    return {ds.name: ds for ds in
            (DataSet(el, node) for el in children_local(node.element, "DataSet"))
            if ds.name}


def control_blocks_of(node) -> dict:
    out = {}
    for kind in CONTROL_BLOCK_TAGS:
        for el in children_local(node.element, kind):
            cb = ControlBlock(kind, el, node)
            if cb.name:
                out.setdefault(cb.name, cb)
    return out


def setting_control_of(node):
    el = next(children_local(node.element, "SettingControl"), None)
    return SettingControl(el) if el is not None else None


def ext_refs_of(node) -> list:
    out = []
    for inputs in children_local(node.element, "Inputs"):
        out.extend(ExtRef(el, node) for el in children_local(inputs, "ExtRef"))
    return out
