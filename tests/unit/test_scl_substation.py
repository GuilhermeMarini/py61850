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
    SPECIFICATION_ELEMENTS,
    SPECIFICATION_NS,
    TERMINAL_ELEMENTS,
    EditRejected,
    Insert,
    Remove,
    SclDocument,
    SetAttributes,
    children_local,
    prune_lnode_specification,
    remove_process_element,
    strip_ns,
    update_bay,
    update_lnode_type,
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


# -- IEC TR 61850-6-100: pruning an LNode's specification --------------------
#
# A SECOND IEC namespace, and the same problem the rest of this module has:
# no corpus material at all. `DOS`, `SDS` and `DAS` are already three of
# `SECTION_NAMES`' twenty-three, so `TestNoCorpusMaterial` above already
# asserts their absence from all three exports and needs no extending.
#
# What the fixtures are shaped by is again IEC's own `Eng POC.ssd`, and what
# it shows is reproduced from scratch by `specified()` below:
#
#   - every DOS inside `LNode/Private[@type="eIEC61850-6-100"]`, 106 of 106;
#   - `SDS` naming a `DA` with bType="Struct", never an `SDO` -- all 17;
#   - `DAS name="d"` against DOTypes that declare only stVal/q/t, which is 72
#     of that file's 73 misses and the reason a strict prune removes a fifth
#     of it.

def specified(ln_type="T_PTOC", second_ln_type="T_PTOC", spec=None,
              declared_dos=(("Str", "T_ACD"), ("StrVal", "T_ASG"),
                            ("Beh", "T_ENS")),
              acd_das=({"name": "general", "bType": "BOOLEAN", "fc": "ST"},
                       {"name": "q", "bType": "Quality", "fc": "ST"}),
              asg_das=({"name": "setMag", "bType": "Struct", "fc": "SP",
                        "type": "T_AnalogueValue"},),
              analogue_bdas=({"name": "f", "bType": "FLOAT32"},
                             {"name": "i", "bType": "INT32"}),
              acd_sdos=(("phsA", "T_ACD_phs"),),
              templates=None, ns=True):
    """One document carrying a specified `LNode`, and the pool behind it.

    The shape is IEC's, reproduced rather than copied:

    - ``T_PTOC`` declares three DOs. ``Str`` is an ACD-shaped `DOType` with a
      leaf `DA` (``general``), a `Quality` `DA` and **an `SDO`** (``phsA``),
      so both kinds of structured child are reachable from one place.
    - ``StrVal`` is an ASG-shaped `DOType` whose only `DA` is
      ``bType="Struct"`` -- the exact shape all 17 of `Eng POC.ssd`'s `SDS`
      resolve to.
    - **None of the three `DOType` declares ``d``**, as the trimmed
      `ELIA_SPS_basic_V001` does not, which is what makes the file's
      commonest miss reproducible here.

    ``spec`` replaces the specification body wholesale; the default is one
    that resolves completely, so a test that changes nothing gets ``[]``.
    """
    if spec is None:
        spec = (fx.dos("Str", body=(fx.das("general")
                                    + fx.sds("phsA", body=fx.das("general"))))
                + fx.dos("StrVal",
                         body=fx.sds("setMag", body=fx.das("f")))
                + fx.dos("Beh", body=fx.das("stVal")))

    if templates is None:
        templates = fx.templates(
            fx.lnode_type("T_PTOC", ln_class="PTOC", dos=declared_dos),
            fx.lnode_type("T_OTHER", ln_class="PTRC",
                          dos=(("Beh", "T_ENS"),)),
            fx.do_type("T_ACD", cdc="ACD", das=acd_das, sdos=acd_sdos),
            fx.do_type("T_ACD_phs", cdc="ACD", das=acd_das),
            fx.do_type("T_ASG", cdc="ASG", das=asg_das),
            fx.do_type("T_ENS", cdc="ENS",
                       das=({"name": "stVal", "bType": "Enum", "fc": "ST",
                             "type": "T_Beh"},)),
            fx.da_type("T_AnalogueValue", bdas=analogue_bdas),
            fx.enum_type("T_Beh", values=((1, "on"), (2, "blocked"))),
        )

    def node(ln_inst, type_, body):
        return (f'<LNode lnClass="PTOC" lnInst="{ln_inst}" '
                f'lnType="{type_}">{body}</LNode>')

    bay = fx.bay("B0", body=fx.substation_function("F1", body=(
        node("1", ln_type, fx.spec_private(spec))
        + node("2", second_ln_type, fx.spec_private(spec)))))
    section = fx.substation("S1", body=fx.voltage_level("V1", body=bay))
    return fx.scl(fx.header(), section, templates, spec_ns=True, ns=ns)


class _Spec(_Base):
    """Every test here reads the document back after applying, never the edit
    list it applied -- `_Base.applied` and the module docstring's rule."""

    def specified_doc(self, name=None, **kwargs):
        return self.doc(specified(**kwargs),
                        name=name or f"spec_{id(kwargs)}.ssd")

    def spec_names(self, doc):
        """``[(local name, @name), ...]`` for every 6-100 element left, in
        document order -- the result, counted."""
        return [(strip_ns(el.tag), el.get("name"))
                for el in doc.root.iter()
                if isinstance(el.tag, str)
                and el.tag.startswith(SPECIFICATION_NS)]

    def pruned(self, doc, id_="T_PTOC", **kwargs):
        edits = prune_lnode_specification(doc, id_, **kwargs)
        self.applied(doc, edits)
        return edits


class TestSpecificationShape(_Spec):
    def test_a_document_whose_specification_resolves_is_left_alone(self):
        doc = self.specified_doc("resolves.ssd")
        before = self.spec_names(doc)
        self.assertEqual([], prune_lnode_specification(doc, "T_PTOC"))
        self.assertEqual(before, self.spec_names(doc))

    def test_an_unknown_type_is_refused_rather_than_read_as_empty(self):
        # The tempting failure: no LNodeType, so nothing is declared, so
        # everything is missing and the whole specification goes.
        doc = self.specified_doc("unknown.ssd")
        with self.assertRaises(EditRejected) as caught:
            prune_lnode_specification(doc, "T_ABSENT")
        self.assertIn("T_ABSENT", str(caught.exception))
        self.assertIn("target", str(caught.exception))

    def test_an_unknown_type_in_the_source_names_the_source(self):
        doc = self.specified_doc("src_unknown.ssd")
        other = self.doc(specified(), name="src_unknown_2.ssd")
        with self.assertRaises(EditRejected) as caught:
            prune_lnode_specification(doc, "T_ABSENT", source=other)
        self.assertIn("source", str(caught.exception))

    def test_an_element_where_a_document_belongs_says_so(self):
        doc = self.specified_doc("not_a_doc.ssd")
        with self.assertRaises(EditRejected) as caught:
            prune_lnode_specification(doc, "T_PTOC", source=doc.root)
        self.assertIn("SclDocument", str(caught.exception))

    def test_a_document_with_no_templates_at_all_is_refused(self):
        doc = self.doc(specified(templates=""), name="no_pool.ssd")
        with self.assertRaises(EditRejected):
            prune_lnode_specification(doc, "T_PTOC")

    def test_the_namespace_constant_is_the_trs_and_both_editions_agree(self):
        # `targetNamespace` is identical in IEC TR 61850-6-100's 2019B9 and
        # 2019C1, which is what makes keying on it edition-stable.
        self.assertEqual("{http://www.iec.ch/61850/2019/SCL/6-100}",
                         SPECIFICATION_NS)
        self.assertEqual(("DOS", "SDS", "DAS"), SPECIFICATION_ELEMENTS)


class TestSpecificationResolution(_Spec):
    """Every level of the walk, and the two spellings that do not mean what
    they say."""

    def test_a_dos_naming_a_declared_do_survives(self):
        doc = self.specified_doc("dos_ok.ssd")
        self.pruned(doc)
        self.assertIn(("DOS", "Str"), self.spec_names(doc))

    def test_an_sds_resolving_to_an_sdo_survives(self):
        # `phsA` is an SDO of T_ACD. The reachable-through-an-SDO branch.
        doc = self.specified_doc("sds_sdo.ssd")
        self.pruned(doc)
        self.assertIn(("SDS", "phsA"), self.spec_names(doc))

    def test_an_sds_resolving_to_a_struct_da_survives(self):
        # `setMag` is a DA with bType="Struct" -- what all 17 of
        # `Eng POC.ssd`'s SDS actually resolve to, against an annotation that
        # calls SDS a sub-Data Object.
        doc = self.specified_doc("sds_struct.ssd")
        self.pruned(doc)
        self.assertIn(("SDS", "setMag"), self.spec_names(doc))

    def test_a_das_under_a_struct_sds_resolves_through_the_datype(self):
        doc = self.specified_doc("das_bda.ssd")
        self.pruned(doc)
        self.assertIn(("DAS", "f"), self.spec_names(doc))

    def test_a_das_naming_a_bda_the_datype_lacks_is_removed(self):
        doc = self.doc(specified(spec=fx.dos(
            "StrVal", body=fx.sds("setMag", body=fx.das("nope")))),
            name="bda_gone.ssd")
        self.pruned(doc)
        self.assertEqual([("DOS", "StrVal"), ("SDS", "setMag")] * 2,
                         self.spec_names(doc))

    def test_the_element_spelling_is_ignored_and_the_name_decides(self):
        # A `DAS` on a Struct DA and an `SDS` on a leaf DA: both resolve,
        # because `uniqueDAorSDOInDOType` makes the NAME the key and the TR
        # gives the spelling no meaning the material supports.
        doc = self.doc(specified(spec=(
            fx.dos("StrVal", body=fx.das("setMag"))
            + fx.dos("Str", body=fx.sds("general")))), name="spelling.ssd")
        self.assertEqual([], prune_lnode_specification(doc, "T_PTOC"))

    def test_a_nested_sds_walks_further_down(self):
        doc = self.doc(specified(spec=fx.dos("Str", body=fx.sds(
            "phsA", body=fx.sds("q", body=fx.das("nope"))))),
            name="nested.ssd")
        # `q` is a Quality DA. It resolves, so the `SDS` over it stays -- but
        # it declares no members, so the `DAS` under it is missing at a depth
        # a single lookup would never have reached.
        self.pruned(doc)
        self.assertEqual([("DOS", "Str"), ("SDS", "phsA"), ("SDS", "q")] * 2,
                         self.spec_names(doc))


class TestSpecificationMissing(_Spec):
    def test_a_dos_the_type_does_not_declare_is_removed(self):
        doc = self.doc(specified(spec=(fx.dos("Str") + fx.dos("TmAChr"))),
                       name="dos_gone.ssd")
        self.pruned(doc)
        self.assertEqual([("DOS", "Str")] * 2, self.spec_names(doc))

    def test_a_das_the_dotype_does_not_declare_is_removed(self):
        # `d` -- the CDC description attribute -- against a trimmed DOType.
        # 72 of `Eng POC.ssd`'s 73 misses are exactly this.
        doc = self.doc(specified(spec=fx.dos(
            "Str", body=fx.das("general") + fx.das("d"))), name="d_gone.ssd")
        self.pruned(doc)
        self.assertEqual([("DOS", "Str"), ("DAS", "general")] * 2,
                         self.spec_names(doc))

    def test_only_the_outermost_missing_element_is_removed(self):
        # A missing DOS carrying four descendants is ONE Remove, not five:
        # the Remove takes the subtree, and a second Remove of a node already
        # gone would apply against a document that no longer holds it.
        doc = self.doc(specified(spec=fx.dos("TmAChr", body=(
            fx.das("numPts") + fx.sds("crvPts", body=fx.das("xVal"))
            + fx.das("maxPts")))), name="outermost.ssd")
        edits = self.pruned(doc)
        self.assertEqual(2, len(edits))          # one per LNode
        self.assertTrue(all(isinstance(edit, Remove) for edit in edits))
        self.assertEqual([], self.spec_names(doc))

    def test_every_removal_is_a_remove_and_nothing_else(self):
        # The reference returns `Remove[]`, not `EditV2[]`: a DOS the type
        # declares and the instance lacks is never invented. `Beh` and
        # `StrVal` are declared and unspecified here, and stay that way.
        doc = self.doc(specified(spec=fx.dos("Str", body=fx.das("d"))),
                       name="removes_only.ssd")
        edits = self.pruned(doc)
        self.assertEqual({Remove}, {type(edit) for edit in edits})
        self.assertEqual([("DOS", "Str")] * 2, self.spec_names(doc))

    def test_both_lnodes_of_the_type_are_pruned(self):
        doc = self.doc(specified(spec=fx.dos("TmAChr")), name="both.ssd")
        self.assertEqual(2, len(self.pruned(doc)))

    def test_an_lnode_of_another_type_is_untouched(self):
        doc = self.doc(specified(second_ln_type="T_OTHER",
                                 spec=fx.dos("TmAChr")), name="other.ssd")
        self.pruned(doc)
        self.assertEqual([("DOS", "TmAChr")], self.spec_names(doc))


class TestSpecificationLeftAlone(_Spec):
    """A type that does not resolve ends the walk on that branch. Deleting a
    specification because the pool was trimmed is the opposite of the edit
    asked for -- `TemplatePool` tolerates a dangling reference on READ
    deliberately, and the renames above leave an unresolvable
    `@connectivityNode` alone for the same reason."""

    def test_an_lnode_with_no_lntype_is_untouched(self):
        # One of the 90-30 example's sixteen has none.
        text = specified(spec=fx.dos("TmAChr")).replace(
            ' lnType="T_PTOC"', "", 1)
        doc = self.doc(text, name="no_lntype.ssd")
        self.pruned(doc)
        self.assertEqual([("DOS", "TmAChr")], self.spec_names(doc))

    def test_a_dos_whose_dotype_is_absent_keeps_its_whole_subtree(self):
        # `Str` IS declared; `T_ACD` is not in the pool. The DOS stays,
        # because the type declares the object, and nothing under it can be
        # judged.
        doc = self.doc(specified(
            declared_dos=(("Str", "T_MISSING"),),
            spec=fx.dos("Str", body=fx.das("general") + fx.das("nope"))),
            name="dotype_gone.ssd")
        self.assertEqual([], prune_lnode_specification(doc, "T_PTOC"))

    def test_a_struct_da_whose_datype_is_absent_keeps_its_children(self):
        doc = self.doc(specified(
            asg_das=({"name": "setMag", "bType": "Struct", "fc": "SP",
                      "type": "T_MISSING"},),
            spec=fx.dos("StrVal", body=fx.sds("setMag", body=fx.das("nope")))),
            name="datype_gone.ssd")
        self.assertEqual([], prune_lnode_specification(doc, "T_PTOC"))

    def test_an_sdo_whose_dotype_is_absent_keeps_its_children(self):
        doc = self.doc(specified(
            acd_sdos=(("phsA", "T_MISSING"),),
            spec=fx.dos("Str", body=fx.sds("phsA", body=fx.das("nope")))),
            name="sdo_gone.ssd")
        self.assertEqual([], prune_lnode_specification(doc, "T_PTOC"))

    def test_ix_is_not_checked(self):
        # `ix` is an array index and the TR's key is (@name, @ix); whether an
        # index is within a bound is a different question, and 0 DA or BDA in
        # either example file carries `@count` to bound it with.
        doc = self.doc(specified(spec=fx.dos("Str", body=(
            fx.das("general", ix="0") + fx.das("general", ix="1")))),
            name="ix.ssd")
        self.assertEqual([], prune_lnode_specification(doc, "T_PTOC"))

    def test_the_other_children_of_a_dos_are_not_touched(self):
        # A DOS may also hold SubscriberLNode, ControllingLNode, ProcessEcho,
        # LogParametersRef and Labels. None names a member of a data type.
        other = ('<eIEC61850-6-100:ControllingLNode outputName="OUT1"/>'
                 '<eIEC61850-6-100:ProcessEcho/>')
        doc = self.doc(specified(spec=fx.dos(
            "Str", body=other + fx.das("d"))), name="siblings.ssd")
        self.pruned(doc)
        self.assertEqual(
            [("DOS", "Str"), ("ControllingLNode", None), ("ProcessEcho", None)]
            * 2, self.spec_names(doc))

    def test_a_dos_with_no_name_at_all_is_removed(self):
        # `@name` is use="required"; one absent cannot resolve and cannot be
        # guessed at.
        text = specified(spec=fx.dos("Str")).replace('DOS name="Str"', "DOS")
        doc = self.doc(text, name="unnamed.ssd")
        self.pruned(doc)
        self.assertEqual([], self.spec_names(doc))


class TestSpecificationAttachment(_Spec):
    """`tBaseElement` opens with an `xs:any namespace="##other"` before `Text`
    and `Private`, identically in 2007B4 and 2007C5 -- so a `DOS` directly
    under an `LNode` is schema-valid, and is what the TR's own annotation asks
    for. IEC's files use the `Private` form on 106 of 106 and this module
    reads both, keyed on the namespace rather than on a `Private@type` string
    that appears nowhere in the 6-100 schema."""

    def bare(self, spec):
        """The same document with the `Private` wrapper taken away."""
        return specified(spec=spec).replace(
            f'<Private type="{fx.SPEC_PREFIX}">', "").replace(
            "</Private>", "")

    def test_a_dos_directly_under_the_lnode_is_found(self):
        doc = self.doc(self.bare(fx.dos("TmAChr")), name="bare_gone.ssd")
        self.pruned(doc)
        self.assertEqual([], self.spec_names(doc))

    def test_a_dos_directly_under_the_lnode_is_also_kept_when_declared(self):
        doc = self.doc(self.bare(fx.dos("Str", body=fx.das("general"))),
                       name="bare_ok.ssd")
        self.assertEqual([], prune_lnode_specification(doc, "T_PTOC"))

    def test_a_private_of_another_type_is_read_just_the_same(self):
        # The `@type` is the files' convention, not the schema's, so the
        # library must not depend on it.
        text = specified(spec=fx.dos("TmAChr")).replace(
            f'type="{fx.SPEC_PREFIX}"', 'type="SomethingElse"')
        doc = self.doc(text, name="other_private.ssd")
        self.pruned(doc)
        self.assertEqual([], self.spec_names(doc))

    def test_a_dos_nested_deeper_inside_a_private_is_left_alone(self):
        # One level into a Private and no further: a DOS below that belongs
        # to whatever structure put it there.
        deeper = (f'<{fx.SPEC_PREFIX}:BayType>'
                  + fx.dos("TmAChr") + f'</{fx.SPEC_PREFIX}:BayType>')
        doc = self.doc(specified(spec=deeper), name="deep.ssd")
        self.assertEqual([], prune_lnode_specification(doc, "T_PTOC"))


class TestSpecificationNamespaceGuard(_Spec):
    """An element whose LOCAL name is `DOS` and whose namespace is not the
    TR's is a different element. A8, A10, A12 and A13 each met a vendor
    element wearing a standard local name."""

    def test_a_dos_in_the_scl_namespace_is_not_a_specification(self):
        doc = self.doc(specified(spec='<DOS name="TmAChr"/>'),
                       name="scl_dos.ssd")
        self.assertEqual([], prune_lnode_specification(doc, "T_PTOC"))

    def test_a_dos_in_a_vendor_namespace_is_not_a_specification(self):
        spec = ('<v:DOS xmlns:v="http://example.invalid/vendor" '
                'name="TmAChr"/>')
        doc = self.doc(specified(spec=spec), name="vendor_dos.ssd")
        self.assertEqual([], prune_lnode_specification(doc, "T_PTOC"))

    def test_a_document_declaring_no_default_namespace_still_works(self):
        # A hand-made SCD declaring none is real; the 6-100 prefix is
        # declared either way, and the LNode sweep is namespace-exact.
        doc = self.doc(specified(spec=fx.dos("TmAChr"), ns=False),
                       name="no_default_ns.ssd")
        self.pruned(doc)
        self.assertEqual([], self.spec_names(doc))


class TestSpecificationApplied(_Spec):
    """Applying it, and reading the bytes back. Five phases running have found
    their defect this way and no other -- Q33 §9, Q34 §4 and §5, Q35 §7 and
    Q36 §7."""

    def test_the_undo_restores_the_file_byte_for_byte(self):
        text = specified(spec=fx.dos("Str", body=fx.das("d")) +
                         fx.dos("TmAChr"))
        doc = self.doc(text, name="undo.ssd")
        before = doc.to_bytes()
        undo = self.applied(doc, prune_lnode_specification(doc, "T_PTOC"))
        self.assertNotEqual(before, doc.to_bytes())
        doc.apply_edit(undo)
        self.assertEqual(before, doc.to_bytes())

    def test_the_prefix_and_the_untouched_elements_survive(self):
        doc = self.doc(specified(spec=fx.dos("Str", body=fx.das("d"))),
                       name="prefix.ssd")
        self.applied(doc, prune_lnode_specification(doc, "T_PTOC"))
        out = doc.to_bytes().decode()
        self.assertIn('xmlns:eIEC61850-6-100=', out)
        self.assertIn('<eIEC61850-6-100:DOS name="Str"', out)
        self.assertNotIn('DAS', out)

    def test_nothing_else_in_the_document_moves(self):
        doc = self.doc(specified(spec=fx.dos("TmAChr")), name="quiet.ssd")
        paths = self.paths(doc)
        terminals = self.terminals(doc)
        self.applied(doc, prune_lnode_specification(doc, "T_PTOC"))
        self.assertEqual(paths, self.paths(doc))
        self.assertEqual(terminals, self.terminals(doc))
        self.assertEqual(8, len(self.all(doc, "DOType"))
                         + len(self.all(doc, "LNodeType"))
                         + len(self.all(doc, "DAType"))
                         + len(self.all(doc, "EnumType")))

    def test_the_pool_is_read_and_never_changed(self):
        # The direction is the distinction: this asks DataTypeTemplates what
        # a type declares and edits the LNode. `remove_process_element` does
        # not cascade into that section and neither does this.
        doc = self.doc(specified(spec=fx.dos("TmAChr")), name="pool.ssd")
        before = ET.tostring(self.find(doc, "DataTypeTemplates"))
        self.applied(doc, prune_lnode_specification(doc, "T_PTOC"))
        self.assertEqual(before,
                         ET.tostring(self.find(doc, "DataTypeTemplates")))

    def test_the_lnode_itself_keeps_every_attribute(self):
        doc = self.doc(specified(spec=fx.dos("TmAChr")), name="attrs.ssd")
        self.applied(doc, prune_lnode_specification(doc, "T_PTOC"))
        lnode = self.all(doc, "LNode")[0]
        self.assertEqual({"lnClass": "PTOC", "lnInst": "1",
                          "lnType": "T_PTOC"}, dict(lnode.attrib))


class TestSpecificationAgainstASource(_Spec):
    """`source` prunes against a declaration that has not landed yet, so one
    intent costs one history entry. Inside a compound edit the document is out
    of date -- Q33 §9, Q34 §5 and Q35 §7, each from a different side."""

    def moved_on(self, name):
        """A second document whose `T_PTOC` has lost `Str` and whose `T_ASG`
        has lost `setMag`."""
        return self.doc(specified(
            declared_dos=(("StrVal", "T_ASG"), ("Beh", "T_ENS")),
            asg_das=()), name=name)

    def test_a_source_document_supplies_the_declaration(self):
        doc = self.specified_doc("src_target.ssd")
        source = self.moved_on("src_source.ssd")
        self.pruned(doc, source=source)
        # `Str` is gone from the new declaration and takes its subtree;
        # `setMag` is gone from the new T_ASG; `Beh` survives untouched.
        self.assertEqual([("DOS", "StrVal"), ("DOS", "Beh"),
                          ("DAS", "stVal")] * 2, self.spec_names(doc))

    def test_the_source_is_not_modified(self):
        doc = self.specified_doc("src_ro_target.ssd")
        source = self.moved_on("src_ro_source.ssd")
        before = source.to_bytes()
        self.applied(doc, prune_lnode_specification(
            doc, "T_PTOC", source=source))
        self.assertEqual(before, source.to_bytes())

    def test_source_is_doc_means_exactly_what_omitting_it_means(self):
        one = self.doc(specified(spec=fx.dos("TmAChr")), name="same_a.ssd")
        two = self.doc(specified(spec=fx.dos("TmAChr")), name="same_b.ssd")
        self.applied(one, prune_lnode_specification(one, "T_PTOC"))
        self.applied(two, prune_lnode_specification(two, "T_PTOC",
                                                    source=two))
        self.assertEqual(one.to_bytes(), two.to_bytes())

    def test_it_composes_with_update_lnode_type_in_one_history_entry(self):
        # The point of `source`: prune against what is ABOUT to land, so the
        # update and the prune are one undo step. Reading the target after
        # the update was applied would work too, at the cost of two.
        doc = self.specified_doc("compose_target.ssd")
        source = self.moved_on("compose_source.ssd")
        before = doc.to_bytes()
        edits = (update_lnode_type(doc, source, "T_PTOC",
                                   on_conflict="overwrite")
                 + prune_lnode_specification(doc, "T_PTOC", source=source))
        undo = self.applied(doc, edits)

        declaration = self.find(doc, "LNodeType", id="T_PTOC")
        self.assertEqual(["StrVal", "Beh"],
                         [do.get("name") for do in declaration])
        self.assertEqual([("DOS", "StrVal"), ("DOS", "Beh"),
                          ("DAS", "stVal")] * 2, self.spec_names(doc))

        doc.apply_edit(undo)
        self.assertEqual(before, doc.to_bytes())


class TestSpecificationCorpus(unittest.TestCase):
    """The corpus is NOT innocent of IEC TR 61850-6-100, and A16b's first
    reading of it said otherwise.

    A16 established that all twenty-three Substation-section element names are
    zero in all three exports, `DOS`, `SDS` and `DAS` among them, and
    `TestNoCorpusMaterial` above still asserts exactly that. What it does not
    say -- and what the phase that added this function initially wrote as
    though it did -- is that the NAMESPACE is absent too. **It is not.**
    `mixed.scd` declares it on the root, with the TR's own
    ``version``/``revision``/``release`` attributes beside the SCL edition's,
    and carries **17 elements in it**: one `ServiceSpecifications` holding
    sixteen `SMVParameters`, in a root-level `Private` alongside five
    Siemens-private blocks.

    So this module's second IEC namespace is **live material from a real
    vendor tool**, not a convention known only from IEC's example files. What
    the corpus lacks is the part `prune_lnode_specification` reads: it has no
    `LNode` at all, so there is nothing for a specification to hang on.

    That makes a real corpus test possible where A16 deliberately had none,
    and it is the tripwire for the day a DIGSI export starts writing `DOS`.
    """

    def corpus(self, name):
        path = roundtrip.CORPUS / name
        if not path.is_file():
            self.skipTest("the corpus is not in this distribution")
        return SclDocument.parse(path)

    def spec_namespace_elements(self, doc):
        return [strip_ns(el.tag) for el in doc.root.iter()
                if isinstance(el.tag, str)
                and el.tag.startswith(SPECIFICATION_NS)]

    def test_one_export_declares_the_namespace_and_two_do_not(self):
        declared = {name: b"61850/2019/SCL/6-100"
                    in (roundtrip.CORPUS / name).read_bytes()
                    for name in ("sel.scd", "mixed.scd", "siemens.scd")
                    if (roundtrip.CORPUS / name).is_file()}
        if not declared:
            self.skipTest("the corpus is not in this distribution")
        self.assertEqual({"sel.scd": False, "mixed.scd": True,
                          "siemens.scd": False}, declared)

    def test_mixed_carries_seventeen_elements_and_none_is_a_specification(self):
        found = self.spec_namespace_elements(self.corpus("mixed.scd"))
        self.assertEqual(17, len(found))
        self.assertEqual({"ServiceSpecifications": 1, "SMVParameters": 16},
                         {name: found.count(name) for name in set(found)})
        # The three this module removes are not among them, which is why
        # `TestNoCorpusMaterial` and this class do not contradict each other.
        self.assertEqual(set(), set(found) & set(SPECIFICATION_ELEMENTS))

    def test_the_two_silent_exports_carry_nothing_in_the_namespace(self):
        for name in ("sel.scd", "siemens.scd"):
            self.assertEqual([], self.spec_namespace_elements(
                self.corpus(name)), name)

    def test_the_prune_is_a_no_op_over_every_type_the_corpus_declares(self):
        # 277 LNodeType in `mixed.scd` and 0 LNode: the pool is entirely for
        # IEDs, so nothing specifies anything. Asserted against the BYTES,
        # because a function that reads a 6-100 Private must be shown not to
        # touch the one the corpus actually has -- that Private is Siemens's
        # neighbour and `sellib`/`siemenslib` territory is next door to it.
        doc = self.corpus("mixed.scd")
        before = doc.to_bytes()
        ids = [type_.get("id")
               for section in children_local(doc.root, "DataTypeTemplates")
               for type_ in children_local(section, "LNodeType")]
        self.assertEqual(277, len(ids))
        self.assertEqual(0, len(list(doc.root.iter(
            doc.root.tag[:doc.root.tag.index("}") + 1] + "LNode"))))
        for id_ in ids:
            self.assertEqual([], prune_lnode_specification(doc, id_))
        self.assertEqual(before, doc.to_bytes())
