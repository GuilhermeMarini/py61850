# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""The `Communication` half of a control block: `GSE` and `SMV` addresses.

    from py61850.scl import SclDocument, create_smv

    doc = SclDocument.parse("station.scd")
    doc.apply_edit(create_smv(doc, connected_ap, "MU01", "SVPB01"))

**This module belongs to A11 and A12 started it**, which is worth saying in
the first paragraph rather than in a commit body. `createSampledValueControl`
writes a `SampledValueControl` into an `LN0` *and* an `SMV` into the
`ConnectedAP` that publishes it, so A12 either produced that `SMV` itself or
delegated. It delegates, and the module that owns the address is where the
expansion went -- the same choice A9 made when it needed to remove a
`DataSet` and handed the expansion to A10 (Q25). Two functions that create an
`SMV` differently is the drift Q23 was written about.

So this file holds one function today. A11 fills in `create_gse`,
`change_gse_content`, `change_smv_content` and `change_gse_or_smv_address`
beside it, and nothing about `create_smv` has to move when it does.

**`communication.py` is the READ model** -- :class:`~py61850.scl.Address`,
:class:`~py61850.scl.ConnectedAP` and the rest, built once and cached. This is
the edit layer, it works in live elements, and it is separate for the reason
:meth:`~py61850.scl.SclDocument.apply_edit` gives: the model is not
invalidated by an edit, so a capability that has to return something an edit
can act on cannot read it from there.

## What an address is made of, and the two things the corpus does not agree on

A `GSE` or `SMV` carries ``ldInst`` and ``cbName`` -- the control block it
addresses -- and an `Address` holding `P` elements typed `MAC-Address`,
`APPID`, `VLAN-ID` and `VLAN-PRIORITY`.

**`Address` is optional.** `tControlBlock` declares it ``minOccurs="0"``, so
an `SMV` naming its control block and carrying no address at all is valid
SCL. That is what :func:`create_smv` writes when the caller supplies no
address data, and it is the shape that matters: allocating a MAC and an APPID
is A17's `macAddressGenerator` and `appIdGenerator`, and a `create` that
invented them now and changed them at A17 would be worse than one that leaves
the element ready for them. Same argument as A10's required `name` (Q27).

**The `P` order is the reference's option order**, because nothing else fixes
it. `tAddress` is ``<xs:sequence>`` over a single repeated `P`, so the schema
imposes no order among them at all, and the corpus writes three different ones
across its 162 addresses -- `MAC-Address, VLAN-ID, VLAN-PRIORITY, APPID` 87
times, `MAC-Address, APPID, VLAN-PRIORITY, VLAN-ID` 39, `VLAN-ID,
VLAN-PRIORITY, MAC-Address, APPID` 36. With no convention to follow, this
writes them in the order the reference declares its own options: mac, appID,
VLAN id, VLAN priority.

**`xsi:type` is not written**, and that is deliberate rather than an
omission. 492 of the corpus's 648 `P` elements carry
``xsi:type="tP_MAC-Address"`` and 156 carry none -- 140 of those being every
`P` in `sel.scd` -- so it is plainly optional. Writing it would mean the
`XMLSchema-instance` prefix has to be declared, and `sel.scd`'s root declares
no such namespace: adding one means editing the root element's attributes,
which :meth:`~py61850.scl.SclDocument.to_bytes` re-orders from the file and
Q16 records as not editable. An attribute that would break the round trip on
a third of the corpus to add information a validator already infers is not
worth writing.
"""

from __future__ import annotations

from typing import List, Optional
from xml.etree import ElementTree as ET

from .document import strip_ns
from .edit import EditRejected, Insert
from .extref import _qualify, _same
from .ordering import reference_for

#: The `P` types an address may carry, in the order :func:`create_smv` writes
#: them. See the module docstring: the schema fixes no order and the corpus
#: writes four, so this is the reference's own option order and nothing more.
P_TYPES = ("MAC-Address", "APPID", "VLAN-ID", "VLAN-PRIORITY")


def _address_element(doc, mac, app_id, vlan_id, vlan_priority):
    """An `<Address>` holding a `P` for each value given, or ``None``.

    ``None`` when every value is absent -- `tControlBlock` makes `Address`
    optional and `tAddress` requires at least one `P`, so an empty one is the
    single shape that is invalid.
    """
    values = {
        "MAC-Address": mac,
        "APPID": app_id,
        "VLAN-ID": vlan_id,
        "VLAN-PRIORITY": vlan_priority,
    }
    if all(value is None for value in values.values()):
        return None
    address = ET.Element(_qualify(doc, "Address"))
    for p_type in P_TYPES:
        value = values[p_type]
        if value is None:
            continue
        element = ET.SubElement(address, _qualify(doc, "P"))
        element.set("type", p_type)
        element.text = value
    return address


def create_smv(doc, connected_ap, ld_inst, cb_name, mac=None, app_id=None,
               vlan_id=None, vlan_priority=None) -> List:
    """The edit that adds an `SMV` addressing one `SampledValueControl`.

    ``connected_ap`` is the `ConnectedAP` of the IED that publishes the
    stream; ``ld_inst`` and ``cb_name`` are the `LDevice@inst` and the control
    block's `name`, which is the pair every `GSE` and `SMV` in SCL is keyed by
    and the pair :func:`~py61850.scl.control_block_gse_or_smv` looks one up
    with.

    The four address values are each optional, and an `SMV` with none of them
    carries no `Address` at all -- valid SCL, and the shape that waits for
    A17's generators. The module docstring argues both that and the `P` order.

    Returns a list holding one :class:`~py61850.scl.Insert`, placed by A7's
    :func:`~py61850.scl.reference_for`, which is Q23's rule and A10's shape.

    Raises :class:`~py61850.scl.EditRejected` if ``connected_ap`` is not a
    `ConnectedAP`, if either key is empty, or if that access point already
    addresses this control block -- the pair is what identifies the address,
    so a second one is not a variant but a contradiction.
    """
    if not isinstance(connected_ap, ET.Element) or \
            strip_ns(connected_ap.tag) != "ConnectedAP":
        name = (strip_ns(connected_ap.tag)
                if isinstance(connected_ap, ET.Element)
                else type(connected_ap).__name__)
        raise EditRejected(f"{name} is not a ConnectedAP; an SMV goes in one")
    if not ld_inst or not cb_name:
        raise EditRejected(
            "an SMV is keyed by ldInst and cbName and both are required by "
            f"the schema; got ldInst={ld_inst!r} cbName={cb_name!r}")

    clash = next((existing for existing in connected_ap
                  if strip_ns(existing.tag) == "SMV"
                  and _same(existing.get("ldInst"), ld_inst)
                  and _same(existing.get("cbName"), cb_name)), None)
    if clash is not None:
        raise EditRejected(
            f"this ConnectedAP already holds an SMV addressing "
            f"{ld_inst}/{cb_name}")

    node = ET.Element(_qualify(doc, "SMV"))
    node.set("ldInst", ld_inst)
    node.set("cbName", cb_name)
    address = _address_element(doc, mac, app_id, vlan_id, vlan_priority)
    if address is not None:
        node.append(address)
    return [Insert(connected_ap, node, reference_for(connected_ap, node.tag))]


def connected_ap_for(doc, ied_name, ap_name=None) -> Optional[ET.Element]:
    """The `ConnectedAP` that publishes for ``ied_name``, or ``None``.

    ``ap_name`` names one access point. Without it the default is the
    reference's own -- *"the AccessPoint holding the Server element"* -- which
    is not the same thing as the first `ConnectedAP` in the file. 13 corpus
    IEDs carry more than one `AccessPoint` and every one of them puts a
    `Server` in exactly one, so the rule resolves to a name every time.

    **It does not always resolve to a connection, and the corpus says so.**
    `sel.scd`'s `RTAC_1` holds its `Server` on access point `S1` and has ten
    `ConnectedAP` elements, named `Eth_01` to `Eth_10` -- not one of them is
    `S1`. So on that IED the documented default names an access point the
    `Communication` section does not address at all, and requiring the match
    would mean no `SMV` could ever be written for the one IED in the corpus
    with ten connections. The IED's first `ConnectedAP` is the fallback, used
    when the `Server`'s access point has no `ConnectedAP` and when no access
    point holds a `Server`.

    **It resolves by name rather than by walking the IED**, because a
    `ConnectedAP` lives in the `Communication` section and an `AccessPoint`
    lives in the `IED` section; the two halves of the file are joined by
    ``iedName``/``apName`` and by nothing else.

    ``None`` is an ordinary answer: an IED that is not on a subnetwork has no
    address at all, and
    :func:`~py61850.scl.create_sampled_value_control` writes the control block
    anyway, as the reference's *"and when possible `SMV`"* does.
    """
    if not ied_name:
        return None
    if ap_name is None:
        ap_name = _server_access_point(doc, ied_name)

    fallback = None
    for element in doc.root.iter():
        if strip_ns(element.tag) != "ConnectedAP":
            continue
        if not _same(element.get("iedName"), ied_name):
            continue
        if ap_name is None:
            return element
        if _same(element.get("apName"), ap_name):
            return element
        if fallback is None:
            fallback = element
    return fallback


def _server_access_point(doc, ied_name) -> Optional[str]:
    """The name of ``ied_name``'s `AccessPoint` that holds a `Server`."""
    for ied in doc.root.iter():
        if strip_ns(ied.tag) != "IED" or not _same(ied.get("name"), ied_name):
            continue
        for access_point in ied:
            if strip_ns(access_point.tag) != "AccessPoint":
                continue
            if any(strip_ns(child.tag) == "Server" for child in access_point):
                return access_point.get("name")
        return None
    return None
