# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""The IEC 61850-7-2 functional constraints, and the ranking that says which
one is worth reading when a data attribute is reachable under several."""

import unittest

from py61850.core import fc


class TestVocabulary(unittest.TestCase):
    def test_the_full_7_2_set_is_present(self):
        # 61850-7-2 defines 13. The 12 below ST..EX were measured across the
        # three reference SCDs (7.1, 13.5 and 23.5 MB, two vendors); SG is the
        # thirteenth, absent from that corpus but part of the standard.
        self.assertEqual(sorted(fc.FUNCTIONAL_CONSTRAINTS), sorted([
            "ST", "MX", "SP", "SV", "CF", "DC", "SG", "SE", "SR", "OR",
            "BL", "EX", "CO",
        ]))

    def test_is_valid(self):
        self.assertTrue(fc.is_valid("ST"))
        self.assertTrue(fc.is_valid("st"))          # case-insensitive
        self.assertFalse(fc.is_valid("ZZ"))
        self.assertFalse(fc.is_valid(""))
        self.assertFalse(fc.is_valid(None))


class TestControl(unittest.TestCase):
    def test_co_is_the_only_control(self):
        self.assertTrue(fc.is_control("CO"))
        for other in ("ST", "MX", "SP", "CF", "DC", "SG", "SE", "SR", "OR",
                      "BL", "EX", "SV"):
            self.assertFalse(fc.is_control(other), other)


class TestControlDataAttributes(unittest.TestCase):
    """The control model's own attributes -- the "is this a command?" question
    asked of a NAME rather than of an FC."""

    def test_the_7_2_control_attributes_are_present(self):
        self.assertEqual(sorted(fc.CONTROL_DATA_ATTRIBUTES),
                         ["Cancel", "Oper", "SBO", "SBOw"])

    def test_a_control_root_makes_the_whole_descent_a_command(self):
        # The FC belongs to the root DA, and so does this: everything under
        # `Oper` is part of the command, not a reading of the point.
        self.assertTrue(fc.is_control_attribute("Oper"))
        self.assertTrue(fc.is_control_attribute("Oper.ctlVal"))
        self.assertTrue(fc.is_control_attribute("SBOw.ctlNum"))
        self.assertTrue(fc.is_control_attribute("Cancel.origin.orIdent"))

    def test_both_spellings_of_the_descent(self):
        # SCL writes an SDI descent with '.', MMS spells every level with '$'.
        self.assertTrue(fc.is_control_attribute("Oper$ctlVal"))
        self.assertEqual(fc.is_control_attribute("Oper$ctlVal"),
                         fc.is_control_attribute("Oper.ctlVal"))

    def test_parts_already_split_are_accepted(self):
        self.assertTrue(fc.is_control_attribute(("Oper", "ctlVal")))

    def test_a_bare_ctlval_leaf_is_a_command_without_its_root(self):
        # This is the rule the root test does not cover: a consumer may hold
        # the leaf alone, and `ctlVal` occurs nowhere but inside a control.
        self.assertTrue(fc.is_control_attribute("ctlVal"))

    def test_a_status_attribute_is_not_a_command(self):
        for reading in ("stVal", "general", "phsA", "mag.f", "q", "t",
                        "setVal", "dirGeneral"):
            self.assertFalse(fc.is_control_attribute(reading), reading)

    def test_an_empty_path_is_not_a_command(self):
        # Refusing is the safe answer: an unknown shape must not be promoted
        # into "this is a command" any more than into "this is a reading".
        self.assertFalse(fc.is_control_attribute(""))
        self.assertFalse(fc.is_control_attribute(None))
        self.assertFalse(fc.is_control_attribute(()))


class TestReadRank(unittest.TestCase):
    def test_status_beats_measurement_beats_config(self):
        self.assertLess(fc.read_rank("ST"), fc.read_rank("MX"))
        self.assertLess(fc.read_rank("MX"), fc.read_rank("CF"))

    def test_control_is_strictly_last(self):
        # A command SETS a point; it is not a reading of one. Polling `CO`
        # would ask the device what it was last told, not what it sees.
        for other in fc.FUNCTIONAL_CONSTRAINTS:
            if other == "CO":
                continue
            self.assertLess(fc.read_rank(other), fc.read_rank("CO"), other)

    def test_an_unknown_fc_outranks_a_control_but_nothing_else(self):
        # Two-tier on purpose: an FC this table has never heard of is still a
        # reading, so it must stay better than a command.
        self.assertLess(fc.read_rank("ZZ"), fc.read_rank("CO"))
        self.assertGreater(fc.read_rank("ZZ"), fc.read_rank("BL"))

    def test_rank_is_case_insensitive(self):
        self.assertEqual(fc.read_rank("st"), fc.read_rank("ST"))


if __name__ == "__main__":
    unittest.main()
