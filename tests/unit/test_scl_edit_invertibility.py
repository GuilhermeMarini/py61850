# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""`undo(apply(e))` is the document it started as -- over generated sequences.

`test_scl_edit.py` states what each primitive does, one case at a time. This
states the one thing that has to be true of all of them together, and it
generates the sequences rather than listing them, because **the interesting
failures are in the ordering between edits, not in single edits.** A5's 57
tests already cover the singles.

**The comparison is bytes, not values.** That is not belt and braces: it is
the only assertion that can see Q13. `ET` keeps attributes in a plain dict, so
an attribute deleted from the middle of an element and set again lands at the
END -- every value right, and the file changed. A test comparing the model
would pass. This one composes with Stage 0's round-trip guarantee instead: a
document that has been edited and fully undone serialises to the same bytes as
the file it was parsed from, down to the order the attributes are written in.

## What is asserted, and where

| | Document | Sequence | Compared |
|---|---|---|---|
| every step inverts | synthetic | 200 edits | around **every** edit |
| the same sequence as one compound edit | synthetic | the same 200 | endpoints |
| a rejected edit rolls the list back | synthetic | 40 edits + one refused | endpoints |
| the parent map still agrees | synthetic | 200 edits | every child in the tree |
| a hundred edits on a vendor export | `siemens.scd` | 100 edits | endpoints |

The split is measured, not assumed. On `siemens.scd` a `to_bytes()` is 280 ms
and on `sel.scd` 840 ms, so serialising around each of 100 edits would be 56 s
and 170 s against a suite that runs in 14 s. On the synthetic document it is
0.1 ms, so there the dense form is free -- and it is the dense form that names
the edit that broke rather than the sequence that contained it.

`sel.scd` -- 23.5 MB, 433,325 parent-map entries -- is behind
`PY61850_EDIT_SLOW=1`. It differs from `siemens.scd` in size rather than in
shape, and 2.9 s is a fifth of the suite.

## The seeds are in this file, on purpose

Five of them, written down, so CI runs the same five sequences on every
machine and a failure bisects. `PY61850_EDIT_SEED=<n>` runs one instead, and
`=random` draws a clock seed for an exploratory run -- printed in the summary
line like every other, so whatever it finds can be pasted back. See
`editgen.py` for why nothing here draws through `choice` or `shuffle`.
"""

import os
import sys
import tempfile
import unittest
from xml.etree import ElementTree as ET

from py61850.scl import (
    EditRejected,
    Insert,
    Remove,
    SclDocument,
    SetAttributes,
    SetTextContent,
)
from tests.unit import editgen, roundtrip
from tests.unit import scl_fixtures as fx

# Written down rather than drawn, so every machine runs the same sequences.
SEEDS = (20260911, 61850, 7, 1291, 4096)

EDITS = 200
CORPUS_EDITS = 100


def _station():
    """A document with room to edit: three IEDs, a Communication section, the
    templates they name, and two comments.

    Deeper and wider than `test_scl_edit.py`'s fixture, which was built to be
    read beside a single assertion. A generator needs somewhere to go: nesting
    for a move to be a real move, elements carrying four and five attributes
    for a reordering to be worth anything, and text content that is not all
    whitespace.
    """
    extrefs = (
        '<ExtRef iedName="REL2" ldInst="LD0" lnClass="LLN0" doName="Pos" '
        'daName="stVal" intAddr="VB001" desc="52a"/>'
        '<ExtRef intAddr="VB002" desc="52b"/>'
        '<ExtRef iedName="REL3" ldInst="LD1" lnClass="XCBR" lnInst="1" '
        'doName="Pos" daName="q" intAddr="VB003"/>'
    )
    fcdas = (fx.fcda("LD0", "XCBR", "Pos", "ST", ln_inst="1", da_name="stVal")
             + fx.fcda("LD0", "XCBR", "Pos", "ST", ln_inst="1", da_name="q")
             + fx.fcda("LD1", "MMXU", "TotW", "MX", ln_inst="1"))
    ln0 = fx.ln0(body=(
        f"<Inputs>{extrefs}</Inputs>"
        + fx.dataset("DS1", (fcdas,), desc="the set under test")
        + fx.gse_control("GC1", "DS1", app_id="APP/GC1")
        + fx.report_control("RC1", "DS1")
        + fx.setting_control()))
    body = fx.doi("Pos", body=(
        fx.sdi("origin", body=fx.dai("orCat", val="bay-control"))
        + fx.dai("ctlModel", val="status-only", s_addr="SEL:0x1234")))
    ieds = "\n".join(
        fx.ied(name, desc=f"relay {name}", type="SEL_451", manufacturer="SEL",
               body=fx.access_point(body=(
                   fx.ldevice("LD0", body=ln0 + fx.ln("XCBR", body=body))
                   + fx.ldevice("LD1", desc="metering",
                                body=fx.ln0() + fx.ln("MMXU")))))
        for name in ("REL1", "REL2", "REL3"))
    comm = fx.communication(fx.subnetwork("SUB1", aps=[
        fx.connected_ap(name, body=(
            fx.address(P_IP="192.0.2.%d" % (10 + i),
                       P_IP_SUBNET="255.255.255.0")
            + fx.gse("LD0", "GC1", addr=fx.address(P_MAC_Address="01-0C-CD-01-00-0%d" % i),
                     min_time="4", max_time="1000")))
        for i, name in enumerate(("REL1", "REL2", "REL3"))]))
    types = fx.templates(
        fx.lnode_type("T_LLN0", dos=[("Mod", "T_INC"), ("Beh", "T_INS")]),
        fx.lnode_type("T_XCBR", ln_class="XCBR", dos=[("Pos", "T_DPC")]),
        fx.do_type("T_DPC", cdc="DPC", das=[{"name": "stVal", "fc": "ST"},
                                            {"name": "q", "fc": "ST"},
                                            {"name": "t", "fc": "ST"}]),
        fx.do_type("T_INC", cdc="INC", das=[{"name": "stVal", "fc": "ST"}]),
        fx.do_type("T_INS", cdc="INS", das=[{"name": "stVal", "fc": "ST"}]),
        fx.da_type("T_ORG", bdas=[{"name": "orCat", "bType": "Enum"}]),
        fx.enum_type("T_ORCAT", values=[(0, "not-supported"), (1, "bay-control")]),
    )
    return fx.scl(fx.comment(" written by nobody, for a test "),
                  fx.header(), comm, ieds, fx.comment(" the templates "), types,
                  version="2007", revision="B", release="4")


def _report(run):
    """Printed on every run, pass or fail. `unittest` says nothing about a
    test that passed, and "OK" is not evidence that a hundred generated edits
    inverted byte for byte -- which is the whole content of this phase."""
    print("\n" + editgen.summarise(run), file=sys.stderr)


class TestGeneratedSequences(unittest.TestCase):
    """The synthetic document, where serialising around every edit is free."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmp.cleanup)
        cls.path = fx.write(cls._tmp.name, "station.scd", _station())

    def setUp(self):
        self.doc = SclDocument.parse(self.path)

    def check(self, run, per_step):
        _report(run)
        if run.failure is not None:
            self.fail(editgen.explain(run, self.path, per_step))

    def test_every_edit_in_a_generated_sequence_inverts_on_the_spot(self):
        """Apply, undo, compare, redo, compare -- around each of 200 edits.

        The strongest form of the property and the one that names the culprit:
        a sequence that only compares its endpoints says that something among
        two hundred edits did not come back.
        """
        for seed in editgen.seeds(SEEDS):
            with self.subTest(seed=seed):
                self.doc = SclDocument.parse(self.path)
                run = editgen.stack_run(self.doc, seed, EDITS, per_step=True,
                                        document="station.scd")
                self.check(run, per_step=True)

    def test_the_generator_produces_every_kind_of_edit(self):
        """Guards against a property that passes by doing nothing interesting.

        A generator that drew only text changes would satisfy every assertion
        here and prove none of what the phase is for.

        One seed, like the two below: generating a sequence costs about what
        checking it does, and the claim above is the one worth paying five
        times for.
        """
        for seed in editgen.seeds(SEEDS[:1]):
            with self.subTest(seed=seed):
                self.doc = SclDocument.parse(self.path)
                run = editgen.stack_run(self.doc, seed, EDITS, per_step=False,
                                        document="station.scd")
                self.assertIsNone(run.failure)
                missing = [k for k in editgen.KINDS if not run.counts[k]]
                self.assertEqual(missing, [], f"never generated: {missing}")
                self.assertEqual(len(run.ops), EDITS)

    def test_the_same_sequence_inverts_as_one_compound_edit(self):
        """The other path: one `apply_edit`, one inverse, inverted in reverse.

        A list of edits is itself an edit, and the sub-lists here are nested,
        so the inverse is nested too. That is what makes "subscribe this
        ExtRef" one entry in a history rather than the several primitives it
        expands to -- and it is a different code path from unwinding a stack,
        which is what `pac-ct`'s journal will do instead.
        """
        for seed in editgen.seeds(SEEDS[:1]):
            with self.subTest(seed=seed):
                self.doc = SclDocument.parse(self.path)
                generated = editgen.stack_run(self.doc, seed, EDITS,
                                              per_step=False,
                                              document="station.scd")
                self.check(generated, per_step=False)
                run = editgen.compound_run(self.doc, generated.edits, seed,
                                           document="station.scd")
                run.ops = generated.ops
                run.counts = generated.counts
                self.check(run, per_step=False)

    def test_the_parent_map_still_agrees_with_the_tree(self):
        """Byte identity does not prove the map survived, and a map that
        disagrees with the tree is the next phase's mystery rather than this
        one's.

        `parent_of` repairs a MISSING entry by rebuilding -- A5 chose a 98 ms
        recovery over a wrong answer -- so what this can catch is an entry
        pointing at a parent the element no longer has. That is the direction
        a move or a rollback would break it in.
        """
        for seed in editgen.seeds(SEEDS[:1]):
            with self.subTest(seed=seed):
                self.doc = SclDocument.parse(self.path)
                run = editgen.stack_run(self.doc, seed, EDITS, per_step=False,
                                        document="station.scd")
                self.assertIsNone(run.failure)
                self.assertEqual(editgen.parent_map_disagreements(self.doc), [])


class TestARejectedEditLeavesTheFileAlone(unittest.TestCase):
    """A rejected edit changes nothing -- which is half of invertibility and
    the same machinery.

    Rolling a list back runs through the inverses of the edits that already
    applied, so a bug in an inverse shows up here as a document that is not
    the one it started as. Both kinds of refusal are generated: one caught by
    the static check before anything is applied at all, and one that is only
    discovered part way through and has to be backed out.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmp.cleanup)
        cls.path = fx.write(cls._tmp.name, "station.scd", _station())

    def bad_edits(self, doc):
        """Edits that are refused whatever the tree has become by the time
        they are reached. Nothing conditional: an edit that might be accepted
        after the edits before it would make this test flap."""
        parent = doc.root[0]
        return [
            (Remove(ET.Element("Nope")), "a node that is not in the document"),
            (Insert(parent, ET.Element("X"), ET.Element("Y")),
             "a reference that is not a child"),
            (Insert(parent, doc.root, None), "the root element"),
            (SetAttributes(parent, {"bad name": "x"}), "a malformed name"),
            (SetTextContent(ET.Element("Nope"), "x"), "a detached element"),
        ]

    def test_a_refused_edit_inside_a_list_rolls_the_rest_back(self):
        for seed in editgen.seeds(SEEDS[:2]):
            doc = SclDocument.parse(self.path)
            original = doc.to_bytes()
            generated = editgen.stack_run(doc, seed, 40, per_step=False,
                                          document="station.scd")
            self.assertIsNone(generated.failure)
            for bad, why in self.bad_edits(doc):
                with self.subTest(seed=seed, refused=why):
                    at = (seed + len(why)) % (len(generated.edits) + 1)
                    compound = (list(generated.edits[:at]) + [bad]
                                + list(generated.edits[at:]))
                    with self.assertRaises(EditRejected):
                        doc.apply_edit(compound)
                    self.assertEqual(
                        doc.to_bytes(), original,
                        f"a list refused at {at} ({why}) left the document "
                        f"changed")
                    self.assertEqual(editgen.parent_map_disagreements(doc), [])


class CorpusInvertibility:
    """A hundred generated edits against a real vendor export.

    Mixed into a `TestCase` per fixture below, the way `test_scl_roundtrip.py`
    does it, so `unittest` collects the fixtures that exist and not an
    abstract one with no file to read.

    **Endpoints only.** A `to_bytes()` here is 280 ms on `siemens.scd` and
    840 ms on `sel.scd`; serialising around each of a hundred edits would be
    56 s and 170 s. What the dense form buys -- naming the edit that broke --
    is bought on the synthetic document instead, and the shrinker is what
    answers it if this one ever fails.
    """

    FIXTURE = None
    SEED = SEEDS[0]

    @classmethod
    def setUpClass(cls):
        cls.path = roundtrip.CORPUS / cls.FIXTURE
        if not cls.path.is_file():
            # The corpus is excluded from the sdist -- 44 MB of vendor SCDs is
            # a development artefact -- so a suite run from an unpacked sdist
            # legitimately has nothing to read here.
            raise unittest.SkipTest(f"{cls.FIXTURE} not present (sdist install)")

    def test_a_hundred_edits_invert_as_a_stack_and_as_one_edit(self):
        """Both application modes, and the parent map afterwards.

        One test method rather than three, because each one would otherwise
        pay its own parse and its own first serialisation -- 460 ms of the
        1.6 s this costs on `siemens.scd`. The stack run leaves the document
        byte-identical to what it was parsed as, which is exactly the state
        the compound run needs to start from, so sharing it is free.
        """
        doc = SclDocument.parse(self.path)
        for seed in editgen.seeds((self.SEED,)):
            run = editgen.stack_run(doc, seed, CORPUS_EDITS, per_step=False,
                                    document=self.FIXTURE)
            _report(run)
            if run.failure is not None:
                # A small budget: each replay is a parse and two
                # serialisations of a multi-megabyte file.
                self.fail(editgen.explain(run, self.path, False, budget=40))

            compound = editgen.compound_run(doc, run.edits, seed,
                                            document=self.FIXTURE)
            compound.ops = run.ops
            compound.counts = run.counts
            _report(compound)
            if compound.failure is not None:
                self.fail(editgen.explain(compound, self.path, False, budget=40))

            self.assertEqual(editgen.parent_map_disagreements(doc), [])


class SiemensScdInvertibility(CorpusInvertibility, unittest.TestCase):
    FIXTURE = "siemens.scd"


@unittest.skipUnless(os.environ.get("PY61850_EDIT_SLOW"),
                     "PY61850_EDIT_SLOW=1 to include sel.scd (23.5 MB)")
class SelScdInvertibility(CorpusInvertibility, unittest.TestCase):
    """The biggest file in the corpus, and the slowest: 433,325 parent-map
    entries and 840 ms a serialisation. It differs from `siemens.scd` in size
    rather than in shape, so it is opt-in rather than a fifth of the suite."""

    FIXTURE = "sel.scd"


if __name__ == "__main__":
    unittest.main()
