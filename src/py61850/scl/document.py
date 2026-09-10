# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""One SCL file, parsed once, and the shallow facts that need no instance model.

``.scd``, ``.icd``, ``.cid``, ``.iid`` and ``.ssd`` are all SCL and all read
here; they differ in how much of the schema they fill in, not in grammar.

Two constructors, because the callers genuinely differ:

- :meth:`SclDocument.load` is the graceful one, for a file a USER supplied.
  A missing file, invalid XML or a DTD gives ``None`` and a log line, never an
  exception -- which is what lets a web tool hand it whatever was uploaded.
- :meth:`SclDocument.parse` is the strict one, for a file the PROJECT ships.
  It raises, because a file that will not parse is a reason to stop rather
  than to carry on with a partial answer.

Traversal is by LOCAL NAME throughout. Some hand-made SCDs declare no
namespace at all, real station files declare several (the reference corpus has
the SEL namespace in all three files, including the Siemens station's, and
Ed2.1's ``6-100`` namespace in one), and matching on a fully-qualified tag
would silently return nothing for either.
"""

from __future__ import annotations

import contextlib
import io
import logging
import re
import threading
from pathlib import Path
from xml.etree import ElementTree as ET

from ._xmlsafe import DtdNotAllowed, reject_dtd_in_file

_logger = logging.getLogger(__name__)

# `xmlns="uri"` and `xmlns:pfx="uri"`, with the prefix CAPTURED. Recovering
# the prefix and not only the URI is what A3 needed: `ElementTree` discards
# prefix declarations when it builds the tree, so a document serialised back
# out comes back as `ns0:` unless the prefix is registered from somewhere,
# and this is the only place that still knows what it was.
_NS_DECL = re.compile(
    br"""xmlns(?::([A-Za-z0-9_.-]+))?\s*=\s*["']([^"']+)["']""")


def strip_ns(tag) -> str:
    """``{ns}LocalName`` -> ``LocalName``; ``""`` for a node that is not an element.

    **A tag that is not a string is not an error here.** The parser keeps
    comments -- see :meth:`SclDocument.parse` -- so every walk over an
    element's children now meets nodes whose ``tag`` is the FACTORY that made
    them rather than a name: ``ET.Comment`` for a comment,
    ``ET.ProcessingInstruction`` for a processing instruction. Both are
    callables, and ``tag.rsplit`` on one raises ``AttributeError``.

    The guard lives here rather than at the call sites because every traversal
    in this package funnels through this one function -- :func:`iter_local`,
    :func:`children_local`, :func:`privates_of` and the two instance walks in
    ``model.py`` -- as does every vendor library, which reaches the tree
    through those helpers and never by raw iteration. One guard covers all of
    them, and a traversal added later inherits it instead of reintroducing the
    bug.

    ``""`` rather than ``None``: it is falsy, it can never equal a local name,
    so a comment is simply skipped by every ``== local_name`` test above, and
    the return type stays one thing for every caller.
    """
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def iter_local(root: ET.Element, local_name: str):
    """Every DESCENDANT whose local name is ``local_name``, namespace ignored.

    Includes ``root`` itself when it matches, which is what makes
    ``iter_local(ied_el, "LDevice")`` read naturally.

    Comments and processing instructions are never returned: they have no
    local name, and :func:`strip_ns` reports that as ``""``.
    """
    if not local_name:
        # An element's local name is never empty, so an empty argument can
        # match no element -- and without this it would match every COMMENT,
        # whose `strip_ns` is exactly `""`. Returning nothing keeps the
        # docstring above literally true for every argument.
        return
    for el in root.iter():
        if strip_ns(el.tag) == local_name:
            yield el


def children_local(node: ET.Element, local_name: str):
    """Every DIRECT CHILD whose local name is ``local_name``.

    The distinction from :func:`iter_local` matters more than it looks: a
    ``DataSet`` under one LN and a ``DataSet`` under the next are both
    descendants of the LDevice, so collecting by descent attributes each of
    them to both.
    """
    if not local_name:
        return          # see `iter_local`
    for el in node:
        if strip_ns(el.tag) == local_name:
            yield el


def privates_of(node: ET.Element) -> dict:
    """``{type: [Private element, ...]}`` for this node's DIRECT children.

    Direct children only, and deliberately: a ``Private`` under a nested
    element belongs to that element. Collecting by descent would hand every
    IED the whole station's privates.

    This is the vendor extension seam. A vendor library reads its own
    ``Private`` types off model nodes instead of walking the XML again --
    Siemens puts 5,886 ``Siemens-MasterId`` elements in one reference station
    this way.
    """
    out: dict = {}
    for el in node:
        if strip_ns(el.tag) != "Private":
            continue
        out.setdefault(el.get("type") or "", []).append(el)
    return out


class Header:
    """The ``<Header>`` element: what tool wrote this file, and to what edition."""

    __slots__ = ("id", "version", "revision", "tool_id", "name_structure")

    def __init__(self, el: ET.Element) -> None:
        self.id = el.get("id")
        self.version = el.get("version")
        self.revision = el.get("revision")
        self.tool_id = el.get("toolID")
        self.name_structure = el.get("nameStructure")

    def __repr__(self) -> str:
        return f"<Header id={self.id!r} version={self.version!r}>"


class SclDocument:
    """One SCL file. Cheap to construct; the expensive parts are lazy."""

    __slots__ = ("root", "path", "_declarations", "_header", "_cache")

    def __init__(self, root: ET.Element, path=None, declarations=()) -> None:
        self.root = root
        self.path = path
        # `(prefix, uri)` pairs as the file wrote them -- see
        # `_declared_namespaces`. Kept whole rather than reduced to URIs
        # because the prefix is half of what `_to_bytes` has to put back.
        self._declarations = tuple(declarations)
        self._header = False        # sentinel: not looked up yet
        self._cache: dict = {}

    # -- constructors -------------------------------------------------------

    @classmethod
    def parse(cls, path) -> "SclDocument":
        """The document, raising on any failure.

        ``OSError`` for a file that cannot be read, ``ET.ParseError`` for XML
        that will not parse, and :class:`DtdNotAllowed` for one declaring a
        DTD.

        **Comments are kept.** ``ElementTree``'s default parser discards them,
        and this file goes back to DIGSI and SEL Architect: an engineer's note
        beside a setting is content, and dropping it turns every save into a
        diff on lines nobody touched. They become ordinary children with a
        non-string ``tag``, which is what :func:`strip_ns` guards.

        The exception is a comment in the PROLOG or the EPILOG -- before
        ``<SCL>`` or after ``</SCL>``. ``ElementTree`` has no document node,
        only a root element, so such a comment has nowhere in the tree to
        attach and is dropped by the parser. A browser's ``DOMParser`` keeps
        it, because its ``Document`` can hold children beside the root; that
        is a structural difference, not something a parser flag closes. No
        file in the reference corpus has one.
        """
        p = Path(path)
        # Before the parser, never after: a DTD's entities expand DURING the
        # parse and there is no half-way to stop at.
        reject_dtd_in_file(p)
        # A parser instance holds the expat state for ONE document and cannot
        # be reused, so it is built per call rather than shared at module
        # level. `insert_comments` is the only target flag set: processing
        # instructions stay dropped, as no SCL file seen here carries one and
        # `strip_ns` tolerates the node either way if that changes.
        parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
        return cls(ET.parse(str(p), parser).getroot(), p, _declared_namespaces(p))

    @classmethod
    def load(cls, path):
        """The document, or ``None`` with a log line on any failure."""
        p = Path(path)
        if not p.is_file():
            _logger.warning("SCL file not found: %s", p)
            return None
        try:
            return cls.parse(p)
        except DtdNotAllowed as e:
            _logger.warning("SCL file refused %s: %s", p, e)
        except (OSError, ET.ParseError) as e:
            _logger.warning("error reading SCL file %s: %s", p, e)
        return None

    # -- serialisation ------------------------------------------------------

    def _to_bytes(self) -> bytes:
        """The document as bytes -- **private, until the guarantee is true**.

        This is the seam the round-trip test measures. **Three of the six
        things that have to survive now do:** comments, because :meth:`parse`
        keeps them in the tree; the document's own namespace PREFIXES; and
        every ``xmlns`` declaration the root carried, **including the ones
        nothing uses**. :func:`_document_prefixes` is the last two of those.

        **The other three are the writer's**, and
        ``tests/unit/test_scl_roundtrip.py`` names each against every fixture:

        1. the line ending is ``ElementTree``'s LF, not the file's own;
        2. the XML declaration is written with single quotes and a
           lower-cased encoding name;
        3. the ROOT's attribute order, which shows up in nothing but the byte
           compare. ``ElementTree`` emits the declarations it generates ahead
           of every ordinary attribute, and the unused ones this re-emits
           land after them all; a real file interleaves the two. No other
           element is affected -- attribute order is preserved everywhere
           ``ElementTree`` is not also emitting a declaration.

        It is private because a name a consumer can reach is a promise, and
        that promise is not true yet. It exists now so that the work which
        makes it true has one call site to improve rather than a call site to
        move. When the list is empty this becomes the public write API, with
        the guarantee and its cosmetic exceptions written into its docstring.
        """
        buf = io.BytesIO()
        with _document_prefixes(self.root, self._declarations):
            ET.ElementTree(self.root).write(
                buf, encoding="utf-8", xml_declaration=True)
        return buf.getvalue()

    # -- shallow facts ------------------------------------------------------

    @property
    def namespaces(self) -> tuple:
        """Every namespace URI the document declares, standard and vendor.

        URIs only, in first-seen order and de-duplicated, which is what a
        consumer asking "does this file speak Siedig?" wants. The prefixes
        each was declared under are :attr:`_declarations`, and they are
        private: nothing outside serialisation has needed one yet, and a name
        a consumer can reach is a promise.
        """
        uris = []
        for _, uri in self._declarations:
            if uri not in uris:
                uris.append(uri)
        return tuple(uris)

    @property
    def header(self):
        """The ``<Header>``, or ``None`` when the file carries none."""
        if self._header is False:
            # DIRECT CHILD of the root only -- see `_ied_elements`'s docstring
            # for why descendant matching is unsafe here: a vendor `Private`
            # block is free to nest an element with this same local name.
            el = next(children_local(self.root, "Header"), None)
            self._header = Header(el) if el is not None else None
        return self._header

    @property
    def edition(self):
        """``"2007B"``, ``"2003"``, ... or ``None`` when the root carries no
        ``version``.

        Read from ``version``/``revision`` on the ``<SCL>`` root, not from
        ``<Header>``. Two things might look like a source for the edition and
        neither is: the namespace URI, because it has not changed since
        Edition 1, and ``Header@version``/``@revision``, because those are
        the exporting TOOL's own bookkeeping, not the schema edition -- on
        the three reference files this reads (SEL, Siemens, a mixed-vendor
        station) they hold values like ``"204"``/``"1.0"`` and
        ``"1"``/``"199"``, unrelated to the ``2007``/``B`` every one of the
        three actually validates against. The standard puts the edition on
        the root element itself: 61850-6 defines ``version``, ``revision``
        and ``release`` there, and all three reference files carry
        ``version="2007" revision="B"``. See :attr:`release` for the third
        of those three, which distinguishes Edition 2 from Edition 2.1 and
        which this short form drops.
        """
        version = self.root.get("version")
        if not version:
            return None
        return f"{version}{self.root.get('revision') or ''}"

    @property
    def release(self):
        """The root's ``release`` attribute, or ``None``.

        This is what separates Edition 2 (``2007/B/3``) from Edition 2.1
        (``2007/B/4``) -- :attr:`edition` alone reads ``"2007B"`` for both.
        Kept as a separate property rather than folded into ``edition``'s
        string so a consumer that only cares about the pre-2.1 distinction
        is not forced to parse one back apart.
        """
        return self.root.get("release")

    @property
    def privates(self) -> dict:
        """Document-level ``Private`` elements, ``{type: [element, ...]}``."""
        return privates_of(self.root)

    @property
    def templates(self):
        """The document's :class:`~py61850.scl.templates.TemplatePool`.

        Built on first access and cached. Templates are station-wide while
        instances are per IED, which is why this hangs off the document and
        the instance trees do not.
        """
        pool = self._cache.get("templates")
        if pool is None:
            from .templates import TemplatePool
            pool = self._cache["templates"] = TemplatePool(self.root)
        return pool

    @property
    def communication(self):
        """The document's :class:`~py61850.scl.communication.Communication`.

        Built on first access and cached. It needs no type pool and no
        instance tree: the section names IEDs but contains none of them.
        """
        comm = self._cache.get("communication")
        if comm is None:
            from .communication import Communication
            comm = self._cache["communication"] = Communication(self.root)
        return comm

    def _ied_elements(self) -> dict:
        """``{name: <IED> element}``, in document order.

        An IED with no name is skipped: nothing can reference it -- not a
        ConnectedAP, not an ExtRef -- and keying it on ``""`` would collide
        with the next unnamed one.

        DIRECT CHILDREN of the root only, deliberately -- not
        :func:`iter_local`'s descendant search. Per 61850-6, ``<IED>`` is
        valid only there; a vendor's ``Private`` block is free to reuse the
        same local name for something that is not a device at all. DIGSI
        nests a bare ``<IED uuidRef=... name=...>`` cross-reference inside
        ``<Private><FolderDetails><FolderInfo>`` for project-tree bookkeeping,
        one per real IED, ahead of the real element in document order. A
        descendant scan matched that decoy first and shadowed the real IED
        for every one of a reference station's 14 devices: every identifying
        field came back ``None`` and the instance tree came back with no
        LDevices at all.
        """
        els = self._cache.get("ied_elements")
        if els is None:
            els = self._cache["ied_elements"] = {}
            for el in children_local(self.root, "IED"):
                name = el.get("name")
                if name and name not in els:
                    els[name] = el
        return els

    @property
    def ied_names(self) -> tuple:
        """Every IED name, in document order."""
        return tuple(self._ied_elements())

    @property
    def ied_headers(self) -> dict:
        """``{name: IedHeader}`` -- identifying fields only, no instance tree.

        This is the station-wide view: listing devices, cross-matching an
        inventory, resolving an IP. It builds no logical nodes and touches no
        type pool.
        """
        headers = self._cache.get("ied_headers")
        if headers is None:
            from .model import IedHeader
            headers = self._cache["ied_headers"] = {
                name: IedHeader(el) for name, el in self._ied_elements().items()}
        return headers

    def ied(self, name):
        """The fully resolved :class:`~py61850.scl.model.Ied`, or ``None``.

        Built on first request and cached. Per IED because that is the unit a
        consumer works in, and because one reference SCD carries 178,406 DAI
        elements across 30 IEDs -- materialising a station to serve one relay
        spends the whole budget.
        """
        key = "ied:" + str(name)
        if key in self._cache:
            return self._cache[key]
        el = self._ied_elements().get(name)
        if el is None:
            return None
        from .model import Ied
        self._cache[key] = Ied(el, self)
        return self._cache[key]

    def __repr__(self) -> str:
        return f"<SclDocument path={self.path!r} edition={self.edition!r}>"


# `register_namespace` writes into a dict shared by every document in the
# process, so a serialisation that registers prefixes is a critical section
# whether or not the caller thinks it is threading. `pac-ct` serves SCL tools
# from a threaded HTTP server, which is precisely a caller that does not think
# so. Reentrant because the whole of `_document_prefixes` runs inside it and
# nothing there re-enters -- but a lock a future edit can re-enter costs
# nothing and deadlocks nobody.
_NS_LOCK = threading.RLock()

# `ElementTree`'s prefix registry. There is no public reader for it -- only
# `register_namespace`, which writes -- so restoring it afterwards means
# naming it. Resolved once, defensively: if a future CPython renames it, every
# call below falls back to registering without restoring, which is what the
# stdlib does anyway. Losing the containment is a worse outcome than the
# prefixes coming back as `ns0:`, so a `_NS_MAP` of `None` is logged where it
# matters rather than passed over in silence.
_NS_MAP = getattr(ET, "_namespace_map", None)

# The prefix format `ElementTree` invents for itself, and the one
# `register_namespace` refuses. See `_document_prefixes`.
_RESERVED_PREFIX = re.compile(r"ns\d+$")


def _used_namespace_uris(root: ET.Element) -> set:
    """Every namespace URI that a tag or an attribute NAME below ``root`` is in.

    This is exactly the set `ElementTree` will emit a declaration for, so its
    complement within the root's declarations is the set that would be lost --
    SEL's private namespace, DIGSI's `IEC_60870_5_104` and `siebase`, and the
    `xmlns:sel` in the hand-written fixture are all in that complement.

    Attribute VALUES are not scanned, and that is a real limitation rather
    than an oversight: a QName in a value (`xsi:type="foo:Bar"`) uses a prefix
    that nothing here can see. It costs nothing, because a prefix used only
    that way is by this measure UNUSED and so is re-emitted literally, which
    is the outcome that keeps the value readable.
    """
    uris = set()
    for el in root.iter():
        tag = el.tag
        # A comment's tag is the factory that made it, not a name -- the same
        # non-string node `strip_ns` guards. It is in no namespace.
        if isinstance(tag, str) and tag.startswith("{"):
            uris.add(tag[1:].partition("}")[0])
        for key in el.keys():
            if key.startswith("{"):
                uris.add(key[1:].partition("}")[0])
    return uris


@contextlib.contextmanager
def _document_prefixes(root: ET.Element, declarations):
    """Serialise ``root`` under its own prefixes, and leave nothing behind.

    Two mechanisms, because `ElementTree` loses namespace declarations in two
    different ways, and one context manager because both have to be undone.

    **Prefixes that are used** are put back with `ET.register_namespace`, the
    only lever the serialiser offers. Including the default one: registering
    `("", uri)` makes `ElementTree` write `xmlns="uri"` and leave the tags
    unprefixed, which the `default_namespace=` argument to `write()` cannot
    do here -- that one raises `ValueError` on the first unqualified
    attribute, and every attribute in SCL is unqualified.

    **Declarations that are not used** cannot go through the serialiser at
    all: it emits a declaration only for a namespace something is in. They are
    set as literal `xmlns:pfx` ATTRIBUTES on the root instead, which
    `ElementTree` writes out verbatim because the name is not `{uri}local`
    shaped. That is a deliberate use of the serialiser rather than an
    accident of it.

    Both are undone on the way out:

    - the registry is snapshotted and restored, so a document serialised here
      changes how NO other document in the process serialises, before or
      after. `register_namespace` is documented as global and it means it: it
      deletes every existing entry sharing either the URI or the prefix.
      **The damage a leak does is not where it first looks**, which was
      measured rather than guessed: registering without restoring would leave
      this package's own output mostly intact, because the loop above re-runs
      immediately before every write. What it would break is every OTHER
      `ElementTree` user in the process -- code that is not a consumer of
      this package at all -- and any document whose declarations are past the
      64 kB `_declared_namespaces` scans, which has nothing of its own to
      re-register and would inherit a stranger's prefix;
    - the literal attributes are removed, so a caller that reads
      ``doc.root.attrib`` never sees an entry that is not an attribute. The
      tree the model walks is the tree the parser built, before and after.

    **Three cases are refused rather than mishandled**, all of them absent
    from the corpus and each one caught by the round-trip test if a fixture
    ever carries it:

    1. A prefix of the reserved `nsN` form. `register_namespace` raises
       `ValueError` on it, and re-emitting it literally could collide with
       the `nsN` the serialiser is about to invent -- two attributes of one
       name is not XML at all. It is dropped.
    2. A prefix already claimed by a used namespace, for a different URI.
       Same collision, same answer. `xsi` is the realistic one: it is in
       `ElementTree`'s registry from the start.
    3. The same URI declared twice. The FIRST prefix is what the tags are
       written with -- first-seen wins, because that is the one a reader of
       the original file sees first -- and the second is re-emitted literally,
       so the declaration survives even though nothing can be written under
       it.
    """
    used = _used_namespace_uris(root)
    with _NS_LOCK:
        snapshot = dict(_NS_MAP) if _NS_MAP is not None else None
        if snapshot is None:
            _logger.warning(
                "xml.etree has no _namespace_map: prefixes will be registered "
                "globally and not restored")
        literal = []
        registered = set()
        for prefix, uri in declarations:
            if uri in used and uri not in registered:
                try:
                    ET.register_namespace(prefix, uri)
                except ValueError:
                    continue            # case 1: reserved `nsN` form
                registered.add(uri)
            else:
                literal.append((prefix, uri))
        # After the loop, not during it: what counts as taken is what the
        # WHOLE document registered, not what the declarations before this one
        # happened to.
        taken = {p for u, p in (_NS_MAP if _NS_MAP is not None else {}).items()
                 if u in used}
        added = []
        for prefix, uri in literal:
            if prefix in taken or _RESERVED_PREFIX.match(prefix):
                continue                # cases 1 and 2
            name = "xmlns:" + prefix if prefix else "xmlns"
            if name in root.attrib:
                # A parsed tree never has one -- expat consumes declarations
                # before the tree exists -- but `SclDocument` can be built
                # around a hand-made root. Deleting somebody's attribute in
                # the `finally` below would be a worse bug than a lost
                # declaration, and the attribute already says what we wanted
                # to say.
                continue
            root.set(name, uri)
            added.append(name)
        try:
            yield
        finally:
            for name in added:
                del root.attrib[name]
            if snapshot is not None:
                _NS_MAP.clear()
                _NS_MAP.update(snapshot)


def _declared_namespaces(path: Path) -> tuple:
    """Every ``xmlns`` declaration in the file, as ``(prefix, uri)`` pairs.

    In first-seen order, with ``""`` for the default namespace. Duplicates of
    the WHOLE PAIR are dropped; the same URI under two prefixes is kept twice,
    because those are two declarations and the fidelity guarantee is about
    both of them.

    ``ElementTree`` discards prefix declarations when it builds the tree --
    an element's tag comes back as ``{uri}Local`` and the prefix that was
    written is gone -- so both halves are recovered from the bytes here. This
    is the only thing in the package that knows the document's own spelling
    of its namespaces, and :meth:`SclDocument._to_bytes` is what consumes it.

    Only the first 64 kB is scanned: SCL declares its namespaces on the root
    element, and a file that declares one a megabyte in is not a file this
    reader is trying to serve. All four corpus fixtures declare every one of
    theirs on the root and nowhere else -- the largest is 23.5 MB and its
    declarations are on line 2.
    """
    seen = []
    try:
        with open(path, "rb") as fh:
            head = fh.read(65536)
    except OSError:
        return ()
    for prefix, uri in _NS_DECL.findall(head):
        pair = (prefix.decode("utf-8", "replace"),
                uri.decode("utf-8", "replace"))
        if pair not in seen:
            seen.append(pair)
    return tuple(seen)
