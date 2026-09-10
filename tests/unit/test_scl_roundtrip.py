# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""A document parsed and written back out with no edit is the file it came from.

This is the specification for the whole write side of the library, written
before the write side exists. Every case here states one thing that must
survive a zero-edit round trip; the ones that do not survive today are marked
`expectedFailure` and listed in `KNOWN_GAPS` with the reason and the work that
closes them. **Closing one turns its case into an unexpected success, which
`unittest` reports as a failure** -- so the table below cannot rot: the suite
goes red the moment reality moves past it, in either direction.

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

Four cosmetic exceptions are permitted and no more -- attribute quote style,
empty-element spacing, CDATA boundaries and the spelling of a numeric
character reference. `roundtrip.normalise` erases exactly those, counts what
it erased, and touches nothing else. Everything else is a gap, including two
that no phase of the current plan closes yet and that were taken in scope
deliberately: **line endings** and the **XML declaration**.

Line endings are the one worth stating a reason for. All three vendor
fixtures are CRLF files; XML end-of-line normalisation is mandatory and
happens inside the parser, so `\\r\\n` is gone before a tree exists and no
amount of parser hardening can bring it back. Calling that cosmetic would
mean every save hands DIGSI a diff on all 660,549 lines of `sel.scd` -- which
is exactly the outcome the fidelity guarantee exists to prevent -- and it
would quietly rewrite the SEL settings text embedded in that file's CDATA
sections, whose own CRLF breaks are part of the payload. So the writer will
record the line ending it read and re-emit it, and until it does, this fails.
"""

import functools
import sys
import unittest

from . import roundtrip

# Which case fails today, for which fixture, and what closes it. Every entry
# is measured, not guessed. An entry that is wrong in either direction breaks
# the suite: a gap that is not listed fails, and a listed gap that has been
# closed is an unexpected success.
KNOWN_GAPS = {
    "sel.scd": {
        "comments": "29 comments dropped -- the parser inserts none (A2)",
        "namespace_prefixes": "the default and esel: rewritten to ns0: and ns1: (A3)",
        "line_endings": "660,549 CRLF lines written back as LF (the writer)",
        "xml_declaration": "quote style and the case of the encoding name (the writer)",
        "bytes": "the four above",
    },
    "mixed.scd": {
        "comments": "1 comment dropped (A2)",
        "namespace_declarations": "IEC_60870_5_104 and siebase are declared and never used (A3)",
        "namespace_prefixes": "four of the seven rewritten to ns0:, ns2:, ns3:, ns4: (A3)",
        "line_endings": "379,313 CRLF lines written back as LF (the writer)",
        "xml_declaration": "quote style and the case of the encoding name (the writer)",
        "bytes": "the five above",
    },
    "siemens.scd": {
        "comments": "2 comments dropped (A2)",
        "namespace_declarations": "IEC_60870_5_104 and siebase are declared and never used (A3)",
        "namespace_prefixes": "three of the six rewritten to ns0:, ns2:, ns3: (A3)",
        "line_endings": "216,700 CRLF lines written back as LF (the writer)",
        "xml_declaration": "quote style and the case of the encoding name (the writer)",
        "bytes": "the five above",
    },
    "namespaces.scd": {
        "namespace_declarations": "xmlns:sel is declared and never used (A3)",
        "namespace_prefixes": "the default and sxy: rewritten to ns0: and ns1: (A3)",
        "line_endings": "the trailing newline after </SCL> is not written (the writer)",
        "xml_declaration": "quote style and the case of the encoding name (the writer)",
        "bytes": "the four above",
    },
}

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
        """The whole file, once the four permitted exceptions are applied."""
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


def _expect_failure(case, name):
    """Mark one case's method as a known gap, without marking the other three.

    `unittest.expectedFailure` sets a flag ON THE FUNCTION IT IS GIVEN and
    returns that same object -- it wraps nothing. The four fixture classes
    inherit one function per case from `RoundTrip`, so marking `test_comments`
    for `sel.scd` would mark it for every fixture, and the three files whose
    comments do survive would report an unexpected success. Each class gets
    its own object to carry the flag.
    """
    inherited = getattr(case, name)

    @functools.wraps(inherited)
    def marked(self):
        return inherited(self)

    setattr(case, name, unittest.expectedFailure(marked))


for _case in (SelScdRoundTrip, MixedScdRoundTrip, SiemensScdRoundTrip,
              NamespacesScdRoundTrip):
    for _name in KNOWN_GAPS[_case.FIXTURE]:
        _expect_failure(_case, "test_" + _name)
del _case, _name


if __name__ == "__main__":
    unittest.main()
