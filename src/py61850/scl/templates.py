# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""``DataTypeTemplates`` -- the station-wide pool every instance resolves through.

An ``LN`` instance carries only ``lnType="SEL_LLN0_V01"``. What data objects it
has, what attributes each of those has, which functional constraint each
attribute sits under and what its base type is -- all of that lives here, once
per document, shared by every IED that references it.

Measured on the reference corpus: ~280 ``LNodeType`` against ~180,000 ``DAI``.
The pool is 0.1% of the file and every instance resolution needs it, which is
why it is built once per document and cached, while instance trees are built
per IED and on demand.

The pool answers what the file *declares*. Whether a particular instance
overrides a value is the instance model's business, not this module's.
"""

from __future__ import annotations

from .document import children_local, privates_of

# A DAType that references itself, directly or through a cycle, is malformed
# but reachable -- expansion stops at this depth rather than exhausting the
# stack. The deepest legitimate nesting in the reference corpus is 3.
_MAX_STRUCT_DEPTH = 8


class AttributeSpec:
    """One ``DA`` or ``BDA`` declaration: what the type says this attribute is."""

    __slots__ = ("name", "fc", "btype", "type", "count", "sub_specs",
                 "enum_values", "privates")

    def __init__(self, name, fc, btype, type_, count=None, sub_specs=None,
                 enum_values=None, privates=None):
        self.name = name
        self.fc = fc
        self.btype = btype
        self.type = type_
        self.count = count
        #: ``{name: AttributeSpec}`` when ``btype == "Struct"``, else ``{}``.
        self.sub_specs = sub_specs or {}
        #: ``{ord: label}`` when ``btype == "Enum"`` and the EnumType resolves.
        self.enum_values = enum_values
        self.privates = privates or {}

    def __repr__(self):
        return f"<AttributeSpec {self.name!r} fc={self.fc!r} bType={self.btype!r}>"


class DoTypeSpec:
    """One ``DOType``: a common data class and the attributes it declares."""

    __slots__ = ("id", "cdc", "attributes", "sub_objects", "privates")

    def __init__(self, id_, cdc, attributes, sub_objects, privates):
        self.id = id_
        self.cdc = cdc
        #: ``{name: AttributeSpec}`` -- the first-level DAs.
        self.attributes = attributes
        #: ``{name: DOType id}`` -- the SDOs, e.g. WYE's phsA.
        self.sub_objects = sub_objects
        self.privates = privates

    def __repr__(self):
        return f"<DoTypeSpec {self.id!r} cdc={self.cdc!r}>"


class LNodeTypeSpec:
    """One ``LNodeType``: a logical-node class and the DOs it declares."""

    __slots__ = ("id", "ln_class", "objects", "privates")

    def __init__(self, id_, ln_class, objects, privates):
        self.id = id_
        self.ln_class = ln_class
        #: ``{DO name: DOType id}``
        self.objects = objects
        self.privates = privates

    def __repr__(self):
        return f"<LNodeTypeSpec {self.id!r} lnClass={self.ln_class!r}>"


class TemplatePool:
    """The parsed ``DataTypeTemplates`` of one document.

    Every lookup returns ``None`` for an id the file does not carry. A file
    referencing a type it does not define is malformed but real -- an ICD a
    tool trimmed, a hand-edited SCD -- and that is a fact for the caller to
    report, not a reason to fail the document.
    """

    __slots__ = ("_lnode_types", "_do_types", "_da_types", "_enum_types")

    def __init__(self, root):
        self._enum_types = {}
        self._da_types = {}
        self._do_types = {}
        self._lnode_types = {}
        self._build(root)

    def _build(self, root):
        # DIRECT CHILD of the root only -- `<DataTypeTemplates>` is
        # schema-valid only there, and a descendant scan would also pick up
        # any same-named element a vendor `Private` block happens to nest.
        # See `document._ied_elements`'s docstring for the shadowing this
        # already caused once, for `<IED>`.
        for section in children_local(root, "DataTypeTemplates"):
            for el in children_local(section, "EnumType"):
                self._enum_types[el.get("id")] = _enum_values(el)
            for el in children_local(section, "DAType"):
                self._da_types[el.get("id")] = el
            for el in children_local(section, "DOType"):
                self._do_types[el.get("id")] = el
            for el in children_local(section, "LNodeType"):
                self._lnode_types[el.get("id")] = el

    # -- lookups ------------------------------------------------------------

    def enum_type(self, id_):
        """``{ord: label}`` for an ``EnumType`` id, or ``None``."""
        return self._enum_types.get(id_)

    def da_type(self, id_):
        """``{name: AttributeSpec}`` for a ``DAType`` id, or ``None``."""
        el = self._da_types.get(id_)
        if el is None:
            return None
        return self._bdas(el, fc=None, depth=0, seen=frozenset({id_}))

    def do_type(self, id_):
        """:class:`DoTypeSpec` for a ``DOType`` id, or ``None``."""
        el = self._do_types.get(id_)
        if el is None:
            return None
        if isinstance(el, DoTypeSpec):
            return el
        spec = DoTypeSpec(
            id_=id_,
            cdc=el.get("cdc"),
            attributes={da.get("name"): self._attribute(da, depth=0)
                        for da in children_local(el, "DA")},
            sub_objects={sdo.get("name"): sdo.get("type")
                         for sdo in children_local(el, "SDO")},
            privates=privates_of(el),
        )
        # Elements are replaced by specs on first lookup, here, so a document
        # whose templates are never consulted pays only for the indexing
        # pass in _build.
        self._do_types[id_] = spec
        return spec

    def lnode_type(self, id_):
        """:class:`LNodeTypeSpec` for an ``LNodeType`` id, or ``None``."""
        el = self._lnode_types.get(id_)
        if el is None:
            return None
        if isinstance(el, LNodeTypeSpec):
            return el
        spec = LNodeTypeSpec(
            id_=id_,
            ln_class=el.get("lnClass"),
            objects={do.get("name"): do.get("type")
                     for do in children_local(el, "DO")},
            privates=privates_of(el),
        )
        self._lnode_types[id_] = spec
        return spec

    # -- construction of one attribute --------------------------------------

    def _attribute(self, el, depth, inherited_fc=None, seen=frozenset()):
        """One ``DA``/``BDA`` element -> :class:`AttributeSpec`.

        ``inherited_fc`` carries the root DA's functional constraint down
        through a Struct: IEC 61850 puts the FC on the root, and everything
        inside it comes along. ``Oper.ctlVal`` is ``CO`` because ``Oper`` is.

        ``seen`` is the set of ``DAType`` ids already expanded on this path.
        A ``DAType`` that references itself, directly or through a cycle, is
        malformed but reachable -- checking membership stops expansion the
        moment a type would be entered twice, rather than guessing how many
        levels are "enough". ``_MAX_STRUCT_DEPTH`` remains underneath it as a
        backstop for a long chain of distinct types that never repeats.
        """
        btype = el.get("bType")
        type_ = el.get("type")
        fc = el.get("fc") or inherited_fc
        sub = {}
        if (btype == "Struct" and type_ and depth < _MAX_STRUCT_DEPTH
                and type_ not in seen):
            da_type_el = self._da_types.get(type_)
            if da_type_el is not None:
                sub = self._bdas(da_type_el, fc, depth + 1, seen | {type_})
        return AttributeSpec(
            name=el.get("name"), fc=fc, btype=btype, type_=type_,
            count=el.get("count"),
            sub_specs=sub,
            enum_values=self._enum_types.get(type_) if btype == "Enum" else None,
            privates=privates_of(el),
        )

    def _bdas(self, da_type_el, fc, depth, seen=frozenset()):
        return {bda.get("name"): self._attribute(bda, depth, inherited_fc=fc,
                                                   seen=seen)
                for bda in children_local(da_type_el, "BDA")}


def _enum_values(el) -> dict:
    """``{ord: label}`` for one ``EnumType`` element.

    An ``EnumVal`` with a non-integer ``ord`` is dropped rather than guessed
    at: the ordinal is what a decoded value is matched against, and a wrong
    one renames a state.
    """
    out = {}
    for val in children_local(el, "EnumVal"):
        try:
            out[int(val.get("ord"))] = (val.text or "").strip()
        except (TypeError, ValueError):
            continue
    return out
