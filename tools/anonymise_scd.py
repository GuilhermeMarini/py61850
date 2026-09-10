# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""Turn a real vendor SCD into a corpus fixture, by byte-level substitution.

The round-trip test this corpus exists for compares the bytes a document was
loaded from against the bytes it serialises back to. So the anonymiser must
not parse. Anything that reparses and re-serialises replaces the vendor's own
formatting -- its indentation, its attribute order, its quote style, its
line endings -- with ElementTree's, and the round-trip test is then measuring
our serialiser against itself instead of against DIGSI and SEL Architect.

Every substitution here is therefore a `bytes.replace` on the raw file. The
output differs from the vendor's original at the substituted spans and
nowhere else.

The mapping is deliberately NOT in this repository. It names the substation,
the utility, the engineers' Windows accounts and their machines -- publishing
it would undo the anonymisation it describes. It lives beside this script as
an untracked `*.local.json`, ignored by `.gitignore`, and only a machine that
holds the vendor originals can re-run this.

Usage:

    python tools/anonymise_scd.py --map tools/scd_identities.local.json
    python tools/anonymise_scd.py --map tools/scd_identities.local.json --check

The first form writes the fixtures and reports what it replaced. The second
re-reads what was written and proves two things: that no source token
survived anywhere, and that the substitution changed no XML structure --
same element count, same attribute count, same comment count, same tag
sequence as the vendor original.
"""

import argparse
import json
import pathlib
import sys
import xml.etree.ElementTree as ET


def load_mapping(path):
    """Read the mapping, and order each file's substitutions longest-first.

    Order matters because the tokens nest. A `C:\\Users\\<account>` path
    contains the account name on its own, and on one of these machines the
    Windows account is named after the utility, so the same string is both.
    Replacing the longest source first means a token is never half-consumed
    by a shorter one that happens to be a substring of it.
    """
    data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    for spec in data["files"].values():
        spec["substitutions"].sort(key=lambda pair: len(pair[0]), reverse=True)
    return data


def substitute(raw, substitutions):
    """Apply every substitution to `raw`, returning the bytes and a count table."""
    counts = []
    for source, replacement in substitutions:
        src = source.encode("utf-8")
        dst = replacement.encode("utf-8")
        n = raw.count(src)
        if n:
            raw = raw.replace(src, dst)
        counts.append((source, replacement, n))
    return raw, counts


def structure_of(raw):
    """The XML shape of a document, as the three numbers that must not move.

    Comments are included in the tree on purpose: they are the thing phase A2
    exists to preserve, and a substitution that ate one would otherwise pass
    unnoticed here.
    """
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    parser.feed(raw)
    root = parser.close()
    elements = comments = attributes = 0
    tags = []
    for node in root.iter():
        if isinstance(node.tag, str):
            elements += 1
            attributes += len(node.attrib)
            tags.append(node.tag)
        else:
            comments += 1
    return {"elements": elements, "attributes": attributes,
            "comments": comments, "tags": tags}


def run(mapping, root, check):
    ok = True
    for name, spec in sorted(mapping["files"].items()):
        source_path = root / spec["source"]
        target_path = root / spec["target"]
        original = source_path.read_bytes()
        anonymised, counts = substitute(original, spec["substitutions"])

        if not check:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_bytes(anonymised)

        print(f"== {name}: {source_path.name} -> {spec['target']}")
        print(f"   {len(original):,} bytes in, {len(anonymised):,} bytes out")
        for source, replacement, n in counts:
            flag = "" if n else "   <-- NO OCCURRENCES, mapping is stale"
            print(f"   {n:6,} x {source!r} -> {replacement!r}{flag}")
            if not n:
                ok = False

        if check:
            written = target_path.read_bytes()
            if written != anonymised:
                print("   FAIL: the tracked fixture is not what this mapping produces")
                ok = False
            for source, _replacement, _n in counts:
                if source.encode("utf-8") in written:
                    print(f"   FAIL: {source!r} survives in the fixture")
                    ok = False
            before, after = structure_of(original), structure_of(written)
            for key in ("elements", "attributes", "comments"):
                if before[key] != after[key]:
                    print(f"   FAIL: {key} {before[key]} -> {after[key]}")
                    ok = False
            if before["tags"] != after["tags"]:
                print("   FAIL: the tag sequence moved")
                ok = False
            print(f"   structure: {after['elements']:,} elements, "
                  f"{after['attributes']:,} attributes, {after['comments']} comments"
                  " -- unchanged")
    return ok


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--map", required=True, help="path to the local mapping JSON")
    ap.add_argument("--check", action="store_true",
                    help="verify the tracked fixtures instead of rewriting them")
    args = ap.parse_args(argv)

    mapping = load_mapping(args.map)
    # Paths in the mapping are relative to the workspace that holds both this
    # repository and the one keeping the vendor originals.
    root = pathlib.Path(mapping.get("root", ".")).expanduser()
    ok = run(mapping, root, args.check)
    print("OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
