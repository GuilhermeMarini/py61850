# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""Allocating a value nothing else has taken: MAC addresses, APPIDs, instance numbers and element names.

    from py61850.scl import (SclDocument, next_app_id, next_mac_address,
                             next_ln_inst, unique_element_name)

    doc = SclDocument.parse("station.scd")
    mac = next_mac_address(doc, "GSE")          # 01-0C-CD-01-00-01
    app_id = next_app_id(doc, "GSE")            # 0002
    name = unique_element_name(ln0, "DataSet")  # DataSet_1

Seven modules in this package stop short of writing a value in because
choosing one is a policy decision, and `track-A-edit-layer.md` made that
policy a phase of its own. This is that phase. **Nothing here applies
anything and nothing here builds an edit** -- each function answers "what is
free?" and hands the answer back.

## The batch record, which is the divergence that matters

The reference's three are **generator functions**: `macAddressGenerator(doc,
"GSE")` returns a closure, and its doc comment says why -- *"Defined once it
can generate unique MAC-address without the need to update the doc
in-between."* The hazard is real and this package has met it three times
(Q33 §9, Q34 §5, Q37 §3): **inside a compound edit the document is out of
date**, so a second allocation that re-reads the tree hands back the value the
first one just claimed.

**Ours solves it the way this package already solved it twice**, rather than
introducing a third mechanism. Every function here takes ``taken`` -- the
values the caller has already claimed and not yet applied -- and unions it
with what the document holds:

    claimed = set()
    for block in blocks:
        mac = next_mac_address(doc, "GSE", claimed)
        claimed.add(mac)

That is `data_types.py`'s ``taken = set(target_pool) | set(order)`` and
`supervision.py`'s ``batch`` parameter, spelled once and made public. It costs
the caller one line and buys three things a closure does not: the state is
inspectable, a caller can seed it with values from somewhere else entirely,
and a test can drive one allocation without constructing a generator.

**The names follow the shape.** A function returning a value is not a
generator, so `macAddressGenerator` becomes :func:`next_mac_address` -- Q23's
rule, and the same correction Q37 §2 made for `updateLnType`.

## The ranges are IEC 61850-8-1's, and the corpus is what confirms them

61850-6 constrains only the SHAPE. ``tP_MAC-Address`` is
``[0-9A-F]{2}`` six times hyphen-separated and ``tP_APPID`` is
``[0-9A-F]{4}`` -- **uppercase, and neither carries a range**. Which values
belong to GOOSE and which to Sampled Values is IEC 61850-8-1's, a document
this project does not hold and reads only through the reference's own doc
comments.

**So the corpus was asked instead, and it answers exactly:**

==================  =========================  ==============================
                    what the reference says    what the three exports do
==================  =========================  ==============================
GSE `MAC-Address`   ``01-0C-CD-01-``           **146 of 146**
SMV `MAC-Address`   ``01-0C-CD-04-``           **16 of 16**
GSE `APPID`         ``0x0000``-``0x3FFF``      ``0x0001``-``0x1933``, inside
SMV `APPID`         ``0x4000``-``0x7FFF``      ``0x4001``-``0x4922``, inside
GSE Type 1A         ``0x8000``-``0xBFFF``      **none**
==================  =========================  ==============================

Not one address in `sel.scd`, `mixed.scd` or `siemens.scd` falls outside the
prefix for its service or outside the APPID band for it. Three vendors, no
exceptions -- which is a stronger footing than A16b's 6-100 had, where only
IEC's own example files could referee.

**The MAC ceiling is the one thing NOT taken from the reference**, and the
corpus is why. The GOOSE block is usually published as
``01-0C-CD-01-00-00``-``01-0C-CD-01-01-FF``, 512 addresses. `mixed.scd`
reaches **``01-0C-CD-01-09-8E``** -- 2,446 -- so a ceiling at ``01-FF`` would
refuse to allocate into a file that three vendors have already written past.
The prefix is measured and kept; the last two octets are allowed their full
range, because the narrower bound cannot be cited and is contradicted by the
material. :data:`MAC_ADDRESS_PREFIXES` carries the half that is measured.

## `LN_INST_RANGE` moved here, and its provenance was wrong

The constant is A14's and shipped in `supervision.py` reading *"the range
61850-6 allows for `tLN@inst`"*. **61850-6 allows no such thing.** ``tLNInst``
is ``[0-9]{1,12}`` -- identical in `2007B4` and `2007C5` -- and ``value="99"``
appears nowhere in either schema set. The bound is the **reference's**
convention, which the same comment credited correctly in its second sentence.

The corpus disagrees with it in both directions and neither case is a
supervision one:

- **232 `LN@inst` are above 99**, all `LPDI` in `mixed.scd`, running to
  **167**;
- **152 are exactly ``0``**, across `CALH`, `LTIM`, `LTMS`, `LTRK`, `GAPC` and
  `LPHD` in two different exports;
- **`LGOS` and `LSVS` reach 31**, so ``(1, 99)`` has never actually bound an
  allocation this package made.

The bound is kept, because the reference documents it and nothing here has hit
it; **the attribution is corrected**, which is what Q35 §9 scheduled for this
phase. It lives here rather than in `supervision.py` because allocation is
this module's subject; `supervision.py` imports it and its behaviour is
unchanged.

## What "unique" is measured against, and it is NARROWER than A16's

`uniqueElementName(parent, tagName)` is one line upstream -- *"@returns Unique
element name with tagName within parent"* -- with no README entry and no
neighbour to fix its meaning, which is Q34 §8's situation without Q34 §8's way
out.

**The schema settles the scope.** Inside an `LN` or `LN0` the constraints are
per-tag: ``uniqueDataSetInLN0`` selects ``./scl:DataSet``,
``uniqueReportControlInLN0`` ``./scl:ReportControl``,
``uniqueGSEControlInLN0`` ``./scl:GSEControl``, and so on, in both editions.
So a `DataSet` may share its name with a `ReportControl` beside it. **That is
the opposite of A16's containers**, where ``uniqueChildNameInVoltageLevel``
selects ``./*`` and a `Bay` collides with a `PowerTransformer` of that name --
and the difference is written in the XSD rather than inferred, so this
function counts same-tag siblings and nothing else.

**The prefix is the reference's and the suffix is the corpus's.** Three of the
functions that call the reference's allocator document what it produces --
*"a unique name starting with `newReportControl_xx`"* -- and A17 missed it by
reading the allocator's own one-line declaration and the corpus instead. Q39
§0 carries the correction and the reason the corpus could not settle it: a
placeholder is renamed before a file ships, so **0 of its 1,309 names begin
with `new`** and that is what the convention predicts.

What the corpus does settle is the SUFFIX. Vendors already write a stem and
then a numeric one:

- `siemens.scd` carries a `DataSet` named literally ``DataSet``, then
  ``DataSet_1``, ``DataSet_2``, ``DataSet_3``;
- `mixed.scd` carries ``A_URCB``, ``A_URCB_1`` ... ``A_URCB_4``, and
  ``CTRL``/``CTRL_2``;
- **77 % of `DataSet` names, 90 % of `ReportControl`, 91 % of `GSEControl` and
  100 % of `SampledValueControl` end in a digit.**

Which is the rule `data_types.py`'s `_fresh_id` already applies to type ids,
conceded in Q34 §3 as "deliberately the simplest that terminates, to be
replaced here rather than competed with". It is not replaced: it is
**confirmed**, and this module publishes it so the two cannot drift.

## A hole is filled before the end is extended

Every allocator here returns the **lowest** free value, not the next one after
the highest. `supervision.py` measured why on real material: corpus `inst`
values are not dense, and **26 of the 59 (IED, class) blocks have a hole in
them**. Appending would leave those holes forever and walk an `LDevice` past
99 that has eleven logical nodes in it.

## What a refusal means, and where it diverges

The reference's three generators return ``string | null`` and say nothing
about what a caller should do with the null. **Ours raises
:class:`~py61850.scl.EditRejected` when the range is exhausted**, because that
is what this package does everywhere else an allocation cannot be made --
`supervision.py` already raises on an exhausted `LN_INST_RANGE`, and a caller
that quietly writes ``None`` into a required attribute produces a document
that fails at the vendor tool. :func:`unique_element_name` cannot exhaust: its
suffix is unbounded.
"""

from xml.etree import ElementTree as ET

from .document import strip_ns
from .edit import EditRejected

#: The two service types every allocator here is parameterised by. `GSE` is a
#: `GSEControl`'s address and `SMV` a `SampledValueControl`'s, which is the
#: reference's own spelling and `address.py`'s.
SERVICE_TYPES = ("GSE", "SMV")

#: ``{service type: the four octets IEC 61850-8-1 reserves}``. **Measured, not
#: inferred**: 146 of 146 GSE addresses and 16 of 16 SMV addresses in the three
#: reference exports carry these and nothing else. The last two octets are
#: allocated over their whole range -- the module docstring has the corpus
#: address that rules out the narrower 512-entry reading.
MAC_ADDRESS_PREFIXES = {"GSE": "01-0C-CD-01", "SMV": "01-0C-CD-04"}

#: ``{(service type, type 1A): (low, high)}`` for `APPID`, from IEC 61850-8-1
#: through the reference's doc comment, corroborated by every one of the 113
#: APPIDs the corpus carries. **A Type 1A SMV band does not exist**: 61850-8-1
#: gives the Trip GOOSE its own range and says nothing of a sampled-value
#: equivalent, so the key is absent rather than mapped to the ordinary band.
APP_ID_RANGES = {
    ("GSE", False): (0x0000, 0x3FFF),
    ("GSE", True): (0x8000, 0xBFFF),
    ("SMV", False): (0x4000, 0x7FFF),
}

#: The range an allocated logical-node instance number is drawn from.
#:
#: **This is the REFERENCE's convention and not the schema's**, which is the
#: correction Q35 §9 scheduled for this phase: ``tLNInst`` is
#: ``[0-9]{1,12}`` in both editions and ``value="99"`` is in neither. The
#: corpus exceeds it 232 times -- all `LPDI` in `mixed.scd`, up to 167 -- and
#: writes ``0`` 152 times, while `LGOS` and `LSVS` reach only 31. Kept because
#: the reference documents it and nothing here has met the bound.
LN_INST_RANGE = (1, 99)

#: ``{element: the attribute holding its instance number}``. `LN` spells it
#: `inst` and `LNode` spells it `lnInst`; both are typed ``tLNInst``, and the
#: pair is why the reference's own generator is documented as returning
#: *"`inst` or `lnInst`"*. `LN0` is absent because it has neither -- there is
#: exactly one per `LDevice` and it is identified by its class.
LN_INST_ELEMENTS = {"LN": "inst", "LNode": "lnInst"}

#: What :func:`unique_element_name` puts in front of the tag. **The
#: reference's**, documented on the three functions that call its allocator
#: rather than on the allocator itself: *"When missing a unique name starting
#: with `newReportControl_xx` is set"*. A placeholder that announces itself as
#: one is the whole point, and the corpus cannot referee it -- 0 of its 1,309
#: control-block and dataset names begin with this, which is what the
#: convention predicts rather than evidence against it.
ALLOCATED_NAME_PREFIX = "new"

#: The first numeric suffix an allocated name or id takes. Measured rather
#: than chosen: `siemens.scd` writes ``DataSet``, ``DataSet_1``, ``DataSet_2``,
#: ``DataSet_3`` and `mixed.scd` ``A_URCB`` through ``A_URCB_4``.
#: `data_types.py`'s `_fresh_id` shares it, which is what stops the two
#: allocators drifting apart on the half they agree about.
ALLOCATED_NAME_SUFFIX_START = 1

_MAC_OCTET_RANGE = (0x0000, 0xFFFF)


# -- reading what is already there ------------------------------------------

def _namespace(tag) -> str:
    if not isinstance(tag, str) or not tag.startswith("{"):
        return ""
    return tag[:tag.index("}") + 1]


def _root_of(document) -> ET.Element:
    """The root element of an :class:`~py61850.scl.SclDocument`.

    Spelled out rather than duck-typed, as `data_types.py` does, so passing an
    `Element` where a document belongs says so instead of surfacing as an
    allocator that thinks everything is free.
    """
    root = getattr(document, "root", None)
    if not isinstance(root, ET.Element):
        raise EditRejected(
            f"expected an SclDocument, not {type(document).__name__}; a MAC "
            f"address and an APPID are unique across the whole station, so "
            f"what is free can only be answered by the document")
    return root


def _p_values(root, p_type) -> list[str]:
    """Every `P` of this type in the document, namespace-exact.

    **Namespace-exact and document-wide.** A MAC address is unique across the
    station rather than within a `SubNetwork`, so the scan cannot be narrowed;
    and A8, A10, A12 and A13 each met a vendor element wearing a standard
    local name, so a `P` in somebody else's namespace is somebody else's.

    The `type` is compared case-insensitively, which is `address.py`'s own
    habit: ``tPTypeEnum`` fixes the spelling but a hand-edited file is real.
    """
    wanted = _namespace(root.tag) + "P"
    target = p_type.upper()
    return [(element.text or "").strip() for element in root.iter(wanted)
            if (element.get("type") or "").upper() == target]


def _normalised(values) -> set:
    return {value.strip().upper() for value in values if value and value.strip()}


def _children(parent, local_name) -> list[ET.Element]:
    """Direct children of this local name, in the parent's own namespace."""
    wanted = _namespace(parent.tag) + local_name
    return [child for child in parent if child.tag == wanted]


# -- MAC addresses ----------------------------------------------------------

def _service(service_type) -> str:
    if service_type not in SERVICE_TYPES:
        raise EditRejected(
            f"service_type must be one of {', '.join(SERVICE_TYPES)}, not "
            f"{service_type!r}; GSE is a GSEControl's address and SMV a "
            f"SampledValueControl's")
    return service_type


def next_mac_address(doc, service_type, taken=()) -> str:
    """The lowest multicast MAC address free in ``doc`` for this service.

    ``service_type`` is ``"GSE"`` or ``"SMV"`` and picks the four-octet prefix
    IEC 61850-8-1 reserves -- :data:`MAC_ADDRESS_PREFIXES`, which is measured
    against the corpus rather than inferred. The last two octets are allocated
    over their whole range, for the reason the module docstring gives: the
    narrower 512-address reading is contradicted by `mixed.scd`.

    ``taken`` is what the caller has already claimed and not yet applied. It
    is the whole of this module's divergence from the reference, which returns
    a closure instead; the module docstring argues it. Values are compared
    case-insensitively and whitespace-trimmed, so a `taken` entry does not
    have to be spelled exactly as the file would spell it.

    Returns the address in the schema's own spelling -- uppercase hex, six
    octets, hyphen-separated, which is what ``tP_MAC-Address`` requires.

    **A hole is filled before the end is extended.** The lowest free value is
    returned, not the one after the highest.

    Raises :class:`~py61850.scl.EditRejected` for an unknown ``service_type``,
    for something that is not an `SclDocument`, and when every address in the
    range is in use.
    """
    prefix = MAC_ADDRESS_PREFIXES[_service(service_type)]
    used = _normalised(_p_values(_root_of(doc), "MAC-Address")) | _normalised(taken)
    low, high = _MAC_OCTET_RANGE
    for value in range(low, high + 1):
        candidate = f"{prefix}-{value >> 8:02X}-{value & 0xFF:02X}"
        if candidate not in used:
            return candidate
    raise EditRejected(
        f"every {service_type} MAC address in {prefix}-00-00 to "
        f"{prefix}-FF-FF is in use; {len(used)} are taken")


# -- APPIDs -----------------------------------------------------------------

def next_app_id(doc, service_type, taken=(), type1a=False) -> str:
    """The lowest `APPID` free in ``doc`` for this service.

    ``type1a`` selects the Trip GOOSE band, which IEC 61850-8-1 separates from
    the ordinary one -- :data:`APP_ID_RANGES`. **It applies to `GSE` only**;
    there is no Type 1A sampled-value band, and asking for one is refused
    rather than quietly answered from the ordinary range. No corpus file uses
    the Type 1A band at all, so it is the one row of that table with no
    measurement behind it.

    ``taken`` is the batch record, as :func:`next_mac_address` describes.

    Returns four uppercase hex digits, which is what ``tP_APPID`` requires.
    **A value the file wrote in lowercase, or one that is not hex at all, is
    still read**: the comparison upper-cases, and a non-hex `APPID` is counted
    as occupying nothing because it names no number. The schema permits the
    second only in a file that was not validated, which is a file this package
    meets.

    Raises :class:`~py61850.scl.EditRejected` for an unknown ``service_type``,
    for ``type1a`` on `SMV`, and when the band is full.
    """
    service = _service(service_type)
    key = (service, bool(type1a))
    if key not in APP_ID_RANGES:
        raise EditRejected(
            f"there is no Type 1A band for {service}; 61850-8-1 gives the "
            f"Trip GOOSE its own APPID range and defines no sampled-value "
            f"equivalent")
    low, high = APP_ID_RANGES[key]

    used = set()
    for value in _normalised(_p_values(_root_of(doc), "APPID")) | _normalised(taken):
        try:
            used.add(int(value, 16))
        except ValueError:
            # Not hex, so it names no number and blocks none. `tP_APPID` is
            # `[0-9A-F]{4}`, but `address.py`'s docstring records that the
            # pattern only binds when the file declares `xsi:type`, so
            # `<P type="APPID">not hex</P>` is schema-valid without it.
            continue
    for value in range(low, high + 1):
        if value not in used:
            return f"{value:04X}"
    raise EditRejected(
        f"every {service} APPID in {low:#06x}..{high:#06x} is in use")


# -- instance numbers -------------------------------------------------------

def next_ln_inst(parent, tag, ln_class, taken=()) -> str:
    """The lowest instance number free for ``ln_class`` inside ``parent``.

    ``tag`` is ``"LN"`` or ``"LNode"`` and decides which attribute is read --
    :data:`LN_INST_ELEMENTS`, since `LN` spells it `inst` and `LNode` spells
    it `lnInst`. ``parent`` is the `LDevice` for the first and whatever
    Substation-section element holds the `LNode` for the second; **direct
    children only**, because that is what the schema's
    ``uniqueLNInLDevice`` counts.

    **Scanned by class alone, not by prefix.** `tLN`'s identity is
    `prefix` + `lnClass` + `inst`, so a prefix-aware allocator would find more
    free numbers -- and SEL writes a prefix naming the SUPERVISED device,
    a vendor convention this package takes no view on. Scanning by class alone
    cannot collide. This is `supervision.py`'s provisional rule, adopted
    unchanged rather than re-argued.

    The range is :data:`LN_INST_RANGE`, whose bound is the reference's
    convention and **not** the schema's; the module docstring has the
    correction and the 232 corpus instances that exceed it.

    Raises :class:`~py61850.scl.EditRejected` for an unknown ``tag``, for a
    ``parent`` that is not an element, and when the range is exhausted.
    """
    if tag not in LN_INST_ELEMENTS:
        raise EditRejected(
            f"tag must be one of {', '.join(sorted(LN_INST_ELEMENTS))}, not "
            f"{tag!r}; LN0 carries neither inst nor lnInst, being one per "
            f"LDevice and identified by its class")
    if not isinstance(parent, ET.Element):
        raise EditRejected(
            f"next_ln_inst counts the children of an element, not "
            f"{type(parent).__name__}")
    attribute = LN_INST_ELEMENTS[tag]
    used = {child.get(attribute) for child in _children(parent, tag)
            if child.get("lnClass") == ln_class}
    used |= {str(value) for value in taken}
    low, high = LN_INST_RANGE
    for value in range(low, high + 1):
        if str(value) not in used:
            return str(value)
    raise EditRejected(
        f"every {ln_class} instance from {low} to {high} is in use in this "
        f"{strip_ns(parent.tag)}; tLN is identified by prefix, lnClass and "
        f"inst")


# -- element names ----------------------------------------------------------

def _allocated_name(parent, tag, name, taken=()):
    """``name`` if the caller gave one, an allocated one if it gave ``None``.

    The rule the three `create_*` functions share, spelled once here rather
    than three times with three chances to drift -- Q25's rule, and the same
    reason `supervision.py` reaches across for `data_set`'s `_limit`.

    **``None`` and ``""`` are deliberately different.** ``None`` is "choose one
    for me"; an empty string is a caller that computed a name and got nothing,
    which is a bug in the caller and is surfaced rather than papered over. The
    refusal the three functions carried before A17b is therefore still
    reachable and still means something, which is why it was rewritten rather
    than deleted.
    """
    if name is None:
        return unique_element_name(parent, tag, taken)
    if not name:
        raise EditRejected(
            f"a {tag} needs a name -- it is required by the schema and unique "
            f"within its logical node. Pass name=None to have one allocated; "
            f"an empty name is a caller that computed one and got nothing")
    return name


def unique_element_name(parent, tag, taken=()) -> str:
    """A `name` no ``tag`` child of ``parent`` carries.

    The stem is :data:`ALLOCATED_NAME_PREFIX` + the tag and the suffix is
    ``_1``, ``_2`` and so on, so a `DataSet` allocated here is ``newDataSet``
    and then ``newDataSet_1``. **The prefix is the reference's**, documented
    on the three functions that call its allocator rather than on the
    allocator itself -- *"When missing a unique name starting with
    `newReportControl_xx` is set"*, and the same sentence for
    `newSampledValueControl_xx` and `newGSEControl_xx`.

    **A17 shipped this without the prefix and Q39 §0 has the correction.** It
    read `uniqueElementName.d.ts`, which is one line and carries no pattern,
    and took the shape from the corpus instead: `siemens.scd` really does
    carry a `DataSet` named ``DataSet`` followed by ``DataSet_1``,
    ``DataSet_2`` and ``DataSet_3``, and 77-100 % of the corpus's control
    block and dataset names end in a digit.

    **But the corpus cannot referee this particular question**, which is the
    part worth keeping. Of its 1,309 such names, **zero** begin with ``new``
    -- and that is exactly what the convention predicts, because a placeholder
    is renamed before a file ships. The corpus measures finished
    configurations; an allocator produces something an engineer is meant to
    replace, and a name that announces itself as auto-created is the point.
    ``ReportControl_1`` is indistinguishable from a deliberate name.

    **Same-tag siblings only**, which is narrower than it looks and is the
    schema's: ``uniqueDataSetInLN0`` selects ``./scl:DataSet`` and
    ``uniqueReportControlInLN0`` ``./scl:ReportControl``, so a `DataSet` may
    share a name with a `ReportControl` beside it. A16's containers are the
    other way -- ``uniqueChildNameInVoltageLevel`` selects ``./*`` -- and this
    module does not generalise across the two.

    ``taken`` is the batch record. ``stem`` is not a parameter because the
    reference has none and the corpus needs none; a caller wanting a different
    stem is choosing a name rather than asking for a free one.

    **This is the one allocator here that cannot be exhausted**, the suffix
    being unbounded, so it raises only for a ``parent`` that is not an
    element.
    """
    if not isinstance(parent, ET.Element):
        raise EditRejected(
            f"unique_element_name counts the children of an element, not "
            f"{type(parent).__name__}")
    used = {child.get("name") for child in _children(parent, tag)}
    used |= {str(value) for value in taken}
    stem = ALLOCATED_NAME_PREFIX + tag
    if stem not in used:
        return stem
    suffix = ALLOCATED_NAME_SUFFIX_START
    while f"{stem}_{suffix}" in used:
        suffix += 1
    return f"{stem}_{suffix}"
