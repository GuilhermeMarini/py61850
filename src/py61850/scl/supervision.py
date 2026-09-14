# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""Supervising a subscription: the `LGOS` and `LSVS` logical nodes.

    from py61850.scl import SclDocument, Supervision, instantiate_supervision

    doc = SclDocument.parse("station.scd")
    sup = Supervision(subscriber=ied, control_block=gse_control)
    doc.apply_edit(instantiate_supervision(doc, sup))

**A subscription is silent about itself.** An `ExtRef` binds an input to a
published attribute and nothing in it says whether the publisher is still
publishing. 61850-7-4 answers that with two logical node classes -- `LGOS`
watches one `GSEControl`, `LSVS` one `SampledValueControl` -- each carrying
the object reference of the block it watches in a single setting value,
``<DOI name="GoCBRef"><DAI name="setSrcRef"><Val>``. Writing that value is
what this module does; A13's :mod:`~py61850.scl.ied` already blanks it when
the publisher goes away.

**One supervision logical node per supervised control block, and the corpus
says so exactly.** Across the three reference station exports, 36 of the 43
IEDs that subscribe to GOOSE hold precisely as many `LGOS` as they have
distinct subscribed `GSEControl` elements -- equality, not approximation. The
remaining seven hold more, which is the same thing seen from the other side:
a vendor allocates spare supervision nodes ahead of time. There are 600
supervision logical nodes in the corpus, 549 of them pointed at something.

## The two idle shapes, and why both are "an available location"

The reference's `canInstantiateSubscriptionSupervision` asks, among other
things, *"whether there is an available location for the control block
reference to be stored"*. Two unrelated vendors answer that question with two
different spellings of the same idea, and **both are free slots**:

==========================================  =====  ========================
shape                                       count  written by
==========================================  =====  ========================
``<DAI name="setSrcRef"><Val/></DAI>``         26  SEL (24), Siemens (2)
``<DOI name="GoCBRef"/>`` -- no `DAI` at all   25  Siemens
==========================================  =====  ========================

The second looks like a missing location and is not. Counting spare slots per
IED settles it: `siemens.scd`'s six `AL1x` relays hold 8 `LGOS` for 7
subscribed control blocks, its four `LT*` relays 8 for 3, and `QPC1_TR1_UPC1`
10 for 9 -- **27 spare, against 25 absent plus 2 empty.** Every unbound
Siemens supervision node is a slot its tool left ready, and the `DOType` those
nodes point at does declare `setSrcRef`. Reading them as unusable would make
this module build a new logical node beside five empty ones.

So a supervision logical node is FREE when its reference value is empty or
missing at any depth -- no `DOI`, no `DAI`, no `Val`, or a `Val` with no text
-- and :func:`instantiate_supervision` fills in whichever of the four is
absent. What already exists is never rebuilt: an existing `Val` has its text
SET, which is Q30's rule for a `P` element applied one section down, and for
the same reason -- a `Remove` and an `Insert` cannot promise to put an element
back where it was, and an element rebuilt loses the attributes the file gave
it.

## `valKind` and `valImport` are written in the TEMPLATES, not on the instance

`isSrcRefEditable` asks whether an SCL tool may write the value at all.
61850-6 puts that in two places: `tDAI` may carry `valKind` and `valImport`,
and so may the `DA` of the `DOType` the instance points at, with the instance
overriding the type. **Reading only the instance gets the wrong answer**:
not one of the corpus's 575 supervision `setSrcRef` instances writes
`valKind`, while the `DOType` behind them writes it 443 times.

Resolved through the type, the reference's rule -- editable when `valKind` is
`RO` or `Conf` and `valImport` is `true` -- reads:

================  =======  ========  =========  ========  =============
file              RO/true  RO/false  Set/false  Set/true  editable
================  =======  ========  =========  ========  =============
`sel.scd`              39        34        157         1  39 of 231
`mixed.scd`           266         2          0         0  266 of 268
`siemens.scd`         101         0          0         0  101 of 101
**total**             406        36        157         1  **406 of 600**
================  =======  ========  =========  ========  =============

**Ours refuses only what a file actually forbids, and that is a divergence.**
Of the 194 the strict reading rejects, **not one carries an explicit refusal**:
157 declare neither attribute at either level, and the other 37 declare
`valKind="RO"` and leave `valImport` absent. Every rejection is produced by
the schema's own defaults -- `valKind="Set"`, `valImport="false"` -- rather
than by anything a vendor wrote. Under the strict reading this library cannot
fill a single one of `sel.scd`'s 24 free slots, and would also decline to
touch 168 values SEL's own tool had already written there.

The reading taken is A8's precedent extended one step: *a restriction that
cannot be resolved is not a restriction*, and neither is one nobody wrote.
:func:`is_src_ref_editable` still answers ``False`` for a file that declares
`valImport="false"` or a `valKind` outside `RO`/`Conf`; on this corpus it
answers ``True`` 600 times out of 600. See Q32.

## The `Services` limit counts BOUND references, not logical nodes

`Services/SupSubscription` declares `maxGo` and `maxSv`. 51 of the 58 corpus
IEDs carry one; `maxGo` is 16, 64, 128 or 150 and `maxSv` is 0 or 60, and
**43 of the 51 declare `maxSv="0"`** -- most of these devices support no
sampled-value supervision at all, which is why the refusal path for `LSVS` has
real vendor data behind it where the one for `LGOS` has a single witness.

That witness decides how the limit is read. **`QPC4_TR1_UPC1` in `sel.scd`
carries 26 `LGOS` against its own `maxGo="16"`** -- and only 10 of those 26
are pointed at anything. Counting logical nodes makes a vendor violate its own
export; counting bound references does not. That is Q26 §1's argument for
`maxAttributes` in the same shape with a sharper witness, so the count here is
of supervision nodes carrying a non-empty reference.

An IED that declares no `SupSubscription` -- 7 of 58 -- is **unconstrained**,
on Q26 §3's precedent, and both guards answer accordingly. No corpus IED puts
a `SupSubscription` on an `AccessPoint`, which leaves the reference's
AccessPoint-first rule as unexercised here as Q26 found it for `ConfDataSet`;
the shared :func:`~py61850.scl.data_set._services_child` implements it anyway.

## A new logical node is placed by its siblings, and seven IEDs have none

Creating an `LGOS` needs three things the schema will not supply: a parent
`LDevice`, an `lnType`, and an `inst`. The first two are read off an existing
supervision node of the same class in the same IED, which is unambiguous
everywhere in the corpus -- **no IED holds one supervision class in more than
one `LDevice`**, though `mixed.scd` puts GOOSE and sampled-value supervision
in two different ones (`ComSupervision_GOOSE` and `ComSupervision_SV`), so the
`LDevice` must be chosen per class rather than per IED.

**Seven IEDs subscribe to GOOSE and hold no supervision node at all**, so
there is nothing to read and nothing to copy. Inventing an `lnType` would mean
writing an `LNodeType` into `DataTypeTemplates`, which is `importLNodeType`'s
work in a later phase -- the same dependency that kept `insert_ied` out of
A13. Those are refused, naming what is missing, and a caller who knows may
pass ``ln_type`` and ``parent`` instead.

`inst` is allocated as the lowest value in 1..99 not used by ANY logical node
of that class in the parent `LDevice`, **ignoring `prefix`**. Ignoring it is
deliberate: `tLN`'s identity is `prefix` + `lnClass` + `inst`, so a
prefix-aware allocator would find more free numbers, and SEL writes a prefix
naming the SUPERVISED device (`Q1_TR1_UPC1` on an `LGOS` in another relay) --
a vendor convention this package takes no view on. Scanning by class alone
cannot collide. Corpus `inst` values run 1 to 31 and are not dense: 26 of the
59 (IED, class) blocks have a hole in them, so allocation fills holes rather
than appending. **Allocation policy is A17's**, and this is the provisional
rule its phase takes over; ``fixed_ln_inst`` overrides it.

## What the seven flags reach, and what each `False` means

**Seven functions carry `ignore_supervision`, not the six four places used to
say**, and since A14b six of them do the work when asked. The default stays
``True`` on all seven: flipping it to the reference's ``false`` would change
what ``subscribe(doc, conn)`` writes into a file for every existing caller,
which is the meaning-change A18's MINOR rule forbids. Q32 §9 settled that and
it is not re-argued here.

=====================================  ====================================
function                               what ``ignore_supervision=False`` does
=====================================  ====================================
:func:`~py61850.scl.subscribe`         instantiates the supervision each
                                       connection implies, skipping the
                                       already-supervised and refusing what
                                       cannot be supervised
:func:`~py61850.scl.unsubscribe`       blanks the supervision whose LAST
                                       `ExtRef` in that subscriber is going
:func:`~py61850.scl.remove_control_block`  blanks every supervision naming
                                       the block being removed
:func:`~py61850.scl.remove_data_set`   through the `unsubscribe` it performs
:func:`~py61850.scl.remove_fcda`       through the `unsubscribe` it performs,
                                       and almost always nothing: a member
                                       only empties a pair when it unbinds
                                       that pair's last `ExtRef`, and 531 of
                                       566 pairs have more than one
:func:`~py61850.scl.update_sampled_value_control`  re-points every `SvCBRef`
                                       naming the block, when `name` changes
:func:`~py61850.scl.update_report_control`  **nothing, and that is not an
                                       oversight** -- 61850-7-4 defines no
                                       report equivalent of `LGOS`, so the
                                       flag is vacuous in both positions and
                                       ``False`` is a documented no-op rather
                                       than a refusal
=====================================  ====================================

**Three expansions, in this module, and the other five modules call them.**
That is Q25's shape -- one expansion owned in one place, with a deferred
import for the back-edge -- and the reason is the one Q25 gives: two paths
that end one subscription differently is drift nobody notices until a file is
wrong. `_expand_subscribe_supervision` and `_expand_unsubscribe_supervision`
are the two halves the flags reach; `_expand_control_block_supervision` is the
third, and it answers *this block is gone* rather than *this subscription
ended* -- the sweep A13's :func:`~py61850.scl.remove_ied` already performs, so
that removing an IED and removing one of its control blocks cannot leave
different supervision behind.

**There is still no batch driver.** The reference has two --
`insertSubscriptionSupervisions`, driven by an array of subscribe edits, and
`removeSubscriptionSupervision`, driven by an array of `ExtRef` elements --
because its API is edit-in, edit-out: the `ExtRef` inside an unapplied
`SetAttributes` does not yet carry `srcCBName`, so the control block has to be
looked up from a record of six strings, which is what
`findControlBlockBySrcAttributes` exists for. **Ours never has that problem**:
:class:`~py61850.scl.Connection` carries the control block as an element,
before anything is applied. So the driver and the record lookup are machinery
for a constraint this library does not have, and the wiring does the work
inline. See Q32.

**What a compound edit cannot see, :class:`_Batch` remembers.** Every question
here is asked of the document, and inside one `subscribe` call the document is
out of date until the caller applies the whole list -- so two supervisions
planned together would take the same free slot, allocate the same `inst` and
spend the same last place under a `Services` limit. 8 of the corpus's 59
(IED, class) blocks hold exactly one free slot. The reference keeps a
`usedSupervisions` set for this and :func:`~py61850.scl.subscribe` already
keeps `created_inputs` for the identical hazard one level down. Q33.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, NamedTuple, Optional
from xml.etree import ElementTree as ET

from .control_block import (
    _is_control_block,
    control_block_obj_ref,
    find_control_block_subscription,
)
from .data_set import _limit, _services_child
from .document import strip_ns
from .edit import EditRejected, Insert, Remove, SetTextContent
from .extref import source_control_block
from .ordering import reference_for

#: The logical node classes that supervise a subscription. `tLN@lnClass` is an
#: enumeration and these are its two supervision members: `LGOS` watches a
#: GOOSE control block, `LSVS` a sampled-value one.
SUPERVISION_LN_CLASSES = ("LGOS", "LSVS")

#: The supervision data objects whose `setSrcRef` holds an object reference
#: with an IED name concatenated into it. `GoID`, `Addr`, `VlanID`, `VlanPri`
#: and `AppID` sit beside these and are deliberately absent -- they are copies
#: of the publisher's identifiers and address, not references. See
#: :mod:`py61850.scl.ied`, which is the half that rewrites them.
SUPERVISION_REFERENCE_DOS = ("GoCBRef", "SvCBRef", "DatSet")

# Which class supervises which kind of control block. A `ReportControl` is
# absent because nothing supervises one: 61850-7-4 defines no report
# equivalent of `LGOS`, which is why `update_report_control`'s own
# `ignore_supervision` has nothing to do either way.
SUPERVISED_CONTROL_BLOCKS = {
    "GSEControl": "LGOS",
    "SampledValueControl": "LSVS",
}

# The data object each class keeps its supervised reference in.
_REFERENCE_DO = {"LGOS": "GoCBRef", "LSVS": "SvCBRef"}

# The `SupSubscription` attribute that limits each class.
_LIMIT_ATTRIBUTE = {"LGOS": "maxGo", "LSVS": "maxSv"}

#: The range 61850-6 allows for `tLN@inst`, and therefore the range an
#: allocated instance number is drawn from. The reference documents the same
#: bounds for its own generator.
LN_INST_RANGE = (1, 99)

class MaxSupervision(NamedTuple):
    """How many subscriptions an IED declares it can supervise.

    ``max_go`` and ``max_sv`` are `SupSubscription`'s two limits, each
    ``None`` when that attribute is absent or unparseable -- which means
    unconstrained, not zero. ``0`` is a real and common answer: 43 of the 51
    corpus IEDs that declare a `SupSubscription` declare ``maxSv="0"``.

    ``scope`` is ``"AccessPoint"`` or ``"IED"`` -- which element's `Services`
    carried the declaration, so a refusal can say where the number came from.
    The two fields and the scope follow :class:`~py61850.scl.MaxAttributes`
    and :class:`~py61850.scl.MaxReportControl`; the reference exports no
    equivalent for this element, and Q29's argument for ``None`` over a ``-1``
    sentinel applies unchanged.
    """

    max_go: Optional[int]
    max_sv: Optional[int]
    scope: str


@dataclass(frozen=True)
class Supervision:
    """One supervision to instantiate: who subscribes, and to what.

    ``subscriber`` is the subscribing `IED`; ``control_block`` is the
    `GSEControl` or `SampledValueControl` it should watch.

    **The reference's record has a union in it and this one does not.** Its
    `Supervision.subscriberIedOrLn` is *"the subscriber IED, or the
    `LGOS`/`LSVS` itself"*, which is two different questions -- *find me a
    place* and *use this place* -- wearing one field. Here the IED is required
    and naming a particular logical node is the ``supervision_ln`` option,
    beside the other placement options it belongs with. A8's
    :class:`~py61850.scl.Connection` is the precedent for the record; the
    union is not, and a field that is one of two things is one a caller has to
    type-test before it can act.
    """

    __slots__ = ("subscriber", "control_block")

    subscriber: ET.Element
    control_block: ET.Element


# -- what is what -----------------------------------------------------------

def _namespace(tag) -> str:
    if not isinstance(tag, str) or not tag.startswith("{"):
        return ""
    return tag[:tag.index("}") + 1]


def _qualified(doc, local_name: str) -> str:
    return _namespace(doc.root.tag) + local_name


def _is(element, local_name) -> bool:
    return (isinstance(element, ET.Element)
            and strip_ns(element.tag) == local_name)


def _describe(element) -> str:
    if not isinstance(element, ET.Element):
        return type(element).__name__
    return strip_ns(element.tag) if isinstance(element.tag, str) else "a comment"


def _own(parent, local_name: str):
    """Every descendant of ``parent`` with this name in ``parent``'s own
    namespace -- never :func:`~py61850.scl.iter_local`, for the reason
    :mod:`py61850.scl.ied` records: vendors shadow SCL's element names."""
    return parent.iter(_namespace(parent.tag) + local_name)


def _same_ns_children(parent, local_name):
    wanted = _namespace(parent.tag) + local_name
    return [child for child in parent if child.tag == wanted]


def _ancestor(doc, element, local_name):
    node = doc.parent_of(element)
    while node is not None:
        if _is(node, local_name):
            return node
        node = doc.parent_of(node)
    return None


def supervision_ln_class(control_block) -> Optional[str]:
    """``"LGOS"``, ``"LSVS"``, or ``None`` -- the class that supervises this
    control block.

    ``None`` for a `ReportControl`, which nothing supervises, and for anything
    that is not a control block at all. The reference's `supervisionLnClass`
    takes a whole `Supervision` record and returns one of two strings with no
    third answer; taking the element alone lets a caller ask the question
    before it has a record, which is when a pane actually asks it.
    """
    if not isinstance(control_block, ET.Element):
        return None
    return SUPERVISED_CONTROL_BLOCKS.get(strip_ns(control_block.tag))


def _is_supervision_ln(element) -> bool:
    return (_is(element, "LN")
            and element.get("lnClass") in SUPERVISION_LN_CLASSES)


def _supervision_lns(doc, ied, ln_class=None) -> List[ET.Element]:
    """Every supervision logical node inside ``ied``, in document order."""
    out = []
    for node in _own(ied, "LN"):
        if not _is_supervision_ln(node):
            continue
        if ln_class is not None and node.get("lnClass") != ln_class:
            continue
        out.append(node)
    return out


# -- the setting value ------------------------------------------------------

def _src_ref_path(doc, supervision_ln):
    """``(doi, dai, val)`` for this node's reference setting, each ``None``
    when the file does not carry it.

    All four depths occur: a node with no `DOI`, one whose `DOI` is empty --
    25 in `siemens.scd` -- one with a `DAI` and no `Val`, and the ordinary
    full path. `DAI` is looked for anywhere under the `DOI` rather than as a
    direct child, because 61850-6 lets an `SDI` nest between them.
    """
    ln_class = supervision_ln.get("lnClass")
    wanted = _REFERENCE_DO.get(ln_class)
    if wanted is None:
        return None, None, None
    namespace = _namespace(supervision_ln.tag)
    doi = next((child for child in supervision_ln
                if child.tag == namespace + "DOI"
                and child.get("name") == wanted), None)
    if doi is None:
        return None, None, None
    dai = next((node for node in doi.iter(namespace + "DAI")
                if node.get("name") == "setSrcRef"), None)
    if dai is None:
        return doi, None, None
    val = next((child for child in dai
                if child.tag == namespace + "Val"), None)
    return doi, dai, val


def _supervised_reference(doc, supervision_ln) -> str:
    """The object reference this node watches, or ``""`` when it watches
    nothing -- which covers all four ways the file can leave it out."""
    _doi, _dai, val = _src_ref_path(doc, supervision_ln)
    if val is None:
        return ""
    return (val.text or "").strip()


def _supervision_values(doc, do_names=SUPERVISION_REFERENCE_DOS):
    """Every `Val` holding a supervision object reference, with its text.

    Yields ``(val, text)``. Used by :mod:`py61850.scl.ied` for the rename and
    removal sweeps, which want all of :data:`SUPERVISION_REFERENCE_DOS`, and
    by :func:`_repoint_supervision` here, which narrows ``do_names`` to the
    one data object that can hold the reference being re-pointed. Narrowing
    matters: a `DataSet` and a control block named alike in one logical node
    build the same object reference, and re-pointing a control block must not
    follow a `DatSet` value that happens to read the same.
    """
    namespace = _namespace(doc.root.tag)
    dai_tag, val_tag = namespace + "DAI", namespace + "Val"
    for node in doc.root.iter(namespace + "LN"):
        if node.get("lnClass") not in SUPERVISION_LN_CLASSES:
            continue
        for doi in node:
            if (doi.tag != namespace + "DOI"
                    or doi.get("name") not in do_names):
                continue
            for dai in doi.iter(dai_tag):
                if dai.get("name") != "setSrcRef":
                    continue
                for val in dai:
                    if val.tag == val_tag:
                        yield val, (val.text or "").strip()


def _retext(element, text: Optional[str]) -> SetTextContent:
    """A :class:`~py61850.scl.SetTextContent` that keeps the element's own
    leading and trailing whitespace.

    An object reference and an `IEDName` are both types derived from
    ``xs:normalizedString``, so no corpus file indents one -- but a file that
    did would otherwise have its indentation eaten by a rename, and the round
    trip is what this project measures itself on.

    **It lives here because three call sites now write a supervision value's
    text** -- A13's IED rename, this module's instantiation and this module's
    re-point -- and two of them disagreeing about whitespace is the drift Q25
    and Q23 were written about. A13 wrote it in :mod:`py61850.scl.ied`, which
    imports this module; moving it down is the direction the import graph
    already runs.
    """
    raw = element.text or ""
    if text is None:
        return SetTextContent(element, None)
    lead = raw[:len(raw) - len(raw.lstrip())]
    trail = raw[len(raw.rstrip()):]
    return SetTextContent(element, lead + text + trail)


# -- may an SCL tool write it -----------------------------------------------

def _declared_val_flags(doc, supervision_ln):
    """``(valKind, valImport)`` as the DOCUMENT declares them, instance over
    type, each ``None`` when neither level says anything.

    The `DAI` wins over the `DA` of the `DOType`, which is 61850-6's own
    precedence. Reading only the `DAI` is what makes the strict rule look
    unresolvable: no corpus `DAI` writes `valKind`, and the `DOType` behind
    them writes it 443 times.
    """
    _doi, dai, _val = _src_ref_path(doc, supervision_ln)
    kind = dai.get("valKind") if dai is not None else None
    imp = dai.get("valImport") if dai is not None else None
    if kind is not None and imp is not None:
        return kind, imp

    wanted = _REFERENCE_DO.get(supervision_ln.get("lnClass"))
    ln_type = supervision_ln.get("lnType")
    da = _template_da(doc, ln_type, wanted, "setSrcRef")
    if da is not None:
        kind = kind if kind is not None else da.get("valKind")
        imp = imp if imp is not None else da.get("valImport")
    return kind, imp


def _template_da(doc, ln_type, do_name, da_name):
    """The `DA` declaration behind ``lnType``/``do_name``/``da_name``.

    Walked straight over `DataTypeTemplates` rather than through
    :attr:`SclDocument.templates`, for two reasons: the pool's
    :class:`~py61850.scl.AttributeSpec` carries `fc` and `bType` but not
    `valKind` or `valImport`, and building it would warm a cache an edit then
    has to invalidate (Q14).
    """
    if not ln_type or not do_name:
        return None
    namespace = _namespace(doc.root.tag)
    templates = next((child for child in doc.root
                      if child.tag == namespace + "DataTypeTemplates"), None)
    if templates is None:
        return None
    ln_node_type = next((child for child in templates
                         if child.tag == namespace + "LNodeType"
                         and child.get("id") == ln_type), None)
    if ln_node_type is None:
        return None
    do = next((child for child in ln_node_type
               if child.tag == namespace + "DO"
               and child.get("name") == do_name), None)
    if do is None or not do.get("type"):
        return None
    do_type = next((child for child in templates
                    if child.tag == namespace + "DOType"
                    and child.get("id") == do.get("type")), None)
    if do_type is None:
        return None
    return next((child for child in do_type
                 if child.tag == namespace + "DA"
                 and child.get("name") == da_name), None)


def is_src_ref_editable(doc, supervision_ln) -> bool:
    """Whether an SCL tool may write this node's `setSrcRef` value.

    61850-6 lets `valKind` and `valImport` be declared on the `DAI` or on the
    `DA` of its `DOType`, the instance overriding the type, and the
    reference's rule is that a value is editable when `valKind` is `RO` or
    `Conf` and `valImport` is `true`.

    **Ours refuses only an explicit refusal.** A file that declares nothing
    gets ``True`` rather than the schema default's ``False``, because every
    one of the 194 corpus nodes the strict reading rejects is rejected by a
    default rather than by anything a vendor wrote -- and among them are all
    24 of `sel.scd`'s free slots and 168 values SEL's own tool had already
    put there. The module docstring carries the measurement and Q32 the
    argument. ``False`` for anything that is not a supervision logical node.
    """
    if not _is_supervision_ln(supervision_ln):
        return False
    kind, imp = _declared_val_flags(doc, supervision_ln)
    if kind is not None and kind not in ("RO", "Conf"):
        return False
    if imp is not None and imp != "true":
        return False
    return True


# -- the Services limit -----------------------------------------------------

def max_supervision(doc, element) -> Optional[MaxSupervision]:
    """How many subscriptions the IED holding ``element`` may supervise.

    ``element`` is an `IED`, an `AccessPoint` or anything inside one. The
    `AccessPoint`'s `Services` is read before the `IED`'s, which is the order
    the reference documents and which no corpus file exercises -- 0 of 58 IEDs
    put a `SupSubscription` on an `AccessPoint`. **An `IED` passed directly
    can only ever find its own declaration**, since it is not inside any of
    its access points; the guards here ask from the logical node the reference
    will be stored in, which is inside one.

    ``None`` when no `SupSubscription` governs it, which means unconstrained
    and not zero; 7 of the 58 corpus IEDs are in that state.
    """
    if not isinstance(element, ET.Element):
        return None
    found = _services_child(doc, element, "SupSubscription")
    if found is None:
        return None
    conf, scope, _owner = found
    return MaxSupervision(_limit(conf, "maxGo"), _limit(conf, "maxSv"), scope)


def _bound_supervisions(doc, scope, ln_class) -> int:
    """How many supervision nodes of this class inside ``scope`` watch
    something.

    ``scope`` is the element whose `Services` declared the limit -- an `IED`
    or an `AccessPoint` -- which is Q26 §2's rule that a declaration governs
    what it is attached to.

    **Bound references, not logical nodes**, and one corpus IED is why:
    `QPC4_TR1_UPC1` carries 26 `LGOS` against its own ``maxGo="16"``, of which
    10 are pointed at anything. Counting elements would have this library
    refuse an edit because the vendor over-allocated; counting references
    agrees with the file. Q26 §1 reached the same answer for `maxAttributes`.
    """
    return sum(1 for node in _supervision_lns(doc, scope, ln_class)
               if _supervised_reference(doc, node))


# -- planning an instantiation ----------------------------------------------

class _Plan(NamedTuple):
    """Where the reference is going, resolved before any edit is built."""

    ln_class: str
    obj_ref: str
    supervision_ln: Optional[ET.Element]   # the node to reuse, or None
    parent: Optional[ET.Element]           # the LDevice a new node goes in
    ln_type: Optional[str]
    inst: Optional[str]


class _Batch:
    """What one compound edit has already spoken for.

    **Every question this module asks is asked of the document, and inside one
    compound edit the document is out of date** -- nothing is applied until the
    caller applies the whole list. So two supervisions planned in one call see
    the same free slot, allocate the same `inst`, and count the same bound
    references against the same `Services` limit. Without this record the
    second `SetTextContent` would overwrite the first on one `Val` and one of
    the two supervisions would be lost with no error anywhere.

    It is the same hazard :func:`~py61850.scl.subscribe` already keeps
    ``created_inputs`` for one level down -- two connections into one logical
    node must share one `Inputs` -- and the reference keeps its own
    ``usedSupervisions`` set for exactly this. Measured, it is not an edge: 8
    of the corpus's 59 (IED, class) blocks hold exactly one free slot, and one
    IED subscribes to 33 distinct control blocks.

    ``None`` everywhere means "not in a batch", which is what a single
    :func:`instantiate_supervision` call is, and then nothing is recorded.
    """

    __slots__ = ("nodes", "insts", "bound")

    def __init__(self):
        self.nodes = set()      # id() of supervision LNs this call has filled
        self.insts = {}         # id(LDevice) -> the inst strings it has taken
        self.bound = {}         # (id(scope), ln_class) -> references it added


def _ied_of(doc, element):
    if _is(element, "IED"):
        return element
    return _ancestor(doc, element, "IED")


def _free_inst(doc, parent, ln_class, fixed_ln_inst, batch=None) -> str:
    """An unused `inst` for a new logical node of ``ln_class`` in ``parent``.

    Scanned by `lnClass` alone -- `prefix` is ignored, so the answer can never
    collide with an existing node whatever prefix it carries. The module
    docstring says why that is deliberate rather than lazy, and that
    allocation policy belongs to a later phase.

    ``batch`` adds the instances an unapplied compound edit has already taken,
    which the document cannot show; see :class:`_Batch`.
    """
    used = {node.get("inst") for node in _same_ns_children(parent, "LN")
            if node.get("lnClass") == ln_class}
    if batch is not None:
        used |= batch.insts.get(id(parent), set())
    if fixed_ln_inst is not None:
        inst = str(fixed_ln_inst)
        if inst in used:
            raise EditRejected(
                f"{ln_class}{inst} is already in LDevice "
                f"{parent.get('inst')!r}; tLN is identified by prefix, "
                f"lnClass and inst")
        return inst
    low, high = LN_INST_RANGE
    for candidate in range(low, high + 1):
        if str(candidate) not in used:
            return str(candidate)
    raise EditRejected(
        f"LDevice {parent.get('inst')!r} already holds {ln_class} instances "
        f"{low} to {high}, which is the whole range 61850-6 allows for "
        f"tLN@inst")


def _plan(doc, supervision, supervision_ln, new_supervision_ln, fixed_ln_inst,
          ln_type, parent, check_editable_src_ref,
          check_duplicate_supervisions, check_max_supervision_limits,
          batch=None) -> _Plan:
    """Everything both public functions need, resolved once.

    The guard and the builder share it so they cannot disagree, which is the
    drift Q23 and Q25 were written to stop: ``can_instantiate_supervision`` is
    this function returning without raising.

    ``batch`` is the record of what an unapplied compound edit has already
    spoken for; it is read by every placement decision below and written once,
    at the end, only when a plan was actually reached. A refusal records
    nothing, which is what keeps a rejected edit from changing the answer to
    the next question.
    """
    if not isinstance(supervision, Supervision):
        raise EditRejected(
            f"instantiate_supervision takes a Supervision, not "
            f"{type(supervision).__name__}")

    subscriber = supervision.subscriber
    if not _is(subscriber, "IED") or doc.parent_of(subscriber) is not doc.root:
        raise EditRejected(
            f"{_describe(subscriber)} is not an IED of this document; a "
            f"supervision subscriber is a direct child of the SCL root")

    control_block = supervision.control_block
    if not _is_control_block(control_block):
        raise EditRejected(
            f"{_describe(control_block)} is not a control block")
    ln_class = supervision_ln_class(control_block)
    if ln_class is None:
        raise EditRejected(
            f"nothing supervises a {strip_ns(control_block.tag)}; 61850-7-4 "
            f"defines LGOS for a GSEControl and LSVS for a "
            f"SampledValueControl, and no class for a ReportControl")

    obj_ref = control_block_obj_ref(doc, control_block)
    if not obj_ref:
        raise EditRejected(
            f"{strip_ns(control_block.tag)} "
            f"{control_block.get('name')!r} has no object reference; it "
            f"needs a name and must be inside an IED")

    if check_duplicate_supervisions:
        already = _watching(doc, subscriber, ln_class, obj_ref)
        if already is not None:
            raise EditRejected(
                f"IED {subscriber.get('name')!r} already supervises "
                f"{obj_ref} with {already.get('prefix') or ''}"
                f"{ln_class}{already.get('inst')}")

    if supervision_ln is not None:
        target = _resolve_named_ln(doc, subscriber, supervision_ln, ln_class,
                                   batch)
    elif new_supervision_ln:
        target = None
    else:
        target = _first_free(doc, subscriber, ln_class, batch)
        if target is None:
            # Deliberately NOT a silent fallback to creating one. "Fill in a
            # free slot" and "add a logical node to this IED" are different
            # sizes of action, and Q27 settled that the larger one is asked
            # for rather than arrived at -- the same reason `remove_fcda`
            # refuses to empty a dataset instead of cascading into
            # `remove_data_set`.
            raise EditRejected(
                f"IED {subscriber.get('name')!r} has no free {ln_class} to "
                f"store {obj_ref} in; pass new_supervision_ln=True to add one")

    if target is not None:
        if check_editable_src_ref and not is_src_ref_editable(doc, target):
            kind, imp = _declared_val_flags(doc, target)
            raise EditRejected(
                f"{target.get('prefix') or ''}{ln_class}{target.get('inst')} "
                f"declares its setSrcRef not writable by a configuration tool "
                f"(valKind={kind!r}, valImport={imp!r}); pass "
                f"check_editable_src_ref=False to write it anyway")
        plan = _Plan(ln_class, obj_ref, target, None, None, None)
        scope_element = target
    else:
        plan = _new_node_plan(doc, subscriber, ln_class, obj_ref,
                              fixed_ln_inst, ln_type, parent,
                              check_editable_src_ref, batch)
        scope_element = plan.parent

    owner = _check_limit(doc, subscriber, scope_element, ln_class, batch,
                         check_max_supervision_limits)
    if batch is not None:
        if plan.supervision_ln is not None:
            batch.nodes.add(id(plan.supervision_ln))
        else:
            batch.insts.setdefault(id(plan.parent), set()).add(plan.inst)
        key = (id(owner if owner is not None else subscriber), ln_class)
        batch.bound[key] = batch.bound.get(key, 0) + 1
    return plan


def _check_limit(doc, subscriber, scope_element, ln_class, batch=None,
                 enforce=True):
    """Refuse when the `SupSubscription` governing this placement is full.

    The declaration is resolved from the element the new reference will be
    STORED in rather than from the IED, so an `AccessPoint`-level
    `SupSubscription` is seen at all -- and the count is then taken in the
    scope that declared it, which is Q26 §2's rule for `ConfDataSet@max`
    applied unchanged. No corpus file exercises either half: all 51
    declarations are IED-level.

    Returns the element the count is taken in, so :func:`_plan` can record a
    pending reference against the same scope the next call will count it in.
    The scope is resolved even when there is no limit and even when
    ``enforce`` is off, because the record is kept either way: a batch that
    turns the check off still fills slots, and the next question still has to
    get a consistent answer.
    """
    found = _services_child(doc, scope_element, "SupSubscription")
    owner = found[2] if found is not None else subscriber
    if not enforce:
        return owner
    limit = max_supervision(doc, scope_element)
    if limit is None:
        return owner
    declared = limit.max_go if ln_class == "LGOS" else limit.max_sv
    if declared is None:
        return owner
    bound = _bound_supervisions(doc, owner, ln_class)
    if batch is not None:
        # What this compound edit has already promised to bind, which the
        # document cannot show: nothing is applied until the caller applies
        # the whole list. Without it a batch of two would take the last free
        # slot twice and write a file that breaks its own declaration.
        bound += batch.bound.get((id(owner), ln_class), 0)
    if bound >= declared:
        raise EditRejected(
            f"IED {subscriber.get('name')!r} already supervises {bound} "
            f"control block(s) with {ln_class}, which is the maximum its "
            f"{limit.scope} SupSubscription declares "
            f"({_LIMIT_ATTRIBUTE[ln_class]}={declared})")
    return owner


def _resolve_named_ln(doc, subscriber, supervision_ln, ln_class, batch=None):
    """The caller's own node, checked -- the reference's union half."""
    if not _is_supervision_ln(supervision_ln):
        raise EditRejected(
            f"supervision_ln must be an LGOS or LSVS, not "
            f"{_describe(supervision_ln)}")
    if supervision_ln.get("lnClass") != ln_class:
        raise EditRejected(
            f"a {strip_ns(supervision_ln.tag)} of class "
            f"{supervision_ln.get('lnClass')} cannot supervise a control "
            f"block that needs {ln_class}")
    if _ied_of(doc, supervision_ln) is not subscriber:
        raise EditRejected(
            f"the given {ln_class} is not inside IED "
            f"{subscriber.get('name')!r}")
    existing = _supervised_reference(doc, supervision_ln)
    if existing:
        raise EditRejected(
            f"{supervision_ln.get('prefix') or ''}{ln_class}"
            f"{supervision_ln.get('inst')} already supervises {existing}")
    if batch is not None and id(supervision_ln) in batch.nodes:
        raise EditRejected(
            f"{supervision_ln.get('prefix') or ''}{ln_class}"
            f"{supervision_ln.get('inst')} is already being given a reference "
            f"by an earlier part of this same edit")
    return supervision_ln


def _watching(doc, subscriber, ln_class, obj_ref):
    """The supervision node inside ``subscriber`` already watching
    ``obj_ref``, or ``None``.

    One function for one question, because three callers ask it and they must
    agree: :func:`_plan`'s duplicate refusal, the subscribe wiring's decision
    to leave an already-supervised block alone -- 548 of the corpus's 566
    pairs -- and the unsubscribe wiring's search for the node whose
    subscription has just ended.
    """
    return next((node for node in _supervision_lns(doc, subscriber, ln_class)
                 if _supervised_reference(doc, node) == obj_ref), None)


def _first_free(doc, subscriber, ln_class, batch=None):
    """The lowest-numbered supervision node of this class watching nothing.

    ``None`` when every one is taken, which is what sends the caller to
    ``new_supervision_ln=True``. Free covers all four shapes the module
    docstring tabulates, the Siemens empty `DOI` included.

    ``batch`` removes the nodes an unapplied compound edit has already filled;
    see :class:`_Batch`.
    """
    free = [node for node in _supervision_lns(doc, subscriber, ln_class)
            if not _supervised_reference(doc, node)
            and (batch is None or id(node) not in batch.nodes)]
    if not free:
        return None

    def order(node):
        raw = node.get("inst") or ""
        return (0, int(raw)) if raw.isdigit() else (1, 0)

    return sorted(free, key=order)[0]


def _new_node_plan(doc, subscriber, ln_class, obj_ref, fixed_ln_inst, ln_type,
                   parent, check_editable_src_ref, batch=None):
    """Parent, `lnType` and `inst` for a logical node that does not exist."""
    sibling = next(iter(_supervision_lns(doc, subscriber, ln_class)), None)

    if parent is None:
        if sibling is None:
            raise EditRejected(
                f"IED {subscriber.get('name')!r} holds no {ln_class}, so "
                f"there is no LDevice to place a new one in and no lnType to "
                f"give it; pass parent and ln_type, or import an "
                f"{ln_class} LNodeType first")
        parent = doc.parent_of(sibling)
    if not _is(parent, "LDevice"):
        raise EditRejected(
            f"a supervision logical node goes in an LDevice, not in "
            f"{_describe(parent)}")
    if _ied_of(doc, parent) is not subscriber:
        raise EditRejected(
            f"LDevice {parent.get('inst')!r} is not inside IED "
            f"{subscriber.get('name')!r}")

    if ln_type is None:
        if sibling is None:
            raise EditRejected(
                f"IED {subscriber.get('name')!r} holds no {ln_class} to take "
                f"an lnType from; pass ln_type")
        ln_type = sibling.get("lnType")
    if not ln_type:
        raise EditRejected(
            f"a new {ln_class} needs an lnType; tLN@lnType is required and "
            f"must name an LNodeType in DataTypeTemplates")

    if check_editable_src_ref:
        da = _template_da(doc, ln_type, _REFERENCE_DO[ln_class], "setSrcRef")
        kind = da.get("valKind") if da is not None else None
        imp = da.get("valImport") if da is not None else None
        if (kind is not None and kind not in ("RO", "Conf")) or \
                (imp is not None and imp != "true"):
            raise EditRejected(
                f"LNodeType {ln_type!r} declares setSrcRef not writable by a "
                f"configuration tool (valKind={kind!r}, valImport={imp!r}); "
                f"pass check_editable_src_ref=False to write it anyway")

    inst = _free_inst(doc, parent, ln_class, fixed_ln_inst, batch)
    return _Plan(ln_class, obj_ref, None, parent, ln_type, inst)


# -- instantiating ----------------------------------------------------------

def can_instantiate_supervision(doc, supervision, supervision_ln=None,
                                new_supervision_ln=False, fixed_ln_inst=None,
                                ln_type=None, parent=None,
                                check_editable_src_ref=True,
                                check_duplicate_supervisions=True,
                                check_max_supervision_limits=True) -> bool:
    """Whether :func:`instantiate_supervision` would succeed with these
    arguments.

    It asks exactly what the reference's
    `canInstantiateSubscriptionSupervision` asks -- whether the value may be
    written at all, whether this IED already supervises the block, whether
    there is a location to store the reference in, whether the `Services`
    declaration allows another, and whether a named logical node is free --
    by planning the edit and discarding it, so the guard cannot come to a
    different conclusion from the function it guards.
    """
    try:
        _plan(doc, supervision, supervision_ln, new_supervision_ln,
              fixed_ln_inst, ln_type, parent, check_editable_src_ref,
              check_duplicate_supervisions, check_max_supervision_limits)
    except EditRejected:
        return False
    return True


def instantiate_supervision(doc, supervision, supervision_ln=None,
                            new_supervision_ln=False, fixed_ln_inst=None,
                            ln_type=None, parent=None,
                            check_editable_src_ref=True,
                            check_duplicate_supervisions=True,
                            check_max_supervision_limits=True) -> List:
    """The edit that makes ``supervision.subscriber`` watch
    ``supervision.control_block``, as one compound edit.

    By default the lowest-numbered free supervision node of the right class is
    reused and only what is missing is written -- a `Val` that is already
    there has its text SET rather than being replaced, which is Q30's rule for
    a `P` element one section down and keeps the element's own attributes and
    position. ``new_supervision_ln=True`` builds a fresh `LN` instead, placed
    in the `LDevice` its siblings use, carrying their `lnType` and the lowest
    unused `inst`.

    ``supervision_ln`` names a particular `LGOS`/`LSVS` to fill in; it is the
    other half of the reference's union-typed `subscriberIedOrLn` and is
    refused if the node is of the wrong class, outside the subscriber, or
    already watching something.

    ``fixed_ln_inst``, ``ln_type`` and ``parent`` override what would
    otherwise be read off a sibling. The three ``check_*`` flags match the
    reference's options and all default to on;
    :func:`can_instantiate_supervision` answers the same question without
    raising.

    Raises :class:`~py61850.scl.EditRejected` and changes nothing when any
    check fails -- there is no ``None`` return, on Q27's convention that a
    refusal a caller forgets to test becomes a silent no-op.
    """
    plan = _plan(doc, supervision, supervision_ln, new_supervision_ln,
                 fixed_ln_inst, ln_type, parent, check_editable_src_ref,
                 check_duplicate_supervisions, check_max_supervision_limits)
    return _instantiate_edits(doc, plan)


def _instantiate_edits(doc, plan) -> List:
    """The edits a resolved :class:`_Plan` turns into.

    Split from :func:`instantiate_supervision` so the wiring in
    :func:`_expand_subscribe_supervision` reaches the same builder through the
    same :func:`_plan`, rather than growing a second one -- the drift Q23 and
    Q25 exist to stop, arriving here because a batch needs to pass a
    :class:`_Batch` the public signature deliberately does not carry.
    """
    obj_ref = plan.obj_ref

    if plan.supervision_ln is None:
        node = _build_supervision_ln(doc, plan, obj_ref)
        return [Insert(plan.parent, node,
                       reference_for(plan.parent, node.tag))]

    target = plan.supervision_ln
    doi, dai, val = _src_ref_path(doc, target)
    if val is not None:
        return [SetTextContent(val, obj_ref)]
    if dai is not None:
        new_val = _element(doc, "Val")
        new_val.text = obj_ref
        return [Insert(dai, new_val, reference_for(dai, new_val.tag))]
    if doi is not None:
        new_dai = _build_src_ref_dai(doc, obj_ref)
        return [Insert(doi, new_dai, reference_for(doi, new_dai.tag))]
    new_doi = _build_src_ref_doi(doc, plan.ln_class, obj_ref)
    return [Insert(target, new_doi, reference_for(target, new_doi.tag))]


def _element(doc, local_name, **attributes):
    node = ET.Element(_qualified(doc, local_name))
    for name, value in attributes.items():
        if value is not None:
            node.set(name, value)
    return node


def _build_src_ref_dai(doc, obj_ref):
    dai = _element(doc, "DAI", name="setSrcRef")
    val = _element(doc, "Val")
    val.text = obj_ref
    dai.append(val)
    return dai


def _build_src_ref_doi(doc, ln_class, obj_ref):
    doi = _element(doc, "DOI", name=_REFERENCE_DO[ln_class])
    doi.append(_build_src_ref_dai(doc, obj_ref))
    return doi


def _build_supervision_ln(doc, plan, obj_ref):
    """A whole `LN`, carrying nothing but the reference it is created for.

    No `prefix` and no `desc`, and the corpus is why neither is guessed at.
    SEL writes a prefix on 228 of `sel.scd`'s 231 supervision nodes, naming
    the device being SUPERVISED rather than anything about this one; Siemens
    writes none on any of its 101, and `mixed.scd` none on 266 of 268. A
    convention two vendors disagree about that completely is a vendor
    convention, and `py61850.scl` takes no view on one. `desc` is on 402 of
    the 600 and is a human note rather than configuration.
    """
    node = _element(doc, "LN", lnClass=plan.ln_class, inst=plan.inst,
                    lnType=plan.ln_type)
    node.append(_build_src_ref_doi(doc, plan.ln_class, obj_ref))
    return node


# -- removing ---------------------------------------------------------------

def _supervised_control_block(doc, obj_ref):
    """The control block ``obj_ref`` names, or ``None`` when the document has
    no such block or more than one."""
    if not obj_ref:
        return None
    found = None
    for local_name in SUPERVISED_CONTROL_BLOCKS:
        for block in doc.root.iter(_qualified(doc, local_name)):
            if control_block_obj_ref(doc, block) != obj_ref:
                continue
            if found is not None:
                return None
            found = block
    return found


def can_remove_supervision(doc, supervision_ln, remove_supervision_ln=False,
                           check_subscription=True) -> bool:
    """Whether :func:`remove_supervision` would succeed with these arguments.

    ``True`` for a node that already watches nothing, which is the state the
    removal produces; that call returns no edits.
    """
    try:
        _removal_target(doc, supervision_ln, check_subscription)
    except EditRejected:
        return False
    return True


def _removal_target(doc, supervision_ln, check_subscription):
    if not _is_supervision_ln(supervision_ln):
        raise EditRejected(
            f"remove_supervision takes an LGOS or LSVS, not "
            f"{_describe(supervision_ln)}")
    subscriber = _ied_of(doc, supervision_ln)
    if subscriber is None:
        raise EditRejected(
            f"{supervision_ln.get('lnClass')} "
            f"{supervision_ln.get('inst')} is not inside an IED of this "
            f"document")
    obj_ref = _supervised_reference(doc, supervision_ln)
    if check_subscription and obj_ref:
        block = _supervised_control_block(doc, obj_ref)
        if block is not None:
            still = [ext_ref for ext_ref
                     in find_control_block_subscription(doc, block)
                     if _ied_of(doc, ext_ref) is subscriber]
            if still:
                raise EditRejected(
                    f"IED {subscriber.get('name')!r} still has {len(still)} "
                    f"ExtRef element(s) subscribed to {obj_ref}; unsubscribe "
                    f"them first, or pass check_subscription=False")
    return obj_ref


def remove_supervision(doc, supervision_ln, remove_supervision_ln=False,
                       check_subscription=True) -> List:
    """The edit that stops ``supervision_ln`` watching anything.

    **The value is blanked and the logical node is kept**, which is what
    ``remove_supervision_ln=False`` means and what this defaults to. Two
    unrelated vendors write an idle supervision node rather than deleting one
    -- 24 `LGOS` in `sel.scd` with an empty `Val` and 25 in `siemens.scd` with
    an empty `DOI` -- so a blanked node is an ordinary shape in a station
    file, and removing the element instead would renumber the `inst` of the
    ones after it. It is the same edit A13's
    :func:`~py61850.scl.remove_ied` already produces, so the two paths cannot
    drift apart.

    ``remove_supervision_ln=True`` takes the whole `LN` out instead.

    ``check_subscription`` refuses while the subscriber still has an `ExtRef`
    bound to the supervised control block -- the reference removes supervision
    *"when all external references of one control block are unsubscribed"*,
    and this is that rule as a guard rather than as a side effect.

    Returns an empty list for a node that already watches nothing and is being
    blanked: there is nothing to do and saying so is not a refusal.
    """
    _removal_target(doc, supervision_ln, check_subscription)
    if remove_supervision_ln:
        return [Remove(supervision_ln)]
    _doi, _dai, val = _src_ref_path(doc, supervision_ln)
    if val is None or not (val.text or "").strip():
        return []
    return [SetTextContent(val, None)]


# -- the wiring -------------------------------------------------------------
#
# What the seven `ignore_supervision` flags reach. Every one of these is
# private: A14b adds no capability and no name to `py61850.scl.__all__`. The
# public surface of this phase is the behaviour of six functions in five other
# modules when `False` is passed, and those modules import from here the way
# A9 imports `_expand_remove_data_set` from A10 -- Q25's shape, one expansion
# in one module, with a deferred import for the back-edge.

def _source_key(ext_ref):
    """The `src*` attributes that identify which control block an `ExtRef`
    takes its data from.

    Grouping by it is what keeps the unsubscribe expansion at one document
    walk per distinct control block instead of one per `ExtRef`. It matters:
    the busiest corpus block carries 64 `ExtRef` elements, and
    :func:`~py61850.scl.source_control_block` walks every IED to resolve one.
    """
    return (ext_ref.get("iedName"), ext_ref.get("srcLDInst"),
            ext_ref.get("srcPrefix"), ext_ref.get("srcLNClass"),
            ext_ref.get("srcLNInst"), ext_ref.get("srcCBName"))


def _expand_subscribe_supervision(doc, connections, new_supervision_ln) -> List:
    """The supervision each connection implies, as edits.

    Reached by :func:`~py61850.scl.subscribe` when `ignore_supervision` is
    `False`. Three things decide the shape, and each was measured:

    - **An already-supervised block is skipped, not refused.** 548 of the
      corpus's 566 (subscriber, control block) pairs are already supervised,
      so the duplicate is the ordinary case here; :func:`_plan`'s own
      duplicate check would refuse it, which is right for a caller asking for
      one supervision and wrong for a caller asking to subscribe.
    - **A `ReportControl` produces nothing.** 61850-7-4 defines `LGOS` for a
      `GSEControl` and `LSVS` for a `SampledValueControl` and no report
      equivalent, and a connection naming no control block at all -- Edition 1
      subscription -- has nothing to supervise either.
    - **What cannot be supervised refuses the whole edit.** The reference
      appends a `null` and subscribes anyway, which it can afford because its
      own default writes supervision: refusing there would break every
      ordinary `subscribe`. Ours defaults to `True`, so `False` is an explicit
      opt-in, and an opt-in that silently does nothing cannot be found out
      about. Measured, silence would be the answer 16 times out of 16: every
      unsupervised subscription in the corpus is on one of the seven IEDs that
      hold no supervision node at all, where a new one needs an `lnType` only
      `importLNodeType` can supply. Q32 and Q33.
    """
    batch = _Batch()
    edits: List = []
    seen = set()
    for connection in connections:
        block = getattr(connection, "control_block", None)
        if block is None:
            continue
        ln_class = supervision_ln_class(block)
        if ln_class is None:
            continue
        subscriber = _ied_of(doc, connection.sink)
        if subscriber is None:
            raise EditRejected(
                f"{_describe(connection.sink)} is not inside an IED of this "
                f"document, so there is nothing to supervise "
                f"{strip_ns(block.tag)} {block.get('name')!r} from; pass "
                f"ignore_supervision=True to subscribe without supervision")
        obj_ref = control_block_obj_ref(doc, block)
        if not obj_ref:
            raise EditRejected(
                f"{strip_ns(block.tag)} {block.get('name')!r} has no object "
                f"reference to supervise; it needs a name and must be inside "
                f"an IED")
        key = (id(subscriber), obj_ref)
        if key in seen:
            # Two ExtRefs of one IED bound to one control block are one
            # subscription to supervise, not two. 531 of 566 corpus pairs
            # carry more than one ExtRef, so this is the common shape.
            continue
        seen.add(key)
        if _watching(doc, subscriber, ln_class, obj_ref) is not None:
            continue
        plan = _plan(doc, Supervision(subscriber, block), None,
                     new_supervision_ln, None, None, None,
                     True, True, True, batch)
        edits.extend(_instantiate_edits(doc, plan))
    return edits


def _expand_unsubscribe_supervision(doc, ext_refs) -> List:
    """The supervision the removal of these `ExtRef` elements ends, as edits.

    Reached by :func:`~py61850.scl.unsubscribe` when `ignore_supervision` is
    `False`, and through it by `remove_data_set` and `remove_fcda`.

    **The rule is the reference's, and it is a question about a SET.**
    Supervision goes *"when all external references of one control block are
    unsubscribed"* -- so the ExtRefs of that block in that subscriber which
    this call does NOT touch are what decide it. Only 35 of the corpus's 566
    pairs carry a single `ExtRef` and the busiest block carries 64, so judging
    one call at a time would make the answer depend on the order the caller
    happened to ask in. That is Q27's argument for `remove_fcda`'s list,
    arriving at `unsubscribe`.

    **The value is blanked and the logical node kept**, because that is what
    :func:`remove_supervision` defaults to and what A13's
    :func:`~py61850.scl.remove_ied` already emits -- the same primitive on the
    same element, so the three paths cannot drift. Two unrelated vendors write
    an idle supervision node rather than deleting one, 49 times between them.

    **`valKind`/`valImport` are not read here**, which is what
    :func:`remove_supervision` already decided by omission: blanking is the
    same edit A13 makes without asking, and under this package's permissive
    reading :func:`is_src_ref_editable` answers `True` for all 600 corpus
    nodes anyway. The strict reading would leave 170 bound supervisions
    watching a subscription that no longer exists. Q33.
    """
    # **Everything this call unbinds, counted once for the whole call.**
    # Grouping the departures per source key would under-count them: `_same`
    # treats an absent attribute and an empty one as the same value, so two
    # ExtRefs bound to one control block can carry different `srcPrefix` or
    # `srcLNInst` spellings and land in two groups. Each group would then see
    # the other's departures as ExtRefs that are staying, and a supervision
    # whose every subscription was going would be left standing.
    leaving = {id(ext_ref) for ext_ref in ext_refs}

    groups: dict = {}
    for ext_ref in ext_refs:
        if ext_ref.get("srcCBName"):
            groups.setdefault(_source_key(ext_ref), []).append(ext_ref)

    edits: List = []
    done = set()
    handled = set()
    for group in groups.values():
        block = source_control_block(doc, group[0])
        if block is None or id(block) in handled:
            # Two groups can resolve to one block, for the spelling reason
            # above. The departures are already counted across the whole
            # call, so the second group would only repeat the walk.
            continue
        handled.add(id(block))
        ln_class = supervision_ln_class(block)
        obj_ref = control_block_obj_ref(doc, block) if ln_class else None
        if not obj_ref:
            continue

        # One document walk for the block, then split by subscriber: an
        # ExtRef in another IED says nothing about this one's supervision.
        staying: dict = {}
        for ext_ref in find_control_block_subscription(doc, block):
            ied = _ied_of(doc, ext_ref)
            if ied is None:
                continue
            staying.setdefault(id(ied), [ied, 0])
            if id(ext_ref) not in leaving:
                staying[id(ied)][1] += 1

        for ied, remaining in staying.values():
            if remaining:
                continue
            node = _watching(doc, ied, ln_class, obj_ref)
            if node is None or id(node) in done:
                continue
            done.add(id(node))
            # `check_subscription=False` is required, not a shortcut: nothing
            # in this compound edit is applied yet, so the ExtRefs going away
            # are all still in the document and the guard would refuse the
            # very removal their departure calls for.
            edits.extend(remove_supervision(doc, node,
                                            check_subscription=False))
    return edits


def _expand_control_block_supervision(doc, control_block) -> List:
    """Every supervision value naming ``control_block``, blanked.

    Reached by :func:`~py61850.scl.remove_control_block` when
    `ignore_supervision` is `False`, and it answers a different question from
    the one above: *this block is gone*, rather than *this subscription
    ended*. A13's :func:`~py61850.scl.remove_ied` already sweeps exactly this
    way, unconditionally, for the blocks inside a removed IED -- so without it
    removing an IED would blank a supervision that removing one of its control
    blocks left standing, which is one defect with two answers.

    **It is a superset of what the unsubscribe path produces for this block,
    and on the corpus it is the same set**: every one of the 566 supervised
    pairs also subscribes, and 0 supervisions survive without a subscription.
    So `remove_control_block` takes this route alone and passes
    `ignore_supervision=True` to the `unsubscribe` inside it, and no `Val` is
    blanked twice.

    This is a divergence: the reference's driver is
    `removeSubscriptionSupervision(extRefs)` and is `ExtRef`-driven with no
    block-driven equivalent. Q21 and Q29 took the same decision -- chase the
    dangling reference the reference does not document -- and Q33 records it.
    """
    ln_class = supervision_ln_class(control_block)
    if ln_class is None:
        return []
    obj_ref = control_block_obj_ref(doc, control_block)
    if not obj_ref:
        return []
    return [SetTextContent(val, None)
            for val, text in _supervision_values(doc, (_REFERENCE_DO[ln_class],))
            if text == obj_ref]


def _repoint_supervision(doc, control_block, new_name) -> List:
    """Every supervision value naming ``control_block``, re-pointed at its new
    name.

    Reached by :func:`~py61850.scl.update_sampled_value_control` when `name`
    changes and `ignore_supervision` is `False` -- the half of *"name: also
    updates SMV.cbName and supervision references"* that A12 could not write.

    **Matched exactly, not decomposed.** A13 resolves an IED rename through
    :func:`~py61850.scl.ied._object_reference_index` because a name is a
    PREFIX of many references and two IEDs can build the same one; a control
    block rename has a single exact string, which
    :func:`~py61850.scl.control_block_obj_ref` hands over before the edit
    applies. So the index is neither reused, moved nor duplicated: it answers
    a question this does not ask.

    The `DOI` is narrowed to the one this class keeps its reference in, so a
    `DatSet` value that happens to read the same -- which a `DataSet` and a
    control block named alike in one logical node would produce -- is left
    alone.
    """
    ln_class = supervision_ln_class(control_block)
    if ln_class is None:
        return []
    old = control_block_obj_ref(doc, control_block)
    suffix = "." + (control_block.get("name") or "")
    if not old or not old.endswith(suffix):
        return []
    wanted = old[:-len(suffix)] + "." + new_name
    return [_retext(val, wanted)
            for val, text in _supervision_values(doc, (_REFERENCE_DO[ln_class],))
            if text == old]
