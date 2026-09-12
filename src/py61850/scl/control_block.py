# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""Control blocks: what publishes a dataset, and what a removal drags with it.

    from py61850.scl import Remove, SclDocument, remove_control_block

    doc = SclDocument.parse("station.scd")
    edit = remove_control_block(doc, Remove(block))
    undo = doc.apply_edit(edit)     # block, dataset, address and subscribers

**Two shapes live here, and the second one is new.** The queries --
:func:`control_blocks`, :func:`find_control_block_subscription`,
:func:`control_block_obj_ref`, :func:`path_id`,
:func:`control_block_gse_or_smv` and :func:`updated_conf_rev` -- answer a
question about the document and change nothing. The two **edit checks** --
:func:`update_dat_set` and :func:`remove_control_block` -- take the edit the
CALLER already built and return the corrected, expanded one:

    doc.apply_edit(update_dat_set(doc, SetAttributes(block, {"datSet": "B"})))

That is the reference's own category -- *"the input is a delta ... the output
is a corrected delta, and the difference between the two contains expertise
related to IEC 61850-6"* -- and it is deliberately not
:mod:`py61850.scl.extref`'s shape, which builds an edit out of elements. A
pane that has already expressed the engineer's intent as a `SetAttributes`
hands that over rather than taking it apart again, and the 61850-6 knowledge
is what comes back attached to it.

**The returned list is everything the caller should apply**, the input edit
included, so applying it is one call and undoing it is one entry. The
reference is not uniform about this -- its `removeControlBlock` returns the
array including the removal and its `updateDatSet` returns only the extra
edit -- and one rule is worth a divergence.

**Nothing here applies anything.** As in `extref`, the document is untouched
until the caller passes the result to
:meth:`~py61850.scl.SclDocument.apply_edit`, and a refusal raises
:class:`~py61850.scl.EditRejected` with nothing half-done.

## What `confRev` means, and when it moves

`confRev` is the configuration revision of the data a control block
publishes: a subscriber that has cached the dataset layout uses it to notice
that what arrives no longer matches what it learned. So it must move when the
PUBLISHED DATA changes -- the `datSet` reference changes, or the referenced
`DataSet` gains, loses or reorders a member -- and it must not move for a
`desc` or a `bufTime`.

**A9 owns exactly one of those triggers**: the `datSet` change, in
:func:`update_dat_set`, and only where it re-points the block at different
data. Where it RENAMES the block's exclusive dataset instead, the published
data is unchanged and the revision does not move -- A10 decided that from the
DataSet side and this side was corrected to agree. The rest of the DataSet
triggers belong with `tDataSet` and `tFCDA`, where the member edits are;
:func:`updated_conf_rev` is the shared rule and
:func:`~py61850.scl.updated_conf_rev_edits` its fan-out.

**The step is 10,000, and the reference corpus is what says so.** It looks
arbitrary until the files are counted: of 1,015 control blocks across three
vendor tools, **1,014 carry a `confRev` congruent to 1 modulo 10,000** --
`850001`, `90001`, `1200001` and the rest are `1 + N x 10000`, one GOOSE
block in the mixed-vendor station standing at `1,890,001`, or 189 data-set
changes deep. The single exception is one SEL block at `2`. A step of 1 would
be indistinguishable from the vendor-encoded values already in the file; a
step of 10,000 continues the sequence the file is already writing.

A caller that names `confRev` in its own edit has decided the revision
itself, and nothing is added -- the reference's `create*Control` options say
the same in their own words.

## What "exclusive" means, for a dataset

`removeControlBlock` removes the `DataSet` the block published *"if used by
the control block exclusively"*, and that needs a definition sharp enough to
test. Here it is: **no other `GSEControl`, `ReportControl`,
`SampledValueControl` or `LogControl` in the SAME logical node carries a
`datSet` equal to this DataSet's `name`.**

Same logical node, because that is the scope 61850-6 resolves `datSet` in --
an unprefixed name, not a path. Measured, the scoping is exactly right: **0
dangling `datSet` references** in all three corpus files under that rule.

Two things the corpus says about the edges:

- **A block that names no dataset is not a user of one.** 720 of the corpus's
  1,015 control blocks -- the majority of all report control blocks in it --
  carry no `datSet` at all: they are one vendor's unconfigured RCB templates,
  carrying `rptID`, `confRev`, `buffered`, `name` and a `Private`, and nothing
  else. They cannot make a dataset non-exclusive and they cannot lose one.
- **A dataset used by nobody is normal.** 114 `DataSet` elements in one
  corpus file and 14 in another are referenced by no control block at all.
  Nothing here sweeps them up; an orphan dataset is a vendor's decision.

No dataset in the corpus is shared by two control blocks, so the branch that
KEEPS a shared dataset is tested against built fixtures rather than against
a file. It is kept anyway, because the schema permits the sharing and a
removal that deleted a dataset another block still published would be
silently destroying a service.

## What a removal takes with it, and what it does not

`Remove(block)` expands to the block, plus:

- **its subscribers, unsubscribed** -- through A8's
  :func:`~py61850.scl.unsubscribe`, so an `ExtRef` with `intAddr` is blanked
  and kept and one without is removed, with an emptied `Inputs` going too;
- **its `DataSet`**, when exclusive by the definition above;
- **its `GSE` or `SMV` address** in the `Communication` section.

The last is a **divergence**: the reference documents four steps and the
address is not one of them. The corpus is 1:1 -- 35, 97 and 14 `GSE` against
exactly that many `GSEControl`, 16 `SMV` against 16 `SampledValueControl` --
so there is no ambiguity about which address belongs to which block, and
leaving it behind writes a `Communication` section naming a control block
that is gone. See Q21.

**`IEDName` needs no path at all.** 345 `IEDName` children in the corpus
record subscribers the Edition 1 way, and every one of them is a CHILD of the
control block, so a removal takes them with the subtree. They are also never
the only record: of the 113 GOOSE and sampled-value blocks in the
mixed-vendor station, 111 carry both `IEDName` children and `ExtRef`
subscribers and **none carries `IEDName` alone**. So
:func:`find_control_block_subscription` returns `ExtRef` elements only, as
the reference's does. The `IEDName` cleanup that genuinely exists is a
removed IED's, and it belongs with `tIED`.

**Supervision is not written yet.** `ignore_supervision` is carried for the
same reason :func:`~py61850.scl.subscribe`'s is, and refuses `False` the same
way: the corpus has 585 `LGOS` and 15 `LSVS` logical nodes and 1,750
`setSrcRef` values waiting for it, and pretending to clean them up would be
worse than saying it does not.

## The stale-model hazard, which this module makes worse

`SclDocument` caches `ied(name)`, and each `LogicalNode` memoises its
`control_blocks` and `data_sets`. A8 only changed attributes, so a cached
model object went on describing the file. **A9 removes elements**: after
`remove_control_block`, `ln.control_blocks` still hands back a
:class:`~py61850.scl.ControlBlock` wrapping an element no longer in the
document, and `ln.data_sets` the same for a removed `DataSet`.

Nothing is invalidated automatically, for the reasons and the numbers in
:meth:`~py61850.scl.SclDocument.apply_edit` -- re-warming one `Ied` on a
22 MB export costs 41 ms against 3 ms warm. Every capability here takes and
returns live ELEMENTS, so a caller working in elements is never stale; a
caller holding model objects across a removal must re-read. See Q14.
"""

from __future__ import annotations

from typing import List, Optional
from xml.etree import ElementTree as ET

from .controls import CONTROL_BLOCK_TAGS
from .document import children_local, iter_local, strip_ns
from .edit import EditRejected, Remove, SetAttributes
# The ancestry helpers A8 wrote, used rather than written again: an `ExtRef`
# and a control block ask the same questions of the same tree, and two copies
# of "which IED is this in" is how the two modules drift apart.
from .extref import (
    _ancestor,
    _ied_name,
    _same,
    match_src_attributes,
    unsubscribe,
)

#: What `confRev` moves by when the published data changes. See the module
#: docstring: 1,014 of the corpus's 1,015 control blocks carry `1 + N x 10000`.
CONF_REV_STEP = 10000

#: The `Communication` element that carries a control block's link-layer
#: address, by the control block's own element name. A `ReportControl` is
#: carried over MMS on the IED's own address and has none of its own, and a
#: `LogControl` writes to a log rather than publishing at all.
ADDRESS_TAG = {"GSEControl": "GSE", "SampledValueControl": "SMV"}


# -- ancestry ---------------------------------------------------------------

def _logical_node(doc, element):
    """The `LN` or `LN0` holding ``element`` -- or ``element`` itself.

    Unlike `extref`'s equivalent this starts at the element rather than at its
    parent, because :func:`path_id` is handed the `LN0` directly.
    """
    node = element
    while node is not None:
        if strip_ns(node.tag) in ("LN", "LN0"):
            return node
        node = doc.parent_of(node)
    return None


def _placement(doc, node):
    """``(iedName, ldInst, prefix, lnClass, lnInst)`` for an `LN`/`LN0`.

    ``None`` when the node is not inside an `IED`. An `LN0` reports `LLN0`
    whether or not the attribute is written -- it is the only class it can
    have, and every reference built here has to name it.
    """
    ied = _ancestor(doc, node, "IED")
    if ied is None or not ied.get("name"):
        return None
    ldevice = _ancestor(doc, node, "LDevice")
    is_ln0 = strip_ns(node.tag) == "LN0"
    return (
        ied.get("name"),
        "" if ldevice is None else (ldevice.get("inst") or ""),
        node.get("prefix") or "",
        node.get("lnClass") or ("LLN0" if is_ln0 else ""),
        node.get("inst") or "",
    )


def _is_control_block(element) -> bool:
    return (isinstance(element, ET.Element)
            and strip_ns(element.tag) in CONTROL_BLOCK_TAGS)


# -- queries ----------------------------------------------------------------

def control_blocks(doc, fcda_or_data_set) -> List[ET.Element]:
    """Every control block that publishes ``fcda_or_data_set``, in file order.

    ``fcda_or_data_set`` is a `DataSet`, or an `FCDA` inside one -- a member
    is published by whatever publishes the dataset holding it, so the two
    questions have one answer.

    **The FCDA is resolved through its parent, not by matching attributes.**
    An `FCDA` belongs to exactly one `DataSet` because it is a child of it, so
    nothing here compares `doName` against anything and the whole-data-object
    question of Q19 does not arise.

    `LogControl` is included: it carries a `datSet` like the other three and a
    dataset a log still writes is a dataset in use. Nothing SUBSCRIBES to a
    log, which is a different question and is why
    :data:`~py61850.scl.extref.SERVICE_TYPE` leaves it out.

    An empty list is an ordinary answer -- 128 `DataSet` elements in the
    reference corpus are published by nothing.
    """
    if not isinstance(fcda_or_data_set, ET.Element):
        return []
    tag = strip_ns(fcda_or_data_set.tag)
    data_set = fcda_or_data_set
    if tag == "FCDA":
        data_set = doc.parent_of(fcda_or_data_set)
        if data_set is None or strip_ns(data_set.tag) != "DataSet":
            return []
    elif tag != "DataSet":
        return []

    name = data_set.get("name")
    node = doc.parent_of(data_set)
    if not name or node is None:
        return []
    return [child for child in node
            if strip_ns(child.tag) in CONTROL_BLOCK_TAGS
            and child.get("datSet") == name]


def find_control_block_subscription(doc, control) -> List[ET.Element]:
    """Every `ExtRef` in the document subscribed to ``control``.

    Edition 2, through the `src*` attributes, which is the same match
    :func:`~py61850.scl.source_control_block` makes from the other end --
    asked once per document here rather than once per `ExtRef`, since the
    publisher is known.

    **Edition 1 `IEDName` children are not returned**, and the module
    docstring says why: they are children of ``control`` and a removal takes
    them with it, and no block in the reference corpus records a subscriber
    only that way.
    """
    if not _is_control_block(control):
        return []
    name = control.get("name")
    publisher = _ied_name(doc, control)
    if not name or not publisher:
        return []
    out = []
    for ext_ref in iter_local(doc.root, "ExtRef"):
        if not _same(ext_ref.get("srcCBName"), name):
            continue
        if not _same(ext_ref.get("iedName"), publisher):
            continue
        if match_src_attributes(doc, ext_ref, control):
            out.append(ext_ref)
    return out


def control_block_obj_ref(doc, control_block) -> Optional[str]:
    """The IEC 61850-7-2 object reference of ``control_block``, or ``None``.

    ``<IED name><LDevice inst>/<prefix><lnClass><inst>.<control block name>``,
    as in ``QPC1_LT1_UPC2CFG/LLN0.GoSB00``.

    **The form is not inferred -- it is in the files.** 1,750 `setSrcRef`
    values across two corpus exports are written exactly this way, which is
    what pins the test. The logical-device half is the IED's name
    concatenated with the `LDevice@inst`, because that is what 61850-6's
    `IEDName` name structure means and because no `LDevice` in the corpus --
    0 of 999 -- carries the `ldName` attribute that would override it.

    ``None`` when the element is not a control block, when it has no name, or
    when it is not inside an `IED`.
    """
    if not _is_control_block(control_block) or not control_block.get("name"):
        return None
    node = _logical_node(doc, control_block)
    if node is None:
        return None
    placement = _placement(doc, node)
    if placement is None:
        return None
    ied_name, ld_inst, prefix, ln_class, ln_inst = placement
    return (f"{ied_name}{ld_inst}/{prefix}{ln_class}{ln_inst}"
            f".{control_block.get('name')}")


def path_id(doc, ln0, cb_name) -> Optional[str]:
    """``<IED name>/<LDevice inst>/<lnClass>/<cb_name>`` for a control block
    that ``ln0`` holds or would hold.

    This is the identifier a new `GSEControl` or `SampledValueControl` takes
    as its `appID`/`smvID` when the caller names none -- built from the
    logical node and a name rather than from an element, so it can be computed
    BEFORE the block exists.

    **The form is inferred and the corpus cannot confirm it.** The reference
    publishes the function with no documentation of what it returns; what
    fixes the shape here is that its `createGSEControl` and
    `createSampledValueControl` both document their id default as
    *"IED.name/LDevice.inst/LLN0/GSEControl.name"*, which is exactly this
    function's two arguments. No `appID` or `smvID` in the reference corpus is
    path-shaped -- all 148 are vendor strings such as
    ``QPC1_LT1_UPC1_C0_22`` -- so no file either agrees or disagrees. Recorded
    as Q20; it is the one thing in this module resting on an inference.

    Note the separators: slashes throughout, where
    :func:`control_block_obj_ref` writes the 7-2 reference with a dot before
    the block name and no separator inside the logical-device name. They are
    two different strings for two different purposes and neither is a
    spelling variant of the other.

    ``None`` when ``ln0`` is not a logical node, or not inside an `IED`.
    """
    if not isinstance(ln0, ET.Element) or strip_ns(ln0.tag) not in ("LN", "LN0"):
        return None
    placement = _placement(doc, ln0)
    if placement is None:
        return None
    ied_name, ld_inst, prefix, ln_class, ln_inst = placement
    return f"{ied_name}/{ld_inst}/{prefix}{ln_class}{ln_inst}/{cb_name}"


def control_block_gse_or_smv(doc, control_block) -> Optional[ET.Element]:
    """The `GSE` or `SMV` element addressing ``control_block``, or ``None``.

    A `GSEControl` is addressed by a `GSE` and a `SampledValueControl` by an
    `SMV`, both inside the `ConnectedAP` of the publishing IED and both keyed
    by ``ldInst`` and ``cbName``. A `ReportControl` has no address of its own
    and a `LogControl` publishes nothing, so both answer ``None``.

    **Every `ConnectedAP` of the IED is searched, not just the first.** 12
    IEDs in the mixed-vendor reference station have more than one access
    point, and the pair ``(ldInst, cbName)`` is unique within an IED whichever
    one carries it.

    This walks the tree rather than reading
    :attr:`~py61850.scl.SclDocument.communication`, because the model is
    cached and an edit does not invalidate it -- the hazard in the module
    docstring -- and because a capability has to return the live element that
    an edit can remove.
    """
    if not _is_control_block(control_block):
        return None
    tag = ADDRESS_TAG.get(strip_ns(control_block.tag))
    name = control_block.get("name")
    if tag is None or not name:
        return None
    node = _logical_node(doc, control_block)
    if node is None:
        return None
    placement = _placement(doc, node)
    if placement is None:
        return None
    ied_name, ld_inst = placement[0], placement[1]

    for communication in children_local(doc.root, "Communication"):
        for subnetwork in children_local(communication, "SubNetwork"):
            for connected_ap in children_local(subnetwork, "ConnectedAP"):
                if not _same(connected_ap.get("iedName"), ied_name):
                    continue
                for address in children_local(connected_ap, tag):
                    if (_same(address.get("ldInst"), ld_inst)
                            and _same(address.get("cbName"), name)):
                        return address
    return None


def updated_conf_rev(control) -> str:
    """What ``control``'s `confRev` becomes when its published data changes.

    A value, not an edit: the caller decides whether the change that triggers
    it is happening. :func:`update_dat_set` is the one trigger this phase
    owns; adding, removing or reordering a `DataSet` member is the other, and
    it belongs where those edits are.

    The step is :data:`CONF_REV_STEP` and the module docstring carries the
    measurement behind it. A control block carrying no `confRev` -- none in
    the corpus does -- starts at one step rather than at zero, because a
    revision of ``0`` reads as "never configured" to a subscriber. A value
    that is not an integer is returned unchanged: it is a vendor's encoding
    and renumbering it would be this library inventing a revision nobody can
    interpret.
    """
    current = control.get("confRev")
    if not current:
        return str(CONF_REV_STEP)
    try:
        return str(int(current) + CONF_REV_STEP)
    except ValueError:
        return current


# -- datasets ---------------------------------------------------------------

def _data_set_of(doc, control):
    """The `DataSet` ``control`` names, resolved in its own logical node."""
    name = control.get("datSet")
    node = doc.parent_of(control)
    if not name or node is None:
        return None
    for data_set in children_local(node, "DataSet"):
        if data_set.get("name") == name:
            return data_set
    return None


def _is_exclusive(doc, data_set, control) -> bool:
    """Whether ``control`` is the only control block publishing ``data_set``."""
    node = doc.parent_of(data_set)
    name = data_set.get("name")
    if node is None or not name:
        return False
    for child in node:
        if child is control or strip_ns(child.tag) not in CONTROL_BLOCK_TAGS:
            continue
        if child.get("datSet") == name:
            return False
    return True


# -- edit checks ------------------------------------------------------------

def update_dat_set(doc, edit) -> List:
    """``edit`` corrected: the `DataSet` renamed with it, and `confRev` moved.

    ``edit`` is a :class:`~py61850.scl.SetAttributes` on a control block. If
    its mapping does not touch `datSet`, or sets it to what it already is,
    the edit is returned alone -- there is nothing 61850-6 has to add.

    **Changing `datSet` on an exclusively-used dataset RENAMES that dataset**
    rather than pointing the block at a different one. That is the
    reference's behaviour and it is more surprising written down than it is in
    use: a dataset only this block publishes has no independent identity, and
    an engineer renaming the block's dataset means the one they can see.

    It follows that a block whose dataset is exclusive cannot be re-pointed at
    another existing dataset through this function, and that is refused rather
    than silently producing two `DataSet` elements with one name -- see Q22.
    The way to re-point such a block is to edit the `DataSet` first, or to
    build the `SetAttributes` and apply it without this check.

    **`confRev` moves only when the block is genuinely re-pointed.** Setting
    `datSet` on a block whose dataset is exclusive RENAMES that dataset, and
    the data the subscriber cached is then exactly what it was -- the same
    members in the same order, under another name. So the rename branch leaves
    the revision alone, and every other path moves it, unless the caller's own
    mapping names `confRev` and has decided it. A10 took the same view from
    the DataSet side, where :func:`~py61850.scl.update_data_set` re-points the
    publishers of a renamed dataset and moves nothing; this was corrected to
    agree with it rather than the two sides of one event disagreeing.

    Returns ``[edit, rename?, confRev?]``, in that order.

    Raises :class:`~py61850.scl.EditRejected` if ``edit`` is not a
    `SetAttributes` on a control block, or if the rename would collide.
    """
    if not isinstance(edit, SetAttributes):
        raise EditRejected(
            f"update_dat_set takes a SetAttributes, not {type(edit).__name__}")
    control = edit.element
    if not _is_control_block(control):
        raise EditRejected(
            f"{strip_ns(control.tag) or type(control).__name__} is not a "
            f"control block; expected one of {', '.join(CONTROL_BLOCK_TAGS)}")
    if "datSet" not in edit.attributes:
        return [edit]

    wanted = edit.attributes["datSet"]
    if _same(wanted, control.get("datSet")):
        return [edit]

    edits: List = [edit]
    renamed = False
    data_set = _data_set_of(doc, control)
    if data_set is not None and wanted and _is_exclusive(doc, data_set, control):
        node = doc.parent_of(data_set)
        clash = next((other for other in children_local(node, "DataSet")
                      if other is not data_set and other.get("name") == wanted),
                     None)
        if clash is not None:
            raise EditRejected(
                f"renaming DataSet {data_set.get('name')!r} to {wanted!r} "
                f"would collide with the DataSet already named {wanted!r} in "
                f"the same logical node")
        edits.append(SetAttributes(data_set, {"name": wanted}))
        renamed = True

    if not renamed and "confRev" not in edit.attributes:
        edits.append(SetAttributes(control,
                                   {"confRev": updated_conf_rev(control)}))
    return edits


def remove_control_block(doc, edit, ignore_supervision=True) -> List:
    """``edit`` expanded: the block, its subscribers, its dataset, its address.

    ``edit`` is a :class:`~py61850.scl.Remove` whose node is a `GSEControl`,
    `ReportControl`, `SampledValueControl` or `LogControl`. What comes back is
    everything that removal implies, as one compound edit:

    1. the removal itself, as given;
    2. the `ExtRef` elements subscribed to it, unsubscribed through
       :func:`~py61850.scl.unsubscribe` -- blanked where a later-binding
       `intAddr` means the input outlives the connection, removed where it
       does not, with an emptied `Inputs` removed too;
    3. its `DataSet`, when no other control block in the logical node
       publishes it -- and whatever THAT removal drags along, through A10's
       :func:`~py61850.scl.remove_data_set`, which is the same expansion a
       caller removing the dataset directly gets;
    4. its `GSE` or `SMV` address in the `Communication` section.

    Step 4 is a divergence from the reference, which documents the other
    three; the module docstring and Q21 carry the argument. Step 3's
    definition of exclusive is there too, along with why 720 of the corpus's
    control blocks -- the ones naming no dataset at all -- reach step 3 and
    do nothing.

    ``ignore_supervision`` behaves exactly as
    :func:`~py61850.scl.unsubscribe`'s: supervision is not written yet, and
    `False` is refused rather than quietly ignored.

    **The subscriber set is the one the `src*` attributes name**, and on this
    corpus that is provably the whole of it: of 830,000 elements across three
    vendor exports, **no `ExtRef` bound to a published attribute fails to also
    name the control block publishing it**. What an `ExtRef` bound to a member
    that publishes a WHOLE data object should do was left to `tDataSet`, and
    A10 answered it: step 3 now delegates, so an `ExtRef` taking one attribute
    of a whole-object member is unsubscribed here too, and is not unsubscribed
    twice -- the ExtRefs step 2 already handled are passed along and skipped.
    See Q19.

    Raises :class:`~py61850.scl.EditRejected` if ``edit`` is not a `Remove` of
    a control block.
    """
    if not ignore_supervision:
        raise EditRejected(
            "subscription supervision is not written yet; "
            "ignore_supervision=False has nothing to turn off")
    if not isinstance(edit, Remove):
        raise EditRejected(
            f"remove_control_block takes a Remove, not {type(edit).__name__}")
    control = edit.node
    if not _is_control_block(control):
        raise EditRejected(
            f"{strip_ns(control.tag) or type(control).__name__} is not a "
            f"control block; expected one of {', '.join(CONTROL_BLOCK_TAGS)}")

    edits: List = [edit]

    subscribers = find_control_block_subscription(doc, control)
    if subscribers:
        edits.extend(unsubscribe(doc, subscribers))

    data_set = _data_set_of(doc, control)
    if data_set is not None and _is_exclusive(doc, data_set, control):
        # A10 owns what removing a DataSet drags along, and this is the one
        # place A9 removes one. The import is deferred because `data_set`
        # imports this module for `control_blocks` and `updated_conf_rev`:
        # the dependency runs A10 -> A9 at module level and back only here.
        from .data_set import _expand_remove_data_set
        edits.append(Remove(data_set))
        edits.extend(_expand_remove_data_set(
            doc, data_set, exclude_blocks=(control,), already=subscribers,
            update_conf_rev=True))

    address = control_block_gse_or_smv(doc, control)
    if address is not None:
        edits.append(Remove(address))
    return edits
