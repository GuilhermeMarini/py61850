# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""Writing `DataTypeTemplates`: importing a type from another document, and removing one.

    from py61850.scl import SclDocument, import_lnode_types, lnode_type_conflicts

    target = SclDocument.parse("station.scd")
    source = SclDocument.parse("new_relay.icd")

    plan = lnode_type_conflicts(target, source, ["SEL_LLN0_V01"])
    if plan.conflicts:
        ...                                     # ask, then choose a policy
    target.apply_edit(import_lnode_types(target, source, ["SEL_LLN0_V01"]))

:mod:`py61850.scl.templates` READS this section -- :class:`TemplatePool` and
the three spec classes are what an instance resolves through. This module
CHANGES it, and it is the first in the package to read from a **second
document**. The split follows `communication.py` and `address.py`: the read
model of an SCL section and the edits over it are different enough to live
apart.

## What a type drags with it

An `LNodeType` is never alone. It names `DOType` through its `DO` children,
those name more `DOType` through `SDO` and both `DAType` and `EnumType`
through `DA`, and a `DAType` names further `DAType` and `EnumType` through
`BDA`. **That transitive closure is the unit of import**, not the `LNodeType`
element, and it is not small:

=========  =======================  ======  ========  ======
file       closure per `LNodeType`     min    median     max
=========  =======================  ======  ========  ======
`sel.scd`                                4        10      23
`mixed.scd`                              5        12      43
`siemens.scd`                            5        14      43
=========  =======================  ======  ========  ======

**All three corpus pools are exactly closed**: 767 `LNodeType`, 794 `DOType`,
86 `DAType` and 304 `EnumType`, with **0 orphans and 0 dangling references**
in any of them. Every `LNodeType` is referenced by an `LN`, `LN0` or `LNode`,
and every subordinate type is referenced from another type. That is the
invariant both capabilities here are built to preserve: an import adds a
closed closure, and a removal takes one away without stranding the rest.

## The conflict, which is the whole of the difficulty

Two documents built by two tools will use the same `id` for different types,
and they do it constantly. Across the three reference exports, taken
pairwise:

============  ===========  ===========  =============
kind          shared `id`   identical    **different**
============  ===========  ===========  =============
`LNodeType`           106            5          **101**
`DOType`               47           10           **37**
`DAType`                8            5              3
`EnumType`              4            4              0
============  ===========  ===========  =============

The 101 are not cosmetic. Re-measured with `desc` ignored, and again with
`Private` ignored, **not one pair changes its answer**: they differ in the set
of `DO` children (42), in the `DOType` a `DO` points at (47), or in `lnClass`
(20). `mixed.scd` and `siemens.scd` alone share 94 `LNodeType` ids of which 89
disagree.

**And it is the ordinary case, not an edge.** Importing one Siemens IED into
`mixed.scd` -- `QPC2_TR1_AL11`, which uses 82 `LNodeType` and pulls 163
`DOType`, 20 `DAType` and 94 `EnumType` behind them -- meets **40 `LNodeType`
ids that already exist in the target**.

So a merge has three possible answers and only one of them may be silent:

===============  ==============================================================
`on_conflict`    what happens to an `id` that exists with different content
===============  ==============================================================
``"refuse"``     :class:`~py61850.scl.EditRejected`, naming the ids. **The
                 default.** Nothing is written and nothing is decided for the
                 caller
``"rename"``     the incoming type enters under a fresh `id` and the copied
                 structures are re-pointed at it; the target's own type is
                 left exactly as it was
``"overwrite"``  the target's type is replaced by the incoming one -- which
                 re-types every existing instance that used that `id`
===============  ==============================================================

**Refusing is the default because of this project's own recorded habit**, not
because it is the most convenient. Q27 settled it once and Q33 §3 again:
"fill a free slot" and "add a logical node" are different sizes of action, and
the larger is asked for rather than arrived at. Creating 40 new type ids in a
station pool, or replacing 40 that instances are using, are both plainly the
larger action. A type whose content is **identical** is reused with no edit at
all under every policy, and that much is the reference's *"check for duplicate
data"* in its plain meaning.

**This is a divergence from the reference and Q34 records it.**
`importLNodeType` carries one boolean, `overwrite?`, and returns `EditV2[]`;
what it does on a genuine conflict is in its function body, which this project
does not read. Three named outcomes replace the boolean because the situation
genuinely has three, and a boolean would have to make one of them silent.

## Sameness is strict, and it is free to be

:func:`same_data_type` compares tag, attributes, text and children
recursively, in order -- the reference's `isEqualNode` semantics. It is
public, where the reference keeps its own equality internal, for the reason
Q32 §0 gave for `max_supervision`: every guard in this package exposes what it
read, so a refusal can be anticipated rather than caught. A caller about to
import wants to know what will collide *before* it asks.

**Ignoring `desc` or `Private` was measured and neither moves a single pair**
of the 106, so the strict reading costs nothing on any file we have and fails
in the harmless direction. A type that IS the same but is spelled differently
gets duplicated under a fresh id, which wastes a pool entry; a type that
DIFFERS only in a vendor `Private` block would, under a permissive reading, be
silently reused -- and `sellib` and `siemenslib` read exactly those blocks.
Strict fails safe.

The reference also ships `hashElement`, and this module has no equivalent. A
hash is an optimisation for comparing every type against every type; ours only
ever compares the pairs that share an `id`, which is at most 40 in the worst
case the corpus offers.

## What is refused rather than guessed

- **A source that references a type it does not define.** `templates.py`
  tolerates this on READ, deliberately, because a trimmed ICD is a real file
  and reporting is the caller's business. Importing one is different: the
  result would be a target that cannot resolve what was just written into it.
  0 of the corpus's 1,951 type references dangle, so this never fires on real
  material.
- **A source in a different XML namespace from the target.** Merging a type
  across SCL editions is an edition question this module does not own, and a
  copy that kept its own namespace would land in `DataTypeTemplates`'s
  ``xs:any`` slot rather than among its own kind. All four fixture documents
  are `http://www.iec.ch/61850/2003/SCL`.

## Removal prunes what it strands, and nothing else

:func:`remove_data_type` takes the caller's `Remove` and adds the types that
removal leaves unreferenced -- *"not to leave NEW unlinked data types"*, in
the reference's words, where "new" is doing real work: a type that was already
unreferenced before the call stays. The corpus cannot tell the two readings
apart, having no unreferenced type at all, so the reference's wording is
followed exactly rather than simplified.

``force`` skips the guard that refuses to remove a type something still
points at, and "points at" covers both directions: an instance
(`LN`/`LN0`/`LNode@lnType`) and another template (`DO@type`, `SDO@type`,
`DA@type`, `BDA@type`). Both leave a dangling reference and the document does
not grade them. **Every one of the corpus's 767 `LNodeType` is in use, so
without ``force`` this refuses on all 767** -- that is the guard working, and
it means the honest use of this function is after the instances are gone.

**`remove_ied` still does not cascade into `DataTypeTemplates`**, and this
module does not change that. A13 decided it and the trigger is what
distinguishes them: there the caller removed a device and an unused
`LNodeType` is ordinary, here the caller named a type.
"""

import copy
from typing import NamedTuple
from xml.etree import ElementTree as ET

from .document import children_local, strip_ns
from .edit import EditRejected, Insert, Remove
from .generator import ALLOCATED_NAME_SUFFIX_START as SUFFIX_START
from .ordering import reference_for

#: The four `DataTypeTemplates` children, in the order the content model puts
#: them. A type's `id` is unique only within its own kind, so everything here
#: is keyed by ``(kind, id)`` rather than by `id` alone.
DATA_TYPE_TAGS = ("LNodeType", "DOType", "DAType", "EnumType")

#: What :func:`import_lnode_types` may do with an `id` that already exists in
#: the target carrying different content. The module docstring argues the
#: default.
ON_CONFLICT = ("refuse", "rename", "overwrite")

# What each kind of template element references, as
# {kind: ((child local name, attribute, kind referenced, bType or None), ...)}.
# `DA` and `BDA` carry a `type` that means a DAType or an EnumType depending
# on `bType`, which is why the fourth field exists.
_REFERENCES = {
    "LNodeType": (("DO", "type", "DOType", None),),
    "DOType": (("SDO", "type", "DOType", None),
               ("DA", "type", "DAType", "Struct"),
               ("DA", "type", "EnumType", "Enum")),
    "DAType": (("BDA", "type", "DAType", "Struct"),
               ("BDA", "type", "EnumType", "Enum")),
    "EnumType": (),
}

# The elements whose `lnType` names an LNodeType. `LNode` is in the Substation
# section and the other two are in an IED; all three make a type "in use".
_LN_TYPE_ELEMENTS = ("LN", "LN0", "LNode")


class TypeImport(NamedTuple):
    """What importing a set of `LNodeType` would do to a target's pool.

    ``ids`` maps ``(kind, id in the source)`` to the `id` that type will carry
    in the target, for everything the chosen policy can place. A type that is
    reused maps to its own `id`; one renamed maps to the fresh one. A conflict
    the policy refuses is **not** in ``ids``, because there is no answer.

    ``added``, ``reused`` and ``conflicts`` partition the closure into the
    types the target does not have, the ones it already has identically, and
    the ones it has under the same `id` with different content. All three are
    tuples of ``(kind, id in the source)``, sorted, so a refusal message and a
    caller's preview list them in the same order every time.
    """

    ids: dict[tuple[str, str], str]
    added: tuple[tuple[str, str], ...]
    reused: tuple[tuple[str, str], ...]
    conflicts: tuple[tuple[str, str], ...]


# -- sameness ---------------------------------------------------------------

def same_data_type(ours, theirs) -> bool:
    """Whether two type declarations are the same element, strictly.

    Tag, attributes, text and children, recursively and in order -- the
    reference's `isEqualNode` semantics, and the predicate
    :func:`import_lnode_types` uses to decide that an `id` already in the
    target is a duplicate rather than a conflict.

    **Strict includes `desc` and `Private`**, and the module docstring has the
    measurement that makes that free: of the 106 `id`s shared between corpus
    exports, not one changes its answer when either is ignored. It also
    includes the ORDER of children, so two `DOType` declaring the same `DA`
    elements in a different sequence are not the same type here. Under
    ``on_conflict="rename"`` that costs a duplicated pool entry and nothing
    else; the permissive reading would instead reuse a type that differs, and
    a vendor library reading the `Private` it dropped would be wrong.

    Text is compared stripped, because these are ``xs:normalizedString`` and
    the whitespace around an `EnumVal`'s label is the file's indentation
    rather than the label.
    """
    if not isinstance(ours, ET.Element) or not isinstance(theirs, ET.Element):
        return False
    if ours.tag != theirs.tag or ours.attrib != theirs.attrib:
        return False
    if (ours.text or "").strip() != (theirs.text or "").strip():
        return False
    if len(ours) != len(theirs):
        return False
    return all(same_data_type(a, b) for a, b in zip(ours, theirs, strict=True))


# -- the pools --------------------------------------------------------------

def _namespace(tag) -> str:
    if not isinstance(tag, str) or not tag.startswith("{"):
        return ""
    return tag[:tag.index("}") + 1]


def _root_of(document):
    """The root element of an :class:`~py61850.scl.SclDocument`.

    Spelled out rather than duck-typed so the failure of passing an `Element`
    where a document belongs says so, instead of surfacing later as an empty
    pool.
    """
    root = getattr(document, "root", None)
    if not isinstance(root, ET.Element):
        raise EditRejected(
            f"expected an SclDocument, not {type(document).__name__}; the "
            f"source of an import has to be a document because an "
            f"LNodeType's DOType, DAType and EnumType live in ITS "
            f"DataTypeTemplates and ElementTree elements carry no owner")
    return root


def _section(root) -> ET.Element | None:
    """The `DataTypeTemplates` element, or ``None``.

    Direct child of the root only, which is
    :meth:`~py61850.scl.TemplatePool._build`'s own rule and for its reason: a
    descendant scan would also find a same-named element nested in a vendor
    `Private`.
    """
    return next(iter(children_local(root, "DataTypeTemplates")), None)


def _pool(root) -> dict[tuple[str, str], ET.Element]:
    """``{(kind, id): element}`` for one document's `DataTypeTemplates`.

    Built per call. These functions hand an edit back and never apply it, so a
    cache the caller cannot invalidate is the hazard Q14 is about --
    `ied.py`'s object-reference index is built per call for the same reason.
    """
    section = _section(root)
    pool: dict[tuple[str, str], ET.Element] = {}
    if section is None:
        return pool
    for kind in DATA_TYPE_TAGS:
        for element in children_local(section, kind):
            id_ = element.get("id")
            if id_:
                pool.setdefault((kind, id_), element)
    return pool


def _referenced(element, kind) -> list[tuple[str, str]]:
    """The ``(kind, id)`` every child of ``element`` points at.

    `DA` and `BDA` carry one `type` attribute meaning either a `DAType` or an
    `EnumType`, told apart by `bType`. A `bType` that is neither -- `BOOLEAN`,
    `Timestamp`, the 20-odd basic types -- names no template at all and its
    `type` attribute, if the file writes one, is not a reference here.
    """
    out: list[tuple[str, str]] = []
    for child_name, attribute, referenced, btype in _REFERENCES.get(kind, ()):
        for child in children_local(element, child_name):
            if btype is not None and child.get("bType") != btype:
                continue
            target = child.get(attribute)
            if target:
                out.append((referenced, target))
    return out


def _closure(pool, roots) -> list[tuple[str, str]]:
    """Every ``(kind, id)`` reachable from ``roots``, in discovery order.

    Breadth-first so the `LNodeType` named by the caller come first and their
    `DOType` before the `DAType` and `EnumType` those reach -- which is the
    order they are inserted in, and the order a refusal lists them in.

    A `DAType` that references itself, directly or through a cycle, is
    malformed but reachable; membership in ``seen`` stops the walk the first
    time a type would be entered twice, exactly as
    :meth:`~py61850.scl.TemplatePool._attribute` does on the read side.

    Raises :class:`~py61850.scl.EditRejected` for a reference the source does
    not define: importing a closure that is not closed produces a target that
    cannot resolve what was just written into it. The module docstring records
    that no corpus file contains one.
    """
    seen = set()
    order: list[tuple[str, str]] = []
    queue = list(roots)
    while queue:
        key = queue.pop(0)
        if key in seen:
            continue
        element = pool.get(key)
        if element is None:
            kind, id_ = key
            raise EditRejected(
                f"the source document references {kind} {id_!r} and does not "
                f"define it; importing it would leave the target unable to "
                f"resolve the type just written into it")
        seen.add(key)
        order.append(key)
        queue.extend(_referenced(element, key[0]))
    return order


# -- the plan ---------------------------------------------------------------

def _fresh_id(kind, id_, taken) -> str:
    """An `id` like ``id_`` that no type of ``kind`` in the target carries.

    A numeric suffix off the source's own `id`, so the imported type stays
    recognisable as the one it was copied from --
    :data:`~py61850.scl.ALLOCATED_NAME_SUFFIX_START` and the rule
    :func:`~py61850.scl.unique_element_name` applies to element names.

    **A17 was expected to replace this and confirmed it instead.** Q34 §3
    conceded it as "the simplest rule that terminates, to be replaced there
    rather than competed with"; A17 measured the corpus and found the numeric
    suffix is what vendors already write -- `siemens.scd` carries ``DataSet``,
    ``DataSet_1``, ``DataSet_2``, ``DataSet_3``, and 77-100 % of its control
    block and dataset names end in a digit. So the suffix stayed.

    **The PREFIX is where the two deliberately differ.**
    `unique_element_name` puts ``new`` in front, because it invents a name
    from nothing and a placeholder should announce itself. This starts from
    the source's own `id`, which is already meaningful and already the
    engineer's: prefixing ``SEL_LLN0_V01`` to ``newSEL_LLN0_V01`` would lose
    the only thing that makes the imported type recognisable. A17b considered
    unifying them and kept the difference for that reason.
    """
    stem = id_ or kind
    n = SUFFIX_START
    while True:
        candidate = f"{stem}_{n}"
        if (kind, candidate) not in taken:
            return candidate
        n += 1


def _agrees(target_pool, source_pool, order, replacing) -> dict[tuple[str, str], bool]:
    """Whether the target's copy of each type means the same as the source's.

    **Comparing the two elements is not enough, and this is the defect that
    would matter most in a real merge.** A target and a source can carry
    `LNodeType` declarations that are identical character for character --
    same `id`, same `lnClass`, the same ``<DO name="Beh" type="SPS_1"/>`` --
    while their `SPS_1` differs. Reusing the target's on the strength of the
    elements matching gives the imported structure a `Beh` it does not have,
    silently, which is the exact outcome `on_conflict` exists to prevent. A
    type means what its CLOSURE means.

    So a type agrees when its own element agrees **and** every type it
    references agrees. A reference the target does not have at all agrees by
    construction: it will be added verbatim from the source, so the target's
    element ends up pointing at the source's content under the source's `id`.

    Computed as a decreasing fixpoint from "everything agrees", which
    terminates on a cycle -- a `DAType` reaching itself is malformed but
    reachable, and `_closure` already walks one without looping.
    """
    agrees = {}
    for key in order:
        mine = target_pool.get(key)
        agrees[key] = (key in replacing or mine is None
                       or same_data_type(mine, source_pool[key]))
    changed = True
    while changed:
        changed = False
        for key in order:
            if not agrees[key] or key not in target_pool or key in replacing:
                continue
            for reference in _referenced(source_pool[key], key[0]):
                if not agrees.get(reference, True):
                    agrees[key] = False
                    changed = True
                    break
    return agrees


def lnode_type_conflicts(doc, source, ids, on_conflict="refuse") -> TypeImport:
    """What importing ``ids`` from ``source`` into ``doc`` would do.

    ``ids`` is a sequence of `LNodeType` `id`s in ``source``; the closure each
    one drags -- its `DOType`, `DAType` and `EnumType`, transitively -- is
    resolved here and is what the answer describes.

    **This is the single computation.** :func:`import_lnode_types` calls it
    and builds its edits from the result, so the preview a caller shows and
    the import it then performs cannot disagree. The pairing is
    `can_add_data_set`/`create_data_set`'s, and the reason it is public at all
    is Q32 §0's: a guard exposes what it read so a refusal can be anticipated.

    **A type counts as already present only when its whole closure does.**
    Two documents can carry byte-identical `LNodeType` elements whose `DOType`
    differ, and reusing the target's on the strength of the elements matching
    would silently re-point the imported structure; :func:`_agrees` has the
    case.

    Returns a :class:`TypeImport`. Raises
    :class:`~py61850.scl.EditRejected` for an `id` ``source`` does not carry,
    for a source whose closure is not closed, for a namespace mismatch, or --
    under the default ``on_conflict="refuse"`` -- when anything conflicts.
    """
    return _plan(doc, source, ids, on_conflict, frozenset())


def _plan(doc, source, ids, on_conflict, replacing) -> TypeImport:
    """:func:`lnode_type_conflicts`, plus the keys the caller is REPLACING.

    ``replacing`` is :func:`update_lnode_type`'s, and it is the one thing that
    function needs which an import does not: the `LNodeType` being updated has
    the same `id` and different content by definition, so it would otherwise
    be counted as a conflict and refused every time. It keeps its `id`, it
    takes the source's content, and it is in none of the three tuples.
    """
    if on_conflict not in ON_CONFLICT:
        raise EditRejected(
            f"on_conflict must be one of {', '.join(ON_CONFLICT)}, "
            f"not {on_conflict!r}")
    if isinstance(ids, str):
        raise EditRejected(
            "ids is a sequence of LNodeType ids; a bare string would be read "
            "one character at a time")

    target_root = _root_of(doc)
    source_root = _root_of(source)
    if _namespace(target_root.tag) != _namespace(source_root.tag):
        raise EditRejected(
            f"the source is in namespace "
            f"{_namespace(source_root.tag) or '(none)'!r} and the target in "
            f"{_namespace(target_root.tag) or '(none)'!r}; merging a type "
            f"across SCL editions is not this function's question")

    source_pool = _pool(source_root)
    target_pool = _pool(target_root)

    roots = []
    for id_ in ids:
        key = ("LNodeType", id_)
        if key not in source_pool:
            raise EditRejected(
                f"the source document carries no LNodeType {id_!r}")
        roots.append(key)

    order = _closure(source_pool, roots)
    agrees = _agrees(target_pool, source_pool, order, replacing)

    # Every id the target will hold once this import is applied, not merely
    # the ones it holds now: a type that is added or reused keeps its source
    # id, so those are claimed too. Seeding `taken` with the target's pool
    # alone is a defect with a real instance in the corpus -- importing
    # `QPC2_TR1_AL11` renames `SIPROTEC5_LNType_USER_Universal` past the two
    # suffixes `mixed.scd` already uses and onto `..._3`, which is a DIFFERENT
    # type the same closure is importing under its own name. One of the two
    # then silently wins, and the pool comes out one type short. Q34 §5, and
    # it is Q33 §9's hazard one level up: inside a compound edit the document
    # is out of date, so the batch has to be its own record.
    taken = set(target_pool) | set(order)
    mapping: dict[tuple[str, str], str] = {}
    added: list[tuple[str, str]] = []
    reused: list[tuple[str, str]] = []
    conflicts: list[tuple[str, str]] = []

    for key in order:
        kind, id_ = key
        if key in replacing:
            # `update_lnode_type`'s root: it is being replaced by definition,
            # so it is neither a reuse nor a conflict and keeps its id.
            mapping[key] = id_
        elif key not in target_pool:
            added.append(key)
            mapping[key] = id_
        elif agrees[key]:
            reused.append(key)
            mapping[key] = id_
        else:
            conflicts.append(key)
            if on_conflict == "rename":
                fresh = _fresh_id(kind, id_, taken)
                taken.add((kind, fresh))
                mapping[key] = fresh
            elif on_conflict == "overwrite":
                mapping[key] = id_

    if conflicts and on_conflict == "refuse":
        raise EditRejected(
            f"{len(conflicts)} data type(s) already exist in the target under "
            f"the same id with different content: "
            f"{_listed(conflicts)}; pass on_conflict=\"rename\" to import "
            f"them under fresh ids, or on_conflict=\"overwrite\" to replace "
            f"the target's -- which re-types every instance using them")

    return TypeImport(ids=mapping, added=tuple(sorted(added)),
                      reused=tuple(sorted(reused)),
                      conflicts=tuple(sorted(conflicts)))


def _listed(keys, limit=6) -> str:
    """``kind id`` for the first few keys, with a count for the rest.

    A refusal naming 40 ids is not read; one naming six and saying how many
    more there are is. The caller that wants all of them has
    :func:`lnode_type_conflicts`, which is why this truncates rather than
    growing a parameter.
    """
    shown = ", ".join(f"{kind} {id_!r}" for kind, id_ in list(keys)[:limit])
    rest = len(keys) - limit
    return f"{shown} and {rest} more" if rest > 0 else shown


# -- importing --------------------------------------------------------------

def _repoint(node, kind, mapping) -> None:
    """Rewrite a COPIED type's references to the ids they carry in the target.

    Applied to the copy, never to the source's own element: the source
    document is read-only to this module and a caller may well go on using it.
    A reference the mapping does not carry is left as it is -- under
    ``"refuse"`` the import never gets this far, and under the other two
    policies every key in the closure is mapped.
    """
    for child_name, attribute, referenced, btype in _REFERENCES.get(kind, ()):
        for child in children_local(node, child_name):
            if btype is not None and child.get("bType") != btype:
                continue
            target = child.get(attribute)
            if target and (referenced, target) in mapping:
                child.set(attribute, mapping[(referenced, target)])


def import_lnode_types(doc, source, ids, on_conflict="refuse") -> list:
    """The edit that brings ``ids`` and everything they need into ``doc``.

    ``doc`` is the target :class:`~py61850.scl.SclDocument` and ``source`` is
    the one to read from -- **a document, not an element**, because the
    closure lives in ITS `DataTypeTemplates` and an `ElementTree` element
    carries no owner document. The reference's `importLNodeType` takes a bare
    element and can, since a DOM node has `ownerDocument`; ours cannot, and
    Q34 records it.

    ``ids`` is a sequence, and taking a whole set in one call is the second
    divergence. It is what makes the compound edit correct rather than merely
    convenient: two `LNodeType` sharing a `DOType` the target lacks would, as
    two calls, each read a document that does not yet hold what the other is
    inserting and each emit an `Insert` for it -- the defect Q33 §9 had to fix
    with a batch record one level down. One call over a list has no previous
    call to be stale against.

    ``on_conflict`` is ``"refuse"`` (the default), ``"rename"`` or
    ``"overwrite"``; the module docstring has the argument and the numbers.
    Types already present with identical content are reused under all three
    and produce no edit at all.

    Returns a list of :class:`~py61850.scl.Insert` -- and, under
    ``"overwrite"``, a :class:`~py61850.scl.Remove` before each replacement --
    ordered so that `DataTypeTemplates` is created first if the target has
    none. Each element is placed by A7's
    :func:`~py61850.scl.reference_for`, so a `DOType` lands among the
    `DOType`s rather than at the end of the section.

    **Every node inserted is a deep COPY.** The reference moves them, because
    a browser has `importNode` and its source is a throwaway parse of an
    upload; `ElementTree` has neither, and A5's `Insert` says a node from
    another document must not be handed over -- it would be in two trees at
    once. Q31 §6 took this decision for `insert_ied` before this phase
    existed.

    Raises :class:`~py61850.scl.EditRejected` for anything
    :func:`lnode_type_conflicts` refuses.
    """
    plan = lnode_type_conflicts(doc, source, ids, on_conflict)
    target_root = _root_of(doc)
    source_pool = _pool(_root_of(source))
    target_pool = _pool(target_root)

    edits: list = []
    section = _section(target_root)
    if section is None:
        # A document with no DataTypeTemplates at all is valid SCL -- an SSD
        # describing a substation and no IEDs is the ordinary case -- and the
        # section has to exist before anything goes in it. It is inserted
        # first, so by the time the type Inserts apply it is in the tree and
        # `Insert`'s own parent check passes.
        section = ET.Element(_namespace(target_root.tag) + "DataTypeTemplates")
        edits.append(Insert(target_root, section,
                            reference_for(target_root, section.tag)))

    placed = set(plan.reused)
    for key in _closure(source_pool, [("LNodeType", id_) for id_ in ids]):
        if key in placed:
            continue
        kind, _ = key
        node = copy.deepcopy(source_pool[key])
        node.set("id", plan.ids[key])
        _repoint(node, kind, plan.ids)
        if key in plan.conflicts and on_conflict == "overwrite":
            edits.append(Remove(target_pool[key]))
        edits.append(Insert(section, node, reference_for(section, node.tag)))
    return edits


def update_lnode_type(doc, source, id_, on_conflict="refuse") -> list:
    """The edit that replaces ``doc``'s `LNodeType` ``id_`` with ``source``'s.

    The reference exports `updateLNodeType(lNodeType, targetDoc)` and
    documents it nowhere -- no doc comment, no README entry. What it can mean
    is fixed by the pair it sits in: `importLNodeType` is for a type the
    target does not have, so this is for one it does, whose declaration has
    moved on. The target's element is removed **in place** and the source's
    copy goes into the same position, so the section's order is undisturbed.

    ``on_conflict`` governs the type's CLOSURE, not the type itself: the named
    `LNodeType` is being replaced by definition and needs no policy, while a
    `DOType` the new declaration reaches may well already exist in the target
    under the same id with different content, and that is the same three-way
    question :func:`import_lnode_types` answers. Sub-types already present
    identically are reused.

    Returns ``[]`` when the two declarations are already the same element --
    :func:`same_data_type`'s strict reading -- because an update that changes
    nothing should produce no history entry.

    **Nothing is pruned.** A `DOType` the OLD declaration was the last user of
    stays in the pool, unreferenced, and :func:`remove_data_type` is what
    takes it out. Splitting it that way keeps one rule for pruning in one
    function rather than two that can drift, which is Q25's precedent; the
    cost is that a caller who wants the pool tight follows this with a removal
    and says so.

    Raises :class:`~py61850.scl.EditRejected` if either document lacks an
    `LNodeType` ``id_``, and for anything :func:`lnode_type_conflicts`
    refuses.
    """
    target_root = _root_of(doc)
    source_root = _root_of(source)
    key = ("LNodeType", id_)

    target_pool = _pool(target_root)
    if key not in target_pool:
        raise EditRejected(
            f"the target document carries no LNodeType {id_!r}; a type it "
            f"does not have is import_lnode_types's, not this function's")
    source_pool = _pool(source_root)
    if key not in source_pool:
        raise EditRejected(
            f"the source document carries no LNodeType {id_!r}")

    # Nothing to do only when the WHOLE closure already agrees. Comparing the
    # two `LNodeType` elements alone would return early for a type whose
    # `DOType` has changed underneath it, which is the case `_agrees` exists
    # for and the one an update is most often asked about.
    if _agrees(target_pool, source_pool, _closure(source_pool, [key]),
               frozenset())[key]:
        return []

    # `key in target_pool` was checked above and `_pool` is built FROM the
    # section, so a pool that holds the key has a section. The annotation is
    # the only place that implication is written down.
    section: ET.Element = _section(target_root)   # type: ignore[assignment]
    old = target_pool[key]
    siblings = list(section)
    at = siblings.index(old)
    after = siblings[at + 1] if at + 1 < len(siblings) else None

    # The closure minus its root: those are ordinary imports under the
    # caller's policy, and `import_lnode_types` is the one place that decides
    # how a sub-type is placed.
    reached = [k for k in _closure(source_pool, [key]) if k != key]
    plan = _plan(doc, source, [id_], on_conflict, frozenset({key}))

    edits: list = [Remove(old)]
    node = copy.deepcopy(source_pool[key])
    node.set("id", id_)
    _repoint(node, "LNodeType", plan.ids)
    edits.append(Insert(section, node, after))

    placed = set(plan.reused)
    for sub in reached:
        if sub in placed:
            continue
        kind, _ = sub
        copied = copy.deepcopy(source_pool[sub])
        copied.set("id", plan.ids[sub])
        _repoint(copied, kind, plan.ids)
        if sub in plan.conflicts and on_conflict == "overwrite":
            edits.append(Remove(target_pool[sub]))
        edits.append(Insert(section, copied,
                            reference_for(section, copied.tag)))
    return edits


# -- removing ---------------------------------------------------------------

def _instance_lnode_types(root) -> set:
    """Every `lnType` an `LN`, `LN0` or `LNode` in the document names.

    This is the half of "linked" that lives outside `DataTypeTemplates`, and
    it is the half that matters in practice: all 767 corpus `LNodeType` are
    reached this way and none is reached from another template, because
    nothing in the schema lets one type name an `LNodeType`.
    """
    wanted = {_namespace(root.tag) + name for name in _LN_TYPE_ELEMENTS}
    return {element.get("lnType") for element in root.iter()
            if element.tag in wanted and element.get("lnType")}


def _linked(pool, excluded, instance_types) -> set:
    """Every ``(kind, id)`` in ``pool`` something still points at.

    ``excluded`` is the set of keys being removed, whose own references stop
    counting the moment they go -- that is what makes a prune transitive.
    """
    linked = set()
    for key, element in pool.items():
        if key in excluded:
            continue
        for reference in _referenced(element, key[0]):
            if reference in pool:
                linked.add(reference)
    for id_ in instance_types:
        if ("LNodeType", id_) in pool:
            linked.add(("LNodeType", id_))
    return linked


def remove_data_type(doc, edit, force=False) -> list:
    """``edit`` expanded: the type, and what its removal leaves unreferenced.

    ``edit`` is a :class:`~py61850.scl.Remove` whose node is an `LNodeType`,
    `DOType`, `DAType` or `EnumType` directly under this document's
    `DataTypeTemplates`. What comes back is the caller's own edit first, as
    Q23 settled for the whole of A9 onward, then a `Remove` for every type the
    first one strands.

    **"Strands" means NEWLY unreferenced.** A type nothing pointed at before
    this call is left exactly where it was -- the reference's own wording is
    *"not to leave NEW unlinked data types"* and the qualifier is kept rather
    than simplified away. It is invisible on the corpus, whose three pools
    contain no unreferenced type at all, which is precisely why the wording is
    followed instead of re-derived.

    The prune is transitive: removing an `LNodeType` can strand a `DOType`,
    which can strand the `DAType` and `EnumType` only it reached.

    ``force`` skips the guard that refuses to remove a type something still
    points at, in either direction -- an instance through
    `LN`/`LN0`/`LNode@lnType`, or another type through `DO`, `SDO`, `DA` or
    `BDA`. **All 767 corpus `LNodeType` are named by an instance, so without
    ``force`` this refuses on every one of them**; the guard is doing its job
    and the honest use of the function is after the instances are gone.

    Raises :class:`~py61850.scl.EditRejected` if ``edit`` is not a `Remove` of
    a data type of this document, or if the type is still referenced and
    ``force`` is not set.
    """
    if not isinstance(edit, Remove):
        raise EditRejected(
            f"remove_data_type takes a Remove, not {type(edit).__name__}")
    node = edit.node
    root = _root_of(doc)
    section = _section(root)
    kind = strip_ns(node.tag) if isinstance(node, ET.Element) else None
    if (kind not in DATA_TYPE_TAGS or section is None
            or not any(child is node for child in section)):
        raise EditRejected(
            f"{_describe(node)} is not a data type of this document; expected "
            f"an {', '.join(DATA_TYPE_TAGS)} directly under DataTypeTemplates")

    pool = _pool(root)
    key = (kind, node.get("id"))
    instance_types = _instance_lnode_types(root)

    if not force:
        holders = _referrers(pool, key, instance_types)
        if holders:
            raise EditRejected(
                f"{kind} {node.get('id')!r} is still referenced by "
                f"{holders}; pass force=True to remove it anyway and leave "
                f"the reference dangling")

    before = _linked(pool, set(), instance_types)
    removed = {key}
    while True:
        linked = _linked(pool, removed, instance_types)
        stranded = {other for other in pool
                    if other not in removed
                    and other in before
                    and other not in linked}
        if not stranded:
            break
        removed |= stranded

    # Discovery order, so the pruned types read down the closure rather than
    # in dictionary order, and a reader can follow what stranded what.
    order = {k: i for i, k in enumerate(pool)}
    # `removed - {key}` is drawn entirely from `pool`, so both the lookup and
    # the ranking below are total. Only `key` itself can carry a `None` id --
    # an element with no `id` is schema-invalid -- and it is the one subtracted.
    pruned: set[tuple[str, str]] = removed - {key}   # type: ignore[assignment]
    return [edit] + [Remove(pool[other])
                     for other in sorted(pruned, key=lambda k: order[k])]


def _referrers(pool, key, instance_types) -> str:
    """A short description of what still points at ``key``, or ``""``.

    Instances are counted first and named first because they are the reason a
    removal is usually refused: a type in a pool is referenced by a device,
    not by another type.
    """
    kind, id_ = key
    if kind == "LNodeType" and id_ in instance_types:
        return "an LN, LN0 or LNode in this document"
    holders = [other for other, element in pool.items()
               if other != key and key in _referenced(element, other[0])]
    if not holders:
        return ""
    return _listed(holders)


def _describe(element) -> str:
    if not isinstance(element, ET.Element):
        return type(element).__name__
    return strip_ns(element.tag) if isinstance(element.tag, str) else "a comment"
