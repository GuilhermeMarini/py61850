# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""Bringing an IED in, renaming one, removing one: the referential repair each implies.

    from py61850.scl import (Remove, SclDocument, SetAttributes, insert_ied,
                             remove_ied, update_ied)

    doc = SclDocument.parse("station.scd")
    doc.apply_edit(update_ied(doc, SetAttributes(ied, {"name": "REL_2"})))
    doc.apply_edit(remove_ied(doc, Remove(ied)))

    relay = SclDocument.parse("new_relay.icd")
    doc.apply_edit(insert_ied(doc, relay, ["REL_3"], on_conflict="rename",
                              add_communication=True))

**An IED's name is not stored once.** It is written on the `IED` element and
then repeated, in this document, everywhere anything refers to the device --
4,320 `ExtRef` elements and 79 `ConnectedAP` elements across three reference
station exports, 345 `IEDName` elements, and 758 object references
concatenated into supervision setting values. Renaming the `IED` element alone
produces a file that is structurally valid and semantically ruined: every one
of those references now names a device that is not in it.

**`update_ied` and `remove_ied` are edit checks** in the reference's sense --
the caller's own edit goes in and the corrected, expanded one comes back, input
edit first, as Q23 settled for the whole of A9 onward:

    edits = update_ied(doc, SetAttributes(ied, {"name": "REL_2"}))
    undo = doc.apply_edit(edits)      # one history entry, 4,000 primitives

**`insert_ied` is not an edit check**, because there is no caller edit to
check: nothing in the target is being changed, so the caller has nothing to
propose. It builds the whole edit itself, as
:func:`~py61850.scl.import_lnode_types` does, and for the same reason.

**Nothing here applies anything**, and a refusal raises
:class:`~py61850.scl.EditRejected` with nothing half-done.

## What an import brings, and the two halves it is made of

The reference documents `insertIed` as importing an IED *"with its
`DataTypeTemplates`"* and, optionally, *"linked `Communication` section
elements"*. Both halves are real and neither is small:

- **the types.** Corpus IEDs use **6 to 110 `LNodeType` each** -- measured per
  device across all 58, which is not the 203-to-287 figure Q31 and A13's note
  carry: that is the size of each FILE's pool, and no single IED uses one
  whole. Each drags a closure of `DOType`, `DAType` and `EnumType` behind it,
  319 templates in all for the single Siemens device Q34 worked through. That merge, with its
  conflict policy, is :func:`~py61850.scl.import_lnode_types`, and **this
  function is the caller that list was designed for.** It is also why
  `insert_ied` was not written with `update_ied` and `remove_ied`: A13 could
  not import an IED whose types nothing could merge, and writing the merge
  there would have been A15 under A13's name. Q31 §6, Q34 §9.
- **the access points.** 79 `ConnectedAP` across the three exports, and a
  device is not one of them: `mixed.scd` puts 12 of its 14 IEDs in two
  `SubNetwork`s and `sel.scd` puts `RTAC_1` in ten.
  :func:`_communication_edits` has what is copied and what is not.

**A list of names, not one**, which is where this diverges from the
reference's single-IED call. The argument is A15's, one level up: two IEDs
from one file share their types heavily -- `siemens.scd`'s `QPC2_TR1_AL11` and
`QPC2_TR1_AL12` share **all 82** of theirs, and 908 of that file's 1,111
``(IED, lnType)`` pairs are re-uses -- so two calls would each plan against a
target that does not yet hold what the other is inserting. One call over a
list has no previous call to be stale against.

**Two things are refused rather than guessed, and both keep this module
consistent with itself.** A name the target already holds is refused, because
:func:`update_ied` refuses the same collision and because a device name is the
engineer's rather than an allocator's. A conflicting data type is refused by
:func:`~py61850.scl.import_lnode_types`'s own default, which is passed
straight through rather than re-decided here.

**One thing is re-pointed and one thing is deliberately not.** `LN@lnType` and
`LN0@lnType` inside the copy are rewritten to the ids the types actually enter
the target under -- without it ``on_conflict="rename"`` re-types 114 of
`QPC2_TR1_AL11`'s 175 logical nodes silently. `ExtRef@iedName` is left exactly
as the source wrote it, because **4,291 of the corpus's 4,291 `ExtRef@iedName`
values name an IED of their own file**: a binding that dangles after an import
dangles only because the rest of the source was left behind, and the next name
in the list may be what resolves it.

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
from the **seven** functions that carry it -- `subscribe`, `unsubscribe`,
`remove_control_block`, `remove_data_set`, `remove_fcda`,
`update_report_control` and `update_sampled_value_control`, a count four
places in this package's prose used to give as six.

Those seven have something to switch off because they CREATE or RE-POINT
supervision, and instantiating an `LGOS` needs the `canInstantiate` rules, the
`Services` checks and the instance allocation that live in
:mod:`py61850.scl.supervision`. A removal needs none of it: the publisher is
gone, so every supervision of it is stale, and blanking a setting value is the
whole operation. There is nothing here to ignore, so there is no flag to
ignore it with, and a caller is not asked to promise something the function
does not do.

**This argument is older than the behaviour it describes, and it survived the
change.** Until A14b the seven REFUSED `False` outright, and the break was
between a function that refused and one that did not ask; now six of them do
the work and `update_report_control` accepts `False` as a no-op, and the break
is between a function that has a choice to offer and one that does not.
`remove_ied` was right to take no flag either way -- Q32 §8 argued it from 49
idle supervision nodes across two vendors before the wiring existed, and
`remove_supervision`'s default still produces exactly the edit step 6 below
produces, so the two paths cannot drift.

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

import copy
from xml.etree import ElementTree as ET

from .control_block import _logical_node, _placement
from .controls import CONTROL_BLOCK_TAGS
from .data_types import _root_of, import_lnode_types, lnode_type_conflicts
from .document import strip_ns
from .edit import EditRejected, Insert, Remove, SetAttributes, SetTextContent
from .extref import unsubscribe
from .ordering import reference_for
from .supervision import (
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


def _object_reference_index(doc) -> dict[str, str | None]:
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
    index: dict[str, str | None] = {}
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

def update_ied(doc, edit) -> list:
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

    edits: list = [edit]
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

def remove_ied(doc, edit) -> list:
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

    edits: list = [edit]
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


# -- inserting --------------------------------------------------------------

def _communication(doc):
    """The `Communication` section, or ``None``.

    Direct child of the root and namespace-exact, for the reason
    :func:`_ied_elements` gives: a section-named element nested in a vendor
    `Private` is not this document's `Communication`.
    """
    tag = _qualified(doc, "Communication")
    return next((child for child in doc.root if child.tag == tag), None)


def _subnetworks(doc):
    """Every `SubNetwork`, as a direct child of a direct-child `Communication`.

    Two levels of direct-child rather than one :func:`_own` scan, so a
    `SubNetwork` a vendor writes inside its own `Private` block is left where
    it is.
    """
    section = _communication(doc)
    if section is None:
        return []
    tag = _qualified(doc, "SubNetwork")
    return [child for child in section if child.tag == tag]


def _ln_type_ids(doc, ied) -> list[str]:
    """The `LNodeType` ids the `LN` and `LN0` elements inside ``ied`` name, in
    document order and without repeats.

    **`LN` and `LN0` are the whole list, and the third `lnType` carrier is not
    a mistake to have left out.** :data:`~py61850.scl.data_types._LN_TYPE_ELEMENTS`
    adds `LNode`, because a removal has to know a type is in use and an `LNode`
    puts it in use -- but `LNode` lives in `Substation`, which is outside every
    `IED`, so no IED subtree contains one.

    Nothing else inside an IED names a data type. Measured over the corpus
    IED that Q34 worked through, `QPC2_TR1_AL11`: of the attributes that look
    like references, 175 are `LN`/`LN0` `lnType`; 229 are ``Private@type``,
    which is a vendor's own vocabulary and not a template id; 7 are `datSet`
    values naming a dataset INSIDE the same IED, which the subtree copy carries
    with it; and `IED@type` and `GSEControl@type` are free-form strings the
    schema does not resolve against anything.
    """
    wanted = (_qualified(doc, "LN"), _qualified(doc, "LN0"))
    out: list[str] = []
    seen = set()
    for element in ied.iter():
        if element.tag not in wanted:
            continue
        id_ = element.get("lnType")
        if id_ and id_ not in seen:
            seen.add(id_)
            out.append(id_)
    return out


def _repoint_ln_type(doc, node, mapping) -> None:
    """Rewrite a COPIED IED's `lnType` values to the ids they carry in the
    target.

    **Without this, ``on_conflict="rename"`` silently re-types most of the
    device.** Importing `QPC2_TR1_AL11` from `siemens.scd` into `mixed.scd`
    meets 40 conflicting `LNodeType` ids out of the 82 the IED uses, and those
    40 enter the target under fresh ids -- so **114 of the IED's 175 `LN`/`LN0`
    elements would be left naming the TARGET's own, different type**. That is
    Q34 §4's defect one level up: a copied structure re-pointed at something it
    does not mean.

    Applied to the copy before it is inserted, never to the source's own
    element, exactly as :func:`~py61850.scl.data_types._repoint` is: the source
    document is read-only to this module and the caller may well go on using
    it. An id the mapping does not carry is left alone -- under ``"refuse"``
    the import never gets this far, and under the other two policies every
    type in the closure is mapped.
    """
    wanted = (_qualified(doc, "LN"), _qualified(doc, "LN0"))
    for element in node.iter():
        if element.tag not in wanted:
            continue
        id_ = element.get("lnType")
        if not id_:
            continue
        fresh = mapping.get(("LNodeType", id_))
        if fresh is not None and fresh != id_:
            element.set("lnType", fresh)


def _communication_edits(doc, source, names) -> list:
    """The `ConnectedAP` subtrees of ``names``, copied into ``doc``.

    **A device is not one access point.** `mixed.scd` puts 12 of its 14 IEDs
    in TWO `SubNetwork`s and `sel.scd` puts `RTAC_1` in ten, so what is copied
    is every `ConnectedAP` the source names the IED in, each into the
    `SubNetwork` that held it. All 79 corpus access points carry an `apName`
    that is a real `AccessPoint` of their IED, so the copy needs no repair.

    A target `SubNetwork` of the same name receives the copy. **One that does
    not exist is CREATED, carrying `name` and `type` and no children**, and
    that is the ordinary case rather than the edge: the three exports share
    **not one `SubNetwork` name between them** in any of the six ordered pairs
    -- `W01`/`Subnet1`/`Architect`/`S01`-`S10` against `PROCESS BUS 1`/`2`/
    `STATION BUS` against `Default_subnet`. Refusing instead would make
    ``add_communication=True`` fail on every pair of real files there is.

    **The source `SubNetwork`'s own children are deliberately not copied** --
    `sel.scd`'s carry a `Text` and a `BitRate`, `mixed.scd`'s four `Private`
    and `siemens.scd`'s one. Those describe the SOURCE's network, and a
    `Private` is the vendor bookkeeping `sellib` and `siemenslib` read; moving
    one vendor's private block into another vendor's station is not a copy of
    an access point.

    **A `SubNetwork` the target already has is used exactly as it stands**,
    `type` included. Rewriting `SubNetwork@type` to match the source would
    re-type every other `ConnectedAP` already in it, which is a far larger
    edit than the one asked for. No corpus pair shares a name, so this never
    fires on available material and is recorded for that reason.
    """
    wanted = set(names)
    target_root = doc.root
    ap_tag = _qualified(source, "ConnectedAP")

    edits: list = []
    section = _communication(doc)
    if section is None:
        # A target with no Communication at all is ordinary -- an SSD
        # describing a substation and no devices is the shape -- and the
        # section has to be in the tree before a SubNetwork goes in it.
        section = ET.Element(_qualified(doc, "Communication"))
        edits.append(Insert(target_root, section,
                            reference_for(target_root, section.tag)))

    have = {subnet.get("name"): subnet for subnet in _subnetworks(doc)}
    taken = {(ap.get("iedName"), ap.get("apName"))
             for subnet in _subnetworks(doc) for ap in subnet
             if ap.tag == _qualified(doc, "ConnectedAP")}
    created: dict[str | None, ET.Element] = {}

    for subnet in _subnetworks(source):
        for ap in subnet:
            if ap.tag != ap_tag or ap.get("iedName") not in wanted:
                continue
            key = (ap.get("iedName"), ap.get("apName"))
            if key in taken:
                # The target already describes this access point, which means
                # it holds a ConnectedAP naming a device it does not have --
                # the IED name itself was refused above if it did. Adding a
                # second one for the same apName would make the document say
                # two things. No corpus file contains an orphan ConnectedAP:
                # all 79 name an IED that is present.
                raise EditRejected(
                    f"the target already holds a ConnectedAP for iedName="
                    f"{key[0]!r} apName={key[1]!r}; it names a device the "
                    f"target does not have, and this import would give that "
                    f"access point a second description")
            name = subnet.get("name")
            # `is None`, never truthiness: an `Element` with no children is
            # FALSY in ElementTree, so `have.get(name) or created.get(name)`
            # throws away a target SubNetwork that exists and happens to be
            # empty and builds a second one beside it. Invisible on the
            # corpus -- all 17 of its SubNetworks already hold access points
            # -- and caught by counting the result on a built fixture.
            into = have.get(name)
            if into is None:
                into = created.get(name)
            if into is None:
                into = ET.Element(_qualified(doc, "SubNetwork"))
                if name is not None:
                    into.set("name", name)
                if subnet.get("type") is not None:
                    into.set("type", subnet.get("type"))
                created[name] = into
                edits.append(Insert(section, into,
                                    reference_for(section, into.tag)))
            node = copy.deepcopy(ap)
            edits.append(Insert(into, node, reference_for(into, node.tag)))
    return edits


def insert_ied(doc, source, names, on_conflict="refuse",
               add_communication=False) -> list:
    """The edit that brings ``names`` from ``source`` into ``doc``.

    ``doc`` is the target :class:`~py61850.scl.SclDocument` and ``source`` is
    the one to read from -- **a document, not an element**, for the reason
    :func:`~py61850.scl.import_lnode_types` records: the `DataTypeTemplates`
    closure lives in ITS section and an `ElementTree` element carries no owner
    document. `doc` first, by Q23.

    ``names`` is a **sequence** of IED names, and that is the first divergence
    from the reference, which imports one device per call. It is forced by the
    same measurement that made A15 take a list: `siemens.scd`'s
    `QPC2_TR1_AL11` and `QPC2_TR1_AL12` share **all 82** of their `LNodeType`
    ids, and 908 of that file's 1,111 ``(IED, lnType)`` pairs are re-uses
    across devices. Two IEDs as two calls would each plan against a target
    that does not yet hold what the other is inserting, and each emit an
    `Insert` for the same 82 types -- Q33 §9's hazard, met for the third time.

    ``on_conflict`` is passed through to
    :func:`~py61850.scl.import_lnode_types` unchanged: ``"refuse"`` (the
    default), ``"rename"`` or ``"overwrite"``. A caller wanting to see what
    will collide before asking has
    :func:`~py61850.scl.lnode_type_conflicts`, which is the same computation
    this builds from.

    ``add_communication`` copies each IED's `ConnectedAP` subtrees into the
    target's `Communication`, creating any `SubNetwork` the target lacks. It
    is **off by default**, matching the reference -- whose `InsertIedOptions`
    is an optional argument -- and this package's habit of asking for the
    larger action rather than arriving at it.

    What comes back, in this order:

    1. the `Communication` edits, when ``add_communication`` is set;
    2. an :class:`~py61850.scl.Insert` of a deep COPY of each IED, its
       `lnType` values re-pointed;
    3. the `DataTypeTemplates` edits for the closure the IEDs need.

    **That order is A7's, not taste.** `Communication`, `IED` and
    `DataTypeTemplates` are in that sequence in the SCL content model, and a
    target missing two of the three resolves both insertion references to
    "append" -- so building them in any other order would put `Communication`
    after the IED it describes. Where all three sections already exist every
    reference is an element that was there before the call and the order does
    not matter.

    **Every node inserted is a deep copy.** The reference is emphatic that its
    own elements are *moved*, because a browser has `importNode` and its source
    is a throwaway parse of an upload; `ElementTree` has neither, and A5's
    `Insert` says a node from another document cannot even be detected and must
    not be handed over. Q31 §6 took this decision before A15 existed and A15
    implemented it for the types.

    **Nothing this IED's `ExtRef` elements name is re-pointed, and the corpus
    is why.** An imported device brings bindings naming other devices, and
    after an import some of them name nothing in the target. Leaving them is
    not an oversight: **4,291 of the corpus's 4,291 `ExtRef@iedName` values
    name an IED of their own file**, so a reference that dangles after an
    import dangles only because the rest of the source was left behind.
    `QPC2_TR1_AL11`'s 28 bindings name seven devices and all seven are in
    `siemens.scd` -- six of them siblings a list import brings in the same
    call. Blanking them would destroy a subscription the next name in the list
    makes valid, and an engineer's binding is information, not litter. The
    reference re-points nothing either; it returns `Insert[]` only.

    Raises :class:`~py61850.scl.EditRejected` if ``source`` carries no IED of
    one of these names, if a name is given twice, if the target already holds
    an IED of that name, and for anything
    :func:`~py61850.scl.lnode_type_conflicts` refuses -- a conflicting type
    under the default policy, a source whose closure is not closed, or a
    namespace mismatch.

    **A name the target already holds is REFUSED rather than renamed**, which
    is the second divergence: the reference checks nothing.

    **Two reasons, where Q35 §4 gave three.** The middle one was "renaming
    would need an allocator, and allocation policy is A17's by name" -- and
    A17 shipped :func:`~py61850.scl.unique_element_name`, so that ground is
    spent and is deleted here rather than left pointing at a phase that has
    happened. A17b reconsidered the refusal with the allocator in hand and
    kept it, on the two that remain:

    :func:`update_ied` refuses a rename that collides, and two functions in
    one module cannot answer the same collision differently. And **an IED name
    is not a type id**: a type id is bookkeeping nothing outside the file
    knows, while a device name is on the relay, in the RDB and on the panel
    door -- `sel.scd` and `siemens.scd` share eight of them. Inventing one for
    the engineer is exactly the silent choice ``on_conflict`` exists to
    prevent, and it is the same line :func:`~py61850.scl.create_gse` sits on
    the other side of: a multicast address is station bookkeeping no engineer
    chooses, so A17b allocates it without asking. The caller's remedy is one
    call they already have -- insert, then :func:`update_ied`.
    """
    if isinstance(names, str):
        raise EditRejected(
            "names is a sequence of IED names; a bare string would be read "
            "one character at a time")
    target_root = _root_of(doc)
    _root_of(source)
    names = list(names)

    seen = set()
    for name in names:
        if name in seen:
            raise EditRejected(
                f"IED {name!r} is named twice in the same import; the second "
                f"copy would collide with the first")
        seen.add(name)

    present = {element.get("name") for element in _ied_elements(doc)}
    incoming = []
    for name in names:
        element = next((candidate for candidate in _ied_elements(source)
                        if candidate.get("name") == name), None)
        if element is None:
            raise EditRejected(
                f"the source document carries no IED {name!r}")
        if name in present:
            raise EditRejected(
                f"the target document already holds an IED named {name!r}; "
                f"insert it under a name that is free, or rename it after "
                f"inserting with update_ied")
        incoming.append(element)

    ids: list[str] = []
    known = set()
    for element in incoming:
        for id_ in _ln_type_ids(source, element):
            if id_ not in known:
                known.add(id_)
                ids.append(id_)

    # The plan and the edits are the SAME computation over the same unchanged
    # documents -- `import_lnode_types` calls `lnode_type_conflicts` itself --
    # so the mapping the copies are re-pointed with is the one the inserted
    # types actually carry. A test asserts that on the corpus rather than
    # leaving it to the reading.
    plan = lnode_type_conflicts(doc, source, ids, on_conflict)
    type_edits = import_lnode_types(doc, source, ids, on_conflict)

    edits: list = []
    if add_communication:
        edits.extend(_communication_edits(doc, source, names))

    reference = reference_for(target_root, _qualified(doc, "IED"))
    for element in incoming:
        node = copy.deepcopy(element)
        _repoint_ln_type(source, node, plan.ids)
        edits.append(Insert(target_root, node, reference))

    edits.extend(type_edits)
    return edits
