# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""The `Communication` half of a control block: `GSE` and `SMV` addresses.

    from py61850.scl import SclDocument, change_gse_or_smv_address

    doc = SclDocument.parse("station.scd")
    address = control_block_gse_or_smv(doc, block)
    doc.apply_edit(change_gse_or_smv_address(doc, address, mac="01-0C-CD-01-00-2A"))

**This module is A11's and A12 started it**, which is worth saying in the
first paragraph rather than in a commit body. `createSampledValueControl`
writes a `SampledValueControl` into an `LN0` *and* an `SMV` into the
`ConnectedAP` that publishes it, so A12 either produced that `SMV` itself or
delegated. It delegated, and the module that owns the address is where the
expansion went -- the same choice A9 made when it needed to remove a
`DataSet` and handed the expansion to A10 (Q25). Two functions that create an
`SMV` differently is the drift Q23 was written about.

**Five capabilities**, where the reference exports four and keeps the fifth
internal: :func:`create_gse`, :func:`create_smv`, :func:`change_gse_content`,
:func:`change_smv_content` and the engine the last two share,
:func:`change_gse_or_smv_address`. The engine is public HERE because
:func:`~py61850.scl.control_block_gse_or_smv` hands back a `GSE` **or** an
`SMV` and does not say which, so the caller that has just found an address
frequently does not know its tag. The two wrappers are not aliases of it:
each refuses the other's tag, and only the `GSE` one carries the timings.

**`communication.py` is the READ model** -- :class:`~py61850.scl.Address`,
:class:`~py61850.scl.ConnectedAP` and the rest, built once and cached. This is
the edit layer, it works in live elements, and it is separate for the reason
:meth:`~py61850.scl.SclDocument.apply_edit` gives: the model is not
invalidated by an edit, so a capability that has to return something an edit
can act on cannot read it from there. **A11 is the fifth instance of that
hazard and the sharpest so far** (Q14): A9's `remove_control_block` reaches
into the `Communication` section as a side effect, but every function here
edits nothing else, so a document whose `.communication` has been read is
stale after any of them. A13 owns the fix, with Stage 3's access pattern
visible; until then, read an edited document through a fresh `parse` or
through the tree.

## What an address is made of, and the two things the corpus does not agree on

A `GSE` or `SMV` carries ``ldInst`` and ``cbName`` -- the control block it
addresses -- and an `Address` holding `P` elements typed `MAC-Address`,
`APPID`, `VLAN-ID` and `VLAN-PRIORITY`. A `GSE` carries `MinTime` and
`MaxTime` after it; an `SMV` has nowhere to put them.

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

**A `P` is found by type, case-insensitively, and keeps its own spelling.**
:class:`~py61850.scl.Address` matches that way because the spelling is not
stable across tools, and an edit layer that matched exactly would answer a
file writing `MAC-ADDRESS` by INSERTING a second `P` for the same parameter --
two elements the read model would then collapse into one. All 648 `P`
elements in the corpus use one spelling each, so nothing here exercises it;
it costs one ``.upper()`` to not have the defect.

## `xsi:type` is what turns the value patterns on, and A12 had that wrong

A12 declined to write it and gave two reasons. **The corpus reason stands and
the schema reason does not**, so it is restated here rather than left where a
reader would believe it.

`tP`'s content is `tPAddr` -- ``xs:normalizedString`` with ``minLength="1"``
and **no pattern at all**. Every value constraint on these four parameters
lives in a DERIVED type, and XSD 1.0 has no conditional type assignment, so
``xsi:type`` is the only thing that reaches them:

===================  ================================
``xsi:type``         what it then requires of the text
===================  ================================
``tP_MAC-Address``   ``[0-9A-F]{2}`` six times, hyphen-separated
``tP_APPID``         ``[0-9A-F]{4}``
``tP_VLAN-ID``       ``[0-9A-F]{3}``
``tP_VLAN-PRIORITY`` ``[0-7]``
===================  ================================

So ``<P type="APPID">not hex at all</P>`` is schema-valid without it. It is a
validation switch, which is why the reference carries an explicit
``instType`` option for it, and A12's "information a validator already
infers" was wrong. Its second claim was measured and is also wrong: setting
``xsi:type`` on a document that declares no such prefix appends one
``xmlns:xsi`` declaration to the root, after the attributes
``SclDocument.to_bytes`` reorders (which is what that function already does
with an attribute the recorded layout does not name), and the inverse edit
removes attribute and declaration together -- the file is byte-identical
after an undo. Q16 is not in the way.

**What is true is the corpus reason**, and it is sharper than A12 could see:
``xsi:type`` is a per-VENDOR habit, not a per-file one. sel.scd writes it on 0
of its 140 `P` elements and declares no ``xsi`` prefix at all; siemens.scd
writes it on 48 of 56 and mixed.scd on 444 of 452, and the 8 missing in each
are **four `GSE` elements belonging to the SEL relays inside those Siemens
exports** -- `TR01_2414`, `TR01_2440`, `TR1_2414`, `TR1_2440`. Those same four
also keep SEL's `P` order and SEL's 4 ms / 1000 ms timing, so the tool that
wrote the IED decided all three, and one file carries both conventions side
by side.

**Hence ``inst_type``, three-valued**, on every function here including the
creates -- which is one option more than the reference gives `createGSE` and
`createSMV`, and it is there so the five cannot disagree:

- ``None``, the default: a `P` this call INSERTS carries ``xsi:type`` if the
  `P` elements already in its `Address` do. All 162 corpus addresses are
  uniform -- never partly typed -- so this reproduces the file's own
  convention without the caller having to know it. An `Address` being created
  has no siblings to follow, so it gets none, which is exactly what
  :func:`create_smv` shipped in A12 and why its behaviour is unchanged.
- ``True``: every `P` the call NAMES carries it, set on one that lacks it.
- ``False``: no `P` this call inserts carries it, and an existing one is left
  alone -- an option that is not asked about is never taken away.

Turning it on is safe on this corpus: all 648 values already match the
pattern their derived type would impose.

## A value is CHANGED, not replaced -- the divergence this phase argues

The reference's three change functions return ``(Insert | Remove)[]``, and
`open-scd-core` has a `SetTextContent` they do not use, so expressing a value
change structurally is a choice there rather than an absence. **Ours uses
:class:`~py61850.scl.SetTextContent`** and returns
``(SetTextContent | Insert)[]``, inserting only a `P` the `Address` does not
yet carry.

The deciding fact is one line of A7's table: ``"Address": (("P",),)`` -- one
slot, so :func:`~py61850.scl.reference_for` answers "append" for every `P`
there is. A `Remove` and an `Insert` of one `P` therefore cannot put it back
where it was: on the 87 corpus addresses written `MAC-Address, VLAN-ID,
VLAN-PRIORITY, APPID`, correcting one character of the MAC would re-emit them
as `VLAN-ID, VLAN-PRIORITY, APPID, MAC-Address`. Rebuilding the whole
`Address` instead would flatten 126 of 162 addresses into this module's
option order, which is the vendor convention A12 declined to invent in the
first place. Whether the reference replaces the `Address` or the `P` could not
be established -- its declaration admits both and its doc comment says
neither, the same epistemic state Q19 and Q21 record.

Ours is better on the measure this project added and OpenSCD does not have:
the bytes of an edited document. Q12 chose `SetTextContent`'s semantics for
the same reason one level down. The cost is that the return type carries an
edit kind the published signature does not list. See Q30.

## An absent option means "leave it alone"

Every value is optional on the change functions, and an option that is not
given changes nothing -- not the `P`, not the `MinTime`, not the `MaxTime`.
There is no way to DELETE through them; a caller that wants a `MaxTime` gone
applies a :class:`~py61850.scl.Remove` itself. The reference's return type
admits `Remove` and its documentation does not say what absence means; the
reading taken here is that `Remove` is there for structural replacement, and
it is the only reading under which a `Communication` section cannot silently
lose its GOOSE timing to a caller who passed one option and not the other.

A call that asks for nothing, or for values the element already carries,
returns ``[]``. An empty list is an edit that does nothing, which is what
:meth:`~py61850.scl.SclDocument.apply_edit` should be handed for a no-op.

## `MinTime` and `MaxTime`

`tGSE` puts both after the `Address`, both ``minOccurs="0"``, both typed
`tDurationInMilliSec` -- whose ``unit`` is optional and ``fixed="s"`` and
whose ``multiplier`` is optional and ``fixed="m"``. So ``<MinTime>4</MinTime>``
already means 4 ms and the two attributes carry no information.

**They are written anyway**, on an element this module creates: 146 of the
corpus's 146 `GSE` elements write them, and matching what every vendor tool
re-exports is worth two attributes that a schema-aware reader ignores. That
points the opposite way from ``xsi:type`` above, deliberately -- there the
attribute carries information the file otherwise loses, here it does not, and
the corpus is what breaks the tie in both directions. An element that already
exists keeps whatever attributes it has: a change sets the text and nothing
else.

**A `GSE` created with neither time carries neither element.** Both are
optional, it is the same argument the optional `Address` rests on, and
defaults are A17's. It is the one shape here the corpus never contains.
"""

from __future__ import annotations

from typing import List, Optional
from xml.etree import ElementTree as ET

from .document import strip_ns
from .edit import EditRejected, Insert, SetAttributes, SetTextContent
from .extref import _qualify, _same
from .ordering import reference_for

#: The `P` types an address may carry, in the order the creates write them.
#: See the module docstring: the schema fixes no order and the corpus writes
#: three, so this is the reference's own option order and nothing more.
P_TYPES = ("MAC-Address", "APPID", "VLAN-ID", "VLAN-PRIORITY")

#: ``xsi:type``, as `ElementTree` stores a namespaced attribute name. The
#: value is an unprefixed QName, which resolves in the document's DEFAULT
#: namespace -- the SCL one, where the derived types are declared -- so the
#: bare ``tP_...`` spelling the corpus writes is the correct one.
XSI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"

#: The derived type each `P` type names. Section 3 of the module docstring
#: says what it turns on and why omitting it is not free.
INST_TYPES = {p_type: f"tP_{p_type}" for p_type in P_TYPES}

# `tDurationInMilliSec` declares both optional and fixed, so they carry no
# information -- the module docstring argues why they are written anyway.
_TIME_ATTRIBUTES = (("unit", "s"), ("multiplier", "m"))


def _is(element, *local_names) -> bool:
    return (isinstance(element, ET.Element)
            and strip_ns(element.tag) in local_names)


def _describe(element) -> str:
    """What to call ``element`` in a refusal -- its tag, or its type."""
    return (strip_ns(element.tag) if isinstance(element, ET.Element)
            else type(element).__name__)


def _address_of(element) -> Optional[ET.Element]:
    """``element``'s `Address` child, or ``None``. First wins: `tControlBlock`
    declares at most one and no corpus file writes a second."""
    return next((child for child in element
                 if strip_ns(child.tag) == "Address"), None)


def _p_element(address, p_type) -> Optional[ET.Element]:
    """The `P` of this type in ``address``, or ``None``.

    Matched case-insensitively, as :class:`~py61850.scl.Address` matches, and
    for its reason: see the module docstring. The element that comes back
    keeps its own spelling of the type -- nothing here rewrites it.
    """
    wanted = (p_type or "").upper()
    for child in address:
        if strip_ns(child.tag) == "P" and (child.get("type") or "").upper() \
                == wanted:
            return child
    return None


def _p_new(doc, p_type, value, inst_type: bool) -> ET.Element:
    """A `<P type=...>` carrying ``value``, and ``xsi:type`` if asked."""
    element = ET.Element(_qualify(doc, "P"))
    element.set("type", p_type)
    if inst_type:
        element.set(XSI_TYPE, INST_TYPES[p_type])
    element.text = value
    return element


def _follows(address, inst_type) -> bool:
    """Whether a `P` written into ``address`` should carry ``xsi:type``.

    ``inst_type=None`` follows the `P` elements already there -- all 162
    corpus addresses are uniform, so the file's own convention is reproduced
    without the caller knowing it. ``address=None`` is an address being
    created, which has nothing to follow.
    """
    if inst_type is not None:
        return bool(inst_type)
    if address is None:
        return False
    return any(XSI_TYPE in child.attrib for child in address
               if strip_ns(child.tag) == "P")


def _address_element(doc, mac, app_id, vlan_id, vlan_priority,
                     inst_type=None):
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
    write_inst_type = _follows(None, inst_type)
    address = ET.Element(_qualify(doc, "Address"))
    for p_type in P_TYPES:
        value = values[p_type]
        if value is None:
            continue
        address.append(_p_new(doc, p_type, value, write_inst_type))
    return address


def _time_element(doc, local_name, value) -> ET.Element:
    element = ET.Element(_qualify(doc, local_name))
    for attribute, fixed in _TIME_ATTRIBUTES:
        element.set(attribute, fixed)
    element.text = value
    return element


def _reject_bad_parent(connected_ap, tag) -> None:
    if not _is(connected_ap, "ConnectedAP"):
        raise EditRejected(
            f"{_describe(connected_ap)} is not a ConnectedAP; "
            f"a{'n' if tag == 'SMV' else ''} {tag} goes in one")


def _reject_bad_key(tag, ld_inst, cb_name) -> None:
    if not ld_inst or not cb_name:
        raise EditRejected(
            f"a{'n' if tag == 'SMV' else ''} {tag} is keyed by ldInst and "
            f"cbName and both are required by the schema; got "
            f"ldInst={ld_inst!r} cbName={cb_name!r}")


def _reject_duplicate(connected_ap, tag, ld_inst, cb_name) -> None:
    clash = next((existing for existing in connected_ap
                  if strip_ns(existing.tag) == tag
                  and _same(existing.get("ldInst"), ld_inst)
                  and _same(existing.get("cbName"), cb_name)), None)
    if clash is not None:
        raise EditRejected(
            f"this ConnectedAP already holds a{'n' if tag == 'SMV' else ''} "
            f"{tag} addressing {ld_inst}/{cb_name}")


# -- creating ---------------------------------------------------------------

def create_gse(doc, connected_ap, ld_inst, cb_name, mac=None, app_id=None,
               vlan_id=None, vlan_priority=None, min_time=None, max_time=None,
               inst_type=None) -> List:
    """The edit that adds a `GSE` addressing one `GSEControl`.

    The `SMV` of :func:`create_smv` with two more children. ``connected_ap``
    is the `ConnectedAP` of the IED that publishes the GOOSE; ``ld_inst`` and
    ``cb_name`` are the `LDevice@inst` and the control block's `name`, which
    is the pair every `GSE` and `SMV` in SCL is keyed by and the pair
    :func:`~py61850.scl.control_block_gse_or_smv` looks one up with.

    ``min_time`` and ``max_time`` are the GOOSE retransmission bounds, in
    milliseconds, as bare strings -- the unit is in the type rather than in
    the value. Each is written as its own element carrying ``unit="s"
    multiplier="m"``, which is what all 146 corpus `GSE` elements do and what
    the module docstring argues for. **Neither is written when it is not
    given**, and a `GSE` carrying no times at all is valid SCL.

    The four address values are each optional, and a `GSE` with none of them
    carries no `Address` -- the shape that waits for A17's generators.
    ``inst_type`` is the module docstring's three-valued ``xsi:type`` switch.

    Returns a list holding one :class:`~py61850.scl.Insert`, placed by A7's
    :func:`~py61850.scl.reference_for`, which is Q23's rule and A10's shape.

    Raises :class:`~py61850.scl.EditRejected` if ``connected_ap`` is not a
    `ConnectedAP`, if either key is empty, or if that access point already
    addresses this control block -- the pair is what identifies the address,
    so a second one is not a variant but a contradiction.
    """
    _reject_bad_parent(connected_ap, "GSE")
    _reject_bad_key("GSE", ld_inst, cb_name)
    _reject_duplicate(connected_ap, "GSE", ld_inst, cb_name)

    node = ET.Element(_qualify(doc, "GSE"))
    node.set("ldInst", ld_inst)
    node.set("cbName", cb_name)
    address = _address_element(doc, mac, app_id, vlan_id, vlan_priority,
                               inst_type)
    if address is not None:
        node.append(address)
    for local_name, value in (("MinTime", min_time), ("MaxTime", max_time)):
        if value is not None:
            node.append(_time_element(doc, local_name, value))
    return [Insert(connected_ap, node, reference_for(connected_ap, node.tag))]


def create_smv(doc, connected_ap, ld_inst, cb_name, mac=None, app_id=None,
               vlan_id=None, vlan_priority=None, inst_type=None) -> List:
    """The edit that adds an `SMV` addressing one `SampledValueControl`.

    ``connected_ap`` is the `ConnectedAP` of the IED that publishes the
    stream; ``ld_inst`` and ``cb_name`` are the `LDevice@inst` and the control
    block's `name`, which is the pair every `GSE` and `SMV` in SCL is keyed by
    and the pair :func:`~py61850.scl.control_block_gse_or_smv` looks one up
    with.

    The four address values are each optional, and an `SMV` with none of them
    carries no `Address` at all -- valid SCL, and the shape that waits for
    A17's generators. The module docstring argues both that and the `P` order.
    ``inst_type`` is its three-valued ``xsi:type`` switch, and its default
    writes none, which is the shape this function shipped with in A12.

    `tSMV` extends `tControlBlock` and adds nothing, so there are no times
    here: `MinTime` and `MaxTime` are `tGSE`'s, and :func:`create_gse` is
    where they are written.

    Returns a list holding one :class:`~py61850.scl.Insert`, placed by A7's
    :func:`~py61850.scl.reference_for`, which is Q23's rule and A10's shape.

    Raises :class:`~py61850.scl.EditRejected` if ``connected_ap`` is not a
    `ConnectedAP`, if either key is empty, or if that access point already
    addresses this control block -- the pair is what identifies the address,
    so a second one is not a variant but a contradiction.
    """
    _reject_bad_parent(connected_ap, "SMV")
    _reject_bad_key("SMV", ld_inst, cb_name)
    _reject_duplicate(connected_ap, "SMV", ld_inst, cb_name)

    node = ET.Element(_qualify(doc, "SMV"))
    node.set("ldInst", ld_inst)
    node.set("cbName", cb_name)
    address = _address_element(doc, mac, app_id, vlan_id, vlan_priority,
                               inst_type)
    if address is not None:
        node.append(address)
    return [Insert(connected_ap, node, reference_for(connected_ap, node.tag))]


# -- changing ---------------------------------------------------------------

def change_gse_or_smv_address(doc, gse_or_smv, mac=None, app_id=None,
                              vlan_id=None, vlan_priority=None,
                              inst_type=None) -> List:
    """The edits that give ``gse_or_smv`` these address parameters.

    The engine :func:`change_gse_content` and :func:`change_smv_content`
    share, and public here although the reference keeps it internal, because
    :func:`~py61850.scl.control_block_gse_or_smv` hands back a `GSE` **or** an
    `SMV` without saying which and this is what that caller wants next.

    **A value that is not given is not touched**, and nothing here deletes: a
    caller that wants a `P` gone applies a :class:`~py61850.scl.Remove`
    itself. A value the element already carries produces no edit either, so a
    call that changes nothing returns ``[]``.

    A `P` that is already there has its TEXT set, through
    :class:`~py61850.scl.SetTextContent`; only a type the `Address` does not
    yet carry is inserted, and it is appended, because `tAddress` is one
    repeated `P` and A7's table gives it one slot. The module docstring argues
    that against the reference's structural replacement, and Q30 records it.

    An element with no `Address` at all gets one, holding every value given,
    placed by :func:`~py61850.scl.reference_for`.

    ``inst_type`` is the module docstring's three-valued ``xsi:type`` switch:
    ``None`` follows the `P` elements already in the `Address`, ``True`` sets
    it on every `P` this call names, ``False`` writes none and removes none.

    Raises :class:`~py61850.scl.EditRejected` if ``gse_or_smv`` is neither a
    `GSE` nor an `SMV`.
    """
    if not _is(gse_or_smv, "GSE", "SMV"):
        raise EditRejected(
            f"{_describe(gse_or_smv)} is neither a GSE nor an SMV; an address "
            f"belongs to one of them")

    values = {
        "MAC-Address": mac,
        "APPID": app_id,
        "VLAN-ID": vlan_id,
        "VLAN-PRIORITY": vlan_priority,
    }
    if all(value is None for value in values.values()):
        return []

    address = _address_of(gse_or_smv)
    if address is None:
        address = _address_element(doc, mac, app_id, vlan_id, vlan_priority,
                                   inst_type)
        return [Insert(gse_or_smv, address,
                       reference_for(gse_or_smv, address.tag))]

    write_inst_type = _follows(address, inst_type)
    edits: List = []
    for p_type in P_TYPES:
        value = values[p_type]
        if value is None:
            continue
        existing = _p_element(address, p_type)
        if existing is None:
            node = _p_new(doc, p_type, value, write_inst_type)
            edits.append(Insert(address, node,
                                reference_for(address, node.tag)))
            continue
        if (existing.text or "") != value:
            edits.append(SetTextContent(existing, value))
        if inst_type is True and existing.get(XSI_TYPE) != INST_TYPES[p_type]:
            edits.append(
                SetAttributes(existing, {XSI_TYPE: INST_TYPES[p_type]}))
    return edits


def change_gse_content(doc, gse, mac=None, app_id=None, vlan_id=None,
                       vlan_priority=None, min_time=None, max_time=None,
                       inst_type=None) -> List:
    """The edits that change a `GSE`'s address and its GOOSE timing.

    :func:`change_gse_or_smv_address` plus `MinTime` and `MaxTime`, which are
    `tGSE`'s alone. Every rule there holds here: an option not given changes
    nothing, nothing is deleted, and a call that changes nothing returns
    ``[]``.

    A time that is already written has its text set and keeps whatever
    attributes it has; one that is missing is inserted carrying ``unit="s"
    multiplier="m"``, placed by :func:`~py61850.scl.reference_for` -- `tGSE`
    orders them `Address`, `MinTime`, `MaxTime`, and a `MaxTime` appended
    ahead of a `MinTime` loads here and fails in DIGSI.

    Raises :class:`~py61850.scl.EditRejected` if ``gse`` is not a `GSE` --
    including for an `SMV`, which has nowhere to put the times.
    """
    if not _is(gse, "GSE"):
        raise EditRejected(
            f"{_describe(gse)} is not a GSE; MinTime and MaxTime are tGSE's "
            f"and an SMV has nowhere to put them")

    edits = change_gse_or_smv_address(doc, gse, mac, app_id, vlan_id,
                                      vlan_priority, inst_type)
    for local_name, value in (("MinTime", min_time), ("MaxTime", max_time)):
        if value is None:
            continue
        existing = next((child for child in gse
                         if strip_ns(child.tag) == local_name), None)
        if existing is None:
            node = _time_element(doc, local_name, value)
            edits.append(Insert(gse, node, reference_for(gse, node.tag)))
        elif (existing.text or "") != value:
            edits.append(SetTextContent(existing, value))
    return edits


def change_smv_content(doc, smv, mac=None, app_id=None, vlan_id=None,
                       vlan_priority=None, inst_type=None) -> List:
    """The edits that change an `SMV`'s address.

    :func:`change_gse_or_smv_address` with the tag pinned. `tSMV` extends
    `tControlBlock` and adds nothing, so an `SMV`'s whole content IS its
    address and there is no timing half to mirror :func:`change_gse_content`.

    It is not an alias: it refuses a `GSE`, as :func:`change_gse_content`
    refuses an `SMV`. A caller holding an element whose tag it has not
    inspected -- which is what
    :func:`~py61850.scl.control_block_gse_or_smv` returns -- wants the engine
    rather than either wrapper.

    Raises :class:`~py61850.scl.EditRejected` if ``smv`` is not an `SMV`.
    """
    if not _is(smv, "SMV"):
        raise EditRejected(
            f"{_describe(smv)} is not an SMV; change_gse_content takes a GSE "
            f"and change_gse_or_smv_address takes either")
    return change_gse_or_smv_address(doc, smv, mac, app_id, vlan_id,
                                     vlan_priority, inst_type)


# -- which ConnectedAP ------------------------------------------------------

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
