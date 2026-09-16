# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""Renaming a `Substation`, a `VoltageLevel` or a `Bay`, removing a piece of the primary system, and pruning an `LNode`'s specification.

    from py61850.scl import (Remove, SclDocument, SetAttributes,
                             prune_lnode_specification,
                             remove_process_element, update_bay,
                             update_substation, update_voltage_level)

    doc = SclDocument.parse("station.ssd")
    doc.apply_edit(update_bay(doc, SetAttributes(bay, {"name": "Q02"})))
    doc.apply_edit(remove_process_element(doc, Remove(bay)))
    doc.apply_edit(prune_lnode_specification(doc, "SE_PTOC_SET_V002"))

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

  :func:`prune_lnode_specification` READS that section and still does not
  change it. The direction is the distinction: it asks the pool what a type
  declares and edits the `LNode` accordingly, never the other way round.

## The specification children: a SECOND IEC namespace inside this section

:func:`prune_lnode_specification` is the odd one out here and the rest of this
docstring is about why it is here anyway.

An `LNode` says *which* logical node a device must provide. IEC **TR**
61850-6-100 adds what that node must contain -- `DOS` for a data object, `SDS`
for a structured child of one, `DAS` for a data attribute -- and they are the
engineer's requirement, written before any device exists. When the `LNodeType`
behind the `LNode` changes, the specification children the new declaration
cannot carry have to go.

**None of the three is in 61850-6.** Zero occurrences of `DOS`, `SDS` or `DAS`
in the `2007B4` XSD set and zero in `2007C5`. They belong to
``{http://www.iec.ch/61850/2019/SCL/6-100}``, whose `targetNamespace` is
identical in the TR's `2019B9` and `2019C1` editions and whose content models
for these three are identical between them, so a reader keyed on the namespace
is edition-stable.

**Only `DOS` is a global element.** `SDS` and `DAS` are declared solely as
local elements inside ``tDOS`` and ``tSDS``, so they can appear nowhere but
under a `DOS`. That makes `DOS` the single attachment point, and it is what
lets this module look for one thing rather than three.

## Where a `DOS` may sit -- two places, and the file convention is not the schema

``tBaseElement``, which **every** SCL element extends, opens with
``<xs:any namespace="##other" processContents="lax" minOccurs="0"
maxOccurs="unbounded"/>`` before `Text` and `Private` -- identical in `2007B4`
and `2007C5`. So a `DOS` is schema-valid **directly under `LNode`**, which is
literally what the TR's own annotation asks for: *"Data Object specification.
To be added to LNode SCL element"*. It is equally valid inside a `Private`,
whose ``tAnyContentFromOtherNamespace`` takes ``##other`` too.

IEC's own example SSDs put **106 of 106** at
``LNode/Private[@type="eIEC61850-6-100"]/DOS`` -- and **that `@type` string
appears nowhere in the 6-100 XSD.** It is the files' convention; it happens to
equal the schema's own namespace PREFIX. So this module finds a `DOS` by its
namespace-qualified tag, among an `LNode`'s own children and among the
children of any of its `Private` elements, and never reads `Private@type` at
all. Both attachment points are covered and neither convention is required.

**This is not the vendor seam, although the mechanism is identical.** The rule
this package states for `Private` is that a **vendor** concept never enters
`py61850.scl` -- and its own test is whether the code would have to carry a
vendor name. This carries an IEC namespace URI and IEC element names,
published by IEC in a TR that is part of 61850-90-30 and 7-6. The boundary the
`Private` rule draws is therefore about **who published the namespace**, not
about the mechanism: `sellib` reads ``Private[@type="SEL_..."]`` and
`siemenslib` reads Siemens's, and neither reads ``eIEC61850-6-100``.

**And the namespace is live material, not a convention known only from IEC's
example files.** `mixed.scd` declares it on the root, with the TR's own
``version``/``revision``/``release`` attributes beside the SCL edition's, and
carries **17 elements in it** -- one `ServiceSpecifications` holding sixteen
`SMVParameters`, in a root-level `Private` sitting alongside five
Siemens-private blocks. What that export has none of is the part this function
reads: **0 `LNode`**, so nothing specifies anything, and the prune over all
**277** of its `LNodeType` emits nothing and leaves the bytes alone.
`sel.scd` and `siemens.scd` do not declare the namespace at all.
`TestSpecificationCorpus` asserts all of it.

## What "missing" means, and the two spellings that do not mean what they say

`DOS@name` is `scl:tDataName` and resolves against the `LNodeType`'s `DO`
names. Below that:

- ``uniqueDAorSDOInDOType`` has selector ``./*`` and field ``@name``, so
  **inside one `DOType` an `SDO` and a `DA` share one name space** and a name
  can never be both. The lookup order is therefore not a choice. So are
  ``uniqueDOInLNodeType`` and ``uniqueBDAInDAType``: the name is a key at
  every level of the walk.
- **`SDS` is not a sub-data-object, whatever its annotation says.** Its
  ``@name`` is typed `scl:tAttributeNameEnum`, not `tDataName`, and in
  `Eng POC.ssd` **all 17 `SDS` resolve to a `DA` with ``bType="Struct"`` and
  not one resolves to an `SDO`** -- ``crvPts``, ``xUnits``, ``yUnits`` under a
  CURVE-shaped DO, ``setMag`` under an ASG.
- **`DAS` is a leaf in the schema.** Its ``xs:choice`` holds `SubscriberLNode`,
  `ControllingLNode`, `ProcessEcho`, `LogParametersRef`, `Val` and `Labels`,
  and no nested `DAS` or `SDS`. So the only spelling available for depth --
  whether the depth comes from an `SDO` or from a `Struct` `DA`/`BDA` -- is
  `SDS`.

**So a child is matched by `@name` in its parent's resolved type and the
spelling is ignored.** A resolver that required ``SDS`` to name an `SDO` would
delete every one of IEC's seventeen.

## It removes, and it removes a fifth of IEC's own file

The reference returns ``Remove[]`` -- not ``EditV2[]`` -- so it is purely
subtractive by declaration as well as by its doc comment: a `DOS` the type
declares and the instance lacks is never invented. Ours matches, and only the
HIGHEST missing element is removed, because a `Remove` of a `DOS` already
takes its `SDS` and `DAS` with it.

**That is not a quiet rule.** Resolved against their own `DataTypeTemplates`,
with 0 of the DOS-carrying `LNode` failing to resolve their `@lnType`:

===============================  =======  ============================
example file                       spec    removed by a strict prune
===============================  =======  ============================
`Eng POC.ssd`                        374   **74** -- 1 `DOS`, 73 `DAS`
`IEC 61850-90-30 examples.ssd`        31   4 directly, **17** with the
                                           subtrees they carry
===============================  =======  ============================

and the misses are systematic rather than scattered: **72 of `Eng POC`'s 73**
are ``DAS name="d"`` -- the CDC description attribute -- against trimmed
`DOType`s such as ``ELIA_SPS_basic_V001``, which declares ``stVal``, ``q`` and
``t`` and nothing else. The `DOS` misses are data objects the `LNodeType`
genuinely does not declare.

The strict reading is kept anyway, because an SSD whose `lnType` has been
re-pointed *should* lose what the new type cannot carry, and softening it
would invent a rule the reference does not have. The number is recorded here
so that a caller is not surprised by it.

## What the prune leaves alone

- **A type that does not resolve.** An `LNode` whose `@lnType` names an
  `LNodeType` the pool lacks, a `DO` whose `@type` names a missing `DOType`, a
  ``Struct`` `DA` whose `DAType` is absent: everything at and below that point
  is untouched. :class:`~py61850.scl.TemplatePool` tolerates a dangling
  reference on READ deliberately, because a trimmed ICD is a real file, and
  deleting a specification because the pool is trimmed is the opposite of what
  the caller asked for. It is the same restraint the renames above apply to a
  `@connectivityNode` that resolves to nothing.
- **An `LNode` with no `@lnType`.** One of the 90-30 example's sixteen has
  none; there is no type for anything to be missing from.
- **`@ix`.** ``SDS@ix`` and ``DAS@ix`` are array indices and the TR's key is
  ``(@name, @ix)``, but whether an index is within an array bound is a
  different question from whether the name exists -- and **0 `DA` or `BDA` in
  either example file carries ``@count``** to bound it with. 4 spec elements
  carry an ``ix``; all four resolve or fail on their name alone.
"""

from xml.etree import ElementTree as ET

from .document import strip_ns
from .edit import EditRejected, Remove, SetAttributes
from .templates import TemplatePool

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

#: IEC **TR** 61850-6-100's namespace, in `ElementTree`'s ``{uri}`` spelling.
#: The three specification elements live here and in neither SCL edition; the
#: `targetNamespace` is the same in the TR's `2019B9` and `2019C1`, so keying
#: on it rather than on a `Private@type` string is edition-stable. The module
#: docstring has the derivation.
SPECIFICATION_NS = "{http://www.iec.ch/61850/2019/SCL/6-100}"

#: The three elements :func:`prune_lnode_specification` removes, outermost
#: first. **Only `DOS` is a global element declaration**; `SDS` and `DAS` are
#: declared solely inside ``tDOS`` and ``tSDS``, so they can appear nowhere
#: but under a `DOS` and this module looks for one attachment point, not
#: three.
SPECIFICATION_ELEMENTS = ("DOS", "SDS", "DAS")


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

def _named_ancestors(doc, element) -> list[ET.Element]:
    """``element``'s ancestors below the root that carry a ``name``, outermost
    first.

    The root itself is excluded -- `SCL` has no name and a path never starts
    with one -- and so is any ancestor without a `name`, which in practice is
    none of them: every container in the Substation section extends
    `tNaming`.
    """
    chain: list[ET.Element] = []
    cursor = doc.parent_of(element)
    while cursor is not None and cursor is not doc.root:
        if cursor.get("name") is not None:
            chain.append(cursor)
        cursor = doc.parent_of(cursor)
    chain.reverse()
    return chain


def _path_of(doc, node, renamed=None) -> str | None:
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


def _connectivity_nodes(doc) -> list[ET.Element]:
    return list(_own(doc, "ConnectivityNode"))


def _connectivity_index(doc) -> dict[str, ET.Element | None]:
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
    index: dict[str, ET.Element | None] = {}
    for node in _connectivity_nodes(doc):
        path = node.get("pathName")
        if not path:
            continue
        if path in index and index[path] is not node:
            index[path] = None
        else:
            index.setdefault(path, node)
    return index


def _terminals(doc) -> list[ET.Element]:
    """Every `Terminal` and every `NeutralPoint`, namespace-exact.

    Both, because `tTerminal` types both -- see :data:`TERMINAL_ELEMENTS`.
    """
    out: list[ET.Element] = []
    for local_name in TERMINAL_ELEMENTS:
        out.extend(_own(doc, local_name))
    return out


# -- renaming ---------------------------------------------------------------

def _collision(doc, element, new_name: str) -> ET.Element | None:
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


def _rename_edits(doc, edit, local_name: str) -> list:
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

    edits: list = [edit]
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
    moved: dict[str, str] = {}
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
        wanted: dict[str, str | None] = {
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


def update_substation(doc, edit) -> list:
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


def update_voltage_level(doc, edit) -> list:
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


def update_bay(doc, edit) -> list:
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

def _process_root(doc, element) -> ET.Element | None:
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


def remove_process_element(doc, edit) -> list:
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

    edits: list = [edit]
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


# -- pruning an LNode's specification ---------------------------------------

def _spec(local_name: str) -> str:
    return SPECIFICATION_NS + local_name


def _specification_roots(doc, lnode) -> list[ET.Element]:
    """Every `DOS` attached to ``lnode``, in document order.

    **Both schema-valid attachment points.** ``tBaseElement`` opens with an
    ``xs:any namespace="##other"``, so a `DOS` may be a direct child of the
    `LNode` -- which is what IEC TR 61850-6-100's own annotation asks for --
    and `Private`'s ``tAnyContentFromOtherNamespace`` accepts one too. IEC's
    example files use the second on 106 of 106, with
    ``@type="eIEC61850-6-100"``, **a string that appears nowhere in the 6-100
    schema**. So the tag's namespace is what identifies a `DOS` here and
    `Private@type` is never read.

    A `Private` is descended one level and no further: a `DOS` nested deeper
    inside one belongs to whatever structure put it there.
    """
    found: list[ET.Element] = []
    for child in lnode:
        if child.tag == _spec("DOS"):
            found.append(child)
        elif isinstance(child.tag, str) and strip_ns(child.tag) == "Private":
            found.extend(grandchild for grandchild in child
                         if grandchild.tag == _spec("DOS"))
    return found


def _specification_children(node) -> list[ET.Element]:
    """The `SDS` and `DAS` directly under ``node``, in document order.

    Nothing else: a `DOS` may also hold `SubscriberLNode`, `ControllingLNode`,
    `ProcessEcho`, `LogParametersRef` and `Labels`, none of which names a
    member of a data type and none of which this function has any view on.
    """
    return [child for child in node
            if child.tag in (_spec("SDS"), _spec("DAS"))]


def _resolve_below(pool, context, name):
    """What ``name`` means inside ``context``, as the next context or a leaf.

    ``context`` is a :class:`~py61850.scl.DoTypeSpec` or the ``{name:
    AttributeSpec}`` mapping of a `DAType`. The answer is one of three:

    ============  =======================================================
    ``"gone"``    the type does not declare this name -- it is missing
    ``{}``        declared, and it declares no members of its own, so
                  anything written under it is missing in turn
    ``None``      declared, and its own type is not in the pool
    an object     declared, and this is the context for its children
    ============  =======================================================

    **A leaf is an EMPTY context rather than a separate answer.** A `DAS` has
    no children in the schema, but a `SDS` may, and one written over a
    ``BOOLEAN`` or a `Quality` names something the type does not declare at
    any depth. Returning the empty mapping makes the next level resolve that
    for itself instead of asking this function to special-case it.

    **The lookup order is not a choice.** ``uniqueDAorSDOInDOType`` selects
    ``./*`` on field ``@name``, so an `SDO` and a `DA` in one `DOType` share
    one name space and a name can never be both; ``uniqueDOInLNodeType`` and
    ``uniqueBDAInDAType`` make the same true one level up and one level down.

    **A declared name whose own type is missing resolves to ``None``**, which
    every caller reads as "leave it and everything under it alone". That is
    the module docstring's rule: a trimmed pool is a real file and deleting a
    specification because a `DOType` is absent is the opposite of the edit
    asked for.
    """
    attribute = None
    if hasattr(context, "sub_objects"):
        sub_object = context.sub_objects.get(name)
        if sub_object is not None:
            # An SDO: the next context is its own DOType, or None when the
            # pool does not carry it.
            return pool.do_type(sub_object)
        attribute = context.attributes.get(name)
    else:
        attribute = context.get(name)
    if attribute is None:
        return "gone"
    if attribute.btype != "Struct" or not attribute.type:
        return {}
    return pool.da_type(attribute.type)


def _missing_below(pool, node, context) -> list[ET.Element]:
    """The specification elements under ``node`` that ``context`` does not
    declare.

    **Only the highest one on each branch.** A :class:`~py61850.scl.Remove` of
    a `DOS` takes its `SDS` and `DAS` with it, so descending into a subtree
    that is already going would emit removals of nodes that no longer exist
    when they apply -- Q33 §9's hazard, which is why this stops at the element
    it removes rather than collecting every descendant.

    **The spelling is ignored and the name decides.** `SDS` is documented as a
    sub-data-object and is nothing of the kind: all 17 in `Eng POC.ssd`
    resolve to a ``Struct`` `DA`, and `tDAS` has no nested `DAS` or `SDS` at
    all, so `SDS` is simply the spelling depth takes. Matching on the element
    name instead of the attribute name would remove every one of them.
    """
    missing: list[ET.Element] = []
    for child in _specification_children(node):
        name = child.get("name")
        below = "gone" if name is None else _resolve_below(pool, context, name)
        if below == "gone":
            missing.append(child)
        elif below is not None:
            missing.extend(_missing_below(pool, child, below))
    return missing


def _declaring_pool(doc, id_, source):
    """The pool ``id_``'s declaration should be read from, and that
    declaration.

    ``source`` is ``None`` for the ordinary case -- the declaration is already
    in ``doc`` -- and a second :class:`~py61850.scl.SclDocument` when the
    prune is being composed with an import that has not been applied yet.
    **Names are what this function compares, and an import preserves them**
    whatever `on_conflict` does to the ids, so resolving the whole closure
    through the source's own pool gives the answer the target will have.
    """
    declaring = doc if source is None else source
    root = getattr(declaring, "root", None)
    if not isinstance(root, ET.Element):
        raise EditRejected(
            f"expected an SclDocument, not {type(declaring).__name__}; a "
            f"declaration is read from a document because an LNodeType's "
            f"DOType and DAType live in ITS DataTypeTemplates and "
            f"ElementTree elements carry no owner")
    pool = TemplatePool(root)
    declaration = pool.lnode_type(id_)
    if declaration is None:
        where = "target" if source is None else "source"
        raise EditRejected(
            f"the {where} document carries no LNodeType {id_!r}; a prune "
            f"compares an LNode's specification against a declaration, and "
            f"there is none to compare it with")
    return pool, declaration


def prune_lnode_specification(doc, id_, source=None) -> list:
    """Remove what `LNodeType` ``id_`` no longer declares, from every `LNode`
    using it.

    An `LNode` may carry a **specification** of what the logical node must
    contain -- `DOS` for a data object, `SDS` for a structured child of one,
    `DAS` for a data attribute. These are IEC **TR** 61850-6-100's, in
    :data:`SPECIFICATION_NS` and in neither SCL edition, and the module
    docstring has the derivation and the two places one may sit.

    ``id_`` is an `LNodeType` `id`. Every `LNode` in ``doc`` whose `@lnType`
    is that `id` is walked, and a :class:`~py61850.scl.Remove` comes back for
    each specification element the type does not declare -- **the outermost
    one on each branch only**, since removing a `DOS` removes its children
    with it. Nothing is added and nothing is renamed, which is the reference's
    own shape: `updateLnType` returns ``Remove[]``, not ``EditV2[]``.

    ``source`` is the document to read the declaration from, and defaults to
    ``doc`` itself. Passing one prunes against a declaration that has not
    landed yet, so this composes with
    :func:`~py61850.scl.update_lnode_type` into a single history entry::

        doc.apply_edit(update_lnode_type(doc, new, "SE_PTOC_SET_V002")
                       + prune_lnode_specification(doc, "SE_PTOC_SET_V002",
                                                   source=new))

    Without that, the prune would have to run after the update was applied and
    one intent would cost two undo steps. It is the same hazard Q33 §9, Q34 §5
    and Q35 §7 each met from a different side: inside a compound edit the
    document is out of date, so the batch has to be its own record.

    **The name says what it does.** The reference calls this `updateLnType`,
    files it under `tSubstation` and documents it as removing children -- so
    the name disagrees with its own doc comment, with its return type and with
    its directory. Q23 settled that the behaviour wins, and
    :func:`~py61850.scl.update_lnode_type` already exists in this package
    meaning something else entirely.

    **A type that does not resolve is left alone**, at whatever depth it stops
    -- an absent `LNodeType`, `DOType` or `DAType` ends the walk on that
    branch rather than condemning it. So is an `LNode` with no `@lnType`, and
    so is `@ix`. The module docstring argues all three, and records that a
    strict prune removes 74 of 374 specification elements from IEC's own
    `Eng POC.ssd`.

    Returns ``[]`` when every specification element resolves, which is the
    ordinary answer for a document nobody has re-typed.

    Raises :class:`~py61850.scl.EditRejected` if the document the declaration
    is read from carries no `LNodeType` ``id_``.
    """
    pool, declaration = _declaring_pool(doc, id_, source)

    edits: list = []
    for lnode in _own(doc, "LNode"):
        if lnode.get("lnType") != id_:
            continue
        for dos in _specification_roots(doc, lnode):
            name = dos.get("name")
            do_type_id = (declaration.objects.get(name)
                          if name is not None else None)
            if do_type_id is None:
                edits.append(Remove(dos))
                continue
            context = pool.do_type(do_type_id)
            if context is None:
                # The DO is declared and its DOType is not in the pool. The
                # `DOS` itself stays -- the type DOES declare the object --
                # and nothing under it can be judged.
                continue
            edits.extend(Remove(element)
                         for element in _missing_below(pool, dos, context))
    return edits
