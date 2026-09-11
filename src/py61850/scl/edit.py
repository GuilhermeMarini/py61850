# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""Changing an SCL document: four primitives, each invertible.

    from py61850.scl import SclDocument, SetAttributes

    doc = SclDocument.parse("station.scd")
    undo = doc.apply_edit(SetAttributes(extref, {"iedName": "REL1"}))
    doc.apply_edit(undo)            # and the document is what it was

**Four edits, and a list of edits is a fifth.** :class:`Insert`,
:class:`Remove`, :class:`SetAttributes` and :class:`SetTextContent` are the
only sanctioned way to change a document. A ``list`` (or ``tuple``) of them is
itself an edit: applied in order, inverted in reverse. That is what makes a
compound operation -- "subscribe this ExtRef", which is several primitives --
a single entry in whatever history a consumer keeps.

**Every edit computes its inverse from the tree as it stands, before it
applies.** :meth:`SclDocument.apply_edit` returns that inverse, and applying
the inverse restores the document exactly -- attribute ORDER included, because
attribute order is part of the file and an undo that reorders them is not an
undo. Nothing here remembers what it returned: a session, a journal and an
undo stack are the consumer's, not this library's.

**A rejected edit changes nothing.** An edit whose preconditions do not hold
raises :class:`EditRejected`. Inside a list, an edit rejected part way through
rolls the earlier ones back -- through their own inverses, which is the same
machinery -- before the exception leaves. Partial application is a bug in the
same way a half-written settings file is: what reaches DIGSI or SEL Architect
has to be a document someone meant.

## The parent map

``ElementTree`` has no parent pointers, and :class:`Remove` needs one. A map
from element to parent is therefore built per document, lazily on the first
edit, and maintained by the applier -- which can maintain it because the
applier is the only thing that changes the tree. It is not free: on a 22 MB
station export it is 433,325 entries and 21 MB, built in 98 ms. A document
that is only read never pays for it.

**If the tree is changed behind the applier's back**, by calling
``ET`` directly, the map is stale. A lookup that misses rebuilds the map once
and retries, so a stale map costs 98 ms rather than a wrong answer -- with one
asymmetry that cannot be closed cheaply: :class:`Insert` reads the map to
decide whether the node it is given is already in the document (a move) or new
(a plain insertion), and a *new* node is indistinguishable from one added
behind its back, since both are absent. Insert therefore trusts the map
without rebuilding. Do not add elements to the tree by hand and then insert
them through an edit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Union
from xml.etree import ElementTree as ET

from ..errors import SclError


class EditRejected(SclError, ValueError):
    """An edit whose preconditions do not hold. Nothing was changed.

    It sits beside :class:`~py61850.scl.DtdNotAllowed` -- an ``SclError`` that
    is also a ``ValueError``, defined in the package that raises it rather
    than in the error tree, because it is meaningless outside SCL editing.
    """


# Characters that cannot appear in an XML attribute name. This is not the full
# `Name` production from XML 1.0 -- implementing that would mean carrying a
# Unicode category table for no measured benefit. It is the set that catches a
# caller who built a name by concatenation and got it wrong, which is the
# mistake that actually happens. `ElementTree` checks none of this: it writes
# whatever string it is handed and the file is broken at the far end.
_BAD_IN_NAME = frozenset(' \t\r\n<>"\'/=&')


@dataclass(frozen=True)
class Insert:
    """Put ``node`` into ``parent``, before ``reference``.

    ``reference=None`` appends. It has no default: "append" is worth spelling
    out at the call site, and a frozen dataclass with ``__slots__`` cannot
    carry one anyway.

    **A node that is already in this document is MOVED**, and the inverse is
    then an :class:`Insert` putting it back where it was -- not a
    :class:`Remove`. A node that is new gives an inverse of :class:`Remove`.
    A node belonging to a DIFFERENT document cannot be detected (``ET`` has no
    owner-document pointer) and must not be handed over: it would end up in
    two trees at once.
    """

    __slots__ = ("parent", "node", "reference")

    parent: ET.Element
    node: ET.Element
    reference: Optional[ET.Element]


@dataclass(frozen=True)
class Remove:
    """Take ``node`` out of the document. The inverse is an :class:`Insert`
    carrying the parent and the sibling that followed it."""

    __slots__ = ("node",)

    node: ET.Element


@dataclass(frozen=True)
class SetAttributes:
    """Set or delete attributes on ``element``.

    ``attributes`` maps name to value, or to ``None`` to delete the attribute.
    A name already present keeps its position; a new one is appended; a
    deletion leaves the order of the rest alone.

    **One rule beyond that, and it exists so a deletion can be undone.** A
    mapping that names EVERY attribute the element currently has is read as a
    complete, ordered description of them: the attributes are rebuilt in the
    mapping's order. Without it the inverse of a deletion would re-add the
    attribute at the end, and a document that had been edited and fully undone
    would no longer be the file it came from.

    **The inverse of such an edit describes the element as it was**, in the
    element's own order -- which is what makes the rule survive being applied
    twice. An edit that names every attribute and changes only their values
    still rebuilds them in the mapping's order, so an inverse that carried
    the mapping's order instead would undo into it.

    **The root element is the one place the order rule cannot reach.** The
    writer puts the root's attributes back into the order the FILE wrote
    them, because it is the one element `ElementTree` reorders -- see
    `SclDocument.to_bytes`. Values and the key set reach the file; a
    reordering of them does not.
    """

    __slots__ = ("element", "attributes")

    element: ET.Element
    attributes: Mapping[str, Optional[str]]

    def __post_init__(self) -> None:
        # Copied, so an edit is a value: a caller that keeps mutating the dict
        # it passed cannot change an edit already applied, or an inverse
        # already handed out.
        object.__setattr__(self, "attributes", dict(self.attributes))


@dataclass(frozen=True)
class SetTextContent:
    """Set ``element``'s text.

    This is ``ET``'s ``.text`` -- the text before the first child -- and NOT
    the DOM's ``textContent``, which is every descendant's text concatenated
    and whose setter deletes the children. Child elements are untouched here,
    which is what makes the primitive invertible at all.

    ``None`` is a real value and not the same as ``""``, and both survive an
    edit and its inverse. **The difference is in the tree and not in the
    file**: `ElementTree`'s serialiser tests the text for truth rather than
    for ``None``, so an element with either spelling comes out ``<X />``, with
    children or without. A consumer that reads ``.text`` can tell them apart;
    a consumer that reads the bytes cannot.
    """

    __slots__ = ("element", "text")

    element: ET.Element
    text: Optional[str]


Edit = Union[Insert, Remove, SetAttributes, SetTextContent, list, tuple]

_PRIMITIVES = (Insert, Remove, SetAttributes, SetTextContent)

# Distinguishes "this element is not in the document" from "this element is
# the root, and has no parent". `None` cannot say both.
_MISSING = object()


# -- the parent map ---------------------------------------------------------

def _build(root: ET.Element) -> Dict[ET.Element, ET.Element]:
    return {child: el for el in root.iter() for child in el}


def _map(doc) -> Dict[ET.Element, ET.Element]:
    m = doc._cache.get("parents")
    if m is None:
        m = doc._cache["parents"] = _build(doc.root)
    return m


def _parent(doc, node, rebuild: bool = True):
    """``node``'s parent, ``None`` if it is the root, ``_MISSING`` if this
    document does not contain it.

    ``rebuild=False`` for the one caller that must not pay for a miss: every
    fresh node an :class:`Insert` is given is a miss, and rebuilding the map
    on each of them would put a 98 ms walk behind every insertion.
    """
    m = _map(doc)
    p = m.get(node, _MISSING)
    if p is not _MISSING:
        return p
    if node is doc.root:
        return None
    if rebuild:
        m = doc._cache["parents"] = _build(doc.root)
        p = m.get(node, _MISSING)
        if p is not _MISSING:
            return p
    return _MISSING


def parent_of(doc, element: ET.Element) -> Optional[ET.Element]:
    """``element``'s parent in ``doc``, or ``None``.

    ``None`` means the root element or an element this document does not
    contain; ``element is doc.root`` tells the two apart. The map behind it is
    built on first use, so the first call on a large document costs a walk.
    """
    p = _parent(doc, element)
    return None if p is _MISSING else p


def _index(parent: ET.Element, child: ET.Element) -> int:
    # By identity. `list.index` would compare with `==`, which on `Element`
    # IS identity today -- but that is a default nobody promised, and two
    # `<FCDA/>` elements with equal attributes are not the same FCDA.
    for i, c in enumerate(parent):
        if c is child:
            return i
    return -1


def _next_sibling(parent: ET.Element, child: ET.Element) -> Optional[ET.Element]:
    i = _index(parent, child)
    return parent[i + 1] if 0 <= i < len(parent) - 1 else None


# -- static checks ----------------------------------------------------------

def _check_static(edit) -> None:
    """Everything checkable without looking at the tree.

    Run over the whole edit BEFORE anything is applied, so the ordinary caller
    mistakes -- a misspelled attribute name, an int where a string belongs --
    are refused without touching the document and without a rollback.
    """
    if isinstance(edit, (list, tuple)):
        for e in edit:
            _check_static(e)
        return
    if not isinstance(edit, _PRIMITIVES):
        raise EditRejected(
            f"not an edit: {type(edit).__name__}; expected one of "
            f"{', '.join(p.__name__ for p in _PRIMITIVES)}, or a list of them")
    if isinstance(edit, Insert):
        _check_element(edit.parent, "parent")
        _check_element(edit.node, "node")
        if edit.reference is not None:
            _check_element(edit.reference, "reference")
        if not isinstance(edit.parent.tag, str):
            raise EditRejected(
                "a comment or processing instruction cannot be a parent")
    elif isinstance(edit, Remove):
        _check_element(edit.node, "node")
    elif isinstance(edit, SetAttributes):
        _check_element(edit.element, "element")
        for name, value in edit.attributes.items():
            _check_attribute_name(name)
            if value is not None and not isinstance(value, str):
                raise EditRejected(
                    f"attribute {name!r} must be a string or None, not "
                    f"{type(value).__name__}")
    else:
        _check_element(edit.element, "element")
        if edit.text is not None and not isinstance(edit.text, str):
            raise EditRejected(
                f"text must be a string or None, not {type(edit.text).__name__}")


def _check_element(value, role: str) -> None:
    if not isinstance(value, ET.Element):
        raise EditRejected(
            f"{role} must be an Element, not {type(value).__name__}")


def _check_attribute_name(name) -> None:
    if not isinstance(name, str):
        raise EditRejected(
            f"attribute name must be a string, not {type(name).__name__}")
    if not name:
        raise EditRejected("attribute name is empty")
    # A namespaced attribute is spelled `{uri}local` here, the way `ET` stores
    # it. The URI is a URI and none of the rules below apply inside it -- it
    # is full of the slashes and colons a NAME may not contain.
    local = name
    if name.startswith("{"):
        end = name.find("}")
        if end < 0:
            raise EditRejected(
                f"attribute name {name!r} opens a namespace it never closes")
        if end == 1:
            raise EditRejected(f"attribute name {name!r} has an empty namespace")
        local = name[end + 1:]
        if not local:
            raise EditRejected(
                f"attribute name {name!r} is a namespace with no local name")
    bad = _BAD_IN_NAME.intersection(local)
    if bad:
        raise EditRejected(
            f"attribute name {name!r} contains {''.join(sorted(bad))!r}")
    if local[0].isdigit() or local[0] in "-.":
        raise EditRejected(f"attribute name {name!r} does not start a name")
    # `prefix:local` would be written out as an attribute LITERALLY called
    # `prefix:local` and mean nothing to a reader resolving namespaces. The
    # exception is a namespace declaration, which the writer already puts on
    # the root that way.
    if ":" in local and not (local == "xmlns" or local.startswith("xmlns:")):
        raise EditRejected(
            f"attribute name {name!r} is prefixed; namespaced attributes are "
            f"written '{{uri}}local', as ElementTree stores them")


# -- applying ---------------------------------------------------------------

def apply_edit(doc, edit: Edit) -> Edit:
    """Apply ``edit`` to ``doc`` and return the edit that undoes it."""
    _check_static(edit)
    return _apply(doc, edit)


def _apply(doc, edit):
    if isinstance(edit, (list, tuple)):
        inverses: List[Edit] = []
        for e in edit:
            try:
                inverses.append(_apply(doc, e))
            except EditRejected:
                # Back out in reverse, through the inverses already computed.
                # They are exact, and every precondition they need holds --
                # the tree is being walked back through states it was just in.
                for undo in reversed(inverses):
                    _apply(doc, undo)
                raise
        inverses.reverse()
        return inverses
    if isinstance(edit, Insert):
        return _insert(doc, edit)
    if isinstance(edit, Remove):
        return _remove(doc, edit)
    if isinstance(edit, SetAttributes):
        return _set_attributes(doc, edit)
    return _set_text(doc, edit)


def _insert(doc, edit: Insert) -> Edit:
    parent, node, reference = edit.parent, edit.node, edit.reference

    if _parent(doc, parent) is _MISSING:
        raise EditRejected("parent is not in this document")
    if node is doc.root:
        raise EditRejected("the root element cannot be inserted")
    if node is parent:
        raise EditRejected("an element cannot be inserted into itself")
    if reference is not None:
        if reference is node:
            raise EditRejected("reference is the node being inserted")
        if _index(parent, reference) < 0:
            raise EditRejected("reference is not a child of parent")

    # A cycle would make the tree unwalkable and `to_bytes` recurse forever.
    ancestor = _parent(doc, parent, rebuild=False)
    while ancestor is not None and ancestor is not _MISSING:
        if ancestor is node:
            raise EditRejected("that insertion would put an element inside itself")
        ancestor = _parent(doc, ancestor, rebuild=False)

    former = _parent(doc, node, rebuild=False)
    moving = former is not _MISSING and former is not None
    if moving:
        inverse: Edit = Insert(former, node, _next_sibling(former, node))
        former.remove(node)
    else:
        inverse = Remove(node)

    # AFTER the removal: moving a node within its own parent shifts the index
    # of everything after it.
    at = len(parent) if reference is None else _index(parent, reference)
    parent.insert(at, node)

    m = _map(doc)
    m[node] = parent
    if not moving:
        # A fresh node arrives with a subtree the map has never seen. A moved
        # one brought its descendants' entries with it.
        for el in node.iter():
            for child in el:
                m[child] = el
    return inverse


def _remove(doc, edit: Remove) -> Edit:
    node = edit.node
    parent = _parent(doc, node)
    if parent is _MISSING:
        raise EditRejected("the node is not in this document")
    if parent is None:
        raise EditRejected("the root element cannot be removed")

    inverse = Insert(parent, node, _next_sibling(parent, node))
    parent.remove(node)

    m = _map(doc)
    del m[node]
    for el in node.iter():
        for child in el:
            m.pop(child, None)
    return inverse


def _set_attributes(doc, edit: SetAttributes) -> Edit:
    element = edit.element
    if _parent(doc, element) is _MISSING:
        raise EditRejected("the element is not in this document")

    attrib = element.attrib
    wanted = edit.attributes
    adds = [k for k, v in wanted.items() if v is not None and k not in attrib]
    drops = [k for k, v in wanted.items() if v is None and k in attrib]

    complete = all(name in wanted for name in attrib)

    if adds or drops or complete:
        # The inverse has to restore ORDER as well as values -- and the only
        # thing that can is a complete description of what was there, read
        # off the ELEMENT rather than off the edit. See `SetAttributes`.
        #
        # `complete` belongs in that condition and not only `adds or drops`.
        # An edit that names every attribute and changes nothing but their
        # values leaves the key set alone, but it is still rebuilt in the
        # mapping's order below -- so an inverse built by walking `wanted`
        # would carry the EDIT's order and undo into it. Values right, file
        # different, which is the failure Q13 exists for, one layer down.
        # The invertibility property test found it on five seeds out of five.
        previous: Dict[str, Optional[str]] = dict(attrib)
        for name in adds:
            previous[name] = None
    else:
        previous = {name: attrib.get(name) for name in wanted}

    if complete:
        # A complete description: rebuilt in the mapping's order.
        rebuilt = {k: v for k, v in wanted.items() if v is not None}
        attrib.clear()
        attrib.update(rebuilt)
    else:
        for name, value in wanted.items():
            if value is None:
                attrib.pop(name, None)
            else:
                attrib[name] = value
    return SetAttributes(element, previous)


def _set_text(doc, edit: SetTextContent) -> Edit:
    element = edit.element
    if _parent(doc, element) is _MISSING:
        raise EditRejected("the element is not in this document")
    inverse = SetTextContent(element, element.text)
    element.text = edit.text
    return inverse
