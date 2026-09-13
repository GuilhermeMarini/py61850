# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""Sampled-value control blocks, and the `SMV` address that goes with them.

    from py61850.scl import SclDocument, create_sampled_value_control

    doc = SclDocument.parse("station.scd")
    edits = create_sampled_value_control(doc, ln0, "SVPB05", dat_set="PhsMeas3")
    doc.apply_edit(edits)     # the control block AND its Communication entry

Three capabilities: a guard, a creation and an edit check. The shapes are the
ones Q23 settled and A10 followed.

## A creation that writes in two sections at once

This is the first `create` in the library that produces **more than one
element**, and the two are in different halves of the file: a
`SampledValueControl` in an `LN0`, and an `SMV` in the `ConnectedAP` that
publishes it. The reference does the same -- `createSampledValueControl`
returns `Insert[]` rather than the single `Insert` its `createDataSet`
returns, and its doc says *"and when possible `SMV` to connected
`ConnectedAP`"*.

**"When possible" is not a refusal.** An IED that is on no subnetwork has no
`ConnectedAP`, and the control block is still written -- one `Insert` instead
of two. A sampled-value stream with no link-layer address is incomplete, but
it is valid SCL and it is what an engineer configuring the IED before the
network has.

**The `SMV` is not built here.** It comes from
:func:`~py61850.scl.create_smv`, in :mod:`py61850.scl.address`, which is
A11's module. A9 did the same thing when it had to remove a `DataSet` and
handed the expansion to A10 (Q25): two functions that create an `SMV`
differently is the drift Q23 exists to stop, and the module that owns the
address is where the one definition belongs.

## What the schema requires, and what the 16 in the corpus carry

`mixed.scd` is the only file in the corpus with a `SampledValueControl` at
all, and its 16 are identical in shape: `name`, `smvID`, `datSet`, `confRev`,
`multicast`, `smpRate`, `nofASDU`, `smpMod`, and a `SmvOpts` carrying six
attributes. 16 `SMV` elements address them 1:1, which is the relation Q21
rests on.

- **`smvID`, `smpRate` and `nofASDU` are `use="required"`**, so all three are
  written. The reference's defaults are taken for the last two -- `"80"` and
  `"1"` -- and `multicast` defaults to `"true"`, which is its schema default
  as well.
- **`SmvOpts` is a REQUIRED child**, as `OptFields` is on a report, and is
  written whether or not the caller names a field in it.
- **`confRev` is `use="optional"` here**, unlike on a `ReportControl` where it
  is required -- `tSampledValueControl` inherits it from
  `tControlWithIEDName`. It is still written by default, at ``"1"``, because a
  subscriber that cannot see a revision cannot notice one changing; passing
  ``conf_rev=None`` leaves it off.

## `smvID`, and the one capability in A9 that this phase confirms

When the caller names no `smvID`, the default is A9's
:func:`~py61850.scl.path_id` -- the reference documents its own default as
*"IED.name/LDevice.inst/LLN0/SampledValueControl.name"*, which is exactly what
that function builds.

**Q20 recorded that shape as an inference no file could confirm, and A12
confirms it.** Three of `siemens.scd`'s 14 `GSEControl@appID` values are
path-shaped, and `path_id` reproduces three of their four segments exactly:

    appID    QPC2_TR1_UPC4/CTRL/LLN0/Control_DataSet
    path_id  QPC2_TR1_AL14/CTRL/LLN0/Control_DataSet

Four segments, slashes throughout, `LLN0` third, and the last segment is the
control block's own name -- the block is called `Control_DataSet` while its
`datSet` is `DataSet`, so it is not the dataset's name. The first segment
differs because the IED was renamed after the id was written: the file's own
`Header/History` records *"QPC2_TR1_UPC4 is imported from ... .iid"*, no IED
carries that name any more, and `UPC5` and `UPC6` moved to `AL15` and `AL16`
the same way. That is a live instance of the rename fan-out A13 owns, left in
a vendor export by a tool that did not follow it.

The confirmation is from the GOOSE side: all 16 corpus `smvID` values are
vendor strings such as `QMA1_MU1_E_S1`, so nothing here disagrees with
`path_id` either.

## What a rename drags along

`updateSampledValueControl`'s doc names two things a `name` change triggers,
*"also updates SMV.cbName and supervision references"*, and this phase does
the first and refuses the second. Supervision is A14's: the corpus holds 15
`LSVS` logical nodes waiting for it, and `ignore_supervision=False` is refused
rather than quietly ignored, exactly as in the six functions that already
carry it.

It also re-points subscribers, which the reference documents for neither
update function -- `mixed.scd` holds 256 `ExtRef` elements with
`serviceType="SMV"`. The argument is in
:mod:`py61850.scl.report_control`'s docstring and in Q29.
"""

from __future__ import annotations

from typing import List
from xml.etree import ElementTree as ET

from .address import connected_ap_for, create_smv
from .control_block import (
    _is_control_block,
    control_block_gse_or_smv,
    path_id,
    update_dat_set,
)
from .data_set import _limit, _namespace, _services_child
from .document import strip_ns
from .edit import EditRejected, Insert, SetAttributes
from .extref import _ancestor, _ied_name, _qualify, _same
from .ordering import may_contain, reference_for
from .report_control import _has_data_set, _name_clash, _resolve_parent

#: What the reference writes when the caller names neither, and what the
#: schema's own defaults are. `smpRate` and `nofASDU` are `use="required"`, so
#: a value has to be chosen rather than omitted.
DEFAULT_SMP_RATE = "80"
DEFAULT_NOF_ASDU = "1"


# -- the guard --------------------------------------------------------------

def can_add_sampled_value_control(doc, ln0) -> bool:
    """Whether another `SampledValueControl` may be added under ``ln0``.

    ``ln0`` is the `LN0` the block would go in, or anything above it -- the
    `AccessPoint`'s `Services/SMVsc` is read first and the `IED`'s second,
    which is the order the reference documents and the same walk
    :func:`~py61850.scl.can_add_data_set` and
    :func:`~py61850.scl.can_add_report_control` make. The count is taken in
    the scope that declared the limit (Q26).

    ``False`` for a parent that cannot hold one at all. An IED declaring no
    `SMVsc` is unconstrained and this answers ``True``, which is A8's rule
    that an unresolvable restriction is not a restriction.

    **This is the one refusal in the phase that fires on vendor material.**
    Every limit A10 met was far from being reached -- at most 22 datasets
    against a declared 50 -- so both of its refusals were tested against built
    fixtures. Here four `mixed.scd` IEDs declare ``SMVsc max="4"`` and hold
    exactly four `SampledValueControl` elements each: they are AT their
    declared limit, and asking for a fifth is refused by the file rather than
    by a fixture. No other corpus file carries an `SMVsc` at all.
    """
    node = _resolve_parent(doc, ln0)
    if node is None or strip_ns(node.tag) != "LN0":
        return False
    if not may_contain(node.tag, _namespace(node.tag) + "SampledValueControl"):
        return False
    found = _services_child(doc, node, "SMVsc")
    if found is None:
        return True
    conf, _scope, owner = found
    limit = _limit(conf, "max")
    if limit is None:
        return True
    return _count_under(owner) < limit


def _count_under(scope) -> int:
    """`SampledValueControl` elements under ``scope``, own namespace only.

    Q27's measurement is why the namespace is checked: counting by local name
    lets another vendor's private markup decide whether an edit is refused --
    `mixed.scd`'s `SMVApplication` privates hold 16 elements of their own.
    """
    wanted = _namespace(scope.tag) + "SampledValueControl"
    return sum(1 for element in scope.iter() if element.tag == wanted)


# -- element creation -------------------------------------------------------

def create_sampled_value_control(doc, parent, name, desc=None, dat_set=None,
                                 smv_id=None, multicast=None,
                                 smp_rate=DEFAULT_SMP_RATE,
                                 nof_asdu=DEFAULT_NOF_ASDU, smp_mod=None,
                                 security_enable=None, conf_rev="1",
                                 smv_opts=None, ap_name=None, mac=None,
                                 app_id=None, vlan_id=None, vlan_priority=None,
                                 force=False) -> List:
    """The edits that add a `SampledValueControl` and address it.

    ``parent`` is an `LN0`, or an `LDevice`, `AccessPoint` or `IED` whose
    first `LN0` is picked. Unlike a `ReportControl`, a `SampledValueControl`
    is an `LN0` child only -- `tLN` does not declare one.

    ``smv_opts`` is a mapping written onto the required `SmvOpts` child.
    ``ap_name`` chooses which access point publishes the stream; without it
    the one holding the `Server` is taken, as the reference documents and
    :func:`~py61850.scl.connected_ap_for` explains.

    Returns **one or two** :class:`~py61850.scl.Insert` edits: the control
    block, and the `SMV` when the IED has a `ConnectedAP` to carry it. Both
    are one compound edit and one history entry.

    ``name`` is required, for Q27's reason, and ``force`` skips the `SMVsc`
    guard and the `datSet` resolution check as the reference's `skipCheck`
    does.

    Raises :class:`~py61850.scl.EditRejected` if ``parent`` has no `LN0`, if
    ``name`` is empty or already used in the logical node, if ``dat_set``
    names no `DataSet` there, or if the declared `SMVsc` maximum is reached
    and ``force`` is not set.
    """
    node = _resolve_parent(doc, parent)
    if node is None or strip_ns(node.tag) != "LN0" or not may_contain(
            node.tag, _namespace(node.tag) + "SampledValueControl"):
        shown = (strip_ns(parent.tag) if isinstance(parent, ET.Element)
                 else type(parent).__name__)
        raise EditRejected(
            f"{shown} may not hold a SampledValueControl and no LN0 was found "
            f"beneath it; 61850-6 places one on LN0 and nowhere else")
    if not name:
        raise EditRejected(
            "a SampledValueControl needs a name -- it is required by the "
            "schema and unique within its logical node; allocating one is "
            "A17's")
    clash = _name_clash(node, name)
    if clash is not None:
        raise EditRejected(
            f"a {strip_ns(clash.tag)} named {name!r} is already in this LN0")
    if dat_set and not force and not _has_data_set(node, dat_set):
        raise EditRejected(
            f"no DataSet named {dat_set!r} is in this LN0; datSet is an "
            f"xs:keyref and resolves in the logical node")
    if not force and not can_add_sampled_value_control(doc, node):
        conf, scope, owner = _services_child(doc, node, "SMVsc")
        raise EditRejected(
            f"{owner.get('name') or scope} already holds "
            f"{_count_under(owner)} SampledValueControl elements, which is "
            f"the maximum its {scope} SMVsc declares (max={conf.get('max')})")

    element = ET.Element(_qualify(doc, "SampledValueControl"))
    for attribute, value in (
            ("desc", desc), ("name", name), ("datSet", dat_set),
            ("confRev", conf_rev),
            ("smvID", smv_id if smv_id is not None else path_id(doc, node, name)),
            ("multicast", multicast), ("smpRate", smp_rate),
            ("nofASDU", nof_asdu), ("smpMod", smp_mod),
            ("securityEnable", security_enable)):
        if value is not None:
            element.set(attribute, value)
    # Required by tSampledValueControl, written empty when nothing was asked
    # for -- every attribute in agSmvOpts has a default or is fixed.
    options = ET.SubElement(element, _qualify(doc, "SmvOpts"))
    for attribute, value in (smv_opts or {}).items():
        options.set(attribute, value)

    edits: List = [Insert(node, element, reference_for(node, element.tag))]

    connected_ap = connected_ap_for(doc, _ied_name(doc, node), ap_name)
    if connected_ap is not None:
        ldevice = _ancestor(doc, node, "LDevice")
        edits.extend(create_smv(
            doc, connected_ap,
            "" if ldevice is None else (ldevice.get("inst") or ""),
            name, mac=mac, app_id=app_id, vlan_id=vlan_id,
            vlan_priority=vlan_priority))
    return edits


# -- edit checks ------------------------------------------------------------

def update_sampled_value_control(doc, edit, ignore_supervision=True) -> List:
    """``edit`` corrected: the dataset, the address, and every subscriber.

    ``edit`` is a :class:`~py61850.scl.SetAttributes` on a
    `SampledValueControl`. What comes back is everything the caller should
    apply, input edit first:

    1. the edit itself, as given;
    2. whatever a `datSet` change implies, through A9's
       :func:`~py61850.scl.update_dat_set` -- the exclusive `DataSet` renamed
       with it, or `confRev` moved when the block is re-pointed. The
       reference's own note says *"confRev attribute is updated +10000 on each
       data set change"*, which is
       :data:`~py61850.scl.CONF_REV_STEP` arrived at independently: A9
       derived the step from 1,014 corpus values before that sentence was
       read;
    3. `SMV@cbName` followed, when `name` changes -- the reference's first
       documented fan-out, found through A9's
       :func:`~py61850.scl.control_block_gse_or_smv`;
    4. `srcCBName` re-pointed on every `ExtRef` subscribed to the block.

    ``ignore_supervision=False`` is refused: supervision is A14's, and this is
    the function whose own documentation promises it, which makes saying so
    more important here than anywhere else.

    Raises :class:`~py61850.scl.EditRejected` if ``edit`` is not a
    `SetAttributes` on a `SampledValueControl`, if it would clear a required
    attribute, or if the new name is already used in the logical node.
    """
    if not ignore_supervision:
        raise EditRejected(
            "subscription supervision is not written yet; "
            "ignore_supervision=False has nothing to turn off")
    if not isinstance(edit, SetAttributes):
        raise EditRejected(
            "update_sampled_value_control takes a SetAttributes, not "
            f"{type(edit).__name__}")
    control = edit.element
    if not _is_control_block(control) or \
            strip_ns(control.tag) != "SampledValueControl":
        shown = (strip_ns(control.tag) if isinstance(control, ET.Element)
                 else type(control).__name__)
        raise EditRejected(f"{shown} is not a SampledValueControl")
    for required in ("smvID", "smpRate", "nofASDU"):
        if required in edit.attributes and not edit.attributes[required]:
            raise EditRejected(
                f"SampledValueControl@{required} is use=\"required\" in "
                f"61850-6; it cannot be cleared")

    from .report_control import _rename_edits

    edits: List = list(update_dat_set(doc, edit))
    # `_rename_edits` validates the new name as well as producing the
    # subscriber edits, so it runs before anything is appended.
    subscribers = _rename_edits(doc, control, edit)
    wanted = edit.attributes.get("name")
    if "name" in edit.attributes and not _same(wanted, control.get("name")):
        address = control_block_gse_or_smv(doc, control)
        if address is not None:
            edits.append(SetAttributes(address, {"cbName": wanted}))
    edits.extend(subscribers)
    return edits
