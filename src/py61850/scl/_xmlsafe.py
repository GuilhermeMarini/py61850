# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""Refuse an XML document that declares a DTD, before anything expands it.

Every SCL file this library reads arrives from outside -- what a client's
integrator sent, what a vendor's tool exported. "The file is trusted" is not
true even on a substation LAN.

A DTD may define entities, and entities may reference each other, so each
level multiplies::

    <!ENTITY a "AAAAAAAAAA">                        10 chars
    <!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">    100
    <!ENTITY c "&b;&b;...">                         1 000

The file stays tiny because it stores only the definitions; the expansion
happens in memory while parsing, and the parser cannot stop half way -- it
finishes or the process dies. Measured against this stack: 317 bytes on the
wire became 1,000,000 characters in 7 ms, a factor of 3155, and two more
levels of the same file is 100 million. Python's own documentation lists
``xml.etree.ElementTree`` as vulnerable to it; the attack is old enough to
have a name, "billion laughs".

Refusing the whole construct is what makes the class gone rather than
mitigated. It costs nothing, because SCL does not use DTDs -- IEC 61850
validates against an XSD schema referenced by namespace. Measured over every
SCL file reachable from the project this came from: **696 files, 0 with a
DOCTYPE** (345 factory ICDs and 351 substation SCD/ICD/CID files).

Why a separate pass rather than a parser flag: ``xml.etree``'s C parser does
not expose the expat instance underneath it, so there is no handler to install
on the parse that actually builds the tree. This runs expat over the PROLOG
only and stops at the root element, so it reads a few hundred bytes of a 22 MB
file -- 0.7 ms even on a document padded with a 500 kB comment, which is the
shape that would defeat a "look at the first N bytes" check.
"""

from __future__ import annotations

from pathlib import Path
from typing import BinaryIO
from xml.parsers import expat

from ..errors import SclError


class DtdNotAllowed(SclError, ValueError):
    """The document declares a ``<!DOCTYPE>``. See this module's docstring.

    Both bases are load-bearing: ``SclError`` so that a caller catching
    ``Iec61850Error`` catches this too, and ``ValueError`` because that is
    what it has always been to the callers that predate this package.
    """


class _ReachedRoot(Exception):
    """Internal: the prolog ended without a DTD, so there is nothing to check."""


def _scan(chunks) -> None:
    """Feed ``chunks`` to expat until the root element or a DTD declaration.

    A malformed prolog raises ``ExpatError``, which is swallowed: the real
    parse is about to hit the very same bytes with the very same grammar and
    will report it properly. That is not a way past this check -- a document
    whose prolog expat cannot read is a document ``ET.parse`` cannot read
    either.
    """
    parser = expat.ParserCreate()

    def _on_doctype(name, sysid, pubid, has_internal_subset):
        raise DtdNotAllowed(
            f"the document declares a DTD (<!DOCTYPE {name}>), which this "
            f"format does not use and which permits entity expansion"
        )

    def _on_root(name, attrs):
        raise _ReachedRoot

    parser.StartDoctypeDeclHandler = _on_doctype
    parser.StartElementHandler = _on_root

    try:
        for chunk in chunks:
            parser.Parse(chunk, not chunk)
    except _ReachedRoot:
        return
    except expat.ExpatError:
        return


def _file_chunks(stream: BinaryIO, size: int = 8192):
    while True:
        chunk = stream.read(size)
        yield chunk
        if not chunk:
            return


def reject_dtd_in_file(path: Path) -> None:
    """Raise :class:`DtdNotAllowed` if the file at ``path`` declares a DTD."""
    with open(path, "rb") as fh:
        _scan(_file_chunks(fh))


def reject_dtd_in_bytes(data: bytes) -> None:
    """Raise :class:`DtdNotAllowed` if ``data`` declares a DTD."""
    _scan(iter((data, b"")))
