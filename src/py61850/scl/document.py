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

import logging
import re
from pathlib import Path
from xml.etree import ElementTree as ET

from ._xmlsafe import DtdNotAllowed, reject_dtd_in_file

_logger = logging.getLogger(__name__)

_NS_DECL = re.compile(br'xmlns(?::[A-Za-z0-9_.-]+)?\s*=\s*["\']([^"\']+)["\']')


def strip_ns(tag: str) -> str:
    """``{ns}LocalName`` -> ``LocalName``."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def iter_local(root: ET.Element, local_name: str):
    """Every DESCENDANT whose local name is ``local_name``, namespace ignored.

    Includes ``root`` itself when it matches, which is what makes
    ``iter_local(ied_el, "LDevice")`` read naturally.
    """
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

    __slots__ = ("root", "path", "_namespaces", "_header", "_cache")

    def __init__(self, root: ET.Element, path=None, namespaces=()) -> None:
        self.root = root
        self.path = path
        self._namespaces = tuple(namespaces)
        self._header = False        # sentinel: not looked up yet
        self._cache: dict = {}

    # -- constructors -------------------------------------------------------

    @classmethod
    def parse(cls, path) -> "SclDocument":
        """The document, raising on any failure.

        ``OSError`` for a file that cannot be read, ``ET.ParseError`` for XML
        that will not parse, and :class:`DtdNotAllowed` for one declaring a
        DTD.
        """
        p = Path(path)
        # Before the parser, never after: a DTD's entities expand DURING the
        # parse and there is no half-way to stop at.
        reject_dtd_in_file(p)
        return cls(ET.parse(str(p)).getroot(), p, _declared_namespaces(p))

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

    # -- shallow facts ------------------------------------------------------

    @property
    def namespaces(self) -> tuple:
        """Every namespace URI the document declares, standard and vendor."""
        return self._namespaces

    @property
    def header(self):
        """The ``<Header>``, or ``None`` when the file carries none."""
        if self._header is False:
            el = next(iter_local(self.root, "Header"), None)
            self._header = Header(el) if el is not None else None
        return self._header

    @property
    def edition(self):
        """``"2007B"``, ``"2003"``, ... from the header, or ``None``.

        Read from the header rather than guessed from the namespace: a file
        declares several namespaces and the standard one has not changed URI
        since Edition 1, so the namespace does not identify the edition.
        """
        h = self.header
        if h is None or not h.version:
            return None
        return f"{h.version}{h.revision or ''}"

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


def _declared_namespaces(path: Path) -> tuple:
    """Every ``xmlns`` URI in the file, in first-seen order.

    ``ElementTree`` discards prefix declarations when it builds the tree, so
    they are recovered from the bytes. Only the first 64 kB is scanned: SCL
    declares its namespaces on the root element, and a file that declares one
    a megabyte in is not a file this reader is trying to serve.
    """
    seen = []
    try:
        with open(path, "rb") as fh:
            head = fh.read(65536)
    except OSError:
        return ()
    for match in _NS_DECL.finditer(head):
        uri = match.group(1).decode("utf-8", "replace")
        if uri not in seen:
            seen.append(uri)
    return tuple(seen)
