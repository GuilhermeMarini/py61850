# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""`py61850.scl.substation` -- renaming a container, and removing a piece of
the primary system.

**This is the first module in the package with NO corpus behind it at all.**
`sel.scd`, `mixed.scd` and `siemens.scd` carry none of the twenty-three
Substation-section element names, in any namespace. `TestNoCorpusMaterial`
asserts exactly that, by scanning the bytes rather than by asking the library
-- so the day a Substation-carrying fixture is added, this module's guard
story changes loudly instead of silently. **There is deliberately no
`TestCorpus` class**, and its absence is a measurement rather than an
oversight.

What the fixtures are shaped by instead is IEC's own published example --
`Eng POC.ssd`, from `IEC_TR_61850-90-30.SSD.2024A1`, which is IEC copyright,
is excluded from this repository and ships with nothing. It was read to check
the conventions, and :func:`station` reproduces its SHAPE from scratch:

- a `Substation` holding one `VoltageLevel` holding three `Bay`;
- **two `ConnectivityNode` of the SAME leaf name in different bays** -- so a
  `cNodeName` alone cannot identify one and only the full path can;
- **two `Terminal` that sit in one bay while naming another**, which is what
  makes a location sweep wrong in both directions and is the single
  measurement the module's design rests on;
- a `NeutralPoint`, which is typed `tTerminal` and carries every attribute a
  `Terminal` does;
- a `Line`, a `Process` and an `IED` at the document root, because
  `SubstationKey` covers the first two and pointedly not the third.

**The assertions count the RESULT, not the edit list.** Q33 §9, Q34 §4 and §5
and Q35 §7 are four phases running in which reading back what the document
says after applying was the only method that found the defect, and a phase
with no corpus has even less business trusting an edit list it wrote itself.
:meth:`_Base.applied` is that: build, apply, re-read.
"""

import re
import tempfile
import unittest
from xml.etree import ElementTree as ET

from py61850.scl import (
    CONTAINER_NAME_ATTRIBUTES,
    PROCESS_SECTIONS,
    TERMINAL_ELEMENTS,
    EditRejected,
    Insert,
    Remove,
    SclDocument,
    SetAttributes,
    remove_process_element,
    strip_ns,
    update_bay,
    update_substation,
    update_voltage_level,
)
from tests.unit import roundtrip
from tests.unit import scl_fixtures as fx


# Every element name the Substation section can hold -- the fifteen obvious
# ones and the eight the brief for this phase did not list. All twenty-three
# are checked against the corpus below.
SECTION_NAMES = (
    "Substation", "VoltageLevel", "Bay", "Terminal", "ConnectivityNode",
    "LNode", "PowerTransformer", "ConductingEquipment", "Process", "Line",
    "DOS", "SDS", "DAS", "Function", "SubFunction", "NeutralPoint",
    "TransformerWinding", "SubEquipment", "GeneralEquipment", "EqFunction",
    "EqSubFunction", "TapChanger", "Voltage",
)


def station(bay0="B0", bay1="B1", bay2="B2", substation="S1", level="V1",
            extra_root="", tail=""):
    """The IEC example's shape, built from nothing.

    ``S1/V1`` holds three bays. ``B0`` carries two connectivity nodes and two
    pieces of conducting equipment; ``B1`` and ``B2`` each carry a node called
    ``L1``. Of ``B0``'s four terminals, two point at its own ``N1`` and two
    point into ``B1`` and ``B2`` -- those two are the ones that sit in one bay
    and name another.

    The `NeutralPoint` under the transformer winding points at ``B0/N2``,
    which no `Terminal` names, so it is the only thing keeping that node
    alive.
    """
    def path(*segments):
        return "/".join(segments)

    n1 = path(substation, level, bay0, "N1")
    n2 = path(substation, level, bay0, "N2")
    l1_in_b1 = path(substation, level, bay1, "L1")
    l1_in_b2 = path(substation, level, bay2, "L1")

    transformer = fx.power_transformer(
        "PT1", body=fx.transformer_winding(
            "W1", body=fx.terminal(n2, "N2", name="NP", tag="NeutralPoint",
                                   substation_name=substation,
                                   voltage_level_name=level, bay_name=bay0)))

    equipment_1 = fx.conducting_equipment("E1", body=(
        fx.terminal(l1_in_b1, "L1", name="T1", substation_name=substation,
                    voltage_level_name=level, bay_name=bay1)
        + fx.terminal(n1, "N1", name="T2", substation_name=substation,
                      voltage_level_name=level, bay_name=bay0)))
    equipment_2 = fx.conducting_equipment("E2", body=(
        fx.terminal(l1_in_b2, "L1", name="T1", substation_name=substation,
                    voltage_level_name=level, bay_name=bay2)
        + fx.terminal(n1, "N1", name="T2", substation_name=substation,
                      voltage_level_name=level, bay_name=bay0)))

    # Schema order inside a Bay: ConductingEquipment, then ConnectivityNode,
    # then Function. A7's table places all three.
    b0 = fx.bay(bay0, body=(equipment_1 + equipment_2
                            + fx.connectivity_node("N1", n1)
                            + fx.connectivity_node("N2", n2)
                            + fx.substation_function("F1")))
    b1 = fx.bay(bay1, body=fx.connectivity_node("L1", l1_in_b1))
    b2 = fx.bay(bay2, body=fx.connectivity_node("L1", l1_in_b2))

    vl = fx.voltage_level(level, body=transformer + b0 + b1 + b2)
    section = fx.substation(substation, body=vl)
    return fx.scl(fx.header(), section, extra_root, tail)


class _Base(unittest.TestCase):
    def doc(self, text=None, name="a.ssd"):
        if not hasattr(self, "_dir"):
            self._dir = tempfile.TemporaryDirectory()
            self.addCleanup(self._dir.cleanup)
        return SclDocument.parse(fx.write(self._dir.name, name,
                                          text if text is not None
                                          else station()))

    # -- reading the document back, never the edit list ---------------------

    def find(self, doc, local_name, **attrs):
        """The one element of this name whose attributes match, or a
        failure naming what was there instead."""
        found = [el for el in self.all(doc, local_name)
                 if all(el.get(k) == v for k, v in attrs.items())]
        if len(found) != 1:
            raise AssertionError(
                f"wanted exactly one {local_name} with {attrs}, got "
                f"{len(found)}")
        return found[0]

    def all(self, doc, local_name):
        return [el for el in doc.root.iter()
                if isinstance(el.tag, str) and strip_ns(el.tag) == local_name]

    def applied(self, doc, edits):
        """Apply and hand back the inverse. Everything after this reads the
        tree, which is the point."""
        return doc.apply_edit(edits)

    def paths(self, doc):
        return sorted(el.get("pathName")
                      for el in self.all(doc, "ConnectivityNode"))

    def terminals(self, doc):
        """``{(tag, cNodeName, where it is): (connectivityNode, the three
        container names)}`` -- everything a rename could touch, keyed by what
        a rename must NOT touch."""
        out = {}
        for tag in TERMINAL_ELEMENTS:
            for el in self.all(doc, tag):
                parent = doc.parent_of(el)
                key = (tag, el.get("name"), parent.get("name"))
                out[key] = (el.get("connectivityNode"),
                            el.get("substationName"),
                            el.get("voltageLevelName"),
                            el.get("bayName"))
        return out


# -- the measurement that replaces a corpus ---------------------------------

class TestNoCorpusMaterial(unittest.TestCase):
    """The three exports carry none of this section, and that is asserted
    rather than assumed.

    **Scanned as bytes, not through the library.** A harness that asks the
    reader under test whether the section is there cannot catch the reader
    failing to see it, which is `roundtrip.py`'s rule applied to a different
    question. A prefixed spelling (`<scl:Bay`) counts, and a longer name that
    merely starts the same (`<BayBoard`) does not.
    """

    FILES = ("sel.scd", "mixed.scd", "siemens.scd")

    def scan(self, name):
        path = roundtrip.CORPUS / name
        if not path.is_file():
            self.skipTest("the corpus is not in this distribution")
        return path.read_bytes()

    def test_every_substation_section_name_is_absent_from_every_export(self):
        for name in self.FILES:
            blob = self.scan(name)
            for element in SECTION_NAMES:
                pattern = re.compile(
                    rb"<(?:[\w.-]+:)?" + element.encode() + rb"[\s/>]")
                self.assertEqual(
                    [], pattern.findall(blob),
                    f"{name} carries a {element}; this module's fixtures were "
                    f"built on the premise that no corpus file does, and that "
                    f"premise now needs re-measuring rather than patching")

    def test_the_count_is_twenty_three_names(self):
        # The brief for this phase listed fifteen. The eight added here are
        # the rest of what the section can hold, and they are zero too.
        self.assertEqual(23, len(SECTION_NAMES))
        self.assertEqual(len(SECTION_NAMES), len(set(SECTION_NAMES)))


# -- what the schema said ---------------------------------------------------

class TestDerivedTables(_Base):
    """The two public tables are derivations, and a test pins the derivation
    rather than the convenience."""

    def test_both_elements_typed_tterminal_are_carried(self):
        # `NeutralPoint` is declared `type="tTerminal"` inside
        # `tTransformerWinding`, so it carries every container attribute a
        # `Terminal` does. Sweeping `Terminal` alone would leave a transformer
        # neutral naming a bay that is gone.
        self.assertEqual(("Terminal", "NeutralPoint"), TERMINAL_ELEMENTS)

    def test_all_five_container_attributes_are_carried(self):
        self.assertEqual(
            {"Substation": "substationName",
             "VoltageLevel": "voltageLevelName",
             "Bay": "bayName",
             "Line": "lineName",
             "Process": "processName"},
            CONTAINER_NAME_ATTRIBUTES)

    def test_the_process_sections_are_substation_keys_selector(self):
        # `SubstationKey`'s selector is `./scl:Substation|./scl:Process|./scl:Line`.
        self.assertEqual(("Substation", "Line", "Process"), PROCESS_SECTIONS)


# -- renaming a bay ---------------------------------------------------------

class TestUpdateBay(_Base):
    def test_the_caller_edit_comes_back_first(self):
        doc = self.doc()
        bay = self.find(doc, "Bay", name="B0")
        edit = SetAttributes(bay, {"name": "Q01"})
        edits = update_bay(doc, edit)
        self.assertIs(edits[0], edit)

    def test_the_nodes_inside_it_are_repathed(self):
        doc = self.doc()
        bay = self.find(doc, "Bay", name="B0")
        self.applied(doc, update_bay(doc, SetAttributes(bay, {"name": "Q01"})))
        self.assertEqual(
            ["S1/V1/B1/L1", "S1/V1/B2/L1", "S1/V1/Q01/N1", "S1/V1/Q01/N2"],
            self.paths(doc))

    def test_a_terminal_in_this_bay_naming_another_is_left_alone(self):
        """**The measurement the module exists for.** `E1/T1` sits inside
        `B0` and names `B1`, because a container attribute describes where the
        NODE is. Renaming `B0` must not touch it."""
        doc = self.doc()
        bay = self.find(doc, "Bay", name="B0")
        self.applied(doc, update_bay(doc, SetAttributes(bay, {"name": "Q01"})))
        after = self.terminals(doc)
        self.assertEqual(("S1/V1/B1/L1", "S1", "V1", "B1"),
                         after[("Terminal", "T1", "E1")])
        self.assertEqual(("S1/V1/B2/L1", "S1", "V1", "B2"),
                         after[("Terminal", "T1", "E2")])

    def test_a_terminal_elsewhere_naming_this_bay_is_followed(self):
        """The other direction: rename `B1`, and the terminal that follows it
        is the one in `B0` -- nowhere near the bay being renamed."""
        doc = self.doc()
        bay = self.find(doc, "Bay", name="B1")
        self.applied(doc, update_bay(doc, SetAttributes(bay, {"name": "Q09"})))
        after = self.terminals(doc)
        self.assertEqual(("S1/V1/Q09/L1", "S1", "V1", "Q09"),
                         after[("Terminal", "T1", "E1")])
        # and the sibling that pointed into B2 did not move
        self.assertEqual(("S1/V1/B2/L1", "S1", "V1", "B2"),
                         after[("Terminal", "T1", "E2")])

    def test_the_terminals_pointing_at_this_bays_own_node_follow(self):
        doc = self.doc()
        bay = self.find(doc, "Bay", name="B0")
        self.applied(doc, update_bay(doc, SetAttributes(bay, {"name": "Q01"})))
        after = self.terminals(doc)
        for where in ("E1", "E2"):
            self.assertEqual(("S1/V1/Q01/N1", "S1", "V1", "Q01"),
                             after[("Terminal", "T2", where)])

    def test_a_neutral_point_follows_exactly_as_a_terminal_does(self):
        doc = self.doc()
        bay = self.find(doc, "Bay", name="B0")
        self.applied(doc, update_bay(doc, SetAttributes(bay, {"name": "Q01"})))
        self.assertEqual(("S1/V1/Q01/N2", "S1", "V1", "Q01"),
                         self.terminals(doc)[("NeutralPoint", "NP", "W1")])

    def test_c_node_name_is_never_touched(self):
        doc = self.doc()
        before = sorted(el.get("cNodeName")
                        for tag in TERMINAL_ELEMENTS
                        for el in self.all(doc, tag))
        bay = self.find(doc, "Bay", name="B0")
        self.applied(doc, update_bay(doc, SetAttributes(bay, {"name": "Q01"})))
        after = sorted(el.get("cNodeName")
                       for tag in TERMINAL_ELEMENTS
                       for el in self.all(doc, tag))
        self.assertEqual(before, after)

    def test_two_nodes_of_one_leaf_name_are_told_apart_by_the_path(self):
        """`B1/L1` and `B2/L1` share a `cNodeName`. Renaming `B1` moves one
        of them and leaves the other exactly where it was."""
        doc = self.doc()
        bay = self.find(doc, "Bay", name="B1")
        self.applied(doc, update_bay(doc, SetAttributes(bay, {"name": "Q09"})))
        self.assertEqual(
            ["S1/V1/B0/N1", "S1/V1/B0/N2", "S1/V1/B2/L1", "S1/V1/Q09/L1"],
            self.paths(doc))

    def test_an_edit_that_does_not_touch_name_comes_back_alone(self):
        doc = self.doc()
        bay = self.find(doc, "Bay", name="B0")
        edit = SetAttributes(bay, {"desc": "feeder"})
        self.assertEqual([edit], update_bay(doc, edit))

    def test_renaming_to_the_same_name_comes_back_alone(self):
        doc = self.doc()
        bay = self.find(doc, "Bay", name="B0")
        edit = SetAttributes(bay, {"name": "B0"})
        self.assertEqual([edit], update_bay(doc, edit))

    def test_an_absent_optional_container_attribute_is_not_invented(self):
        """`bayName` is `use="optional"`. A file that omits it has made a
        choice, and a rename is not the edit that reverses it."""
        node = fx.connectivity_node("N1", "S1/V1/B0/N1")
        equipment = fx.conducting_equipment(
            "E1", body=fx.terminal("S1/V1/B0/N1", "N1"))
        doc = self.doc(fx.scl(fx.header(), fx.substation(
            "S1", body=fx.voltage_level(
                "V1", body=fx.bay("B0", body=equipment + node)))))
        bay = self.find(doc, "Bay", name="B0")
        self.applied(doc, update_bay(doc, SetAttributes(bay, {"name": "Q01"})))
        terminal = self.find(doc, "Terminal", name="T1")
        self.assertEqual("S1/V1/Q01/N1", terminal.get("connectivityNode"))
        self.assertIsNone(terminal.get("bayName"))

    def test_a_container_attribute_that_was_already_wrong_is_left(self):
        """The reference was followed, so the node is right. The stale
        `bayName` is a pre-existing inconsistency and repairing it is a
        different edit from the one that was asked for."""
        node = fx.connectivity_node("N1", "S1/V1/B0/N1")
        equipment = fx.conducting_equipment(
            "E1", body=fx.terminal("S1/V1/B0/N1", "N1", bay_name="ELSEWHERE"))
        doc = self.doc(fx.scl(fx.header(), fx.substation(
            "S1", body=fx.voltage_level(
                "V1", body=fx.bay("B0", body=equipment + node)))))
        bay = self.find(doc, "Bay", name="B0")
        self.applied(doc, update_bay(doc, SetAttributes(bay, {"name": "Q01"})))
        terminal = self.find(doc, "Terminal", name="T1")
        self.assertEqual("S1/V1/Q01/N1", terminal.get("connectivityNode"))
        self.assertEqual("ELSEWHERE", terminal.get("bayName"))

    def test_a_path_that_disagrees_with_its_ancestry_is_left(self):
        """Rewriting it wholesale would be the repair this module does not
        do, and the terminal pointing at it stays consistent with it."""
        node = fx.connectivity_node("N1", "NOT/THE/ANCESTRY")
        equipment = fx.conducting_equipment(
            "E1", body=fx.terminal("NOT/THE/ANCESTRY", "N1"))
        doc = self.doc(fx.scl(fx.header(), fx.substation(
            "S1", body=fx.voltage_level(
                "V1", body=fx.bay("B0", body=equipment + node)))))
        bay = self.find(doc, "Bay", name="B0")
        edits = update_bay(doc, SetAttributes(bay, {"name": "Q01"}))
        self.assertEqual(1, len(edits))
        self.applied(doc, edits)
        self.assertEqual(["NOT/THE/ANCESTRY"], self.paths(doc))
        self.assertEqual("NOT/THE/ANCESTRY",
                         self.find(doc, "Terminal",
                                   name="T1").get("connectivityNode"))

    def test_an_ambiguous_path_is_left_alone(self):
        """Two nodes carrying one path violate `ConnectivityNodeKey` already,
        and a terminal naming it does not say which it means."""
        b0 = fx.bay("B0", body=(
            fx.conducting_equipment("E1",
                                    body=fx.terminal("S1/V1/B0/N1", "N1"))
            + fx.connectivity_node("N1", "S1/V1/B0/N1")))
        # A second node of the same path, in a bay whose name produces it.
        b1 = fx.bay("B0x", body=fx.connectivity_node("N1", "S1/V1/B0/N1"))
        doc = self.doc(fx.scl(fx.header(), fx.substation(
            "S1", body=fx.voltage_level("V1", body=b0 + b1))))
        bay = self.find(doc, "Bay", name="B0")
        self.applied(doc, update_bay(doc, SetAttributes(bay, {"name": "Q01"})))
        terminal = self.find(doc, "Terminal", name="T1")
        self.assertEqual("S1/V1/B0/N1", terminal.get("connectivityNode"))


# -- renaming a voltage level and a substation ------------------------------

class TestUpdateVoltageLevel(_Base):
    def test_every_path_under_it_moves(self):
        doc = self.doc()
        level = self.find(doc, "VoltageLevel", name="V1")
        self.applied(doc, update_voltage_level(
            doc, SetAttributes(level, {"name": "V9"})))
        self.assertEqual(
            ["S1/V9/B0/N1", "S1/V9/B0/N2", "S1/V9/B1/L1", "S1/V9/B2/L1"],
            self.paths(doc))

    def test_every_terminal_follows_and_keeps_its_own_bay_name(self):
        doc = self.doc()
        level = self.find(doc, "VoltageLevel", name="V1")
        self.applied(doc, update_voltage_level(
            doc, SetAttributes(level, {"name": "V9"})))
        after = self.terminals(doc)
        self.assertEqual(("S1/V9/B1/L1", "S1", "V9", "B1"),
                         after[("Terminal", "T1", "E1")])
        self.assertEqual(("S1/V9/B0/N1", "S1", "V9", "B0"),
                         after[("Terminal", "T2", "E1")])
        self.assertEqual(("S1/V9/B0/N2", "S1", "V9", "B0"),
                         after[("NeutralPoint", "NP", "W1")])


class TestUpdateSubstation(_Base):
    def test_every_path_and_terminal_follows(self):
        doc = self.doc()
        section = self.find(doc, "Substation", name="S1")
        self.applied(doc, update_substation(
            doc, SetAttributes(section, {"name": "S9"})))
        self.assertEqual(
            ["S9/V1/B0/N1", "S9/V1/B0/N2", "S9/V1/B1/L1", "S9/V1/B2/L1"],
            self.paths(doc))
        for value in self.terminals(doc).values():
            self.assertTrue(value[0].startswith("S9/"), value)
            self.assertEqual("S9", value[1])

    def test_a_substation_nested_in_a_process_is_renamed_too(self):
        """A path is built from every named ancestor, so a `Process` wrapper
        contributes its own segment and the substation's name lands second.

        **Whether IEC intends that is not measured**: `tConnectivityNodeReference`
        only says `.+/.+(/.+)*`, and neither example SSD nests a `Substation`
        in a `Process`. What protects a file written to a different convention
        is the test below -- a path that disagrees with its ancestry is left
        exactly as it is, so this module does nothing rather than something
        wrong."""
        inner = fx.substation("INNER", body=fx.voltage_level(
            "V1", body=fx.bay("B0", body=fx.connectivity_node(
                "N1", "PR1/INNER/V1/B0/N1"))))
        doc = self.doc(fx.scl(fx.header(), "", "",
                              fx.process("PR1", body=inner)))
        section = self.find(doc, "Substation", name="INNER")
        self.applied(doc, update_substation(
            doc, SetAttributes(section, {"name": "DEEP"})))
        self.assertEqual(["PR1/DEEP/V1/B0/N1"], self.paths(doc))

    def test_a_nested_substation_on_another_convention_is_left_alone(self):
        """The same document with paths that omit the `Process` segment. Every
        path disagrees with the ancestry this module computes, so every one is
        left -- the rename is the whole edit."""
        inner = fx.substation("INNER", body=fx.voltage_level(
            "V1", body=fx.bay("B0", body=fx.connectivity_node(
                "N1", "INNER/V1/B0/N1"))))
        doc = self.doc(fx.scl(fx.header(), "", "",
                              fx.process("PR1", body=inner)))
        section = self.find(doc, "Substation", name="INNER")
        edits = update_substation(doc, SetAttributes(section, {"name": "DEEP"}))
        self.assertEqual(1, len(edits))
        self.applied(doc, edits)
        self.assertEqual(["INNER/V1/B0/N1"], self.paths(doc))


# -- collisions -------------------------------------------------------------

class TestCollisions(_Base):
    """The set is the schema's, and it is not the same at every level."""

    def test_a_bay_collides_with_another_bay(self):
        doc = self.doc()
        bay = self.find(doc, "Bay", name="B0")
        with self.assertRaises(EditRejected) as caught:
            update_bay(doc, SetAttributes(bay, {"name": "B1"}))
        self.assertIn("'B1'", str(caught.exception))

    def test_a_bay_collides_with_a_power_transformer_of_that_name(self):
        """`uniqueChildNameInVoltageLevel` selects `./*`, so the name space is
        shared across every tag. This is wider than a caller expects and it is
        written in the XSD."""
        doc = self.doc()
        bay = self.find(doc, "Bay", name="B0")
        with self.assertRaises(EditRejected) as caught:
            update_bay(doc, SetAttributes(bay, {"name": "PT1"}))
        self.assertIn("PowerTransformer", str(caught.exception))

    def test_a_voltage_level_collides_with_a_named_sibling(self):
        doc = self.doc(station(extra_root=""))
        level = self.find(doc, "VoltageLevel", name="V1")
        # The Substation's other named child here is the VoltageLevel alone,
        # so build one with a Function beside it.
        doc2 = self.doc(fx.scl(fx.header(), fx.substation(
            "S1", body=fx.voltage_level("V1")
            + fx.substation_function("AUX"))), name="b.ssd")
        level2 = self.find(doc2, "VoltageLevel", name="V1")
        with self.assertRaises(EditRejected) as caught:
            update_voltage_level(doc2, SetAttributes(level2, {"name": "AUX"}))
        self.assertIn("Function", str(caught.exception))
        self.assertIsNotNone(level)

    def test_a_substation_collides_with_a_line_at_the_root(self):
        doc = self.doc(station(tail=fx.line("LN1")))
        section = self.find(doc, "Substation", name="S1")
        with self.assertRaises(EditRejected) as caught:
            update_substation(doc, SetAttributes(section, {"name": "LN1"}))
        self.assertIn("Line", str(caught.exception))

    def test_a_substation_collides_with_a_process_at_the_root(self):
        doc = self.doc(station(tail=fx.process("PR1")))
        section = self.find(doc, "Substation", name="S1")
        with self.assertRaises(EditRejected):
            update_substation(doc, SetAttributes(section, {"name": "PR1"}))

    def test_a_substation_does_NOT_collide_with_an_ied(self):
        """`SubstationKey` selects `Substation|Process|Line`; `IEDKey` is a
        separate key over `./scl:IED`. A station and a device may share a
        name, and refusing that would be this module inventing a rule."""
        doc = self.doc(station(extra_root=fx.ied("REL1")))
        section = self.find(doc, "Substation", name="S1")
        self.applied(doc, update_substation(
            doc, SetAttributes(section, {"name": "REL1"})))
        self.assertEqual(
            "REL1", self.find(doc, "Substation", name="REL1").get("name"))
        self.assertEqual(["REL1/V1/B0/N1", "REL1/V1/B0/N2",
                          "REL1/V1/B1/L1", "REL1/V1/B2/L1"], self.paths(doc))

    def test_a_refusal_writes_nothing(self):
        doc = self.doc()
        before = doc.to_bytes()
        bay = self.find(doc, "Bay", name="B0")
        with self.assertRaises(EditRejected):
            update_bay(doc, SetAttributes(bay, {"name": "B1"}))
        self.assertEqual(before, doc.to_bytes())


# -- refusals ---------------------------------------------------------------

class TestRenameRefusals(_Base):
    def test_a_remove_where_a_set_attributes_belongs(self):
        doc = self.doc()
        bay = self.find(doc, "Bay", name="B0")
        with self.assertRaises(EditRejected) as caught:
            update_bay(doc, Remove(bay))
        self.assertIn("SetAttributes", str(caught.exception))

    def test_the_wrong_element(self):
        doc = self.doc()
        level = self.find(doc, "VoltageLevel", name="V1")
        with self.assertRaises(EditRejected) as caught:
            update_bay(doc, SetAttributes(level, {"name": "Q01"}))
        self.assertIn("VoltageLevel is not a Bay", str(caught.exception))

    def test_an_element_of_another_document(self):
        doc = self.doc()
        other = self.doc(name="b.ssd")
        stranger = self.find(other, "Bay", name="B0")
        with self.assertRaises(EditRejected):
            update_bay(doc, SetAttributes(stranger, {"name": "Q01"}))

    def test_an_empty_name(self):
        doc = self.doc()
        bay = self.find(doc, "Bay", name="B0")
        with self.assertRaises(EditRejected) as caught:
            update_bay(doc, SetAttributes(bay, {"name": ""}))
        self.assertIn("required", str(caught.exception))

    def test_a_bay_in_a_foreign_namespace_is_not_this_documents(self):
        """Namespace-exact, as every reader in this package is since A8."""
        body = ('<Private type="v"><Bay xmlns="urn:vendor" name="B0"/>'
                '</Private>')
        doc = self.doc(fx.scl(fx.header(), fx.substation(
            "S1", body=body + fx.voltage_level("V1", body=fx.bay("B0")))))
        stranger = [el for el in doc.root.iter()
                    if el.tag == "{urn:vendor}Bay"][0]
        with self.assertRaises(EditRejected):
            update_bay(doc, SetAttributes(stranger, {"name": "Q01"}))


# -- invertibility ----------------------------------------------------------

class TestRenameInvertible(_Base):
    """A rename and its undo restore the BYTES, which is what composes with
    Stage 0's round-trip guarantee."""

    def test_a_bay_rename_inverts_to_the_byte(self):
        doc = self.doc()
        before = doc.to_bytes()
        bay = self.find(doc, "Bay", name="B0")
        undo = self.applied(
            doc, update_bay(doc, SetAttributes(bay, {"name": "Q01"})))
        self.assertNotEqual(before, doc.to_bytes())
        doc.apply_edit(undo)
        self.assertEqual(before, doc.to_bytes())

    def test_a_substation_rename_inverts_to_the_byte(self):
        doc = self.doc()
        before = doc.to_bytes()
        section = self.find(doc, "Substation", name="S1")
        undo = self.applied(doc, update_substation(
            doc, SetAttributes(section, {"name": "S9"})))
        doc.apply_edit(undo)
        self.assertEqual(before, doc.to_bytes())

    def test_a_removal_inverts_to_the_byte(self):
        doc = self.doc()
        before = doc.to_bytes()
        bay = self.find(doc, "Bay", name="B1")
        undo = self.applied(doc, remove_process_element(doc, Remove(bay)))
        self.assertNotEqual(before, doc.to_bytes())
        doc.apply_edit(undo)
        self.assertEqual(before, doc.to_bytes())


# -- removing ---------------------------------------------------------------

class TestRemoveProcessElement(_Base):
    def test_the_caller_edit_comes_back_first(self):
        doc = self.doc()
        bay = self.find(doc, "Bay", name="B1")
        edit = Remove(bay)
        self.assertIs(remove_process_element(doc, edit)[0], edit)

    def test_a_terminal_pointing_into_the_removed_bay_goes(self):
        """`B1` holds `L1`, and `E1/T1` -- which lives in `B0` -- points at
        it. Removing `B1` orphans that terminal."""
        doc = self.doc()
        bay = self.find(doc, "Bay", name="B1")
        self.applied(doc, remove_process_element(doc, Remove(bay)))
        remaining = {(k[0], k[1], k[2]) for k in self.terminals(doc)}
        self.assertNotIn(("Terminal", "T1", "E1"), remaining)
        # and the one pointing into B2 is untouched
        self.assertIn(("Terminal", "T1", "E2"), remaining)

    def test_a_node_nothing_points_at_any_more_goes(self):
        """Remove the transformer: its `NeutralPoint` was the only thing
        naming `B0/N2`, so that node is orphaned in pass two."""
        doc = self.doc()
        transformer = self.find(doc, "PowerTransformer", name="PT1")
        self.applied(doc, remove_process_element(doc, Remove(transformer)))
        self.assertEqual(["S1/V1/B0/N1", "S1/V1/B1/L1", "S1/V1/B2/L1"],
                         self.paths(doc))

    def test_a_node_with_a_surviving_terminal_stays(self):
        """`B0/N1` has two terminals. Removing the equipment holding one
        leaves the other, so the node is not an orphan."""
        doc = self.doc()
        equipment = self.find(doc, "ConductingEquipment", name="E1")
        self.applied(doc, remove_process_element(doc, Remove(equipment)))
        self.assertIn("S1/V1/B0/N1", self.paths(doc))

    def test_removing_the_last_terminal_of_a_node_takes_the_node(self):
        """Two removals, and the cascade is visible only by counting what is
        left. `E1` holds the only terminal naming `B1/L1`, so that node goes
        with it; `E2` holds the only one naming `B2/L1`, and between them they
        hold both of `B0/N1`'s. `B0/N2` survives all of it, because the
        `NeutralPoint` under the transformer still names it."""
        doc = self.doc()
        equipment = self.find(doc, "ConductingEquipment", name="E1")
        self.applied(doc, remove_process_element(doc, Remove(equipment)))
        self.assertEqual(["S1/V1/B0/N1", "S1/V1/B0/N2", "S1/V1/B2/L1"],
                         self.paths(doc))

        equipment = self.find(doc, "ConductingEquipment", name="E2")
        self.applied(doc, remove_process_element(doc, Remove(equipment)))
        self.assertEqual(["S1/V1/B0/N2"], self.paths(doc))

    def test_an_orphan_is_zero_terminals_and_not_fewer_than_two(self):
        """`B1/L1` and `B2/L1` are each named by exactly ONE terminal. A node
        joining a single piece of equipment is electrically pointless and
        structurally legal, and the schema does not grade it -- so removing
        something unrelated must leave both standing. Reading "orphan" as
        "fewer than two terminals" would take both here."""
        doc = self.doc()
        function = self.find(doc, "Function", name="F1")
        edits = remove_process_element(doc, Remove(function))
        self.assertEqual(1, len(edits))
        self.applied(doc, edits)
        self.assertEqual(["S1/V1/B0/N1", "S1/V1/B0/N2",
                          "S1/V1/B1/L1", "S1/V1/B2/L1"], self.paths(doc))
        self.assertEqual(5, len(self.terminals(doc)))

    def test_removing_a_whole_substation_takes_everything_with_it(self):
        doc = self.doc()
        section = self.find(doc, "Substation", name="S1")
        self.applied(doc, remove_process_element(doc, Remove(section)))
        self.assertEqual([], self.paths(doc))
        self.assertEqual({}, self.terminals(doc))

    def test_a_node_outside_the_subtree_pointed_at_from_outside_stays(self):
        doc = self.doc()
        bay = self.find(doc, "Bay", name="B2")
        self.applied(doc, remove_process_element(doc, Remove(bay)))
        # B0's own nodes are untouched; only B2's L1 went.
        self.assertEqual(["S1/V1/B0/N1", "S1/V1/B0/N2", "S1/V1/B1/L1"],
                         self.paths(doc))

    def test_the_removal_prunes_no_data_types(self):
        """As `remove_ied` does not: the caller removed a piece of the
        primary system, not a type."""
        pool = fx.templates(fx.lnode_type("T_CSWI", "CSWI"))
        doc = self.doc(fx.scl(fx.header(), fx.substation(
            "S1", body=fx.voltage_level(
                "V1", body=fx.bay("B0", body=fx.conducting_equipment(
                    "E1", body=fx.lnode(ln_class="CSWI",
                                        ln_type="T_CSWI"))))), "", pool))
        bay = self.find(doc, "Bay", name="B0")
        self.applied(doc, remove_process_element(doc, Remove(bay)))
        self.assertEqual(1, len(self.all(doc, "LNodeType")))


class TestRemoveRefusals(_Base):
    def test_a_set_attributes_where_a_remove_belongs(self):
        doc = self.doc()
        bay = self.find(doc, "Bay", name="B0")
        with self.assertRaises(EditRejected) as caught:
            remove_process_element(doc, SetAttributes(bay, {"name": "X"}))
        self.assertIn("Remove", str(caught.exception))

    def test_an_ied_is_remove_ieds(self):
        doc = self.doc(station(extra_root=fx.ied("REL1")))
        ied = self.find(doc, "IED", name="REL1")
        with self.assertRaises(EditRejected) as caught:
            remove_process_element(doc, Remove(ied))
        self.assertIn("remove_ied", str(caught.exception))

    def test_an_element_outside_every_process_section(self):
        doc = self.doc(station(extra_root=fx.ied("REL1")))
        header = self.find(doc, "Header", id="ST1")
        with self.assertRaises(EditRejected) as caught:
            remove_process_element(doc, Remove(header))
        self.assertIn("Substation, Line and Process", str(caught.exception))

    def test_an_element_of_another_document(self):
        doc = self.doc()
        other = self.doc(name="b.ssd")
        with self.assertRaises(EditRejected):
            remove_process_element(
                doc, Remove(self.find(other, "Bay", name="B0")))

    def test_something_that_is_not_an_element(self):
        doc = self.doc()
        with self.assertRaises(EditRejected):
            remove_process_element(doc, Remove("Bay"))


# -- placement --------------------------------------------------------------

class TestLineAndProcess(_Base):
    """`Line` and `Process` are in `PROCESS_SECTIONS` and in A7's table, and
    a node inside one gets a path the same way."""

    def test_a_node_in_a_line_is_removable_as_a_process_element(self):
        doc = self.doc(fx.scl(fx.header(), "", "", fx.line(
            "LN1", body=fx.conducting_equipment(
                "E1", body=fx.terminal("LN1/N1", "N1"))
            + fx.connectivity_node("N1", "LN1/N1"))))
        line = self.find(doc, "ConductingEquipment", name="E1")
        self.applied(doc, remove_process_element(doc, Remove(line)))
        self.assertEqual([], self.paths(doc))

    def test_a_two_segment_path_is_what_a_line_produces(self):
        doc = self.doc(fx.scl(fx.header(), "", "", fx.line(
            "LN1", body=fx.connectivity_node("N1", "LN1/N1"))))
        self.assertEqual(["LN1/N1"], self.paths(doc))


class TestInsertPlacement(_Base):
    """A16 adds no insertion of its own, but A7's table has to place these
    elements for one to be possible at all -- confirmed rather than
    assumed."""

    def test_a_bay_lands_among_the_bays(self):
        from py61850.scl import reference_for
        doc = self.doc()
        level = self.find(doc, "VoltageLevel", name="V1")
        tag = level.tag.replace("VoltageLevel", "Bay")
        node = ET.Element(tag, {"name": "B3"})
        self.applied(doc, Insert(level, node, reference_for(level, tag)))
        self.assertEqual(["PT1", "B0", "B1", "B2", "B3"],
                         [child.get("name") for child in level])

    def test_a_connectivity_node_lands_after_the_equipment(self):
        from py61850.scl import reference_for
        doc = self.doc()
        bay = self.find(doc, "Bay", name="B1")
        tag = bay.tag.replace("Bay", "ConductingEquipment")
        node = ET.Element(tag, {"name": "E9", "type": "CBR"})
        self.applied(doc, Insert(bay, node, reference_for(bay, tag)))
        self.assertEqual(["ConductingEquipment", "ConnectivityNode"],
                         [strip_ns(child.tag) for child in bay])


class TestAttributeOrderIsNotDisturbed(_Base):
    """The defect this phase found, and the only method that shows it.

    `SetAttributes` reads a mapping naming EVERY attribute an element has as a
    complete, ordered description and rebuilds them in the mapping's order --
    A5's rule, and what makes a deletion invertible. A `Terminal` carrying
    nothing but `connectivityNode` and one container name is exactly such an
    element, so a rename that built its mapping in its own order reordered the
    file's attributes.

    **A round-trip test cannot see it.** The undo restores the bytes either
    way, because the inverse describes the element in the element's own order.
    What the defect produces is a whole-file diff where a rename was asked
    for, and only reading the serialised FORWARD result shows it -- the fifth
    phase running where counting the result was the method that worked.
    """

    def minimal(self, first="bayName", second="connectivityNode"):
        pair = {"bayName": '"B0"', "connectivityNode": '"S1/V1/B0/N1"'}
        body = ('<ConductingEquipment name="E1" type="CBR">'
                f'<Terminal {first}={pair[first]} {second}={pair[second]}/>'
                '</ConductingEquipment>'
                '<ConnectivityNode name="N1" pathName="S1/V1/B0/N1"/>')
        return fx.scl(fx.header(), fx.substation("S1", body=fx.voltage_level(
            "V1", body=fx.bay("B0", body=body))))

    def rename(self, text, name):
        doc = self.doc(text, name=name)
        bay = self.find(doc, "Bay", name="B0")
        self.applied(doc, update_bay(doc, SetAttributes(bay, {"name": "Q01"})))
        return doc.to_bytes().decode()

    def test_the_files_own_order_survives_a_rename(self):
        out = self.rename(self.minimal(), "order_a.ssd")
        self.assertIn('<Terminal bayName="Q01" '
                      'connectivityNode="S1/V1/Q01/N1" />', out)

    def test_and_survives_it_the_other_way_round(self):
        out = self.rename(
            self.minimal("connectivityNode", "bayName"), "order_b.ssd")
        self.assertIn('<Terminal connectivityNode="S1/V1/Q01/N1" '
                      'bayName="Q01" />', out)
