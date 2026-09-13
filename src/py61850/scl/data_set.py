# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""Datasets and their members: creating, renaming, and removing them safely.

    from py61850.scl import Remove, SclDocument, remove_data_set

    doc = SclDocument.parse("station.scd")
    edit = remove_data_set(doc, Remove(data_set))
    doc.apply_edit(edit)     # dataset, its subscribers, and every publisher

**Both of the reference's other two shapes are here**, where
:mod:`py61850.scl.control_block` had only one:

- **element creation** -- :func:`create_data_set` takes a parent and what the
  caller wants, and returns the edit that makes it. That is A8's shape.
- **edit checks** -- :func:`update_data_set`, :func:`remove_data_set` and
  :func:`remove_fcda` take the edit the CALLER already built and return the
  corrected, expanded one, input edit first. That is A9's shape, and Q23
  settled it.

The rest -- :func:`can_add_data_set`, :func:`can_add_fcda`,
:func:`max_attributes` and :func:`fcda_subscriptions` -- are queries, and
change nothing.

**Nothing here applies anything**, and a refusal raises
:class:`~py61850.scl.EditRejected` with nothing half-done.

## What the schema requires, and why it decides most of this module

Three facts from `SCL_IED.xsd` (2007B4) are load-bearing here, and each one
removes a choice that would otherwise have been ours:

- **A `DataSet` may not be empty.** Its content model is
  ``<xs:choice maxOccurs="unbounded">`` over `FCDA`, and `minOccurs` defaults
  to 1. So a dataset an edit empties is not merely unusual, it is invalid --
  which is why :func:`remove_fcda` refuses to take the last member rather
  than quietly producing one. The reference corpus agrees and says nothing
  about it either way: **0 of its 295 `DataSet` elements are empty**, the
  smallest holding one member.
- **`datSet` is a `keyref`, not a loose reference.** `DataSetKeyLN0` and
  `DataSetKeyInLN` are `xs:key` on `DataSet@name`, and all four control-block
  `@datSet` selectors are `keyref`s into them. A block left naming a dataset
  that is gone is invalid, so :func:`remove_data_set`'s repair of its
  publishers is forced by 61850-6 rather than chosen. The attribute is
  CLEARED rather than the block being removed, because `datSet` is
  ``use="optional"`` -- and 720 of the corpus's 1,015 control blocks carry
  none at all, so a block without one is the ordinary shape and not a
  casualty.
- **A `DataSet@name` is unique within its logical node.** The same `xs:key`
  says so, which is what :func:`create_data_set` and :func:`update_data_set`
  refuse a collision against. Q22 met the rule from the control-block side.

## An element also called `DataSet`, which is not one

`mixed.scd` holds **113** elements named `DataSet` that are not SCL datasets
at all: they are in `http://www.siemens.com/energy/2011/11/Siedig`, inside a
`Private`, under `GooseApplication` and `SMVApplication`, and `siemens.scd`
holds 14 more. Every one of them is empty, which is exactly the shape the
schema forbids of a real dataset -- because they are not real datasets, and
the vendor library is what reads them.

So **nothing here counts by local name.** A `DataSet` is a same-namespace
child of an `LN` or `LN0`; an `FCDA` is a same-namespace child of one of
those. Counting with :func:`~py61850.scl.iter_local` instead would have the
guards in this module refuse an IED because of 22 elements belonging to
another vendor's tooling.

## What the two guards read, and what the corpus says about it

`Services/ConfDataSet` carries `max` -- how many datasets -- and
`maxAttributes` -- how many members one dataset may hold. The reference reads
it **from the `AccessPoint` first and from the `IED` if that has none**, and
so does this module.

**The corpus exercises only half of that rule.** All 58 of its IEDs declare a
`ConfDataSet`, every one on the IED-level `Services`, and not one of
`mixed.scd`'s 24 AccessPoint-level `Services` elements carries one. The
AccessPoint branch is therefore written from the reference's documented
behaviour and tested against built fixtures alone.

**`maxAttributes` counts one dataset's members, and the corpus proves it.**
Twelve of `mixed.scd`'s fourteen IEDs declare ``maxAttributes="200"`` while
holding 242 to 477 members across their datasets; no single dataset exceeds
122. Read as a per-IED total, a vendor's own export would violate its own
declaration twelve times, so the per-dataset reading is the only one the
files support.

**`max` counts in the scope that declared it** -- every dataset under the
`AccessPoint` when the `AccessPoint` declared the limit, and under the `IED`
when the IED did. Unlike `maxAttributes`, no file settles this: the largest
per-IED count is 22 against a declared 50 and the largest on one logical node
is 10, so both readings survive the corpus and this one is a judgement about
what a declaration means rather than a measurement.

**A limit that is not declared is not a limit.** An IED with no `Services`,
or with `Services` and no `ConfDataSet`, is unconstrained and both guards
answer ``True``. That is A8's rule for `pDO` and `pDA` applied again -- an
unresolvable restriction is not a restriction -- and the alternative would
refuse every dataset on a hand-written ICD that declares no services at all.
`ConfDataSet@modify` is deliberately NOT read: the reference's guards do not
mention it, and no file in the corpus sets it false (24 say `true`, 4 are
silent).

**Neither refusal branch is reachable from the corpus.** No IED is near its
own limit -- at most 22 datasets against a `max` of 50, at most 122 members
against a `maxAttributes` of 200 -- so both refusals are tested against
built fixtures. That is the opposite of A16's problem and worth saying: here
the LIMITS are real and only the breach is synthetic.

## `confRev`, and which of these edits moves it

A9 defined the rule -- `confRev` moves when the PUBLISHED DATA changes -- and
owned exactly one trigger, the `datSet` reference changing on a block. The
rest are here, and they do not all say yes:

| edit | moves `confRev`? |
|---|---|
| a member removed (:func:`remove_fcda`) | **yes**, on every block publishing that dataset |
| the dataset removed (:func:`remove_data_set`) | **yes** -- the blocks now publish nothing |
| a member added or reordered | **yes**, and :func:`updated_conf_rev_edits` is how |
| the dataset RENAMED (:func:`update_data_set`) | **no** |
| `desc` changed, or the dataset created | **no** |

**A rename does not move it, and that is the one to argue.** What a
subscriber caches is the layout of the data, and a rename changes the name of
the dataset rather than a single attribute travelling in it. A9's
:func:`~py61850.scl.update_dat_set` met the same event from the other side --
setting `datSet` on a block whose exclusive dataset is renamed with it -- and
moved the revision; that was inconsistent with this rule and A10 corrected
it, so the two sides of one event now agree.

`update_conf_rev=False` suppresses it. A9 reads the caller's own mapping
instead, which cannot work here: the revision lands on a control block and
the caller's edit is on the dataset or on one of its members, so there is no
mapping to read.

## Q19, answered here because A8 and A9 both deferred it

An `FCDA` carrying a `doName` and no `daName` publishes the whole data
object, and **half of the corpus's dataset members are that shape** -- 3,417
of 6,967, and 1,348 of `siemens.scd`'s 1,603. Whether such a member satisfies
an `ExtRef` bound to one attribute of it is Q19, and it had to be answered
before a removal could know what to repair.

**The answer is that it depends which question is being asked**, and the two
are now two functions. :func:`~py61850.scl.match_data_attributes` stays
literal, because it decides what :func:`~py61850.scl.subscribe` WRITES.
:func:`~py61850.scl.fcda_covers_ext_ref` is the wider relation -- would
removing this member break this ExtRef -- and it is what this module and
:func:`~py61850.scl.is_subscribed` ask.

**The count is 4, and the filter is part of the answer.** Restricted to the
dataset the subscribed control block actually publishes, `sel.scd` carries
four such subscriptions and the other two exports carry none. Swept
document-wide instead, the same test reports 4, 180 and 74 -- and every one
of those 180 and 74 also has a literal member match in the dataset it really
subscribes to. They are whole-object members of datasets those ExtRefs never
subscribed to, and unsubscribing 254 correct subscriptions is what a removal
would do if :func:`fcda_subscriptions` ignored the control block. It does
not.

## The stale-model hazard, for the third time

:class:`~py61850.scl.LogicalNode` memoises `data_sets`, and each
:class:`~py61850.scl.DataSet` reads its members once at construction. After
:func:`remove_fcda` a cached `DataSet.fcdas` still lists the member that is
gone, and after :func:`remove_data_set` `ln.data_sets` still carries the
dataset. Nothing is invalidated automatically, for the reasons and the
numbers in :meth:`~py61850.scl.SclDocument.apply_edit`. Every capability here
takes and returns live ELEMENTS, so a caller working in elements is never
stale. See Q14, which A13 is where it is answered.
"""

from __future__ import annotations

from typing import List, NamedTuple, Optional, Tuple
from xml.etree import ElementTree as ET

from .control_block import control_blocks, updated_conf_rev
from .document import strip_ns
from .edit import EditRejected, Insert, Remove, SetAttributes
from .extref import (
    _ancestor,
    _ied_name,
    _qualify,
    _same,
    match_src_attributes,
    unsubscribe,
)
from .ordering import may_contain, reference_for

#: The elements 61850-6 lets a `DataSet` hang off. `DataSet` is a child of
#: `tLN0` and of `tLN`; nothing else in the schema holds one.
DATA_SET_PARENTS = ("LN0", "LN")


class MaxAttributes(NamedTuple):
    """How many members one dataset may hold, and where the limit was found.

    ``scope`` is ``"AccessPoint"`` or ``"IED"`` -- which element's `Services`
    carried the `ConfDataSet` that set ``max``. It is there so a refusal can
    say where the number came from; an engineer told only that 200 is the
    limit has no way to find the declaration.

    **The reference returns the same two fields and does not document what
    its `scope` string holds**, so this is the honest reading of a name
    rather than a compared behaviour -- the same epistemic state Q20 records
    for `pathId`.
    """

    max: int
    scope: str


# -- what is what -----------------------------------------------------------

def _is(element, local_name) -> bool:
    return (isinstance(element, ET.Element)
            and strip_ns(element.tag) == local_name)


def _namespace(tag) -> str:
    return tag[:tag.index("}") + 1] if tag.startswith("{") else ""


def _same_ns_children(parent, local_name):
    """``parent``'s children with this name IN THE PARENT'S OWN NAMESPACE.

    The module docstring says why this is not
    :func:`~py61850.scl.children_local`: 127 elements called `DataSet` in the
    reference corpus belong to a vendor's private namespace, and counting
    them against an IED's `ConfDataSet` limit would refuse an edit because of
    somebody else's tooling.
    """
    wanted = _namespace(parent.tag) + local_name
    return [child for child in parent if child.tag == wanted]


def _logical_node_of(doc, data_set):
    """The `LN`/`LN0` holding ``data_set``, or ``None``."""
    parent = doc.parent_of(data_set)
    if parent is None or strip_ns(parent.tag) not in DATA_SET_PARENTS:
        return None
    return parent


def _data_sets_under(scope, namespace) -> int:
    """How many SCL datasets sit anywhere inside ``scope``.

    Counted through the logical nodes that may hold one, in the document's own
    namespace -- see :func:`_same_ns_children`.
    """
    total = 0
    for node in scope.iter():
        if strip_ns(node.tag) in DATA_SET_PARENTS and node.tag.startswith(namespace or ""):
            total += len(_same_ns_children(node, "DataSet"))
    return total


# -- the declared limits ----------------------------------------------------

def _services_child(doc, element, local_name) -> Optional[Tuple[ET.Element, str, ET.Element]]:
    """``(declaration, scope name, scope element)`` governing ``element``.

    ``local_name`` is the `Services` child that carries the limit --
    `ConfDataSet` here, `ConfReportControl` and `SMVsc` in A12's two modules.
    The `AccessPoint` holding ``element`` is read first and the `IED` second,
    which is the order the reference documents for all three. ``None`` when
    neither declares one, which means unconstrained and not zero.

    **The walk includes ``element`` itself.** A12's `can_add_report_control`
    accepts an `IED` or an `AccessPoint` as its parent, where A10's guards only
    ever take a logical node, and an ancestor walk that started at the parent
    would miss the declaration on the very element it was handed.

    Shared rather than copied: three capabilities across two phases ask the
    same question of the same tree, and three copies of "which Services
    governs this element" is how they come to disagree about the
    AccessPoint-first rule.
    """
    for scope in ("AccessPoint", "IED"):
        owner = element if _is(element, scope) else _ancestor(doc, element, scope)
        if owner is None:
            continue
        for services in _same_ns_children(owner, "Services"):
            for declaration in _same_ns_children(services, local_name):
                return declaration, scope, owner
    return None


def _conf_data_set(doc, element) -> Optional[Tuple[ET.Element, str, ET.Element]]:
    """``(ConfDataSet, scope name, scope element)`` governing ``element``."""
    return _services_child(doc, element, "ConfDataSet")


def _limit(conf, attribute) -> Optional[int]:
    """``conf``'s ``attribute`` as a positive integer, or ``None``.

    An unparseable or absent limit is not a limit. `max` is
    ``use="required"`` on `tServiceWithMax` and every corpus `ConfDataSet`
    writes both, so this is a guard against a hand-edited file rather than
    against anything a tool produces.
    """
    raw = conf.get(attribute)
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value >= 0 else None


def max_attributes(doc, fcda_or_data_set) -> Optional[MaxAttributes]:
    """How many members the dataset ``fcda_or_data_set`` belongs to may hold.

    ``fcda_or_data_set`` is an `FCDA`, as the reference takes, or the
    `DataSet` itself -- one member and its dataset are governed by the same
    declaration, so the two questions have one answer. A9's
    :func:`~py61850.scl.control_blocks` accepts the same pair for the same
    reason.

    ``None`` when no `ConfDataSet` governs it, which means unconstrained and
    not zero. The module docstring carries the measurement that says the
    number counts ONE dataset's members rather than an IED's total.
    """
    if not isinstance(fcda_or_data_set, ET.Element):
        return None
    found = _conf_data_set(doc, fcda_or_data_set)
    if found is None:
        return None
    conf, scope, _owner = found
    value = _limit(conf, "maxAttributes")
    return None if value is None else MaxAttributes(value, scope)


def can_add_data_set(doc, ln0) -> bool:
    """Whether another `DataSet` may be added to ``ln0``.

    ``ln0`` is an `LN0` -- or an `LN`, which 61850-6 also lets hold datasets
    and which the reference's parameter name does not cover. ``False`` for
    anything else, because the content model does not place a `DataSet` there
    at all.

    The count is taken in the scope that declared the limit and compared
    against `ConfDataSet@max`; an IED that declares none is unconstrained.
    Both are argued in the module docstring.
    """
    if not isinstance(ln0, ET.Element):
        return False
    if not may_contain(ln0.tag, _namespace(ln0.tag) + "DataSet"):
        return False
    found = _conf_data_set(doc, ln0)
    if found is None:
        return True
    conf, _scope, owner = found
    limit = _limit(conf, "max")
    if limit is None:
        return True
    return _data_sets_under(owner, _namespace(ln0.tag)) < limit


def can_add_fcda(doc, data_set) -> bool:
    """Whether another `FCDA` may be added to ``data_set``.

    ``False`` for anything that is not a `DataSet`. A dataset whose IED
    declares no `maxAttributes` is unconstrained.
    """
    if not _is(data_set, "DataSet"):
        return False
    limit = max_attributes(doc, data_set)
    if limit is None:
        return True
    return len(_same_ns_children(data_set, "FCDA")) < limit.max


# -- who takes what a member publishes --------------------------------------

def _member_keys(fcdas) -> Tuple[set, set]:
    """``(exact, whole)`` lookup keys for a set of dataset members.

    ``exact`` holds the six data attributes of every member; ``whole`` holds
    the five above `daName` for the members that name no attribute, which
    publish the object entire. An `ExtRef` is taken by this set if either
    matches -- which is :func:`~py61850.scl.fcda_covers_ext_ref` in the form a
    single sweep can use, rather than one call per member per ExtRef.
    """
    exact, whole = set(), set()
    for fcda in fcdas:
        if not fcda.get("doName"):
            # A member naming no data object covers nothing -- 5 of the
            # corpus's 6,967 are like this. See `fcda_covers_ext_ref`.
            continue
        key = tuple(fcda.get(name) or "" for name in
                    ("ldInst", "prefix", "lnClass", "lnInst", "doName"))
        if fcda.get("daName"):
            exact.add(key + (fcda.get("daName"),))
        else:
            whole.add(key)
    return exact, whole


def _subscriptions_to(doc, fcdas) -> List[ET.Element]:
    """Every `ExtRef` whose binding these members satisfy, in document order.

    One sweep of the document's ExtRefs against all the members at once: a
    22 MB export holds 12,540 of them and a dataset holds up to 122 members,
    so asking the question member by member is a million ancestor walks for an
    answer one pass produces.

    **An ExtRef naming a control block must name one that publishes THIS
    dataset.** Without that filter, removing a whole-object member would
    unsubscribe every ExtRef that happens to take the same attribute from a
    different dataset -- 254 of them across `mixed.scd` and `siemens.scd`,
    every one correctly subscribed elsewhere. An ExtRef naming no control
    block is not filtered, because there is nothing to check it against and a
    binding by data attributes alone is still a binding.
    """
    fcdas = [f for f in fcdas if isinstance(f, ET.Element)]
    if not fcdas:
        return []
    publisher = _ied_name(doc, fcdas[0])
    if not publisher:
        return []
    exact, whole = _member_keys(fcdas)
    publishers = control_blocks(doc, fcdas[0])

    out = []
    for ext_ref in doc.root.iter():
        if strip_ns(ext_ref.tag) != "ExtRef":
            continue
        if not _same(ext_ref.get("iedName"), publisher):
            continue
        key = tuple(ext_ref.get(name) or "" for name in
                    ("ldInst", "prefix", "lnClass", "lnInst", "doName"))
        if key not in whole and key + (ext_ref.get("daName") or "",) not in exact:
            continue
        if ext_ref.get("srcCBName") and not any(
                match_src_attributes(doc, ext_ref, block) for block in publishers):
            continue
        out.append(ext_ref)
    return out


def fcda_subscriptions(doc, fcda) -> List[ET.Element]:
    """Every `ExtRef` that would be left dangling if ``fcda`` were removed.

    A member is taken by an `ExtRef` bound to exactly what it publishes, and
    -- when it publishes a whole data object -- by one bound to any attribute
    of that object. Q19 and :func:`~py61850.scl.fcda_covers_ext_ref` carry the
    argument; the module docstring carries the four corpus cases.

    **Named for what it is rather than transliterated.** The reference's
    symbol is `fCDAsSubscription`, which has the plural on the wrong word and
    spells to `f_cdas_subscription` in Python; this is the same kind of
    correction Q23 recorded for `updated_conf_rev` and
    `find_control_block_subscription`.
    """
    if not _is(fcda, "FCDA"):
        return []
    return _subscriptions_to(doc, [fcda])


# -- confRev ----------------------------------------------------------------

def updated_conf_rev_edits(doc, data_set, exclude=()) -> List[SetAttributes]:
    """The `confRev` edits for every control block publishing ``data_set``.

    A9's :func:`~py61850.scl.updated_conf_rev` is the rule for ONE block and
    returns a value; this is the fan-out, and returns edits. The published
    data of a dataset changes for every block that publishes it at once, and
    :func:`~py61850.scl.control_blocks` is what says which those are.

    ``exclude`` holds blocks to leave alone, by identity -- a block being
    removed in the same compound edit has no revision worth moving.

    **It is public because two of A10's four triggers are not A10's edits.**
    Adding a member and reordering one are `Insert`s the caller builds, and
    the reference has no edit check for either; the rule that their `confRev`
    must move is still this phase's, so it is reachable rather than buried in
    the two removals that use it.
    """
    excluded = {id(block) for block in exclude}
    return [SetAttributes(block, {"confRev": updated_conf_rev(block)})
            for block in control_blocks(doc, data_set)
            if id(block) not in excluded]


# -- element creation -------------------------------------------------------

def create_data_set(doc, parent, name, desc=None, force=False) -> List:
    """The edit that adds a new `DataSet` to ``parent``.

    ``parent`` is an `LN0` or an `LN`. The element is placed by A7's
    :func:`~py61850.scl.reference_for`, so it lands where the content model
    puts a `DataSet` rather than at the end.

    **`name` is required here, where the reference's is optional.** Filling
    one in means allocating a unique name, and allocation policy is A17's --
    its own row in the plan calls it a likely divergence. A `create` that
    invented a name now and changed it at A17 would be worse than one that
    asks. `DataSet@name` is ``use="required"`` in the schema, so there is no
    valid element to produce without it.

    ``force`` skips the `ConfDataSet` guard, as the reference's `skipCheck`
    does. The collision and the content-model checks are not skippable: they
    are what makes the result a `DataSet` at all.

    Returns a list holding one :class:`~py61850.scl.Insert`, not a bare
    `Insert` -- one rule for applying what comes back, which is Q23's, and the
    same shape :func:`~py61850.scl.subscribe` returns.

    **A dataset is created empty, and an empty dataset is invalid** -- the
    module docstring's first schema fact. That is the reference's behaviour
    and it is kept: creation is a step toward a document someone meant, with
    the members following, where :func:`remove_fcda` emptying one is a step
    away from it. The asymmetry is real and is recorded rather than smoothed
    over.

    Raises :class:`~py61850.scl.EditRejected` if ``parent`` may not hold a
    `DataSet`, if ``name`` is empty or already used in ``parent``, or if the
    IED's declared `max` is reached and ``force`` is not set.
    """
    if not isinstance(parent, ET.Element):
        raise EditRejected(
            f"parent must be an Element, not {type(parent).__name__}")
    if not may_contain(parent.tag, _namespace(parent.tag) + "DataSet"):
        raise EditRejected(
            f"{strip_ns(parent.tag)} may not hold a DataSet; expected one of "
            f"{', '.join(DATA_SET_PARENTS)}")
    if not name:
        raise EditRejected(
            "a DataSet needs a name -- it is required by the schema and "
            "unique within its logical node; allocating one is A17's")
    clash = next((other for other in _same_ns_children(parent, "DataSet")
                  if other.get("name") == name), None)
    if clash is not None:
        raise EditRejected(
            f"a DataSet named {name!r} is already in this "
            f"{strip_ns(parent.tag)}")
    if not force and not can_add_data_set(doc, parent):
        conf, scope, owner = _conf_data_set(doc, parent)
        raise EditRejected(
            f"{owner.get('name') or scope} already holds "
            f"{_data_sets_under(owner, _namespace(parent.tag))} DataSet "
            f"elements, which is the maximum its {scope} ConfDataSet declares "
            f"(max={conf.get('max')})")

    node = ET.Element(_qualify(doc, "DataSet"))
    node.set("name", name)
    if desc is not None:
        node.set("desc", desc)
    return [Insert(parent, node, reference_for(parent, node.tag))]


# -- edit checks ------------------------------------------------------------

def update_data_set(doc, edit) -> List:
    """``edit`` corrected: every control block that publishes it re-pointed.

    ``edit`` is a :class:`~py61850.scl.SetAttributes` on a `DataSet`. If its
    mapping does not touch `name`, or sets it to what it already is, the edit
    comes back alone -- a `desc` is not published data and nothing follows
    from changing it.

    **Renaming a dataset re-points its publishers, and that is the mirror of
    Q22.** A9 met the same event from the control-block side, where setting
    `datSet` on a block renames the dataset it exclusively publishes; here the
    dataset is renamed directly and every block naming it -- one or several --
    follows. Leaving them behind would produce a `datSet` naming nothing,
    which the schema's `keyref` forbids.

    **`confRev` does not move.** The data a subscriber caches is unchanged; a
    rename changes what the dataset is called. The module docstring argues it,
    and A10 corrected A9's other half to agree.

    Returns ``[edit, *re-pointings]``.

    Raises :class:`~py61850.scl.EditRejected` if ``edit`` is not a
    `SetAttributes` on a `DataSet`, if the new name is empty, or if it
    collides with another `DataSet` in the same logical node.
    """
    if not isinstance(edit, SetAttributes):
        raise EditRejected(
            f"update_data_set takes a SetAttributes, not {type(edit).__name__}")
    data_set = edit.element
    if not _is(data_set, "DataSet"):
        raise EditRejected(
            f"{strip_ns(data_set.tag) or type(data_set).__name__} is not a "
            f"DataSet")
    if "name" not in edit.attributes:
        return [edit]

    wanted = edit.attributes["name"]
    if _same(wanted, data_set.get("name")):
        return [edit]
    if not wanted:
        raise EditRejected(
            "DataSet@name is required by the schema; it cannot be cleared")

    node = _logical_node_of(doc, data_set)
    if node is not None:
        clash = next((other for other in _same_ns_children(node, "DataSet")
                      if other is not data_set and other.get("name") == wanted),
                     None)
        if clash is not None:
            raise EditRejected(
                f"renaming DataSet {data_set.get('name')!r} to {wanted!r} "
                f"would collide with the DataSet already named {wanted!r} in "
                f"the same logical node")

    return [edit] + [SetAttributes(block, {"datSet": wanted})
                     for block in control_blocks(doc, data_set)]


def remove_data_set(doc, edit, ignore_supervision=True,
                    update_conf_rev=True) -> List:
    """``edit`` expanded: the dataset, its subscribers, and its publishers.

    ``edit`` is a :class:`~py61850.scl.Remove` whose node is a `DataSet`. What
    comes back is everything that removal implies, as one compound edit:

    1. the removal itself, as given;
    2. the `ExtRef` elements taking any of its members, unsubscribed through
       :func:`~py61850.scl.unsubscribe` -- blanked where an `intAddr` means
       the input outlives the connection, removed where it does not, with an
       emptied `Inputs` going too;
    3. every control block that published it, with `datSet` cleared and
       `confRev` moved, in one edit per block.

    Step 3 is not a courtesy: `datSet` is an `xs:keyref` and a block naming a
    dataset that is gone makes the file invalid. Step 2's membership is Q19's
    answer -- a member publishing a whole data object takes the ExtRefs bound
    to its attributes with it.

    ``ignore_supervision`` behaves exactly as
    :func:`~py61850.scl.unsubscribe`'s: supervision is not written yet, and
    `False` is refused rather than quietly ignored. ``update_conf_rev=False``
    leaves the revisions alone for a caller that sets them itself.

    Raises :class:`~py61850.scl.EditRejected` if ``edit`` is not a `Remove` of
    a `DataSet`.
    """
    if not ignore_supervision:
        raise EditRejected(
            "subscription supervision is not written yet; "
            "ignore_supervision=False has nothing to turn off")
    if not isinstance(edit, Remove):
        raise EditRejected(
            f"remove_data_set takes a Remove, not {type(edit).__name__}")
    data_set = edit.node
    if not _is(data_set, "DataSet"):
        raise EditRejected(
            f"{strip_ns(data_set.tag) or type(data_set).__name__} is not a "
            f"DataSet")
    return [edit] + _expand_remove_data_set(
        doc, data_set, exclude_blocks=(), already=(),
        update_conf_rev=update_conf_rev)


def _expand_remove_data_set(doc, data_set, exclude_blocks, already,
                            update_conf_rev) -> List:
    """Everything a `DataSet` removal drags with it, apart from the removal.

    Shared with A9's :func:`~py61850.scl.remove_control_block`, which removes
    an exclusively-published dataset from the other side and must not produce
    a second, differently-shaped expansion of the same event -- the drift Q23
    exists to stop. ``exclude_blocks`` is the control block that removal is
    already taking out, and ``already`` the `ExtRef` elements it has already
    unsubscribed; both are compared by identity.
    """
    handled = {id(ext_ref) for ext_ref in already}
    excluded = {id(block) for block in exclude_blocks}

    edits: List = []
    members = _same_ns_children(data_set, "FCDA")
    subscribers = [ext_ref for ext_ref in _subscriptions_to(doc, members)
                   if id(ext_ref) not in handled]
    if subscribers:
        edits.extend(unsubscribe(doc, subscribers))

    for block in control_blocks(doc, data_set):
        if id(block) in excluded:
            continue
        attributes = {"datSet": None}
        if update_conf_rev:
            attributes["confRev"] = updated_conf_rev(block)
        edits.append(SetAttributes(block, attributes))
    return edits


def remove_fcda(doc, edits, ignore_supervision=True,
                update_conf_rev=True) -> List:
    """``edits`` expanded: the members, their subscribers, and every `confRev`.

    ``edits`` is one :class:`~py61850.scl.Remove` of an `FCDA` or a list of
    them. What comes back is:

    1. the removals themselves, as given and in the order given;
    2. the `ExtRef` elements taking those members, unsubscribed;
    3. `confRev` moved on every control block publishing an affected dataset.

    **A list is accepted, where the reference takes one removal.** Whether the
    last member is being taken is a question about the whole set: removing
    twenty-nine of thirty members one call at a time succeeds and the thirtieth
    refuses, which is an answer that depends on the order the caller happened
    to ask in. Judged together it does not. :func:`~py61850.scl.unsubscribe`
    takes a list for the same kind of reason.

    **Emptying a dataset is refused.** `tDataSet` requires at least one `FCDA`,
    so a dataset an edit empties is invalid and no corpus file holds one. The
    removal is refused rather than cascading into
    :func:`remove_data_set`, because "remove this member" turning silently
    into "remove the dataset, clear three control blocks and unsubscribe
    thirty inputs" is a much larger action than the caller asked for, and a
    caller who means that has :func:`remove_data_set` to say so with.

    ``ignore_supervision`` and ``update_conf_rev`` behave as
    :func:`remove_data_set`'s.

    Raises :class:`~py61850.scl.EditRejected` if anything in ``edits`` is not
    a `Remove` of an `FCDA` that sits in a `DataSet`, or if the removals would
    leave a dataset empty.
    """
    if not ignore_supervision:
        raise EditRejected(
            "subscription supervision is not written yet; "
            "ignore_supervision=False has nothing to turn off")
    # A single edit of any type is wrapped rather than iterated: `list()` on
    # one that is not a `Remove` raises `TypeError`, and a caller who passed
    # the wrong edit deserves the EditRejected below naming what they passed.
    edits = list(edits) if isinstance(edits, (list, tuple)) else [edits]

    #: Datasets in the order they were first met, each with the members this
    #: call takes out of it. `id()` keys because an Element is not hashable by
    #: value and two datasets can carry the same name in different IEDs.
    affected: dict = {}
    for edit in edits:
        if not isinstance(edit, Remove):
            raise EditRejected(
                f"remove_fcda takes Remove edits, not {type(edit).__name__}")
        fcda = edit.node
        if not _is(fcda, "FCDA"):
            raise EditRejected(
                f"{strip_ns(fcda.tag) or type(fcda).__name__} is not an FCDA")
        data_set = doc.parent_of(fcda)
        if not _is(data_set, "DataSet"):
            raise EditRejected(
                "an FCDA outside a DataSet is not a dataset member; "
                f"this one's parent is "
                f"{'nothing' if data_set is None else strip_ns(data_set.tag)}")
        affected.setdefault(id(data_set), (data_set, []))[1].append(fcda)

    for data_set, going in affected.values():
        remaining = [member for member in _same_ns_children(data_set, "FCDA")
                     if not any(member is gone for gone in going)]
        if not remaining:
            raise EditRejected(
                f"removing {len(going)} member(s) would leave DataSet "
                f"{data_set.get('name')!r} empty, and 61850-6 requires at "
                f"least one FCDA; remove the DataSet itself instead")

    out: List = list(edits)
    handled: set = set()
    for _data_set, going in affected.values():
        subscribers = [ext_ref for ext_ref in _subscriptions_to(doc, going)
                       if id(ext_ref) not in handled]
        handled.update(id(ext_ref) for ext_ref in subscribers)
        if subscribers:
            out.extend(unsubscribe(doc, subscribers))
    if update_conf_rev:
        for data_set, _going in affected.values():
            out.extend(updated_conf_rev_edits(doc, data_set))
    return out
