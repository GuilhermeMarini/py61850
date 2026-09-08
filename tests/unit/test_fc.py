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
