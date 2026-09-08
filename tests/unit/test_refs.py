# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""IEC 61850-8-1 naming: object reference <-> MMS domain and item name."""

import unittest

from py61850.core import refs


class TestLdName(unittest.TestCase):
    def test_default_is_ied_name_plus_ld_inst(self):
        self.assertEqual(refs.ld_name("QPC1_TR1_UPC1", "PRO"),
                         "QPC1_TR1_UPC1PRO")

    def test_explicit_ld_name_wins(self):
        # 61850-6 lets an LDevice carry ldName; when it does, that IS the
        # MMS domain and the iedName+inst rule does not apply.
        self.assertEqual(refs.ld_name("QPC1_TR1_UPC1", "PRO", "BAY1PROT"),
                         "BAY1PROT")

    def test_blank_ld_name_falls_back(self):
        self.assertEqual(refs.ld_name("IED", "LD0", ""), "IEDLD0")


class TestLnName(unittest.TestCase):
    def test_prefix_class_inst(self):
        self.assertEqual(refs.ln_name("BKR1", "CSWI", "1"), "BKR1CSWI1")

    def test_missing_parts_are_empty(self):
        self.assertEqual(refs.ln_name(None, "PTRC", "1"), "PTRC1")
        self.assertEqual(refs.ln_name("", "LLN0", ""), "LLN0")


class TestMmsItem(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(refs.mms_item("PTRC1", "ST", ["Op", "general"]),
                         "PTRC1$ST$Op$general")

    def test_nested_path(self):
        # A control's ctlVal descends through the Oper SDI; every level is a
        # further $ segment.
        self.assertEqual(refs.mms_item("CSWI1", "CO", ["Pos", "Oper", "ctlVal"]),
                         "CSWI1$CO$Pos$Oper$ctlVal")

    def test_data_object_only(self):
        self.assertEqual(refs.mms_item("LLN0", "ST", ["Beh"]), "LLN0$ST$Beh")


class TestObjectReference(unittest.TestCase):
    def test_dotted_form(self):
        self.assertEqual(
            refs.object_reference("QPC1PRO", "PTRC1", ["Op", "general"]),
            "QPC1PRO/PTRC1.Op.general")


class TestSplitItem(unittest.TestCase):
    def test_round_trips_mms_item(self):
        ln, fc, path = refs.split_item("CSWI1$CO$Pos$Oper$ctlVal")
        self.assertEqual(ln, "CSWI1")
        self.assertEqual(fc, "CO")
        self.assertEqual(path, ("Pos", "Oper", "ctlVal"))
        self.assertEqual(refs.mms_item(ln, fc, path),
                         "CSWI1$CO$Pos$Oper$ctlVal")

    def test_an_item_with_no_fc_is_refused(self):
        # "LLN0" alone names a logical node, not an attribute. Guessing an FC
        # here produces an item no device serves.
        self.assertIsNone(refs.split_item("LLN0"))
        self.assertIsNone(refs.split_item(""))


class TestDaParts(unittest.TestCase):
    def test_both_separators_are_understood(self):
        # SCL writes the descent through an SDI with '.', MMS spells every
        # level with '$'. Sources of a map use one each.
        self.assertEqual(refs.da_parts("Oper.ctlVal"), ("Oper", "ctlVal"))
        self.assertEqual(refs.da_parts("Oper$ctlVal"), ("Oper", "ctlVal"))
        self.assertEqual(refs.da_parts("stVal"), ("stVal",))

    def test_empty_segments_are_dropped(self):
        self.assertEqual(refs.da_parts(""), ())
        self.assertEqual(refs.da_parts(None), ())
        self.assertEqual(refs.da_parts("a..b"), ("a", "b"))


if __name__ == "__main__":
    unittest.main()
