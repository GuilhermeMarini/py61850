# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""Renaming a `Substation`, a `VoltageLevel` or a `Bay`, and removing a piece of the primary system.

    from py61850.scl import (Remove, SclDocument, SetAttributes,
                             remove_process_element, update_bay,
                             update_substation, update_voltage_level)

    doc = SclDocument.parse("station.ssd")
    doc.apply_edit(update_bay(doc, SetAttributes(bay, {"name": "Q02"})))
    doc.apply_edit(remove_process_element(doc, Remove(bay)))

**A container's name is written down four more times than on the container.**
A `Bay` called ``Q01`` is named by its own `@name`, by the `pathName` of every
`ConnectivityNode` inside it, by the `@connectivityNode` of every `Terminal`
pointing at one of those nodes, and by that `Terminal`'s own `@bayName`.
Renaming the `Bay` element alone leaves a document that loads and is wrong: a
`ConnectivityNode` whose path names a bay that does not exist, and terminals
pointing into it.

**These are edit checks** in the reference's sense -- the caller's own edit
goes in, the corrected and expanded one comes back, input edit first, as Q23
settled for A9 onward::

    edits = update_bay(doc, SetAttributes(bay, {"name": "Q02"}))
    undo = doc.apply_edit(edits)     # one history entry

``doc`` goes in front of the caller's edit because `ElementTree` has no owner
document, which is the same divergence every edit check in this package
carries. The reference's `updateBay`, `updateVoltageLevel` and
`updateSubstation` take the `SetAttributes` alone.

**Nothing here applies anything**, and a refusal raises
:class:`~py61850.scl.EditRejected` with nothing half-done.

## What "cross-referenced elements" means, and how the schema was asked

The reference documents all three as updating *"attributes and
cross-referenced elements"* and says nothing about WHICH -- which is exactly
what `updateIED` did. A13 answered its version by asking the XSD which complex
types declare an `iedName` attribute and getting an exhaustive six. **The same
question here has an even sharper answer: one complex type and one attribute
each.**

Resolving attributes through `xs:extension` bases and `xs:attributeGroup`
references over all 153 complex types of `2007B4` and all 165 of `2007C5`:

======================================================  =================  ==========================
attribute                                               declared by        elements typed by it
======================================================  =================  ==========================
``substationName`` ``voltageLevelName`` ``bayName``
``lineName`` ``processName`` ``cNodeName``
``connectivityNode``                                    **tTerminal**      `Terminal`, `NeutralPoint`
``pathName``                                            tConnectivityNode  `ConnectivityNode`
======================================================  =================  ==========================

Identical in both editions; Edition 2.1 adds none. Two things in that table
are wider than they look and both are the schema's, not a choice:

**`NeutralPoint` IS a `Terminal`.** ``<xs:element name="NeutralPoint"
type="tTerminal" minOccurs="0"/>`` inside `tTransformerWinding` -- so a
transformer neutral carries every one of those seven attributes and a rename
that swept `Terminal` alone would leave it naming a bay that is gone.
:data:`TERMINAL_ELEMENTS` is the pair.

**There are FIVE container attributes, not three.** `lineName` and
`processName` exist because a `Terminal` can sit under a `Line` or a `Process`
as easily as under a `Bay`, and A7's content-model table already places both.
:data:`CONTAINER_NAME_ATTRIBUTES` is the mapping. This module renames the
three the reference exports functions for; the other two are in the table
because the resolution machinery reads them and a later phase that adds
``update_line`` needs no second derivation.

## What the document-level constraints add

`SCL.xsd` hangs four identity constraints on the `SCL` element itself, and
three of them are about this module:

- **`SubstationKey`** -- selector ``./scl:Substation|./scl:Process|./scl:Line``,
  field ``@name``. **A `Substation` shares one name space with `Process` and
  `Line`**, and with nothing else: an `IED` named the same is legal, because
  `IEDKey` is a separate key over ``./scl:IED``.
- **`ref2SubstationFromTerminal`** -- selector ``.//scl:Terminal``, field
  ``@substationName``, referring to `SubstationKey`. **The schema itself
  declares the referential link** the reference's doc comment only promises.
  It is declared for `substationName` alone; the other four container
  attributes are convention, and `pathName` and `connectivityNode` have no
  keyref at all.
- **`ConnectivityNodeKey`** -- selector ``.//scl:ConnectivityNode``, field
  ``@pathName``. **A path is unique across the whole document**, which is what
  makes resolving one a lookup rather than a search.

And the containers each carry a ``uniqueChildNameIn...`` constraint whose
selector is ``./*`` and whose field is ``@name`` --
`uniqueChildNameInSubstation`, `uniqueChildNameInVoltageLevel`,
`uniqueChildNameInBay` among them. See :func:`_collision`.

## A path is RESOLVED, never split -- and the reason is measured

`tConnectivityNodeReference` is ``xs:normalizedString`` restricted by the
pattern ``.+/.+(/.+)*``, so a path looks decomposable in a way an LDName did
not. **It is not.** `tName` is ``xs:normalizedString`` with ``minLength 1``
and **no pattern at all**, so nothing in the standard stops a bay being called
``A/B`` -- and a path built from it splits two ways. That is Q31 §2's problem
reached from the opposite direction: A13 had no delimiter to split on, and
this has one that is not reserved.

So this module does what A13 did. It indexes every `ConnectivityNode` the
document actually contains by its `pathName`, rebuilds the path of each one it
is moving **from that node's own ancestry**, and rewrites a `Terminal` only
when that terminal's `@connectivityNode` resolves through the index to a node
that moved. A reference resolving to nothing is **left alone**; a path two
different nodes carry resolves to nothing, because such a document already
violates `ConnectivityNodeKey` and declining to guess is the only answer that
cannot make it worse.

## Why a location sweep is wrong, and the two elements that prove it

The obvious alternative -- rewrite the `@bayName` of every `Terminal` INSIDE
the bay -- is wrong in both directions, and IEC's own published example says
so on 2 of its 14 terminals.

`Eng POC.ssd`, from `IEC_TR_61850-90-30.SSD.2024A1`, holds one `Substation`,
one `VoltageLevel`, three `Bay` (``MY_Bay``, ``R1``, ``R2``), seven
`ConnectivityNode` and fourteen `Terminal`. Twelve of the fourteen sit inside
``MY_Bay`` and carry ``bayName="MY_Bay"``. **The other two also sit inside
``MY_Bay`` and carry ``bayName="R1"`` and ``bayName="R2"``**, because they
point at a `ConnectivityNode` that lives in a different bay:

===========================  ===============================================
terminal                     names
===========================  ===============================================
in ``MY_Bay``/``SR1``        ``MY_Substation/MY_VoltageLevel/R1/L1``
in ``MY_Bay``/``SR2``        ``MY_Substation/MY_VoltageLevel/R2/L1``
===========================  ===============================================

**A container attribute describes where the NODE is, not where the terminal
is.** So renaming ``MY_Bay`` must leave those two alone although they are
inside it, and renaming ``R1`` must rewrite one of them although it is
nowhere near it. A location sweep gets both cases wrong; resolving through
`@connectivityNode` gets both right, and that is the whole argument for the
shape of this module.

The same file carries the case that makes `cNodeName` insufficient on its own:
**two `ConnectivityNode` elements are both named ``L1``**, one in ``R1`` and
one in ``R2``. Only the full path tells them apart.

## What was measured, and what could not ship

**The three reference exports contain none of this.** All twenty-three
Substation-section element names -- the fifteen obvious ones plus
`NeutralPoint`, `TransformerWinding`, `SubEquipment`, `GeneralEquipment`,
`EqFunction`, `EqSubFunction`, `TapChanger` and `Voltage` -- are **zero in
`sel.scd`, `mixed.scd` and `siemens.scd`, in every namespace**. Not thin
material: none. `tests/unit/test_scl_substation.py` asserts that emptiness, so
the day a Substation-carrying fixture arrives this module's guard story
changes loudly rather than silently.

The measurements quoted above come from IEC's own example files, which are
**IEC copyright and ship with nothing**: they are excluded from this
repository for the reason its `.gitignore` gives for the schema itself. They
were read to check the conventions this module implements, and the built
fixtures in the test module are shaped to match what they show. Nothing
derived from them is published here beyond the counts in this docstring, on
the reading of the code-component terms that Q17 settled for the ordering
table.

## What this module does not do

- **It does not touch `@cNodeName`.** That is the connectivity node's OWN
  name, which renaming its container does not change. All 14 of the example's
  terminals carry the leaf of their path there and would be corrupted by a
  sweep that treated it as part of the container path.
- **It does not invent an absent optional attribute.** `substationName`,
  `voltageLevelName`, `bayName`, `lineName` and `processName` are all
  ``use="optional"``; a file that omits one has made a choice, and a rename is
  not the edit that should reverse it.
- **It does not repair an attribute that was already wrong.** A container
  attribute is followed when it holds the OLD name. One holding something else
  on a terminal whose path resolves into the renamed element is left, because
  the document was inconsistent before the call and repairing it is a
  different edit from the one that was asked for.
- **It does not cascade into `DataTypeTemplates`.** An `LNodeType` that a
  removed `LNode` was the last user of stays, exactly as `remove_ied` leaves
  one: the caller removed a piece of the primary system, not a type.
"""

from __future__ import annotations

from typing import Dict, List, Optional
from xml.etree import ElementTree as ET

from .document import strip_ns
from .edit import EditRejected, Remove, SetAttributes

#: The two elements typed `tTerminal`, and therefore the two that carry the
#: container-name attributes a rename has to follow. **Exhaustive against the
#: schema**: `tTerminal` is the only complex type in 61850-6 declaring any of
#: them, and `Terminal` and `NeutralPoint` are the only elements typed by it.
#: `NeutralPoint` is declared inside `tTransformerWinding`; the module
#: docstring carries the derivation.
TERMINAL_ELEMENTS = ("Terminal", "NeutralPoint")

#: ``{container element: the `tTerminal` attribute naming it}``. **Five, not
#: the three this phase renames** -- a `Terminal` may sit under a `Line` or a
#: `Process` as readily as under a `Bay`, and A7's content-model table already
#: places both. The two extra rows are here because the resolution machinery
#: reads them and because a later ``update_line`` should not have to derive
#: this a second time.
CONTAINER_NAME_ATTRIBUTES = {
    "Substation": "substationName",
    "VoltageLevel": "voltageLevelName",
    "Bay": "bayName",
    "Line": "lineName",
    "Process": "processName",
}

#: The three elements that root a description of the primary system, and the
#: three `SubstationKey` selects on: ``./scl:Substation|./scl:Process|./scl:Line``.
#: :func:`remove_process_element` requires its node to be one of these or to
#: sit inside one.
PROCESS_SECTIONS = ("Substation", "Line", "Process")


# -- namespace-exact access -------------------------------------------------
#
# These three are `ied.py`'s, spelled again rather than imported: a module
# about the Substation section should not depend on the module about IEDs for
# namespace arithmetic, and the alternative -- reaching across for a private
# name -- is worse. **If a third caller needs them they belong in
# `document.py`**, which both already import, exactly as `_retext` was moved
# down to `supervision.py` when its third call site appeared.

def _namespace(doc) -> str:
    """``"{uri}"`` for this document, or ``""`` when it declares none."""
    tag = doc.root.tag
    return tag[:tag.index("}") + 1] if tag.startswith("{") else ""


def _qualified(doc, local_name: str) -> str:
    return _namespace(doc) + local_name


def _own(doc, local_name: str):
    """Every descendant of the root with this name **in the document's own
    namespace**, which is not the question :func:`~py61850.scl.iter_local`
    asks.

    A16 has no measured instance of the shadowing that makes this matter --
    the corpus carries none of these elements at all -- but A8, A10, A12 and
    A13 each found a vendor element of a standard local name, and building the
    one reader in this package that would be fooled by the next one is not a
    saving.
    """
    return doc.root.iter(_qualified(doc, local_name))


def _describe(element) -> str:
    if not isinstance(element, ET.Element):
        return type(element).__name__
    tag = element.tag
    return strip_ns(tag) if isinstance(tag, str) else "a comment"


def _is(doc, element, local_name: str) -> bool:
    return (isinstance(element, ET.Element)
            and element.tag == _qualified(doc, local_name))


# -- paths ------------------------------------------------------------------

def _named_ancestors(doc, element) -> List[ET.Element]:
    """``element``'s ancestors below the root that carry a ``name``, outermost
    first.

    The root itself is excluded -- `SCL` has no name and a path never starts
    with one -- and so is any ancestor without a `name`, which in practice is
    none of them: every container in the Substation section extends
    `tNaming`.
    """
    chain: List[ET.Element] = []
    cursor = doc.parent_of(element)
    while cursor is not None and cursor is not doc.root:
        if cursor.get("name") is not None:
            chain.append(cursor)
        cursor = doc.parent_of(cursor)
    chain.reverse()
    return chain


def _path_of(doc, node, renamed=None) -> Optional[str]:
    """The `pathName` ``node`` should carry, built from its own ancestry.

    ``renamed`` is an optional ``(element, new name)`` pair: where that element
    appears in the ancestry, the new name is used instead of the one currently
    on it. That is what lets a rename compute the path a node WILL have while
    the tree still says otherwise -- the edit has not been applied, and
    computing the inverse from the tree as it stands is A5's rule.

    ``None`` when the node itself has no `name`, since a path's last segment
    is the node's own and there is nothing to end it with.

    **The convention is not in the XSD and is measured rather than assumed.**
    `tConnectivityNodeReference`'s pattern is only ``.+/.+(/.+)*``. IEC's
    `Eng POC.ssd` carries the answer on **7 of 7** connectivity nodes: the
    path is the ``/``-joined `name` of every named ancestor, outermost first,
    with the node's own `name` last -- ``MY_Substation/MY_VoltageLevel/R1/L1``
    for an `L1` in bay `R1`.
    """
    own = node.get("name")
    if own is None:
        return None
    target, fresh = renamed if renamed is not None else (None, None)
    segments = [fresh if ancestor is target else ancestor.get("name")
                for ancestor in _named_ancestors(doc, node)]
    segments.append(own)
    return "/".join(segment or "" for segment in segments)


def _connectivity_nodes(doc) -> List[ET.Element]:
    return list(_own(doc, "ConnectivityNode"))


def _connectivity_index(doc) -> Dict[str, Optional[ET.Element]]:
    """``{pathName: the node carrying it}``, and ``None`` where two carry one.

    This is what turns a `Terminal`'s `@connectivityNode` from a string that
    would have to be split into an element that is known to exist. Built per
    call rather than cached, for the reason
    :func:`~py61850.scl.ied._object_reference_index` gives: these functions
    build an edit and hand it back, and a cache the caller cannot invalidate
    is the hazard Q14 is about.

    **A path two different nodes carry maps to ``None``**, which every caller
    here reads as "leave it alone". `ConnectivityNodeKey` makes such a
    document invalid already, and an ambiguous reference is not this
    function's to resolve by picking the first.
    """
    index: Dict[str, Optional[ET.Element]] = {}
    for node in _connectivity_nodes(doc):
        path = node.get("pathName")
        if not path:
            continue
        if path in index and index[path] is not node:
            index[path] = None
        else:
            index.setdefault(path, node)
    return index


def _terminals(doc) -> List[ET.Element]:
    """Every `Terminal` and every `NeutralPoint`, namespace-exact.

    Both, because `tTerminal` types both -- see :data:`TERMINAL_ELEMENTS`.
    """
    out: List[ET.Element] = []
    for local_name in TERMINAL_ELEMENTS:
        out.extend(_own(doc, local_name))
    return out


# -- renaming ---------------------------------------------------------------

def _collision(doc, element, new_name: str) -> Optional[ET.Element]:
    """The sibling ``new_name`` would collide with, or ``None``.

    **The set is the schema's own, and it is not the same at every level.**

    At the document root the constraint is `SubstationKey`, whose selector is
    ``./scl:Substation|./scl:Process|./scl:Line``: a root `Substation` shares
    one name space with those two and with nothing else. An `IED` of the same
    name is legal, because `IEDKey` is a separate key.

    Anywhere else the constraint is the parent's ``uniqueChildNameIn...``,
    whose selector is ``./*`` and whose field is ``@name`` -- so **every named
    sibling counts, whatever its tag.** A `Bay` renamed inside a
    `VoltageLevel` collides with the other bays and equally with a
    `PowerTransformer`, a `GeneralEquipment` or a `Function` of that name.
    That is wider than a caller expects and it is written in the XSD, not
    inferred: `uniqueChildNameInVoltageLevel`, `uniqueChildNameInSubstation`
    and `uniqueChildNameInBay` all select ``./*``.
    """
    parent = doc.parent_of(element)
    if parent is None:
        return None
    if parent is doc.root:
        wanted = {_qualified(doc, name) for name in PROCESS_SECTIONS}
        siblings = [child for child in parent if child.tag in wanted]
    else:
        siblings = list(parent)
    return next((other for other in siblings
                 if other is not element and other.get("name") == new_name),
                None)


def _rename_edits(doc, edit, local_name: str) -> List:
    """``edit`` expanded: the rename, and every reference that has to follow.

    The engine behind :func:`update_substation`, :func:`update_voltage_level`
    and :func:`update_bay`, which differ only in the element they accept and
    the `tTerminal` attribute that names it. Three public names over one
    computation, rather than three implementations that can drift -- Q25's
    rule, and the reason this phase opens one module and not three.
    """
    if not isinstance(edit, SetAttributes):
        raise EditRejected(
            f"update_{_snake(local_name)} takes a SetAttributes, not "
            f"{type(edit).__name__}")
    element = edit.element
    if not _is(doc, element, local_name):
        raise EditRejected(
            f"{_describe(element)} is not a {local_name} of this document, in "
            f"the document's own namespace")
    if doc.parent_of(element) is None:
        raise EditRejected(
            f"this {local_name} is not in the document; an edit check reads "
            f"the tree the element sits in")
    if "name" not in edit.attributes:
        return [edit]

    new_name = edit.attributes["name"]
    old_name = element.get("name")
    if new_name == old_name:
        return [edit]
    if not new_name:
        raise EditRejected(
            f"a {local_name} name is required; tNaming@name is "
            f'use="required" and every reference to it is written by name')

    clash = _collision(doc, element, new_name)
    if clash is not None:
        raise EditRejected(
            f"renaming {local_name} {old_name!r} to {new_name!r} would "
            f"collide with the {_describe(clash)} already named "
            f"{new_name!r}; the schema gives a container's children one name "
            f"space across all of their tags")

    edits: List = [edit]
    if old_name is None:
        # Nothing can refer to it: a path is built from ancestor names and an
        # ancestor with no name cannot have contributed one. The rename is
        # the whole edit.
        return edits

    inside = set(element.iter())
    attribute = CONTAINER_NAME_ATTRIBUTES[local_name]

    # 1. The connectivity nodes underneath, whose paths carry the old name.
    #    Computed from each node's own ancestry with the new name substituted,
    #    never by editing the old string -- the module docstring says why a
    #    path cannot be split.
    moved: Dict[str, str] = {}
    for node in _connectivity_nodes(doc):
        if node not in inside:
            continue
        was = node.get("pathName")
        # Only a path that AGREES with the ancestry it has now is rewritten.
        # One that does not was wrong before this call, and replacing it
        # wholesale would be the repair the module docstring says a rename is
        # not -- the same restraint the container attributes get below.
        if was is None or was != _path_of(doc, node):
            continue
        now = _path_of(doc, node, renamed=(element, new_name))
        if now is None or now == was:
            continue
        edits.append(SetAttributes(node, {"pathName": now}))
        moved[was] = now

    # 2. The terminals pointing at one of them, wherever in the document they
    #    live. `moved` is keyed by the path each node carries NOW, which is
    #    what a terminal's `@connectivityNode` holds now, so the lookup needs
    #    no index -- the index below is what proves the path was unambiguous.
    ambiguous = {path for path, node in _connectivity_index(doc).items()
                 if node is None}
    for terminal in _terminals(doc):
        reference = terminal.get("connectivityNode")
        if not reference or reference in ambiguous or reference not in moved:
            continue
        wanted: Dict[str, Optional[str]] = {
            "connectivityNode": moved[reference]}
        if terminal.get(attribute) == old_name:
            wanted[attribute] = new_name
        # **Ordered by the ELEMENT's own attributes, not by this dict.**
        # `SetAttributes` reads a mapping that names every attribute the
        # element currently has as a complete, ordered description and rebuilds
        # them in the mapping's order -- the rule that makes a deletion
        # invertible. A terminal carrying nothing but `connectivityNode` and
        # one container name is exactly such an element, so a mapping built in
        # this function's order would silently reorder the file's attributes.
        # The undo still restores the bytes, which is why a round-trip test
        # cannot see it; what it produces is a whole-file diff where the
        # engineer asked for a rename. Found by reading the serialised result.
        changes = {name: wanted[name] for name in terminal.keys()
                   if name in wanted}
        edits.append(SetAttributes(terminal, changes))
    return edits


def _snake(local_name: str) -> str:
    out = []
    for index, character in enumerate(local_name):
        if character.isupper() and index:
            out.append("_")
        out.append(character.lower())
    return "".join(out)


def update_substation(doc, edit) -> List:
    """``edit`` expanded: a `Substation` rename, and everything that named it.

    ``edit`` is a :class:`~py61850.scl.SetAttributes` on a `Substation`. If it
    does not touch `name`, or sets it to what it already is, the edit comes
    back alone -- `desc` is the substation's own and nothing refers to it.

    A rename returns, after the caller's own edit:

    1. every `ConnectivityNode` inside it, with its `pathName` rebuilt from
       its ancestry;
    2. every `Terminal` and `NeutralPoint` whose `@connectivityNode` is one of
       those paths, with the reference updated and with `@substationName`
       updated where it held the old name.

    **The collision set is `SubstationKey`'s** -- ``Substation``, ``Process``
    and ``Line`` at the document root -- so renaming onto a `Line`'s name is
    refused and renaming onto an `IED`'s is not. A `Substation` nested inside
    a `Process` is governed by that `Process`'s ``uniqueChildNameInProcess``
    instead, which counts every named child; :func:`_collision` has both.

    Raises :class:`~py61850.scl.EditRejected` if ``edit`` is not a
    `SetAttributes` on a `Substation` of this document, if the new name is
    empty, or if it collides.
    """
    return _rename_edits(doc, edit, "Substation")


def update_voltage_level(doc, edit) -> List:
    """``edit`` expanded: a `VoltageLevel` rename, and everything that named
    it.

    The shape is :func:`update_substation`'s, with `@voltageLevelName` as the
    `tTerminal` attribute and segment two of the path as the part rebuilt.

    **The collision set is every named sibling of the parent `Substation`**,
    not merely the other voltage levels: `uniqueChildNameInSubstation` selects
    ``./*``, so a `PowerTransformer`, a `GeneralEquipment` or a `Function` of
    that name refuses the rename too.
    """
    return _rename_edits(doc, edit, "VoltageLevel")


def update_bay(doc, edit) -> List:
    """``edit`` expanded: a `Bay` rename, and everything that named it.

    The shape is :func:`update_substation`'s, with `@bayName` as the
    `tTerminal` attribute and segment three of the path as the part rebuilt.

    **A terminal INSIDE this bay is not necessarily one of them**, and the
    module docstring has IEC's own example measuring exactly that: two of
    `Eng POC.ssd`'s fourteen terminals sit in ``MY_Bay`` and name ``R1`` and
    ``R2``, because a container attribute describes where the connectivity
    node is rather than where the terminal is. What decides is whether the
    terminal's `@connectivityNode` resolves into this bay.

    **The collision set is every named sibling of the parent `VoltageLevel`**
    -- `uniqueChildNameInVoltageLevel` selects ``./*``.
    """
    return _rename_edits(doc, edit, "Bay")


# -- removing ---------------------------------------------------------------

def _process_root(doc, element) -> Optional[ET.Element]:
    """The `Substation`, `Line` or `Process` ``element`` is in, or ``None``.

    ``element`` itself counts, so removing a whole `Substation` is in scope.
    """
    wanted = {_qualified(doc, name) for name in PROCESS_SECTIONS}
    cursor = element
    while cursor is not None:
        if cursor.tag in wanted:
            return cursor
        cursor = doc.parent_of(cursor)
    return None


def remove_process_element(doc, edit) -> List:
    """``edit`` expanded: the removal, and the elements it orphans.

    ``edit`` is a :class:`~py61850.scl.Remove` whose node is in the primary
    system description -- a `Substation`, `Line` or `Process`, or anything
    inside one. What comes back is the removal as given, followed by:

    1. every `Terminal` and `NeutralPoint` OUTSIDE the removed subtree whose
       `@connectivityNode` names a `ConnectivityNode` INSIDE it. Such a
       terminal points at a node that is about to stop existing, and it is the
       reference's own *"orphan Terminal"*;
    2. every `ConnectivityNode` outside the subtree that no surviving terminal
       points at any more -- the reference's *"orphan ConnectivityNode"*.

    **An orphan connectivity node is one with ZERO terminals left, not one
    with fewer than two.** A node joining a single piece of equipment is
    electrically pointless and structurally legal, and the schema does not
    grade it; removing an element the caller did not name on a topology
    inference is the larger action that Q27 and Q33 §3 settled should be asked
    for rather than arrived at. Zero is the reading that cannot destroy
    something the engineer meant.

    **Two passes are enough and a third would find nothing.** Removing a
    terminal cannot orphan a node it did not point at, and a node removed in
    pass two has by definition no surviving terminal pointing at it, so there
    is nothing for a pass three to collect. The prune is therefore complete
    without a fixpoint, unlike
    :func:`~py61850.scl.remove_data_type`'s, where a type can reach another.

    **A reference that resolves to nothing is left alone**, and so is one two
    nodes share: the module docstring's rule, applied here as in the renames.

    **`DataTypeTemplates` is not touched**, as `remove_ied` does not touch it:
    an `LNodeType` that a removed `LNode` was the last user of is ordinary.

    Raises :class:`~py61850.scl.EditRejected` if ``edit`` is not a `Remove`,
    if its node is not in this document, or if the node is outside every
    `Substation`, `Line` and `Process` -- which is the case for an `IED`,
    whose removal is :func:`~py61850.scl.remove_ied`'s.
    """
    if not isinstance(edit, Remove):
        raise EditRejected(
            f"remove_process_element takes a Remove, not "
            f"{type(edit).__name__}")
    node = edit.node
    if not isinstance(node, ET.Element):
        raise EditRejected(
            f"remove_process_element removes an element, not "
            f"{_describe(node)}")
    if doc.parent_of(node) is None and node is not doc.root:
        raise EditRejected(
            "that element is not in this document")
    if _process_root(doc, node) is None:
        raise EditRejected(
            f"a {_describe(node)} outside every Substation, Line and Process "
            f"is not a process element; removing an IED is remove_ied's")

    edits: List = [edit]
    inside = set(node.iter())

    going = {cn.get("pathName") for cn in _connectivity_nodes(doc)
             if cn in inside and cn.get("pathName")}
    ambiguous = {path for path, found in _connectivity_index(doc).items()
                 if found is None}

    # Pass one: the terminals left pointing into what is going.
    orphan_terminals = [terminal for terminal in _terminals(doc)
                        if terminal not in inside
                        and terminal.get("connectivityNode") in going
                        and terminal.get("connectivityNode") not in ambiguous]
    edits.extend(Remove(terminal) for terminal in orphan_terminals)

    # Pass two: the nodes nothing points at any more. `survivors` counts what
    # will still be in the document once the removal and pass one have been
    # applied -- the tree still holds all of it, which is why this is counted
    # rather than observed. Q33 §9, Q34 §4 and Q35 §7 are three phases running
    # where reading the RESULT rather than the edit list was the only method
    # that worked, and this is the same shape one level down.
    doomed = inside.union(orphan_terminals)
    pointed_at = {terminal.get("connectivityNode")
                  for terminal in _terminals(doc) if terminal not in doomed}
    for cn in _connectivity_nodes(doc):
        if cn in inside:
            continue
        path = cn.get("pathName")
        if path and path not in pointed_at:
            edits.append(Remove(cn))
    return edits
