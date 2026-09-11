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

import codecs
import contextlib
import io
import logging
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import NamedTuple
from xml.etree import ElementTree as ET

from . import edit as _edit
from ._xmlsafe import DtdNotAllowed, reject_dtd_in_file

_logger = logging.getLogger(__name__)

# `xmlns="uri"` and `xmlns:pfx="uri"`, with the prefix CAPTURED. Recovering
# the prefix and not only the URI is what A3 needed: `ElementTree` discards
# prefix declarations when it builds the tree, so a document serialised back
# out comes back as `ns0:` unless the prefix is registered from somewhere,
# and this is the only place that still knows what it was.
_NS_DECL = re.compile(
    br"""xmlns(?::([A-Za-z0-9_.-]+))?\s*=\s*["']([^"']+)["']""")

# The XML declaration, and the first start tag after the prolog. Both are read
# from the file's own bytes for the same reason the declarations above are:
# `ElementTree` keeps neither, and the writer has to put both back.
_DECLARATION = re.compile(br"<\?xml.*?\?>", re.S)
_PROLOG_ITEM = re.compile(br"<\?.*?\?>|<!--.*?-->|<!\[[^]]*\]\]>|<![^>]*>", re.S)
_START_TAG = re.compile(
    br"""<([^\s/>]+)((?:\s+[^\s=/>]+\s*=\s*(?:"[^"]*"|'[^']*'))*)\s*(/?)>""")
_ATTRIBUTE = re.compile(
    br"""([^\s=/>]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
# `encoding="..."` inside a declaration, with the VALUE's span captured so it
# can be replaced without disturbing the quote style around it.
_ENCODING = re.compile(br"""(encoding\s*=\s*["'])([^"']*)(["'])""")


class _SourceLayout(NamedTuple):
    """How the file was written, as opposed to what it says.

    Five facts that XML parsing destroys and that the fidelity guarantee is
    about. `ElementTree` keeps none of them: expat consumes the prolog before
    a tree exists, normalises every line ending on the way past, and hands
    back an element whose attributes have lost the order they were written in
    only for the ROOT -- where the serialiser re-sorts them around the
    declarations it generates.

    Defaults describe a document that was never read from a file at all --
    one built around a hand-made root. It gets `ElementTree`'s own habits:
    LF, no declaration, no trailing newline, and whatever attribute order the
    tree happens to carry.
    """

    declarations: tuple = ()        # (prefix, uri) pairs, first-seen order
    declaration: bytes = b""        # the XML declaration verbatim; b"" for none
    bom: bytes = b""                # a byte-order mark, if the file opened with one
    eol: bytes = b"\n"              # the ending the file is mostly written with
    final_newline: bool = False     # whether the last line was terminated
    root_attributes: tuple = ()     # the root's attribute NAMES, in written order


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

    __slots__ = ("root", "path", "_layout", "_header", "_cache")

    def __init__(self, root: ET.Element, path=None, layout=None) -> None:
        self.root = root
        self.path = path
        # How the file was written -- see `_SourceLayout` and `_source_layout`.
        # One object rather than five attributes because it is one thing, and
        # because :meth:`to_bytes` consumes all five together.
        self._layout = layout if layout is not None else _SourceLayout()
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
        return cls(ET.parse(str(p), parser).getroot(), p, _source_layout(p))

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

    def to_bytes(self) -> bytes:
        """The document as bytes: the file it was parsed from, as near as XML allows.

        **The guarantee.** A document parsed and written back with **no edit
        applied** is the file it came from, byte for byte, with five
        exceptions -- none of which any consumer can observe through an XML
        parser, and all of which are `ElementTree` limits rather than choices:

        1. **Attribute quote style.** ``a='1'`` comes back ``a="1"``. The
           serialiser writes double quotes and offers no say in it.
        2. **Empty-element spacing.** ``<X/>`` comes back ``<X />``.
        3. **Empty-element form.** ``<X></X>`` comes back ``<X />``. Expat
           reports no character data for either spelling, so both arrive as
           one element with ``text`` of ``None`` and nothing downstream can
           tell them apart. A file is not usually consistent about this
           itself: the reference SEL export writes 115,576 empty elements as
           ``<X />`` and 72 as ``<X></X>``, some of them lines apart.
        4. **CDATA boundaries.** ``<![CDATA[a<b]]>`` comes back ``a&lt;b``,
           for the same reason -- expat reports the content as ordinary
           character data and the boundary never reaches the tree.
        5. **Numeric character references.** ``&#xA;`` comes back ``&#10;``.
           The escaper writes the decimal form and the spelling is not
           configurable.

        **Everything else survives**: comments, indentation, attribute order,
        namespace prefixes, ``xmlns`` declarations nothing uses, the line
        ending the file was written with, its trailing newline if it had one,
        its byte-order mark if it had one, and the XML declaration spelled as
        the file spelled it. ``tests/unit/test_scl_roundtrip.py`` holds that
        claim against four real station exports, 44 MB of them, at the level
        of bytes.

        **One thing is deliberately not reproduced**: an encoding declaration
        naming anything but UTF-8. Expat decodes the file, this writes UTF-8,
        and re-emitting ``encoding="ISO-8859-1"`` over UTF-8 bytes would hand
        back a file that no longer parses. The quote style is kept and the
        name alone is corrected; nothing else in the declaration is touched.
        Fidelity that produces an unreadable file is not fidelity.
        """
        buf = io.BytesIO()
        with _document_prefixes(self.root, self._layout.declarations):
            # `xml_declaration=False`: `ElementTree` spells one with single
            # quotes and a lower-cased encoding name, and the file's own
            # spelling is recorded. It goes back on below.
            ET.ElementTree(self.root).write(
                buf, encoding="utf-8", xml_declaration=False)
        data = _reorder_root_attributes(buf.getvalue(),
                                        self._layout.root_attributes)
        declaration = _reconcile_declaration(self._layout.declaration)
        if declaration:
            data = declaration + b"\n" + data
        if self._layout.bom:
            data = self._layout.bom + data
        if self._layout.final_newline:
            # `ElementTree` writes none: the root element has no tail, because
            # whitespace after the document element is not character data and
            # never reaches the tree.
            data += b"\n"
        if self._layout.eol != b"\n":
            # Last, and over the whole document. XML end-of-line
            # normalisation happens inside expat, so every `\r\n` the file was
            # written with is already an LF before a tree exists and there is
            # nothing left to tell one newline from another -- re-emitting
            # them one at a time is not an option the tree can support.
            #
            # `ElementTree` emits no bare `\r` for this to collide with: it
            # escapes one in an attribute value as `&#13;`. The single shape
            # this rewrites wrongly is a `&#xD;` or `&#xA;` character
            # reference in element TEXT -- exempt from end-of-line
            # normalisation, so it reaches the tree as a real newline and
            # comes back out as one. No corpus file carries one; the 23 in
            # the SEL export are all inside attribute values, where the
            # escaper puts them back as references. The round-trip test is
            # what says so the day a fixture disagrees.
            data = data.replace(b"\n", self._layout.eol)
        return data

    # -- editing ------------------------------------------------------------

    def apply_edit(self, edit):
        """Apply ``edit`` and return the edit that undoes it.

        ``edit`` is an :class:`~py61850.scl.Insert`,
        :class:`~py61850.scl.Remove`, :class:`~py61850.scl.SetAttributes` or
        :class:`~py61850.scl.SetTextContent`, or a list of them -- which is
        itself an edit, applied in order and inverted in reverse.

        Raises :class:`~py61850.scl.EditRejected`, having changed nothing. See
        :mod:`py61850.scl.edit` for what that costs and what it guarantees.

        **The lazy caches are not invalidated.** ``.templates``,
        ``.communication``, ``.ied_headers`` and each ``ied(name)`` are built
        on first use and kept; an edit that removes an IED leaves the cached
        one reachable. Rebuilding them all costs about 1 ms, but re-warming a
        single ``Ied`` costs 41 ms against 3 ms warm, on a 22 MB export -- a
        14x tax on exactly the loop an editor runs. What to do about it is a
        recorded open question, to be answered where the model objects are
        actually edited; for now, an edited document is best read through a
        fresh :meth:`parse` or through the tree itself.
        """
        return _edit.apply_edit(self, edit)

    def parent_of(self, element):
        """``element``'s parent, or ``None`` for the root and for an element
        this document does not contain.

        The parent map ``ElementTree`` does not provide. It is built on first
        use -- 98 ms and 21 MB on a 22 MB export -- and maintained by
        :meth:`apply_edit` thereafter.
        """
        return _edit.parent_of(self, element)

    def write(self, path) -> None:
        """Write the document to ``path``, atomically.

        The bytes are :meth:`to_bytes` and carry its guarantee.

        They are built in a temporary file beside the destination and only
        then ``os.replace``d onto it, so a failure anywhere -- a full disk, a
        serialisation that raises, an interrupt -- leaves the destination
        exactly as it was, whether that is absent or the SCD that was there
        before. ``cfbwrite`` writes an RDB the same way for the same reason:
        the file goes back into a vendor tool, and a half-written one is
        worse than none at all.

        An existing file is replaced without asking, and nothing is read back.
        """
        data = self.to_bytes()
        dst = Path(path)
        fd, tmp_name = tempfile.mkstemp(dir=str(dst.parent),
                                        prefix=f".{dst.name}.", suffix=".scl-tmp")
        os.close(fd)
        tmp = Path(tmp_name)
        try:
            tmp.write_bytes(data)
            # mkstemp opens at 0600; the finished SCD is an ordinary file.
            os.chmod(tmp, 0o644)
            os.replace(tmp, dst)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    # -- shallow facts ------------------------------------------------------

    @property
    def namespaces(self) -> tuple:
        """Every namespace URI the document declares, standard and vendor.

        URIs only, in first-seen order and de-duplicated, which is what a
        consumer asking "does this file speak Siedig?" wants. The prefixes
        each was declared under are :attr:`_layout`, and they are
        private: nothing outside serialisation has needed one yet, and a name
        a consumer can reach is a promise.
        """
        uris = []
        for _, uri in self._layout.declarations:
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
      64 kB `_source_layout` scans, which has nothing of its own to
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


def _reconcile_declaration(declaration: bytes) -> bytes:
    """The file's own XML declaration, with a non-UTF-8 encoding name corrected.

    Verbatim is the answer for every declaration in the corpus and for every
    one this is ever likely to meet: the four fixtures spell the encoding
    `UTF-8` three times and `utf-8` once, both double-quoted, where
    `ElementTree` would write `<?xml version='1.0' encoding='utf-8'?>` for
    all four.

    The exception is the one case where reproducing the file would break it.
    Expat decodes whatever the file declared; :meth:`SclDocument.to_bytes`
    writes UTF-8. A declaration that still said `windows-1252` over UTF-8
    bytes would describe the file wrongly, and the next tool to open it would
    either fail or read mojibake. So the NAME is replaced and everything
    around it -- the quote style, the version, a `standalone` attribute,
    the spacing -- is left exactly as it was.

    `codecs.lookup` is what decides, rather than a list of spellings, so
    `utf8`, `UTF_8` and `U8` are all recognised as the same encoding and left
    alone. An encoding Python does not know is treated as not-UTF-8, which is
    the safe direction: the bytes really are UTF-8.
    """
    if not declaration:
        return b""
    match = _ENCODING.search(declaration)
    if match is None:
        # No encoding declared. UTF-8 is XML's default for a file without
        # one, so the declaration is already true of what is written.
        return declaration
    try:
        is_utf8 = codecs.lookup(
            match.group(2).decode("ascii", "replace")).name == "utf-8"
    except LookupError:
        is_utf8 = False
    if is_utf8:
        return declaration
    return (declaration[:match.start(2)] + b"utf-8"
            + declaration[match.end(2):])


def _reorder_root_attributes(data: bytes, order) -> bytes:
    """``data`` with the root's attributes back in the order the file wrote them.

    **The root is the one element `ElementTree` reorders**, and it does so
    because it is the one element it adds attributes to: the namespace
    declarations it generates are written first, sorted by prefix, ahead of
    every ordinary attribute. The declarations :func:`_document_prefixes`
    re-emits as literal attributes land after them all. A real file
    interleaves the two and no fixture survives the split -- the SEL export
    opens ``<SCL xmlns:esel=... version=... xmlns=...>``, with the default
    declaration LAST.

    There is no hook for this inside the serialiser, so it is done to the
    bytes afterwards, to one tag. That is cheap and it is bounded: the tag is
    at offset 0, because the declaration has not been prepended yet and a
    comment in the prolog never reaches the tree.

    **Attributes not in ``order`` keep their place after those that are.**
    That is what an edit adding an attribute produces, and appending it is
    the only answer that does not pretend to know where the file would have
    put it. A recorded name the output no longer has is simply absent -- an
    edit removed it.

    Nothing happens at all when ``order`` is empty -- a document not read
    from a file, or one whose root tag ran past the 64 kB
    :func:`_source_layout` scans -- and `ElementTree`'s order stands.
    """
    if not order:
        return data
    match = _START_TAG.match(data)
    if match is None:                   # not a start tag: nothing to reorder
        return data
    written = []
    for attr in _ATTRIBUTE.finditer(match.group(2)):
        value = attr.group(2) if attr.group(2) is not None else attr.group(3)
        written.append((attr.group(1), value))
    by_name = dict(written)
    if len(by_name) != len(written):
        # Two attributes of one name is not XML, and a tree that produced it
        # is not one to tidy up. Left exactly as the serialiser wrote it.
        return data
    sequence = [name for name in order if name in by_name]
    sequence += [name for name, _ in written if name not in order]
    rebuilt = b"<" + match.group(1) + b"".join(
        b' %s="%s"' % (name, by_name[name]) for name in sequence)
    # `ElementTree` writes an empty element as `<X />`; keep that spacing
    # rather than inventing a difference this function was not asked about.
    rebuilt += b" />" if match.group(3) else b">"
    return rebuilt + data[match.end():]


def _source_layout(path: Path) -> _SourceLayout:
    """How ``path`` was written, as :class:`_SourceLayout` -- five facts XML loses.

    **The declarations** are every ``xmlns`` in the file, as ``(prefix, uri)``
    pairs, in first-seen order, with ``""`` for the default namespace.
    Duplicates of the WHOLE PAIR are dropped; the same URI under two prefixes
    is kept twice, because those are two declarations and the fidelity
    guarantee is about both of them. ``ElementTree`` discards prefix
    declarations when it builds the tree -- an element's tag comes back as
    ``{uri}Local`` and the prefix that was written is gone -- so both halves
    are recovered from the bytes here.

    **The XML declaration** and **the root's attribute names** are read the
    same way and for the same reason: expat consumes the prolog before a tree
    exists, and the serialiser re-sorts the root's attributes around the
    declarations it generates.

    **The line ending** is whichever of CRLF and LF is commoner in the head,
    LF on a tie or a file with no line break at all. End-of-line
    normalisation is mandatory in XML and happens inside expat, so this is
    the only place the file's own ending is still visible. **The final
    newline** is the one fact read from the end of the file rather than the
    head -- one byte, one seek.

    Only the first 64 kB is scanned, as in a file that declares a namespace a
    megabyte in is not a file this reader is trying to serve. All four corpus
    fixtures declare every namespace on the root and nowhere else; the
    largest is 23.5 MB and its declarations are on line 2. A root start tag
    that ran past the window would come back with no attribute order, and
    :func:`_reorder_root_attributes` would leave the serialiser's order
    alone -- degraded, not wrong.

    An unreadable file gives the default layout rather than raising:
    :meth:`SclDocument.parse` has already opened it once by the time this
    runs, and a document that serialises with `ElementTree`'s own habits is a
    better answer here than a second, different exception.
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(65536)
            if fh.seek(0, os.SEEK_END):
                fh.seek(-1, os.SEEK_END)
                final_newline = fh.read(1) == b"\n"
            else:
                final_newline = False   # an empty file ends no line
    except OSError:
        return _SourceLayout()

    seen = []
    for prefix, uri in _NS_DECL.findall(head):
        pair = (prefix.decode("utf-8", "replace"),
                uri.decode("utf-8", "replace"))
        if pair not in seen:
            seen.append(pair)

    # A byte-order mark is not content and never reaches the tree, so it is
    # recorded here or it is lost. It also has to be stepped over before
    # anything else in the prolog can be recognised.
    bom = codecs.BOM_UTF8 if head.startswith(codecs.BOM_UTF8) else b""
    pos = len(bom)
    declaration = _DECLARATION.match(head, pos)

    crlf = head.count(b"\r\n")
    lf = head.count(b"\n") - crlf

    return _SourceLayout(
        declarations=tuple(seen),
        declaration=declaration.group() if declaration else b"",
        bom=bom,
        eol=b"\r\n" if crlf > lf else b"\n",
        final_newline=final_newline,
        root_attributes=_root_attribute_names(head, pos),
    )


def _root_attribute_names(head: bytes, pos: int) -> tuple:
    """The root start tag's attribute NAMES, in the order the file wrote them.

    Values are not recorded: they are in the tree, and the tree is what the
    writer serialises. Only the order is lost, and only for this one element.
    """
    while True:
        while head[pos:pos + 1].isspace():
            pos += 1
        item = _PROLOG_ITEM.match(head, pos)
        if item is None:
            break
        pos = item.end()
    match = _START_TAG.match(head, pos)
    if match is None:
        return ()
    return tuple(attr.group(1) for attr in _ATTRIBUTE.finditer(match.group(2)))
