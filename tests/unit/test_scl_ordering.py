# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""Schema-ordered insertion: the table, and the reference it resolves.

A child in the wrong position produces a document that loads here and fails in
DIGSI, so the interesting question is not whether the function returns
something -- it is whether what it returns agrees with IEC 61850-6. Three
different things answer that, and none of them is "the author read the schema
carefully":

1. **The table is regenerated from the schema and compared.** If
   `_content_models.py` has drifted from the XSD by so much as a line, this
   fails and names it. Skipped where the schema package is not present, which
   is everywhere except a machine that holds IEC's distribution -- so it is a
   real check locally and no check at all in CI, and the two below are what
   carry CI.
2. **Every parent in the corpus already agrees with the table.** 830,000
   elements written by three independent vendor tools, none of which has ever
   seen this table. A rule we invented would show up as a real file breaking
   it.
3. **Inserting each legal child into each parent leaves a legal order.** Over
   every pair the table allows, not a list of chosen cases.

`may_contain` and `content_model` are tested beside them, because A8's
``canAdd*`` guards are the first consumer of both.
"""

import subprocess
import sys
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

from py61850.scl import (
    Insert,
    SclDocument,
    content_model,
    may_contain,
    reference_for,
)
from py61850.scl._content_models import CONTENT_MODELS
from py61850.scl.ordering import ANY, _RANKS
from tests.unit import roundtrip

REPO = Path(__file__).resolve().parents[2]
SCHEMA = REPO / "docs" / "IEC61850 files" / "IEC_61850-6.2018.SCL.2007B4.full.zip"
NS = "http://www.iec.ch/61850/2003/SCL"

# A floor, so the sweep below cannot quietly stop sweeping. The table has 82
# parents; if a generator change dropped most of them the count collapses and
# the property would still "pass" over what was left.
MINIMUM_PAIRS = 200


def q(name):
    """A tag in the SCL namespace, as a parsed document carries it."""
    return f"{{{NS}}}{name}"


def ranks_of(parent):
    """Each child's slot index, skipping the ones the model does not place."""
    table = _RANKS.get(parent.tag[parent.tag.index("}") + 1:], {})
    out = []
    for child in parent:
        if not isinstance(child.tag, str):
            continue
        name = child.tag[child.tag.index("}") + 1:] if child.tag.startswith("{") else child.tag
        if name in table:
            out.append((name, table[name]))
    return out


def is_ordered(parent):
    """Whether this parent's children are in an order the table permits."""
    ranks = [r for _, r in ranks_of(parent)]
    return all(a <= b for a, b in zip(ranks, ranks[1:]))


class TestTheTableIsTheSchema(unittest.TestCase):
    """The table is generated, so the check is that it still regenerates."""

    @unittest.skipUnless(SCHEMA.is_file(),
                         "the IEC schema package is not on this machine")
    def test_regenerating_it_from_the_schema_changes_nothing(self):
        """`tools/build_content_models.py --check` must be silent.

        This is the whole provenance claim, made executable. A table that
        someone hand-edited, or that was generated from a different edition,
        fails here and says which line.
        """
        result = subprocess.run(
            [sys.executable, str(REPO / "tools" / "build_content_models.py"),
             "--check"],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0,
                         result.stdout + result.stderr)

    def test_the_table_covers_what_the_later_phases_edit(self):
        """The elements A8-A17 insert into, named rather than counted.

        A regeneration that silently lost half the schema would still be
        self-consistent; this is the list that says which half matters.
        """
        for parent in ("SCL", "IED", "AccessPoint", "Server", "LDevice",
                       "LN0", "LN", "Inputs", "DataSet", "DOI", "SDI",
                       "Communication", "SubNetwork", "ConnectedAP", "GSE",
                       "SMV", "Address", "DataTypeTemplates", "LNodeType",
                       "DOType", "DAType", "EnumType", "ReportControl",
                       "GSEControl", "SampledValueControl", "Substation",
                       "VoltageLevel", "Bay", "Header", "Private"):
            with self.subTest(parent=parent):
                self.assertTrue(content_model(parent),
                                f"{parent} has no content model")

    def test_the_inherited_base_comes_first_everywhere(self):
        """`tBaseElement` contributes ``xs:any``, ``Text?``, ``Private*``, so
        `Private` precedes every type-specific child in SCL.

        Easy to get wrong by hand and the reason the table is generated."""
        for parent, slots in CONTENT_MODELS.items():
            if not may_contain(parent, "Private"):
                continue
            with self.subTest(parent=parent):
                flat = [name for slot in slots for name in slot]
                after = flat[flat.index("Private") + 1:]
                self.assertNotIn("Text", after)
                self.assertEqual(flat[:flat.index("Private")].count(ANY), 1)


class TestTheIecAttributionTravelsWithTheTable(unittest.TestCase):
    """`_content_models.py` is derived from an IEC code component, whose
    licence requires the notice, the conditions and the disclaimer to be
    retained in redistributions and the component to be attributed.

    Pinned here because the header is GENERATED: an edit to the generator that
    dropped the attribution would otherwise ship quietly, and a licence
    obligation is not something to discover from a downstream report.
    """

    NOTICE = REPO / "NOTICE-IEC.txt"
    ATTRIBUTION = "derived from IEC 61850-6:2009/AMD1:2018"

    def test_the_generated_table_attributes_the_standard(self):
        source = (REPO / "src" / "py61850" / "scl"
                  / "_content_models.py").read_text(encoding="utf-8")
        self.assertIn(self.ATTRIBUTION, source)
        self.assertIn("NOTICE-IEC.txt", source)

    def test_the_notice_carries_the_conditions_and_the_disclaimer(self):
        notice = self.NOTICE.read_text(encoding="utf-8")
        self.assertIn(self.ATTRIBUTION, notice)
        self.assertIn("Redistributions of software must retain", notice)
        self.assertIn("ARE DISCLAIMED", notice)
        self.assertIn("www.iec.ch/CCv1", notice)

    def test_the_notice_is_packaged(self):
        """It has to reach whoever installs the wheel, not only whoever reads
        the repository."""
        pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('license-files = ["LICENSE", "NOTICE-IEC.txt"]', pyproject)
        self.assertIn('"NOTICE-IEC.txt"', pyproject.split("[tool.hatch.build.targets.sdist]")[1])


class TestTheCorpusAgreesWithTheTable(unittest.TestCase):
    """Three vendor tools, 830,000 elements, and none of them has seen this
    table. If the ordering here were invented, a real file would break it."""

    FIXTURES = ("siemens.scd", "mixed.scd", "sel.scd")

    def test_every_parent_in_every_corpus_file_is_already_in_order(self):
        checked = 0
        for name in self.FIXTURES:
            path = roundtrip.CORPUS / name
            if not path.is_file():
                # Excluded from the sdist, like the round-trip corpus itself.
                continue
            doc = SclDocument.parse(path)
            for element in doc.root.iter():
                if not isinstance(element.tag, str) or not len(element):
                    continue
                checked += 1
                if not is_ordered(element):
                    self.fail(f"{name}: <{element.tag}> writes its children as "
                              f"{[n for n, _ in ranks_of(element)]}, which the "
                              f"table does not allow")
        if checked:
            self.assertGreater(checked, 100_000, "the corpus sweep shrank")


class TestInsertingEachChildIntoEachParent(unittest.TestCase):
    """The property, over every pair the table allows."""

    def build(self, parent_name):
        """``parent_name`` holding one child from each of its slots, in order.

        The densest legal parent the table describes, which is the arrangement
        a new child has the most ways to land wrongly in.
        """
        parent = ET.Element(q(parent_name))
        for slot in content_model(parent_name):
            for name in slot:
                if name != ANY:
                    parent.append(ET.Element(q(name)))
                    break
        return parent

    def test_inserting_a_legal_child_leaves_a_legal_order(self):
        pairs = 0
        for parent_name, slots in CONTENT_MODELS.items():
            for slot in slots:
                for child_name in slot:
                    if child_name == ANY:
                        continue
                    pairs += 1
                    with self.subTest(parent=parent_name, child=child_name):
                        parent = self.build(parent_name)
                        node = ET.Element(q(child_name))
                        reference = reference_for(parent, node)
                        at = len(parent) if reference is None else list(parent).index(reference)
                        parent.insert(at, node)
                        self.assertTrue(
                            is_ordered(parent),
                            f"<{child_name}> into <{parent_name}> gave "
                            f"{[n for n, _ in ranks_of(parent)]}")
        self.assertGreater(pairs, MINIMUM_PAIRS)

    def test_inserting_into_an_empty_parent_appends(self):
        for parent_name in CONTENT_MODELS:
            with self.subTest(parent=parent_name):
                self.assertIsNone(
                    reference_for(ET.Element(q(parent_name)), q("Private")))


class TestWhatTheResolverAnswers(unittest.TestCase):
    """The cases the sweep above cannot state in words."""

    def test_it_puts_a_dataset_before_the_doi_that_follows_it(self):
        ln0 = ET.Element(q("LN0"))
        for name in ("Private", "DOI", "Inputs"):
            ln0.append(ET.Element(q(name)))
        reference = reference_for(ln0, "DataSet")
        self.assertEqual(reference.tag, q("DOI"))

    def test_a_private_goes_before_every_type_specific_child(self):
        ied = ET.Element(q("IED"))
        ied.append(ET.Element(q("AccessPoint")))
        self.assertEqual(reference_for(ied, "Private").tag, q("AccessPoint"))

    def test_an_unordered_slot_appends_after_its_own_members(self):
        """``tDOI`` is a repeated ``xs:choice``: `SDI` and `DAI` share a slot,
        so a new `DAI` goes after both and not between them."""
        doi = ET.Element(q("DOI"))
        for name in ("SDI", "DAI", "SDI"):
            doi.append(ET.Element(q(name)))
        self.assertIsNone(reference_for(doi, "DAI"))

    def test_an_unordered_slot_still_respects_the_slots_before_it(self):
        doi = ET.Element(q("DOI"))
        for name in ("Private", "SDI"):
            doi.append(ET.Element(q(name)))
        self.assertEqual(reference_for(doi, "Text").tag, q("Private"))

    def test_an_unlisted_parent_appends(self):
        """A vendor element, or anything below a `Private`. Refusing would
        make the resolver useless on the files this library exists to read."""
        vendor = ET.Element("{http://example.invalid/v}Application")
        vendor.append(ET.Element("{http://example.invalid/v}Setting"))
        self.assertIsNone(reference_for(vendor, "Whatever"))

    def test_a_child_the_parent_may_not_hold_appends(self):
        ln0 = ET.Element(q("LN0"))
        ln0.append(ET.Element(q("DOI")))
        self.assertIsNone(reference_for(ln0, "Bay"))

    def test_a_foreign_namespace_child_lands_in_the_any_slot(self):
        """``tBaseElement`` opens with ``xs:any namespace="##other"``, so
        foreign content precedes `Text` and `Private` rather than having no
        position at all."""
        ied = ET.Element(q("IED"))
        for name in ("Private", "AccessPoint"):
            ied.append(ET.Element(q(name)))
        reference = reference_for(ied, "{http://example.invalid/v}Thing")
        self.assertEqual(reference.tag, q("Private"))

    def test_the_reference_is_always_an_element_never_a_comment(self):
        """A comment neither moves the reference nor becomes one.

        Walking back over a comment to keep it with the element it introduces
        was written and then removed: it is right only where a comment
        captions what FOLLOWS it and exactly wrong for one that trails what
        precedes it, and the markup does not say which. Returning a node whose
        `tag` is a function would also be a trap for every caller that looks
        at it. See Q18; the corpus's 32 comments all sit inside a `Private`,
        where no ordering applies at all.
        """
        ln0 = ET.Element(q("LN0"))
        ln0.append(ET.Element(q("Private")))
        ln0.append(ET.Comment(" the inputs "))
        ln0.append(ET.Element(q("Inputs")))
        reference = reference_for(ln0, "DOI")
        self.assertIs(reference, ln0[2])
        self.assertEqual(reference.tag, q("Inputs"))

    def test_a_comment_does_not_hide_the_child_after_it(self):
        """The scan skips comments rather than stopping at them, so an element
        the model places later is still found."""
        ln0 = ET.Element(q("LN0"))
        ln0.append(ET.Comment(" leading "))
        ln0.append(ET.Element(q("Inputs")))
        self.assertEqual(reference_for(ln0, "Private").tag, q("Inputs"))

    def test_a_tag_may_be_given_as_a_name_an_element_or_a_clark_tag(self):
        ln0 = ET.Element(q("LN0"))
        ln0.append(ET.Element(q("DOI")))
        for tag in ("DataSet", q("DataSet"), ET.Element(q("DataSet"))):
            with self.subTest(tag=tag):
                self.assertEqual(reference_for(ln0, tag).tag, q("DOI"))


class TestMayContain(unittest.TestCase):
    def test_it_answers_from_the_schema(self):
        self.assertTrue(may_contain("LN0", "DataSet"))
        self.assertTrue(may_contain("DOI", "SDI"))
        self.assertTrue(may_contain("Bay", "ConductingEquipment"))
        self.assertFalse(may_contain("LN0", "Bay"))
        self.assertFalse(may_contain("DataSet", "IED"))

    def test_an_unlisted_parent_contains_nothing(self):
        self.assertFalse(may_contain("NotAnSclElement", "Private"))

    def test_a_private_accepts_foreign_content(self):
        """``tPrivate`` is ``xs:any``: holding another namespace's elements is
        what it is for, so a guard that refused them would refuse the point of
        the element. Both tags have to be namespaced for that to be visible."""
        self.assertTrue(may_contain(q("Private"), "{http://example.invalid/v}Thing"))
        self.assertFalse(may_contain(q("Private"), q("IED")))
        self.assertFalse(may_contain(q("Private"), "Thing"))

    def test_it_takes_a_tag_as_readily_as_a_name(self):
        self.assertTrue(may_contain(q("LN0"), q("DataSet")))


class TestItComposesWithTheEditLayer(unittest.TestCase):
    """The resolver and `Insert` are separate on purpose; this is the seam
    where a caller puts them back together, on a real vendor export."""

    @classmethod
    def setUpClass(cls):
        cls.path = roundtrip.CORPUS / "siemens.scd"
        if not cls.path.is_file():
            raise unittest.SkipTest("siemens.scd not present (sdist install)")

    def test_a_resolved_insertion_lands_in_order_and_still_inverts(self):
        doc = SclDocument.parse(self.path)
        original = doc.to_bytes()
        ln0 = next(el for el in doc.root.iter() if el.tag == q("LN0"))
        node = ET.Element(q("DataSet"), {"name": "DS_NEW"})

        undo = doc.apply_edit(Insert(ln0, node, reference_for(ln0, node)))
        self.assertTrue(is_ordered(ln0),
                        f"{[n for n, _ in ranks_of(ln0)]}")
        self.assertNotEqual(doc.to_bytes(), original)

        doc.apply_edit(undo)
        self.assertEqual(doc.to_bytes(), original)


if __name__ == "__main__":
    unittest.main()
