# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""Renaming an IED, and removing one: the referential repair either implies.

    from py61850.scl import Remove, SclDocument, SetAttributes, remove_ied, update_ied

    doc = SclDocument.parse("station.scd")
    doc.apply_edit(update_ied(doc, SetAttributes(ied, {"name": "REL_2"})))
    doc.apply_edit(remove_ied(doc, Remove(ied)))

**An IED's name is not stored once.** It is written on the `IED` element and
then repeated, in this document, everywhere anything refers to the device --
4,320 `ExtRef` elements and 79 `ConnectedAP` elements across three reference
station exports, 345 `IEDName` elements, and 758 object references
concatenated into supervision setting values. Renaming the `IED` element alone
produces a file that is structurally valid and semantically ruined: every one
of those references now names a device that is not in it.

**So both functions here are edit checks** in the reference's sense -- the
caller's own edit goes in and the corrected, expanded one comes back, input
edit first, as Q23 settled for the whole of A9 onward:

    edits = update_ied(doc, SetAttributes(ied, {"name": "REL_2"}))
    undo = doc.apply_edit(edits)      # one history entry, 4,000 primitives

**Nothing here applies anything**, and a refusal raises
:class:`~py61850.scl.EditRejected` with nothing half-done.

**`insert_ied` is deliberately not here.** The reference's `insertIed` is
documented as importing an IED *"with its `DataTypeTemplates`"*, and merging
`LNodeType`, `DOType`, `DAType` and `EnumType` into a target document with
conflict resolution is `importLNodeType` -- a different phase's capability,
which does not exist yet. Corpus IEDs carry 203 to 287 `LNodeType` between
them. Writing the import without the merge would produce an IED whose types
are not in the file; writing the merge here would be that phase under this
one's name. See Q31.

## What carries an IED's name, and how this module finds each one

Three mechanisms, and they are found three different ways because they ARE
three different things.

**1. An `iedName` ATTRIBUTE, on six elements.** The reference names six --
`LNode`, `ClientLN`, `ExtRef`, `KDC`, `Association`, `ConnectedAP` -- and that
list is not a convenience. It is exhaustive against the schema: exactly six
complex types in 61850-6 declare an `iedName` attribute, directly or through
an attribute group or a base extension, and the six elements typed by them are
these. Checked against `2007B4` and `2007C5` alike; Edition 2.1 adds none.
:data:`IED_NAME_ELEMENTS` is that list, public so a vendor library can find
the same seam without re-deriving it.

**Sweeping every element that happens to carry an `iedName` was considered and
is worse**, which is worth recording because it looks like the tidier rule.
Over the standard namespace the two sweeps are identical -- that is what the
schema fact above means. They differ only on vendor elements, and there the
attribute sweep does damage:

=========================================================  =====  ===============
`{http://www.selinc.com/2006/61850}GooseSubscription`      count  followed by it?
=========================================================  =====  ===============
``iedName``                                                  220  **yes**
``goCbRef`` -- ``QPC1_TFE_UPC1CFG/LLN0$GO$GoSB00``           220  no
``datSetRef`` -- ``QPC1_TFE_UPC1CFG/LLN0$GOPB_138``          220  no
``appId`` -- ``QPC1_TFE_UPC1_C0_61``                         203  no, and must not
=========================================================  =====  ===============

An element left carrying the new name in one attribute and the old name in two
others is inconsistent in a way the untouched element is not. The same sweep
never sees `{...Siedig}IED@name` (14 in one export -- a vendor's own
bookkeeping copy of the name) or `{...selinc}IcdFilePath` text (22), because
those are not spelled `iedName`. **The vendor half of a vendor file belongs to
the vendor library**, which is the standing rule, and half-rewriting it here
would make that harder rather than easier.

One measurement belongs with this, because it is a fresh instance of a hazard
this package has now met three times. `sel.scd` holds **969 elements whose
local name is `ExtRef`**, and 29 of them are
`{http://www.selinc.com/2006/61850}ExtRef` -- a SEL private element of the
same local name, sitting inside `Inputs` beside the real ones. Q27 found 127
elements called `DataSet` that are not datasets and Q29 found 14 called `IED`
that are not IEDs; this is the same shadowing at the element A8's whole phase
is about. **Everything in this module is namespace-exact** as a result, and
`doc.root.iter()` is given a fully qualified tag rather than a local name.

**2. An `IEDName` ELEMENT, whose TEXT is the name.** 345 in the corpus, all
resolving, every one a child of a control block -- the Edition 1 way of
recording a subscriber. `sel.scd` has none at all, which is why A9's
`find_control_block_subscription` could return `ExtRef` elements only and be
complete. Here they matter: a removed IED's `IEDName` entries are the record
that has to go, and a renamed one's have to follow.

**3. An object reference with the name CONCATENATED into it**, in a
supervision setting value. 61850-7-2 builds an LDName as the IED name
immediately followed by the `LDevice@inst`, with no separator --
``QPC1_LT1_UPC2CFG/LLN0.GoSB00`` is iedName ``QPC1_LT1_UPC2`` then ldInst
``CFG`` -- so there is no delimiter to split on.

## The concatenated references are three data objects, not one, and the way in
## is to resolve them rather than to decompose them

Every `Val` in all three corpus files whose text begins with a real IED name
is inside an `LGOS` or an `LSVS`, and they are not all the same kind of thing:

=========================  ====  =====  =======  =====  ==============================
`DOI@name` / `DAI@name`     sel  mixed  siemens  total  what it is
=========================  ====  =====  =======  =====  ==============================
`GoCBRef`/`SvCBRef` ·
``setSrcRef``               207    268       74  **549**  object reference of the
                                                        supervised control block
`DatSet` · ``setSrcRef``    207      2        0  **209**  object reference of the
                                                        supervised dataset
`GoID` · ``setVal``         190      1        0  **191**  the GOOSE application id
=========================  ====  =====  =======  =====  ==============================

**`GoID` is not a reference and this module never touches it.** All 191 values
are exactly equal to a real `GSEControl@appID`. SEL's tool happens to build
its application ids out of the IED name, so a prefix sweep would rewrite 191
values that are correct and stay correct -- an `appID` is a free-form
identifier that renaming a device does not change. The same argument covers
`Addr`, `VlanID`, `VlanPri` and `AppID` beside it: they are copies of the
publisher's link-layer address, not references, and a removal that guessed at
them would be inventing rather than repairing.

**The other two are resolved, not decomposed.** Rather than matching the old
name as a prefix of an LDName -- which is ambiguous in principle, since
nothing says an IED name cannot be a prefix of another -- this module builds
an index of every object reference the document actually contains, through the
same construction A9's :func:`~py61850.scl.control_block_obj_ref` uses, and
looks each setting value up in it. A value that resolves names a real control
block or a real dataset, and the IED it belongs to is then a fact rather than
a guess; a value that resolves to nothing is **left alone**, because a
reference this document cannot account for is not this function's to rewrite.

Measured: **758 of 758 resolve, and 0 dangle**, across the three exports --
549 control-block references and 209 dataset references.

So *"what happens in a file where one decomposes two ways"* has an answer that
does not depend on the decomposition being unique. A value is rewritten when
the document contains exactly one element with that reference and that element
is inside the IED being renamed. Where two **different** IEDs build the same
reference -- ``R1`` with an `LDevice` called ``XLD``, and ``R1X`` with one
called ``LD`` -- the index records the collision and the value is **left
alone**, because it genuinely does not say which device it means. Such a
document is already invalid, since 61850-7-2 object references are unique, and
declining to guess is the only answer that cannot make it worse.

This is an addition the reference's documentation does not describe in this
detail; `updateIED` promises *"all control block object references pointing to
the IED"* and says nothing about how they are found, and its one option --
`checkPermission` -- exists to gate exactly this rewrite. See Q31.

## What a removal repairs, and the half of supervision it does

`Remove(ied)` takes the whole IED subtree, so everything INSIDE it -- its
`ExtRef` elements, its datasets, its control blocks, its own supervision
logical nodes -- goes with it and needs no edit of its own. What is left is
everything OUTSIDE the IED that names it:

1. **`ExtRef` subscribers**, unsubscribed through A8's
   :func:`~py61850.scl.unsubscribe`, so one carrying `intAddr` is blanked and
   kept and one without is removed, with an emptied `Inputs` going too. 4,320
   in the corpus, the worst single IED accounting for 408 of them;
2. **`ConnectedAP`, `ClientLN`, `KDC` and `Association`** elements naming it,
   removed -- the reference's own list;
3. **`IEDName`** elements naming it, removed;
4. **`LNode`** elements naming it, set to :data:`ORPHAN_IED_NAME` rather than
   removed -- a bay's logical node outlives the device allocated to it;
5. **supervision object references** naming one of its control blocks or
   datasets, blanked.

**An empty `SubNetwork` is left standing.** Removing the last `ConnectedAP`
from one is valid SCL, and a subnetwork is a description of the network rather
than of the devices on it. **`DataTypeTemplates` are left alone too**, as the
reference leaves them: an `LNodeType` no instance uses is ordinary, and 114
`DataSet` elements in one corpus file are already referenced by nothing.

**`ignore_supervision` is not a parameter here**, which is a deliberate break
from the **seven** functions that carry it and refuse `False` --
`subscribe`, `unsubscribe`, `remove_control_block`, `remove_data_set`,
`remove_fcda`, `update_report_control` and `update_sampled_value_control`, a
count four places in this package's prose used to give as six. Those refuse
because they would have to CREATE or RE-POINT supervision, and instantiating
an `LGOS` needs the `canInstantiate` rules, the `Services` checks and the
instance allocation that live in :mod:`py61850.scl.supervision`. A removal
needs none of it: the
publisher is gone, so every supervision of it is stale, and blanking a setting
value is the whole operation. There is nothing here to ignore, so there is no
flag to ignore it with, and a caller is not asked to promise something the
function does not do.

**Blanked, not deleted, and two vendors' files say so.** `sel.scd` carries
**24 `LGOS` whose `GoCBRef` is already empty** -- the shape its tool writes
for a supervision logical node that is allocated and not yet pointed at
anything, with `setSrcRef` present and its `Val` carrying no text -- and
`siemens.scd` carries **25 more** written a different way, with the `DOI`
present and no `DAI` under it at all. Blanking produces the first shape
exactly. Removing the `LGOS` instead would renumber the `inst` attributes of
the ones after it, which other things in the file may name, to gain nothing
two unrelated vendors do not already demonstrate is normal 49 times between
them.

:func:`~py61850.scl.remove_supervision` produces the SAME edit -- the same
primitive on the same element -- for a supervision node being cleared on its
own, so the two paths cannot drift into leaving a stale supervision in two
different shapes. See Q32.

## The stale-model hazard, which this module closes

A5 recorded the hazard and A9, A11 and A12 each recorded meeting it again:
`SclDocument` caches `.templates`, `.communication`, `.ied_headers` and each
`ied(name)`, and an edit changed the tree underneath them. **A13 is where it
stops being tolerable** -- :func:`remove_ied` removes the very element
`doc.ied(name)` wraps -- so :meth:`~py61850.scl.SclDocument.apply_edit` now
invalidates by ancestry, and the reasoning is in Q14 and in `edit.py`.
"""

from __future__ import annotations

from typing import Dict, List, Optional
from xml.etree import ElementTree as ET

from .control_block import _logical_node, _placement
from .controls import CONTROL_BLOCK_TAGS
from .document import strip_ns
from .edit import EditRejected, Remove, SetAttributes, SetTextContent
from .extref import unsubscribe
from .supervision import (
    SUPERVISION_LN_CLASSES,
    SUPERVISION_REFERENCE_DOS,
    _retext,
    _supervision_values,
)

#: The SCL elements that carry an `iedName` attribute, and therefore everything
#: a rename has to follow and a removal has to account for. **Exhaustive
#: against the schema**: these six are exactly the elements whose complex types
#: -- `tLNode`, `tClientLN`, `tExtRef`, `tKDC`, `tAssociation`, `tConnectedAP`
#: -- declare the attribute in 61850-6 `2007B4`, and `2007C5` adds none. The
#: module docstring carries the derivation and why an unrestricted attribute
#: sweep is not used instead.
IED_NAME_ELEMENTS = ("LNode", "ClientLN", "ExtRef", "KDC", "Association",
                     "ConnectedAP")

#: What an `LNode` left without a device is set to. **The four-character
#: string, and the schema is unambiguous about it.** `tLNode@iedName` is
#: ``type="tIEDNameOrNone" default="None"``; `tIEDNameOrNone` is a union of
#: `tIEDName` with `tIEDNameIsNone`, whose whole content is
#: ``<xs:pattern value="None"/>``; and `tIEDName` carries six patterns --
#: ``[A-MO-Za-z][0-9A-Za-z_]{3}``, ``N[0-9A-Za-np-z_][0-9A-Za-z_]{2}``,
#: ``No[0-9A-Za-mo-z_][0-9A-Za-z_]``, ``Non[0-9A-Za-df-z_]`` and two for the
#: other lengths -- written for no purpose but to exclude it. So no real IED
#: can be called `None`, this value is a reserved sentinel, and it is neither
#: Python's ``None`` nor the absence of the attribute.
ORPHAN_IED_NAME = "None"

# -- namespace-exact access -------------------------------------------------

def _namespace(doc) -> str:
    """``"{uri}"`` for this document, or ``""`` when it declares none.

    A hand-written SCD that declares no namespace is real and this library
    reads them, so the empty answer is an ordinary one rather than an error.
    """
    tag = doc.root.tag
    return tag[:tag.index("}") + 1] if tag.startswith("{") else ""


def _qualified(doc, local_name: str) -> str:
    return _namespace(doc) + local_name


def _own(doc, local_name: str):
    """Every descendant of the root with this name **in the document's own
    namespace**, which is not the same question as
    :func:`~py61850.scl.iter_local` asks.

    The module docstring has the measurement that makes the difference
    load-bearing: 29 of `sel.scd`'s 969 elements called `ExtRef` are a SEL
    private element of the same local name.
    """
    return doc.root.iter(_qualified(doc, local_name))


def _ied_elements(doc):
    """Every real `IED`: a DIRECT CHILD of the root, in the document's own
    namespace.

    Both halves are needed and neither is redundant. Direct-child is
    :meth:`~py61850.scl.SclDocument._ied_elements`'s own rule, and the reason
    is recorded there -- DIGSI nests a bare ``<IED uuidRef= name=>``
    cross-reference inside a `Private` for project bookkeeping. Namespace-exact
    is this module's addition, and it costs nothing: `siemens.scd` holds 14
    elements called `IED` in `http://www.siemens.com/energy/2011/11/Siedig`,
    one per real device, and while none of them is a child of the root today,
    nothing in the standard promises that of the next vendor.
    """
    tag = _qualified(doc, "IED")
    return [child for child in doc.root if child.tag == tag]


def _is_ied(doc, element) -> bool:
    return (isinstance(element, ET.Element)
            and element.tag == _qualified(doc, "IED")
            and doc.parent_of(element) is doc.root)


def _describe(element) -> str:
    if not isinstance(element, ET.Element):
        return type(element).__name__
    tag = element.tag
    return strip_ns(tag) if isinstance(tag, str) else "a comment"


# -- object references ------------------------------------------------------

def _object_reference(doc, element):
    """``(reference, owning IED name)`` for a named element under an `LN`.

    The construction is A9's, and :func:`~py61850.scl.control_block_obj_ref`
    is the same string for a control block -- ``<IED name><LDevice inst>/``
    ``<prefix><lnClass><inst>.<name>``. It is spelled once more here rather
    than called, because a `DataSet` is not a control block and A9's function
    refuses one, and the 209 dataset references in the corpus are built the
    same way. ``tests/unit/test_scl_ied.py`` pins that the two agree on every
    control block in all three exports.
    """
    name = element.get("name")
    if not name:
        return None
    node = _logical_node(doc, element)
    if node is None:
        return None
    placement = _placement(doc, node)
    if placement is None:
        return None
    ied_name, ld_inst, prefix, ln_class, ln_inst = placement
    return (f"{ied_name}{ld_inst}/{prefix}{ln_class}{ln_inst}.{name}", ied_name)


def _object_reference_index(doc) -> Dict[str, Optional[str]]:
    """``{object reference: owning IED name}`` for every control block and
    `DataSet` in the document.

    This is what turns a supervision setting value from a string that has to
    be decomposed into an element that is known to exist. Built per call
    rather than cached: these functions build an edit and hand it back, and a
    cache the caller cannot invalidate is the hazard Q14 is about.

    **A reference two DIFFERENT IEDs both produce maps to ``None``**, which
    every caller here reads as "leave it alone". That is the case the
    concatenation makes possible: an IED named ``R1`` with an `LDevice` called
    ``XLD`` and an IED named ``R1X`` with one called ``LD`` build the same
    LDName, so a setting value carrying it genuinely does not say which device
    it means. Such a document is already invalid -- 61850-7-2 object
    references are unique -- and the answer to an ambiguous reference is to
    decline to guess, not to pick the one that happens to come first. Two
    blocks in the SAME IED colliding still resolve, because the owner is not
    in doubt and the owner is all this module reads.

    No corpus file contains either case: 758 of 758 supervision values resolve
    to exactly one owner.
    """
    index: Dict[str, Optional[str]] = {}
    names = CONTROL_BLOCK_TAGS + ("DataSet",)
    for local_name in names:
        for element in _own(doc, local_name):
            resolved = _object_reference(doc, element)
            if resolved is None:
                continue
            reference, owner = resolved
            if reference in index and index[reference] != owner:
                index[reference] = None
            else:
                index.setdefault(reference, owner)
    return index


# -- renaming ---------------------------------------------------------------

def update_ied(doc, edit) -> List:
    """``edit`` expanded: the rename, and every reference that has to follow.

    ``edit`` is a :class:`~py61850.scl.SetAttributes` on an `IED` element. If
    its mapping does not touch `name`, or sets it to what it already is, the
    edit is returned alone -- `desc`, `type`, `manufacturer` and
    `configVersion` are the IED's own and nothing refers to them.

    A rename returns, after the caller's own edit:

    1. every `LNode`, `ClientLN`, `ExtRef`, `KDC`, `Association` and
       `ConnectedAP` whose `iedName` is the old name, re-pointed --
       :data:`IED_NAME_ELEMENTS`, which the module docstring shows is
       exhaustive against the schema rather than a list someone assembled;
    2. every `IEDName` element whose text is the old name, re-texted;
    3. every supervision `setSrcRef` naming one of this IED's control blocks
       or datasets, rebuilt -- **resolved through the document's own object
       references, never by matching the old name as a prefix.** A value that
       resolves to nothing is left alone.

    An `ExtRef` carrying ``iedName="@"`` is untouched, and correctly so:
    `tIEDNameOrRelative` gives that spelling the meaning *"the IED this
    element is in"*, which no rename changes. No corpus file uses it.

    Raises :class:`~py61850.scl.EditRejected` if ``edit`` is not a
    `SetAttributes` on an `IED`, if the new name is empty, if it is
    :data:`ORPHAN_IED_NAME`, or if another IED already carries it.
    """
    if not isinstance(edit, SetAttributes):
        raise EditRejected(
            f"update_ied takes a SetAttributes, not {type(edit).__name__}")
    ied = edit.element
    if not _is_ied(doc, ied):
        raise EditRejected(
            f"{_describe(ied)} is not an IED of this document; an IED is a "
            f"direct child of the SCL root in the document's own namespace")
    if "name" not in edit.attributes:
        return [edit]

    new_name = edit.attributes["name"]
    old_name = ied.get("name")
    if new_name == old_name:
        return [edit]
    if not new_name:
        raise EditRejected(
            "an IED name is required; tIED@name is use=\"required\" and "
            "every reference to the device is written by name")
    if new_name == ORPHAN_IED_NAME:
        raise EditRejected(
            f"{ORPHAN_IED_NAME!r} is the reserved value an LNode carries when "
            f"it has no device; tIEDName excludes it by pattern, so no IED "
            f"may be named it")
    clash = next((other for other in _ied_elements(doc)
                  if other is not ied and other.get("name") == new_name), None)
    if clash is not None:
        raise EditRejected(
            f"renaming IED {old_name!r} to {new_name!r} would collide with "
            f"the IED already named {new_name!r}")

    edits: List = [edit]
    if not old_name:
        # Nothing can refer to an unnamed IED: `iedName` is `use="required"`
        # wherever it is not defaulted, and the one place it is defaulted
        # defaults to `None`. The rename is the whole edit.
        return edits

    for local_name in IED_NAME_ELEMENTS:
        for element in _own(doc, local_name):
            if element.get("iedName") == old_name:
                edits.append(SetAttributes(element, {"iedName": new_name}))

    for element in _own(doc, "IEDName"):
        if (element.text or "").strip() == old_name:
            edits.append(_retext(element, new_name))

    index = _object_reference_index(doc)
    for val, text in _supervision_values(doc):
        if not text or index.get(text) != old_name:
            continue
        edits.append(_retext(val, new_name + text[len(old_name):]))
    return edits


# -- removing ---------------------------------------------------------------

def remove_ied(doc, edit) -> List:
    """``edit`` expanded: the IED, and everything outside it that named it.

    ``edit`` is a :class:`~py61850.scl.Remove` whose node is an `IED`. What
    comes back is everything that removal implies, as one compound edit:

    1. the removal itself, as given -- which takes the whole subtree, so the
       IED's own `ExtRef` elements, datasets, control blocks and supervision
       logical nodes need no edit of their own;
    2. the `ExtRef` elements subscribed to it, unsubscribed through
       :func:`~py61850.scl.unsubscribe`;
    3. the `ConnectedAP`, `ClientLN`, `KDC` and `Association` elements naming
       it, removed;
    4. the `IEDName` elements naming it, removed;
    5. the `LNode` elements naming it, set to :data:`ORPHAN_IED_NAME`;
    6. the supervision `setSrcRef` values naming one of its control blocks or
       datasets, blanked.

    Steps 3 to 5 are the reference's own list. Step 6 is the half of
    supervision a removal can do honestly, and the module docstring says why
    there is no `ignore_supervision` flag to turn it off and why the value is
    blanked rather than the `LGOS` deleted.

    An empty `SubNetwork` and an unused `LNodeType` are both left standing;
    neither is a dangling reference, and the module docstring has the reason.

    Raises :class:`~py61850.scl.EditRejected` if ``edit`` is not a `Remove` of
    an `IED` of this document.
    """
    if not isinstance(edit, Remove):
        raise EditRejected(
            f"remove_ied takes a Remove, not {type(edit).__name__}")
    ied = edit.node
    if not _is_ied(doc, ied):
        raise EditRejected(
            f"{_describe(ied)} is not an IED of this document; an IED is a "
            f"direct child of the SCL root in the document's own namespace")

    edits: List = [edit]
    name = ied.get("name")
    if not name:
        # As in `update_ied`: nothing can name it, so the removal is complete
        # on its own. Such an IED is also invisible to `doc.ied_names`.
        return edits

    # Everything the removal already takes. Membership by identity, which is
    # what `Element` hashing is, and the whole subtree is alive for the length
    # of this call because the document still holds it.
    inside = set(ied.iter())

    subscribers = [element for element in _own(doc, "ExtRef")
                   if element.get("iedName") == name and element not in inside]
    if subscribers:
        edits.extend(unsubscribe(doc, subscribers))

    for local_name in ("ConnectedAP", "ClientLN", "KDC", "Association"):
        for element in _own(doc, local_name):
            if element.get("iedName") == name and element not in inside:
                edits.append(Remove(element))

    for element in _own(doc, "IEDName"):
        if (element.text or "").strip() == name and element not in inside:
            edits.append(Remove(element))

    for element in _own(doc, "LNode"):
        if element.get("iedName") == name and element not in inside:
            edits.append(SetAttributes(element,
                                       {"iedName": ORPHAN_IED_NAME}))

    index = _object_reference_index(doc)
    for val, text in _supervision_values(doc):
        if not text or index.get(text) != name or val in inside:
            continue
        edits.append(SetTextContent(val, None))
    return edits
