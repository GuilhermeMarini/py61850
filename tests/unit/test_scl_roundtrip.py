# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""A document parsed and written back out with no edit is the file it came from.

This is the specification for the whole write side of the library. It was
written before the write side existed, with every case that did not yet pass
marked `expectedFailure` and listed in a `KNOWN_GAPS` table; **that table is
gone, because the gaps are**. All four fixtures now round-trip byte for byte
under the five cosmetic exceptions `roundtrip` erases, and nothing here is
expected to fail.

The mechanism that removed the table is worth keeping in mind for the edit
layer, which will need it again: `unittest` reports a passing
`expectedFailure` as an UNEXPECTED SUCCESS, and an unexpected success fails
the run. A list of known gaps written that way cannot rot -- the suite goes
red the moment reality moves past it, in either direction, and the way to
close a gap is to delete its entry and watch the run go green.

## Why this test is worth more than the library's other 336

`SET_D` in `sellib` holds itself to `parse(b).serialize() == b`, and this is
the same standard applied to XML, for the same reason: the SCD goes back to
DIGSI and to SEL Architect. A library that reformats the 99 % of a file it did
not touch turns every save into a whole-file diff and becomes hostile to the
workflow it exists to serve.

That guarantee is only worth what the corpus is worth. Three of these four
fixtures are real station exports -- their indentation, their attribute order,
their comments, their private namespaces are what has to survive, and no file
this library wrote itself could stand in for them. `tests/fixtures/scl/`
records what was anonymised out of them and what deliberately was not.

## What counts as a difference

Five cosmetic exceptions are permitted and no more -- attribute quote style,
empty-element spacing, empty-element form, CDATA boundaries and the spelling
of a numeric character reference. `roundtrip.normalise` erases exactly those,
counts what it erased, and touches nothing else. Every one of them is a place
where `ElementTree` gives no say; none is observable through an XML parser.

**Three differences were taken in scope deliberately rather than added to
that list**, and the writer closes all three:

**Line endings.** All three vendor fixtures are CRLF files; XML end-of-line
normalisation is mandatory and happens inside the parser, so `\r\n` is gone
before a tree exists and no amount of parser hardening brings it back.
Calling that cosmetic would mean every save hands DIGSI a diff on all 660,549
lines of `sel.scd` -- exactly the outcome the fidelity guarantee exists to
prevent -- and it would quietly rewrite the SEL settings text embedded in
that file's CDATA sections, whose own CRLF breaks are part of the payload. So
`_SourceLayout` records the ending the file was read with and
`SclDocument.to_bytes` re-emits it, over the whole document and last.

**The XML declaration.** `ElementTree` writes
`<?xml version='1.0' encoding='utf-8'?>` and every fixture here writes double
quotes, three of them with `UTF-8` capitalised. The writer keeps the file's
own spelling instead of the serialiser's, by writing the declaration itself.

**The root's attribute order**, the one that shows up nowhere except in
`test_bytes`. `ElementTree` writes the namespace declarations it GENERATES
first, sorted by prefix, ahead of every ordinary attribute; the unused ones
this library re-emits as literal attributes land after them all. Real roots
interleave the two -- `sel.scd` opens `<SCL xmlns:esel=... version=...
xmlns=...>` with the default declaration LAST, and `siemens.scd` opens
`<SCL version=... xmlns=...>` -- so every fixture's line 2 differed even once
all four had every declaration under its own prefix. The writer records the
order and reorders that one tag after serialising. Nothing but the root is
affected: `ElementTree` preserves attribute order everywhere it is not also
emitting a declaration.
"""

import sys
import unittest

from . import roundtrip

_reports = {}


def report_for(name):
    """The measured report for one fixture, computed once and printed once.

    Printed on every run, passing or failing: `unittest` says nothing at all
    about an expected failure, and a phase whose entire deliverable is a list
    of gaps has to put that list somewhere a reader will see it.
    """
    report = _reports.get(name)
    if report is None:
        original, produced = roundtrip.round_trip(name)
        report = _reports[name] = roundtrip.Report(name, original, produced)
        print("\n" + roundtrip.format_report(report), file=sys.stderr)
    return report


class RoundTrip:
    """One fixture's cases. Mixed into a `TestCase` per fixture below.

    Not a `TestCase` itself, so that `unittest` collects the four real
    fixtures and not an abstract fifth with no file to read.
    """

    FIXTURE = None

    @classmethod
    def setUpClass(cls):
        if not (roundtrip.CORPUS / cls.FIXTURE).is_file():
            # The corpus is excluded from the sdist -- 44 MB of vendor SCDs is
            # a development artefact -- so a suite run from an unpacked sdist
            # legitimately has nothing to read here.
            raise unittest.SkipTest(f"{cls.FIXTURE} not present (sdist install)")
        cls.report = report_for(cls.FIXTURE)

    def test_comments(self):
        """Every comment in the input is in the output."""
        lost = self.report.lost_comments
        self.assertEqual([], lost, f"{len(lost)} comments lost, first: "
                                   f"{[c[:60] for c in lost[:3]]}")

    def test_namespace_declarations(self):
        """Every namespace URI declared on the root is declared on the output root.

        Including the ones nothing uses. SEL's private namespace and DIGSI's
        `IEC_60870_5_104` are exactly that shape, and `sxy:` coordinates --
        which both DIGSI and SEL Architect read to draw a single-line diagram
        -- would be the expensive one to lose.
        """
        dropped = self.report.dropped_namespaces
        self.assertEqual([], [p.decode() or "(default)" for p, _ in dropped],
                         "namespace declarations dropped from the root")

    def test_namespace_prefixes(self):
        """Prefixes are the document's own, and none was invented.

        A rewrite to `ns0:` loses no information a parser cares about and
        every bit of information a person reading the file does.
        """
        self.assertEqual(
            {p.decode(): u.decode() for p, u in self.report.before.prefix_map.items()},
            {p.decode(): u.decode() for p, u in self.report.after.prefix_map.items()},
            "the root's prefix -> URI mapping changed")
        self.assertEqual([], [p.decode() for p in self.report.after.invented_prefixes],
                         "the serialiser invented prefixes")

    def test_line_endings(self):
        """The file is written back with the line endings it was read with.

        Including the last one. A file that ends `</SCL>\\n` must come back
        ending that way; `ElementTree` writes no trailing newline at all, so
        only the hand-written fixture -- the one file here that has one --
        can catch it.
        """
        before, after = self.report.before, self.report.after
        self.assertEqual(
            (before.dominant_eol, before.final_newline),
            (after.dominant_eol, after.final_newline),
            f"read {before.crlf:,} CRLF / {before.lf:,} LF"
            f"{' with' if before.final_newline else ' without'} a final newline, "
            f"wrote {after.crlf:,} CRLF / {after.lf:,} LF"
            f"{' with' if after.final_newline else ' without'} one")

    def test_xml_declaration(self):
        """The XML declaration is reproduced as it was written."""
        self.assertEqual(self.report.before.declaration, self.report.after.declaration)

    def test_bytes(self):
        """The whole file, once the five permitted exceptions are applied."""
        if self.report.equal:
            return
        offset, left, right = self.report.first_difference
        self.fail(f"{self.report.differing_lines:,} lines differ; "
                  f"first at byte {offset:,}\n  in : {left!r}\n  out: {right!r}")


class SelScdRoundTrip(RoundTrip, unittest.TestCase):
    """AcSELerator Architect, 23.5 MB, 29 comments, 7 CDATA sections."""
    FIXTURE = "sel.scd"


class MixedScdRoundTrip(RoundTrip, unittest.TestCase):
    """A DIGSI export containing SEL relays -- seven namespaces on the root."""
    FIXTURE = "mixed.scd"


class SiemensScdRoundTrip(RoundTrip, unittest.TestCase):
    """IEC 61850 System Configurator, 7.1 MB."""
    FIXTURE = "siemens.scd"


class NamespacesScdRoundTrip(RoundTrip, unittest.TestCase):
    """Hand-written: `xmlns:sel` declared and unused, `sxy:` coordinates used."""
    FIXTURE = "namespaces.scd"


if __name__ == "__main__":
    unittest.main()
