# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""Where in its parent a new SCL element belongs.

    from py61850.scl import Insert, reference_for

    doc.apply_edit(Insert(ln0, dataset, reference_for(ln0, "DataSet")))

**SCL content models are XSD sequences**, so a child in the wrong position
produces a document that loads here and fails in DIGSI. The ordering is data
from the standard rather than something to reason about at a call site, so it
lives in a generated table -- :mod:`py61850.scl._content_models`, built by
``tools/build_content_models.py`` from the IEC schema, checked by
``tests/unit/test_scl_ordering.py`` -- and this module is the one function
that reads it.

**It resolves, it does not enforce.** :func:`reference_for` answers "which
child do I insert before"; it is :class:`~py61850.scl.Insert` that puts the
node there, and `Insert` neither calls this nor cares. That separation is
deliberate: the primitives stay primitive, an application that knows better
than the table can pass its own reference, and the property test in
``tests/unit/test_scl_edit_invertibility.py`` can go on inserting arbitrary
elements in arbitrary places. Refusing a badly placed child is the business of
the ``canAdd*`` guards that come with each capability, not of the edit layer.

## What a slot is

The table gives each element a tuple of slots, in order. A slot holds one name
where the schema fixes its position and several where it does not:

    "DOI": (("##other",), ("Text",), ("Private",), ("SDI", "DAI"))

``tDOI``'s content is a repeated ``xs:choice``, so `SDI` and `DAI` share a
slot and neither goes before the other -- the corpus writes both orders across
197,000 elements and both are correct. ``tServices`` is an ``xs:all``, so its
33 children share one slot. A new child goes after everything in its own slot
and before the first element of a later one.

## What it does not know

- **A parent the table does not list** -- a vendor element, anything below a
  `Private` -- resolves to "append". The alternative, refusing, would make the
  function unusable on the documents this library exists to read: `Private`
  content is ``xs:any`` by definition and no ordering applies inside it.
- **A child the parent may not hold** also resolves to "append", for the same
  reason. Whether it MAY be there is :func:`may_contain`'s question, and
  answering it is a guard's job rather than this one's.
- **Comments.** The reference is always an element, so a new child inserted
  ahead of an element that a comment introduces lands BETWEEN the comment and
  that element. Walking back over the comment to keep the pair together was
  written and then removed: it is right only where a comment captions what
  follows it, exactly wrong for one that trails what precedes it, and nothing
  in the markup tells the two apart. All 32 comments in the reference corpus
  sit inside a `Private`, whose content is ``xs:any``, so the case does not
  arise there at all. See Q18.
"""

from typing import Optional
from xml.etree import ElementTree as ET

from ._content_models import CONTENT_MODELS

# `xs:any namespace="##other"` -- where a foreign-namespace child belongs.
# `tBaseElement` puts it ahead of `Text` and `Private`, so it is a real
# position and not a fallback.
ANY = "##other"

# Built once, from the table: {element: {child: slot index}}. The table is the
# reviewable form -- it is what a reader compares against the schema -- and
# this is the form a lookup wants.
_RANKS = {
    parent: {name: index for index, slot in enumerate(slots) for name in slot}
    for parent, slots in CONTENT_MODELS.items()
}


def _local(tag) -> Optional[str]:
    """The local name of ``tag``, or ``None`` for a comment or a PI."""
    if not isinstance(tag, str):
        return None
    return tag[tag.index("}") + 1:] if tag.startswith("{") else tag


def _namespace(tag) -> str:
    if not isinstance(tag, str) or not tag.startswith("{"):
        return ""
    return tag[1:tag.index("}")]


def _rank(ranks, tag, parent_namespace) -> Optional[int]:
    """``tag``'s slot in ``ranks``, or ``None`` if the model does not place it.

    A child in a different namespace from its parent is foreign content, and
    the schema has a position for that -- the ``xs:any`` slot -- which is not
    the same as having no position at all.

    **A tag carrying NO namespace is not foreign**, it is a caller writing
    ``"DataSet"`` instead of ``"{uri}DataSet"``, which this module accepts on
    purpose. Reading the empty namespace as "differs from the parent's" would
    send every such call to the ``xs:any`` slot and put new children at the
    top of the element.
    """
    name = _local(tag)
    if name is None:
        return None
    namespace = _namespace(tag)
    if namespace and namespace != parent_namespace:
        return ranks.get(ANY)
    return ranks.get(name)


def content_model(parent_tag):
    """The slots ``parent_tag`` may hold, in order, or ``()`` if unlisted.

    ``parent_tag`` is a tag or a local name: ``"LN0"`` and
    ``"{http://www.iec.ch/61850/2003/SCL}LN0"`` both work, because callers
    have an ``Element`` and elements carry the namespace.
    """
    return CONTENT_MODELS.get(_local(parent_tag) or "", ())


def may_contain(parent_tag, child_tag) -> bool:
    """Whether the content model of ``parent_tag`` names ``child_tag``.

    A **schema** question, not a document one: it says nothing about how many
    are allowed, which the table does not carry, nor about the attributes that
    make an element valid. The ``canAdd*`` guards that arrive with each
    capability are what answer those; this is the part of it that is pure
    ordering data.

    **An ``xs:any`` model accepts foreign content**, and `Private` is the case
    that matters -- its whole purpose is to hold elements from another
    namespace. Answering ``False`` there would have a guard refuse exactly the
    content the element exists for. Both tags have to be given in full
    ``{uri}local`` form for that to be visible; with bare names there is no
    namespace to compare and only the named children answer ``True``.
    """
    ranks = _RANKS.get(_local(parent_tag) or "")
    if not ranks:
        return False
    if (_local(child_tag) or "") in ranks:
        return True
    child_ns = _namespace(child_tag)
    return bool(ANY in ranks and child_ns and child_ns != _namespace(parent_tag))


def reference_for(parent: ET.Element, tag) -> Optional[ET.Element]:
    """The child of ``parent`` that a new ``tag`` should be inserted before.

    ``None`` means append, which is also the answer for a parent the table
    does not list and for a child its model does not name -- see the module
    docstring. Pass the result straight to :class:`~py61850.scl.Insert`.

    ``tag`` may be a local name or a full ``{uri}local`` tag; an
    ``Element`` is accepted too, so a caller that has built the node can pass
    the node.
    """
    if isinstance(tag, ET.Element):
        tag = tag.tag
    ranks = _RANKS.get(_local(parent.tag) or "")
    if not ranks:
        return None
    namespace = _namespace(parent.tag)
    mine = _rank(ranks, tag, namespace)
    if mine is None:
        return None

    for child in parent:
        rank = _rank(ranks, child.tag, namespace)
        if rank is not None and rank > mine:
            return child
    return None
