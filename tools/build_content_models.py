#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""Extract the SCL content models from the IEC schema into a Python table.

    python tools/build_content_models.py                    # rewrite the table
    python tools/build_content_models.py --check            # compare, don't write

**The table is generated rather than transcribed**, and that is the whole
point of this file. A7 requires the ordering's provenance to be stated and
checkable against the schema; a hand-written table is neither, because nobody
can review 82 element orderings against an XSD by eye and be sure. Here the
provenance is the run, and `--check` is the review: it regenerates and
compares, so a table that has drifted from the schema fails a test rather than
producing documents that load here and fail in DIGSI.

## What it reads, and what it does not ship

IEC's own code-component distribution of the SCL schema, as the zip it arrives
in, from ``docs/IEC61850 files/``. **That directory is excluded from the
repository** -- the schemas are IEC copyright and the terms are at
www.iec.ch/CCv1. Nothing IEC-published is copied into the output: what the
table carries is element names and the order they go in, which is one
structural dimension of the schema and not its attributes, its types, its
enumerations, its cardinalities, its uniqueness constraints or its prose.

Whether even that may be shipped is **Q17**, deliberately still open. This
script and everything that uses it work identically either way.

## How a content model is resolved

An SCL element's children come from its complexType, and 102 of the 153 types
are extensions, so the model is the base type's content followed by the
extension's own. `tBaseElement` sits at the bottom of almost every chain and
contributes ``xs:any(##other)*``, ``Text?``, ``Private*`` -- which is why
`Private` precedes every type-specific child everywhere in SCL, a fact that is
easy to get wrong by hand and impossible to get wrong here.

The output is a tuple of SLOTS per element. A slot holds one name when the
schema fixes its position, and several when it does not:

    "DOI": (("##other",), ("Text",), ("Private",), ("SDI", "DAI"))

`SDI` and `DAI` share a slot because `tDOI`'s content is a repeated
``xs:choice`` -- either order is legal, and 197,000 elements in the corpus
write both. `tServices` is an ``xs:all``, so all 33 of its children share one
slot. Reading order off a sequence and calling it the schema would have
invented a rule for both.
"""

import argparse
import pathlib
import sys
import zipfile
from xml.etree import ElementTree as ET

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from py61850.scl._xmlsafe import reject_dtd_in_bytes  # noqa: E402

XS = "{http://www.w3.org/2001/XMLSchema}"

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCHEMA_DIR = ROOT / "docs" / "IEC61850 files"
DEFAULT_PACKAGE = "IEC_61850-6.2018.SCL.2007B4.full.zip"
TARGET = ROOT / "src" / "py61850" / "scl" / "_content_models.py"

# The marker for `xs:any namespace="##other"`. It is a real position in the
# content model -- `tBaseElement` puts foreign content BEFORE `Text` -- so it
# occupies a slot rather than being dropped.
ANY = "##other"


class Schema:
    """The SCL complexTypes and element declarations, from one package."""

    def __init__(self, sources):
        self.types = {}
        self.elements = {}          # name -> type name, or an inline complexType
        self.version = None
        for name, data in sorted(sources.items()):
            root = ET.fromstring(data)
            if self.version is None and root.get("version"):
                self.version = root.get("version")
            for ct in root.iter(XS + "complexType"):
                if ct.get("name"):
                    self.types[ct.get("name")] = ct
            for el in root.iter(XS + "element"):
                self._declare(el)

    def _declare(self, el):
        name = el.get("name")
        if not name or name in self.elements:
            return
        if el.get("type"):
            self.elements[name] = _local(el.get("type"))
            return
        inline = el.find(XS + "complexType")
        if inline is not None:
            self.elements[name] = inline

    def model_of(self, element_name):
        """The slots for one element name, or ``()`` if it holds no elements."""
        declared = self.elements.get(element_name)
        if declared is None:
            return ()
        node = declared if not isinstance(declared, str) else self.types.get(declared)
        return () if node is None else tuple(self._slots(node, ()))

    def _slots(self, complex_type, seen):
        """Slots for a complexType node, base classes first.

        `seen` guards a cycle. The schema has none, but a generator that would
        recurse forever on a malformed one is a generator that hangs a build
        rather than failing it.
        """
        extension = complex_type.find(f"{XS}complexContent/{XS}extension")
        node = extension if extension is not None else complex_type
        slots = []
        if extension is not None:
            base = _local(extension.get("base"))
            if base and base not in seen:
                slots += self._slots(self.types[base], seen + (base,))
        for child in node:
            slots += self._compositor(child)
        return slots

    def _compositor(self, node):
        """Slots contributed by one `xs:sequence`, `xs:choice` or `xs:all`.

        A sequence fixes the position of each item, so each contributes its own
        slot. A choice or an all fixes nothing between its members, so they
        share one -- which is what stops the resolver inventing an order the
        schema does not have.
        """
        tag = node.tag.replace(XS, "")
        if tag in ("choice", "all"):
            names = self._names(node)
            return [tuple(names)] if names else []
        if tag != "sequence":
            return []
        slots = []
        for child in node:
            kind = child.tag.replace(XS, "")
            if kind == "element":
                name = child.get("name") or _local(child.get("ref"))
                if name:
                    slots.append((name,))
            elif kind == "any":
                slots.append((ANY,))
            elif kind in ("sequence", "choice", "all"):
                slots += self._compositor(child)
        return slots

    def _names(self, node):
        out = []
        for el in node.iter():
            kind = el.tag.replace(XS, "")
            if kind == "element":
                name = el.get("name") or _local(el.get("ref"))
                if name and name not in out:
                    out.append(name)
            elif kind == "any" and ANY not in out:
                out.append(ANY)
        return out


def _local(qname):
    return qname.split(":")[-1] if qname else qname


def read_package(path):
    """``{filename: bytes}`` for the SCL schemas inside an IEC package."""
    schemas = {}
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if not (name.endswith(".xsd") and name.startswith("SCL")):
                continue
            data = archive.read(name)
            # The same rule the library applies to every SCL file it reads.
            # A schema package is not untrusted input in any realistic sense,
            # but `ElementTree` expands entities and the house answer to that
            # is already written; using it here costs a line and keeps one
            # rule rather than two.
            reject_dtd_in_bytes(data)
            schemas[name] = data
    return schemas


def render(schema, package_name):
    """The generated module, as text."""
    models = {}
    for name in sorted(schema.elements):
        slots = schema.model_of(name)
        if slots:
            models[name] = slots

    lines = [
        "# SPDX-License-Identifier: AGPL-3.0-or-later",
        "# Copyright (C) 2026 Guilherme Marini",
        "#",
        "# This file is part of py61850. It is free software under the GNU Affero",
        "# General Public License v3 or later; see LICENSE. A commercial licence,",
        "# for use in software you do not wish to release under the AGPL, is",
        "# available from the copyright holder -- see COMMERCIAL.md.",
        '"""The SCL content models: which children an element may hold, in which order.',
        "",
        "**Generated. Do not edit by hand** -- run ``tools/build_content_models.py``,",
        "which is also what ``tests/unit/test_scl_ordering.py`` uses to check that this",
        "file still says what the schema says.",
        "",
        "Extracted from the SCL schema published by IEC as a code component:",
        "",
        f"    IEC 61850-6:2009/AMD1:2018 -- SCL schema version {schema.version}",
        f"    {package_name}",
        "",
        "The schema itself is not part of this repository. What is here is one",
        "structural dimension of it -- element names and the order they go in -- and",
        "none of its attributes, types, enumerations, cardinalities, constraints or",
        "documentation.",
        "",
        "**This code was derived from IEC 61850-6:2009/AMD1:2018 within modifications",
        "permitted in the relevant IEC standard. Please reproduce this note if",
        "possible.** The IEC copyright notice, the code-component licence conditions",
        "and the disclaimer they require are in ``NOTICE-IEC.txt``, which ships with",
        "every sdist and wheel. py61850 is not endorsed by or affiliated with IEC.",
        "",
        "Each entry is a tuple of SLOTS. A slot holds one name where the schema fixes",
        "its position and several where it does not: `DOI` below carries `SDI` and",
        "`DAI` in one slot because `tDOI`'s content is a repeated ``xs:choice``, and",
        "`Services` carries all 33 of its children in one because ``tServices`` is an",
        "``xs:all``. Elements in the same slot are interchangeable; a slot always",
        "comes before the one after it.",
        "",
        f'``{ANY}`` is ``xs:any namespace="##other"`` -- foreign-namespace content,',
        "which `tBaseElement` places ahead of `Text` and `Private`.",
        '"""',
        "",
        "CONTENT_MODELS = {",
    ]
    for name, slots in models.items():
        body = ", ".join(_slot(s) for s in slots)
        entry = f'    "{name}": ({body},),'
        if len(entry) <= 79:
            lines.append(entry)
        else:
            lines.append(f'    "{name}": (')
            for slot in slots:
                lines.append(f"        {_slot(slot)},")
            lines.append("    ),")
    lines.append("}")
    lines.append("")
    return "\n".join(lines)


def _slot(names):
    inner = ", ".join(f'"{n}"' for n in names)
    return f"({inner},)" if len(names) == 1 else f"({inner})"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--package", default=str(SCHEMA_DIR / DEFAULT_PACKAGE),
                        help="the IEC SCL code-component zip to read")
    parser.add_argument("--check", action="store_true",
                        help="compare against the committed table instead of writing it")
    args = parser.parse_args(argv)

    package = pathlib.Path(args.package)
    if not package.is_file():
        print(f"schema package not found: {package}", file=sys.stderr)
        return 2
    schema = Schema(read_package(package))
    produced = render(schema, package.name)

    if not args.check:
        TARGET.write_text(produced, encoding="utf-8")
        print(f"{TARGET.relative_to(ROOT)}: "
              f"{produced.count(chr(10))} lines from {package.name}")
        return 0

    current = TARGET.read_text(encoding="utf-8") if TARGET.is_file() else ""
    if current == produced:
        print(f"{TARGET.relative_to(ROOT)} agrees with {package.name}")
        return 0
    print(f"{TARGET.relative_to(ROOT)} DIFFERS from {package.name}", file=sys.stderr)
    for line in _first_difference(current, produced):
        print(line, file=sys.stderr)
    return 1


def _first_difference(left, right):
    a, b = left.splitlines(), right.splitlines()
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return [f"  line {i + 1}", f"    committed: {x}", f"    schema   : {y}"]
    return [f"  {len(a)} lines committed, {len(b)} generated"]


if __name__ == "__main__":
    sys.exit(main())
