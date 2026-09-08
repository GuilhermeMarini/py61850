# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""The ``Communication`` section: where each IED and control block publishes.

This section is shallow by nature -- it names IEDs but contains none of them --
so every question here is answered without materialising a single instance
tree. An IED's IP address and a GOOSE control block's MAC/APPID/VLAN are the
two things a commissioning tool asks first, and neither needs the type pool.
"""

from __future__ import annotations

from .document import children_local, iter_local, privates_of


class Address:
    """An ``<Address>``: its ``<P type=...>`` parameters, by type.

    Lookup is case-insensitive because the type spelling is not stable across
    tools -- ``MAC-Address`` and ``MAC-ADDRESS`` are the same parameter.
    """

    __slots__ = ("params",)

    def __init__(self, el=None):
        self.params = {}
        if el is None:
            return
        for p in iter_local(el, "P"):
            ptype = (p.get("type") or "").upper()
            text = (p.text or "").strip()
            if ptype and text:
                self.params[ptype] = text

    def get(self, p_type, default=None):
        return self.params.get((p_type or "").upper(), default)

    @property
    def ip(self):
        return self.get("IP")

    @property
    def mac(self):
        return self.get("MAC-ADDRESS")

    @property
    def appid(self):
        return self.get("APPID")

    @property
    def vlan_id(self):
        return self.get("VLAN-ID")

    @property
    def vlan_priority(self):
        return self.get("VLAN-PRIORITY")

    def __repr__(self):
        return f"<Address {self.params!r}>"


class ControlBlockAddress:
    """One ``<GSE>`` or ``<SMV>``: the link-layer address of a control block.

    Identified by ``(ied_name, ld_inst, cb_name)`` -- the same triple the
    control block itself is identified by inside the IED, which is what lets a
    subscription be resolved against it.
    """

    __slots__ = ("kind", "ied_name", "ld_inst", "cb_name", "address",
                 "min_time", "max_time", "privates")

    def __init__(self, kind, ied_name, el):
        self.kind = kind
        self.ied_name = ied_name
        self.ld_inst = el.get("ldInst") or ""
        self.cb_name = el.get("cbName") or ""
        addr_el = next(children_local(el, "Address"), None)
        self.address = Address(addr_el)
        self.min_time = _text(next(children_local(el, "MinTime"), None))
        self.max_time = _text(next(children_local(el, "MaxTime"), None))
        self.privates = privates_of(el)

    @property
    def key(self) -> tuple:
        return (self.ied_name, self.ld_inst, self.cb_name)

    def __repr__(self):
        return f"<{self.kind} {self.key!r}>"


class ConnectedAP:
    """One ``<ConnectedAP>``: one access point of one IED, on one subnetwork.

    An IED may have several -- 41 access points across 30 IEDs in one
    reference SCD -- so this is never collapsed to one per IED.
    """

    __slots__ = ("ied_name", "ap_name", "address", "gses", "smvs", "privates")

    def __init__(self, el):
        self.ied_name = el.get("iedName") or el.get("iedname") or ""
        self.ap_name = el.get("apName") or ""
        self.address = Address(next(children_local(el, "Address"), None))
        self.gses = [ControlBlockAddress("GSE", self.ied_name, g)
                     for g in children_local(el, "GSE")]
        self.smvs = [ControlBlockAddress("SMV", self.ied_name, s)
                     for s in children_local(el, "SMV")]
        self.privates = privates_of(el)

    def __repr__(self):
        return f"<ConnectedAP {self.ied_name!r} ap={self.ap_name!r}>"


class SubNetwork:
    """One ``<SubNetwork>`` and the access points on it."""

    __slots__ = ("name", "type", "connected_aps", "privates")

    def __init__(self, el):
        self.name = el.get("name") or ""
        self.type = el.get("type")
        self.connected_aps = [ConnectedAP(ap)
                              for ap in children_local(el, "ConnectedAP")]
        self.privates = privates_of(el)

    def __repr__(self):
        return f"<SubNetwork {self.name!r} aps={len(self.connected_aps)}>"


class Communication:
    """The whole ``<Communication>`` section."""

    __slots__ = ("subnetworks",)

    def __init__(self, root):
        self.subnetworks = []
        # DIRECT CHILD of the root only. `<Communication>` is schema-valid
        # only there; a descendant scan would also match a same-named element
        # a vendor `Private` block happens to nest -- see `_ied_elements`'s
        # docstring in `document.py` for the shadowing this class of bug
        # already caused once, for `<IED>`.
        for section in children_local(root, "Communication"):
            self.subnetworks.extend(
                SubNetwork(sn) for sn in children_local(section, "SubNetwork"))

    def connected_aps(self) -> list:
        """Every access point, across every subnetwork, in document order."""
        return [ap for sn in self.subnetworks for ap in sn.connected_aps]

    def ip_by_ied(self) -> dict:
        """``{iedName: first IP found}``.

        First wins: an IED with several access points has several addresses,
        and picking one arbitrarily later is worse than picking the first
        consistently here. Access points with no IP are simply absent.
        """
        out: dict = {}
        for ap in self.connected_aps():
            if ap.ied_name and ap.ied_name not in out and ap.address.ip:
                out[ap.ied_name] = ap.address.ip
        return out

    def control_block_addresses(self) -> dict:
        """``{(ied, ld_inst, cb_name): ControlBlockAddress}`` for GSE and SMV.

        A block naming no control block is skipped: it identifies nothing, and
        keeping it would put a key ending in ``""`` into a map every caller
        looks up by control-block name.
        """
        out: dict = {}
        for ap in self.connected_aps():
            for cb in list(ap.gses) + list(ap.smvs):
                if cb.cb_name:
                    out.setdefault(cb.key, cb)
        return out

    def __repr__(self):
        return f"<Communication subnetworks={len(self.subnetworks)}>"


def _text(el):
    return None if el is None else (el.text or "").strip() or None
