#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""Extract the 61850-7-3/7-4 type facts an ExtRef restriction check needs.

    python tools/build_nsd_types.py                     # rewrite the table
    python tools/build_nsd_types.py --check             # compare, don't write

**Why any IEC data is needed here at all.** An `ExtRef` states what it expects
with `pDO` and `pDA` -- a data object and a data attribute named in IEC's OWN
namespace, not in the document. `pDO="Ind"` means "a data object of whatever
common data class 61850-7-4 gives `Ind`", and nothing in the SCD says what
that class is. Measured on the reference corpus, resolving `pDO` through the
document's own `DataTypeTemplates` instead covers **0 of siemens.scd's 1,244**
and **256 of mixed.scd's 4,670**: a subscriber declares an `LNodeType` for
what it CONTAINS, and `Ind` lives in the publisher. So the mapping has to come
from the standard, exactly as it does in the reference implementation.

## What it reads, and what it does not ship

IEC's own code-component distributions of the 7-4 (logical node classes) and
7-3 (common data classes) namespace definitions, as the zips they arrive in,
from ``docs/IEC61850 files/``. **That directory is excluded from the
repository.** Two facts are taken out of them and nothing else:

- ``DO_CDC`` -- each data-object name and its common data class, plus the
  dotted path of every sub-data object a class holds (`A.phsA` is a `CMV`).
  1,473 names.
- ``CDC_ATTRIBUTES`` -- for each of the 70 common data classes, the name of
  each data attribute and its BASIC type, with constructed attributes
  flattened into the dotted paths an `ExtRef@pDA` is written in (`mag.f`).

What is left behind is the whole of the rest: every functional constraint,
every presence condition, every enumeration and its literals, the abbreviation
table, the service parameters, the UML identifiers, and all of the
descriptive text the NSD exists to carry.

## Two shapes that differ from the reference's table, and why

The reference ships `nsd.json`: 3.47 MB, one entry per data-object name, each
repeating the FULL attribute dictionary of its class. That is 1,469 copies of
70 distinct dictionaries.

- **Keyed by class, not by data object.** `Ind`, `Alm` and `Wrn` are all `SPS`
  and all carry the same twelve attributes; storing that once is the same
  information, and it is what makes this table 44 kB instead of 3.5 MB.
- **Flattened by data-object name**, as the reference's is. That loses nothing
  here, and it was checked rather than assumed: across the 165 concrete logical
  node classes, 5,225 (class, data object) pairs use **1,138 distinct data
  object names and not one of them carries two different classes.**

## The release

`2007B5`, which is IEC 61850-7-3:2010/7-4:2010 Edition 2.1. It is the release
the reference's own shipped table is built from, so a behavioural comparison
against it is like for like; the corpus files declare SCL `2007B4`, which is
the same edition of a different part.
"""

import argparse
import collections
import pathlib
import sys
import zipfile
from xml.etree import ElementTree as ET

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from py61850.scl._xmlsafe import reject_dtd_in_bytes  # noqa: E402

NSD = "{http://www.iec.ch/61850/2016/NSD}"

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCHEMA_DIR = ROOT / "docs" / "IEC61850 files"
DEFAULT_LN = "IEC_61850-7-4.NSD.2007B5.Light.zip"
DEFAULT_CDC = "IEC_61850-7-3.NSD.2007B5.Light.zip"
TARGET = ROOT / "src" / "py61850" / "scl" / "_nsd_types.py"

# A DataAttribute whose `typeKind` is ENUMERATED has an enumeration for a type
# and `Enum` for a basic type, which is how SCL spells it in a `DA@bType`. The
# NSD names the enumeration; SCL never does at this level.
ENUM = "Enum"


def read_namespace(path):
    """The parsed ``.nsd`` root inside an IEC namespace package."""
    with zipfile.ZipFile(path) as archive:
        names = [n for n in archive.namelist() if n.endswith(".nsd")]
        if len(names) != 1:
            raise SystemExit(f"{path.name}: expected one .nsd, found {names}")
        data = archive.read(names[0])
        # The same rule the library applies to every XML file it reads.
        reject_dtd_in_bytes(data)
        return ET.fromstring(data), names[0]


def data_object_classes(root):
    """``{data object name: CDC name}`` for every concrete logical node class.

    A logical node class inherits through `base`, and the chain runs up into
    the abstract classes that carry `Mod`, `Beh` and `Health`. Resolving it is
    the whole of the work: `GGIO` itself declares ten data objects and holds
    twenty-three.
    """
    declared = {}
    abstract = set()
    for tag in ("LNClass", "AbstractLNClass"):
        for el in root.iter(NSD + tag):
            declared[el.get("name")] = el
            if tag == "AbstractLNClass":
                abstract.add(el.get("name"))

    def resolve(name, seen):
        el = declared.get(name)
        if el is None or name in seen:
            return {}
        base = el.get("base")
        out = dict(resolve(base, seen + (name,))) if base else {}
        for do in el.iter(NSD + "DataObject"):
            out[do.get("name")] = do.get("type")
        return out

    flat = collections.defaultdict(set)
    for name in declared:
        if name in abstract:
            continue
        for do, cdc in resolve(name, ()).items():
            if do and cdc:
                flat[do].add(cdc)

    clashes = {k: sorted(v) for k, v in flat.items() if len(v) > 1}
    if clashes:
        # Not a warning. The table is keyed by data-object name, and a name
        # with two classes would make one of the two silently wrong -- which
        # is exactly the sort of thing a generated table exists to prevent.
        raise SystemExit(f"data object names with more than one CDC: {clashes}")
    return {do: next(iter(cdcs)) for do, cdcs in flat.items()}


def cdc_attributes(root):
    """``{CDC name: {attribute path: basic type}}``.

    Constructed attributes are flattened into the dotted paths an `ExtRef`
    writes in `pDA`: `MV.mag` is an `AnalogueValue`, so the table carries
    `mag.i` and `mag.f` rather than `mag`, which is what the corpus asks for
    -- 128 ExtRefs in `mixed.scd` name `instMag.i` and five name `mag.f`.

    **Sub-data OBJECTS are not flattened**, because `pDA` cannot reach them:
    `WYE.phsA` is a data object, and an `ExtRef` naming it would say so in
    `pDO`.
    """
    constructed = {c.get("name"): c
                   for c in root.iter(NSD + "ConstructedAttribute")}

    def expand(prefix, el, depth):
        kind = el.get("typeKind")
        type_ = el.get("type")
        if kind == "ENUMERATED":
            return {prefix: ENUM}
        if kind == "CONSTRUCTED" and depth < 8:
            node = constructed.get(type_)
            if node is None:
                return {}
            out = {}
            for sub in node.iter(NSD + "SubDataAttribute"):
                out.update(expand(f"{prefix}.{sub.get('name')}", sub, depth + 1))
            return out
        return {prefix: type_} if type_ else {}

    out = {}
    for cdc in root.iter(NSD + "CDC"):
        name = cdc.get("name")
        attributes = out.setdefault(name, {})
        for da in cdc:
            if not da.tag.endswith("}DataAttribute") or not da.get("name"):
                continue
            for path, btype in expand(da.get("name"), da, 0).items():
                # Nine class names are declared three times over -- the setting
                # classes `SPG`, `ING`, `ENG`, `TSG`, `CUG`, `VSG`, `ASG`,
                # `CURVE` and `CSG` each have a variant per setting group kind.
                # They are unioned rather than overwritten, because an SCL
                # `DA@bType` carries no variant and the attribute has to be
                # findable whichever one the file meant. A variant that
                # contradicted another on the same path would make the union
                # a lie, so it stops the build instead.
                if attributes.get(path, btype) != btype:
                    raise SystemExit(
                        f"{name}.{path}: variants disagree, "
                        f"{attributes[path]!r} and {btype!r}")
                attributes[path] = btype
    return out


def sub_data_objects(root):
    """``{CDC name: {sub-data-object name: CDC name}}``.

    `WYE` holds `phsA`, `phsB` and `phsC`, each a `CMV`. An `ExtRef` reaches
    one by writing a dotted `pDO` -- ``pDO="A.phsA"`` -- so the data-object
    table has to carry those paths too.
    """
    out = {}
    for cdc in root.iter(NSD + "CDC"):
        subs = out.setdefault(cdc.get("name"), {})
        for sdo in cdc:
            if sdo.tag.endswith("}SubDataObject") and sdo.get("name"):
                subs[sdo.get("name")] = sdo.get("type")
    return out


def expand_sub_objects(do_cdc, sub_objects):
    """``do_cdc`` with every dotted sub-data-object path added.

    Depth is bounded rather than trusted: a namespace whose classes referred to
    each other in a cycle would otherwise generate for ever.
    """
    out = dict(do_cdc)
    frontier = dict(do_cdc)
    for _ in range(4):
        nxt = {}
        for path, cdc in frontier.items():
            for name, sub_cdc in sub_objects.get(cdc, {}).items():
                if sub_cdc and f"{path}.{name}" not in out:
                    nxt[f"{path}.{name}"] = sub_cdc
        if not nxt:
            break
        out.update(nxt)
        frontier = nxt
    return out


def render(do_cdc, attributes, sources):
    """The generated module, as text."""
    lines = [
        "# SPDX-License-Identifier: AGPL-3.0-or-later",
        "# Copyright (C) 2026 Guilherme Marini",
        "#",
        "# This file is part of py61850. It is free software under the GNU Affero",
        "# General Public License v3 or later; see LICENSE. A commercial licence,",
        "# for use in software you do not wish to release under the AGPL, is",
        "# available from the copyright holder -- see COMMERCIAL.md.",
        '"""What 61850-7-4 and 7-3 say a data object and a data attribute ARE.',
        "",
        "**Generated. Do not edit by hand** -- run ``tools/build_nsd_types.py``,",
        "which is also what ``tests/unit/test_scl_extref.py`` uses to check that this",
        "file still says what the namespace definitions say.",
        "",
        "Extracted from the namespace definitions published by IEC as code",
        "components:",
        "",
    ]
    for name in sources:
        lines.append(f"    {name}")
    lines += [
        "",
        "Those packages are not part of this repository. What is here is two facts",
        "out of them -- which common data class each data object name has, and which",
        "basic type each of a class's data attributes has -- and none of their",
        "functional constraints, presence conditions, enumerations, abbreviations,",
        "service parameters or documentation.",
        "",
        "**This code was derived from IEC 61850-7-3:2010 and IEC 61850-7-4:2020",
        "within modifications permitted in the relevant IEC standard. Please",
        "reproduce this note if possible.** The IEC copyright notice, the",
        "code-component licence conditions and the disclaimer they require are in",
        "``NOTICE-IEC.txt``, which ships with every sdist and wheel. py61850 is not",
        "endorsed by or affiliated with IEC.",
        "",
        "`DO_CDC` is keyed by data-object NAME, flattened across the logical node",
        "classes that declare it -- which loses nothing, because across the 165",
        "concrete classes no data-object name carries two different classes.",
        "",
        "`CDC_ATTRIBUTES` is keyed by CLASS, and its attribute paths are dotted the",
        "way an ``ExtRef@pDA`` writes them: a constructed attribute is flattened, so",
        "`MV` carries `mag.i` and `mag.f` rather than `mag`. An enumerated attribute",
        f"is ``{ENUM}``, which is how SCL spells it in a ``DA@bType``.",
        '"""',
        "",
        "DO_CDC = {",
    ]
    for name in sorted(do_cdc):
        lines.append(f'    "{name}": "{do_cdc[name]}",')
    lines += ["}", "", "CDC_ATTRIBUTES = {"]
    for cdc in sorted(attributes):
        lines.append(f'    "{cdc}": {{')
        for path in sorted(attributes[cdc]):
            lines.append(f'        "{path}": "{attributes[cdc][path]}",')
        lines.append("    },")
    lines += ["}", ""]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ln-package", default=str(SCHEMA_DIR / DEFAULT_LN),
                        help="the IEC 61850-7-4 namespace code-component zip")
    parser.add_argument("--cdc-package", default=str(SCHEMA_DIR / DEFAULT_CDC),
                        help="the IEC 61850-7-3 namespace code-component zip")
    parser.add_argument("--check", action="store_true",
                        help="compare against the committed table instead of writing it")
    args = parser.parse_args(argv)

    packages = [pathlib.Path(args.ln_package), pathlib.Path(args.cdc_package)]
    for package in packages:
        if not package.is_file():
            print(f"namespace package not found: {package}", file=sys.stderr)
            return 2
    ln_root, _ = read_namespace(packages[0])
    cdc_root, _ = read_namespace(packages[1])

    produced = render(
        expand_sub_objects(data_object_classes(ln_root),
                           sub_data_objects(cdc_root)),
        cdc_attributes(cdc_root),
        [p.name for p in packages])

    if not args.check:
        TARGET.write_text(produced, encoding="utf-8")
        print(f"{TARGET.relative_to(ROOT)}: {produced.count(chr(10))} lines "
              f"from {', '.join(p.name for p in packages)}")
        return 0

    current = TARGET.read_text(encoding="utf-8") if TARGET.is_file() else ""
    if current == produced:
        print(f"{TARGET.relative_to(ROOT)} agrees with "
              f"{', '.join(p.name for p in packages)}")
        return 0
    print(f"{TARGET.relative_to(ROOT)} DIFFERS from the namespace packages",
          file=sys.stderr)
    for line in _first_difference(current, produced):
        print(line, file=sys.stderr)
    return 1


def _first_difference(left, right):
    a, b = left.splitlines(), right.splitlines()
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return [f"  line {i + 1}", f"    committed: {x}", f"    namespace: {y}"]
    return [f"  {len(a)} lines committed, {len(b)} generated"]


if __name__ == "__main__":
    sys.exit(main())
