# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""Report control blocks: creating them, and what a change to one drags along.

    from py61850.scl import SclDocument, create_report_control

    doc = SclDocument.parse("station.scd")
    doc.apply_edit(create_report_control(doc, ln0, "URep01", dat_set="DSet01"))

Five capabilities, in the reference's three shapes:

- **queries** -- :func:`max_report_control` and
  :func:`number_report_control_instances` answer a question and change
  nothing;
- **a guard** -- :func:`can_add_report_control`, which is what stops a
  document being written that this library loads and the vendor tool refuses;
- **element creation** -- :func:`create_report_control`, A8 and A10's shape;
- **an edit check** -- :func:`update_report_control`, A9's shape: the
  caller's `SetAttributes` goes in and the corrected, expanded one comes back,
  input edit first (Q23).

## What `max` counts, and the reading that was rejected

`Services/ConfReportControl` carries `max`, and 61850-6 inherits the word
*instances* for it from `tServiceWithMax`. A `ReportControl` is not
necessarily one instance: `indexed` defaults to **true**, and `RptEnabled@max`
then says how many numbered instances the IED creates from that one element.
So "22 instances out of 4 elements" is expressible, and the two readings can
disagree.

**They are counted as ELEMENTS here**, and three things decide it:

- **The reference counts elements.** `numberReportControlInstances`, which is
  published beside the guard and is what feeds it, is documented as
  *"Number of `ReportControl` elements within root"*. Its sibling takes a
  `newInstances` count with no per-element instance figure beside it, which
  only makes sense if an element is an instance.
- **The corpus does not disagree.** It looks at first as though it does:
  `siemens.scd`'s `TR1_2414` declares `max="14"` and holds four
  `ReportControl` elements whose `RptEnabled@max` values are 7, 7, 7 and 1.
  Summed, that is 22 against a declared 14 -- the vendor breaching its own
  declaration. But **all four carry `indexed="false"`**, which resets the
  instance count to one apiece, so the file's own attributes say 4 and not 22.
  Swept across all three exports, **no `ReportControl` anywhere in the corpus
  has `indexed` absent-or-true together with an `RptEnabled@max` above 1** --
  every multi-instance declaration in it is cancelled by its own `indexed`.
  Elements and instances are equal for all 58 IEDs, so no file refutes either
  reading.
- **It removes a question rather than answering one.** `RptEnabled` is absent
  from 727 of the corpus's 852 `ReportControl` elements, so an instance count
  would need a rule for what to assume there. Counting elements never reads
  `RptEnabled` at all.

It is a judgement and not a measurement, and the case it would get wrong is
real: a file with two `indexed="true"` blocks at `RptEnabled@max="8"` against
a declared `max="14"` passes here and arguably should not. No such file
exists in the corpus. See Q28.

**`bufMode` is deliberately not read.** `tServiceConfReportControl` carries
it -- `unbuffered`, `buffered` or `both`, defaulting to `both` -- and an IED
declaring `unbuffered` arguably may not take a buffered block at all. The
reference's guard names `max` and `maxBuf` and not this, 54 of the corpus's 58
declarations say `both` and the other 2 say nothing, so no file would be
decided by it either way. Recorded for the same reason Q26 recorded
`ConfDataSet@modify`: it is the attribute on that element which most
plausibly belongs in a guard and is knowingly absent from ours.

## What the schema requires, and what the corpus writes instead

- **`OptFields` is a REQUIRED child** of `tReportControl` -- `minOccurs`
  defaults to 1 -- and `confRev` is `use="required"`. So neither is optional
  in :func:`create_report_control`, which writes an `OptFields` whether or not
  the caller names a field in it. All 852 corpus blocks carry one; 720 of them
  carry one with **no attributes at all**, which is valid because every
  attribute in `agOptFields` has a default.
- **`TrgOps` and `RptEnabled` are `minOccurs="0"`.** Neither is written
  unless asked for, which is the shape 720 of the corpus's blocks have: an
  empty `OptFields`, a `Private`, no `TrgOps`, no `RptEnabled` and no
  `datSet`. Those are one vendor's unconfigured RCB templates and they are the
  majority shape these functions meet.
- **`indexed` is never written as "true".** It defaults to true in the schema,
  812 of the corpus's 852 blocks omit it, 40 write `"false"` and **not one
  writes `"true"`**. Writing the default in to make it explicit would be this
  library changing bytes nobody asked it to change.
- **`confRev` starts at 1.** A9 measured that 1,014 of the corpus's 1,015
  control blocks carry a revision congruent to 1 modulo
  :data:`~py61850.scl.CONF_REV_STEP`; 1 is where that sequence begins, and a
  new block joins it at the start rather than one step in.

Do not read the names: `TR1_2414`'s `BRep01`, `BRep02` and `BRep03` are
**unbuffered** and its `URep01` is **buffered**.

## What a rename drags along, which the reference does not document

`updateReportControl`'s doc comment is one line -- *"Updates `ReportControl`
attributes and cross-referenced elements"* -- and does not say which. Its
sibling `updateSampledValueControl` lists two for a name change, `SMV.cbName`
and supervision, and a `ReportControl` has neither: it is carried over MMS on
the IED's own address, so it has no `Communication` element, and LGOS/LSVS
supervise GOOSE and sampled values rather than reports.

**It has subscribers, though, and this corpus has 58 of them.** `sel.scd`
holds 58 `ExtRef` elements with `serviceType="Report"` whose `srcCBName` names
a `ReportControl`, spread over 29 blocks, and A9's
:func:`~py61850.scl.find_control_block_subscription` finds every one. Renaming
a block without following them leaves 58 inputs bound to a control block that
no longer exists under that name. So :func:`update_report_control` re-points
them, and that is an ADDITION to a documented behaviour rather than a
difference from one -- the reference's declared return type, `SetAttributes[]`,
permits exactly this and its documentation neither requires nor forbids it.
See Q29.

## The stale-model hazard, in a shape A9 and A10 did not have

A9 and A10 removed elements, so a cached :class:`~py61850.scl.ControlBlock`
went on describing something that was gone. **This phase mostly creates and
updates**, and both are a different failure:

- `ControlBlock.__init__` copies `name`, `dat_set` and `conf_rev` off the
  element at construction, so after an :func:`update_report_control` a cached
  object reports the value the attribute USED to have while the tree carries
  the new one -- stale by disagreeing rather than by dangling;
- after a :func:`create_report_control` a cached `ln.control_blocks` does not
  list the new block at all -- stale by omission.

Nothing is invalidated automatically, for the reasons and the numbers in
:meth:`~py61850.scl.SclDocument.apply_edit`. Every capability here takes and
returns live ELEMENTS, so a caller working in elements is never stale. Q14 is
still A13's to answer; this module only adds two more shapes to it.
"""

from __future__ import annotations

from typing import List, NamedTuple, Optional
from xml.etree import ElementTree as ET

from .control_block import _is_control_block, update_dat_set
from .controls import CONTROL_BLOCK_TAGS
from .data_set import _limit, _namespace, _same_ns_children, _services_child
from .document import strip_ns
from .edit import EditRejected, Insert, SetAttributes
from .extref import _qualify, _same
from .ordering import may_contain, reference_for

#: The elements 61850-6 lets a `ReportControl` hang off. `tLN0` and `tLN` both
#: declare one; nothing else in the schema does.
REPORT_CONTROL_PARENTS = ("LN0", "LN")

#: Parents :func:`create_report_control` accepts INDIRECTLY, resolving each to
#: the first `LN0` beneath it -- the reference's *"in the later case first
#: `LN0` is picked"*.
INDIRECT_PARENTS = ("LDevice", "AccessPoint", "IED", "Server")


class MaxReportControl(NamedTuple):
    """What a `ConfReportControl` declares, and where it was found.

    ``max`` is how many `ReportControl` elements the scope takes in total and
    ``max_buf`` how many of them may be buffered. Either may be ``None``:
    `maxBuf` is ``use="optional"`` and **32 of the corpus's 58 IEDs declare
    none**, so an absent one is the common path rather than an edge.

    ``scope`` is ``"AccessPoint"`` or ``"IED"`` -- which element's `Services`
    carried the declaration -- exactly as
    :class:`~py61850.scl.MaxAttributes`'s is, so a refusal can say where the
    number came from.

    **The reference returns ``{max, maxBuf}`` with ``-1`` for "not
    declared"**, which conflates two different absences: an IED with no
    `ConfReportControl` at all, and one that declares `max` and no `maxBuf`.
    A10 had already chosen `None` for the first of those in `max_attributes`,
    and one convention across the two phases is worth the divergence. See Q29.
    """

    max: Optional[int]
    max_buf: Optional[int]
    scope: str


class ReportControlInstances(NamedTuple):
    """How many `ReportControl` elements a scope holds, split by `buffered`.

    The reference's `numberReportControlInstances` returns
    ``{bufInstances, unBufInstances}``; these are the same two numbers under
    names that say what they count. The module docstring argues at length that
    they count ELEMENTS, which is what the reference's own doc comment says
    despite what its name suggests.
    """

    buffered: int
    unbuffered: int

    @property
    def total(self) -> int:
        return self.buffered + self.unbuffered


# -- queries ----------------------------------------------------------------

def max_report_control(doc, element) -> Optional[MaxReportControl]:
    """What `Services/ConfReportControl` allows for ``element``'s scope.

    ``element`` is anything inside -- or being added to -- an `IED`: a logical
    node, an `LDevice`, an `AccessPoint` or the `IED` itself. The
    `AccessPoint`'s `Services` is read first and the `IED`'s second, which is
    the order the reference documents for all three of these declarations.

    **The AccessPoint half is unexercised by every file we have**, exactly as
    Q26 found for `ConfDataSet`: all 58 corpus `ConfReportControl` elements are
    IED-level, and not one of `mixed.scd`'s 24 AccessPoint-level `Services`
    carries one. It is implemented from the reference's documented behaviour
    and tested against built fixtures alone.

    ``None`` when no `ConfReportControl` governs ``element`` -- unconstrained,
    not zero, which is A8's rule that an unresolvable restriction is not a
    restriction.
    """
    if not isinstance(element, ET.Element):
        return None
    found = _services_child(doc, element, "ConfReportControl")
    if found is None:
        return None
    conf, scope, _owner = found
    return MaxReportControl(_limit(conf, "max"), _limit(conf, "maxBuf"), scope)


def number_report_control_instances(root) -> ReportControlInstances:
    """The `ReportControl` elements under ``root``, split by `buffered`.

    ``root`` is any element -- ``doc.root`` for the document, an `IED` or an
    `AccessPoint` for one scope. `buffered` defaults to **false** in the
    schema, and 8 corpus blocks omit it, so an absent attribute counts as
    unbuffered.

    **Counted in the document's own namespace.** Q27's measurement is why:
    127 elements named `DataSet` in the corpus belong to a vendor's private
    namespace, and a count by local name would let another tool's markup
    decide whether an edit is refused. Nothing under a `Private` is counted
    here for the same reason.
    """
    if not isinstance(root, ET.Element):
        return ReportControlInstances(0, 0)
    wanted = _namespace(root.tag) + "ReportControl"
    buffered = unbuffered = 0
    for element in root.iter():
        if element.tag != wanted:
            continue
        if element.get("buffered") == "true":
            buffered += 1
        else:
            unbuffered += 1
    return ReportControlInstances(buffered, unbuffered)


def can_add_report_control(doc, parent, buffered=False, new_instances=1) -> bool:
    """Whether ``new_instances`` more `ReportControl` elements fit in ``parent``.

    ``parent`` is where the blocks would go or anything above it -- an `LN`,
    an `LN0`, an `LDevice`, an `AccessPoint` or an `IED`. The limit is counted
    **in the scope that declared it**, which is Q26's rule applied to a second
    declaration: every `ReportControl` under the `AccessPoint` when the
    AccessPoint's `Services` carried the `ConfReportControl`, under the `IED`
    when the IED's did.

    ``buffered`` says which kind is being added, and it decides whether
    `maxBuf` is consulted at all. `max` governs the total either way.

    An IED that declares no `ConfReportControl`, or declares one with no
    usable `max`, is unconstrained and this answers ``True``.
    """
    if not isinstance(parent, ET.Element):
        return False
    if new_instances <= 0:
        return True
    found = _services_child(doc, parent, "ConfReportControl")
    if found is None:
        return True
    conf, _scope, owner = found
    counted = number_report_control_instances(owner)

    total = _limit(conf, "max")
    if total is not None and counted.total + new_instances > total:
        return False
    if buffered:
        buf = _limit(conf, "maxBuf")
        if buf is not None and counted.buffered + new_instances > buf:
            return False
    return True


# -- element creation -------------------------------------------------------

def _resolve_parent(doc, parent):
    """The `LN`/`LN0` a new block goes in, for a direct or indirect parent.

    The reference takes *"direct parent `LN`, `LN0` or indirect parents
    `LDevice`, `AccessPoint` or `IED`. In the later case first `LN0` is
    picked."* -- a wider input than anything A10 accepted, because an engineer
    adding a report to an IED means its first logical device's `LN0` and
    should not have to find it.
    """
    if not isinstance(parent, ET.Element):
        return None
    if strip_ns(parent.tag) in REPORT_CONTROL_PARENTS:
        return parent
    if strip_ns(parent.tag) not in INDIRECT_PARENTS:
        return None
    wanted = _namespace(parent.tag) + "LN0"
    for element in parent.iter():
        if element.tag == wanted:
            return element
    return None


def _name_clash(node, name):
    """A control block in ``node`` already called ``name``, or ``None``.

    **Every kind is checked, not only `ReportControl`.** The reference says
    only *"check for unique report control name"* without naming a scope, and
    what could not be established there is settled from the standard instead:
    :func:`~py61850.scl.control_block_obj_ref` builds a 61850-7-2 reference
    that carries no element type, so two blocks of different kinds sharing one
    name inside a logical node produce the SAME object reference and nothing
    can tell the pair apart afterwards. No logical node in the corpus holds
    two control blocks of one name. See Q29.
    """
    for child in node:
        if strip_ns(child.tag) in CONTROL_BLOCK_TAGS and child.get("name") == name:
            return child
    return None


def _has_data_set(node, name) -> bool:
    return any(data_set.get("name") == name
               for data_set in _same_ns_children(node, "DataSet"))


def create_report_control(doc, parent, name, desc=None, dat_set=None,
                          rpt_id=None, conf_rev="1", buffered=None,
                          buf_time=None, indexed=None, intg_pd=None,
                          trg_ops=None, opt_fields=None, instances=None,
                          force=False) -> List:
    """The edit that adds a new `ReportControl`, with its required `OptFields`.

    ``parent`` is an `LN` or `LN0`, or an `LDevice`, `AccessPoint` or `IED`
    whose first `LN0` is then picked -- see :func:`_resolve_parent`. The
    element is placed by A7's :func:`~py61850.scl.reference_for`.

    ``trg_ops`` and ``opt_fields`` are mappings of attribute name to value,
    written onto a `TrgOps` and an `OptFields` child. **An `OptFields` is
    always written** because the schema requires one; a `TrgOps` only when
    asked for. ``instances`` writes `RptEnabled@max`, and `indexed="false"`
    forces it to ``"1"`` -- the reference's own rule, and the reason
    `TR1_2414`'s declared 22 instances are really 4.

    **`name` is required here, where the reference's is optional.** Filling one
    in means allocating a unique one, which is A17's `uniqueElementName` and
    whose policy A17's own row calls a likely divergence; a `create` that
    invented a name now and changed it at A17 is worse than one that asks.
    Q27 recorded the same decision for `create_data_set`, and a control block
    is not different from a dataset in this respect -- `tControl@name` is
    ``use="required"`` exactly as `DataSet@name` is.

    ``conf_rev`` defaults to ``"1"``, where the corpus's revision sequence
    starts; ``force`` skips the `ConfReportControl` guard and the `datSet`
    resolution check, as the reference's `skipCheck` does. The name-collision
    and content-model checks are not skippable: they are what makes the result
    a `ReportControl` at all.

    Returns a list holding one :class:`~py61850.scl.Insert` (Q23).

    Raises :class:`~py61850.scl.EditRejected` if ``parent`` cannot hold a
    `ReportControl`, if ``name`` is empty or already used, if ``conf_rev`` is
    empty, if ``dat_set`` names no `DataSet` in the same logical node, or if
    the declared maximum is reached and ``force`` is not set.
    """
    node = _resolve_parent(doc, parent)
    if node is None or not may_contain(node.tag, _namespace(node.tag) + "ReportControl"):
        shown = (strip_ns(parent.tag) if isinstance(parent, ET.Element)
                 else type(parent).__name__)
        raise EditRejected(
            f"{shown} may not hold a ReportControl and no LN0 was found "
            f"beneath it; expected one of "
            f"{', '.join(REPORT_CONTROL_PARENTS + INDIRECT_PARENTS)}")
    if not name:
        raise EditRejected(
            "a ReportControl needs a name -- it is required by the schema and "
            "unique within its logical node; allocating one is A17's")
    if not conf_rev:
        raise EditRejected(
            "ReportControl@confRev is use=\"required\" in 61850-6; it cannot "
            "be empty")
    clash = _name_clash(node, name)
    if clash is not None:
        raise EditRejected(
            f"a {strip_ns(clash.tag)} named {name!r} is already in this "
            f"{strip_ns(node.tag)}")
    if dat_set and not force and not _has_data_set(node, dat_set):
        raise EditRejected(
            f"no DataSet named {dat_set!r} is in this {strip_ns(node.tag)}; "
            f"datSet is an xs:keyref and resolves in the logical node")
    is_buffered = buffered == "true"
    if not force and not can_add_report_control(doc, node, buffered=is_buffered):
        limit = max_report_control(doc, node)
        counted = number_report_control_instances(
            _services_child(doc, node, "ConfReportControl")[2])
        raise EditRejected(
            f"this {limit.scope} already holds {counted.total} ReportControl "
            f"elements ({counted.buffered} buffered), which is the maximum "
            f"its {limit.scope} ConfReportControl declares "
            f"(max={limit.max}, maxBuf={limit.max_buf})")

    element = ET.Element(_qualify(doc, "ReportControl"))
    for attribute, value in (("desc", desc), ("name", name),
                             ("datSet", dat_set), ("intgPd", intg_pd),
                             ("rptID", rpt_id), ("confRev", conf_rev),
                             ("buffered", buffered), ("bufTime", buf_time),
                             ("indexed", indexed)):
        if value is not None:
            element.set(attribute, value)

    if trg_ops:
        child = ET.SubElement(element, _qualify(doc, "TrgOps"))
        for attribute, value in trg_ops.items():
            child.set(attribute, value)
    # Required by tReportControl, and written empty when nothing was asked
    # for -- which is what 720 of the corpus's blocks carry.
    fields = ET.SubElement(element, _qualify(doc, "OptFields"))
    for attribute, value in (opt_fields or {}).items():
        fields.set(attribute, value)
    if instances is not None or indexed == "false":
        enabled = ET.SubElement(element, _qualify(doc, "RptEnabled"))
        enabled.set("max", "1" if indexed == "false" else instances)

    return [Insert(node, element, reference_for(node, element.tag))]


# -- edit checks ------------------------------------------------------------

def update_report_control(doc, edit, ignore_supervision=True) -> List:
    """``edit`` corrected: the dataset, the revision, and every subscriber.

    ``edit`` is a :class:`~py61850.scl.SetAttributes` on a `ReportControl`.
    What comes back is everything the caller should apply, input edit first:

    1. the edit itself, as given;
    2. whatever a `datSet` change implies -- the exclusive `DataSet` renamed
       with it, or `confRev` moved when the block is genuinely re-pointed.
       That is A9's :func:`~py61850.scl.update_dat_set`, called rather than
       reimplemented, so a block re-pointed through this function and one
       re-pointed directly behave identically;
    3. `srcCBName` re-pointed on every `ExtRef` subscribed to the block, when
       `name` changes. The module docstring carries the 58 corpus cases and
       the argument that this is an addition to the reference rather than a
       difference from it;
    4. `RptEnabled@max` reset to ``"1"`` when `indexed` is set to `"false"`,
       which is the reference's documented rule for that attribute.

    ``ignore_supervision`` is carried for the same reason
    :func:`~py61850.scl.unsubscribe`'s is and refuses `False` the same way,
    even though a `ReportControl` has no LGOS or LSVS of its own: A14 decides
    what supervision means for every control block at once, and a function
    that quietly accepted `False` here would be claiming to have done
    something.

    Raises :class:`~py61850.scl.EditRejected` if ``edit`` is not a
    `SetAttributes` on a `ReportControl`, if it would clear a required
    attribute, or if the new name is already used in the logical node.
    """
    if not ignore_supervision:
        raise EditRejected(
            "subscription supervision is not written yet; "
            "ignore_supervision=False has nothing to turn off")
    if not isinstance(edit, SetAttributes):
        raise EditRejected(
            "update_report_control takes a SetAttributes, not "
            f"{type(edit).__name__}")
    control = edit.element
    if not _is_control_block(control) or strip_ns(control.tag) != "ReportControl":
        raise EditRejected(
            f"{strip_ns(control.tag) if isinstance(control, ET.Element) else type(control).__name__}"
            f" is not a ReportControl")

    edits: List = list(update_dat_set(doc, edit))
    edits.extend(_rename_edits(doc, control, edit))
    edits.extend(_indexed_edits(control, edit))
    return edits


def _rename_edits(doc, control, edit) -> List:
    """`srcCBName` re-pointed on every subscriber, when `name` changes."""
    if "name" not in edit.attributes:
        return []
    wanted = edit.attributes["name"]
    if _same(wanted, control.get("name")):
        return []
    if not wanted:
        raise EditRejected(
            f"{strip_ns(control.tag)}@name is required by the schema; it "
            f"cannot be cleared")
    node = doc.parent_of(control)
    if node is not None:
        clash = _name_clash(node, wanted)
        if clash is not None and clash is not control:
            raise EditRejected(
                f"renaming {control.get('name')!r} to {wanted!r} would "
                f"collide with the {strip_ns(clash.tag)} already named "
                f"{wanted!r} in the same logical node")
    # Deferred: `control_block` imports nothing from here, and this is the one
    # call back into A9's subscriber sweep.
    from .control_block import find_control_block_subscription
    return [SetAttributes(ext_ref, {"srcCBName": wanted})
            for ext_ref in find_control_block_subscription(doc, control)]


def _indexed_edits(control, edit) -> List:
    """`RptEnabled@max` reset to 1 when `indexed` is turned off."""
    if edit.attributes.get("indexed") != "false":
        return []
    out = []
    for child in control:
        if strip_ns(child.tag) == "RptEnabled" and child.get("max") not in (None, "1"):
            out.append(SetAttributes(child, {"max": "1"}))
    return out
