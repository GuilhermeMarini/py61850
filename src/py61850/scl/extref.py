# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""Subscribing an `ExtRef` to a published data attribute, and unsubscribing it.

    from py61850.scl import Connection, SclDocument, subscribe

    doc = SclDocument.parse("station.scd")
    edit = subscribe(doc, Connection(sink=ext_ref, fcda=fcda, control_block=cb))
    undo = doc.apply_edit(edit)     # one history entry, however many primitives

**These functions build an edit; they do not apply one.** `subscribe` and
`unsubscribe` return a compound edit -- a list of the primitives in
:mod:`py61850.scl.edit`, which that module treats as a single edit applied in
order and inverted in reverse. Nothing here touches the document until the
caller passes the result to :meth:`SclDocument.apply_edit`, and nothing here
remembers that it did.

**A diff is computed against the document as it stands.** Two subscriptions
built by two separate calls and then applied together can therefore conflict
-- both would create the `Inputs` element the other also creates. Pass the
connections together instead, in one call, and they are reconciled:

    subscribe(doc, [Connection(...), Connection(...)])   # right
    [subscribe(doc, c1), subscribe(doc, c2)]             # two `Inputs`

## What a subscription IS, in the file

An `ExtRef` says "this logical node takes an input from somewhere else", and
it says it in two halves that this module always writes together:

- the **data attribute** -- `iedName`, `ldInst`, `prefix`, `lnClass`,
  `lnInst`, `doName`, `daName` -- which names the published attribute;
- the **source** -- `srcLDInst`, `srcPrefix`, `srcLNClass`, `srcLNInst`,
  `srcCBName` -- which names the control block that publishes it, and
  `serviceType`, which says how.

Both halves, because the corpus writes both: 2,924 of `mixed.scd`'s ExtRefs,
693 of `sel.scd`'s and 422 of `siemens.scd`'s carry the full set. A few carry
the source half alone -- 218 in `sel.scd` -- which is a subscription to a
control block that has not yet been pointed at an attribute, and is exactly
why :attr:`ExtRef.is_bound` asks for `iedName` and `srcCBName` rather than for
all twelve.

## Later binding, and the ExtRef that is deleted instead of blanked

An `ExtRef` carrying `intAddr` is a TEMPLATE the IED published for itself: the
internal address exists whether or not anything is connected to it, so
unsubscribing **blanks the binding and keeps the element**. An `ExtRef`
without `intAddr` only exists because someone made the connection, so
unsubscribing **removes it**, and removes an `Inputs` left with no children.
Both shapes are in the corpus: 12,482 of `sel.scd`'s 12,540 ExtRefs carry
`intAddr`, and the 58 that do not are its Report subscriptions.

## The type restrictions

`pServT`, `pLN`, `pDO` and `pDA` are what an IED's own ICD declares it will
accept at an input. `pServT` and `pLN` are checked against the document alone.
`pDO` and `pDA` name a data object and a data attribute in IEC's namespace
rather than in the file, so they are resolved through
:mod:`py61850.scl._nsd_types`; when that table is not present, those two
restrictions are not checked -- an unresolvable restriction is not a
restriction, which is the same thing the file already says about the 12,540
ExtRefs in `sel.scd` that declare no `pDO` at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, NamedTuple, Optional
from xml.etree import ElementTree as ET

from .document import children_local, strip_ns
from .edit import EditRejected, Insert, Remove, SetAttributes
from .ordering import reference_for

try:  # pragma: no cover - the absent branch is exercised by test_scl_extref
    from ._nsd_types import CDC_ATTRIBUTES, DO_CDC
except ImportError:  # pragma: no cover
    # The table is derived from an IEC code component. Everything here works
    # without it; the `pDO`/`pDA` half of the restriction check simply has
    # nothing to check against. See the module docstring.
    CDC_ATTRIBUTES: dict = {}
    DO_CDC: dict = {}

#: The control-block elements an `ExtRef` can name, and the `serviceType` each
#: one is published by. `LogControl` is absent on purpose: a log is written,
#: not published, so no ExtRef subscribes to one.
SERVICE_TYPE = {
    "GSEControl": "GOOSE",
    "SampledValueControl": "SMV",
    "ReportControl": "Report",
}

#: The attributes that name the published data attribute, in the order 61850-6
#: declares them -- which is the order a file writes them in, and therefore the
#: order a newly bound ExtRef grows them in.
DATA_ATTRIBUTES = ("iedName", "ldInst", "prefix", "lnClass", "lnInst",
                   "doName", "daName")

#: The attributes that name the publishing control block.
SRC_ATTRIBUTES = ("srcLDInst", "srcPrefix", "srcLNClass", "srcLNInst",
                  "srcCBName")

#: Everything a subscription writes, and therefore everything unsubscribing
#: takes away.
BINDING_ATTRIBUTES = DATA_ATTRIBUTES + ("serviceType",) + SRC_ATTRIBUTES


@dataclass(frozen=True)
class Connection:
    """One subscription to build: a sink, and the source it takes.

    ``sink`` is an `ExtRef` for later binding -- the IED published the input
    and the engineer is filling in where it comes from -- or an `LN0`, `LN` or
    `Inputs`, in which case the `ExtRef` (and the `Inputs`, if it is missing)
    is created.

    ``control_block`` is ``None`` for a connection that writes only the
    data-attribute half. That is a real state and not a degenerate one: 218
    ExtRefs in the reference corpus carry a source and no data attributes, and
    the reverse is equally expressible. It has no default, for the reason
    :class:`~py61850.scl.Insert`'s ``reference`` has none -- spelling it at the
    call site is worth the two words, and a frozen dataclass with ``__slots__``
    cannot carry one anyway.
    """

    __slots__ = ("sink", "fcda", "control_block")

    sink: ET.Element
    fcda: ET.Element
    control_block: Optional[ET.Element]


class TypeRestriction(NamedTuple):
    """A common data class, and the basic type of one of its attributes.

    It says two things depending on where it came from: what an `ExtRef`
    REQUIRES, read off its `pDO` and `pDA`, and what an `FCDA` OFFERS, read
    off the templates. The restriction check is the comparison of the two, so
    they are one type rather than two that would have to be kept in step.

    ``btype`` is ``None`` when only a data object is named -- an `ExtRef` with
    no `pDA`, an `FCDA` with no `daName`.
    """

    cdc: str
    btype: Optional[str]


# -- ancestry ---------------------------------------------------------------

def _ancestor(doc, element, local_name):
    """The nearest ancestor of ``element`` with this local name, or ``None``.

    `ElementTree` has no parent pointers and no owner document, so this is
    where the reference's `closest()` and `ownerDocument` go: every function
    below that needs to know which IED an element is in takes ``doc``.
    """
    node = doc.parent_of(element)
    while node is not None:
        if strip_ns(node.tag) == local_name:
            return node
        node = doc.parent_of(node)
    return None


def _ied_name(doc, element) -> Optional[str]:
    ied = _ancestor(doc, element, "IED")
    return None if ied is None else ied.get("name")


def _logical_node_of(doc, element):
    """``(LDevice inst, prefix, lnClass, lnInst)`` for the LN holding
    ``element``, or ``None``.

    An `LN0` reports its class as ``LLN0`` whether or not the attribute is
    written, because that is the only class it can have and a control block's
    `srcLNClass` has to say it.
    """
    node = doc.parent_of(element)
    while node is not None:
        tag = strip_ns(node.tag)
        if tag in ("LN", "LN0"):
            ld = _ancestor(doc, node, "LDevice")
            return (
                "" if ld is None else (ld.get("inst") or ""),
                node.get("prefix") or "",
                node.get("lnClass") or ("LLN0" if tag == "LN0" else ""),
                node.get("inst") or "",
            )
        node = doc.parent_of(node)
    return None


def _same(left, right) -> bool:
    """Whether two SCL attribute values are the same.

    An absent attribute and an empty one are the same thing here: SCL omits
    `prefix` and `lnInst` rather than writing them empty, and a comparison
    that told the two apart would call an ExtRef unmatched against the FCDA it
    is bound to.
    """
    return (left or "") == (right or "")


# -- reading the restrictions -----------------------------------------------

def ext_ref_type_restrictions(ext_ref) -> Optional[TypeRestriction]:
    """The common data class and basic type this `ExtRef` will accept.

    ``None`` when no valid specification can be produced -- there is no `pDO`,
    or the namespace tables do not carry it, or they carry it and not the
    `pDA` named beside it.

    The class of a `pDO` comes from IEC 61850-7-4 and the basic type of a
    `pDA` from 61850-7-3; see :mod:`py61850.scl._nsd_types`. A `pDO` may be
    dotted, naming a sub-data object -- ``pDO="A.phsA"`` is a `CMV` -- and a
    `pDA` is dotted through a constructed attribute, as ``mag.f`` is.
    """
    p_do = ext_ref.get("pDO")
    if not p_do:
        return None
    cdc = DO_CDC.get(p_do)
    if not cdc:
        return None
    p_da = ext_ref.get("pDA")
    if not p_da:
        return TypeRestriction(cdc, None)
    btype = CDC_ATTRIBUTES.get(cdc, {}).get(p_da)
    if btype is None:
        return None
    return TypeRestriction(cdc, btype)


def fcda_type(doc, fcda):
    """``(cdc, bType)`` for what an `FCDA` actually points at, or ``None``.

    Resolved through the publisher's own `DataTypeTemplates`: the FCDA names a
    logical device, a logical node and a data object inside the IED it sits
    in, and the `LNodeType`/`DOType`/`DAType` chain says what that is.

    ``bType`` is ``None`` when the FCDA names a whole data object and no
    attribute, which is legal and common.
    """
    ied = _ancestor(doc, fcda, "IED")
    if ied is None:
        return None
    ln = _find_logical_node(ied, fcda.get("ldInst"), fcda.get("prefix"),
                            fcda.get("lnClass"), fcda.get("lnInst"))
    if ln is None:
        return None
    pool = doc.templates
    ln_type = pool.lnode_type(ln.get("lnType"))
    if ln_type is None:
        return None

    parts = [p for p in (fcda.get("doName") or "").split(".") if p]
    if not parts:
        return None
    do_type = pool.do_type(ln_type.objects.get(parts[0]))
    for name in parts[1:]:
        if do_type is None:
            return None
        do_type = pool.do_type(do_type.sub_objects.get(name))
    if do_type is None:
        return None

    da_parts = [p for p in (fcda.get("daName") or "").split(".") if p]
    if not da_parts:
        return TypeRestriction(do_type.cdc, None)
    spec = do_type.attributes.get(da_parts[0])
    for name in da_parts[1:]:
        if spec is None:
            return None
        spec = spec.sub_specs.get(name)
    if spec is None:
        return None
    return TypeRestriction(do_type.cdc, spec.btype)


def _find_logical_node(ied, ld_inst, prefix, ln_class, ln_inst):
    """The `LN`/`LN0` inside ``ied`` that an FCDA's four attributes name."""
    for access_point in children_local(ied, "AccessPoint"):
        for server in children_local(access_point, "Server"):
            for ldevice in children_local(server, "LDevice"):
                if not _same(ldevice.get("inst"), ld_inst):
                    continue
                for node in list(children_local(ldevice, "LN0")) + \
                        list(children_local(ldevice, "LN")):
                    is_ln0 = strip_ns(node.tag) == "LN0"
                    node_class = node.get("lnClass") or ("LLN0" if is_ln0 else "")
                    if (_same(node.get("prefix"), prefix)
                            and _same(node_class, ln_class)
                            and _same(node.get("inst"), ln_inst)):
                        return node
    return None


# -- the restriction check --------------------------------------------------

def fcda_meets_ext_ref_restrictions(doc, ext_ref, fcda, control_block_type=None,
                                    check_only_btype=False) -> bool:
    """Whether ``fcda`` satisfies what ``ext_ref`` declares it will accept.

    ``control_block_type`` is what `pServT` is compared against -- ``"GOOSE"``,
    ``"Report"``, ``"SMV"`` or ``"Poll"``. Leaving it out skips that check
    rather than failing it, because an FCDA on its own is not published by
    anything in particular.

    ``check_only_btype`` checks the service type and the basic type and leaves
    `pDO` and `pLN` alone -- for a caller binding an attribute whose class is
    deliberately not the one the input was specified against.

    **A restriction that cannot be resolved is not a restriction.** An `ExtRef`
    with no `pDO` constrains nothing, and so does one whose `pDO` the
    namespace tables do not carry, or whose FCDA does not resolve through the
    templates. The same is true of every `pDO` when the tables are absent.
    """
    return _clash(doc, ext_ref, fcda, control_block_type, check_only_btype) is None


def _clash(doc, ext_ref, fcda, control_block_type, check_only_btype):
    """The reason ``fcda`` fails ``ext_ref``'s restrictions, or ``None``.

    One string, naming what was expected and what was offered, in that order.
    It is what :func:`subscribe` refuses with: a caller told only that a
    binding was rejected has to guess which of four restrictions it was.
    """
    service = ext_ref.get("pServT")
    if service and control_block_type and service != control_block_type:
        return (f"ExtRef expects service type {service}; "
                f"the control block publishes {control_block_type}")

    if not check_only_btype:
        ln_class = ext_ref.get("pLN")
        if ln_class and not _same(ln_class, fcda.get("lnClass")):
            return (f"ExtRef expects logical node class {ln_class}; "
                    f"the FCDA is {fcda.get('lnClass') or '(none)'}")

    wanted = ext_ref_type_restrictions(ext_ref)
    if wanted is None:
        return None
    offered = fcda_type(doc, fcda)
    if offered is None:
        return None

    if not check_only_btype and wanted.cdc != offered.cdc:
        return _refusal(ext_ref, wanted, fcda, offered)
    if wanted.btype and offered.btype and wanted.btype != offered.btype:
        return _refusal(ext_ref, wanted, fcda, offered)
    return None


def _refusal(ext_ref, wanted, fcda, offered) -> str:
    """The two clashing types, expected first and offered second.

    Named rather than counted: a caller told only that a binding was rejected
    has to guess which of four restrictions it was, and an engineer reading it
    on a pane has no way to guess at all.
    """
    if wanted.btype and offered.btype:
        return (f"ExtRef expects {wanted.cdc}.{ext_ref.get('pDA')} "
                f"({wanted.btype}); "
                f"{offered.cdc}.{fcda.get('daName')} is {offered.btype}")
    return f"ExtRef expects {wanted.cdc}; the FCDA is {offered.cdc}"


# -- matching ---------------------------------------------------------------

def match_data_attributes(doc, ext_ref, fcda) -> bool:
    """Whether ``ext_ref`` is bound to exactly the attribute ``fcda`` publishes.

    The publisher's name is not on the FCDA -- it is the name of the IED the
    FCDA sits in, which is why this takes ``doc``.
    """
    if not _same(ext_ref.get("iedName"), _ied_name(doc, fcda)):
        return False
    for name in DATA_ATTRIBUTES[1:]:
        if not _same(ext_ref.get(name), fcda.get(name)):
            return False
    return True


def fcda_covers_ext_ref(doc, ext_ref, fcda) -> bool:
    """Whether ``fcda`` publishes what ``ext_ref`` takes -- wholly or in part.

    This is :func:`match_data_attributes` widened by exactly one rule: **an
    `FCDA` that names a data object and no attribute publishes every attribute
    of it**, so it covers an `ExtRef` bound to one of them. `Ind04` covers
    `Ind04.stVal`; the reverse is not true, and neither is `Ind04.q` covering
    `Ind04.stVal`.

    **It is a different question from `match_data_attributes`, which is why it
    is a different function.** "Is this ExtRef bound to exactly this member"
    decides what :func:`subscribe` WRITES, and widening it there would blank a
    correct `daName` with the whole-object FCDA's absent one. "Would removing
    this member break this ExtRef" decides what a removal has to repair, and
    the literal answer there leaves a subscription pointing at data that is
    gone. Q19 records that the two consequences do not want one answer.

    **The corpus is why the rule exists rather than being hypothetical.**
    Restricted to the dataset the subscribed control block actually publishes,
    `sel.scd` carries **4** subscriptions of this shape and the other two
    exports carry none: two `ASV4 GGIO1.Ind04.stVal` and two
    `PSV2 GGIO1.Ind13.stVal`, each bound while the published dataset carries
    the data object whole. Half of all dataset members in the corpus -- 3,417
    FCDAs of 6,967 -- are whole-object, so the structure is everywhere even
    though the live subscriptions into it are four.

    An empty `daName` and an absent one are the same thing here, as they are
    everywhere else in this package: a member that names no attribute
    publishes the object.

    `doName` is compared literally. An FCDA naming `Ind04` does not cover an
    ExtRef naming the sub-object `Ind04.subDo`, which is a deeper containment
    question than the corpus asks and than the reference's own declaration
    -- *"`FCDA` element with or without `daName`"* -- describes.

    **An FCDA naming no data object at all covers nothing.** `doName` is
    optional in the schema and 5 of `siemens.scd`'s 1,603 members leave it
    off -- the same 5 :func:`fcda_type` cannot resolve. Comparing an empty
    `doName` against an empty one would have such a member cover every ExtRef
    in its logical node that names no data object either.
    """
    if not fcda.get("doName"):
        return False
    if not _same(ext_ref.get("iedName"), _ied_name(doc, fcda)):
        return False
    for name in DATA_ATTRIBUTES[1:-1]:
        if not _same(ext_ref.get(name), fcda.get(name)):
            return False
    if not fcda.get("daName"):
        return True
    return _same(ext_ref.get("daName"), fcda.get("daName"))


def match_src_attributes(doc, ext_ref, control) -> bool:
    """Whether ``ext_ref``'s ``src*`` attributes name ``control``.

    Two of them have defaults from 61850-6 rather than being required: an
    absent `srcLNClass` means `LLN0`, which is where control blocks live, and
    an absent `srcLDInst` means the logical device the subscribed attribute is
    in. Every bound ExtRef in the reference corpus writes both, so the
    defaults are a safety net rather than the common path.
    """
    placement = _logical_node_of(doc, control)
    if placement is None:
        return False
    ld_inst, prefix, ln_class, ln_inst = placement
    return (_same(ext_ref.get("srcCBName"), control.get("name"))
            and _same(ext_ref.get("srcLDInst") or ext_ref.get("ldInst"), ld_inst)
            and _same(ext_ref.get("srcPrefix"), prefix)
            and _same(ext_ref.get("srcLNClass") or "LLN0", ln_class)
            and _same(ext_ref.get("srcLNInst"), ln_inst))


def source_control_block(doc, ext_ref):
    """The control block ``ext_ref`` subscribes to, or ``None``.

    Edition 2 only, through the `src*` attributes -- an Edition 1 subscription
    names no control block at all, so there is nothing to find.
    """
    name = ext_ref.get("srcCBName")
    if not name:
        return None
    publisher = ext_ref.get("iedName")
    if not publisher:
        return None
    for ied in children_local(doc.root, "IED"):
        if ied.get("name") != publisher:
            continue
        for access_point in children_local(ied, "AccessPoint"):
            for server in children_local(access_point, "Server"):
                for ldevice in children_local(server, "LDevice"):
                    for node in list(children_local(ldevice, "LN0")) + \
                            list(children_local(ldevice, "LN")):
                        for kind in SERVICE_TYPE:
                            for block in children_local(node, kind):
                                if (block.get("name") == name
                                        and match_src_attributes(doc, ext_ref, block)):
                                    return block
    return None


def is_subscribed(doc, fcda, scope) -> bool:
    """Whether anything inside ``scope`` already takes ``fcda``.

    ``scope`` is a subscriber `IED`, or any part of one -- an `AccessPoint`, a
    `Server`, an `LDevice`, a logical node. The question is asked of a source
    list: a dataset member already consumed here is one the engineer does not
    need to be offered again.

    **A whole-object member counts as taken when one of its attributes is
    bound**, through :func:`fcda_covers_ext_ref` rather than through the
    literal :func:`match_data_attributes`. The member IS consumed -- what
    arrives on the wire is the object, and the subscriber is reading a field
    of it -- and offering it again as unused would be wrong. Q19 records the
    decision and why the two questions take different answers.
    """
    for ext_ref in scope.iter():
        if strip_ns(ext_ref.tag) != "ExtRef":
            continue
        if fcda_covers_ext_ref(doc, ext_ref, fcda):
            return True
    return False


# -- subscribing ------------------------------------------------------------

def subscribe(doc, connections, force=False, ignore_supervision=True,
              check_only_btype=False) -> List:
    """The edit that binds each connection, as one compound edit.

    ``connections`` is one :class:`Connection` or a list of them. Build them
    together rather than concatenating separate calls -- see the module
    docstring.

    ``force`` skips the restriction checks. ``check_only_btype`` narrows them.
    ``ignore_supervision`` is here so the signature does not change when
    LGOS/LSVS supervision arrives: **supervision is not written today**, and
    passing ``False`` is refused rather than silently ignored.

    Raises :class:`EditRejected` if a connection fails its restrictions,
    naming what was expected and what was offered. Nothing is applied by this
    function, so nothing is left half-done either way.
    """
    if not ignore_supervision:
        raise EditRejected(
            "subscription supervision is not written yet; "
            "ignore_supervision=False has nothing to turn off")
    if isinstance(connections, Connection):
        connections = [connections]

    edits: List = []
    created_inputs = {}
    for connection in connections:
        edits.extend(_subscribe_one(doc, connection, force, check_only_btype,
                                    created_inputs))
    return edits


def _subscribe_one(doc, connection, force, check_only_btype, created_inputs):
    sink, fcda, block = connection.sink, connection.fcda, connection.control_block
    for role, element in (("sink", sink), ("fcda", fcda)):
        if not isinstance(element, ET.Element):
            raise EditRejected(
                f"{role} must be an Element, not {type(element).__name__}")

    kind = None if block is None else strip_ns(block.tag)
    if block is not None and kind not in SERVICE_TYPE:
        raise EditRejected(
            f"{kind} is not a control block; expected one of "
            f"{', '.join(sorted(SERVICE_TYPE))}")

    ext_ref = sink if strip_ns(sink.tag) == "ExtRef" else None
    if ext_ref is not None and not force:
        reason = _clash(doc, ext_ref, fcda,
                        None if kind is None else SERVICE_TYPE[kind],
                        check_only_btype)
        if reason:
            raise EditRejected(reason)

    binding = _binding(doc, fcda, block, kind)
    if ext_ref is not None:
        return [SetAttributes(ext_ref, binding)]

    inputs, edits = _inputs_of(doc, sink, created_inputs)
    node = ET.Element(_qualify(doc, "ExtRef"))
    node.attrib.update({k: v for k, v in binding.items() if v is not None})
    edits.append(Insert(inputs, node, reference_for(inputs, node.tag)))
    return edits


def _binding(doc, fcda, block, kind) -> dict:
    """The attributes a subscription writes, in 61850-6's own order.

    Every one of them is named, with ``None`` for those this connection does
    not set, so binding an ExtRef that was bound to something else clears what
    does not apply instead of leaving half of the previous source behind.
    """
    out = {name: None for name in BINDING_ATTRIBUTES}
    out["iedName"] = _ied_name(doc, fcda)
    for name in DATA_ATTRIBUTES[1:]:
        out[name] = fcda.get(name) or None
    if block is None:
        return out

    out["serviceType"] = SERVICE_TYPE[kind]
    out["srcCBName"] = block.get("name")
    placement = _logical_node_of(doc, block)
    if placement is not None:
        ld_inst, prefix, ln_class, ln_inst = placement
        out["srcLDInst"] = ld_inst or None
        out["srcPrefix"] = prefix or None
        # **`srcLNClass` is written even when it is `LLN0`**, which the schema
        # makes its default and which a reader may therefore assume. Omitting
        # it was tried and withdrawn: all 4,322 bound ExtRefs in the reference
        # corpus spell it out, from three independent vendor tools, and every
        # one of them spells it `LLN0`. Leaving it off would make a
        # re-subscribed ExtRef differ from the one the vendor tool wrote, for
        # a saving of sixteen characters.
        #
        # `srcPrefix` and `srcLNInst` go the other way, and the same corpus is
        # why: they appear only where they have a value -- 29 of sel.scd's 969
        # and 1 of mixed.scd's 2,928 -- so writing them empty would be the
        # invention instead.
        out["srcLNClass"] = ln_class or None
        out["srcLNInst"] = ln_inst or None
    return out


def _inputs_of(doc, sink, created_inputs):
    """``(Inputs element, edits that create it)`` for a logical-node sink."""
    tag = strip_ns(sink.tag)
    if tag == "Inputs":
        return sink, []
    if tag not in ("LN", "LN0"):
        raise EditRejected(
            f"sink must be an ExtRef, Inputs, LN or LN0, not {tag}")
    existing = next(children_local(sink, "Inputs"), None)
    if existing is not None:
        return existing, []
    # One `Inputs` per logical node per call, however many connections target
    # it. Two calls cannot see each other's, which is why they must be one.
    made = created_inputs.get(id(sink))
    if made is not None:
        return made, []
    node = ET.Element(_qualify(doc, "Inputs"))
    created_inputs[id(sink)] = node
    return node, [Insert(sink, node, reference_for(sink, node.tag))]


def _qualify(doc, local_name) -> str:
    """``local_name`` in the document's own namespace.

    A hand-written SCD that declares none is real, and this library reads
    them, so an element created for one carries no namespace either.
    """
    tag = doc.root.tag
    return f"{tag[:tag.index('}') + 1]}{local_name}" if tag.startswith("{") \
        else local_name


# -- unsubscribing ----------------------------------------------------------

def unsubscribe(doc, ext_refs, ignore_supervision=True) -> List:
    """The edit that unbinds each `ExtRef`, as one compound edit.

    An `ExtRef` with `intAddr` is blanked and kept: the internal address is
    the IED's own, and it goes on existing whether or not anything is
    connected to it. One without is removed, and an `Inputs` that its removal
    empties is removed too.

    ``ignore_supervision`` is here for the same reason as `subscribe`'s, and
    behaves the same way.
    """
    if not ignore_supervision:
        raise EditRejected(
            "subscription supervision is not written yet; "
            "ignore_supervision=False has nothing to turn off")
    if isinstance(ext_refs, ET.Element):
        ext_refs = [ext_refs]

    edits: List = []
    removed = {}
    for ext_ref in ext_refs:
        if not isinstance(ext_ref, ET.Element):
            raise EditRejected(
                f"ExtRef must be an Element, not {type(ext_ref).__name__}")
        if strip_ns(ext_ref.tag) != "ExtRef":
            raise EditRejected(f"not an ExtRef: {strip_ns(ext_ref.tag)}")
        if ext_ref.get("intAddr"):
            blanked = {name: None for name in BINDING_ATTRIBUTES
                       if name in ext_ref.attrib}
            if blanked:
                edits.append(SetAttributes(ext_ref, blanked))
            continue
        edits.append(Remove(ext_ref))
        parent = doc.parent_of(ext_ref)
        if parent is not None and strip_ns(parent.tag) == "Inputs":
            removed.setdefault(id(parent), [parent, []])[1].append(ext_ref)

    for parent, gone in removed.values():
        # The `Inputs` goes only when nothing at all is left in it. A `Private`
        # or a `Text` is content someone put there, and an element holding one
        # is not a leaf however many ExtRefs leave.
        if all(child in gone for child in parent):
            edits.append(Remove(parent))
    return edits
